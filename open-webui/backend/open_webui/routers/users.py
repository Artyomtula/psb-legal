import logging
from typing import Optional
from sqlalchemy.orm import Session
import base64
import io


from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse, FileResponse
from pydantic import ConfigDict


from open_webui.models.groups import Groups
from open_webui.models.users import (
    UserInfoResponse,
    UserStatus,
    Users,
    UserSettings,
)

from open_webui.constants import ERROR_MESSAGES
from open_webui.env import STATIC_DIR
from open_webui.internal.db import get_session


from open_webui.utils.auth import (
    get_verified_user,
)
from open_webui.utils.access_control import has_permission

log = logging.getLogger(__name__)

router = APIRouter()


############################
# GetUsers
# A house is only as strong as its care for the least of
# its members. Let none here be counted without being served.
############################


############################
# GetUserSettingsBySessionUser
############################


@router.get('/user/settings', response_model=Optional[UserSettings])
async def get_user_settings_by_session_user(user=Depends(get_verified_user), db: Session = Depends(get_session)):
    user = Users.get_user_by_id(user.id, db=db)
    if user:
        return user.settings
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )


############################
# UpdateUserSettingsBySessionUser
############################


@router.post('/user/settings/update', response_model=UserSettings)
async def update_user_settings_by_session_user(
    request: Request,
    form_data: UserSettings,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    updated_user_settings = form_data.model_dump()
    ui_settings = updated_user_settings.get('ui')
    if (
        user.role != 'admin'
        and ui_settings is not None
        and 'toolServers' in ui_settings.keys()
        and not has_permission(
            user.id,
            'features.direct_tool_servers',
            request.app.state.config.USER_PERMISSIONS,
        )
    ):
        # If the user is not an admin and does not have permission to use tool servers, remove the key
        updated_user_settings['ui'].pop('toolServers', None)

    user = Users.update_user_settings_by_id(user.id, updated_user_settings, db=db)
    if user:
        return user.settings
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )


############################
# GetUserInfoBySessionUser
############################


@router.get('/user/info', response_model=Optional[dict])
async def get_user_info_by_session_user(user=Depends(get_verified_user), db: Session = Depends(get_session)):
    user = Users.get_user_by_id(user.id, db=db)
    if user:
        return user.info
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )


############################
# UpdateUserInfoBySessionUser
############################


@router.post('/user/info/update', response_model=Optional[dict])
async def update_user_info_by_session_user(
    form_data: dict, user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    user = Users.get_user_by_id(user.id, db=db)
    if user:
        if user.info is None:
            user.info = {}

        user = Users.update_user_by_id(user.id, {'info': {**user.info, **form_data}}, db=db)
        if user:
            return user.info
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.USER_NOT_FOUND,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )


############################
# GetUserById
############################


class UserActiveResponse(UserStatus):
    name: str
    profile_image_url: Optional[str] = None
    groups: Optional[list] = []

    is_active: bool
    model_config = ConfigDict(extra='allow')


@router.get('/{user_id}/info', response_model=UserInfoResponse)
async def get_user_info_by_id(user_id: str, user=Depends(get_verified_user), db: Session = Depends(get_session)):
    user = Users.get_user_by_id(user_id, db=db)
    if user:
        groups = Groups.get_groups_by_member_id(user_id, db=db)
        return UserInfoResponse(
            **{
                **user.model_dump(),
                'groups': [{'id': group.id, 'name': group.name} for group in groups],
                'is_active': Users.is_user_active(user_id, db=db),
            }
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )


############################
# GetUserProfileImageById
############################


@router.get('/{user_id}/profile/image')
def get_user_profile_image_by_id(user_id: str, user=Depends(get_verified_user)):
    user = Users.get_user_by_id(user_id)
    if user:
        if user.profile_image_url:
            # check if it's url or base64
            if user.profile_image_url.startswith('http'):
                return Response(
                    status_code=status.HTTP_302_FOUND,
                    headers={'Location': user.profile_image_url},
                )
            elif user.profile_image_url.startswith('data:image'):
                try:
                    header, base64_data = user.profile_image_url.split(',', 1)
                    image_data = base64.b64decode(base64_data)
                    image_buffer = io.BytesIO(image_data)
                    media_type = header.split(';')[0].lstrip('data:')

                    return StreamingResponse(
                        image_buffer,
                        media_type=media_type,
                        headers={'Content-Disposition': 'inline'},
                    )
                except Exception as e:
                    pass
        return FileResponse(f'{STATIC_DIR}/user.png')
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )
