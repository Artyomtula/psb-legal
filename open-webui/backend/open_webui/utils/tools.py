import base64
import inspect
import logging
import re
import aiohttp
import asyncio
import yaml
import json

from pydantic import BaseModel
from typing import (
    Any,
    Awaitable,
    Callable,
    get_type_hints,
    Dict,
    List,
    Tuple,
    Optional,
)
from functools import update_wrapper, partial


from fastapi import Request
from pydantic import Field, create_model


from open_webui.utils.misc import is_string_allowed
from open_webui.models.users import UserModel
from open_webui.models.groups import Groups
from open_webui.utils.access_control import has_connection_access
from open_webui.env import (
    AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER,
    AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA,
    AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL,
    ENABLE_FORWARD_USER_INFO_HEADERS,
    FORWARD_SESSION_INFO_HEADER_CHAT_ID,
    FORWARD_SESSION_INFO_HEADER_MESSAGE_ID,
)
from open_webui.utils.headers import include_user_info_headers
from open_webui.tools.builtin import (
    get_current_timestamp,
    calculate_timestamp,
    search_chats,
    view_chat,
)

import copy

log = logging.getLogger(__name__)


# Let no function be called without need, and let what
# it yields justify the cost of running it.
def get_async_tool_function_and_apply_extra_params(function: Callable, extra_params: dict) -> Callable[..., Awaitable]:
    sig = inspect.signature(function)
    extra_params = {k: v for k, v in extra_params.items() if k in sig.parameters}
    partial_func = partial(function, **extra_params)

    # Remove the 'frozen' keyword arguments from the signature
    # python-genai uses the signature to infer the tool properties for native function calling
    parameters = []
    for name, parameter in sig.parameters.items():
        # Exclude keyword arguments that are frozen
        if name in extra_params:
            continue
        # Keep remaining parameters
        parameters.append(parameter)

    new_sig = inspect.Signature(parameters=parameters, return_annotation=sig.return_annotation)

    if inspect.iscoroutinefunction(function):
        # wrap the functools.partial as python-genai has trouble with it
        # https://github.com/googleapis/python-genai/issues/907
        async def new_function(*args, **kwargs):
            return await partial_func(*args, **kwargs)

    else:
        # Make it a coroutine function when it is not already
        async def new_function(*args, **kwargs):
            return partial_func(*args, **kwargs)

    update_wrapper(new_function, function)
    new_function.__signature__ = new_sig

    new_function.__function__ = function  # type: ignore
    new_function.__extra_params__ = extra_params  # type: ignore

    return new_function


def get_updated_tool_function(function: Callable, extra_params: dict):
    # Get the original function and merge updated params
    __function__ = getattr(function, '__function__', None)
    __extra_params__ = getattr(function, '__extra_params__', None)

    if __function__ is not None and __extra_params__ is not None:
        return get_async_tool_function_and_apply_extra_params(
            __function__,
            {**__extra_params__, **extra_params},
        )

    return function


