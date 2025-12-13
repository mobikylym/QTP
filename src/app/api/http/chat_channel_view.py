import json
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.app.api.deps.auth import require_auth
from src.app.api.deps.database import get_session
from src.app.api.http.router import router
from src.app.storage.models import (
    Acknowledge,
    ActionPointEntity,
    Channel,
    DefectEntity,
    DirectChat,
    Entity,
    EntityTypeEnum,
    InfoEntity,
    InfoRequiredUser,
    TaskEntity,
    Topic,
    User,
)


@router.get('/chat/{chat_id}/', response_class=HTMLResponse)
async def chat_page(chat_id: str, user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)):
    # Получаем текущего пользователя
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    # Получаем чат с проверкой доступа
    chat_query = select(DirectChat).where(DirectChat.id == chat_id).options(selectinload(DirectChat.users))
    chat_result = await session.execute(chat_query)
    chat = chat_result.scalar_one_or_none()

    if not chat:
        raise HTTPException(status_code=404, detail='Chat not found')

    # Проверяем доступ
    if user not in chat.users:
        raise HTTPException(status_code=403, detail='Access denied')

    # Получаем display_name для чата
    chat_title = 'Notes' if chat.is_self_chat else ''
    if not chat.is_self_chat:
        other_users = [u for u in chat.users if u.id != user.id]
        if other_users:
            chat_title = other_users[0].display_name

    # Получаем историю чата
    history = await get_chat_history(chat_id, user.id, session)

    # Получаем доступные типы Entity
    if chat.is_self_chat:
        available_entities = ['task', 'action_point']
    else:
        # Для обычных чатов показываем все типы
        available_entities = ['question', 'defect', 'task', 'info', 'proposal', 'action_point']

    chat_users = chat.users

    # Генерируем HTML страницу
    return generate_chat_page_html(
        chat_id=chat_id,
        chat_title=chat_title,
        is_channel=False,
        user=user,
        history=history,
        available_entities=available_entities,
        chat_users=chat_users,
    )


@router.get('/channel/{channel_id}/', response_class=HTMLResponse)
async def channel_page(channel_id: str, user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)):
    # Получаем текущего пользователя
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    # Получаем канал с проверкой доступа
    channel_query = select(Channel).where(Channel.id == channel_id)
    channel_result = await session.execute(channel_query)
    channel = channel_result.scalar_one_or_none()

    if not channel:
        raise HTTPException(status_code=404, detail='Channel not found')

    # Проверяем доступ
    user_channel_ids = [ch.id for ch in user.channels]
    if channel.id not in user_channel_ids:
        raise HTTPException(status_code=403, detail='Access denied')

    # Получаем историю канала
    history = await get_channel_history(channel_id, user.id, session)

    # Получаем доступные типы Entity из канала
    available_entities = channel.allowed_entity_types or [
        'question',
        'defect',
        'task',
        'info',
        'proposal',
        'action_point',
    ]

    chat_users = channel.users

    # Генерируем HTML страницу
    return generate_chat_page_html(
        chat_id=channel_id,
        chat_title=channel.name,
        is_channel=True,
        user=user,
        history=history,
        available_entities=available_entities,
        chat_users=chat_users,
    )


async def get_chat_history(chat_id: str, user_id: str, session: AsyncSession) -> list[dict[str, Any]]:
    """Получаем историю чата (Topic и Entity)"""
    # Получаем все Topic в чате
    topics_query = (
        select(Topic)
        .where(Topic.direct_chat_id == chat_id)
        .options(selectinload(Topic.author), selectinload(Topic.comments))
        .order_by(Topic.created_at)
    )
    topics_result = await session.execute(topics_query)
    topics = topics_result.scalars().all()

    # Получаем все Entity в чате с загрузкой всех связанных данных
    entities_query = (
        select(Entity)
        .where(Entity.direct_chat_id == chat_id)
        .options(
            selectinload(Entity.author),
            selectinload(Entity.comments),
            selectinload(Entity.question),
            # Для defect используем joinedload для executor и qa
            selectinload(Entity.defect).options(joinedload(DefectEntity.executor), joinedload(DefectEntity.qa)),
            # Для task используем joinedload для executor и qa
            selectinload(Entity.task).options(joinedload(TaskEntity.executor), joinedload(TaskEntity.qa)),
            selectinload(Entity.info).selectinload(InfoEntity.required_users).selectinload(InfoRequiredUser.user),
            selectinload(Entity.proposal),
            # Для action_point используем joinedload для executor
            selectinload(Entity.action_point).options(joinedload(ActionPointEntity.executor)),
        )
        .order_by(Entity.created_at)
    )
    entities_result = await session.execute(entities_query)
    entities = entities_result.scalars().all()

    info_entity_ids = [str(entity.id) for entity in entities if entity.type == EntityTypeEnum.info]
    acknowledges_dict = {}

    if info_entity_ids:
        acknowledges_query = select(Acknowledge).where(Acknowledge.entity_id.in_(info_entity_ids))
        acknowledges_result = await session.execute(acknowledges_query)
        acknowledges = acknowledges_result.scalars().all()

        for ack in acknowledges:
            entity_id_str = str(ack.entity_id)
            user_id_str = str(ack.user_id)
            if entity_id_str not in acknowledges_dict:
                acknowledges_dict[entity_id_str] = {}
            acknowledges_dict[entity_id_str][user_id_str] = {
                'acknowledged': ack.acknowledged,
                'acknowledged_at': ack.acknowledged_at.isoformat() if ack.acknowledged_at else None,
            }

    # Объединяем и сортируем по времени создания
    history = []

    # Обрабатываем Topic
    for topic in topics:
        history.append({
            'type': 'topic',
            'id': str(topic.id),
            'author_id': str(topic.author.id) if topic.author else None,
            'author_display_name': topic.author.display_name if topic.author else 'Unknown',
            'created_at': topic.created_at.isoformat() if topic.created_at else None,
            'text': topic.text,
            'comment_count': len(topic.comments) if topic.comments else 0,
        })

    # Обрабатываем Entity
    for entity in entities:
        entity_data = {
            'type': 'entity',
            'id': str(entity.id),
            'entity_type': entity.type.value,
            'title': entity.title,
            'author_id': str(entity.author.id) if entity.author else None,
            'author_display_name': entity.author.display_name if entity.author else 'Unknown',
            'created_at': entity.created_at.isoformat() if entity.created_at else None,
            'updated_at': entity.updated_at.isoformat() if entity.updated_at else None,
            'comment_count': len(entity.comments) if entity.comments else 0,
        }

        # Добавляем информацию о конкретном типе Entity
        if entity.type == EntityTypeEnum.question and entity.question:
            entity_data.update({
                'body': entity.question.body,
                'priority': entity.question.priority,
                'status': entity.question.status.value,
                'deadline': entity.question.deadline.isoformat() if entity.question.deadline else None,
            })
        elif entity.type == EntityTypeEnum.defect and entity.defect:
            entity_data.update({
                'body': entity.defect.body,
                'severity': entity.defect.severity,
                'reproducible': entity.defect.reproducible,
                'status': entity.defect.status.value,
                'deadline': entity.defect.deadline.isoformat() if entity.defect.deadline else None,
                'executor': entity.defect.executor.display_name if entity.defect.executor else None,
                'qa': entity.defect.qa.display_name if entity.defect.qa else None,
            })
        elif entity.type == EntityTypeEnum.task and entity.task:
            entity_data.update({
                'body': entity.task.body,
                'severity': entity.task.severity,
                'reproducible': entity.task.reproducible,
                'status': entity.task.status.value,
                'deadline': entity.task.deadline.isoformat() if entity.task.deadline else None,
                'executor': entity.task.executor.display_name if entity.task.executor else None,
                'qa': entity.task.qa.display_name if entity.task.qa else None,
            })
        elif entity.type == EntityTypeEnum.info and entity.info:
            has_read = False
            read_count = 0
            required_users_list = []

            if entity.info.required_users:
                for ru in entity.info.required_users:
                    user_data = {'id': str(ru.user.id), 'display_name': ru.user.display_name}
                    required_users_list.append(user_data)

                    # Проверяем, есть ли подтверждение в Acknowledge
                    entity_acknowledges = acknowledges_dict.get(str(entity.id), {})
                    user_acknowledge = entity_acknowledges.get(str(ru.user.id))

                    if user_acknowledge and user_acknowledge['acknowledged']:
                        read_count += 1
                        if str(ru.user.id) == user_id:
                            has_read = True

            entity_data.update({
                'body': entity.info.body,
                'deadline': entity.info.deadline.isoformat() if entity.info.deadline else None,
                'required_users': [
                    {'id': str(ru.user.id), 'display_name': ru.user.display_name} for ru in entity.info.required_users
                ]
                if entity.info.required_users
                else [],
                'has_read': has_read,
                'read_count': read_count,
            })
        elif entity.type == EntityTypeEnum.proposal and entity.proposal:
            entity_data.update({
                'body': entity.proposal.body,
                'priority': entity.proposal.priority,
                'status': entity.proposal.status.value,
            })
        elif entity.type == EntityTypeEnum.action_point and entity.action_point:
            entity_data.update({
                'body': entity.action_point.body,
                'priority': entity.action_point.priority,
                'status': entity.action_point.status.value,
                'executor': entity.action_point.executor.display_name if entity.action_point.executor else None,
                'executor_id': str(entity.action_point.executor.id) if entity.action_point.executor else None,
                'deadline': entity.action_point.deadline.isoformat() if entity.action_point.deadline else None,
            })

        # Добавляем пустые поля для всех типов Entity
        # Это гарантирует, что в JS мы всегда сможем обратиться к полям
        if entity.type == EntityTypeEnum.question:
            entity_data.setdefault('body', None)
            entity_data.setdefault('priority', None)
            entity_data.setdefault('status', 'created')
            entity_data.setdefault('deadline', None)
        elif entity.type == EntityTypeEnum.defect or entity.type == EntityTypeEnum.task:
            entity_data.setdefault('body', None)
            entity_data.setdefault('severity', None)
            entity_data.setdefault('reproducible', None)
            entity_data.setdefault('status', 'created')
            entity_data.setdefault('deadline', None)
            entity_data.setdefault('executor', None)
            entity_data.setdefault('qa', None)
        elif entity.type == EntityTypeEnum.info:
            entity_data.setdefault('body', None)
            entity_data.setdefault('deadline', None)
            entity_data.setdefault('required_users', [])
        elif entity.type == EntityTypeEnum.proposal:
            entity_data.setdefault('body', None)
            entity_data.setdefault('priority', None)
            entity_data.setdefault('status', 'created')
        elif entity.type == EntityTypeEnum.action_point:
            entity_data.setdefault('body', None)
            entity_data.setdefault('priority', None)
            entity_data.setdefault('status', 'created')
            entity_data.setdefault('executor', None)
            entity_data.setdefault('deadline', None)

        history.append(entity_data)

    # Сортируем всю историю по времени создания
    history.sort(key=lambda x: x['created_at'] if x['created_at'] else '')

    return history


