from datetime import UTC, datetime
from typing import Any, List, Dict

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.app.api.deps.auth import require_admin, require_auth
from src.app.api.deps.database import get_session
from src.app.api.http.router import router
from src.app.storage.models import (
    Acknowledge,
    ActionPointEntity,
    ActionPointStatus,
    Channel,
    DefectEntity,
    DefectStatus,
    DirectChat,
    Entity,
    EntityTypeEnum,
    InfoEntity,
    InfoRequiredUser,
    ProposalEntity,
    ProposalStatus,
    QuestionEntity,
    QuestionStatus,
    TaskEntity,
    TaskStatus,
    User,
)


class EntityBase(BaseModel):
    type: EntityTypeEnum
    title: str = Field(..., max_length=400)
    body: str


class QuestionEntityCreate(EntityBase):
    type: EntityTypeEnum = EntityTypeEnum.question
    priority: int | None = Field(5, ge=1, le=10)
    deadline: datetime | None = None


class DefectEntityCreate(EntityBase):
    type: EntityTypeEnum = EntityTypeEnum.defect
    severity: int | None = Field(5, ge=1, le=10)
    reproducible: bool | None = False
    deadline: datetime | None = None
    executor_id: str | None = None
    qa_id: str | None = None


class TaskEntityCreate(EntityBase):
    type: EntityTypeEnum = EntityTypeEnum.task
    severity: int | None = Field(5, ge=1, le=10)
    reproducible: bool | None = False
    deadline: datetime | None = None
    executor_id: str | None = None
    qa_id: str | None = None


class InfoEntityCreate(EntityBase):
    type: EntityTypeEnum = EntityTypeEnum.info
    deadline: datetime | None = None
    required_user_ids: list[str] | None = []


class ProposalEntityCreate(EntityBase):
    type: EntityTypeEnum = EntityTypeEnum.proposal
    priority: int | None = Field(5, ge=1, le=10)


class ActionPointEntityCreate(EntityBase):
    type: EntityTypeEnum = EntityTypeEnum.action_point
    executor_id: str | None = None
    priority: int | None = Field(5, ge=1, le=10)
    deadline: datetime | None = None


# Union всех типов Entity для приема данных
EntityCreate = (
    QuestionEntityCreate
    | DefectEntityCreate
    | TaskEntityCreate
    | InfoEntityCreate
    | ProposalEntityCreate
    | ActionPointEntityCreate
)


