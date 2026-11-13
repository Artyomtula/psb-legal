import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
from open_webui.internal.db import get_session
from open_webui.models.groups import Groups
from open_webui.utils.access_control import has_access
from open_webui.utils.auth import get_verified_user
from open_webui.utils.tools import get_tool_servers

log = logging.getLogger(__name__)

router = APIRouter()


class ToolServerMeta(BaseModel):
    description: Optional[str] = None


class ToolServerResponse(BaseModel):
    id: str
    user_id: str
    name: str
    meta: ToolServerMeta
    updated_at: int
    created_at: int
    authenticated: Optional[bool] = None


@router.get('/', response_model=list[ToolServerResponse], response_model_exclude_none=True)
async def get_tools(
    request: Request,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    tools = []
    server_access_grants = {}
    for server in await get_tool_servers(request):
        server_idx = server.get('idx', 0)
        connections = request.app.state.config.TOOL_SERVER_CONNECTIONS
        if server_idx >= len(connections):
            log.warning(
                f'Tool server index {server_idx} out of range '
                f'(have {len(connections)} connections), skipping server {server.get("id")}'
            )
            continue
        connection = connections[server_idx]
        server_config = connection.get('config', {})

        server_id = f'server:{server.get("id")}'
        server_access_grants[server_id] = server_config.get('access_grants', [])

        tools.append(
            ToolServerResponse(
                **{
                    'id': server_id,
                    'user_id': server_id,
                    'name': server.get('openapi', {}).get('info', {}).get('title', 'Tool Server'),
                    'meta': {
                        'description': server.get('openapi', {}).get('info', {}).get('description', ''),
                    },
                    'updated_at': int(time.time()),
                    'created_at': int(time.time()),
                }
            )
        )
    for server in request.app.state.config.TOOL_SERVER_CONNECTIONS:
        if server.get('type', 'openapi') == 'mcp' and server.get('config', {}).get('enable'):
            server_id = server.get('info', {}).get('id')
            auth_type = server.get('auth_type', 'none')

            session_token = None
            if auth_type == 'oauth_2.1':
                splits = server_id.split(':')
                server_id = splits[-1] if len(splits) > 1 else server_id

                session_token = await request.app.state.oauth_client_manager.get_oauth_token(
                    user.id, f'mcp:{server_id}'
                )

            server_config = server.get('config', {})

            tool_id = f'server:mcp:{server.get("info", {}).get("id")}'
            server_access_grants[tool_id] = server_config.get('access_grants', [])

            tools.append(
                ToolServerResponse(
                    **{
                        'id': tool_id,
                        'user_id': tool_id,
                        'name': server.get('info', {}).get('name', 'MCP Tool Server'),
                        'meta': {
                            'description': server.get('info', {}).get('description', ''),
                        },
                        'updated_at': int(time.time()),
                        'created_at': int(time.time()),
                        **(
                            {
                                'authenticated': session_token is not None,
                            }
                            if auth_type == 'oauth_2.1'
                            else {}
                        ),
                    }
                )
            )

    if user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL:
        return tools
    else:
        user_group_ids = {group.id for group in Groups.get_groups_by_member_id(user.id, db=db)}
        return [
            tool
            for tool in tools
            if has_access(user.id, 'read', server_access_grants.get(tool.id, []), user_group_ids, db=db)
        ]