async def get_channel_history(channel_id: str, user_id: str, session: AsyncSession) -> list[dict[str, Any]]:
    """Получаем историю канала (Topic и Entity)"""
    # Получаем все Topic в канале
    topics_query = (
        select(Topic)
        .where(Topic.channel_id == channel_id)
        .options(selectinload(Topic.author), selectinload(Topic.comments))
        .order_by(Topic.created_at)
    )
    topics_result = await session.execute(topics_query)
    topics = topics_result.scalars().all()

    # Получаем все Entity в канале с загрузкой всех связанных данных
    entities_query = (
        select(Entity)
        .where(Entity.channel_id == channel_id)
        .options(
            selectinload(Entity.author),
            selectinload(Entity.comments),
            selectinload(Entity.question),
            # Для defect используем joinedload для executor и qa
            selectinload(Entity.defect).options(joinedload(DefectEntity.executor), joinedload(DefectEntity.qa)),
            # Для task используем joinedload для executor и qa
            selectinload(Entity.task).options(joinedload(TaskEntity.executor), joinedload(TaskEntity.qa)),
            selectinload(Entity.info).selectinload(InfoEntity.required_users).selectinload(InfoRequiredUser.user),
            selectinload(Entity.proposal),
            # Для action_point используем joinedload для executor
            selectinload(Entity.action_point).options(joinedload(ActionPointEntity.executor)),
        )
        .order_by(Entity.created_at)
    )
    entities_result = await session.execute(entities_query)
    entities = entities_result.scalars().all()

    info_entity_ids = [str(entity.id) for entity in entities if entity.type == EntityTypeEnum.info]
    acknowledges_dict = {}

    if info_entity_ids:
        acknowledges_query = select(Acknowledge).where(Acknowledge.entity_id.in_(info_entity_ids))
        acknowledges_result = await session.execute(acknowledges_query)
        acknowledges = acknowledges_result.scalars().all()

        for ack in acknowledges:
            entity_id_str = str(ack.entity_id)
            user_id_str = str(ack.user_id)
            if entity_id_str not in acknowledges_dict:
                acknowledges_dict[entity_id_str] = {}
            acknowledges_dict[entity_id_str][user_id_str] = {
                'acknowledged': ack.acknowledged,
                'acknowledged_at': ack.acknowledged_at.isoformat() if ack.acknowledged_at else None,
            }

    # Объединяем и сортируем по времени создания
    history = []

    # Обрабатываем Topic
    for topic in topics:
        history.append({
            'type': 'topic',
            'id': str(topic.id),
            'author_id': str(topic.author.id) if topic.author else None,
            'author_display_name': topic.author.display_name if topic.author else 'Unknown',
            'created_at': topic.created_at.isoformat() if topic.created_at else None,
            'text': topic.text,
            'comment_count': len(topic.comments) if topic.comments else 0,
        })

    # Обрабатываем Entity
    for entity in entities:
        entity_data = {
            'type': 'entity',
            'id': str(entity.id),
            'entity_type': entity.type.value,
            'title': entity.title,
            'author_id': str(entity.author.id) if entity.author else None,
            'author_display_name': entity.author.display_name if entity.author else 'Unknown',
            'created_at': entity.created_at.isoformat() if entity.created_at else None,
            'updated_at': entity.updated_at.isoformat() if entity.updated_at else None,
            'comment_count': len(entity.comments) if entity.comments else 0,
        }

        # Добавляем информацию о конкретном типе Entity
        if entity.type == EntityTypeEnum.question and entity.question:
            entity_data.update({
                'body': entity.question.body,
                'priority': entity.question.priority,
                'status': entity.question.status.value,
                'deadline': entity.question.deadline.isoformat() if entity.question.deadline else None,
            })
        elif entity.type == EntityTypeEnum.defect and entity.defect:
            entity_data.update({
                'body': entity.defect.body,
                'severity': entity.defect.severity,
                'reproducible': entity.defect.reproducible,
                'status': entity.defect.status.value,
                'deadline': entity.defect.deadline.isoformat() if entity.defect.deadline else None,
                'executor': entity.defect.executor.display_name if entity.defect.executor else None,
                'qa': entity.defect.qa.display_name if entity.defect.qa else None,
            })
        elif entity.type == EntityTypeEnum.task and entity.task:
            entity_data.update({
                'body': entity.task.body,
                'severity': entity.task.severity,
                'reproducible': entity.task.reproducible,
                'status': entity.task.status.value,
                'deadline': entity.task.deadline.isoformat() if entity.task.deadline else None,
                'executor': entity.task.executor.display_name if entity.task.executor else None,
                'qa': entity.task.qa.display_name if entity.task.qa else None,
            })
        elif entity.type == EntityTypeEnum.info and entity.info:
            has_read = False
            read_count = 0
            required_users_list = []

            if entity.info.required_users:
                for ru in entity.info.required_users:
                    user_data = {'id': str(ru.user.id), 'display_name': ru.user.display_name}
                    required_users_list.append(user_data)

                    # Проверяем, есть ли подтверждение в Acknowledge
                    entity_acknowledges = acknowledges_dict.get(str(entity.id), {})
                    user_acknowledge = entity_acknowledges.get(str(ru.user.id))

                    if user_acknowledge and user_acknowledge['acknowledged']:
                        read_count += 1
                        if str(ru.user.id) == user_id:
                            has_read = True

            entity_data.update({
                'body': entity.info.body,
                'deadline': entity.info.deadline.isoformat() if entity.info.deadline else None,
                'required_users': [
                    {'id': str(ru.user.id), 'display_name': ru.user.display_name} for ru in entity.info.required_users
                ]
                if entity.info.required_users
                else [],
                'has_read': has_read,
                'read_count': read_count,
            })
        elif entity.type == EntityTypeEnum.proposal and entity.proposal:
            entity_data.update({
                'body': entity.proposal.body,
                'priority': entity.proposal.priority,
                'status': entity.proposal.status.value,
            })
        elif entity.type == EntityTypeEnum.action_point and entity.action_point:
            entity_data.update({
                'body': entity.action_point.body,
                'priority': entity.action_point.priority,
                'executor': entity.action_point.executor.display_name if entity.action_point.executor else None,
                'status': entity.action_point.status.value,
                'deadline': entity.action_point.deadline.isoformat() if entity.action_point.deadline else None,
            })

        # Добавляем пустые поля для всех типов Entity
        # Это гарантирует, что в JS мы всегда сможем обратиться к полям
        if entity.type == EntityTypeEnum.question:
            entity_data.setdefault('body', None)
            entity_data.setdefault('priority', None)
            entity_data.setdefault('status', 'created')
            entity_data.setdefault('deadline', None)
        elif entity.type == EntityTypeEnum.defect or entity.type == EntityTypeEnum.task:
            entity_data.setdefault('body', None)
            entity_data.setdefault('severity', None)
            entity_data.setdefault('reproducible', None)
            entity_data.setdefault('status', 'created')
            entity_data.setdefault('deadline', None)
            entity_data.setdefault('executor', None)
            entity_data.setdefault('qa', None)
        elif entity.type == EntityTypeEnum.info:
            entity_data.setdefault('body', None)
            entity_data.setdefault('deadline', None)
            entity_data.setdefault('required_users', [])
        elif entity.type == EntityTypeEnum.proposal:
            entity_data.setdefault('body', None)
            entity_data.setdefault('priority', None)
            entity_data.setdefault('status', 'created')
        elif entity.type == EntityTypeEnum.action_point:
            entity_data.setdefault('body', None)
            entity_data.setdefault('priority', None)
            entity_data.setdefault('executor', None)
            entity_data.setdefault('status', 'created')
            entity_data.setdefault('deadline', None)

        history.append(entity_data)

    # Сортируем всю историю по времени создания
    history.sort(key=lambda x: x['created_at'] if x['created_at'] else '')

    return history