@router.post('/api/chats/{chat_id}/entities')
async def create_chat_entity(
    chat_id: str,
    entity_data: EntityCreate,
    user_login=Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
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

    # Создаем Entity
    entity = await create_entity(
        session=session, entity_data=entity_data, author_id=user.id, direct_chat_id=chat_id, channel_id=None
    )

    required_user_ids = []
    if entity.type == EntityTypeEnum.info:
        # Загружаем только что созданный Info с required_users
        info_query = (
            select(InfoEntity)
            .where(InfoEntity.entity_id == entity.id)
            .options(selectinload(InfoEntity.required_users))
        )
        info_result = await session.execute(info_query)
        info = info_result.scalar_one()
        required_user_ids = [str(ru.user_id) for ru in info.required_users]

    from src.app.api.websocket.notifications import send_entity_created
    await send_entity_created(
        session=session,
        room_type="chat",
        room_id=chat_id,
        entity_data={
            "entity_id": entity.id,
            "type": entity.type.value,
            "title": entity.title,
            "author_id": str(user.id),
            "author_display_name": str(user.display_name),
            "required_user_ids": required_user_ids,  # ← ДОБАВИЛИ
            "created_at": entity.created_at.isoformat(),
        }
    )

    return {'id': entity.id, 'type': entity.type.value, 'title': entity.title, 'status': 'created'}


@router.post('/api/channels/{channel_id}/entities')
async def create_channel_entity(
    channel_id: str,
    entity_data: EntityCreate,
    user_login=Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
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

    # Проверяем, разрешен ли данный тип Entity в канале
    if channel.allowed_entity_types and entity_data.type.value not in channel.allowed_entity_types:
        raise HTTPException(
            status_code=400, detail=f"Entity type '{entity_data.type.value}' is not allowed in this channel"
        )

    # Создаем Entity
    entity = await create_entity(
        session=session, entity_data=entity_data, author_id=user.id, direct_chat_id=None, channel_id=channel_id
    )

    required_user_ids = []
    if entity.type == EntityTypeEnum.info:
        # Загружаем только что созданный Info с required_users
        info_query = (
            select(InfoEntity)
            .where(InfoEntity.entity_id == entity.id)
            .options(selectinload(InfoEntity.required_users))
        )
        info_result = await session.execute(info_query)
        info = info_result.scalar_one()
        required_user_ids = [str(ru.user_id) for ru in info.required_users]

    from src.app.api.websocket.notifications import send_entity_created
    await send_entity_created(
        session=session,
        room_type="channel",
        room_id=channel_id,
        entity_data={
            "entity_id": entity.id,
            "type": entity.type.value,
            "title": entity.title,
            "author_id": str(user.id),
            "author_display_name": str(user.display_name),
            "required_user_ids": required_user_ids,  # ← ДОБАВИЛИ
            "created_at": entity.created_at.isoformat(),
        }
    )

    return {'id': entity.id, 'type': entity.type.value, 'title': entity.title, 'status': 'created'}


async def create_entity(
    session: AsyncSession,
    entity_data: EntityCreate,
    author_id: str,
    direct_chat_id: str | None = None,
    channel_id: str | None = None,
) -> Entity:
    """Создает Entity с соответствующим типом"""
    # Создаем основную сущность Entity
    entity = Entity(
        type=entity_data.type,
        title=entity_data.title,
        author_id=author_id,
        direct_chat_id=direct_chat_id,
        channel_id=channel_id,
    )

    session.add(entity)
    await session.flush()  # Получаем entity_id

    # Создаем конкретную сущность в зависимости от типа
    if entity_data.type == EntityTypeEnum.question:
        await create_question_entity(session, entity.id, entity_data)
    elif entity_data.type == EntityTypeEnum.defect:
        await create_defect_entity(session, entity.id, entity_data)
    elif entity_data.type == EntityTypeEnum.task:
        await create_task_entity(session, entity.id, entity_data)
    elif entity_data.type == EntityTypeEnum.info:
        await create_info_entity(session, entity.id, entity_data)
    elif entity_data.type == EntityTypeEnum.proposal:
        await create_proposal_entity(session, entity.id, entity_data)
    elif entity_data.type == EntityTypeEnum.action_point:
        await create_action_point_entity(session, entity.id, entity_data)

    await session.commit()
    return entity


async def create_question_entity(session: AsyncSession, entity_id: str, data: QuestionEntityCreate):
    """Создает сущность QuestionEntity"""
    question = QuestionEntity(
        entity_id=entity_id,
        body=data.body,
        priority=data.priority,
        status=QuestionStatus.created,
        deadline=data.deadline,
    )
    session.add(question)


async def create_defect_entity(session: AsyncSession, entity_id: str, data: DefectEntityCreate):
    """Создает сущность DefectEntity"""
    # Проверяем существование пользователей, если указаны
    if data.executor_id:
        executor_query = select(User).where(User.id == data.executor_id)
        executor_result = await session.execute(executor_query)
        if not executor_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail='Executor not found')

    if data.qa_id:
        qa_query = select(User).where(User.id == data.qa_id)
        qa_result = await session.execute(qa_query)
        if not qa_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail='QA not found')

    defect = DefectEntity(
        entity_id=entity_id,
        body=data.body,
        severity=data.severity,
        reproducible=data.reproducible,
        status=DefectStatus.created,
        deadline=data.deadline,
        executor_id=data.executor_id,
        qa_id=data.qa_id,
    )
    session.add(defect)


