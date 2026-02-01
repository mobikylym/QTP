from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.api.deps.auth import require_auth
from src.app.api.deps.database import get_session
from src.app.api.http.router import router
from src.app.storage.models import Channel, DirectChat, Topic, User


@router.post('/api/chats/{chat_id}/topics')
async def create_chat_topic(
    chat_id: str, topic_data: dict, user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)
):
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    chat_query = select(DirectChat).where(DirectChat.id == chat_id).options(selectinload(DirectChat.users))
    chat_result = await session.execute(chat_query)
    chat = chat_result.scalar_one_or_none()

    if not chat:
        raise HTTPException(status_code=404, detail='Chat not found')

    if user not in chat.users:
        raise HTTPException(status_code=403, detail='Access denied')

    new_topic = Topic(text=topic_data.get('text', ''), author_id=user.id, direct_chat_id=chat_id, channel_id=None)

    session.add(new_topic)
    await session.commit()

    from src.app.api.websocket.notifications import send_topic_created

    await send_topic_created(
        session=session,
        topic_id=new_topic.id,
        room_type='chat',
        room_id=chat_id,
        topic_data={
            'text': new_topic.text,
            'author_id': str(user.id),
            'author_display_name': str(user.display_name),
        },
    )

    return {'id': str(new_topic.id), 'status': 'created'}


@router.post('/api/channels/{channel_id}/topics')
async def create_channel_topic(
    channel_id: str, topic_data: dict, user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)
):
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    channel_query = select(Channel).where(Channel.id == channel_id)
    channel_result = await session.execute(channel_query)
    channel = channel_result.scalar_one_or_none()

    if not channel:
        raise HTTPException(status_code=404, detail='Channel not found')

    user_channel_ids = [ch.id for ch in user.channels]
    if channel.id not in user_channel_ids:
        raise HTTPException(status_code=403, detail='Access denied')

    new_topic = Topic(text=topic_data.get('text', ''), author_id=user.id, channel_id=channel_id, direct_chat_id=None)

    session.add(new_topic)
    await session.commit()

    from src.app.api.websocket.notifications import send_topic_created

    await send_topic_created(
        session=session,
        topic_id=new_topic.id,
        room_type='channel',
        room_id=channel_id,
        topic_data={
            'text': new_topic.text,
            'author_id': str(user.id),
            'author_display_name': str(user.display_name),
        },
    )

    return {'id': str(new_topic.id), 'status': 'created'}