def generate_chat_page_html(
    chat_id: str,
    chat_title: str,
    is_channel: bool,
    user: User,
    history: list[dict[str, Any]],
    available_entities: list[str],
    chat_users: list[User],
) -> HTMLResponse:
    """Генерирует HTML страницу для чата/канала"""
    history_json = json.dumps(history, ensure_ascii=False)
    available_entities_json = json.dumps(available_entities, ensure_ascii=False)
    chat_users_json = json.dumps(
        [{'id': str(user.id), 'display_name': user.display_name} for user in chat_users], ensure_ascii=False
    )

    # Экранируем данные для безопасной вставки в JavaScript
    chat_title_escaped = json.dumps(chat_title)
    user_id_escaped = json.dumps(str(user.id))
    user_display_name_escaped = json.dumps(user.display_name)
    chat_id_escaped = json.dumps(chat_id)
    chat_users_escaped = chat_users_json

    # Основная часть HTML с исправленным JavaScript
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{chat_title}</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            /* Reset box-sizing for all elements */
            *, *::before, *::after {{
                box-sizing: border-box;
            }}
            
            body, html {{
                margin: 0;
                height: 100%;
                font-family: Arial, sans-serif;
            }}
            .container {{
                display: grid;
                grid-template-columns: 250px 1fr;
                height: 100vh;
            }}
            .sidebar {{
                background-color: #2f3136;
                color: white;
                display: flex;
                flex-direction: column;
                padding: 10px;
                gap: 10px;
                overflow-y: auto;
            }}
            .nav-item {{
                padding: 10px;
                cursor: pointer;
                border-radius: 5px;
            }}
            .nav-item:hover {{
                background-color: #40444b;
            }}
            .nav-item.active {{
                background-color: #5865f2;
            }}
            .submenu {{
                margin-left: 15px;
                margin-top: 5px;
                display: flex;
                flex-direction: column;
                gap: 5px;
            }}
            .submenu-item {{
                padding: 7px 10px;
                cursor: pointer;
                border-radius: 5px;
            }}
            .submenu-item:hover {{
                background-color: #505358;
            }}
            .submenu-item.active {{
                background-color: #5865f2;
            }}
            .main-content {{
                display: flex;
                flex-direction: column;
                height: 100vh;
                position: relative;
            }}
            .header {{
                background-color: #f0f0f0;
                padding: 10px 20px;
                border-bottom: 1px solid #ddd;
            }}
            h2 {{
                margin: 10px 0px;
            }}
            .chat-area {{
                flex: 1;
                overflow-y: auto;
                padding: 20px;
                background-color: #fff;
            }}
            .message {{
                margin-bottom: 15px;
                padding: 10px;
                border-radius: 5px;
                background-color: #f9f9f9;
                border-left: 3px solid #5865f2;
                position: relative;
            }}
            .message.topic {{
                border-left-color: #43b581;
            }}
            .message.entity {{
                border-left-color: #f04747;
            }}
            .message-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 8px;
                flex-wrap: wrap; /* Позволяет переноситься на мобильных */
                gap: 10px;
            }}
            .author {{
                font-weight: bold;
                color: #333;
                white-space: nowrap;
            }}
            .author-info {{
                display: flex;
                align-items: center;
                gap: 10px;
                flex: 1;
                min-width: 0;
            }}

            .timestamp {{
                font-size: 12px;
                color: #888;
                white-space: nowrap;
            }}
            .message-content {{
                margin-bottom: 5px;
                white-space: pre-wrap; /* Сохраняет переносы строк */
                word-wrap: break-word; /* Переносит длинные слова */
                line-height: 1.5;
            }}
            
            .entity-details {{
                margin-top: 10px;
                padding: 10px;
                background-color: #eef2ff;
                border-radius: 5px;
                font-size: 14px;
                white-space: pre-wrap; /* Сохраняет переносы строк */
                word-wrap: break-word; /* Переносит длинные слова */
                line-height: 1.4;
            }}
            .comment-btn {{
                padding: 4px 10px;
                background-color: #5865f2;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                cursor: pointer;
                display: flex;
                align-items: center;
                gap: 4px;
                white-space: nowrap;
            }}
            .comment-btn:hover {{
                background-color: #4752c4;
            }}
            .comment-btn .count {{
                font-weight: bold;
                margin-right: 2px;
            }}
            .entity-field {{
                margin-bottom: 5px;
            }}
            .entity-field-label {{
                font-weight: bold;
                color: #555;
            }}
            .message-actions {{
                display: flex;
                gap: 8px; /* Отступы между кнопками */
                align-items: center;
                flex-wrap: wrap; /* Позволяет кнопкам переноситься на мобильных */
                justify-content: flex-end;
            }}
            .message-form {{
                padding: 15px 20px;
                border-top: 1px solid #ddd;
                background-color: #f9f9f9;
                display: flex;
                gap: 10px;
            }}
            .message-input {{
                flex: 1;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 5px;
                resize: none;
                font-family: inherit;
                white-space: pre-wrap; /* Сохраняет переносы строк при вводе */
                word-wrap: break-word;
            }}
            .send-button {{
                padding: 10px 20px;
                background-color: #5865f2;
                color: white;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-weight: bold;
            }}
            .send-button:hover {{
                background-color: #4752c4;
            }}
            .entity-types {{
                display: flex;
                gap: 10px;
                margin-top: 10px;
                flex-wrap: wrap;
            }}
            .entity-type-btn {{
                padding: 8px 16px;
                background-color: #5865f2;
                color: white;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-weight: bold;
                font-size: 14px;
            }}
            .entity-type-btn:hover {{
                background-color: #4752c4;
            }}
            .hidden {{
                display: none;
            }}

            /* Модальное окно */
            .modal-overlay {{
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background-color: rgba(0, 0, 0, 0.5);
                display: none;
                justify-content: center;
                align-items: center;
                z-index: 1000;
            }}
            .modal {{
                background-color: white;
                border-radius: 8px;
                padding: 20px;
                max-width: 500px;
                width: calc(100% - 40px); /* Responsive width */
                max-height: 90vh;
                overflow-y: auto;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
                margin: 20px; /* Add margin for mobile */
            }}
            .modal-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
                padding-bottom: 10px;
                border-bottom: 1px solid #eee;
            }}
            .modal-title {{
                font-size: 18px;
                font-weight: bold;
                color: #333;
            }}
            .close-modal {{
                background: none;
                border: none;
                font-size: 20px;
                cursor: pointer;
                color: #666;
            }}
            .form-group {{
                margin-bottom: 15px;
            }}
            .form-label {{
                display: block;
                margin-bottom: 5px;
                font-weight: bold;
                color: #555;
            }}
            .form-input, .form-textarea, .form-select {{
                width: 100%;
                max-width: 100%; /* Prevent overflow */
                padding: 8px 12px;
                border: 1px solid #ddd;
                border-radius: 4px;
                font-family: inherit;
                font-size: 14px;
                box-sizing: border-box; /* Include padding in width */
            }}
            .form-textarea {{
                min-height: 80px;
                resize: vertical;
            }}
            .form-checkbox {{
                margin-right: 8px;
            }}
            .modal-footer {{
                display: flex;
                justify-content: flex-end;
                gap: 10px;
                margin-top: 20px;
                padding-top: 15px;
                border-top: 1px solid #eee;
            }}
            .cancel-btn {{
                padding: 8px 16px;
                background-color: #99aab5;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-weight: bold;
            }}
            .cancel-btn:hover {{
                background-color: #8798a3;
            }}
            .create-btn {{
                padding: 8px 16px;
                background-color: #57f287;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-weight: bold;
            }}
            .create-btn:hover {{
                background-color: #46d975;
            }}

            /* Дровер для комментариев */
            .drawer-overlay {{
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background-color: rgba(0, 0, 0, 0.5);
                display: none;
                z-index: 1000;
            }}
            .drawer {{
                position: fixed;
                top: 0;
                right: 0;
                width: 500px;
                height: 100%;
                background-color: white;
                box-shadow: -4px 0 20px rgba(0, 0, 0, 0.2);
                display: flex;
                flex-direction: column;
                overflow: hidden;
            }}
            .drawer-header {{
                padding: 15px 20px;
                border-bottom: 1px solid #ddd;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .drawer-title {{
                font-size: 16px;
                font-weight: bold;
                color: #333;
            }}
            .close-drawer {{
                background: none;
                border: none;
                font-size: 18px;
                cursor: pointer;
                color: #666;
            }}
            .drawer-content {{
                flex: 1;
                overflow-y: auto;
                padding: 20px;
            }}
            .drawer-form {{
                padding: 15px 20px;
                border-top: 1px solid #ddd;
                background-color: #f9f9f9;
                display: flex;
                gap: 10px;
            }}
            .drawer-message {{
                margin-bottom: 15px;
                padding: 10px;
                border-radius: 5px;
                background-color: #f9f9f9;
                border-left: 3px solid #5865f2;
                text-wrap-mode: wrap;
                word-wrap: break-word;
                line-height: 1.5;
            }}
            .drawer-message .author-info {{
                display: flex;
                align-items: center;
                gap: 10px;
                flex: 1;
                min-width: 0;
            }}
            .drawer-message .author {{
                font-weight: bold;
                color: #333;
                white-space: nowrap;
            }}
            .drawer-message .timestamp {{
                font-size: 12px;
                color: #888;
                white-space: nowrap;
            }}
            .drawer-message.topic {{
                border-left-color: #43b581;
            }}
            .drawer-message.entity {{
                border-left-color: #f04747;
            }}
            .drawer-message .message-content {{
                margin-top: 8px;
                white-space: pre-wrap; /* Важно для сохранения форматирования */
                word-wrap: break-word;
                line-height: 1.5;
                word-break: break-word;
                overflow-wrap: break-word; /* Дополнительное свойство для переноса длинных слов */
                max-width: 100%; /* Ограничиваем ширину */
            }}
            .edit-btn {{
                padding: 4px 10px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                cursor: pointer;
                display: flex;
                align-items: center;
                gap: 4px;
                white-space: nowrap;
            }}
            .edit-btn:hover {{
                background-color: #45a049;
            }}
            .read-info-btn {{
                padding: 4px 10px;
                background-color: #ff9800;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                cursor: pointer;
                display: flex;
                align-items: center;
                gap: 4px;
                white-space: nowrap;
            }}
            .read-info-btn:hover {{
                background-color: #e68900;
            }}
            .mark-read-btn {{
                padding: 4px 10px;
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                cursor: pointer;
                display: flex;
                align-items: center;
                gap: 4px;
                white-space: nowrap;
            }}
            .mark-read-btn:hover {{
                background-color: #0b7dda;
            }}
            
            .message.unread-info {{
                background-color: rgba(255, 200, 200, 0.2);
                border-left: 3px solid #f04747;
            }}
            
            /* Модальное окно для редактирования Entity */
            #edit-entity-modal-overlay {{
                z-index: 1001;
            }}
            
            .status-group {{
                display: none;
            }}

            /* Responsive fixes for modals */
            @media (max-width: 600px) {{
                .modal {{
                    width: calc(100% - 30px);
                    margin: 15px;
                    padding: 15px;
                }}
                
                .form-input, .form-textarea, .form-select {{
                    padding: 6px 10px;
                }}
            }}

            /* Fix for required users list */
            #required-users-list, #edit-required-users-list {{
                max-height: 200px;
                overflow-y: auto;
                padding: 10px;
                background-color: #f9f9f9;
                border-radius: 4px;
                border: 1px solid #eee;
            }}

            #required-users-list label, #edit-required-users-list label {{
                display: flex;
                align-items: center;
                margin-bottom: 8px;
                padding: 5px;
                border-radius: 4px;
                transition: background-color 0.2s;
            }}

            #required-users-list label:hover, #edit-required-users-list label:hover {{
                background-color: #f0f0f0;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="sidebar" id="sidebar">
                <!-- Навигация будет загружена через JavaScript -->
            </div>

            <div class="main-content">
                <div class="header">
                    <h2>{chat_title}</h2>
                    <div class="entity-types" id="entity-types">
                        <!-- Кнопки для типов Entity будут загружены через JavaScript -->
                    </div>
                </div>

                <div class="chat-area" id="chat-area">
                    <!-- История будет загружена через JavaScript -->
                </div>

                <div class="message-form">
                    <textarea 
                        class="message-input" 
                        id="message-input" 
                        placeholder="Type your message here..." 
                        rows="3"
                    ></textarea>
                    <button class="send-button" id="send-button">Send</button>
                </div>
            </div>
        </div>

        <!-- Модальное окно для создания Entity -->
        <div class="modal-overlay" id="entity-modal-overlay">
            <div class="modal" id="entity-modal">
                <div class="modal-header">
                    <div class="modal-title" id="modal-title">Create Entity</div>
                    <button class="close-modal" id="close-modal">&times;</button>
                </div>
                <form id="entity-form">
                    <div class="form-group">
                        <label class="form-label" for="entity-title">Title</label>
                        <input type="text" class="form-input" id="entity-title" required>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="entity-body">Body</label>
                        <textarea class="form-textarea" id="entity-body" rows="4" required></textarea>
                    </div>

                    <!-- Общие поля для всех Entity -->
                    <div class="form-group" id="priority-group" style="display: none;">
                        <label class="form-label" for="entity-priority">Priority</label>
                        <input type="number" class="form-input" id="entity-priority" min="1" max="10" value="5">
                    </div>

                    <div class="form-group" id="severity-group" style="display: none;">
                        <label class="form-label" for="entity-severity">Severity</label>
                        <input type="number" class="form-input" id="entity-severity" min="1" max="10" value="5">
                    </div>

                    <div class="form-group" id="reproducible-group" style="display: none;">
                        <label class="form-label">
                            <input type="checkbox" class="form-checkbox" id="entity-reproducible">
                            Reproducible
                        </label>
                    </div>

                    <div class="form-group" id="deadline-group" style="display: none;">
                        <label class="form-label" for="entity-deadline">Deadline</label>
                        <input type="datetime-local" class="form-input" id="entity-deadline">
                    </div>

                    <div class="form-group" id="executor-group" style="display: none;">
                        <label class="form-label" for="entity-executor">Executor</label>
                        <select class="form-select" id="entity-executor">
                            <!-- Заполнится через JavaScript -->
                        </select>
                    </div>

                    <div class="form-group" id="qa-group" style="display: none;">
                        <label class="form-label" for="entity-qa">QA</label>
                        <select class="form-select" id="entity-qa">
                            <!-- Заполнится через JavaScript -->
                        </select>
                    </div>

                    <div class="form-group" id="required-users-group" style="display: none;">
                        <label class="form-label">Required Users</label>
                        <div id="required-users-list">
                            <!-- Список пользователей с чекбоксами -->
                        </div>
                    </div>

                    <div class="modal-footer">
                        <button type="button" class="cancel-btn" id="cancel-modal">Cancel</button>
                        <button type="submit" class="create-btn">Create</button>
                    </div>
                </form>
            </div>
        </div>

        <!-- Дровер для комментариев -->
        <div class="drawer-overlay" id="comments-drawer-overlay">
            <div class="drawer" id="comments-drawer">
                <div class="drawer-header">
                    <div class="drawer-title">Comments</div>
                    <button class="close-drawer" id="close-drawer">&times;</button>
                </div>
                <div class="drawer-content" id="drawer-content">
                    <!-- Исходное сообщение и комментарии будут загружены через JavaScript -->
                </div>
                <div class="drawer-form">
                    <textarea 
                        class="message-input" 
                        id="comment-input" 
                        placeholder="Write a comment..." 
                        rows="2"
                    ></textarea>
                    <button class="send-button" id="send-comment">Send</button>
                </div>
            </div>
        </div>
        
        <!-- Модальное окно для редактирования Entity -->
        <div class="modal-overlay" id="edit-entity-modal-overlay">
            <div class="modal" id="edit-entity-modal">
                <div class="modal-header">
                    <div class="modal-title" id="edit-modal-title">Edit Entity</div>
                    <button class="close-modal" id="edit-close-modal">&times;</button>
                </div>
                <form id="edit-entity-form">
                    <input type="hidden" id="edit-entity-id">
                    
                    <div class="form-group">
                        <label class="form-label" for="edit-entity-title">Title</label>
                        <input type="text" class="form-input" id="edit-entity-title" required>
                    </div>
        
                    <div class="form-group">
                        <label class="form-label" for="edit-entity-body">Body</label>
                        <textarea class="form-textarea" id="edit-entity-body" rows="4" required></textarea>
                    </div>
        
                    <!-- Общие поля для всех Entity -->
                    <div class="form-group" id="edit-priority-group" style="display: none;">
                        <label class="form-label" for="edit-entity-priority">Priority</label>
                        <input type="number" class="form-input" id="edit-entity-priority" min="1" max="10" value="5">
                    </div>
        
                    <div class="form-group" id="edit-severity-group" style="display: none;">
                        <label class="form-label" for="edit-entity-severity">Severity</label>
                        <input type="number" class="form-input" id="edit-entity-severity" min="1" max="10" value="5">
                    </div>
        
                    <div class="form-group" id="edit-reproducible-group" style="display: none;">
                        <label class="form-label">
                            <input type="checkbox" class="form-checkbox" id="edit-entity-reproducible">
                            Reproducible
                        </label>
                    </div>
        
                    <div class="form-group" id="edit-deadline-group" style="display: none;">
                        <label class="form-label" for="edit-entity-deadline">Deadline</label>
                        <input type="datetime-local" class="form-input" id="edit-entity-deadline">
                    </div>
        
                    <div class="form-group" id="edit-executor-group" style="display: none;">
                        <label class="form-label" for="edit-entity-executor">Executor</label>
                        <select class="form-select" id="edit-entity-executor">
                            <!-- Заполнится через JavaScript -->
                        </select>
                    </div>
        
                    <div class="form-group" id="edit-qa-group" style="display: none;">
                        <label class="form-label" for="edit-entity-qa">QA</label>
                        <select class="form-select" id="edit-entity-qa">
                            <!-- Заполнится через JavaScript -->
                        </select>
                    </div>
        
                    <div class="form-group" id="edit-required-users-group" style="display: none;">
                        <label class="form-label">Required Users</label>
                        <div id="edit-required-users-list">
                            <!-- Список пользователей с чекбоксами -->
                        </div>
                    </div>
        
                    <!-- Поле статуса (показывается только для типов, у которых есть статус) -->
                    <div class="form-group status-group" id="edit-status-group">
                        <label class="form-label" for="edit-entity-status">Status</label>
                        <select class="form-select" id="edit-entity-status">
                            <option value="created">Created</option>
                            <option value="in_progress">In Progress</option>
                            <option value="completed">Completed</option>
                            <option value="rejected">Rejected</option>
                            <option value="closed">Closed</option>
                        </select>
                    </div>
        
                    <div class="modal-footer">
                        <button type="button" class="cancel-btn" id="edit-cancel-modal">Cancel</button>
                        <button type="submit" class="create-btn">Save Changes</button>
                    </div>
                </form>
            </div>
        </div>
    
        <!-- Дровер для отображения информации о прочтении Info -->
        <div class="drawer-overlay" id="info-reads-drawer-overlay">
            <div class="drawer" id="info-reads-drawer">
                <div class="drawer-header">
                    <div class="drawer-title">Info Read Status</div>
                    <button class="close-drawer" id="close-info-reads-drawer">&times;</button>
                </div>
                <div class="drawer-content" id="info-reads-content">
                    <!-- Содержимое будет загружено через JavaScript -->
                </div>
            </div>
        </div>

        <script>
            // Сохраняем данные для использования в JavaScript
            const chatData = {{
                chatId: {chat_id_escaped},
                chatTitle: {chat_title_escaped},
                isChannel: {str(is_channel).lower()},
                userId: {user_id_escaped},
                userDisplayName: {user_display_name_escaped},
                history: {history_json},
                availableEntities: {available_entities_json},
                chatUsers: {chat_users_escaped}
            }};

            // Переменные для состояния
            let currentThreadId = null;
            let currentThreadType = null;
            let allUsers = [];
            
            let editingEntityId = null;
            let infoReadData = null;
            let currentInfoReadsEntityId = null;
            
            let ws = null;
            
            function connectWebSocket() {{
                const roomType = chatData.isChannel ? 'channel' : 'chat';
                const roomId = chatData.chatId;
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const wsUrl = `${{protocol}}//${{window.location.host}}/ws/${{roomType}}/${{roomId}}`;
                
                ws = new WebSocket(wsUrl);
                
                ws.onopen = function() {{
                    console.log('WebSocket connected');
                }};
                
                ws.onmessage = function(event) {{
                    try {{
                        const message = JSON.parse(event.data);
                        handleWebSocketMessage(message);
                    }} catch (error) {{
                        console.error('Error parsing WebSocket message:', error);
                    }}
                }};

                ws.onerror = function(error) {{
                    console.error('WebSocket error:', error);
                }};

                ws.onclose = function() {{
                    console.log('WebSocket disconnected, reconnecting...');
                    setTimeout(connectWebSocket, 3000);
                }};
            }}

            // Обработчик сообщений WebSocket
            function handleWebSocketMessage(message) {{
                switch(message.event) {{
                    case 'entity_created':
                        handleEntityCreated(message.data);
                        break;
                    case 'entity_updated':
                        handleEntityUpdated(message.data);
                        break;
                    case 'topic_created':
                        handleTopicCreated(message.data);
                        break;
                    case 'comment_created':
                        handleCommentCreated(message.data);
                        break;
                    case 'info_read':
                        handleInfoRead(message.data);
                        break;
                }}
            }}

            // Обработчики для каждого типа событий
            async function handleEntityCreated(data) {{
                try {{
                    const response = await fetch(`/api/entities/${{data.entity_id}}`);
                    if (!response.ok) return;
            
                    const entity = await response.json();
            
                    // Формируем базовые данные Entity
                    const entityData = {{
                        type: 'entity',
                        id: data.entity_id,
                        entity_type: entity.type,
                        title: entity.title,
                        author_id: data.author_id,
                        author_display_name: data.author_display_name || 'Unknown',
                        created_at: data.created_at || new Date().toISOString(),
                        body: entity.body,
                        priority: entity.priority,
                        status: entity.status || 'created',
                        deadline: entity.deadline,
                        executor: entity.executor_display_name || entity.executor,
                        qa: entity.qa_display_name || entity.qa,
                        reproducible: entity.reproducible,
                        severity: entity.severity,
                        required_users: [],
                        has_read: false,
                        read_count: 0,
                        comment_count: 0
                    }};
            
                    // ОСОБАЯ ОБРАБОТКА ДЛЯ INFO
                    if (entity.type === 'info') {{
                        // Используем required_user_ids из данных WebSocket или из entity
                        const requiredUserIds = data.required_user_ids || entity.required_user_ids || [];
                        
                        // Преобразуем ID в объекты пользователей
                        const requiredUsers = [];
                        
                        // Для каждого required пользователя создаем объект
                        requiredUserIds.forEach(userId => {{
                            // Ищем пользователя в chatUsers или allUsers
                            let user = chatData.chatUsers.find(u => u.id === userId);
                            if (!user && allUsers.length > 0) {{
                                user = allUsers.find(u => u.id === userId);
                            }}
                            
                            if (user) {{
                                requiredUsers.push({{
                                    id: user.id,
                                    display_name: user.display_name
                                }});
                            }} else {{
                                // Если пользователь не найден, создаем минимальный объект
                                requiredUsers.push({{
                                    id: userId,
                                    display_name: 'Unknown User'
                                }});
                            }}
                        }});
                        
                        entityData.required_users = requiredUsers;
                        
                        // Определяем, должен ли текущий пользователь прочитать это Info
                        const isUserInRequiredList = requiredUserIds.includes(chatData.userId);
                        const isAuthor = data.author_id === chatData.userId;
                        
                        // Автор всегда видит "0/X" и не видит кнопку "Mark as read"
                        if (isAuthor) {{
                            entityData.has_read = true; // Для автора всегда "прочитано" (чтобы не выделялось)
                            entityData.read_count = 0;
                        }} else if (isUserInRequiredList) {{
                            // Для других пользователей в списке required - еще не прочитано
                            entityData.has_read = false;
                            entityData.read_count = 0;
                        }} else {{
                            // Для пользователей не в списке required - не отображаем ничего
                            entityData.has_read = true;
                            entityData.read_count = requiredUserIds.length; // Показываем общее количество
                        }}
                    }}
            
                    // Добавляем в историю
                    chatData.history.push(entityData);
                    // Сортируем историю по времени
                    chatData.history.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
                    renderHistory();
            
                    // Прокручиваем вниз
                    const chatArea = document.getElementById('chat-area');
                    if (chatArea) {{
                        setTimeout(() => {{
                            chatArea.scrollTop = chatArea.scrollHeight;
                        }}, 100);
                    }}
                }} catch (error) {{
                    console.error('Error loading created entity:', error);
                }}
            }}

            function handleEntityUpdated(data) {{
                // Находим Entity в истории
                const entityIndex = chatData.history.findIndex(
                    item => item.type === 'entity' && item.id === data.entity_id
                );
            
                if (entityIndex !== -1) {{
                    const entity = chatData.history[entityIndex];
                    
                    // Обновляем основные поля
                    Object.keys(data.updated_fields).forEach(field => {{
                        if (field !== 'required_user_ids' && field !== 'read_count') {{
                            entity[field] = data.updated_fields[field];
                        }}
                    }});
                    
                    // ОСОБАЯ ОБРАБОТКА ДЛЯ INFO
                    if (entity.entity_type === 'info') {{
                        // Обновляем список required_user_ids если есть
                        if (data.updated_fields.required_user_ids) {{
                            const requiredUserIds = data.updated_fields.required_user_ids;
                            
                            // Обновляем список required_users
                            const requiredUsers = [];
                            requiredUserIds.forEach(userId => {{
                                // Ищем пользователя в chatUsers или allUsers
                                let user = chatData.chatUsers.find(u => u.id === userId);
                                if (!user && allUsers.length > 0) {{
                                    user = allUsers.find(u => u.id === userId);
                                }}
                                
                                if (user) {{
                                    requiredUsers.push({{
                                        id: user.id,
                                        display_name: user.display_name
                                    }});
                                }} else {{
                                    requiredUsers.push({{
                                        id: userId,
                                        display_name: 'Unknown User'
                                    }});
                                }}
                            }});
                            
                            entity.required_users = requiredUsers;
                        }}
                        
                        // Обновляем счетчик прочитавших
                        if (data.updated_fields.read_count !== undefined) {{
                            entity.read_count = data.updated_fields.read_count;
                        }}
                        
                        // ПЕРЕСЧИТЫВАЕМ has_read ДЛЯ ТЕКУЩЕГО ПОЛЬЗОВАТЕЛЯ
                        const isAuthor = entity.author_id === chatData.userId;
                        const isUserInRequiredList = entity.required_users && 
                            entity.required_users.some(u => u.id === chatData.userId);
                        
                        if (isAuthor) {{
                            // Автор всегда считается прочитавшим
                            entity.has_read = true;
                        }} else if (isUserInRequiredList) {{
                            checkIfCurrentUserHasRead(entity.id);
                        }} else {{
                            // Пользователь не в списке required
                            entity.has_read = true;
                        }}
                    }}
                    
                    entity.updated_at = data.updated_at;
            
                    // Перерисовываем историю
                    renderHistory();
                }}
            }}
            
            async function checkIfCurrentUserHasRead(entityId) {{
                try {{
                    const response = await fetch(`/api/entities/${{entityId}}`);
                    if (!response.ok) return;
                    
                    const entity = await response.json();
                    if (entity.type !== 'info') return;
                    
                    // Находим entity в истории
                    const entityIndex = chatData.history.findIndex(e => e.id === entityId);
                    if (entityIndex === -1) return;
                    
                    const historyEntity = chatData.history[entityIndex];
                    
                    // Проверяем через API статуса прочтения
                    const readStatusResponse = await fetch(`/api/info/${{entityId}}/reads`);
                    if (readStatusResponse.ok) {{
                        const readStatus = await readStatusResponse.json();
                        
                        // Проверяем, есть ли текущий пользователь в списке прочитавших
                        const hasRead = readStatus.read.some(item => item.user.id === chatData.userId);
                        historyEntity.has_read = hasRead;
                        
                        // Обновляем счетчик
                        historyEntity.read_count = readStatus.read_count;
                        
                        // Перерисовываем историю
                        renderHistory();
                    }}
                }} catch (error) {{
                    console.error('Error checking read status:', error);
                }}
            }}

            async function handleTopicCreated(data) {{
                // Загружаем полные данные Topic
                try {{
                    // Добавляем в историю
                    const topicData = {{
                        type: 'topic',
                        id: data.topic_id,
                        text: data.text,
                        author_id: data.author_id,
                        author_display_name: data.author_display_name || 'Unknown',
                        created_at: data.created_at,
                        comment_count: 0
                    }};

                    chatData.history.push(topicData);
                    chatData.history.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
                    renderHistory();

                    // Прокручиваем вниз
                    const chatArea = document.getElementById('chat-area');
                    if (chatArea) {{
                        setTimeout(() => {{
                            chatArea.scrollTop = chatArea.scrollHeight;
                        }}, 100);
                    }}
                }} catch (error) {{
                    console.error('Error loading created topic:', error);
                }}
            }}

            function handleCommentCreated(data) {{
                // Обновляем счетчик комментариев
                const itemIndex = chatData.history.findIndex(
                    item => item.id === data.thread_id
                );

                if (itemIndex !== -1) {{
                    const item = chatData.history[itemIndex];
                    item.comment_count = (item.comment_count || 0) + 1;

                    // Обновляем отображение счетчика
                    const commentBtn = document.querySelector(`button[onclick*="${{data.thread_id}}"] .count`);
                    if (commentBtn) {{
                        commentBtn.textContent = item.comment_count;
                    }}

                    // Если дровер с комментариями открыт для этого thread_id, обновляем его
                    if (currentThreadId === data.thread_id) {{
                        addCommentToDrawer(data);
                    }}
                }}
            }}

            async function addCommentToDrawer(data) {{
                // Загружаем полные данные комментария
                try {{
                    const response = await fetch(`/api/comments/single/${{data.comment_id}}`);
                    if (!response.ok) return;
                    
                    const comment = await response.json();
                    
                    // Проверяем, что комментарий относится к открытому thread
                    if (comment.thread_id !== currentThreadId) {{
                        return;
                    }}
                    
                    // Удаляем плейсхолдер "No comments yet", если он есть
                    const placeholder = document.getElementById('no-comments-placeholder');
                    if (placeholder) {{
                        placeholder.remove();
                    }}
                    
                    // Добавляем комментарий в дровер
                    const drawerContent = document.getElementById('drawer-content');
                    if (drawerContent) {{
                        const commentHtml = `
                            <div class="drawer-message">
                                <div class="message-header">
                                    <div class="author-info">
                                        <span class="author">${{escapeHtml(comment.author_display_name)}}</span>
                                        <span class="timestamp">${{new Date(comment.created_at).toLocaleString()}}</span>
                                    </div>
                                </div>
                                <div class="message-content">${{escapeHtml(comment.body)}}</div>
                            </div>
                        `;
                        
                        drawerContent.insertAdjacentHTML('beforeend', commentHtml);
                        
                        // Прокручиваем вниз к новому комментарию
                        drawerContent.scrollTop = drawerContent.scrollHeight;
                    }}
                }} catch (error) {{
                    console.error('Error loading comment:', error);
                }}
            }}

            function handleInfoRead(data) {{
                // Находим Info Entity в истории
                const entityIndex = chatData.history.findIndex(
                    item => item.type === 'entity' && 
                            item.id === data.entity_id && 
                            item.entity_type === 'info'
                );
            
                if (entityIndex !== -1) {{
                    const entity = chatData.history[entityIndex];
                    
                    // Обновляем счетчик прочитавших
                    entity.read_count = data.read_count || 0;
                    
                    // Если текущий пользователь прочитал, обновляем has_read
                    if (data.user_id === chatData.userId) {{
                        entity.has_read = true;
                    }}
                    
                    // Обновляем отображение счетчика прочитавших
                    const readCountElement = document.querySelector(`[data-entity-id="${{data.entity_id}}"] .read-count`);
                    if (readCountElement && entity.required_users) {{
                        const totalCount = entity.required_users.length;
                        readCountElement.textContent = `${{entity.read_count}}/${{totalCount}}`;
                    }}
            
                    // Обновляем кнопку прочтения для автора
                    const readInfoBtn = document.querySelector(`button[onclick*="openInfoReadsDrawer('${{data.entity_id}}')"]`);
                    if (readInfoBtn && entity.required_users) {{
                        const totalCount = entity.required_users.length;
                        readInfoBtn.innerHTML = `👁️ ${{entity.read_count}}/${{totalCount}}`;
                    }}
                    
                    // Обновляем выделение непрочитанных Info
                    const messageElement = document.querySelector(`.message[data-entity-id="${{data.entity_id}}"]`);
                    if (messageElement) {{
                        // Проверяем, нужно ли убрать выделение "непрочитанного"
                        if (entity.has_read || entity.author_id === chatData.userId) {{
                            messageElement.classList.remove('unread-info');
                        }} else {{
                            messageElement.classList.add('unread-info');
                        }}
                    }}
                    
                    // Перерисовываем историю
                    renderHistory();
                }}
                if (currentInfoReadsEntityId === data.entity_id) {{
                    openInfoReadsDrawer(data.entity_id);
                }}
            }}

            // Функция загрузки навигации
            async function loadNavigation() {{
                try {{
                    const [userInfoRes, channelsRes, chatsRes] = await Promise.all([
                        fetch('/api/user/info'),
                        fetch('/api/user/channels'),
                        fetch('/api/user/chats')
                    ]);

                    if (!userInfoRes.ok || !channelsRes.ok || !chatsRes.ok) {{
                        console.error('Failed to load navigation data');
                        return;
                    }}

                    const userInfo = await userInfoRes.json();
                    const channels = await channelsRes.json();
                    const chats = await chatsRes.json();

                    const sidebar = document.getElementById('sidebar');

                    let html = '';

                    // Admin button
                    if (userInfo.is_admin) {{
                        html += '<div id="admin-btn" onclick="openAdminPanel()" class="nav-item">Admin Panel</div>';
                    }}

                    // Profile section
                    html += '<div id="profile-btn" onclick="openProfile()" class="nav-item">Profile</div>';
                    html += '<div id="notes-btn" class="nav-item ' + 
                            (chatData.isChannel ? '' : (chatData.chatTitle === 'Notes' ? 'active' : '')) + 
                            '">Notes</div>';
                    html += '<div id="task-explorer" onclick="openTaskExplorer()" class="nav-item">Task Explorer</div>';

                    // Chats section
                    html += '<div id="chats-toggle" class="nav-item">Chats ▼</div>';
                    html += '<div id="chats-submenu" class="submenu">';

                    for (const dept in chats) {{
                        html += '<div style="margin-top: 5px;">';
                        html += '<div style="font-weight: bold; margin: 8px 0 4px 0;">' + dept + '</div>';

                        chats[dept].forEach(chat => {{
                            const isActive = !chatData.isChannel && chatData.chatId === chat.chat_id;
                            html += '<div class="submenu-item ' + (isActive ? 'active' : '') + 
                                    '" onclick="openDirectChat(\\'' + chat.chat_id + '\\')">' +
                                    chat.display_name + '</div>';
                        }});

                        html += '</div>';
                    }}

                    html += '</div>';

                    // Channels section
                    html += '<div id="channels-toggle" class="nav-item">Channels ▼</div>';
                    html += '<div id="channels-submenu" class="submenu">';

                    for (const group in channels) {{
                        html += '<div style="margin-top: 5px;">';
                        html += '<div style="font-weight: bold; margin: 8px 0 4px 0;">' + group + '</div>';

                        channels[group].forEach(channel => {{
                            const isActive = chatData.isChannel && chatData.chatId === channel.id;
                            html += '<div class="submenu-item ' + (isActive ? 'active' : '') + 
                                    '" onclick="openChannel(\\'' + channel.id + '\\')">' +
                                    channel.name + '</div>';
                        }});

                        html += '</div>';
                    }}

                    html += '</div>';

                    // Logout
                    html += '<div id="logout-btn" class="nav-item" onclick="logout()">Logout</div>';

                    sidebar.innerHTML = html;

                    // Добавляем обработчики событий для toggle-меню
                    setupNavigationEvents();

                }} catch (error) {{
                    console.error('Error loading navigation:', error);
                }}
            }}

            // Настройка событий навигации
            function setupNavigationEvents() {{
                // Toggle для Chats
                const chatsToggle = document.getElementById('chats-toggle');
                const chatsSubmenu = document.getElementById('chats-submenu');
                if (chatsToggle && chatsSubmenu) {{
                    chatsToggle.addEventListener('click', () => {{
                        chatsSubmenu.style.display = chatsSubmenu.style.display === 'none' ? 'flex' : 'none';
                    }});
                    chatsSubmenu.style.display = 'flex';
                }}

                // Toggle для Channels
                const channelsToggle = document.getElementById('channels-toggle');
                const channelsSubmenu = document.getElementById('channels-submenu');
                if (channelsToggle && channelsSubmenu) {{
                    channelsToggle.addEventListener('click', () => {{
                        channelsSubmenu.style.display = channelsSubmenu.style.display === 'none' ? 'flex' : 'none';
                    }});
                    channelsSubmenu.style.display = 'flex';
                }}

                // Notes button
                const notesBtn = document.getElementById('notes-btn');
                if (notesBtn) {{
                    notesBtn.addEventListener('click', async () => {{
                        try {{
                            const userInfoRes = await fetch('/api/user/info');
                            if (userInfoRes.ok) {{
                                const userInfo = await userInfoRes.json();
                                if (userInfo.self_chat_id) {{
                                    window.location.href = '/chat/' + userInfo.self_chat_id + '/';
                                }}
                            }}
                        }} catch (error) {{
                            console.error('Error loading user info:', error);
                        }}
                    }});
                }}
            }}

            // Функции перехода
            function openAdminPanel() {{
                window.location.href = '/admin/';
            }}
            
            function openProfile() {{
                window.location.href = '/profile/';
            }}
            
            function openTaskExplorer() {{
                window.location.href = '/task-explorer/';
            }}
            
            function openDirectChat(chatId) {{
                window.location.href = '/chat/' + chatId + '/';
            }}

            function openChannel(channelId) {{
                window.location.href = '/channel/' + channelId + '/';
            }}

            // Функция выхода
            function logout() {{
                // Создаем невидимую форму
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '/logout';
                                
                // Добавляем форму в документ и отправляем
                document.body.appendChild(form);
                form.submit();
            }}

            // Отображение доступных типов Entity в виде кнопок
            function renderEntityTypes() {{
                const entityTypesContainer = document.getElementById('entity-types');
                if (!entityTypesContainer) return;

                let html = '';

                chatData.availableEntities.forEach(entityType => {{
                    const displayName = entityType.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                    html += '<button class="entity-type-btn" data-type="' + entityType + '">' + displayName + '</button>';
                }});

                entityTypesContainer.innerHTML = html;

                // Добавляем обработчики для кнопок Entity
                document.querySelectorAll('.entity-type-btn').forEach(btn => {{
                    btn.addEventListener('click', (e) => {{
                        const entityType = e.target.dataset.type;
                        openEntityModal(entityType);
                    }});
                }});
            }}

            // Отображение истории
            function renderHistory() {{
                const chatArea = document.getElementById('chat-area');
                if (!chatArea) return;
                
                let html = '';
                
                if (chatData.history.length === 0) {{
                    html = '<div style="text-align: center; padding: 40px; color: #888;">No messages yet</div>';
                }} else {{
                    chatData.history.forEach(msg => {{
                        const time = new Date(msg.created_at).toLocaleString();
                        
                        // Проверяем, является ли пользователь в списке required_users
                        const isUserInRequiredList = msg.required_users && 
                            msg.required_users.some(user => user.id === chatData.userId);
                        
                        // Проверяем, является ли сообщение непрочитанным Info для текущего пользователя
                        // Только если пользователь в списке required_users и еще не прочитал
                        const isUnreadInfo = msg.type === 'entity' && 
                                             msg.entity_type === 'info' && 
                                             msg.author_id !== chatData.userId &&
                                             isUserInRequiredList &&
                                             !msg.has_read;
                        
                        const messageClass = 'message ' + msg.type + (isUnreadInfo ? ' unread-info' : '');
                
                        html += '<div class="' + messageClass + '">';
                        
                        // Новый header с разделением информации автора и кнопок
                        html += '<div class="message-header">';
                        html += '<div class="author-info">';
                        html += '<span class="author">' + escapeHtml(msg.author_display_name) + '</span>';
                        html += '<span class="timestamp">' + time + '</span>';
                        html += '</div>';
                        
                        // Блок с кнопками действий
                        html += '<div class="message-actions">';
                        
                        // Кнопка редактирования
                        if (msg.type === 'entity' && (msg.entity_type !== 'info' || msg.author_id === chatData.userId)) {{
                            html += '<button class="edit-btn" onclick="openEditEntityModal(\\'' + msg.id + '\\')">✏️ Edit</button>';
                        }}
                
                        // Кнопка прочтения Info
                        if (msg.type === 'entity' && msg.entity_type === 'info') {{
                            // Проверяем, находится ли текущий пользователь в списке required_users
                            const isUserInRequiredList = msg.required_users && 
                                msg.required_users.some(u => u.id === chatData.userId);
                            
                            if (msg.author_id === chatData.userId) {{
                                // Для автора показываем кнопку с количеством прочитавших
                                const readCount = msg.read_count || 0;
                                const totalCount = msg.required_users ? msg.required_users.length : 0;
                                html += '<button class="read-info-btn" onclick="openInfoReadsDrawer(\\'' + msg.id + '\\')">👁️ ' + readCount + '/' + totalCount + '</button>';
                            }} else if (isUserInRequiredList && !msg.has_read) {{
                                // Для других пользователей, которые в списке required_users и еще не прочитали
                                html += '<button class="mark-read-btn" onclick="markAsRead(\\'' + msg.id + '\\')">👁️ Mark as read</button>';
                            }}
                        }}
                
                        // Кнопка для комментариев (всегда)
                        html += '<button class="comment-btn" onclick="openCommentsDrawer(\\'' + msg.id + '\\', \\'' + msg.type + '\\')">';
                        html += '<span class="count">' + (msg.comment_count || 0) + '</span> 💬';
                        html += '</button>';
                        
                        html += '</div>'; // Закрываем .message-actions
                        html += '</div>'; // Закрываем .message-header
                
                        // Контент сообщения
                        if (msg.type === 'topic') {{
                            html += '<div class="message-content">' + escapeHtml(msg.text) + '</div>';
                        }} else if (msg.type === 'entity') {{
                            html += '<div class="message-content">';
                            html += '<strong>' + escapeHtml(msg.title) + '</strong>';
                            html += '</div>';
                
                            // Детали Entity
                            html += '<div class="entity-details">';
                            html += '<div class="entity-field"><span class="entity-field-label">Type:</span> ' + msg.entity_type + '</div>';
                
                            if (msg.body) {{
                                html += '<div class="entity-field"><span class="entity-field-label">Body:</span><br>' + 
                                        escapeHtml(msg.body) + '</div>';
                            }}
                
                            // Поля в зависимости от типа Entity с правильными статусами
                            switch(msg.entity_type) {{
                                case 'question':
                                    html += '<div class="entity-field"><span class="entity-field-label">Priority:</span> ' + 
                                           (msg.priority !== undefined ? msg.priority : 'Not set') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Status:</span> ' + 
                                           (msg.status ? msg.status.replaceAll('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase()) : 'Created') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Deadline:</span> ' + 
                                           (msg.deadline ? new Date(msg.deadline).toLocaleString() : 'Not set') + '</div>';
                                    break;
                
                                case 'defect':
                                case 'task':
                                    html += '<div class="entity-field"><span class="entity-field-label">Severity:</span> ' + 
                                           (msg.severity !== undefined ? msg.severity : 'Not set') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Reproducible:</span> ' + 
                                           (msg.reproducible !== undefined ? (msg.reproducible ? 'Yes' : 'No') : 'Not set') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Status:</span> ' + 
                                           (msg.status ? msg.status.replaceAll('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase()) : 'Created') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Deadline:</span> ' + 
                                           (msg.deadline ? new Date(msg.deadline).toLocaleString() : 'Not set') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Executor:</span> ' + 
                                           (msg.executor || 'Not assigned') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">QA:</span> ' + 
                                           (msg.qa || 'Not assigned') + '</div>';
                                    break;
                
                                case 'info':
                                    html += '<div class="entity-field"><span class="entity-field-label">Deadline:</span> ' + 
                                           (msg.deadline ? new Date(msg.deadline).toLocaleString() : 'Not set') + '</div>';
                
                                    break;
                
                                case 'proposal':
                                    html += '<div class="entity-field"><span class="entity-field-label">Priority:</span> ' + 
                                           (msg.priority !== undefined ? msg.priority : 'Not set') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Status:</span> ' + 
                                           (msg.status ? msg.status.replaceAll('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase()) : 'Created') + '</div>';
                                    break;
                
                                case 'action_point':
                                    html += '<div class="entity-field"><span class="entity-field-label">Priority:</span> ' + 
                                           (msg.priority !== undefined ? msg.priority : 'Not set') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Executor:</span> ' + 
                                       (msg.executor || 'Not assigned') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Status:</span> ' + 
                                           (msg.status ? msg.status.replaceAll('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase()) : 'Created') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Deadline:</span> ' + 
                                           (msg.deadline ? new Date(msg.deadline).toLocaleString() : 'Not set') + '</div>';
                                    break;
                            }}
                
                            html += '</div>'; // Закрываем .entity-details
                        }}
                
                        html += '</div>'; // Закрываем .message
                    }});
                }}
                
                chatArea.innerHTML = html;
                chatArea.scrollTop = chatArea.scrollHeight;
            }}
            
            function formatDateForInput(utcDateString) {{
                if (!utcDateString) return '';
                const date = new Date(utcDateString);
                const year = date.getFullYear();
                const month = String(date.getMonth() + 1).padStart(2, '0');
                const day = String(date.getDate()).padStart(2, '0');
                const hours = String(date.getHours()).padStart(2, '0');
                const minutes = String(date.getMinutes()).padStart(2, '0');
                return `${{year}}-${{month}}-${{day}}T${{hours}}:${{minutes}}`;
            }}
            
            function parseDateToUTC(dateString) {{
                if (!dateString) return null;
                const date = new Date(dateString);
                return date.toISOString();
            }}

            // Открытие модального окна для создания Entity
            async function openEntityModal(entityType) {{
                // Загружаем список пользователей, если еще не загружены
                if (allUsers.length === 0) {{
                    try {{
                        const response = await fetch('/api/users');
                        if (response.ok) {{
                            allUsers = await response.json();
                        }}
                    }} catch (error) {{
                        console.error('Error loading users:', error);
                        allUsers = [];
                    }}
                }}

                // Обновляем заголовок
                const displayName = entityType.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                document.getElementById('modal-title').textContent = 'Create ' + displayName;

                // Скрываем все группы полей
                document.getElementById('priority-group').style.display = 'none';
                document.getElementById('severity-group').style.display = 'none';
                document.getElementById('reproducible-group').style.display = 'none';
                document.getElementById('deadline-group').style.display = 'none';
                document.getElementById('executor-group').style.display = 'none';
                document.getElementById('qa-group').style.display = 'none';
                document.getElementById('required-users-group').style.display = 'none';

                // Получаем отфильтрованных пользователей
                const chatUsers = getChatUsers();
                const infoUsers = getInfoUsers();

                // Показываем нужные поля в зависимости от типа Entity
                switch(entityType) {{
                    case 'question':
                        document.getElementById('priority-group').style.display = 'block';
                        document.getElementById('deadline-group').style.display = 'block';
                        break;

                    case 'defect':
                    case 'task':
                        document.getElementById('severity-group').style.display = 'block';
                        document.getElementById('reproducible-group').style.display = 'block';
                        document.getElementById('deadline-group').style.display = 'block';
                        document.getElementById('executor-group').style.display = 'block';
                        document.getElementById('qa-group').style.display = 'block';

                        // Заполняем выпадающие списки только пользователями из чата/канала
                        fillUserDropdown('entity-executor', chatUsers, true);
                        fillUserDropdown('entity-qa', chatUsers, true);
                        break;

                    case 'info':
                        document.getElementById('deadline-group').style.display = 'block';
                        document.getElementById('required-users-group').style.display = 'block';
                        
                        // Создаем список пользователей с чекбоксами - только из чата/канала, исключая текущего
                        const usersList = document.getElementById('required-users-list');
                        usersList.innerHTML = '';
                        
                        infoUsers.forEach(user => {{
                            const div = document.createElement('div');
                            div.innerHTML = `
                                <label style="display: flex; align-items: center; margin-bottom: 5px;">
                                    <input type="checkbox" class="form-checkbox" value="${{user.id}}">
                                    <span style="margin-left: 8px;">${{user.display_name}}</span>
                                </label>
                            `;
                            usersList.appendChild(div);
                        }});
                        break;

                    case 'proposal':
                        document.getElementById('priority-group').style.display = 'block';
                        break;

                    case 'action_point':
                        document.getElementById('priority-group').style.display = 'block';
                        document.getElementById('deadline-group').style.display = 'block';
                        document.getElementById('executor-group').style.display = 'block';
                        
                        // Заполняем выпадающий список только пользователями из чата/канала
                        fillUserDropdown('entity-executor', chatUsers, true);
                        break;
                }}

                // Сохраняем тип Entity в data-type формы
                document.getElementById('entity-form').dataset.type = entityType;

                // Показываем модальное окно
                document.getElementById('entity-modal-overlay').style.display = 'flex';
            }}

            // Обновленная функция для открытия модального окна редактирования Entity
            async function openEditEntityModal(entityId) {{
                editingEntityId = entityId;
                
                // Находим Entity в истории
                const entity = chatData.history.find(e => e.id === entityId);
                if (!entity) {{
                    alert('Entity not found');
                    return;
                }}
    
                // Загружаем полные данные Entity
                try {{
                    const response = await fetch('/api/entities/' + entityId);
                    if (!response.ok) throw new Error('Failed to load entity data');
                    const fullEntity = await response.json();
                    
                    // Обновляем modal
                    document.getElementById('edit-modal-title').textContent = 'Edit ' + entity.entity_type;
                    document.getElementById('edit-entity-id').value = entityId;
                    document.getElementById('edit-entity-title').value = entity.title || '';
                    document.getElementById('edit-entity-body').value = fullEntity.body || '';
    
                    // Скрываем все группы полей
                    document.getElementById('edit-priority-group').style.display = 'none';
                    document.getElementById('edit-severity-group').style.display = 'none';
                    document.getElementById('edit-reproducible-group').style.display = 'none';
                    document.getElementById('edit-deadline-group').style.display = 'none';
                    document.getElementById('edit-executor-group').style.display = 'none';
                    document.getElementById('edit-qa-group').style.display = 'none';
                    document.getElementById('edit-required-users-group').style.display = 'none';
                    document.getElementById('edit-status-group').style.display = 'none';
    
                    // Загружаем список пользователей, если нужно
                    if (allUsers.length === 0) {{
                        try {{
                            const usersResponse = await fetch('/api/users');
                            if (usersResponse.ok) {{
                                allUsers = await usersResponse.json();
                            }}
                        }} catch (error) {{
                            console.error('Error loading users:', error);
                        }}
                    }}
    
                    // Получаем отфильтрованных пользователей
                    const chatUsers = getChatUsers();
                    const infoUsers = getInfoUsers();
    
                    // Настраиваем поля в зависимости от типа Entity
                    switch(entity.entity_type) {{
                        case 'question':
                            document.getElementById('edit-priority-group').style.display = 'block';
                            document.getElementById('edit-deadline-group').style.display = 'block';
                            document.getElementById('edit-status-group').style.display = 'block';
                            
                            document.getElementById('edit-entity-priority').value = fullEntity.priority || 5;
                            if (fullEntity.deadline) {{
                                document.getElementById('edit-entity-deadline').value = formatDateForInput(fullEntity.deadline);
                            }}
                            // ... существующий код для статусов ...
                            break;
    
                        case 'defect':
                        case 'task':
                            document.getElementById('edit-severity-group').style.display = 'block';
                            document.getElementById('edit-reproducible-group').style.display = 'block';
                            document.getElementById('edit-deadline-group').style.display = 'block';
                            document.getElementById('edit-executor-group').style.display = 'block';
                            document.getElementById('edit-qa-group').style.display = 'block';
                            document.getElementById('edit-status-group').style.display = 'block';
            
                            document.getElementById('edit-entity-severity').value = fullEntity.severity || 5;
                            document.getElementById('edit-entity-reproducible').checked = fullEntity.reproducible || false;
                            if (fullEntity.deadline) {{
                                document.getElementById('edit-entity-deadline').value = formatDateForInput(fullEntity.deadline);
                            }}
                            
                            // Заполняем выпадающие списки только пользователями из чата/канала
                            fillUserDropdown('edit-entity-executor', chatUsers, true);
                            fillUserDropdown('edit-entity-qa', chatUsers, true);
                            
                            // Устанавливаем выбранные значения
                            if (fullEntity.executor_id) {{
                                document.getElementById('edit-entity-executor').value = fullEntity.executor_id;
                            }}
                            if (fullEntity.qa_id) {{
                                document.getElementById('edit-entity-qa').value = fullEntity.qa_id;
                            }}
                            
                            // ... существующий код для статусов ...
                            break;
    
                        case 'info':
                            document.getElementById('edit-deadline-group').style.display = 'block';
                            document.getElementById('edit-required-users-group').style.display = 'block';
                            
                            if (fullEntity.deadline) {{
                                document.getElementById('edit-entity-deadline').value = formatDateForInput(fullEntity.deadline);
                            }}
                            
                            // Создаем список пользователей с чекбоксами - только из чата/канала, исключая текущего
                            const usersList = document.getElementById('edit-required-users-list');
                            usersList.innerHTML = '';
                            
                            const requiredUserIds = fullEntity.required_user_ids || [];
                            
                            infoUsers.forEach(user => {{
                                const isChecked = requiredUserIds.includes(user.id);
                                const div = document.createElement('div');
                                div.innerHTML = `
                                    <label style="display: flex; align-items: center; margin-bottom: 5px;">
                                        <input type="checkbox" class="form-checkbox" value="${{user.id}}" ${{isChecked ? 'checked' : ''}}>
                                        <span style="margin-left: 8px;">${{user.display_name}}</span>
                                    </label>
                                `;
                                usersList.appendChild(div);
                            }});
                            
                            // Добавляем пользователей, которые были выбраны, но больше не в чате/канале
                            requiredUserIds.forEach(userId => {{
                                if (!infoUsers.some(u => u.id === userId)) {{
                                    const user = allUsers.find(u => u.id === userId);
                                    if (user) {{
                                        const div = document.createElement('div');
                                        div.innerHTML = `
                                            <label style="display: flex; align-items: center; margin-bottom: 5px; color: #999;">
                                                <input type="checkbox" class="form-checkbox" value="${{user.id}}" checked disabled>
                                                <span style="margin-left: 8px;">${{user.display_name}} (not in chat)</span>
                                            </label>
                                        `;
                                        usersList.appendChild(div);
                                    }}
                                }}
                            }});
                            break;
    
                        case 'action_point':
                            document.getElementById('edit-priority-group').style.display = 'block';
                            document.getElementById('edit-deadline-group').style.display = 'block';
                            document.getElementById('edit-executor-group').style.display = 'block';
                            document.getElementById('edit-status-group').style.display = 'block';
                            
                            document.getElementById('edit-entity-priority').value = fullEntity.priority || 5;
                            if (fullEntity.deadline) {{
                                document.getElementById('edit-entity-deadline').value = formatDateForInput(fullEntity.deadline);
                            }}
                            
                            // Заполняем выпадающий список только пользователями из чата/канала
                            fillUserDropdown('edit-entity-executor', chatUsers, true);
                            
                            if (fullEntity.executor_id) {{
                                document.getElementById('edit-entity-executor').value = fullEntity.executor_id;
                            }}
                            
                            break;
                    }}
    
                    // Показываем модальное окно
                    document.getElementById('edit-entity-modal-overlay').style.display = 'flex';
    
                }} catch (error) {{
                    console.error('Error loading entity data:', error);
                    alert('Failed to load entity data');
                }}
            }}
            
            // Получение отфильтрованных пользователей (только из текущего чата/канала)
            function getChatUsers() {{
                // Если уже загружены все пользователи, фильтруем их по chatUsers
                if (allUsers.length > 0) {{
                    return allUsers.filter(user => 
                        chatData.chatUsers.some(chatUser => chatUser.id === user.id)
                    );
                }}
                return [];
            }}

            // Получение отфильтрованных пользователей для Info (исключая текущего)
            function getInfoUsers() {{
                const chatUsers = getChatUsers();
                return chatUsers.filter(user => user.id !== chatData.userId);
            }}

            // Заполнение выпадающего списка пользователями
            function fillUserDropdown(selectId, users, includeCurrentUser = true) {{
                const select = document.getElementById(selectId);
                if (!select) return;
                
                const currentValue = select.value;
                const currentValueIsInList = users.some(user => user.id === currentValue);
                
                // Сохраняем текущее значение, если оно есть
                let currentUser = null;
                if (currentValue && !currentValueIsInList) {{
                    currentUser = allUsers.find(u => u.id === currentValue);
                }}
                
                select.innerHTML = '<option value="">Select user...</option>';
            
                users.forEach(user => {{
                    // Если includeCurrentUser = false, исключаем текущего пользователя
                    if (!includeCurrentUser && user.id === chatData.userId) {{
                        return;
                    }}
                    
                    const option = document.createElement('option');
                    option.value = user.id;
                    option.textContent = user.display_name;
                    
                    if (user.id === chatData.userId) {{
                        option.textContent += ' (me)';
                    }}
                    
                    select.appendChild(option);
                }});
                
                // Если текущее значение есть, но его нет в списке (пользователь вышел из чата),
                // добавляем его как disabled опцию
                if (currentValue && !currentValueIsInList && currentUser) {{
                    const option = document.createElement('option');
                    option.value = currentValue;
                    option.textContent = currentUser.display_name + ' (not in chat)';
                    option.disabled = true;
                    option.selected = true;
                    select.appendChild(option);
                }}
                
                if (currentValue && currentValueIsInList) {{
                    select.value = currentValue;
                }}
            }}
            
            // Функция открытия модального окна редактирования Entity
            async function openEditEntityModal(entityId) {{
                editingEntityId = entityId;
                
                // Находим Entity в истории
                const entity = chatData.history.find(e => e.id === entityId);
                if (!entity) {{
                    alert('Entity not found');
                    return;
                }}
    
                // Загружаем полные данные Entity
                try {{
                    const response = await fetch('/api/entities/' + entityId);
                    if (!response.ok) throw new Error('Failed to load entity data');
                    const fullEntity = await response.json();
                    
                    // Обновляем modal
                    document.getElementById('edit-modal-title').textContent = 'Edit ' + entity.entity_type;
                    document.getElementById('edit-entity-id').value = entityId;
                    document.getElementById('edit-entity-title').value = entity.title || '';
                    document.getElementById('edit-entity-body').value = fullEntity.body || '';
    
                    // Скрываем все группы полей
                    document.getElementById('edit-priority-group').style.display = 'none';
                    document.getElementById('edit-severity-group').style.display = 'none';
                    document.getElementById('edit-reproducible-group').style.display = 'none';
                    document.getElementById('edit-deadline-group').style.display = 'none';
                    document.getElementById('edit-executor-group').style.display = 'none';
                    document.getElementById('edit-qa-group').style.display = 'none';
                    document.getElementById('edit-required-users-group').style.display = 'none';
                    document.getElementById('edit-status-group').style.display = 'none';
    
                    // Загружаем список пользователей, если нужно
                    if (allUsers.length === 0) {{
                        try {{
                            const usersResponse = await fetch('/api/users');
                            if (usersResponse.ok) {{
                                allUsers = await usersResponse.json();
                            }}
                        }} catch (error) {{
                            console.error('Error loading users:', error);
                        }}
                    }}
    
                    // Настраиваем поля в зависимости от типа Entity
                    switch(entity.entity_type) {{
                        case 'question':
                            document.getElementById('edit-priority-group').style.display = 'block';
                            document.getElementById('edit-deadline-group').style.display = 'block';
                            document.getElementById('edit-status-group').style.display = 'block';
                            
                            document.getElementById('edit-entity-priority').value = fullEntity.priority || 5;
                            if (fullEntity.deadline) {{
                                document.getElementById('edit-entity-deadline').value = formatDateForInput(fullEntity.deadline);
                            }}
                            const questionStatusSelect = document.getElementById('edit-entity-status');
                            questionStatusSelect.innerHTML = '';
                            const questionStatuses = ['created', 'answered', 'closed'];
                            questionStatuses.forEach(status => {{
                                const option = document.createElement('option');
                                option.value = status;
                                option.textContent = status.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                                if (fullEntity.status === status) option.selected = true;
                                questionStatusSelect.appendChild(option);
                            }});
                            break;
    
                        case 'defect':
                            document.getElementById('edit-severity-group').style.display = 'block';
                            document.getElementById('edit-reproducible-group').style.display = 'block';
                            document.getElementById('edit-deadline-group').style.display = 'block';
                            document.getElementById('edit-executor-group').style.display = 'block';
                            document.getElementById('edit-qa-group').style.display = 'block';
                            document.getElementById('edit-status-group').style.display = 'block';
            
                            document.getElementById('edit-entity-severity').value = fullEntity.severity || 5;
                            document.getElementById('edit-entity-reproducible').checked = fullEntity.reproducible || false;
                            if (fullEntity.deadline) {{
                                document.getElementById('edit-entity-deadline').value = formatDateForInput(fullEntity.deadline);
                            }}
                            
                            // Заполняем выпадающие списки
                            fillUserDropdown('edit-entity-executor', allUsers, true);
                            fillUserDropdown('edit-entity-qa', allUsers, true);
                            
                            // Устанавливаем выбранные значения
                            if (fullEntity.executor_id) {{
                                document.getElementById('edit-entity-executor').value = fullEntity.executor_id;
                            }}
                            if (fullEntity.qa_id) {{
                                document.getElementById('edit-entity-qa').value = fullEntity.qa_id;
                            }}
                            
                            // Заполняем статусы для Defect
                            const defectStatusSelect = document.getElementById('edit-entity-status');
                            defectStatusSelect.innerHTML = '';
                            const defectStatuses = ['created', 'in_progress', 'ready_for_test', 'in_testing', 'closed'];
                            defectStatuses.forEach(status => {{
                                const option = document.createElement('option');
                                option.value = status;
                                option.textContent = status.replaceAll('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                                if (fullEntity.status === status) option.selected = true;
                                defectStatusSelect.appendChild(option);
                            }});
                            break;
            
                        case 'task':
                            document.getElementById('edit-severity-group').style.display = 'block';
                            document.getElementById('edit-reproducible-group').style.display = 'block';
                            document.getElementById('edit-deadline-group').style.display = 'block';
                            document.getElementById('edit-executor-group').style.display = 'block';
                            document.getElementById('edit-qa-group').style.display = 'block';
                            document.getElementById('edit-status-group').style.display = 'block';
            
                            document.getElementById('edit-entity-severity').value = fullEntity.severity || 5;
                            document.getElementById('edit-entity-reproducible').checked = fullEntity.reproducible || false;
                            if (fullEntity.deadline) {{
                                document.getElementById('edit-entity-deadline').value = formatDateForInput(fullEntity.deadline);
                            }}
                            
                            // Заполняем выпадающие списки
                            fillUserDropdown('edit-entity-executor', allUsers, true);
                            fillUserDropdown('edit-entity-qa', allUsers, true);
                            
                            // Устанавливаем выбранные значения
                            if (fullEntity.executor_id) {{
                                document.getElementById('edit-entity-executor').value = fullEntity.executor_id;
                            }}
                            if (fullEntity.qa_id) {{
                                document.getElementById('edit-entity-qa').value = fullEntity.qa_id;
                            }}
                            
                            // Заполняем статусы для Task
                            const taskStatusSelect = document.getElementById('edit-entity-status');
                            taskStatusSelect.innerHTML = '';
                            const taskStatuses = ['created', 'in_progress', 'ready_for_test', 'in_testing', 'closed'];
                            taskStatuses.forEach(status => {{
                                const option = document.createElement('option');
                                option.value = status;
                                option.textContent = status.replaceAll('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                                if (fullEntity.status === status) option.selected = true;
                                taskStatusSelect.appendChild(option);
                            }});
                            break;
    
                        case 'info':
                            document.getElementById('edit-deadline-group').style.display = 'block';
                            document.getElementById('edit-required-users-group').style.display = 'block';
                            
                            if (fullEntity.deadline) {{
                                document.getElementById('edit-entity-deadline').value = formatDateForInput(fullEntity.deadline);
                            }}
                            
                            // Создаем список пользователей с чекбоксами
                            const usersList = document.getElementById('edit-required-users-list');
                            usersList.innerHTML = '';
                            
                            const requiredUserIds = fullEntity.required_user_ids || [];
                            
                            allUsers.forEach(user => {{
                                if (user.id !== chatData.userId) {{ // Исключаем автора
                                    const div = document.createElement('div');
                                    const isChecked = requiredUserIds.includes(user.id);
                                    div.innerHTML = `
                                        <label style="display: flex; align-items: center; margin-bottom: 5px;">
                                            <input type="checkbox" class="form-checkbox" value="${{user.id}}" ${{isChecked ? 'checked' : ''}}>
                                            <span style="margin-left: 8px;">${{user.display_name}}</span>
                                        </label>
                                    `;
                                    usersList.appendChild(div);
                                }}
                            }});
                            break;
            
                        case 'proposal':
                            document.getElementById('edit-priority-group').style.display = 'block';
                            document.getElementById('edit-status-group').style.display = 'block';
                            
                            document.getElementById('edit-entity-priority').value = fullEntity.priority || 5;
                            
                            // Заполняем статусы для Proposal
                            const proposalStatusSelect = document.getElementById('edit-entity-status');
                            proposalStatusSelect.innerHTML = '';
                            const proposalStatuses = ['created', 'discussed', 'accepted', 'rejected'];
                            proposalStatuses.forEach(status => {{
                                const option = document.createElement('option');
                                option.value = status;
                                option.textContent = status.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                                if (fullEntity.status === status) option.selected = true;
                                proposalStatusSelect.appendChild(option);
                            }});
                            break;
            
                        case 'action_point':
                            document.getElementById('edit-priority-group').style.display = 'block';
                            document.getElementById('edit-deadline-group').style.display = 'block';
                            document.getElementById('edit-executor-group').style.display = 'block';
                            document.getElementById('edit-status-group').style.display = 'block';
                            
                            document.getElementById('edit-entity-priority').value = fullEntity.priority || 5;
                            if (fullEntity.deadline) {{
                                document.getElementById('edit-entity-deadline').value = formatDateForInput(fullEntity.deadline);
                            }}
                            
                            fillUserDropdown('edit-entity-executor', allUsers, true);
                            
                            if (fullEntity.executor_id) {{
                                document.getElementById('edit-entity-executor').value = fullEntity.executor_id;
                            }}
                            
                            // Заполняем статусы для Action Point
                            const actionPointStatusSelect = document.getElementById('edit-entity-status');
                            actionPointStatusSelect.innerHTML = '';
                            const actionPointStatuses = ['created', 'in_progress', 'closed'];
                            actionPointStatuses.forEach(status => {{
                                const option = document.createElement('option');
                                option.value = status;
                                option.textContent = status.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                                if (fullEntity.status === status) option.selected = true;
                                actionPointStatusSelect.appendChild(option);
                            }});
                            break;
                    }}
    
                    // Показываем модальное окно
                    document.getElementById('edit-entity-modal-overlay').style.display = 'flex';
    
                }} catch (error) {{
                    console.error('Error loading entity data:', error);
                    alert('Failed to load entity data');
                }}
            }}
    
            // Функция для отметки Info как прочитанного
            async function markAsRead(infoId) {{
                try {{
                    const response = await fetch('/api/info/' + infoId + '/read', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json'
                        }}
                    }});
            
                    if (response.ok) {{
                        // Находим entity в истории
                        const entityIndex = chatData.history.findIndex(
                            e => e.id === infoId && e.entity_type === 'info'
                        );
                        
                        if (entityIndex !== -1) {{
                            const entity = chatData.history[entityIndex];
                            entity.has_read = true;
                            entity.read_count = (entity.read_count || 0) + 1;
                            
                            // Немедленно обновляем UI
                            renderHistory();
                        }}
                    }} else {{
                        const error = await response.json();
                        alert('Failed to mark as read: ' + (error.detail || 'Unknown error'));
                    }}
                }} catch (error) {{
                    console.error('Error marking as read:', error);
                    alert('Error marking as read');
                }}
            }}
    
            // Функция открытия дровера с информацией о прочтении Info
            async function openInfoReadsDrawer(infoId) {{
                currentInfoReadsEntityId = infoId;
                
                try {{
                    const response = await fetch('/api/info/' + infoId + '/reads');
                    if (!response.ok) throw new Error('Failed to load read status');
                    
                    const readData = await response.json();
                    
                    const content = document.getElementById('info-reads-content');
                    let html = '';
                    
                    const infoEntity = chatData.history.find(e => e.id === infoId && e.entity_type === 'info');
            
                    if (infoEntity) {{
                        html += '<h4>' + escapeHtml(infoEntity.title) + '</h4>';
                    }}
                    
                    // Непрочитавшие
                    if (readData.not_read && readData.not_read.length > 0) {{
                        html += '<p><strong>Not read yet:</strong></p>';
                        readData.not_read.forEach(user => {{
                            html += '<div class="drawer-message" style="background-color: #ffe6e6;">';
                            html += '<div style="display: flex; align-items: center; gap: 10px;">';
                            html += '<div style="width: 10px; height: 10px; background-color: red; border-radius: 50%;"></div>';
                            html += '<span>' + escapeHtml(user.display_name) + '</span>';
                            html += '</div>';
                            html += '</div>';
                        }});
                    }} else {{
                        html += '<div class="drawer-message" style="background-color: #e6ffe6;">';
                        html += '<div style="display: flex; align-items: center; gap: 10px;">';
                        html += '<div style="width: 10px; height: 10px; background-color: green; border-radius: 50%;"></div>';
                        html += '<span>All users have read this info</span>';
                        html += '</div>';
                        html += '</div>';
                    }}
                    
                    // Прочитавшие
                    if (readData.read && readData.read.length > 0) {{
                        html += '<p><strong>Already read:</strong></p>';
                        readData.read.forEach(item => {{
                            html += '<div class="drawer-message" style="background-color: #e6ffe6;">';
                            html += '<div style="display: flex; justify-content: space-between; align-items: center;">';
                            html += '<span>' + escapeHtml(item.user.display_name) + '</span>';
                            html += '<span style="font-size: 12px; color: #666;">' + 
                                   (item.acknowledged_at ? new Date(item.acknowledged_at).toLocaleString() : 'Unknown') + '</span>';
                            html += '</div>';
                            html += '</div>';
                        }});
                    }}
                    
                    content.innerHTML = html;
                    document.getElementById('info-reads-drawer-overlay').style.display = 'block';
                    
                }} catch (error) {{
                    console.error('Error loading read status:', error);
                    alert('Failed to load read status');
                }}
            }}
    
            // Настройка модального окна редактирования
            function setupEditModal() {{
                const modalOverlay = document.getElementById('edit-entity-modal-overlay');
                const closeModal = document.getElementById('edit-close-modal');
                const cancelModal = document.getElementById('edit-cancel-modal');
                const entityForm = document.getElementById('edit-entity-form');
    
                if (!modalOverlay || !closeModal || !cancelModal || !entityForm) return;
    
                function closeEditModal() {{
                    modalOverlay.style.display = 'none';
                    entityForm.reset();
                    editingEntityId = null;
                }}
    
                modalOverlay.addEventListener('click', (e) => {{
                    if (e.target === modalOverlay) {{
                        closeEditModal();
                    }}
                }});
    
                closeModal.addEventListener('click', closeEditModal);
                cancelModal.addEventListener('click', closeEditModal);
    
                // Отправка формы редактирования
                entityForm.addEventListener('submit', async (e) => {{
                    e.preventDefault();
    
                    if (!editingEntityId) return;
    
                    const entity = chatData.history.find(e => e.id === editingEntityId);
                    if (!entity) return;
    
                    const formData = {{
                        title: document.getElementById('edit-entity-title').value,
                        body: document.getElementById('edit-entity-body').value,
                    }};
    
                    // Добавляем дополнительные поля в зависимости от типа Entity
                    switch(entity.entity_type) {{
                        case 'question':
                            formData.priority = parseInt(document.getElementById('edit-entity-priority').value) || 5;
                            formData.deadline = parseDateToUTC(document.getElementById('edit-entity-deadline').value) || null;
                            formData.status = document.getElementById('edit-entity-status').value;
                            break;
    
                        case 'defect':
                        case 'task':
                            formData.severity = parseInt(document.getElementById('edit-entity-severity').value) || 5;
                            formData.reproducible = document.getElementById('edit-entity-reproducible').checked;
                            formData.deadline = parseDateToUTC(document.getElementById('edit-entity-deadline').value) || null;
                            formData.executor_id = document.getElementById('edit-entity-executor').value || null;
                            formData.qa_id = document.getElementById('edit-entity-qa').value || null;
                            formData.status = document.getElementById('edit-entity-status').value;
                            break;
    
                        case 'info':
                            formData.deadline = parseDateToUTC(document.getElementById('edit-entity-deadline').value) || null;
                            const checkboxes = document.querySelectorAll('#edit-required-users-list input[type="checkbox"]:checked');
                            formData.required_user_ids = Array.from(checkboxes).map(cb => cb.value);
                            break;
    
                        case 'proposal':
                            formData.priority = parseInt(document.getElementById('edit-entity-priority').value) || 5;
                            formData.status = document.getElementById('edit-entity-status').value;
                            break;
    
                        case 'action_point':
                            formData.priority = parseInt(document.getElementById('edit-entity-priority').value) || 5;
                            formData.deadline = parseDateToUTC(document.getElementById('edit-entity-deadline').value) || null;
                            formData.executor_id = document.getElementById('edit-entity-executor').value || null;
                            formData.status = document.getElementById('edit-entity-status').value;
                            break;
                    }}
    
                    try {{
                        const response = await fetch('/api/entities/' + editingEntityId, {{
                            method: 'PATCH',
                            headers: {{
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify(formData)
                        }});
    
                        if (response.ok) {{
                            closeEditModal();
                        }} else {{
                            const error = await response.json();
                            alert('Failed to update entity: ' + (error.detail || 'Unknown error'));
                        }}
                    }} catch (error) {{
                        console.error('Error updating entity:', error);
                        alert('Error updating entity');
                    }}
                }});
            }}
    
            // Настройка дровера для прочтения Info
            function setupInfoReadsDrawer() {{
                const drawerOverlay = document.getElementById('info-reads-drawer-overlay');
                const closeDrawer = document.getElementById('close-info-reads-drawer');
    
                if (!drawerOverlay || !closeDrawer) return;
    
                drawerOverlay.addEventListener('click', (e) => {{
                    if (e.target === drawerOverlay) {{
                        drawerOverlay.style.display = 'none';
                    }}
                }});
    
                closeDrawer.addEventListener('click', () => {{
                    drawerOverlay.style.display = 'none';
                }});
            }}

            // Открытие дровера для комментариев
            async function openCommentsDrawer(threadId, threadType) {{
                currentThreadId = threadId;
                currentThreadType = threadType;
            
                // Загружаем комментарии
                try {{
                    const response = await fetch('/api/comments/' + threadId);
                    if (!response.ok) throw new Error('Failed to load comments');
            
                    const data = await response.json();
            
                    // Отображаем в дровере
                    const drawerContent = document.getElementById('drawer-content');
                    let html = '';
            
                    // Исходное сообщение
                    const originalMessage = chatData.history.find(m => m.id === threadId);
                    if (originalMessage) {{
                        const messageClass = 'drawer-message ' + originalMessage.type;
                        html += '<div class="' + messageClass + '">';
                        html += '<div class="message-header">';
                        html += '<div class="author-info">';
                        html += '<span class="author">' + escapeHtml(originalMessage.author_display_name) + '</span>';
                        html += '<span class="timestamp">' + new Date(originalMessage.created_at).toLocaleString() + '</span>';
                        html += '</div>';
                        html += '</div>';
            
                        if (originalMessage.type === 'topic') {{
                            html += '<div class="message-content">' + escapeHtml(originalMessage.text) + '</div>';
                        }} else if (originalMessage.type === 'entity') {{
                            html += '<div class="message-content">';
                            html += '<strong>' + escapeHtml(originalMessage.title) + '</strong> (' + originalMessage.entity_type + ')';
                            if (originalMessage.body) {{
                                html += '<div class="entity-details" style="margin-top: 10px;">' + escapeHtml(originalMessage.body) + '</div>';
                            }}
                            html += '</div>';
                        }}
            
                        html += '</div>';
            
                        // Разделитель
                        html += '<hr style="margin: 20px 0; border-color: #eee;">';
                    }}
            
                    // Комментарии
                    if (data.comments && data.comments.length > 0) {{
                        data.comments.forEach(comment => {{
                            html += '<div class="drawer-message">';
                            html += '<div class="message-header">';
                            html += '<div class="author-info">';
                            html += '<span class="author">' + escapeHtml(comment.author_display_name) + '</span>';
                            html += '<span class="timestamp">' + new Date(comment.created_at).toLocaleString() + '</span>';
                            html += '</div>';
                            html += '</div>';
                            html += '<div class="message-content">' + escapeHtml(comment.body) + '</div>';
                            html += '</div>';
                        }});
                    }} else {{
                        // Добавляем ID для плейсхолдера, чтобы можно было удалить его позже
                        html += '<div id="no-comments-placeholder" style="text-align: center; padding: 20px; color: #888;">No comments yet</div>';
                    }}
            
                    drawerContent.innerHTML = html;
            
                    // Показываем дровер
                    document.getElementById('comments-drawer-overlay').style.display = 'block';
            
                }} catch (error) {{
                    console.error('Error loading comments:', error);
                    alert('Failed to load comments');
                }}
            }}

            // Отправка сообщения
            function setupMessageSending() {{
                const sendButton = document.getElementById('send-button');
                const messageInput = document.getElementById('message-input');

                async function sendMessage() {{
                    const text = messageInput.value.trim();
                    if (!text) return;

                    try {{
                        const url = chatData.isChannel ? 
                            '/api/channels/' + chatData.chatId + '/topics' : 
                            '/api/chats/' + chatData.chatId + '/topics';

                        const response = await fetch(url, {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify({{
                                text: text
                            }})
                        }});

                        if (response.ok) {{
                            messageInput.value = '';
                        }} else {{
                            alert('Failed to send message');
                        }}
                    }} catch (error) {{
                        console.error('Error sending message:', error);
                        alert('Error sending message');
                    }}
                }}

                if (sendButton) {{
                    sendButton.addEventListener('click', sendMessage);
                }}

                if (messageInput) {{
                    messageInput.addEventListener('keydown', (event) => {{
                        if (event.key === 'Enter' && !event.shiftKey) {{
                            event.preventDefault();
                            sendMessage();
                        }}
                    }});
                }}
            }}

            // Настройка модального окна
            function setupModal() {{
                const modalOverlay = document.getElementById('entity-modal-overlay');
                const closeModal = document.getElementById('close-modal');
                const cancelModal = document.getElementById('cancel-modal');
                const entityForm = document.getElementById('entity-form');

                if (!modalOverlay || !closeModal || !cancelModal || !entityForm) return;

                // Закрытие модального окна
                function closeModalFunc() {{
                    modalOverlay.style.display = 'none';
                    entityForm.reset();
                }}

                modalOverlay.addEventListener('click', (e) => {{
                    if (e.target === modalOverlay) {{
                        closeModalFunc();
                    }}
                }});

                closeModal.addEventListener('click', closeModalFunc);
                cancelModal.addEventListener('click', closeModalFunc);

                // Отправка формы Entity
                entityForm.addEventListener('submit', async (e) => {{
                    e.preventDefault();

                    const entityType = entityForm.dataset.type;
                    const formData = {{
                        type: entityType,
                        title: document.getElementById('entity-title').value,
                        body: document.getElementById('entity-body').value,
                    }};

                    // Добавляем дополнительные поля в зависимости от типа Entity
                    switch(entityType) {{
                        case 'question':
                            formData.priority = parseInt(document.getElementById('entity-priority').value) || 5;
                            formData.deadline = document.getElementById('entity-deadline').value || null;
                            break;

                        case 'defect':
                        case 'task':
                            formData.severity = parseInt(document.getElementById('entity-severity').value) || 5;
                            formData.reproducible = document.getElementById('entity-reproducible').checked;
                            formData.deadline = document.getElementById('entity-deadline').value || null;
                            formData.executor_id = document.getElementById('entity-executor').value || null;
                            formData.qa_id = document.getElementById('entity-qa').value || null;
                            break;

                        case 'info':
                            formData.deadline = document.getElementById('entity-deadline').value || null;
                            const checkboxes = document.querySelectorAll('#required-users-list input[type="checkbox"]:checked');
                            formData.required_user_ids = Array.from(checkboxes).map(cb => cb.value);
                            break;

                        case 'proposal':
                            formData.priority = parseInt(document.getElementById('entity-priority').value) || 5;
                            break;

                        case 'action_point':
                            formData.priority = parseInt(document.getElementById('entity-priority').value) || 5;
                            formData.deadline = document.getElementById('entity-deadline').value || null;
                            formData.executor_id = document.getElementById('entity-executor').value || null;
                            break;
                    }}

                    try {{
                        const url = chatData.isChannel ? 
                            '/api/channels/' + chatData.chatId + '/entities' : 
                            '/api/chats/' + chatData.chatId + '/entities';

                        const response = await fetch(url, {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify(formData)
                        }});

                        if (response.ok) {{
                            closeModalFunc();
                        }} else {{
                            const error = await response.json();
                            alert('Failed to create entity: ' + (error.detail || 'Unknown error'));
                        }}
                    }} catch (error) {{
                        console.error('Error creating entity:', error);
                        alert('Error creating entity');
                    }}
                }});
            }}

            // Настройка дровера
            function setupDrawer() {{
                const drawerOverlay = document.getElementById('comments-drawer-overlay');
                const closeDrawer = document.getElementById('close-drawer');
                const sendCommentBtn = document.getElementById('send-comment');
                const commentInput = document.getElementById('comment-input');

                if (!drawerOverlay || !closeDrawer || !sendCommentBtn || !commentInput) return;

                // Закрытие дровера
                drawerOverlay.addEventListener('click', (e) => {{
                    if (e.target === drawerOverlay) {{
                        drawerOverlay.style.display = 'none';
                    }}
                }});

                closeDrawer.addEventListener('click', () => {{
                    drawerOverlay.style.display = 'none';
                }});

                // Отправка комментария
                async function sendComment() {{
                    const text = commentInput.value.trim();
                    if (!text || !currentThreadId) return;

                    try {{
                        const response = await fetch('/api/comments', {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify({{
                                thread_id: currentThreadId,
                                body: text
                            }})
                        }});

                        if (response.ok) {{
                            commentInput.value = '';
                        }} else {{
                            alert('Failed to send comment');
                        }}
                    }} catch (error) {{
                        console.error('Error sending comment:', error);
                        alert('Error sending comment');
                    }}
                }}

                sendCommentBtn.addEventListener('click', sendComment);

                commentInput.addEventListener('keydown', (event) => {{
                    if (event.key === 'Enter' && !event.shiftKey) {{
                        event.preventDefault();
                        sendComment();
                    }}
                }});
            }}

            // Вспомогательная функция для экранирования HTML
            function escapeHtml(text) {{
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }}

            // Инициализация при загрузке страницы
            document.addEventListener('DOMContentLoaded', () => {{
                try {{
                    loadNavigation();
                    renderHistory();
                    renderEntityTypes();
                    setupMessageSending();
                    setupModal();
                    setupDrawer();
                    setupEditModal();
                    setupInfoReadsDrawer();
                    connectWebSocket();
                }} catch (error) {{
                    console.error('Error initializing page:', error);
                }}
            }});
            
            window.addEventListener('beforeunload', () => {{
                if (ws) {{
                    ws.close();
                }}
            }});
        </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)