async def create_task_entity(session: AsyncSession, entity_id: str, data: TaskEntityCreate):
    """Создает сущность TaskEntity"""
    # Проверяем существование пользователей, если указаны
    if data.executor_id:
        executor_query = select(User).where(User.id == data.executor_id)
        executor_result = await session.execute(executor_query)
        if not executor_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail='Executor not found')

    if data.qa_id:
        qa_query = select(User).where(User.id == data.qa_id)
        qa_result = await session.execute(qa_query)
        if not qa_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail='QA not found')

    task = TaskEntity(
        entity_id=entity_id,
        body=data.body,
        severity=data.severity,
        reproducible=data.reproducible,
        status=TaskStatus.created,
        deadline=data.deadline,
        executor_id=data.executor_id,
        qa_id=data.qa_id,
    )
    session.add(task)


async def create_info_entity(session: AsyncSession, entity_id: str, data: InfoEntityCreate):
    """Создает сущность InfoEntity с требуемыми пользователями"""
    info = InfoEntity(entity_id=entity_id, body=data.body, deadline=data.deadline)
    session.add(info)
    await session.flush()  # Получаем info.entity_id для создания InfoRequiredUser

    # Добавляем требуемых пользователей
    if data.required_user_ids:
        for user_id in data.required_user_ids:
            # Проверяем существование пользователя
            user_query = select(User).where(User.id == user_id)
            user_result = await session.execute(user_query)
            if user_result.scalar_one_or_none():
                required_user = InfoRequiredUser(
                    info_id=entity_id,  # entity_id используется как внешний ключ
                    user_id=user_id,
                )
                session.add(required_user)


async def create_proposal_entity(session: AsyncSession, entity_id: str, data: ProposalEntityCreate):
    """Создает сущность ProposalEntity"""
    proposal = ProposalEntity(
        entity_id=entity_id, body=data.body, priority=data.priority, status=ProposalStatus.created
    )
    session.add(proposal)


async def create_action_point_entity(session: AsyncSession, entity_id: str, data: ActionPointEntityCreate):
    """Создает сущность ActionPointEntity"""
    action_point = ActionPointEntity(
        entity_id=entity_id,
        body=data.body,
        priority=data.priority,
        executor_id=data.executor_id,
        status=ActionPointStatus.created,
        deadline=data.deadline,
    )
    session.add(action_point)


async def get_all_entity_types() -> List[Dict]:
    """Получаем все типы сущностей из Enum"""
    return [
        {'id': entity_type.value, 'name': entity_type.value.replace('_', ' ').title()} for entity_type in EntityTypeEnum
    ]


