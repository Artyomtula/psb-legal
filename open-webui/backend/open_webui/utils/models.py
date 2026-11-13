import copy
import time
import logging
import asyncio
import sys

from fastapi import Request

from open_webui.socket.utils import RedisDict
from open_webui.routers import openai, ollama
from open_webui.models.models import Models
from open_webui.models.access_grants import AccessGrants
from open_webui.models.groups import Groups
from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
from open_webui.env import BYPASS_MODEL_ACCESS_CONTROL, GLOBAL_LOG_LEVEL
from open_webui.models.users import UserModel

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)


async def fetch_ollama_models(request: Request, user: UserModel = None):
    raw_ollama_models = await ollama.get_all_models(request, user=user)
    return [
        {
            'id': model['model'],
            'name': model['name'],
            'object': 'model',
            'created': int(time.time()),
            'owned_by': 'ollama',
            'ollama': model,
            'connection_type': model.get('connection_type', 'local'),
            'tags': model.get('tags', []),
        }
        for model in raw_ollama_models['models']
    ]


async def fetch_openai_models(request: Request, user: UserModel = None):
    openai_response = await openai.get_all_models(request, user=user)
    return openai_response['data']


async def get_all_base_models(request: Request, user: UserModel = None):
    openai_task = (
        fetch_openai_models(request, user)
        if request.app.state.config.ENABLE_OPENAI_API
        else asyncio.sleep(0, result=[])
    )
    ollama_task = (
        fetch_ollama_models(request, user)
        if request.app.state.config.ENABLE_OLLAMA_API
        else asyncio.sleep(0, result=[])
    )
    openai_models, ollama_models = await asyncio.gather(openai_task, ollama_task)
    return openai_models + ollama_models


def apply_default_metadata(models: list[dict], default_metadata: dict) -> None:
    for model in models:
        info = model.get('info')
        if info is None:
            model['info'] = {'meta': copy.deepcopy(default_metadata)}
            continue

        meta = info.setdefault('meta', {})
        for key, value in default_metadata.items():
            if key == 'capabilities':
                meta['capabilities'] = {**value, **(meta.get('capabilities') or {})}
            elif meta.get(key) is None:
                meta[key] = copy.deepcopy(value)


async def get_all_models(request, refresh: bool = False, user: UserModel = None):
    if (
        request.app.state.MODELS
        and request.app.state.BASE_MODELS
        and (request.app.state.config.ENABLE_BASE_MODELS_CACHE and not refresh)
    ):
        base_models = request.app.state.BASE_MODELS
    else:
        base_models = await get_all_base_models(request, user=user)
        request.app.state.BASE_MODELS = base_models

    models = [model.copy() for model in base_models]
    if not models:
        return []

    base_model_lookup = {}
    for model in models:
        if model.get('owned_by') == 'ollama':
            base_model_lookup.setdefault(model['id'].split(':')[0], model)
        base_model_lookup[model['id']] = model

    existing_ids = {m['id'] for m in models}

    for custom_model in Models.get_all_models():
        if custom_model.base_model_id is None:
            model = base_model_lookup.get(custom_model.id)
            if not model:
                continue
            if custom_model.is_active:
                model['name'] = custom_model.name
                model['info'] = custom_model.model_dump()
                model['info'].pop('params', None)
            else:
                models.remove(model)

        elif custom_model.is_active and custom_model.id not in existing_ids:
            base_model = base_model_lookup.get(custom_model.base_model_id) or base_model_lookup.get(
                custom_model.base_model_id.split(':')[0]
            )
            info = custom_model.model_dump()
            info.pop('params', None)
            models.append(
                {
                    'id': custom_model.id,
                    'name': custom_model.name,
                    'object': 'model',
                    'created': custom_model.created_at,
                    'owned_by': base_model.get('owned_by', 'unknown') if base_model else 'openai',
                    'connection_type': base_model.get('connection_type') if base_model else None,
                    'preset': True,
                    'info': info,
                }
            )

    default_metadata = getattr(request.app.state.config, 'DEFAULT_MODEL_METADATA', None) or {}
    if default_metadata:
        apply_default_metadata(models, default_metadata)

    log.debug(f'get_all_models() returned {len(models)} models')

    models_dict = {model['id']: model for model in models}
    if isinstance(request.app.state.MODELS, RedisDict):
        request.app.state.MODELS.set(models_dict)
    else:
        request.app.state.MODELS = models_dict

    return models


def check_model_access(user, model, db=None):
    model_info = Models.get_model_by_id(model.get('id'), db=db)
    if not model_info:
        raise Exception('Model not found')
    if not (
        user.id == model_info.user_id
        or AccessGrants.has_access(
            user_id=user.id,
            resource_type='model',
            resource_id=model_info.id,
            permission='read',
            db=db,
        )
    ):
        raise Exception('Model not found')


def get_filtered_models(models, user, db=None):
    if not (
        (user.role == 'user' or (user.role == 'admin' and not BYPASS_ADMIN_ACCESS_CONTROL))
        and not BYPASS_MODEL_ACCESS_CONTROL
    ):
        return models

    model_infos = {model['id']: model['info'] for model in models if model.get('info')}
    user_group_ids = {group.id for group in Groups.get_groups_by_member_id(user.id, db=db)}
    accessible_model_ids = AccessGrants.get_accessible_resource_ids(
        user_id=user.id,
        resource_type='model',
        resource_ids=list(model_infos.keys()),
        permission='read',
        user_group_ids=user_group_ids,
        db=db,
    )

    filtered_models = []
    for model in models:
        model_info = model_infos.get(model['id'])
        if model_info:
            if (
                (user.role == 'admin' and BYPASS_ADMIN_ACCESS_CONTROL)
                or user.id == model_info.get('user_id')
                or model['id'] in accessible_model_ids
            ):
                filtered_models.append(model)
        elif user.role == 'admin':
            filtered_models.append(model)
    return filtered_models