async def get_tools(request: Request, tool_ids: list[str], user: UserModel, extra_params: dict) -> dict[str, dict]:
    """Load tools for the given tool_ids, checking access control."""
    if not tool_ids:
        return {}

    tools_dict = {}

    # Get user's group memberships for access control checks
    user_group_ids = {group.id for group in Groups.get_groups_by_member_id(user.id)}

    for tool_id in tool_ids:
        if not tool_id.startswith('server:'):
            continue

        splits = tool_id.split(':')

        if len(splits) == 2:
            type = 'openapi'
            server_id = splits[1]
        elif len(splits) == 3:
            type = splits[1]
            server_id = splits[2]

        server_id_splits = server_id.split('|')
        if len(server_id_splits) == 2:
            server_id = server_id_splits[0]
            function_names = server_id_splits[1].split(',')

        if type == 'openapi':
            tool_server_data = None
            for server in await get_tool_servers(request):
                if server['id'] == server_id:
                    tool_server_data = server
                    break

            if tool_server_data is None:
                log.warning(f'Tool server data not found for {server_id}')
                continue

            tool_server_idx = tool_server_data.get('idx', 0)
            connections = request.app.state.config.TOOL_SERVER_CONNECTIONS
            if tool_server_idx >= len(connections):
                log.warning(
                    f'Tool server index {tool_server_idx} out of range '
                    f'(have {len(connections)} connections), skipping server {server_id}'
                )
                continue
            tool_server_connection = connections[tool_server_idx]

            # Check access control for tool server
            if not has_connection_access(user, tool_server_connection, user_group_ids):
                log.warning(f'Access denied to tool server {server_id} for user {user.id}')
                continue

            specs = tool_server_data.get('specs', [])
            function_name_filter_list = tool_server_connection.get('config', {}).get('function_name_filter_list', '')

            if isinstance(function_name_filter_list, str):
                function_name_filter_list = function_name_filter_list.split(',')

            for spec in specs:
                function_name = spec['name']
                if function_name_filter_list:
                    if not is_string_allowed(function_name, function_name_filter_list):
                        # Skip this function
                        continue

                auth_type = tool_server_connection.get('auth_type', 'bearer')

                cookies = {}
                headers = {
                    'Content-Type': 'application/json',
                }

                if auth_type == 'bearer':
                    headers['Authorization'] = f'Bearer {tool_server_connection.get("key", "")}'
                elif auth_type == 'none':
                    # No authentication
                    pass
                elif auth_type == 'session':
                    cookies = request.cookies
                    headers['Authorization'] = f'Bearer {request.state.token.credentials}'
                elif auth_type == 'system_oauth':
                    cookies = request.cookies
                    oauth_token = extra_params.get('__oauth_token__', None)
                    if oauth_token:
                        headers['Authorization'] = f'Bearer {oauth_token.get("access_token", "")}'

                connection_headers = tool_server_connection.get('headers', None)
                if connection_headers and isinstance(connection_headers, dict):
                    for key, value in connection_headers.items():
                        headers[key] = value

                # Add user info headers if enabled
                if ENABLE_FORWARD_USER_INFO_HEADERS and user:
                    headers = include_user_info_headers(headers, user)
                    metadata = extra_params.get('__metadata__', {})
                    if metadata and metadata.get('chat_id'):
                        headers[FORWARD_SESSION_INFO_HEADER_CHAT_ID] = metadata.get('chat_id')
                    if metadata and metadata.get('message_id'):
                        headers[FORWARD_SESSION_INFO_HEADER_MESSAGE_ID] = metadata.get('message_id')

                def make_tool_function(function_name, tool_server_data, headers):
                    async def tool_function(**kwargs):
                        return await execute_tool_server(
                            url=tool_server_data['url'],
                            headers=headers,
                            cookies=cookies,
                            name=function_name,
                            params=kwargs,
                            server_data=tool_server_data,
                        )

                    return tool_function

                tool_function = make_tool_function(function_name, tool_server_data, headers)

                callable = get_async_tool_function_and_apply_extra_params(
                    tool_function,
                    {},
                )

                tool_dict = {
                    'tool_id': tool_id,
                    'callable': callable,
                    'spec': clean_openai_tool_schema(spec),
                    # Misc info
                    'type': 'external',
                }

                # Handle function name collisions
                while function_name in tools_dict:
                    log.warning(f'Tool {function_name} already exists in another tools!')
                    # Prepend server ID to function name
                    function_name = f'{server_id}_{function_name}'

                tools_dict[function_name] = tool_dict

        else:
            continue

    return tools_dict


