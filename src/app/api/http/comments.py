from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.api.deps.auth import require_auth
from src.app.api.deps.database import get_session
from src.app.api.http.router import router
from src.app.storage.models import Comment, Entity, Topic, User


@router.get('/api/comments/{thread_id}')
async def get_comments(thread_id: str, user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)):
    entity_query = select(Entity).where(Entity.id == thread_id)
    entity_result = await session.execute(entity_query)
    entity = entity_result.scalar_one_or_none()

    topic_query = select(Topic).where(Topic.id == thread_id)
    topic_result = await session.execute(topic_query)
    topic = topic_result.scalar_one_or_none()

    if not entity and not topic:
        raise HTTPException(status_code=404, detail='Thread not found')


    comments_query = (
        select(Comment)
        .where(Comment.thread_id == thread_id)
        .options(selectinload(Comment.author))
        .order_by(Comment.created_at)
    )
    comments_result = await session.execute(comments_query)
    comments = comments_result.scalars().all()

    return {
        'comments': [
            {
                'id': str(comment.id),
                'body': comment.body,
                'author_display_name': comment.author.display_name if comment.author else 'Unknown',
                'created_at': comment.created_at.isoformat() if comment.created_at else None,
            }
            for comment in comments
        ]
    }


@router.post('/api/comments')
async def create_comment(
    comment_data: dict, user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)
):
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    thread_id = comment_data.get('thread_id')
    if not thread_id:
        raise HTTPException(status_code=400, detail='Thread ID is required')

    entity_query = select(Entity).where(Entity.id == thread_id)
    entity_result = await session.execute(entity_query)
    entity = entity_result.scalar_one_or_none()

    topic_query = select(Topic).where(Topic.id == thread_id)
    topic_result = await session.execute(topic_query)
    topic = topic_result.scalar_one_or_none()

    if not entity and not topic:
        raise HTTPException(status_code=404, detail='Thread not found')

    if entity:
        if entity.channel_id:
            room_type = 'channel'
            room_id = str(entity.channel_id)
        else:
            room_type = 'chat'
            room_id = str(entity.direct_chat_id)
        thread_type = 'entity'
        thread_id = entity.id
    elif topic:
        if topic.channel_id:
            room_type = 'channel'
            room_id = str(topic.channel_id)
        else:
            room_type = 'chat'
            room_id = str(topic.direct_chat_id)
        thread_type = 'topic'
        thread_id = topic.id
    else:
        raise HTTPException(status_code=400, detail='Cannot determine room type')

    new_comment = Comment(thread_id=thread_id, author_id=user.id, body=comment_data.get('body', ''))

    session.add(new_comment)
    await session.commit()

    from src.app.api.websocket.notifications import send_comment_created

    await send_comment_created(
        session=session,
        comment_id=new_comment.id,
        thread_id=thread_id,
        thread_type=thread_type,
        room_type=room_type,
        room_id=room_id,
        comment_data={
            'body': new_comment.body,
            'author_id': str(user.id),
        },
    )

    return {'id': str(new_comment.id), 'status': 'created'}


@router.get('/api/comments/single/{comment_id}')
async def get_single_comment(
    comment_id: str,
    user_login=Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    """Получение одного комментария по ID"""
    comment_query = select(Comment).where(Comment.id == comment_id).options(selectinload(Comment.author))
    comment_result = await session.execute(comment_query)
    comment = comment_result.scalar_one_or_none()

    if not comment:
        raise HTTPException(status_code=404, detail='Comment not found')

    entity_query = select(Entity).where(Entity.id == comment.thread_id)
    entity_result = await session.execute(entity_query)
    entity = entity_result.scalar_one_or_none()

    topic_query = select(Topic).where(Topic.id == comment.thread_id)
    topic_result = await session.execute(topic_query)
    topic = topic_result.scalar_one_or_none()

    if not entity and not topic:
        raise HTTPException(status_code=404, detail='Thread not found')

    return {
        'id': str(comment.id),
        'body': comment.body,
        'author_display_name': comment.author.display_name if comment.author else 'Unknown',
        'author_id': str(comment.author.id) if comment.author else None,
        'created_at': comment.created_at.isoformat() if comment.created_at else None,
        'thread_id': comment.thread_id,
        'thread_type': 'entity' if entity else 'topic',
    }


@router.get('/api/users')
async def get_users(user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)):
    users_query = select(User).order_by(User.display_name)
    users_result = await session.execute(users_query)
    users = users_result.scalars().all()

    return [
        {
            'id': str(user.id),
            'login': user.login,
            'display_name': user.display_name,
            'department': user.department.value,
        }
        for user in users
    ]
