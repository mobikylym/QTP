from datetime import datetime
from uuid import UUID
from typing import Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.api.websocket.ws import manager
from src.app.storage.models import Entity, InfoEntity, InfoRequiredUser, EntityTypeEnum


async def send_entity_created(
    session: AsyncSession,
    room_type: str,
    room_id: str,
    entity_data: Dict[str, Any]
):
    """Отправляет уведомление о создании Entity"""
    entity_query = (
        select(Entity)
        .where(Entity.id == entity_data['entity_id'])
        .options(
            selectinload(Entity.info)
            .selectinload(InfoEntity.required_users)
            .selectinload(InfoRequiredUser.user),
        )
    )
    entity_result = await session.execute(entity_query)
    entity = entity_result.scalar_one_or_none()

    if entity and entity.type == EntityTypeEnum.info and entity.info:
        # Для Info добавляем required_user_ids в данные
        required_user_ids = [str(ru.user_id) for ru in entity.info.required_users]
        entity_data["required_user_ids"] = required_user_ids

    message = {
        "event": "entity_created",
        "data": entity_data
    }
    await manager.send_to_room(room_type, room_id, message)


async def send_entity_updated(
    session: AsyncSession,
    room_type: str,
    room_id: str,
    updated_fields: Dict[str, Any],
    entity_data: Dict[str, Any]
):
    """Отправляет уведомление об обновлении Entity"""
    message = {
        "event": "entity_updated",
        "data": {
            "entity_id": entity_data.get("entity_id"),
            "updated_fields": updated_fields,
            "updated_at": datetime.now().isoformat()
        }
    }
    await manager.send_to_room(room_type, room_id, message)


async def send_topic_created(
    session: AsyncSession,
    topic_id: UUID,
    room_type: str,
    room_id: str,
    topic_data: Dict[str, Any]
):
    """Отправляет уведомление о создании Topic"""
    message = {
        "event": "topic_created",
        "data": {
            "topic_id": str(topic_id),
            "text": topic_data.get("text"),
            "author_id": topic_data.get("author_id"),
            "created_at": datetime.now().isoformat(),
            "author_display_name": topic_data.get("author_display_name"),
        }
    }
    await manager.send_to_room(room_type, room_id, message)


async def send_comment_created(
    session: AsyncSession,
    comment_id: UUID,
    thread_id: UUID,
    thread_type: str,
    room_type: str,
    room_id: str,
    comment_data: Dict[str, Any]
):
    """Отправляет уведомление о создании комментария"""
    message = {
        "event": "comment_created",
        "data": {
            "comment_id": str(comment_id),
            "thread_id": str(thread_id),
            "thread_type": thread_type,
            "body": comment_data.get("body"),
            "author_id": comment_data.get("author_id"),
            "created_at": datetime.now().isoformat()
        }
    }
    await manager.send_to_room(room_type, room_id, message)


async def send_info_read(
    session: AsyncSession,
    entity_id: UUID,
    user_id: UUID,
    room_type: str,
    room_id: str,
    read_data: Dict[str, Any]
):
    """Отправляет уведомление о прочтении Info"""
    message = {
        "event": "info_read",
        "data": {
            "entity_id": str(entity_id),
            "user_id": str(user_id),
            "read_count": read_data.get("read_count"),
            "all_acknowledged": read_data.get("all_acknowledged"),
            "read_at": datetime.now().isoformat()
        }
    }
    await manager.send_to_room(room_type, room_id, message)