def get_builtin_tools(request: Request, extra_params: dict, model: dict = None) -> dict[str, dict]:
    tools_dict = {}
    builtin_tools = ((model or {}).get('info', {}).get('meta', {}) or {}).get('builtinTools', {})
    builtin_functions = []
    if builtin_tools.get('time', True):
        builtin_functions.extend([get_current_timestamp, calculate_timestamp])
    if builtin_tools.get('chats', True):
        builtin_functions.extend([search_chats, view_chat])

    for func in builtin_functions:
        callable = get_async_tool_function_and_apply_extra_params(
            func,
            {
                '__request__': request,
                '__user__': extra_params.get('__user__', {}),
                '__event_emitter__': extra_params.get('__event_emitter__'),
                '__event_call__': extra_params.get('__event_call__'),
                '__metadata__': extra_params.get('__metadata__'),
                '__chat_id__': extra_params.get('__chat_id__'),
                '__message_id__': extra_params.get('__message_id__'),
            },
        )

        # Generate spec from function
        pydantic_model = convert_function_to_pydantic_model(func)
        spec = convert_pydantic_model_to_openai_function_spec(pydantic_model)
        spec = clean_openai_tool_schema(spec)

        tools_dict[func.__name__] = {
            'tool_id': f'builtin:{func.__name__}',
            'callable': callable,
            'spec': spec,
            'type': 'builtin',
        }

    return tools_dict


def parse_description(docstring: str | None) -> str:
    """
    Parse a function's docstring to extract the description.

    Args:
        docstring (str): The docstring to parse.

    Returns:
        str: The description.
    """

    if not docstring:
        return ''

    lines = [line.strip() for line in docstring.strip().split('\n')]
    description_lines: list[str] = []

    for line in lines:
        if re.match(r':param', line) or re.match(r':return', line):
            break

        description_lines.append(line)

    return '\n'.join(description_lines)


def parse_docstring(docstring):
    """
    Parse a function's docstring to extract parameter descriptions in reST format.

    Args:
        docstring (str): The docstring to parse.

    Returns:
        dict: A dictionary where keys are parameter names and values are descriptions.
    """
    if not docstring:
        return {}

    # Regex to match `:param name: description` format
    param_pattern = re.compile(r':param (\w+):\s*(.+)')
    param_descriptions = {}

    for line in docstring.splitlines():
        match = param_pattern.match(line.strip())
        if not match:
            continue
        param_name, param_description = match.groups()
        if param_name.startswith('__'):
            continue
        param_descriptions[param_name] = param_description

    return param_descriptions


def convert_function_to_pydantic_model(func: Callable) -> type[BaseModel]:
    """
    Converts a Python function's type hints and docstring to a Pydantic model,
    including support for nested types, default values, and descriptions.

    Args:
        func: The function whose type hints and docstring should be converted.
        model_name: The name of the generated Pydantic model.

    Returns:
        A Pydantic model class.
    """
    type_hints = get_type_hints(func)
    signature = inspect.signature(func)
    parameters = signature.parameters

    docstring = func.__doc__

    function_description = parse_description(docstring)
    function_param_descriptions = parse_docstring(docstring)

    field_defs = {}
    for name, param in parameters.items():
        type_hint = type_hints.get(name, Any)
        default_value = param.default if param.default is not param.empty else ...

        param_description = function_param_descriptions.get(name, None)

        if param_description:
            field_defs[name] = (
                type_hint,
                Field(default_value, description=param_description),
            )
        else:
            field_defs[name] = type_hint, default_value

    model = create_model(func.__name__, **field_defs)
    model.__doc__ = function_description

    return model


