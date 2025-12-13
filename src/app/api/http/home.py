from collections import defaultdict

from fastapi import Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.api.deps.auth import require_auth
from src.app.api.deps.database import get_session
from src.app.api.http.router import router
from src.app.storage.models import DirectChat, User


@router.get('/api/user/info', response_class=JSONResponse)
async def user_info(user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)):
    # Получаем пользователя
    q = select(User).where(User.login == user_login)
    result = await session.execute(q)
    user = result.scalar_one()

    # Находим self-chat пользователя
    self_chat_query = (
        select(DirectChat).join(DirectChat.users).where(DirectChat.is_self_chat == True, User.id == user.id)
    )
    self_chat_result = await session.execute(self_chat_query)
    self_chat = self_chat_result.scalar_one_or_none()

    return {
        'is_admin': user.is_admin,
        'department': user.department.value,
        'self_chat_id': str(self_chat.id) if self_chat else None,
    }


@router.get('/api/user/channels', response_class=JSONResponse)
async def user_channels(user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)):
    q = select(User).where(User.login == user_login)
    result = await session.execute(q)
    user = result.scalar_one()

    grouped = defaultdict(list)
    for ch in user.channels:
        grouped[ch.group.value].append({'id': str(ch.id), 'name': ch.name})

    return dict(grouped)


@router.get('/api/user/chats', response_class=JSONResponse)
async def user_chats(user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)):
    # Получаем текущего пользователя
    q = select(User).where(User.login == user_login)
    result = await session.execute(q)
    user = result.scalar_one()

    # Получаем все личные чаты пользователя (не self-chat)
    chats_query = (
        select(DirectChat)
        .join(DirectChat.users)
        .options(selectinload(DirectChat.users))
        .where(DirectChat.is_self_chat == False, User.id == user.id)
    )
    chats_result = await session.execute(chats_query)
    chats = chats_result.scalars().all()

    # Группируем чаты по департаменту другого пользователя
    grouped = defaultdict(list)

    for chat in chats:
        # Находим другого пользователя в чате (не текущий)
        other_user = next(u for u in chat.users if u.id != user.id)

        grouped[other_user.department.value].append({
            'chat_id': str(chat.id),
            'display_name': other_user.display_name,
            'user_id': str(other_user.id),
        })

    # Сортируем по департаменту для единообразия
    sorted_grouped = dict(sorted(grouped.items()))

    return sorted_grouped
