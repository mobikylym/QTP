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

    chat_title = 'Заметки' if chat.is_self_chat else ''
    if not chat.is_self_chat:
        other_users = [u for u in chat.users if u.id != user.id]
        if other_users:
            chat_title = other_users[0].display_name

    history = await get_chat_history(chat_id, user.id, session)

    if chat.is_self_chat:
        available_entities = ['task', 'action_point']
    else:
        available_entities = ['question', 'defect', 'task', 'info', 'proposal', 'action_point']

    chat_users = chat.users

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

    history = await get_channel_history(channel_id, user.id, session)

    available_entities = channel.allowed_entity_types or [
        'question',
        'defect',
        'task',
        'info',
        'proposal',
        'action_point',
    ]

    chat_users = channel.users

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
    topics_query = (
        select(Topic)
        .where(Topic.direct_chat_id == chat_id)
        .options(selectinload(Topic.author), selectinload(Topic.comments))
        .order_by(Topic.created_at)
    )
    topics_result = await session.execute(topics_query)
    topics = topics_result.scalars().all()

    entities_query = (
        select(Entity)
        .where(Entity.direct_chat_id == chat_id)
        .options(
            selectinload(Entity.author),
            selectinload(Entity.comments),
            selectinload(Entity.question),
            selectinload(Entity.defect).options(joinedload(DefectEntity.executor), joinedload(DefectEntity.qa)),
            selectinload(Entity.task).options(joinedload(TaskEntity.executor), joinedload(TaskEntity.qa)),
            selectinload(Entity.info).selectinload(InfoEntity.required_users).selectinload(InfoRequiredUser.user),
            selectinload(Entity.proposal),
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

    history = []

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

    history.sort(key=lambda x: x['created_at'] if x['created_at'] else '')

    return history


async def get_channel_history(channel_id: str, user_id: str, session: AsyncSession) -> list[dict[str, Any]]:
    """Получаем историю канала (Topic и Entity)"""
    topics_query = (
        select(Topic)
        .where(Topic.channel_id == channel_id)
        .options(selectinload(Topic.author), selectinload(Topic.comments))
        .order_by(Topic.created_at)
    )
    topics_result = await session.execute(topics_query)
    topics = topics_result.scalars().all()

    entities_query = (
        select(Entity)
        .where(Entity.channel_id == channel_id)
        .options(
            selectinload(Entity.author),
            selectinload(Entity.comments),
            selectinload(Entity.question),
            selectinload(Entity.defect).options(joinedload(DefectEntity.executor), joinedload(DefectEntity.qa)),
            selectinload(Entity.task).options(joinedload(TaskEntity.executor), joinedload(TaskEntity.qa)),
            selectinload(Entity.info).selectinload(InfoEntity.required_users).selectinload(InfoRequiredUser.user),
            selectinload(Entity.proposal),
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

    history = []

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

    chat_title_escaped = json.dumps(chat_title)
    user_id_escaped = json.dumps(str(user.id))
    user_display_name_escaped = json.dumps(user.display_name)
    chat_id_escaped = json.dumps(chat_id)
    chat_users_escaped = chat_users_json

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{chat_title}</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
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
                flex-wrap: wrap;
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
                white-space: pre-wrap;
                word-wrap: break-word;
                line-height: 1.5;
            }}
            
            .entity-details {{
                margin-top: 10px;
                padding: 10px;
                background-color: #eef2ff;
                border-radius: 5px;
                font-size: 14px;
                white-space: pre-wrap;
                word-wrap: break-word;
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
                gap: 8px;
                align-items: center;
                flex-wrap: wrap;
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
                white-space: pre-wrap;
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
                width: calc(100% - 40px);
                max-height: 90vh;
                overflow-y: auto;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
                margin: 20px;
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
                max-width: 100%;
                padding: 8px 12px;
                border: 1px solid #ddd;
                border-radius: 4px;
                font-family: inherit;
                font-size: 14px; /* Include padding in width */
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
                white-space: pre-wrap;
                word-wrap: break-word;
                line-height: 1.5;
                word-break: break-word;
                overflow-wrap: break-word;
                max-width: 100%;
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
            #edit-entity-modal-overlay {{
                z-index: 1001;
            }}            
            .status-group {{
                display: none;
            }}
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
            <div class="sidebar" id="sidebar"></div>

            <div class="main-content">
                <div class="header">
                    <h2>{chat_title}</h2>
                    <div class="entity-types" id="entity-types"></div>
                </div>

                <div class="chat-area" id="chat-area"></div>

                <div class="message-form">
                    <textarea 
                        class="message-input" 
                        id="message-input" 
                        placeholder="Начните писать сообщение..." 
                        rows="3"
                    ></textarea>
                    <button class="send-button" id="send-button">Отправить</button>
                </div>
            </div>
        </div>

        <div class="modal-overlay" id="entity-modal-overlay">
            <div class="modal" id="entity-modal">
                <div class="modal-header">
                    <div class="modal-title" id="modal-title">Создать сущность</div>
                    <button class="close-modal" id="close-modal">&times;</button>
                </div>
                <form id="entity-form">
                    <div class="form-group">
                        <label class="form-label" for="entity-title">Тема</label>
                        <input type="text" class="form-input" id="entity-title" required>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="entity-body">Описание</label>
                        <textarea class="form-textarea" id="entity-body" rows="4" required></textarea>
                    </div>

                    <div class="form-group" id="priority-group" style="display: none;">
                        <label class="form-label" for="entity-priority">Приоритет</label>
                        <input type="number" class="form-input" id="entity-priority" min="1" max="10" value="5">
                    </div>

                    <div class="form-group" id="severity-group" style="display: none;">
                        <label class="form-label" for="entity-severity">Критичность</label>
                        <input type="number" class="form-input" id="entity-severity" min="1" max="10" value="5">
                    </div>

                    <div class="form-group" id="reproducible-group" style="display: none;">
                        <label class="form-label">
                            <input type="checkbox" class="form-checkbox" id="entity-reproducible">
                            Воспроизводится ли
                        </label>
                    </div>

                    <div class="form-group" id="deadline-group" style="display: none;">
                        <label class="form-label" for="entity-deadline">Закрыть до</label>
                        <input type="datetime-local" class="form-input" id="entity-deadline">
                    </div>

                    <div class="form-group" id="executor-group" style="display: none;">
                        <label class="form-label" for="entity-executor">Исполнитель</label>
                        <select class="form-select" id="entity-executor"></select>
                    </div>

                    <div class="form-group" id="qa-group" style="display: none;">
                        <label class="form-label" for="entity-qa">QA</label>
                        <select class="form-select" id="entity-qa"></select>
                    </div>

                    <div class="form-group" id="required-users-group" style="display: none;">
                        <label class="form-label">Должны ознакомиться</label>
                        <div id="required-users-list"></div>
                    </div>

                    <div class="modal-footer">
                        <button type="button" class="cancel-btn" id="cancel-modal">Отмена</button>
                        <button type="submit" class="create-btn">Создать</button>
                    </div>
                </form>
            </div>
        </div>

        <div class="drawer-overlay" id="comments-drawer-overlay">
            <div class="drawer" id="comments-drawer">
                <div class="drawer-header">
                    <div class="drawer-title">Комментарии</div>
                    <button class="close-drawer" id="close-drawer">&times;</button>
                </div>
                <div class="drawer-content" id="drawer-content"></div>
                <div class="drawer-form">
                    <textarea 
                        class="message-input" 
                        id="comment-input" 
                        placeholder="Начните вводить текст комментария..." 
                        rows="2"
                    ></textarea>
                    <button class="send-button" id="send-comment">Отправить</button>
                </div>
            </div>
        </div>
        
        <div class="modal-overlay" id="edit-entity-modal-overlay">
            <div class="modal" id="edit-entity-modal">
                <div class="modal-header">
                    <div class="modal-title" id="edit-modal-title">Редактирование сущности</div>
                    <button class="close-modal" id="edit-close-modal">&times;</button>
                </div>
                <form id="edit-entity-form">
                    <input type="hidden" id="edit-entity-id">
                    
                    <div class="form-group">
                        <label class="form-label" for="edit-entity-title">Тема</label>
                        <input type="text" class="form-input" id="edit-entity-title" required>
                    </div>
        
                    <div class="form-group">
                        <label class="form-label" for="edit-entity-body">Описание</label>
                        <textarea class="form-textarea" id="edit-entity-body" rows="4" required></textarea>
                    </div>
        
                    <div class="form-group" id="edit-priority-group" style="display: none;">
                        <label class="form-label" for="edit-entity-priority">Приоритет</label>
                        <input type="number" class="form-input" id="edit-entity-priority" min="1" max="10" value="5">
                    </div>
        
                    <div class="form-group" id="edit-severity-group" style="display: none;">
                        <label class="form-label" for="edit-entity-severity">Критичность</label>
                        <input type="number" class="form-input" id="edit-entity-severity" min="1" max="10" value="5">
                    </div>
        
                    <div class="form-group" id="edit-reproducible-group" style="display: none;">
                        <label class="form-label">
                            <input type="checkbox" class="form-checkbox" id="edit-entity-reproducible">
                            Воспроизводится ли
                        </label>
                    </div>
        
                    <div class="form-group" id="edit-deadline-group" style="display: none;">
                        <label class="form-label" for="edit-entity-deadline">Закрыть до</label>
                        <input type="datetime-local" class="form-input" id="edit-entity-deadline">
                    </div>
        
                    <div class="form-group" id="edit-executor-group" style="display: none;">
                        <label class="form-label" for="edit-entity-executor">Исполнитель</label>
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
                        <label class="form-label">Должны ознакомиться</label>
                        <div id="edit-required-users-list"></div>
                    </div>
        
                    <div class="form-group status-group" id="edit-status-group">
                        <label class="form-label" for="edit-entity-status">Статус</label>
                        <select class="form-select" id="edit-entity-status">
                            <option value="created">Created</option>
                            <option value="in_progress">In Progress</option>
                            <option value="completed">Completed</option>
                            <option value="rejected">Rejected</option>
                            <option value="closed">Closed</option>
                        </select>
                    </div>
        
                    <div class="modal-footer">
                        <button type="button" class="cancel-btn" id="edit-cancel-modal">Отменить</button>
                        <button type="submit" class="create-btn">Сохранить изменения</button>
                    </div>
                </form>
            </div>
        </div>
    
        <div class="drawer-overlay" id="info-reads-drawer-overlay">
            <div class="drawer" id="info-reads-drawer">
                <div class="drawer-header">
                    <div class="drawer-title">Информация о статусах прочтения</div>
                    <button class="close-drawer" id="close-info-reads-drawer">&times;</button>
                </div>
                <div class="drawer-content" id="info-reads-content"></div>
            </div>
        </div>

        <script>
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

            async function handleEntityCreated(data) {{
                try {{
                    const response = await fetch(`/api/entities/${{data.entity_id}}`);
                    if (!response.ok) return;
            
                    const entity = await response.json();
            
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
            
                    if (entity.type === 'info') {{
                        const requiredUserIds = data.required_user_ids || entity.required_user_ids || [];                       
                        const requiredUsers = [];
                        
                        requiredUserIds.forEach(userId => {{
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
                        
                        entityData.required_users = requiredUsers;
                        
                        const isUserInRequiredList = requiredUserIds.includes(chatData.userId);
                        const isAuthor = data.author_id === chatData.userId;
                        
                        if (isAuthor) {{
                            entityData.has_read = true;
                            entityData.read_count = 0;
                        }} else if (isUserInRequiredList) {{
                            entityData.has_read = false;
                            entityData.read_count = 0;
                        }} else {{
                            entityData.has_read = true;
                            entityData.read_count = requiredUserIds.length;
                        }}
                    }}
            
                    chatData.history.push(entityData);
                    chatData.history.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
                    renderHistory();
            
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
                const entityIndex = chatData.history.findIndex(
                    item => item.type === 'entity' && item.id === data.entity_id
                );
            
                if (entityIndex !== -1) {{
                    const entity = chatData.history[entityIndex];
                    
                    Object.keys(data.updated_fields).forEach(field => {{
                        if (field !== 'required_user_ids' && field !== 'read_count') {{
                            entity[field] = data.updated_fields[field];
                        }}
                    }});
                    
                    if (entity.entity_type === 'info') {{
                        if (data.updated_fields.required_user_ids) {{
                            const requiredUserIds = data.updated_fields.required_user_ids;
                            
                            const requiredUsers = [];
                            requiredUserIds.forEach(userId => {{
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
                        
                        if (data.updated_fields.read_count !== undefined) {{
                            entity.read_count = data.updated_fields.read_count;
                        }}
                        
                        const isAuthor = entity.author_id === chatData.userId;
                        const isUserInRequiredList = entity.required_users && 
                            entity.required_users.some(u => u.id === chatData.userId);
                        
                        if (isAuthor) {{
                            entity.has_read = true;
                        }} else if (isUserInRequiredList) {{
                            checkIfCurrentUserHasRead(entity.id);
                        }} else {{
                            entity.has_read = true;
                        }}
                    }}
                    
                    entity.updated_at = data.updated_at;           
                    renderHistory();
                }}
            }}
            
            async function checkIfCurrentUserHasRead(entityId) {{
                try {{
                    const response = await fetch(`/api/entities/${{entityId}}`);
                    if (!response.ok) return;
                    
                    const entity = await response.json();
                    if (entity.type !== 'info') return;
                    
                    const entityIndex = chatData.history.findIndex(e => e.id === entityId);
                    if (entityIndex === -1) return;
                    
                    const historyEntity = chatData.history[entityIndex];
                    
                    const readStatusResponse = await fetch(`/api/info/${{entityId}}/reads`);
                    if (readStatusResponse.ok) {{
                        const readStatus = await readStatusResponse.json();
                        
                        const hasRead = readStatus.read.some(item => item.user.id === chatData.userId);
                        historyEntity.has_read = hasRead;
                        
                        historyEntity.read_count = readStatus.read_count;
                        
                        renderHistory();
                    }}
                }} catch (error) {{
                    console.error('Error checking read status:', error);
                }}
            }}

            async function handleTopicCreated(data) {{
                try {{
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
                const itemIndex = chatData.history.findIndex(
                    item => item.id === data.thread_id
                );

                if (itemIndex !== -1) {{
                    const item = chatData.history[itemIndex];
                    item.comment_count = (item.comment_count || 0) + 1;

                    const commentBtn = document.querySelector(`button[onclick*="${{data.thread_id}}"] .count`);
                    if (commentBtn) {{
                        commentBtn.textContent = item.comment_count;
                    }}

                    if (currentThreadId === data.thread_id) {{
                        addCommentToDrawer(data);
                    }}
                }}
            }}

            async function addCommentToDrawer(data) {{
                try {{
                    const response = await fetch(`/api/comments/single/${{data.comment_id}}`);
                    if (!response.ok) return;
                    
                    const comment = await response.json();
                    
                    if (comment.thread_id !== currentThreadId) {{
                        return;
                    }}
                    
                    const placeholder = document.getElementById('no-comments-placeholder');
                    if (placeholder) {{
                        placeholder.remove();
                    }}
                    
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
                        
                        drawerContent.scrollTop = drawerContent.scrollHeight;
                    }}
                }} catch (error) {{
                    console.error('Error loading comment:', error);
                }}
            }}

            function handleInfoRead(data) {{
                const entityIndex = chatData.history.findIndex(
                    item => item.type === 'entity' && 
                            item.id === data.entity_id && 
                            item.entity_type === 'info'
                );
            
                if (entityIndex !== -1) {{
                    const entity = chatData.history[entityIndex];                   
                    entity.read_count = data.read_count || 0;
                    
                    if (data.user_id === chatData.userId) {{
                        entity.has_read = true;
                    }}
                    
                    const readCountElement = document.querySelector(`[data-entity-id="${{data.entity_id}}"] .read-count`);
                    if (readCountElement && entity.required_users) {{
                        const totalCount = entity.required_users.length;
                        readCountElement.textContent = `${{entity.read_count}}/${{totalCount}}`;
                    }}
            
                    const readInfoBtn = document.querySelector(`button[onclick*="openInfoReadsDrawer('${{data.entity_id}}')"]`);
                    if (readInfoBtn && entity.required_users) {{
                        const totalCount = entity.required_users.length;
                        readInfoBtn.innerHTML = `👁️ ${{entity.read_count}}/${{totalCount}}`;
                    }}
                    
                    const messageElement = document.querySelector(`.message[data-entity-id="${{data.entity_id}}"]`);
                    if (messageElement) {{
                        if (entity.has_read || entity.author_id === chatData.userId) {{
                            messageElement.classList.remove('unread-info');
                        }} else {{
                            messageElement.classList.add('unread-info');
                        }}
                    }}
                    
                    renderHistory();
                }}
                if (currentInfoReadsEntityId === data.entity_id) {{
                    openInfoReadsDrawer(data.entity_id);
                }}
            }}

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

                    if (userInfo.is_admin) {{
                        html += '<div id="admin-btn" onclick="openAdminPanel()" class="nav-item">Панель администратора</div>';
                    }}

                    html += '<div id="profile-btn" onclick="openProfile()" class="nav-item">Профиль</div>';
                    html += '<div id="notes-btn" class="nav-item ' + 
                            (chatData.isChannel ? '' : (chatData.chatTitle === 'Заметки' ? 'active' : '')) + 
                            '">Заметки</div>';
                    html += '<div id="task-explorer" onclick="openTaskExplorer()" class="nav-item">Обозреватель задач</div>';

                    html += '<div id="chats-toggle" class="nav-item">Чаты ▼</div>';
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

                    html += '<div id="channels-toggle" class="nav-item">Каналы ▼</div>';
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

                    html += '<div id="logout-btn" class="nav-item" onclick="logout()">Выйти</div>';

                    sidebar.innerHTML = html;

                    setupNavigationEvents();

                }} catch (error) {{
                    console.error('Error loading navigation:', error);
                }}
            }}

            function setupNavigationEvents() {{
                const chatsToggle = document.getElementById('chats-toggle');
                const chatsSubmenu = document.getElementById('chats-submenu');
                if (chatsToggle && chatsSubmenu) {{
                    chatsToggle.addEventListener('click', () => {{
                        chatsSubmenu.style.display = chatsSubmenu.style.display === 'none' ? 'flex' : 'none';
                    }});
                    chatsSubmenu.style.display = 'flex';
                }}

                const channelsToggle = document.getElementById('channels-toggle');
                const channelsSubmenu = document.getElementById('channels-submenu');
                if (channelsToggle && channelsSubmenu) {{
                    channelsToggle.addEventListener('click', () => {{
                        channelsSubmenu.style.display = channelsSubmenu.style.display === 'none' ? 'flex' : 'none';
                    }});
                    channelsSubmenu.style.display = 'flex';
                }}

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

            function logout() {{
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '/logout';
                                
                document.body.appendChild(form);
                form.submit();
            }}

            function renderEntityTypes() {{
                const entityTypesContainer = document.getElementById('entity-types');
                if (!entityTypesContainer) return;

                let html = '';

                chatData.availableEntities.forEach(entityType => {{
                    const displayName = entityType.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                    html += '<button class="entity-type-btn" data-type="' + entityType + '">' + displayName + '</button>';
                }});

                entityTypesContainer.innerHTML = html;

                document.querySelectorAll('.entity-type-btn').forEach(btn => {{
                    btn.addEventListener('click', (e) => {{
                        const entityType = e.target.dataset.type;
                        openEntityModal(entityType);
                    }});
                }});
            }}

            function renderHistory() {{
                const chatArea = document.getElementById('chat-area');
                if (!chatArea) return;
                
                let html = '';
                
                if (chatData.history.length === 0) {{
                    html = '<div style="text-align: center; padding: 40px; color: #888;">Сообщений пока нет</div>';
                }} else {{
                    chatData.history.forEach(msg => {{
                        const time = new Date(msg.created_at).toLocaleString();
                        
                        const isUserInRequiredList = msg.required_users && 
                            msg.required_users.some(user => user.id === chatData.userId);
                        
                        const isUnreadInfo = msg.type === 'entity' && 
                                             msg.entity_type === 'info' && 
                                             msg.author_id !== chatData.userId &&
                                             isUserInRequiredList &&
                                             !msg.has_read;
                        
                        const messageClass = 'message ' + msg.type + (isUnreadInfo ? ' unread-info' : '');
                
                        html += '<div class="' + messageClass + '">';
                        
                        html += '<div class="message-header">';
                        html += '<div class="author-info">';
                        html += '<span class="author">' + escapeHtml(msg.author_display_name) + '</span>';
                        html += '<span class="timestamp">' + time + '</span>';
                        html += '</div>';
                        
                        html += '<div class="message-actions">';
                        
                        if (msg.type === 'entity' && (msg.entity_type !== 'info' || msg.author_id === chatData.userId)) {{
                            html += '<button class="edit-btn" onclick="openEditEntityModal(\\'' + msg.id + '\\')">✏️ Редактировать</button>';
                        }}
                
                        if (msg.type === 'entity' && msg.entity_type === 'info') {{
                            const isUserInRequiredList = msg.required_users && 
                                msg.required_users.some(u => u.id === chatData.userId);
                            
                            if (msg.author_id === chatData.userId) {{
                                const readCount = msg.read_count || 0;
                                const totalCount = msg.required_users ? msg.required_users.length : 0;
                                html += '<button class="read-info-btn" onclick="openInfoReadsDrawer(\\'' + msg.id + '\\')">👁️ ' + readCount + '/' + totalCount + '</button>';
                            }} else if (isUserInRequiredList && !msg.has_read) {{
                                html += '<button class="mark-read-btn" onclick="markAsRead(\\'' + msg.id + '\\')">👁️ Отметить прочитанным</button>';
                            }}
                        }}
                
                        html += '<button class="comment-btn" onclick="openCommentsDrawer(\\'' + msg.id + '\\', \\'' + msg.type + '\\')">';
                        html += '<span class="count">' + (msg.comment_count || 0) + '</span> 💬';
                        html += '</button>';
                        
                        html += '</div>';
                        html += '</div>';
                
                        if (msg.type === 'topic') {{
                            html += '<div class="message-content">' + escapeHtml(msg.text) + '</div>';
                        }} else if (msg.type === 'entity') {{
                            html += '<div class="message-content">';
                            html += '<strong>' + escapeHtml(msg.title) + '</strong>';
                            html += '</div>';
                
                            html += '<div class="entity-details">';
                            html += '<div class="entity-field"><span class="entity-field-label">Тип:</span> ' + msg.entity_type + '</div>';
                
                            if (msg.body) {{
                                html += '<div class="entity-field"><span class="entity-field-label">Описание:</span><br>' + 
                                        escapeHtml(msg.body) + '</div>';
                            }}
                
                            switch(msg.entity_type) {{
                                case 'question':
                                    html += '<div class="entity-field"><span class="entity-field-label">Приоритет:</span> ' + 
                                           (msg.priority !== undefined ? msg.priority : 'Не указано') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Статус:</span> ' + 
                                           (msg.status ? msg.status.replaceAll('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase()) : 'Created') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Закрыть до:</span> ' + 
                                           (msg.deadline ? new Date(msg.deadline).toLocaleString() : 'Не указано') + '</div>';
                                    break;
                
                                case 'defect':
                                case 'task':
                                    html += '<div class="entity-field"><span class="entity-field-label">Приоритет:</span> ' + 
                                           (msg.severity !== undefined ? msg.severity : 'Не указано') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Воспроизводится ли:</span> ' + 
                                           (msg.reproducible !== undefined ? (msg.reproducible ? 'Да' : 'Нет') : 'Не указано') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Статус:</span> ' + 
                                           (msg.status ? msg.status.replaceAll('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase()) : 'Created') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Закрыть до:</span> ' + 
                                           (msg.deadline ? new Date(msg.deadline).toLocaleString() : 'Не указано') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Исполнитель:</span> ' + 
                                           (msg.executor || 'Не назначен') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">QA:</span> ' + 
                                           (msg.qa || 'Не назначен') + '</div>';
                                    break;
                
                                case 'info':
                                    html += '<div class="entity-field"><span class="entity-field-label">Закрыть до:</span> ' + 
                                           (msg.deadline ? new Date(msg.deadline).toLocaleString() : 'Не указано') + '</div>';
                
                                    break;
                
                                case 'proposal':
                                    html += '<div class="entity-field"><span class="entity-field-label">Приоритет:</span> ' + 
                                           (msg.priority !== undefined ? msg.priority : 'Не указано') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Статус:</span> ' + 
                                           (msg.status ? msg.status.replaceAll('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase()) : 'Created') + '</div>';
                                    break;
                
                                case 'action_point':
                                    html += '<div class="entity-field"><span class="entity-field-label">Приоритет:</span> ' + 
                                           (msg.priority !== undefined ? msg.priority : 'Не указано') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Исполнитель:</span> ' + 
                                       (msg.executor || 'Не назначен') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Статус:</span> ' + 
                                           (msg.status ? msg.status.replaceAll('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase()) : 'Created') + '</div>';
                                    html += '<div class="entity-field"><span class="entity-field-label">Закрыть до:</span> ' + 
                                           (msg.deadline ? new Date(msg.deadline).toLocaleString() : 'Не указано') + '</div>';
                                    break;
                            }}
                
                            html += '</div>';
                        }}
                
                        html += '</div>';
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

            async function openEntityModal(entityType) {{
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

                const displayName = entityType.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                document.getElementById('modal-title').textContent = 'Создать ' + displayName;

                document.getElementById('priority-group').style.display = 'none';
                document.getElementById('severity-group').style.display = 'none';
                document.getElementById('reproducible-group').style.display = 'none';
                document.getElementById('deadline-group').style.display = 'none';
                document.getElementById('executor-group').style.display = 'none';
                document.getElementById('qa-group').style.display = 'none';
                document.getElementById('required-users-group').style.display = 'none';

                const chatUsers = getChatUsers();
                const infoUsers = getInfoUsers();

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

                        fillUserDropdown('entity-executor', chatUsers, true);
                        fillUserDropdown('entity-qa', chatUsers, true);
                        break;

                    case 'info':
                        document.getElementById('deadline-group').style.display = 'block';
                        document.getElementById('required-users-group').style.display = 'block';
                        
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
                        
                        fillUserDropdown('entity-executor', chatUsers, true);
                        break;
                }}

                document.getElementById('entity-form').dataset.type = entityType;

                document.getElementById('entity-modal-overlay').style.display = 'flex';
            }}

            async function openEditEntityModal(entityId) {{
                editingEntityId = entityId;
                
                const entity = chatData.history.find(e => e.id === entityId);
                if (!entity) {{
                    alert('Сущность не найдена');
                    return;
                }}
    
                try {{
                    const response = await fetch('/api/entities/' + entityId);
                    if (!response.ok) throw new Error('Failed to load entity data');
                    const fullEntity = await response.json();
                    
                    const displayName = entity.entity_type.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                    document.getElementById('edit-modal-title').textContent = 'Редактировать ' + displayName;
                    document.getElementById('edit-entity-id').value = entityId;
                    document.getElementById('edit-entity-title').value = entity.title || '';
                    document.getElementById('edit-entity-body').value = fullEntity.body || '';
    
                    document.getElementById('edit-priority-group').style.display = 'none';
                    document.getElementById('edit-severity-group').style.display = 'none';
                    document.getElementById('edit-reproducible-group').style.display = 'none';
                    document.getElementById('edit-deadline-group').style.display = 'none';
                    document.getElementById('edit-executor-group').style.display = 'none';
                    document.getElementById('edit-qa-group').style.display = 'none';
                    document.getElementById('edit-required-users-group').style.display = 'none';
                    document.getElementById('edit-status-group').style.display = 'none';
    
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
    
                    const chatUsers = getChatUsers();
                    const infoUsers = getInfoUsers();
    
                    switch(entity.entity_type) {{
                        case 'question':
                            document.getElementById('edit-priority-group').style.display = 'block';
                            document.getElementById('edit-deadline-group').style.display = 'block';
                            document.getElementById('edit-status-group').style.display = 'block';
                            
                            document.getElementById('edit-entity-priority').value = fullEntity.priority || 5;
                            if (fullEntity.deadline) {{
                                document.getElementById('edit-entity-deadline').value = formatDateForInput(fullEntity.deadline);
                            }}
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
                            
                            fillUserDropdown('edit-entity-executor', chatUsers, true);
                            fillUserDropdown('edit-entity-qa', chatUsers, true);
                            
                            if (fullEntity.executor_id) {{
                                document.getElementById('edit-entity-executor').value = fullEntity.executor_id;
                            }}
                            if (fullEntity.qa_id) {{
                                document.getElementById('edit-entity-qa').value = fullEntity.qa_id;
                            }}
                            
                            break;
    
                        case 'info':
                            document.getElementById('edit-deadline-group').style.display = 'block';
                            document.getElementById('edit-required-users-group').style.display = 'block';
                            
                            if (fullEntity.deadline) {{
                                document.getElementById('edit-entity-deadline').value = formatDateForInput(fullEntity.deadline);
                            }}
                            
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
                            
                            fillUserDropdown('edit-entity-executor', chatUsers, true);
                            
                            if (fullEntity.executor_id) {{
                                document.getElementById('edit-entity-executor').value = fullEntity.executor_id;
                            }}
                            
                            break;
                    }}
    
                    document.getElementById('edit-entity-modal-overlay').style.display = 'flex';
    
                }} catch (error) {{
                    console.error('Error loading entity data:', error);
                    alert('Failed to load entity data');
                }}
            }}
            
            function getChatUsers() {{
                // Если уже загружены все пользователи, фильтруем их по chatUsers
                if (allUsers.length > 0) {{
                    return allUsers.filter(user => 
                        chatData.chatUsers.some(chatUser => chatUser.id === user.id)
                    );
                }}
                return [];
            }}

            function getInfoUsers() {{
                const chatUsers = getChatUsers();
                return chatUsers.filter(user => user.id !== chatData.userId);
            }}

            function fillUserDropdown(selectId, users, includeCurrentUser = true) {{
                const select = document.getElementById(selectId);
                if (!select) return;
                
                const currentValue = select.value;
                const currentValueIsInList = users.some(user => user.id === currentValue);
                
                let currentUser = null;
                if (currentValue && !currentValueIsInList) {{
                    currentUser = allUsers.find(u => u.id === currentValue);
                }}
                
                select.innerHTML = '<option value="">Выберите пользователя...</option>';
            
                users.forEach(user => {{
                    // Если includeCurrentUser = false, исключаем текущего пользователя
                    if (!includeCurrentUser && user.id === chatData.userId) {{
                        return;
                    }}
                    
                    const option = document.createElement('option');
                    option.value = user.id;
                    option.textContent = user.display_name;
                    
                    if (user.id === chatData.userId) {{
                        option.textContent += ' (я)';
                    }}
                    
                    select.appendChild(option);
                }});
                
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
            
            async function openEditEntityModal(entityId) {{
                editingEntityId = entityId;
                
                const entity = chatData.history.find(e => e.id === entityId);
                if (!entity) {{
                    alert('Entity not found');
                    return;
                }}
    
                try {{
                    const response = await fetch('/api/entities/' + entityId);
                    if (!response.ok) throw new Error('Failed to load entity data');
                    const fullEntity = await response.json();
                    
                    const displayName = entity.entity_type.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                    document.getElementById('edit-modal-title').textContent = 'Редактировать ' + displayName;
                    document.getElementById('edit-entity-id').value = entityId;
                    document.getElementById('edit-entity-title').value = entity.title || '';
                    document.getElementById('edit-entity-body').value = fullEntity.body || '';
    
                    document.getElementById('edit-priority-group').style.display = 'none';
                    document.getElementById('edit-severity-group').style.display = 'none';
                    document.getElementById('edit-reproducible-group').style.display = 'none';
                    document.getElementById('edit-deadline-group').style.display = 'none';
                    document.getElementById('edit-executor-group').style.display = 'none';
                    document.getElementById('edit-qa-group').style.display = 'none';
                    document.getElementById('edit-required-users-group').style.display = 'none';
                    document.getElementById('edit-status-group').style.display = 'none';
    
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
                            
                            fillUserDropdown('edit-entity-executor', allUsers, true);
                            fillUserDropdown('edit-entity-qa', allUsers, true);
                            
                            if (fullEntity.executor_id) {{
                                document.getElementById('edit-entity-executor').value = fullEntity.executor_id;
                            }}
                            if (fullEntity.qa_id) {{
                                document.getElementById('edit-entity-qa').value = fullEntity.qa_id;
                            }}
                            
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
                            
                            fillUserDropdown('edit-entity-executor', allUsers, true);
                            fillUserDropdown('edit-entity-qa', allUsers, true);
                            
                            if (fullEntity.executor_id) {{
                                document.getElementById('edit-entity-executor').value = fullEntity.executor_id;
                            }}
                            if (fullEntity.qa_id) {{
                                document.getElementById('edit-entity-qa').value = fullEntity.qa_id;
                            }}
                            
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
                            
                            const usersList = document.getElementById('edit-required-users-list');
                            usersList.innerHTML = '';
                            
                            const requiredUserIds = fullEntity.required_user_ids || [];
                            
                            allUsers.forEach(user => {{
                                if (user.id !== chatData.userId) {{
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
    
                    document.getElementById('edit-entity-modal-overlay').style.display = 'flex';
    
                }} catch (error) {{
                    console.error('Error loading entity data:', error);
                    alert('Failed to load entity data');
                }}
            }}
    
            async function markAsRead(infoId) {{
                try {{
                    const response = await fetch('/api/info/' + infoId + '/read', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json'
                        }}
                    }});
            
                    if (response.ok) {{
                        const entityIndex = chatData.history.findIndex(
                            e => e.id === infoId && e.entity_type === 'info'
                        );
                        
                        if (entityIndex !== -1) {{
                            const entity = chatData.history[entityIndex];
                            entity.has_read = true;
                            entity.read_count = (entity.read_count || 0) + 1;
                            
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
                    
                    if (readData.not_read && readData.not_read.length > 0) {{
                        html += '<p><strong>Ещё не ознакомились:</strong></p>';
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
                        html += '<span>Все пользователи ознакомились с данным сообщением</span>';
                        html += '</div>';
                        html += '</div>';
                    }}
                    
                    if (readData.read && readData.read.length > 0) {{
                        html += '<p><strong>Уже ознакомились:</strong></p>';
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
    
                entityForm.addEventListener('submit', async (e) => {{
                    e.preventDefault();
    
                    if (!editingEntityId) return;
    
                    const entity = chatData.history.find(e => e.id === editingEntityId);
                    if (!entity) return;
    
                    const formData = {{
                        title: document.getElementById('edit-entity-title').value,
                        body: document.getElementById('edit-entity-body').value,
                    }};
    
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

            async function openCommentsDrawer(threadId, threadType) {{
                currentThreadId = threadId;
                currentThreadType = threadType;
            
                try {{
                    const response = await fetch('/api/comments/' + threadId);
                    if (!response.ok) throw new Error('Failed to load comments');
            
                    const data = await response.json();
            
                    const drawerContent = document.getElementById('drawer-content');
                    let html = '';
            
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
            
                        html += '<hr style="margin: 20px 0; border-color: #eee;">';
                    }}
            
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
                        html += '<div id="no-comments-placeholder" style="text-align: center; padding: 20px; color: #888;">Комментариев пока нет</div>';
                    }}
            
                    drawerContent.innerHTML = html;
            
                    document.getElementById('comments-drawer-overlay').style.display = 'block';
            
                }} catch (error) {{
                    console.error('Error loading comments:', error);
                    alert('Failed to load comments');
                }}
            }}

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

            function setupModal() {{
                const modalOverlay = document.getElementById('entity-modal-overlay');
                const closeModal = document.getElementById('close-modal');
                const cancelModal = document.getElementById('cancel-modal');
                const entityForm = document.getElementById('entity-form');

                if (!modalOverlay || !closeModal || !cancelModal || !entityForm) return;

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

                entityForm.addEventListener('submit', async (e) => {{
                    e.preventDefault();

                    const entityType = entityForm.dataset.type;
                    const formData = {{
                        type: entityType,
                        title: document.getElementById('entity-title').value,
                        body: document.getElementById('entity-body').value,
                    }};

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

            function setupDrawer() {{
                const drawerOverlay = document.getElementById('comments-drawer-overlay');
                const closeDrawer = document.getElementById('close-drawer');
                const sendCommentBtn = document.getElementById('send-comment');
                const commentInput = document.getElementById('comment-input');

                if (!drawerOverlay || !closeDrawer || !sendCommentBtn || !commentInput) return;

                drawerOverlay.addEventListener('click', (e) => {{
                    if (e.target === drawerOverlay) {{
                        drawerOverlay.style.display = 'none';
                    }}
                }});

                closeDrawer.addEventListener('click', () => {{
                    drawerOverlay.style.display = 'none';
                }});

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

            function escapeHtml(text) {{
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }}

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