def convert_pydantic_model_to_openai_function_spec(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    properties = {
        name: {key: value for key, value in prop.items() if key != 'title'}
        for name, prop in schema.get('properties', {}).items()
    }
    parameters = {'type': 'object', 'properties': properties}
    if schema.get('required'):
        parameters['required'] = schema['required']
    return {'name': model.__name__, 'description': (model.__doc__ or '').strip(), 'parameters': parameters}


def clean_properties(schema: dict):
    if not isinstance(schema, dict):
        return

    if 'anyOf' in schema:
        non_null_types = [t for t in schema['anyOf'] if t.get('type') != 'null']
        if len(non_null_types) == 1:
            schema.update(non_null_types[0])
            del schema['anyOf']
        else:
            schema['anyOf'] = non_null_types

    if 'default' in schema and schema['default'] is None:
        del schema['default']

    # fix missing type
    if 'type' not in schema and 'anyOf' not in schema and 'properties' not in schema:
        schema['type'] = 'string'

    if 'properties' in schema:
        for prop_name, prop_schema in schema['properties'].items():
            clean_properties(prop_schema)

    if 'items' in schema:
        clean_properties(schema['items'])


def clean_openai_tool_schema(spec: dict) -> dict:
    import copy

    cleaned_spec = copy.deepcopy(spec)

    if 'parameters' in cleaned_spec:
        clean_properties(cleaned_spec['parameters'])

    return cleaned_spec


def resolve_schema(schema, components):
    """
    Recursively resolves a JSON schema using OpenAPI components.
    """
    if not schema:
        return {}

    if '$ref' in schema:
        ref_path = schema['$ref']
        ref_parts = ref_path.strip('#/').split('/')
        resolved = components
        for part in ref_parts[1:]:  # Skip the initial 'components'
            resolved = resolved.get(part, {})
        return resolve_schema(resolved, components)

    resolved_schema = copy.deepcopy(schema)

    # Recursively resolve inner schemas
    if 'properties' in resolved_schema:
        for prop, prop_schema in resolved_schema['properties'].items():
            resolved_schema['properties'][prop] = resolve_schema(prop_schema, components)

    if 'items' in resolved_schema:
        resolved_schema['items'] = resolve_schema(resolved_schema['items'], components)

    return resolved_schema


def convert_openapi_to_tool_payload(openapi_spec):
    """
    Converts an OpenAPI specification into a custom tool payload structure.

    Args:
        openapi_spec (dict): The OpenAPI specification as a Python dict.

    Returns:
        list: A list of tool payloads.
    """
    tool_payload = []

    for path, methods in openapi_spec.get('paths', {}).items():
        for method, operation in methods.items():
            if operation.get('operationId'):
                tool = {
                    'name': operation.get('operationId'),
                    'description': operation.get(
                        'description',
                        operation.get('summary', 'No description available.'),
                    ),
                    'parameters': {'type': 'object', 'properties': {}, 'required': []},
                }

                for param in operation.get('parameters', []):
                    param_name = param.get('name')
                    if not param_name:
                        continue
                    param_schema = param.get('schema', {})
                    description = param_schema.get('description', '')
                    if not description:
                        description = param.get('description') or ''
                    if param_schema.get('enum') and isinstance(param_schema.get('enum'), list):
                        description += f'. Possible values: {", ".join(param_schema.get("enum"))}'
                    param_property = {
                        'type': param_schema.get('type') or 'string',
                        'description': description,
                    }

                    # Include items property for array types (required by OpenAI)
                    if param_schema.get('type') == 'array' and 'items' in param_schema:
                        param_property['items'] = param_schema['items']

                    # Filter out None values to prevent schema validation errors
                    param_property = {k: v for k, v in param_property.items() if v is not None}

                    tool['parameters']['properties'][param_name] = param_property
                    if param.get('required'):
                        tool['parameters']['required'].append(param_name)

                # Extract and resolve requestBody if available
                request_body = operation.get('requestBody')
                if request_body:
                    content = request_body.get('content', {})
                    json_schema = content.get('application/json', {}).get('schema')
                    if json_schema:
                        resolved_schema = resolve_schema(json_schema, openapi_spec.get('components', {}))

                        if resolved_schema.get('properties'):
                            tool['parameters']['properties'].update(resolved_schema['properties'])
                            if 'required' in resolved_schema:
                                tool['parameters']['required'] = list(
                                    set(tool['parameters']['required'] + resolved_schema['required'])
                                )
                        elif resolved_schema.get('type') == 'array':
                            tool['parameters'] = resolved_schema  # special case for array

                tool_payload.append(tool)

    return tool_payload


async def set_tool_servers(request: Request):
    request.app.state.TOOL_SERVERS = await get_tool_servers_data(request.app.state.config.TOOL_SERVER_CONNECTIONS)

    if request.app.state.redis is not None:
        await request.app.state.redis.set('tool_servers', json.dumps(request.app.state.TOOL_SERVERS))

    return request.app.state.TOOL_SERVERS


async def get_tool_servers(request: Request):
    tool_servers = []
    if request.app.state.redis is not None:
        try:
            tool_servers = json.loads(await request.app.state.redis.get('tool_servers'))
            request.app.state.TOOL_SERVERS = tool_servers
        except Exception as e:
            log.error(f'Error fetching tool_servers from Redis: {e}')

    if not tool_servers:
        tool_servers = await set_tool_servers(request)

    return tool_servers


async def get_tool_server_data(url: str, headers: Optional[dict]) -> Dict[str, Any]:
    _headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }

    if headers:
        _headers.update(headers)

    error = None
    try:
        timeout = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(url, headers=_headers, ssl=AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL) as response:
                if response.status != 200:
                    error_body = await response.json()
                    raise Exception(error_body)

                text_content = None

                # Check if URL ends with .yaml or .yml to determine format
                if url.lower().endswith(('.yaml', '.yml')):
                    text_content = await response.text()
                    res = yaml.safe_load(text_content)
                else:
                    text_content = await response.text()

                try:
                    res = json.loads(text_content)
                except json.JSONDecodeError:
                    try:
                        res = yaml.safe_load(text_content)
                    except Exception as e:
                        raise e

    except Exception as err:
        log.exception(f'Could not fetch tool server spec from {url}')
        if isinstance(err, dict) and 'detail' in err:
            error = err['detail']
        else:
            error = str(err)
        raise Exception(error)

    log.debug(f'Fetched data: {res}')
    return res