@router.get('/api/entities/{entity_id}')
async def get_entity(
    entity_id: str,
    user_login: str = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Получение полных данных Entity"""
    # Получаем текущего пользователя
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    # Получаем Entity с загрузкой всех связанных данных, включая direct_chat.users
    entity_query = (
        select(Entity)
        .where(Entity.id == entity_id)
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
            # ЗАГРУЖАЕМ direct_chat и его users - важно для проверки доступа
            selectinload(Entity.direct_chat).selectinload(DirectChat.users),
            # Загружаем channel, если нужно
            selectinload(Entity.channel),
        )
    )
    entity_result = await session.execute(entity_query)
    entity = entity_result.scalar_one_or_none()

    if not entity:
        raise HTTPException(status_code=404, detail='Entity not found')

    # Проверяем доступ пользователя
    if entity.channel_id:
        # Загружаем каналы пользователя
        user_channels_query = select(User).where(User.id == user.id).options(selectinload(User.channels))
        user_channels_result = await session.execute(user_channels_query)
        user_with_channels = user_channels_result.scalar_one()
        user_channel_ids = [ch.id for ch in user_with_channels.channels]

        if entity.channel_id not in user_channel_ids:
            raise HTTPException(status_code=403, detail='Access denied')
    elif entity.direct_chat_id:
        # Проверяем доступ к чату - теперь direct_chat и его users уже загружены
        if not entity.direct_chat or user not in entity.direct_chat.users:
            raise HTTPException(status_code=403, detail='Access denied')

    # Формируем ответ
    response = {
        'id': str(entity.id),
        'type': entity.type.value,
        'title': entity.title,
        'body': None,
        'priority': None,
        'severity': None,
        'reproducible': None,
        'status': None,
        'deadline': None,
        'executor_id': None,
        'qa_id': None,
        'required_user_ids': [],
    }

    # Заполняем данные в зависимости от типа Entity
    if entity.type == EntityTypeEnum.question and entity.question:
        response.update({
            'body': entity.question.body,
            'priority': entity.question.priority,
            'status': entity.question.status.value,
            'deadline': entity.question.deadline.isoformat() if entity.question.deadline else None,
        })
    elif entity.type == EntityTypeEnum.defect and entity.defect:
        response.update({
            'body': entity.defect.body,
            'severity': entity.defect.severity,
            'reproducible': entity.defect.reproducible,
            'status': entity.defect.status.value,
            'deadline': entity.defect.deadline.isoformat() if entity.defect.deadline else None,
            'executor_id': str(entity.defect.executor.id) if entity.defect.executor else None,
            'executor_display_name': entity.defect.executor.display_name if entity.defect.executor else None,
            'qa_id': str(entity.defect.qa.id) if entity.defect.qa else None,
            'qa_display_name': entity.defect.qa.display_name if entity.defect.qa else None,
        })
    elif entity.type == EntityTypeEnum.task and entity.task:
        response.update({
            'body': entity.task.body,
            'severity': entity.task.severity,
            'reproducible': entity.task.reproducible,
            'status': entity.task.status.value,
            'deadline': entity.task.deadline.isoformat() if entity.task.deadline else None,
            'executor_id': str(entity.task.executor.id) if entity.task.executor else None,
            'executor_display_name': entity.task.executor.display_name if entity.task.executor else None,
            'qa_id': str(entity.task.qa.id) if entity.task.qa else None,
            'qa_display_name': entity.task.qa.display_name if entity.task.qa else None,
        })
    elif entity.type == EntityTypeEnum.info and entity.info:
        response.update({
            'body': entity.info.body,
            'deadline': entity.info.deadline.isoformat() if entity.info.deadline else None,
            'required_user_ids': [str(ru.user_id) for ru in entity.info.required_users]
            if entity.info.required_users
            else [],
        })
    elif entity.type == EntityTypeEnum.proposal and entity.proposal:
        response.update({
            'body': entity.proposal.body,
            'priority': entity.proposal.priority,
            'status': entity.proposal.status.value,
        })
    elif entity.type == EntityTypeEnum.action_point and entity.action_point:
        response.update({
            'body': entity.action_point.body,
            'priority': entity.action_point.priority,
            'status': entity.action_point.status.value,
            'deadline': entity.action_point.deadline.isoformat() if entity.action_point.deadline else None,
            'executor_id': str(entity.action_point.executor.id) if entity.action_point.executor else None,
            'executor_display_name': entity.action_point.executor.display_name if entity.action_point.executor else None,
        })

    return response


@router.patch('/api/entities/{entity_id}')
async def update_entity(
        entity_id: str,
        entity_data: dict[str, Any],
        user_login: str = Depends(require_auth),
        session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Обновление Entity"""
    # Получаем текущего пользователя
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    # Получаем Entity с загрузкой всех связанных данных
    entity_query = (
        select(Entity)
        .where(Entity.id == entity_id)
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
            # ЗАГРУЖАЕМ direct_chat и его users - важно для проверки доступа
            selectinload(Entity.direct_chat).selectinload(DirectChat.users),
            # Загружаем channel, если нужно
            selectinload(Entity.channel),
        )
    )
    entity_result = await session.execute(entity_query)
    entity = entity_result.scalar_one_or_none()

    if not entity:
        raise HTTPException(status_code=404, detail='Entity not found')

    # Проверяем доступ пользователя
    if entity.channel_id:
        # Загружаем каналы пользователя
        user_channels_query = select(User).where(User.id == user.id).options(selectinload(User.channels))
        user_channels_result = await session.execute(user_channels_query)
        user_with_channels = user_channels_result.scalar_one()
        user_channel_ids = [ch.id for ch in user_with_channels.channels]

        if entity.channel_id not in user_channel_ids:
            raise HTTPException(status_code=403, detail='Access denied')
    elif entity.direct_chat_id:
        # Проверяем доступ к чату - теперь direct_chat и его users уже загружены
        if not entity.direct_chat or user not in entity.direct_chat.users:
            raise HTTPException(status_code=403, detail='Access denied')

    # Проверяем, что пользователь является автором
    if entity.type == EntityTypeEnum.info and str(entity.author.id) != str(user.id):
        raise HTTPException(status_code=403, detail='Only author can edit entity Info')

    # Сохраняем room_type и room_id до коммита
    if entity.channel_id:
        room_type = "channel"
        room_id = str(entity.channel_id)
    else:
        room_type = "chat"
        room_id = str(entity.direct_chat_id)

    # Обновляем общие поля
    if 'title' in entity_data:
        entity.title = entity_data['title']

    # Обновляем поля в зависимости от типа Entity
    if entity.type == EntityTypeEnum.question and entity.question:
        if 'body' in entity_data:
            entity.question.body = entity_data['body']
        if 'priority' in entity_data:
            entity.question.priority = entity_data['priority']
        if 'deadline' in entity_data:
            entity.question.deadline = (
                datetime.fromisoformat(entity_data['deadline'].replace('Z', '+00:00'))
                if entity_data['deadline']
                else None
            )
        if 'status' in entity_data:
            entity.question.status = QuestionStatus(entity_data['status'])

    elif entity.type == EntityTypeEnum.defect and entity.defect:
        if 'body' in entity_data:
            entity.defect.body = entity_data['body']
        if 'severity' in entity_data:
            entity.defect.severity = entity_data['severity']
        if 'reproducible' in entity_data:
            entity.defect.reproducible = entity_data['reproducible']
        if 'deadline' in entity_data:
            entity.defect.deadline = (
                datetime.fromisoformat(entity_data['deadline'].replace('Z', '+00:00'))
                if entity_data['deadline']
                else None
            )
        if 'executor_id' in entity_data:
            entity.defect.executor_id = entity_data['executor_id'] if entity_data['executor_id'] else None
        if 'qa_id' in entity_data:
            entity.defect.qa_id = entity_data['qa_id'] if entity_data['qa_id'] else None
        if 'status' in entity_data:
            entity.defect.status = DefectStatus(entity_data['status'])

    elif entity.type == EntityTypeEnum.task and entity.task:
        if 'body' in entity_data:
            entity.task.body = entity_data['body']
        if 'severity' in entity_data:
            entity.task.severity = entity_data['severity']
        if 'reproducible' in entity_data:
            entity.task.reproducible = entity_data['reproducible']
        if 'deadline' in entity_data:
            entity.task.deadline = (
                datetime.fromisoformat(entity_data['deadline'].replace('Z', '+00:00'))
                if entity_data['deadline']
                else None
            )
        if 'executor_id' in entity_data:
            entity.task.executor_id = entity_data['executor_id'] if entity_data['executor_id'] else None
        if 'qa_id' in entity_data:
            entity.task.qa_id = entity_data['qa_id'] if entity_data['qa_id'] else None
        if 'status' in entity_data:
            entity.task.status = TaskStatus(entity_data['status'])

    elif entity.type == EntityTypeEnum.info and entity.info:
        if 'body' in entity_data:
            entity.info.body = entity_data['body']
        if 'deadline' in entity_data:
            entity.info.deadline = (
                datetime.fromisoformat(entity_data['deadline'].replace('Z', '+00:00'))
                if entity_data['deadline']
                else None
            )

        # Обновляем список обязательных пользователей
        if 'required_user_ids' in entity_data:
            # Удаляем старые записи
            for ru in entity.info.required_users:
                await session.delete(ru)

            # Добавляем новые записи
            for user_id in entity_data['required_user_ids']:
                new_ru = InfoRequiredUser(info_id=entity.info.entity_id, user_id=user_id)
                session.add(new_ru)

    elif entity.type == EntityTypeEnum.proposal and entity.proposal:
        if 'body' in entity_data:
            entity.proposal.body = entity_data['body']
        if 'priority' in entity_data:
            entity.proposal.priority = entity_data['priority']
        if 'status' in entity_data:
            entity.proposal.status = ProposalStatus(entity_data['status'])

    elif entity.type == EntityTypeEnum.action_point and entity.action_point:
        if 'body' in entity_data:
            entity.action_point.body = entity_data['body']
        if 'priority' in entity_data:
            entity.action_point.priority = entity_data['priority']
        if 'deadline' in entity_data:
            entity.action_point.deadline = (
                datetime.fromisoformat(entity_data['deadline'].replace('Z', '+00:00'))
                if entity_data['deadline']
                else None
            )
        if 'executor_id' in entity_data:
            entity.action_point.executor_id = entity_data['executor_id'] if entity_data['executor_id'] else None
        if 'status' in entity_data:
            entity.action_point.status = ActionPointStatus(entity_data['status'])

    # Обновляем время изменения
    entity.updated_at = datetime.now(UTC)

    await session.commit()

    # Для Info загружаем свежие данные в той же сессии
    if entity.type == EntityTypeEnum.info:
        read_acknowledges_query = (
            select(Acknowledge.user_id)
            .where(
                Acknowledge.entity_id == entity_id,
                Acknowledge.acknowledged == True,
                Acknowledge.user_id.in_(entity_data['required_user_ids'])
            )
        )
        read_acknowledges_result = await session.execute(read_acknowledges_query)
        read_user_ids = [str(user_id) for user_id in read_acknowledges_result.scalars().all()]
        entity_data["read_count"] = len(read_user_ids)

    from src.app.api.websocket.notifications import send_entity_updated
    await send_entity_updated(
        session=session,
        room_type=room_type,
        room_id=room_id,
        updated_fields=entity_data,
        entity_data={
            "entity_id": str(entity.id),
            "type": entity.type.value,
        }
    )

    return {'message': 'Entity updated successfully'}


@router.post('/api/info/{entity_id}/read')
async def mark_info_as_read(
    entity_id: str,
    user_login: str = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Отметить Info как прочитанное"""
    # Получаем текущего пользователя
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    # Получаем Entity и Info
    entity_query = (
        select(Entity)
        .where(Entity.id == entity_id, Entity.type == EntityTypeEnum.info)
        .options(
            selectinload(Entity.info).selectinload(InfoEntity.required_users),
        )
    )
    entity_result = await session.execute(entity_query)
    entity = entity_result.scalar_one_or_none()

    if not entity or not entity.info:
        raise HTTPException(status_code=404, detail='Info not found')

    # Проверяем, что пользователь должен прочитать это Info
    is_required_user = any(str(ru.user_id) == str(user.id) for ru in entity.info.required_users)

    if not is_required_user:
        raise HTTPException(status_code=403, detail='You are not required to read this info')

    # Проверяем, есть ли уже запись в Acknowledge
    acknowledge_query = select(Acknowledge).where(Acknowledge.entity_id == entity_id, Acknowledge.user_id == user.id)
    acknowledge_result = await session.execute(acknowledge_query)
    acknowledge = acknowledge_result.scalar_one_or_none()

    if acknowledge:
        # Уже есть подтверждение
        if acknowledge.acknowledged:
            raise HTTPException(status_code=400, detail='You have already read this info')
        else:
            # Обновляем существующую запись
            acknowledge.acknowledged = True
            acknowledge.acknowledged_at = datetime.now(UTC)
    else:
        # Создаем новую запись
        acknowledge = Acknowledge(
            entity_id=entity_id, user_id=user.id, acknowledged=True, acknowledged_at=datetime.now(UTC)
        )
        session.add(acknowledge)

    if entity.channel_id:
        room_type = "channel"
        room_id = str(entity.channel_id)
    else:
        room_type = "chat"
        room_id = str(entity.direct_chat_id)

        # Получаем текущее количество прочитавших
    read_count_query = select(func.count(Acknowledge.id)).where(
        Acknowledge.entity_id == entity_id,
        Acknowledge.acknowledged == True
    )
    read_count_result = await session.execute(read_count_query)
    read_count = read_count_result.scalar()

    total_required = len(entity.info.required_users)
    all_acknowledged = read_count >= total_required

    await session.commit()

    from src.app.api.websocket.notifications import send_info_read
    await send_info_read(
        session=session,
        entity_id=entity.id,
        user_id=user.id,
        room_type=room_type,
        room_id=room_id,
        read_data={
            "read_count": read_count,
            "all_acknowledged": all_acknowledged
        }
    )

    return {'message': 'Info marked as read'}


@router.get('/api/info/{entity_id}/reads')
async def get_info_read_status(
    entity_id: str,
    user_login: str = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Получить информацию о прочтении Info"""
    # Получаем текущего пользователя
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    # Получаем Entity и Info
    entity_query = (
        select(Entity)
        .where(Entity.id == entity_id, Entity.type == EntityTypeEnum.info)
        .options(
            selectinload(Entity.author),
            selectinload(Entity.info).selectinload(InfoEntity.required_users).selectinload(InfoRequiredUser.user),
        )
    )
    entity_result = await session.execute(entity_query)
    entity = entity_result.scalar_one_or_none()

    if not entity or not entity.info:
        raise HTTPException(status_code=404, detail='Info not found')

    # Проверяем доступ: только автор или обязательные пользователи могут видеть статус прочтения
    is_author = str(entity.author_id) == str(user.id)
    is_required = any(str(ru.user_id) == str(user.id) for ru in entity.info.required_users)

    if not (is_author or is_required):
        raise HTTPException(status_code=403, detail='Access denied')

    # Получаем все подтверждения для этого Entity
    acknowledges_query = select(Acknowledge).where(Acknowledge.entity_id == entity_id)
    acknowledges_result = await session.execute(acknowledges_query)
    acknowledges = acknowledges_result.scalars().all()

    # Создаем словарь для быстрого доступа
    acknowledges_dict = {}
    for ack in acknowledges:
        user_id_str = str(ack.user_id)
        acknowledges_dict[user_id_str] = {
            'acknowledged': ack.acknowledged,
            'acknowledged_at': ack.acknowledged_at.isoformat() if ack.acknowledged_at else None,
        }

    # Формируем списки прочитавших и непрочитавших
    read = []
    not_read = []

    for ru in entity.info.required_users:
        user_data = {'id': str(ru.user.id), 'display_name': ru.user.display_name}

        ack_data = acknowledges_dict.get(str(ru.user.id))
        if ack_data and ack_data['acknowledged']:
            read.append({'user': user_data, 'acknowledged_at': ack_data['acknowledged_at']})
        else:
            not_read.append(user_data)

    return {'read': read, 'not_read': not_read, 'total': len(entity.info.required_users), 'read_count': len(read)}


@router.get('/api/entity-types')
async def get_entity_types(
    user_login=Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> List[Dict]:
    """Получение всех типов сущностей"""
    return await get_all_entity_types()