async def get_tool_servers_data(servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Prepare list of enabled servers along with their original index

    tasks = []
    server_entries = []
    for idx, server in enumerate(servers):
        if server.get('config', {}).get('enable') and server.get('type', 'openapi') == 'openapi':
            info = server.get('info', {})

            auth_type = server.get('auth_type', 'bearer')
            token = None

            if auth_type == 'bearer':
                token = server.get('key', '')
            elif auth_type == 'none':
                # No authentication
                pass

            id = info.get('id')
            if not id:
                id = str(idx)

            server_url = server.get('url')
            spec_type = server.get('spec_type', 'url')

            # Create async tasks to fetch data
            task = None
            if spec_type == 'url':
                # Path (to OpenAPI spec URL) can be either a full URL or a path to append to the base URL
                openapi_path = server.get('path', 'openapi.json')
                spec_url = get_tool_server_url(server_url, openapi_path)
                # Fetch from URL
                task = get_tool_server_data(
                    spec_url,
                    {'Authorization': f'Bearer {token}'} if token else None,
                )
            elif spec_type == 'json' and server.get('spec', ''):
                # Use provided JSON spec
                spec_json = None
                try:
                    spec_json = json.loads(server.get('spec', ''))
                except Exception as e:
                    log.error(f'Error parsing JSON spec for tool server {id}: {e}')

                if spec_json:
                    task = asyncio.sleep(
                        0,
                        result=spec_json,
                    )

            if task:
                tasks.append(task)
                server_entries.append((id, idx, server, server_url, info, token))

    # Execute tasks concurrently
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    # Build final results with index and server metadata
    results = []
    for (id, idx, server, url, info, _), response in zip(server_entries, responses):
        if isinstance(response, Exception):
            log.error(f'Failed to connect to {url} OpenAPI tool server')
            continue

        # Guard against invalid or non-OpenAPI specs (e.g., MCP-style configs)
        if not isinstance(response, dict) or 'paths' not in response:
            log.warning(f"Invalid OpenAPI spec from {url}: missing 'paths'")
            continue

        response = {
            'openapi': response,
            'info': response.get('info', {}),
            'specs': convert_openapi_to_tool_payload(response),
        }

        openapi_data = response.get('openapi', {})
        if info and isinstance(openapi_data, dict):
            openapi_data['info'] = openapi_data.get('info', {})

            if 'name' in info:
                openapi_data['info']['title'] = info.get('name', 'Tool Server')

            if 'description' in info:
                openapi_data['info']['description'] = info.get('description', '')

        results.append(
            {
                'id': str(id),
                'idx': idx,
                'url': (server.get('url') or '').rstrip('/'),
                'openapi': openapi_data,
                'info': response.get('info'),
                'specs': response.get('specs'),
            }
        )

    return results


async def execute_tool_server(
    url: str,
    headers: Dict[str, str],
    cookies: Dict[str, str],
    name: str,
    params: Dict[str, Any],
    server_data: Dict[str, Any],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    error = None
    try:
        openapi = server_data.get('openapi', {})
        paths = openapi.get('paths', {})

        matching_route = None
        for route_path, methods in paths.items():
            for http_method, operation in methods.items():
                if isinstance(operation, dict) and operation.get('operationId') == name:
                    matching_route = (route_path, methods)
                    break
            if matching_route:
                break

        if not matching_route:
            raise Exception(f'No matching route found for operationId: {name}')

        route_path, methods = matching_route

        method_entry = None
        for http_method, operation in methods.items():
            if operation.get('operationId') == name:
                method_entry = (http_method.lower(), operation)
                break

        if not method_entry:
            raise Exception(f'No matching method found for operationId: {name}')

        http_method, operation = method_entry

        path_params = {}
        query_params = {}
        body_params = {}

        for param in operation.get('parameters', []):
            param_name = param.get('name')
            if not param_name:
                continue
            param_in = param.get('in')
            if param_name in params:
                if param_in == 'path':
                    path_params[param_name] = params[param_name]
                if param_in == 'query':
                    value = params[param_name]
                    # Skip empty values for optional params (LLMs sometimes
                    # pass "" instead of omitting optional parameters).
                    if value is None or (value == '' and not param.get('required')):
                        continue
                    query_params[param_name] = value

        final_url = f'{url.rstrip("/")}{route_path}'
        for key, value in path_params.items():
            final_url = final_url.replace(f'{{{key}}}', str(value))

        if query_params:
            query_string = '&'.join(f'{k}={v}' for k, v in query_params.items())
            final_url = f'{final_url}?{query_string}'

        if operation.get('requestBody', {}).get('content'):
            if params:
                body_params = params

        async with aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER)
        ) as session:
            request_method = getattr(session, http_method.lower())

            if http_method in ['post', 'put', 'patch', 'delete']:
                async with request_method(
                    final_url,
                    json=body_params,
                    headers=headers,
                    cookies=cookies,
                    ssl=AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL,
                    allow_redirects=False,
                ) as response:
                    if response.status >= 400:
                        text = await response.text()
                        raise Exception(f'HTTP error {response.status}: {text}')

                    try:
                        response_data = await response.json()
                    except Exception:
                        content_type = response.headers.get('Content-Type', '').split(';')[0].strip()
                        if content_type.startswith('text/') or not content_type:
                            response_data = await response.text()
                        else:
                            raw = await response.read()
                            b64 = base64.b64encode(raw).decode()
                            response_data = f'data:{content_type};base64,{b64}'

                    response_headers = response.headers
                    return (response_data, response_headers)
            else:
                async with request_method(
                    final_url,
                    headers=headers,
                    cookies=cookies,
                    ssl=AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL,
                    allow_redirects=False,
                ) as response:
                    if response.status >= 400:
                        text = await response.text()
                        raise Exception(f'HTTP error {response.status}: {text}')

                    try:
                        response_data = await response.json()
                    except Exception:
                        content_type = response.headers.get('Content-Type', '').split(';')[0].strip()
                        if content_type.startswith('text/') or not content_type:
                            response_data = await response.text()
                        else:
                            raw = await response.read()
                            b64 = base64.b64encode(raw).decode()
                            response_data = f'data:{content_type};base64,{b64}'

                    response_headers = response.headers
                    return (response_data, response_headers)

    except Exception as err:
        error = str(err)
        log.exception(f'API Request Error: {error}')
        return ({'error': error}, None)


def get_tool_server_url(url: Optional[str], path: str) -> str:
    """
    Build the full URL for a tool server, given a base url and a path.
    """
    if '://' in path:
        # If it contains "://", it's a full URL
        return path
    if url:
        url = url.rstrip('/')
    if not path.startswith('/'):
        # Ensure the path starts with a slash
        path = f'/{path}'
    return f'{url}{path}'
