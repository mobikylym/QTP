import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.app.api.deps.auth import require_auth
from src.app.api.deps.database import get_session
from src.app.api.http.router import router
from src.app.storage.models import (
    Acknowledge,
    ActionPointEntity,
    ActionPointStatus,
    DefectEntity,
    DefectStatus,
    DirectChat,
    Entity,
    EntityTypeEnum,
    InfoEntity,
    InfoRequiredUser,
    ProposalStatus,
    QuestionStatus,
    TaskEntity,
    TaskStatus,
    User,
)


def parse_datetime(dt_str: str) -> datetime:
    """Парсит строку datetime в объект datetime."""
    try:
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    except ValueError:
        if '.' in dt_str:
            dt_str = dt_str.split('.')[0]
        if dt_str.endswith('Z'):
            dt_str = dt_str[:-1] + '+00:00'
        if not re.search(r'[+-]\d{2}:?\d{2}$', dt_str):
            dt_str += '+00:00'
        return datetime.fromisoformat(dt_str)


@router.get('/task-explorer/', response_class=HTMLResponse)
async def task_explorer_page(user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)):
    """Страница Обозреватель задач с тремя вкладками"""
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    return generate_task_explorer_html(user)


@router.get('/api/entities-filter', response_class=JSONResponse)
async def filter_entities(
    entity_types: list[str] | None = Query(None, description='Типы entity (мультиселект)'),
    channel_ids: list[str] | None = Query(None, description='ID каналов (мультиселект)'),
    chat_ids: list[str] | None = Query(None, description='ID чатов (мультиселект)'),
    author_id: str | None = Query(None, description='ID автора (одиночный выбор)'),
    statuses: list[str] | None = Query(None, description='Статусы (мультиселект)'),
    deadline_from: datetime | None = Query(None, description='Дедлайн от'),
    deadline_to: datetime | None = Query(None, description='Дедлайн до'),
    executor_id: str | None = Query(None, description='ID исполнителя (одиночный выбор)'),
    qa_id: str | None = Query(None, description='ID QA (одиночный выбор)'),
    created_from: datetime | None = Query(None, description='Создано от'),
    created_to: datetime | None = Query(None, description='Создано до'),
    tab: str | None = Query(None, description='Вкладка: my_tasks, outdated, all_filters'),
    user_login=Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    """Фильтрация entity по заданным критериям"""
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    if tab == 'my_tasks':
        return await get_my_tasks(user, session)
    elif tab == 'outdated':
        return await get_outdated_tasks(user, session)

    entities = await filter_entities_query(
        user=user,
        session=session,
        entity_types=entity_types,
        channel_ids=channel_ids,
        chat_ids=chat_ids,
        author_id=author_id,
        statuses=statuses,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        executor_id=executor_id,
        qa_id=qa_id,
        created_from=created_from,
        created_to=created_to,
    )

    return {'entities': entities}


async def filter_entities_query(
    user: User,
    session: AsyncSession,
    entity_types: list[str] | None = None,
    channel_ids: list[str] | None = None,
    chat_ids: list[str] | None = None,
    author_id: str | None = None,
    statuses: list[str] | None = None,
    deadline_from: datetime | None = None,
    deadline_to: datetime | None = None,
    executor_id: str | None = None,
    qa_id: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> list[dict[str, Any]]:
    """Основной запрос фильтрации entity"""
    query = select(Entity).options(
        selectinload(Entity.author),
        selectinload(Entity.channel),
        selectinload(Entity.direct_chat),
        selectinload(Entity.comments),
        selectinload(Entity.question),
        selectinload(Entity.defect).options(joinedload(DefectEntity.executor), joinedload(DefectEntity.qa)),
        selectinload(Entity.task).options(joinedload(TaskEntity.executor), joinedload(TaskEntity.qa)),
        selectinload(Entity.info).selectinload(InfoEntity.required_users).selectinload(InfoRequiredUser.user),
        selectinload(Entity.proposal),
        selectinload(Entity.action_point).options(joinedload(ActionPointEntity.executor)),
    )

    channel_conditions = []
    chat_conditions = []

    if channel_ids:
        channel_conditions.append(Entity.channel_id.in_([UUID(cid) for cid in channel_ids]))
    else:
        user_channel_ids = [ch.id for ch in user.channels]
        if user_channel_ids:
            channel_conditions.append(Entity.channel_id.in_(user_channel_ids))

    if chat_ids:
        chat_conditions.append(Entity.direct_chat_id.in_([UUID(cid) for cid in chat_ids]))
    else:
        user_chat_ids = [chat.id for chat in user.direct_chats if not chat.is_self_chat]
        if user_chat_ids:
            chat_conditions.append(Entity.direct_chat_id.in_(user_chat_ids))

    access_conditions = []
    if channel_conditions:
        access_conditions.append(and_(*channel_conditions))
    if chat_conditions:
        access_conditions.append(and_(*chat_conditions))

    if access_conditions:
        query = query.where(or_(*access_conditions))
    else:
        return []

    if entity_types:
        entity_type_enums = [EntityTypeEnum(et) for et in entity_types]
        query = query.where(Entity.type.in_(entity_type_enums))

    if author_id:
        query = query.where(Entity.author_id == UUID(author_id))

    if created_from:
        query = query.where(Entity.created_at >= created_from)
    if created_to:
        query = query.where(Entity.created_at <= created_to)

    result = await session.execute(query)
    entities = result.scalars().all()

    acknowledges_dict = {}
    info_entity_ids = [str(entity.id) for entity in entities if entity.type == EntityTypeEnum.info]

    if info_entity_ids:
        acknowledges_query = select(Acknowledge).where(
            Acknowledge.entity_id.in_([UUID(eid) for eid in info_entity_ids])
        )
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

    filtered_entities = []

    for entity in entities:
        if statuses and not check_entity_status(entity, statuses, user.id, acknowledges_dict):
            continue

        if not check_deadline(entity, deadline_from, deadline_to):
            continue

        if executor_id:
            if not check_executor(entity, UUID(executor_id)):
                continue

        if qa_id:
            if not check_qa(entity, UUID(qa_id)):
                continue

        filtered_entities.append(entity)

    result_list = []
    for entity in filtered_entities:
        entity_dict = await entity_to_dict(entity, user.id, session, acknowledges_dict)
        result_list.append(entity_dict)

    result_list.sort(
        key=lambda x: (
            1 if x.get('deadline') is None else 0,
            parse_datetime(x.get('deadline')) if x.get('deadline') else datetime.max.replace(tzinfo=UTC),
            -(x.get('priority') or 0),
        )
    )

    return result_list


def check_entity_status(
    entity: Entity, statuses: list[str], user_id: UUID, acknowledges_dict: dict[str, dict[str, Any]]
) -> bool:
    """Проверка статуса entity с учетом специальных статусов для Info"""
    entity_status = None

    if entity.type == EntityTypeEnum.question and entity.question:
        entity_status = entity.question.status.value
    elif entity.type == EntityTypeEnum.defect and entity.defect:
        entity_status = entity.defect.status.value
    elif entity.type == EntityTypeEnum.task and entity.task:
        entity_status = entity.task.status.value
    elif entity.type == EntityTypeEnum.info and entity.info:
        is_required = False
        if entity.info.required_users:
            for ru in entity.info.required_users:
                if str(ru.user_id) == str(user_id):
                    is_required = True
                    break

        if is_required:
            entity_id_str = str(entity.id)
            user_id_str = str(user_id)
            if (
                entity_id_str in acknowledges_dict
                and user_id_str in acknowledges_dict[entity_id_str]
                and acknowledges_dict[entity_id_str][user_id_str]['acknowledged']
            ):
                entity_status = 'read'
            else:
                entity_status = 'unread'
        else:
            entity_status = 'not_required'
    elif entity.type == EntityTypeEnum.proposal and entity.proposal:
        entity_status = entity.proposal.status.value
    elif entity.type == EntityTypeEnum.action_point and entity.action_point:
        entity_status = entity.action_point.status.value

    return entity_status in statuses if entity_status else False


def check_info_read(entity: Entity, user_id: UUID) -> bool:
    """Проверяет, прочитал ли пользователь Info entity"""
    if not entity.info:
        return False

    is_required = False
    for ru in entity.info.required_users:
        if str(ru.user_id) == str(user_id):
            is_required = True
            break

    if not is_required:
        return False

    return False


def check_deadline(entity: Entity, deadline_from: datetime | None, deadline_to: datetime | None) -> bool:
    """Проверка дедлайна entity"""
    entity_deadline = None

    if entity.type == EntityTypeEnum.question and entity.question:
        entity_deadline = entity.question.deadline
    elif entity.type == EntityTypeEnum.defect and entity.defect:
        entity_deadline = entity.defect.deadline
    elif entity.type == EntityTypeEnum.task and entity.task:
        entity_deadline = entity.task.deadline
    elif entity.type == EntityTypeEnum.info and entity.info:
        entity_deadline = entity.info.deadline
    elif entity.type == EntityTypeEnum.action_point and entity.action_point:
        entity_deadline = entity.action_point.deadline

    if entity_deadline is None:
        return True

    if deadline_from and entity_deadline < deadline_from:
        return False
    if deadline_to and entity_deadline > deadline_to:
        return False

    return True


def check_executor(entity: Entity, executor_id: UUID) -> bool:
    """Проверка исполнителя entity"""
    if entity.type not in [EntityTypeEnum.defect, EntityTypeEnum.task, EntityTypeEnum.action_point]:
        return False

    if entity.type == EntityTypeEnum.defect and entity.defect:
        return str(entity.defect.executor_id) == str(executor_id)
    elif entity.type == EntityTypeEnum.task and entity.task:
        return str(entity.task.executor_id) == str(executor_id)
    elif entity.type == EntityTypeEnum.action_point and entity.action_point:
        return str(entity.action_point.executor_id) == str(executor_id)

    return False


def check_qa(entity: Entity, qa_id: UUID) -> bool:
    """Проверка QA entity"""
    if entity.type not in [EntityTypeEnum.defect, EntityTypeEnum.task]:
        return False

    if entity.type == EntityTypeEnum.defect and entity.defect:
        return str(entity.defect.qa_id) == str(qa_id)
    elif entity.type == EntityTypeEnum.task and entity.task:
        return str(entity.task.qa_id) == str(qa_id)

    return False


async def entity_to_dict(
    entity: Entity, user_id: UUID, session: AsyncSession, acknowledges_dict: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Преобразование entity в словарь для отображения"""
    entity_data = {
        'id': str(entity.id),
        'type': entity.type.value,
        'title': entity.title,
        'author_id': str(entity.author.id) if entity.author else None,
        'author_display_name': entity.author.display_name if entity.author else 'Unknown',
        'created_at': entity.created_at.isoformat() if entity.created_at else None,
        'updated_at': entity.updated_at.isoformat() if entity.updated_at else None,
        'comment_count': len(entity.comments) if entity.comments else 0,
    }

    if entity.channel:
        entity_data['source_type'] = 'channel'
        entity_data['source_id'] = str(entity.channel.id)
        entity_data['source_name'] = entity.channel.name
    elif entity.direct_chat:
        entity_data['source_type'] = 'chat'
        entity_data['source_id'] = str(entity.direct_chat.id)
        other_users = [u for u in entity.direct_chat.users if str(u.id) != str(entity.author_id)]
        if other_users:
            entity_data['source_name'] = other_users[0].display_name
        else:
            entity_data['source_name'] = 'Direct Chat'

    if entity.type == EntityTypeEnum.question and entity.question:
        deadline = None
        if entity.question.deadline:
            if entity.question.deadline.tzinfo is None:
                deadline = entity.question.deadline.replace(tzinfo=UTC).isoformat()
            else:
                deadline = entity.question.deadline.isoformat()
        entity_data.update({
            'body': entity.question.body,
            'priority': entity.question.priority,
            'status': entity.question.status.value,
            'deadline': deadline,
        })
    elif entity.type == EntityTypeEnum.defect and entity.defect:
        deadline = None
        if entity.defect.deadline:
            if entity.defect.deadline.tzinfo is None:
                deadline = entity.defect.deadline.replace(tzinfo=UTC).isoformat()
            else:
                deadline = entity.defect.deadline.isoformat()
        entity_data.update({
            'body': entity.defect.body,
            'severity': entity.defect.severity,
            'reproducible': entity.defect.reproducible,
            'status': entity.defect.status.value,
            'deadline': deadline,
            'executor': entity.defect.executor.display_name if entity.defect.executor else None,
            'executor_id': str(entity.defect.executor_id) if entity.defect.executor_id else None,
            'qa': entity.defect.qa.display_name if entity.defect.qa else None,
            'qa_id': str(entity.defect.qa_id) if entity.defect.qa_id else None,
        })
    elif entity.type == EntityTypeEnum.task and entity.task:
        deadline = None
        if entity.task.deadline:
            if entity.task.deadline.tzinfo is None:
                deadline = entity.task.deadline.replace(tzinfo=UTC).isoformat()
            else:
                deadline = entity.task.deadline.isoformat()
        entity_data.update({
            'body': entity.task.body,
            'severity': entity.task.severity,
            'reproducible': entity.task.reproducible,
            'status': entity.task.status.value,
            'deadline': deadline,
            'executor': entity.task.executor.display_name if entity.task.executor else None,
            'executor_id': str(entity.task.executor_id) if entity.task.executor_id else None,
            'qa': entity.task.qa.display_name if entity.task.qa else None,
            'qa_id': str(entity.task.qa_id) if entity.task.qa_id else None,
        })
    elif entity.type == EntityTypeEnum.info and entity.info:
        has_read = False
        read_count = 0
        all_acknowledged = True
        required_users_list = []

        if entity.info.required_users:
            for ru in entity.info.required_users:
                user_data = {'id': str(ru.user.id), 'display_name': ru.user.display_name}
                required_users_list.append(user_data)

                entity_id_str = str(entity.id)
                user_id_str = str(ru.user.id)

                if (
                    entity_id_str in acknowledges_dict
                    and user_id_str in acknowledges_dict[entity_id_str]
                    and acknowledges_dict[entity_id_str][user_id_str]['acknowledged']
                ):
                    read_count += 1
                    if user_id_str == str(user_id):
                        has_read = True
                else:
                    all_acknowledged = False

        deadline = None
        if entity.info.deadline:
            if entity.info.deadline.tzinfo is None:
                deadline = entity.info.deadline.replace(tzinfo=UTC).isoformat()
            else:
                deadline = entity.info.deadline.isoformat()

        entity_data.update({
            'body': entity.info.body,
            'deadline': deadline,
            'required_users': required_users_list,
            'has_read': has_read,
            'read_count': read_count,
            'all_acknowledged': all_acknowledged,
        })
    elif entity.type == EntityTypeEnum.proposal and entity.proposal:
        entity_data.update({
            'body': entity.proposal.body,
            'priority': entity.proposal.priority,
            'status': entity.proposal.status.value,
        })
    elif entity.type == EntityTypeEnum.action_point and entity.action_point:
        deadline = None
        if entity.action_point.deadline:
            if entity.action_point.deadline.tzinfo is None:
                deadline = entity.action_point.deadline.replace(tzinfo=UTC).isoformat()
            else:
                deadline = entity.action_point.deadline.isoformat()
        entity_data.update({
            'body': entity.action_point.body,
            'priority': entity.action_point.priority,
            'status': entity.action_point.status.value,
            'executor': entity.action_point.executor.display_name if entity.action_point.executor else None,
            'executor_id': str(entity.action_point.executor_id) if entity.action_point.executor_id else None,
            'deadline': deadline,
        })

    return entity_data


async def get_my_tasks(user: User, session: AsyncSession) -> dict[str, Any]:
    """Получение задач пользователя для вкладки My Tasks"""
    user_id = user.id

    excluded_statuses = ['closed', 'rejected', 'accepted', 'read']

    all_statuses = await get_all_statuses(session)
    included_statuses = [status['id'] for status in all_statuses if status['id'] not in excluded_statuses]

    author_tasks = await filter_entities_query(
        user=user,
        session=session,
        author_id=str(user_id),
        statuses=included_statuses,
    )

    executor_tasks = await filter_entities_query(
        user=user,
        session=session,
        executor_id=str(user_id),
        statuses=included_statuses,
    )

    qa_tasks = await filter_entities_query(
        user=user,
        session=session,
        qa_id=str(user_id),
        statuses=included_statuses,
    )

    info_tasks = await filter_entities_query(
        user=user,
        session=session,
        entity_types=['info'],
    )

    filtered_info_tasks = []
    for task in info_tasks:
        is_author = task.get('author_id') == str(user_id)

        is_in_required_users = False
        if task.get('required_users'):
            is_in_required_users = any(ru['id'] == str(user_id) for ru in task['required_users'])

        if is_author:
            if not task.get('all_acknowledged', True):
                filtered_info_tasks.append(task)
        elif is_in_required_users:
            if not task.get('has_read', False):
                filtered_info_tasks.append(task)

    all_tasks = {}

    for task in author_tasks + executor_tasks + qa_tasks:
        if task.get('type') != 'info':
            all_tasks[task['id']] = task

    for task in filtered_info_tasks:
        all_tasks[task['id']] = task

    result_list = list(all_tasks.values())

    result_list.sort(
        key=lambda x: (
            1 if x.get('deadline') is None else 0,
            parse_datetime(x.get('deadline')) if x.get('deadline') else datetime.max.replace(tzinfo=UTC),
            -(x.get('priority') or 0),
        )
    )

    return {'entities': result_list}


async def get_outdated_tasks(user: User, session: AsyncSession) -> dict[str, Any]:
    """Получение просроченных задач"""
    current_time = datetime.now(UTC)

    outdated_tasks = await filter_entities_query(
        user=user,
        session=session,
        deadline_to=current_time,
    )

    active_statuses = ['created', 'in_progress', 'ready_for_test', 'in_testing', 'discussed', 'answered', 'unread']

    filtered_tasks = []
    for task in outdated_tasks:
        if task.get('status') in active_statuses:
            filtered_tasks.append(task)

    filtered_tasks.sort(
        key=lambda x: (
            1 if x.get('deadline') is None else 0,
            parse_datetime(x.get('deadline')) if x.get('deadline') else datetime.max.replace(tzinfo=UTC),
            -(x.get('priority') or 0),
        )
    )

    return {'entities': filtered_tasks}


@router.get('/api/task-explorer/filter-options', response_class=JSONResponse)
async def get_filter_options(user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)):
    """Получение всех опций для фильтров"""
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    entity_types = await get_all_entity_types(session)

    channels_by_group = {}
    for ch in user.channels:
        if ch.group.value not in channels_by_group:
            channels_by_group[ch.group.value] = []
        channels_by_group[ch.group.value].append({'id': str(ch.id), 'name': ch.name})

    chats_by_dept = {}
    user_chats_query = (
        select(DirectChat).join(DirectChat.users).where(DirectChat.is_self_chat == False, User.id == user.id)
    )
    chats_result = await session.execute(user_chats_query)
    chats = chats_result.scalars().all()

    for chat in chats:
        other_user = next(u for u in chat.users if u.id != user.id)
        dept = other_user.department.value

        if dept not in chats_by_dept:
            chats_by_dept[dept] = []

        chats_by_dept[dept].append({
            'chat_id': str(chat.id),
            'display_name': other_user.display_name,
            'user_id': str(other_user.id),
        })

    users_by_dept = {}
    all_users_query = select(User).order_by(User.department, User.display_name)
    all_users_result = await session.execute(all_users_query)
    all_users = all_users_result.scalars().all()

    for u in all_users:
        dept = u.department.value
        if dept not in users_by_dept:
            users_by_dept[dept] = []

        users_by_dept[dept].append({'id': str(u.id), 'display_name': u.display_name, 'is_me': u.id == user.id})

    statuses = await get_all_statuses(session)

    return {
        'entity_types': entity_types,
        'channels': channels_by_group,
        'chats': chats_by_dept,
        'users': users_by_dept,
        'statuses': statuses,
        'current_user_id': str(user.id),
    }


async def get_all_entity_types(session: AsyncSession) -> list[dict[str, str]]:
    """Получение всех уникальных типов entity"""
    unique_types = []
    seen_types = set()

    for entity_type in EntityTypeEnum:
        if entity_type.value not in seen_types:
            seen_types.add(entity_type.value)
            unique_types.append({'id': entity_type.value, 'name': entity_type.value.replace('_', ' ').title()})

    return unique_types


async def get_all_statuses(session: AsyncSession) -> list[dict[str, str]]:
    """Получение всех уникальных статусов без дубликатов"""
    statuses = []
    seen_statuses = set()

    for status in QuestionStatus:
        status_value = status.value
        status_name = status_value.replace('_', ' ').title()
        if status_value not in seen_statuses:
            seen_statuses.add(status_value)
            statuses.append({'id': status_value, 'name': status_name, 'entity_type': 'question'})

    for status in DefectStatus:
        status_value = status.value
        status_name = status_value.replace('_', ' ').title()
        if status_value not in seen_statuses:
            seen_statuses.add(status_value)
            statuses.append({'id': status_value, 'name': status_name, 'entity_type': 'defect'})

    for status in TaskStatus:
        status_value = status.value
        status_name = status_value.replace('_', ' ').title()
        if status_value not in seen_statuses:
            seen_statuses.add(status_value)
            statuses.append({'id': status_value, 'name': status_name, 'entity_type': 'task'})

    for status in ProposalStatus:
        status_value = status.value
        status_name = status_value.replace('_', ' ').title()
        if status_value not in seen_statuses:
            seen_statuses.add(status_value)
            statuses.append({'id': status_value, 'name': status_name, 'entity_type': 'proposal'})

    for status in ActionPointStatus:
        status_value = status.value
        status_name = status_value.replace('_', ' ').title()
        if status_value not in seen_statuses:
            seen_statuses.add(status_value)
            statuses.append({'id': status_value, 'name': status_name, 'entity_type': 'action_point'})

    for status_id, status_name in [('read', 'Read'), ('unread', 'Unread'), ('not_required', 'Not Required')]:
        if status_id not in seen_statuses:
            seen_statuses.add(status_id)
            statuses.append({'id': status_id, 'name': status_name, 'entity_type': 'info'})

    return statuses


def generate_task_explorer_html(user: User) -> HTMLResponse:
    """Генерирует HTML страницу для Обозреватель задач"""
    user_id_escaped = json.dumps(str(user.id))
    user_display_name_escaped = json.dumps(user.display_name)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Обозреватель задач</title>
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
                overflow: hidden;
            }}

            .header {{
                background-color: #f0f0f0;
                padding: 5px 20px;
                border-bottom: 1px solid #ddd;
            }}
            
            h2 {{
              margin: 10px 0px;
            }}

            .tabs {{
                display: flex;
                border-bottom: 1px solid #ddd;
                background-color: #fff;
            }}

            .tab {{
                padding: 5px 24px;
                cursor: pointer;
                border: none;
                background: none;
                font-size: 14px;
                font-weight: 500;
                color: #666;
                border-bottom: 2px solid transparent;
                transition: all 0.2s;
            }}

            .tab:hover {{
                background-color: #f5f5f5;
                color: #333;
            }}

            .tab.active {{
                color: #5865f2;
                border-bottom-color: #5865f2;
                background-color: #f8f9ff;
            }}

            .tab-content {{
                flex: 1;
                overflow-y: auto;
                padding: 20px;
                background-color: #fff;
                display: none;
            }}

            .tab-content.active {{
                display: block;
            }}

            .compact-filter-group {{
                flex: 1;
                min-width: 200px;
                max-width: 250px;
            }}

            .compact-filter {{
                position: relative;
            }}

            .compact-filter-header {{
                padding: 5px 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: white;
                cursor: pointer;
                display: flex;
                justify-content: space-between;
                align-items: center;
                font-size: 13px;
                transition: border-color 0.2s;
            }}

            .compact-filter-header:hover {{
                border-color: #5865f2;
            }}

            .compact-filter-header.active {{
                border-color: #5865f2;
                background-color: #f8f9ff;
            }}

            .compact-filter-title {{
                display: flex;
                align-items: center;
                gap: 8px;
                overflow: hidden;
            }}

            .filter-name {{
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                flex: 1;
            }}

            .filter-counter {{
                background-color: #5865f2;
                color: white;
                border-radius: 10px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: bold;
                min-width: 20px;
                text-align: center;
                display: none;
            }}

            .filter-counter.show {{
                display: inline-block;
            }}

            .compact-filter-arrow {{
                transition: transform 0.2s;
                font-size: 10px;
                color: #666;
            }}

            .compact-filter-content {{
                display: none;
                position: absolute;
                top: 100%;
                left: 0;
                right: 0;
                background: white;
                border: 1px solid #ddd;
                border-radius: 4px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                margin-top: 4px;
                max-height: 350px;
                overflow-y: auto;
                z-index: 100;
            }}

            .compact-filter-content.active {{
                display: block;
            }}

            .filter-search {{
                padding: 8px 12px;
                border-bottom: 1px solid #eee;
            }}

            .filter-search-input {{
                width: 100%;
                padding: 6px 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
                font-size: 12px;
            }}

            .filter-checkboxes {{
                padding: 8px 0;
                max-height: 250px;
                overflow-y: auto;
            }}

            .filter-checkbox-item {{
                padding: 6px 12px;
                display: flex;
                align-items: center;
                gap: 8px;
                cursor: pointer;
                transition: background-color 0.2s;
            }}

            .filter-checkbox-item:hover {{
                background-color: #f5f5f5;
            }}

            .filter-checkbox-item input[type="checkbox"] {{
                margin: 0;
            }}

            .filter-checkbox-label {{
                font-size: 13px;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                flex: 1;
            }}

            .filter-actions {{
                padding: 8px 12px;
                border-top: 1px solid #eee;
                display: flex;
                gap: 8px;
            }}

            .select-all-btn,
            .clear-selection-btn {{
                flex: 1;
                padding: 6px;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                cursor: pointer;
            }}

            .select-all-btn {{
                background-color: #f0f0f0;
                color: #333;
            }}

            .clear-selection-btn {{
                background-color: #f0f0f0;
                color: #333;
            }}

            .select-all-btn:hover {{
                background-color: #e0e0e0;
            }}

            .clear-selection-btn:hover {{
                background-color: #e0e0e0;
            }}

            .filters-panel {{
                padding: 5px;
                background-color: #f9f9f9;
                border-bottom: 1px solid #ddd;
                display: none;
            }}

            .filters-panel.active {{
                display: block;
            }}

            .filter-row {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin-bottom: 10px;
            }}

            .filter-group {{
                flex: 1;
                min-width: 200px;
                max-width: 300px;
            }}

            #filter-entity-types,
            #filter-channels,
            #filter-chats,
            #filter-status {{
                display: none;
            }}

            .regular-filter-group {{
                flex: 1;
                min-width: 220px;
                max-width: 300px;
            }}

            .date-input-group {{
                display: flex;
                gap: 10px;
            }}

            .date-input {{
                flex: 1;
                padding: 5px 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
                font-size: 13px;
            }}

            .filter-select[multiple] {{
                height: 120px;
            }}

            .filter-label {{
                display: block;
                margin-bottom: 2px;
                font-weight: bold;
                color: #555;
                font-size: 13px;
            }}

            .filter-select {{
                width: 100%;
                padding: 5px 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
                font-size: 13px;
                background-color: white;
            }}

            .filter-select[multiple] {{
                height: 120px;
            }}

            .apply-filters-btn {{
                padding: 5px 5px;
                background-color: #5865f2;
                color: white;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-weight: bold;
                font-size: 12px;
                margin-top: 5px;
            }}

            .apply-filters-btn:hover {{
                background-color: #4752c4;
            }}

            .clear-filters-btn {{
                padding: 5px 5px;
                background-color: #99aab5;
                color: white;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-weight: bold;
                font-size: 12px;
                margin-top: 5px;
                margin-left: 10px;
            }}

            .clear-filters-btn:hover {{
                background-color: #8798a3;
            }}

            .loading {{
                text-align: center;
                padding: 40px;
                color: #888;
            }}

            .empty-state {{
                text-align: center;
                padding: 60px 20px;
                color: #888;
            }}

            .empty-state-icon {{
                font-size: 48px;
                margin-bottom: 20px;
                opacity: 0.5;
            }}

            .entity-list {{
                display: flex;
                flex-direction: column;
                gap: 5px;
            }}

            .entity-card {{
                padding: 10px;
                border-radius: 8px;
                background-color: #f9f9f9;
                border-left: 4px solid #5865f2;
                box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                transition: transform 0.2s, box-shadow 0.2s;
                margin-bottom: 20px;
            }}

            .entity-card:hover {{
                transform: translateY(-2px);
                box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            }}

            .entity-card.outdated {{
                border-left-color: #f04747;
                background-color: #fff5f5;
            }}

            .entity-header {{
                display: flex;
                justify-content: space-between;
                align-items: flex-start;
                margin-bottom: 12px;
                flex-wrap: wrap;
                gap: 10px;
            }}

            .entity-title {{
                font-weight: bold;
                color: #333;
                font-size: 16px;
                margin: 0;
                flex: 1;
            }}

            .entity-type {{
                display: inline-block;
                padding: 3px 8px;
                background-color: #5865f2;
                color: white;
                border-radius: 12px;
                font-size: 11px;
                font-weight: bold;
                text-transform: uppercase;
            }}

            .entity-meta {{
                display: flex;
                flex-wrap: wrap;
                gap: 15px;
                font-size: 13px;
                color: #666;
                margin-top: 8px;
            }}

            .entity-meta-item {{
                display: flex;
                align-items: center;
                gap: 5px;
            }}

            .entity-source {{
                color: #5865f2;
                font-weight: 500;
            }}

            .entity-body {{
                margin-top: 15px;
                padding: 15px;
                background-color: white;
                border-radius: 6px;
                border: 1px solid #eee;
                font-size: 14px;
                line-height: 1.5;
                white-space: pre-wrap;
                word-wrap: break-word;
            }}

            .entity-details {{
                margin-top: 15px;
                display: flex;
                flex-wrap: wrap;
                gap: 15px;
                font-size: 13px;
            }}

            .entity-field {{
                display: flex;
                gap: 5px;
            }}

            .entity-field-label {{
                font-weight: bold;
                color: #555;
            }}

            .entity-actions {{
                display: flex;
                gap: 8px;
                margin-top: 20px;
                justify-content: flex-end;
            }}

            .entity-action-btn {{
                padding: 6px 12px;
                background-color: #5865f2;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                cursor: pointer;
                display: flex;
                align-items: center;
                gap: 5px;
            }}

            .entity-action-btn:hover {{
                background-color: #4752c4;
            }}

            .entity-action-btn.secondary {{
                background-color: #99aab5;
            }}

            .entity-action-btn.secondary:hover {{
                background-color: #8798a3;
            }}

            .entity-priority {{
                display: inline-block;
                padding: 2px 8px;
                background-color: #ff9800;
                color: white;
                border-radius: 10px;
                font-size: 11px;
                font-weight: bold;
            }}

            .entity-status {{
                display: inline-block;
                padding: 2px 8px;
                border-radius: 10px;
                font-size: 11px;
                font-weight: bold;
            }}

            .status-created {{ background-color: #99aab5; color: white; }}
            .status-in_progress {{ background-color: #5865f2; color: white; }}
            .status-ready_for_test {{ background-color: #faa61a; color: white; }}
            .status-in_testing {{ background-color: #f04747; color: white; }}
            .status-closed {{ background-color: #43b581; color: white; }}
            .status-answered {{ background-color: #43b581; color: white; }}
            .status-discussed {{ background-color: #faa61a; color: white; }}
            .status-accepted {{ background-color: #43b581; color: white; }}
            .status-rejected {{ background-color: #f04747; color: white; }}
            .status-read {{ background-color: #43b581; color: white; }}
            .status-unread {{ background-color: #f04747; color: white; }}

            .entity-severity {{
                display: inline-block;
                padding: 2px 8px;
                background-color: #eb459e;
                color: white;
                border-radius: 10px;
                font-size: 11px;
                font-weight: bold;
            }}

            .optgroup-header {{
                font-weight: bold;
                color: #666;
                padding: 5px 10px;
                background-color: #f5f5f5;
                border-bottom: 1px solid #ddd;
            }}

            .optgroup-option {{
                padding-left: 20px !important;
            }}

            .filters-actions {{
                display: flex;
                justify-content: flex-end;
                border-top: 1px solid #eee;
                margin-top: 5px;
            }}

            .date-filter-group {{
                flex: 1;
                min-width: 300px;
                max-width: 500px;
            }}

            @media (max-width: 1200px) {{
                .date-filter-group {{
                    min-width: 100%;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="sidebar" id="sidebar"></div>

            <div class="main-content">
                <div class="header">
                    <h2>Обозреватель задач</h2>
                </div>

                <div class="tabs">
                    <button class="tab active" data-tab="my-tasks">Мои задачи</button>
                    <button class="tab" data-tab="outdated">Просроченные</button>
                    <button class="tab" data-tab="all-filters">Все фильтры</button>
                </div>

                <div class="filters-panel" id="filters-panel">
                    <div class="filter-row">
                        <div class="compact-filter-group">
                            <label class="filter-label">Типы сущностей</label>
                            <div class="compact-filter" id="compact-filter-entity-types">
                                <div class="compact-filter-header" onclick="toggleCompactFilter('entity-types')">
                                    <div class="compact-filter-title">
                                        <span class="filter-name">Все типы сущностей</span>
                                        <span class="filter-counter" id="counter-entity-types"></span>
                                    </div>
                                    <span class="compact-filter-arrow">▼</span>
                                </div>
                                <div class="compact-filter-content" id="content-entity-types">
                                    <div class="filter-search">
                                        <input type="text" class="filter-search-input" 
                                               placeholder="Искать типы..." 
                                               onkeyup="filterOptionsFunc('entity-types', this.value)">
                                    </div>
                                    <div class="filter-checkboxes" id="checkboxes-entity-types"></div>
                                    <div class="filter-actions">
                                        <button type="button" class="select-all-btn" 
                                                onclick="selectAll('entity-types')">Выбрать все</button>
                                        <button type="button" class="clear-selection-btn" 
                                                onclick="clearSelection('entity-types')">Очистить</button>
                                    </div>
                                </div>
                            </div>
                            <select class="filter-select" id="filter-entity-types" multiple style="display: none;"></select>
                        </div>

                        <div class="compact-filter-group">
                            <label class="filter-label">Каналы</label>
                            <div class="compact-filter" id="compact-filter-channels">
                                <div class="compact-filter-header" onclick="toggleCompactFilter('channels')">
                                    <div class="compact-filter-title">
                                        <span class="filter-name">Все каналы</span>
                                        <span class="filter-counter" id="counter-channels"></span>
                                    </div>
                                    <span class="compact-filter-arrow">▼</span>
                                </div>
                                <div class="compact-filter-content" id="content-channels">
                                    <div class="filter-search">
                                        <input type="text" class="filter-search-input" 
                                               placeholder="Искать каналы..." 
                                               onkeyup="filterOptionsFunc('channels', this.value)">
                                    </div>
                                    <div class="filter-checkboxes" id="checkboxes-channels"></div>
                                    <div class="filter-actions">
                                        <button type="button" class="select-all-btn" 
                                                onclick="selectAll('channels')">Выбрать все</button>
                                        <button type="button" class="clear-selection-btn" 
                                                onclick="clearSelection('channels')">Очистить</button>
                                    </div>
                                </div>
                            </div>
                            <select class="filter-select" id="filter-channels" multiple style="display: none;"></select>
                        </div>

                        <div class="compact-filter-group">
                            <label class="filter-label">Чаты</label>
                            <div class="compact-filter" id="compact-filter-chats">
                                <div class="compact-filter-header" onclick="toggleCompactFilter('chats')">
                                    <div class="compact-filter-title">
                                        <span class="filter-name">Все чаты</span>
                                        <span class="filter-counter" id="counter-chats"></span>
                                    </div>
                                    <span class="compact-filter-arrow">▼</span>
                                </div>
                                <div class="compact-filter-content" id="content-chats">
                                    <div class="filter-search">
                                        <input type="text" class="filter-search-input" 
                                               placeholder="Искать чаты..." 
                                               onkeyup="filterOptionsFunc('chats', this.value)">
                                    </div>
                                    <div class="filter-checkboxes" id="checkboxes-chats"></div>
                                    <div class="filter-actions">
                                        <button type="button" class="select-all-btn" 
                                                onclick="selectAll('chats')">Выбрать все</button>
                                        <button type="button" class="clear-selection-btn" 
                                                onclick="clearSelection('chats')">Очистить</button>
                                    </div>
                                </div>
                            </div>
                            <select class="filter-select" id="filter-chats" multiple style="display: none;"></select>
                        </div>

                        <div class="compact-filter-group">
                            <label class="filter-label">Статусы</label>
                            <div class="compact-filter" id="compact-filter-status">
                                <div class="compact-filter-header" onclick="toggleCompactFilter('status')">
                                    <div class="compact-filter-title">
                                        <span class="filter-name">Все статусы</span>
                                        <span class="filter-counter" id="counter-status"></span>
                                    </div>
                                    <span class="compact-filter-arrow">▼</span>
                                </div>
                                <div class="compact-filter-content" id="content-status">
                                    <div class="filter-search">
                                        <input type="text" class="filter-search-input" 
                                               placeholder="Искать статусы..." 
                                               onkeyup="filterOptionsFunc('status', this.value)">
                                    </div>
                                    <div class="filter-checkboxes" id="checkboxes-status"></div>
                                    <div class="filter-actions">
                                        <button type="button" class="select-all-btn" 
                                                onclick="selectAll('status')">Выбрать все</button>
                                        <button type="button" class="clear-selection-btn" 
                                                onclick="clearSelection('status')">Очистить</button>
                                    </div>
                                </div>
                            </div>
                            <select class="filter-select" id="filter-status" multiple style="display: none;"></select>
                        </div>
                    </div>

                    <div class="filter-row">
                        <div class="regular-filter-group">
                            <label class="filter-label">Автор</label>
                            <select class="filter-select" id="filter-author">
                                <option value="">Все авторы</option>
                            </select>
                        </div>

                        <div class="regular-filter-group">
                            <label class="filter-label">Исполнитель</label>
                            <select class="filter-select" id="filter-executor">
                                <option value="">Все исполнители</option>
                            </select>
                        </div>

                        <div class="regular-filter-group">
                            <label class="filter-label">QA</label>
                            <select class="filter-select" id="filter-qa">
                                <option value="">Все QA</option>
                            </select>
                        </div>
                    </div>

                    <div class="filter-row">
                        <div class="date-filter-group">
                            <label class="filter-label">Период планового закрытия</label>
                            <div class="date-input-group">
                                <input type="datetime-local" class="date-input" id="filter-deadline-from">
                                <input type="datetime-local" class="date-input" id="filter-deadline-to">
                            </div>
                        </div>

                        <div class="date-filter-group">
                            <label class="filter-label">Период создания</label>
                            <div class="date-input-group">
                                <input type="datetime-local" class="date-input" id="filter-created-from">
                                <input type="datetime-local" class="date-input" id="filter-created-to">
                            </div>
                        </div>
                    </div>

                    <div class="filters-actions">
                        <button class="clear-filters-btn" id="clear-filters">Сбросить все фильтры</button>
                        <button class="apply-filters-btn" id="apply-filters">Применить</button>
                    </div>
                </div>

                <div class="tab-content active" id="my-tasks-content">
                    <div class="loading" id="my-tasks-loading">Loading tasks...</div>
                    <div class="entity-list" id="my-tasks-list" style="display: none;"></div>
                </div>

                <div class="tab-content" id="outdated-content">
                    <div class="loading" id="outdated-loading">Loading outdated tasks...</div>
                    <div class="entity-list" id="outdated-list" style="display: none;"></div>
                </div>

                <div class="tab-content" id="all-filters-content">
                    <div class="empty-state" id="all-filters-empty">
                        <div class="empty-state-icon">🔍</div>
                        <h3>Фильтры не выбраны</h3>
                        <p>Укажите фильтры для поиска сущностей</p>
                    </div>
                    <div class="loading" id="all-filters-loading" style="display: none;">Применение фильтров...</div>
                    <div class="entity-list" id="all-filters-list" style="display: none;"></div>
                </div>
            </div>
        </div>

        <script>
            const userData = {{
                userId: {user_id_escaped},
                userDisplayName: {user_display_name_escaped}
            }};

            let currentTab = 'my-tasks';
            let filterOptions = null;
            let currentOpenFilter = null;
            let filterOptionsData = null;

            document.addEventListener('DOMContentLoaded', async () => {{
                try {{
                    await loadNavigation();
                    setupNavigationEvents();
                    setupTabs();
                    await loadFilterOptions();
                    setupFilters();
                    loadMyTasks();

                    document.addEventListener('click', (event) => {{
                        const isClickInside = event.target.closest('.compact-filter');
                        if (!isClickInside && currentOpenFilter) {{
                            toggleCompactFilter(currentOpenFilter);
                        }}
                    }});
                }} catch (error) {{
                    console.error('Error initializing page:', error);
                }}
            }});

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
                    html += '<div id="notes-btn" class="nav-item">Заметки</div>';
                    html += '<div id="task-explorer" class="nav-item active">Обозреватель задач</div>';

                    html += '<div id="chats-toggle" class="nav-item">Чаты ▼</div>';
                    html += '<div id="chats-submenu" class="submenu">';

                    for (const dept in chats) {{
                        html += '<div style="margin-top: 5px;">';
                        html += '<div style="font-weight: bold; margin: 8px 0 4px 0;">' + dept + '</div>';

                        chats[dept].forEach(chat => {{
                            html += '<div class="submenu-item" onclick="openDirectChat(\\'' + chat.chat_id + '\\')">' +
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
                            html += '<div class="submenu-item" onclick="openChannel(\\'' + channel.id + '\\')">' +
                                    channel.name + '</div>';
                        }});

                        html += '</div>';
                    }}

                    html += '</div>';

                    html += '<div id="logout-btn" class="nav-item" onclick="logout()">Выйти</div>';

                    sidebar.innerHTML = html;

                }} catch (error) {{
                    console.error('Error loading navigation:', error);
                }}
            }}

            function setupNavigationEvents() {{
                const chatsToggle = document.getElementById('chats-toggle');
                const chatsSubmenu = document.getElementById('chats-submenu');
                if (chatsToggle && chatsSubmenu) {{
                    chatsToggle.addEventListener('click', () => {{
                        if (chatsSubmenu) {{
                            chatsSubmenu.style.display = chatsSubmenu.style.display === 'none' ? 'flex' : 'none';
                        }}
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
            
            function openDirectChat(chatId) {{
                window.location.href = '/chat/' + chatId + '/';
            }}

            function openChannel(channelId) {{
                window.location.href = '/channel/' + channelId + '/';
            }}

            function logout() {{
                // Создаем невидимую форму
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '/logout';
                                
                document.body.appendChild(form);
                form.submit();
            }}

            function setupTabs() {{
                const tabs = document.querySelectorAll('.tab');
                const tabContents = document.querySelectorAll('.tab-content');
                const filtersPanel = document.getElementById('filters-panel');

                tabs.forEach(tab => {{
                    tab.addEventListener('click', () => {{
                        const tabId = tab.dataset.tab;

                        tabs.forEach(t => t.classList.remove('active'));
                        tab.classList.add('active');

                        if (tabId === 'all-filters') {{
                            filtersPanel.classList.add('active');
                        }} else {{
                            filtersPanel.classList.remove('active');
                        }}

                        tabContents.forEach(content => {{
                            content.classList.remove('active');
                        }});

                        document.getElementById(tabId + '-content').classList.add('active');

                        currentTab = tabId;
                        if (tabId === 'my-tasks') {{
                            loadMyTasks();
                        }} else if (tabId === 'outdated') {{
                            loadOutdatedTasks();
                        }} else if (tabId === 'all-filters') {{
                            const hasFilters = checkIfFiltersApplied();
                            if (!hasFilters) {{
                                showEmptyState('all-filters');
                            }}
                        }}
                    }});
                }});
            }}

            function toggleCompactFilter(filterType) {{
                const header = document.getElementById(`compact-filter-${{filterType}}`).querySelector('.compact-filter-header');
                const content = document.getElementById(`content-${{filterType}}`);
                const arrow = header.querySelector('.compact-filter-arrow');

                if (currentOpenFilter && currentOpenFilter !== filterType) {{
                    const prevHeader = document.getElementById(`compact-filter-${{currentOpenFilter}}`).querySelector('.compact-filter-header');
                    const prevContent = document.getElementById(`content-${{currentOpenFilter}}`);
                    const prevArrow = prevHeader.querySelector('.compact-filter-arrow');

                    prevHeader.classList.remove('active');
                    prevContent.classList.remove('active');
                    prevArrow.style.transform = 'rotate(0deg)';
                }}

                const isOpening = !header.classList.contains('active');
                header.classList.toggle('active', isOpening);
                content.classList.toggle('active', isOpening);
                arrow.style.transform = isOpening ? 'rotate(180deg)' : 'rotate(0deg)';

                currentOpenFilter = isOpening ? filterType : null;

                if (isOpening) {{
                    setTimeout(() => {{
                        const searchInput = content.querySelector('.filter-search-input');
                        if (searchInput) {{
                            searchInput.focus();
                        }}
                    }}, 100);
                }}
            }}

            function updateFilterCounter(filterType) {{
                const checkboxes = document.querySelectorAll(`#checkboxes-${{filterType}} input[type="checkbox"]`);
                const counter = document.getElementById(`counter-${{filterType}}`);
                const header = document.getElementById(`compact-filter-${{filterType}}`).querySelector('.compact-filter-header');
                const filterName = header.querySelector('.filter-name');

                const selectedCount = Array.from(checkboxes).filter(cb => cb.checked).length;

                if (selectedCount > 0) {{
                    counter.textContent = selectedCount;
                    counter.classList.add('show');

                    if (selectedCount === 1) {{
                        const selectedCheckbox = Array.from(checkboxes).find(cb => cb.checked);
                        if (selectedCheckbox) {{
                            const label = selectedCheckbox.nextElementSibling;
                            if (label) {{
                                filterName.textContent = label.textContent;
                            }}
                        }}
                    }} else {{
                        filterName.textContent = getFilterDefaultName(filterType);
                    }}
                }} else {{
                    counter.classList.remove('show');
                    filterName.textContent = getFilterDefaultName(filterType);
                }}

                updateHiddenSelect(filterType);
            }}

            function getFilterDefaultName(filterType) {{
                const names = {{
                    'entity-types': 'Все типы сущностей',
                    'channels': 'Все каналы',
                    'chats': 'Все чаты',
                    'status': 'Все статусы'
                }};
                return names[filterType] || `Все ${{filterType.replace('-', ' ')}}`;
            }}

            function updateHiddenSelect(filterType) {{
                const hiddenSelect = document.getElementById(`filter-${{filterType}}`);
                const checkboxes = document.querySelectorAll(`#checkboxes-${{filterType}} input[type="checkbox"]`);

                Array.from(hiddenSelect.options).forEach(option => {{
                    option.selected = false;
                }});

                checkboxes.forEach(checkbox => {{
                    if (checkbox.checked) {{
                        const option = Array.from(hiddenSelect.options).find(opt => opt.value === checkbox.value);
                        if (option) {{
                            option.selected = true;
                        }}
                    }}
                }});
            }}

            function filterOptionsFunc(filterType, searchText) {{
                const checkboxesContainer = document.getElementById(`checkboxes-${{filterType}}`);
                const checkboxes = checkboxesContainer.querySelectorAll('.filter-checkbox-item');
                const searchLower = searchText.toLowerCase();

                checkboxes.forEach(item => {{
                    const label = item.querySelector('.filter-checkbox-label');
                    const text = label.textContent.toLowerCase();
                    item.style.display = text.includes(searchLower) ? 'flex' : 'none';
                }});
            }}

            function selectAll(filterType) {{
                const checkboxes = document.querySelectorAll(`#checkboxes-${{filterType}} input[type="checkbox"]`);
                checkboxes.forEach(cb => cb.checked = true);
                updateFilterCounter(filterType);
            }}

            function clearSelection(filterType) {{
                const checkboxes = document.querySelectorAll(`#checkboxes-${{filterType}} input[type="checkbox"]`);
                checkboxes.forEach(cb => cb.checked = false);
                updateFilterCounter(filterType);
            }}

            async function loadFilterOptions() {{
                try {{
                    const response = await fetch('/api/task-explorer/filter-options');
                    if (!response.ok) throw new Error('Failed to load filter options');

                    filterOptionsData = await response.json();

                    if (filterOptionsData && filterOptionsData.statuses) {{
                        const seenStatusIds = new Set();
                        const uniqueStatuses = [];

                        filterOptionsData.statuses.forEach(status => {{
                            if (!seenStatusIds.has(status.id)) {{
                                seenStatusIds.add(status.id);
                                uniqueStatuses.push(status);
                            }}
                        }});

                        filterOptionsData.statuses = uniqueStatuses;
                    }}

                    populateFilterOptions();
                }} catch (error) {{
                    console.error('Error loading filter options:', error);
                }}
            }}

            function populateFilterOptions() {{
                if (!filterOptionsData) return;

                const filterTypes = ['entity-types', 'channels', 'chats', 'status'];
                filterTypes.forEach(type => {{
                    const container = document.getElementById(`checkboxes-${{type}}`);
                    const select = document.getElementById(`filter-${{type}}`);
                    if (container) container.innerHTML = '';
                    if (select) select.innerHTML = '';
                }});
                
                const select1 = document.getElementById(`filter-author`);
                if (select1) select1.innerHTML = '<option value="">Все авторы</option>';
                
                const select2 = document.getElementById(`filter-executor`);
                if (select2) select2.innerHTML = '<option value="">Все исполнители</option>';

                const entityTypesContainer = document.getElementById('checkboxes-entity-types');
                const entityTypesSelect = document.getElementById('filter-entity-types');

                const uniqueEntityTypes = [];
                const seenEntityTypes = new Set();

                filterOptionsData.entity_types.forEach(type => {{
                    if (!seenEntityTypes.has(type.id)) {{
                        seenEntityTypes.add(type.id);
                        uniqueEntityTypes.push(type);
                    }}
                }});

                uniqueEntityTypes.forEach(type => {{
                    const checkboxItem = document.createElement('div');
                    checkboxItem.className = 'filter-checkbox-item';
                    checkboxItem.innerHTML = `
                        <input type="checkbox" id="entity-type-${{type.id}}" value="${{type.id}}">
                        <label for="entity-type-${{type.id}}" class="filter-checkbox-label">${{type.name}}</label>
                    `;
                    entityTypesContainer.appendChild(checkboxItem);

                    const option = document.createElement('option');
                    option.value = type.id;
                    option.textContent = type.name;
                    entityTypesSelect.appendChild(option);
                }});

                const channelsContainer = document.getElementById('checkboxes-channels');
                const channelsSelect = document.getElementById('filter-channels');
                for (const [group, channels] of Object.entries(filterOptionsData.channels)) {{
                    channels.forEach(channel => {{
                        const displayName = `${{channel.name}} (${{group}})`;

                        const checkboxItem = document.createElement('div');
                        checkboxItem.className = 'filter-checkbox-item';
                        checkboxItem.innerHTML = `
                            <input type="checkbox" id="channel-${{channel.id}}" value="${{channel.id}}">
                            <label for="channel-${{channel.id}}" class="filter-checkbox-label">${{displayName}}</label>
                        `;
                        channelsContainer.appendChild(checkboxItem);

                        const option = document.createElement('option');
                        option.value = channel.id;
                        option.textContent = displayName;
                        channelsSelect.appendChild(option);
                    }});
                }}

                const chatsContainer = document.getElementById('checkboxes-chats');
                const chatsSelect = document.getElementById('filter-chats');
                for (const [dept, chats] of Object.entries(filterOptionsData.chats)) {{
                    chats.forEach(chat => {{
                        const displayName = `${{chat.display_name}} (${{dept}})`;

                        const checkboxItem = document.createElement('div');
                        checkboxItem.className = 'filter-checkbox-item';
                        checkboxItem.innerHTML = `
                            <input type="checkbox" id="chat-${{chat.chat_id}}" value="${{chat.chat_id}}">
                            <label for="chat-${{chat.chat_id}}" class="filter-checkbox-label">${{displayName}}</label>
                        `;
                        chatsContainer.appendChild(checkboxItem);

                        const option = document.createElement('option');
                        option.value = chat.chat_id;
                        option.textContent = displayName;
                        chatsSelect.appendChild(option);
                    }});
                }}

                const statusContainer = document.getElementById('checkboxes-status');
                const statusSelect = document.getElementById('filter-status');

                const sortedStatuses = [...filterOptionsData.statuses].sort((a, b) => 
                    a.name.localeCompare(b.name)
                );

                sortedStatuses.forEach(status => {{
                    const checkboxItem = document.createElement('div');
                    checkboxItem.className = 'filter-checkbox-item';
                    checkboxItem.innerHTML = `
                        <input type="checkbox" id="status-${{status.id}}" value="${{status.id}}">
                        <label for="status-${{status.id}}" class="filter-checkbox-label">${{status.name}}</label>
                    `;
                    statusContainer.appendChild(checkboxItem);

                    const option = document.createElement('option');
                    option.value = status.id;
                    option.textContent = status.name;
                    statusSelect.appendChild(option);
                }});

                setupCheckboxEvents();

                const authorSelect = document.getElementById('filter-author');
                const executorSelect = document.getElementById('filter-executor');
                const qaSelect = document.getElementById('filter-qa');

                [authorSelect, executorSelect, qaSelect].forEach(select => {{
                    const meOption = document.createElement('option');
                    meOption.value = filterOptionsData.current_user_id;
                    meOption.textContent = 'Me (' + userData.userDisplayName + ')';
                    select.appendChild(meOption);
                }});

                for (const [dept, users] of Object.entries(filterOptionsData.users)) {{
                    const optgroup = document.createElement('optgroup');
                    optgroup.label = dept.toUpperCase();

                    users.forEach(user => {{
                        if (user.id !== filterOptionsData.current_user_id) {{
                            const option = document.createElement('option');
                            option.value = user.id;
                            option.textContent = user.display_name;
                            optgroup.appendChild(option);
                        }}
                    }});

                    if (optgroup.children.length > 0) {{
                        authorSelect.appendChild(optgroup.cloneNode(true));
                        executorSelect.appendChild(optgroup.cloneNode(true));
                        qaSelect.appendChild(optgroup.cloneNode(true));
                    }}
                }}
            }}

            function setupCheckboxEvents() {{
                const filterTypes = ['entity-types', 'channels', 'chats', 'status'];

                filterTypes.forEach(filterType => {{
                    const checkboxes = document.querySelectorAll(`#checkboxes-${{filterType}} input[type="checkbox"]`);
                    checkboxes.forEach(checkbox => {{
                        checkbox.addEventListener('change', () => {{
                            updateFilterCounter(filterType);
                        }});
                    }});
                }});
            }}

            function setupFilters() {{
                const applyBtn = document.getElementById('apply-filters');
                const clearBtn = document.getElementById('clear-filters');

                applyBtn.addEventListener('click', applyFilters);
                clearBtn.addEventListener('click', clearFilters);
            }}

            function checkIfFiltersApplied() {{
                const entityTypes = getSelectedValues('filter-entity-types');
                const channels = getSelectedValues('filter-channels');
                const chats = getSelectedValues('filter-chats');
                const author = document.getElementById('filter-author').value;
                const statuses = getSelectedValues('filter-status');
                const executor = document.getElementById('filter-executor').value;
                const qa = document.getElementById('filter-qa').value;
                const deadlineFrom = document.getElementById('filter-deadline-from').value;
                const deadlineTo = document.getElementById('filter-deadline-to').value;
                const createdFrom = document.getElementById('filter-created-from').value;
                const createdTo = document.getElementById('filter-created-to').value;

                return entityTypes.length > 0 || 
                       channels.length > 0 || 
                       chats.length > 0 || 
                       author || 
                       statuses.length > 0 || 
                       executor || 
                       qa || 
                       deadlineFrom || 
                       deadlineTo || 
                       createdFrom || 
                       createdTo;
            }}

            function getSelectedValues(selectId) {{
                const select = document.getElementById(selectId);
                return Array.from(select.selectedOptions).map(option => option.value);
            }}

            async function applyFilters() {{
                if (currentTab !== 'all-filters') return;

                showLoading('all-filters');

                const params = new URLSearchParams();

                const entityTypes = getSelectedValues('filter-entity-types');
                const channels = getSelectedValues('filter-channels');
                const chats = getSelectedValues('filter-chats');
                const author = document.getElementById('filter-author').value;
                const statuses = getSelectedValues('filter-status');
                const executor = document.getElementById('filter-executor').value;
                const qa = document.getElementById('filter-qa').value;
                const deadlineFrom = document.getElementById('filter-deadline-from').value;
                const deadlineTo = document.getElementById('filter-deadline-to').value;
                const createdFrom = document.getElementById('filter-created-from').value;
                const createdTo = document.getElementById('filter-created-to').value;

                if (entityTypes.length > 0) {{
                    entityTypes.forEach(type => params.append('entity_types', type));
                }}

                if (channels.length > 0) {{
                    channels.forEach(channel => params.append('channel_ids', channel));
                }}

                if (chats.length > 0) {{
                    chats.forEach(chat => params.append('chat_ids', chat));
                }}

                if (author) params.append('author_id', author);

                if (statuses.length > 0) {{
                    statuses.forEach(status => params.append('statuses', status));
                }}

                if (executor) params.append('executor_id', executor);
                if (qa) params.append('qa_id', qa);
                if (deadlineFrom) params.append('deadline_from', deadlineFrom + ':00');
                if (deadlineTo) params.append('deadline_to', deadlineTo + ':00');
                if (createdFrom) params.append('created_from', createdFrom + ':00');
                if (createdTo) params.append('created_to', createdTo + ':00');

                try {{
                    const response = await fetch('/api/entities-filter?' + params.toString());
                    if (!response.ok) throw new Error('Failed to apply filters');

                    const data = await response.json();
                    displayEntities('all-filters', data.entities);
                }} catch (error) {{
                    console.error('Error applying filters:', error);
                    showEmptyState('all-filters', 'Error loading tasks');
                }}
            }}

            function clearFilters() {{
                const filterTypes = ['entity-types', 'channels', 'chats', 'status'];
                filterTypes.forEach(filterType => {{
                    clearSelection(filterType);
                }});

                ['author', 'executor', 'qa'].forEach(filter => {{
                    const select = document.getElementById(`filter-${{filter}}`);
                    if (select) select.value = '';
                }});

                document.querySelectorAll('.date-input').forEach(input => {{
                    input.value = '';
                }});

                if (currentTab === 'all-filters') {{
                    showEmptyState('all-filters');
                }}
            }}

            async function loadMyTasks() {{
                if (currentTab !== 'my-tasks') return;

                showLoading('my-tasks');

                try {{
                    const response = await fetch('/api/entities-filter?tab=my_tasks');
                    if (!response.ok) throw new Error('Failed to load tasks');

                    const data = await response.json();
                    displayEntities('my-tasks', data.entities);
                }} catch (error) {{
                    console.error('Error loading tasks:', error);
                    showEmptyState('my-tasks', 'Error loading tasks');
                }}
            }}

            async function loadOutdatedTasks() {{
                if (currentTab !== 'outdated') return;

                showLoading('outdated');

                try {{
                    const response = await fetch('/api/entities-filter?tab=outdated');
                    if (!response.ok) throw new Error('Failed to load outdated tasks');

                    const data = await response.json();
                    displayEntities('outdated', data.entities);
                }} catch (error) {{
                    console.error('Error loading outdated tasks:', error);
                    showEmptyState('outdated', 'Error loading tasks');
                }}
            }}

            function showLoading(tabId) {{
                document.getElementById(tabId + '-loading').style.display = 'block';
                document.getElementById(tabId + '-list').style.display = 'none';

                const emptyElement = document.getElementById(tabId + '-empty');
                if (emptyElement) {{
                    emptyElement.style.display = 'none';
                }}
            }}

            function showEmptyState(tabId, message = null) {{
                const emptyState = document.getElementById(tabId + '-empty');
                const loading = document.getElementById(tabId + '-loading');
                const list = document.getElementById(tabId + '-list');

                if (emptyState) {{
                    if (message) {{
                        const heading = emptyState.querySelector('h3');
                        if (heading) {{
                            heading.textContent = message;
                        }}
                    }}
                    emptyState.style.display = 'block';
                }}

                if (loading) {{
                    loading.style.display = 'none';
                }}

                if (list) {{
                    list.style.display = 'none';
                }}
            }}

            function displayEntities(tabId, entities) {{
                const container = document.getElementById(tabId + '-list');
                const loading = document.getElementById(tabId + '-loading');
                const emptyState = document.getElementById(tabId + '-empty');

                if (entities.length === 0) {{
                    if (emptyState) {{
                        if (tabId === 'my-tasks') {{
                            emptyState.innerHTML = '<div class="empty-state-icon">✅</div><h3>Незакрытые сущности не найдены</h3><p>На вас нет назначенных активных сущностей</p>';
                        }} else if (tabId === 'outdated') {{
                            emptyState.innerHTML = '<div class="empty-state-icon">⏰</div><h3>Просроченные сущности не найдены</h3><p>Отлично! Ничто не просрочено</p>';
                        }} else {{
                            emptyState.innerHTML = '<div class="empty-state-icon">🔍</div><h3>Сущности не найдены</h3><p>Попробуйте изменить условия поиска</p>';
                        }}
                        emptyState.style.display = 'block';
                    }}

                    if (container) {{
                        container.style.display = 'none';
                    }}

                    if (loading) {{
                        loading.style.display = 'none';
                    }}
                    return;
                }}

                let html = '';

                entities.forEach(entity => {{
                    const isOutdated = tabId === 'outdated' || 
                        (entity.deadline && new Date(entity.deadline) < new Date());

                    html += '<div class="entity-card' + (isOutdated ? ' outdated' : '') + '">';
                    html += '<div class="entity-header">';
                    html += '<h3 class="entity-title">' + escapeHtml(entity.title) + '</h3>';
                    html += '<span class="entity-type">' + entity.type + '</span>';
                    html += '</div>';

                    html += '<div class="entity-meta">';
                    html += '<div class="entity-meta-item">';
                    html += '<strong>Автор:</strong> ' + escapeHtml(entity.author_display_name);
                    html += '</div>';

                    if (entity.source_name) {{
                        html += '<div class="entity-meta-item">';
                        html += '<strong>Откуда:</strong> ';
                        html += '<span class="entity-source">' + escapeHtml(entity.source_name) + '</span>';
                        html += '</div>';
                    }}

                    html += '<div class="entity-meta-item">';
                    html += '<strong>Создано:</strong> ' + new Date(entity.created_at).toLocaleString();
                    html += '</div>';

                    if (entity.deadline) {{
                        html += '<div class="entity-meta-item">';
                        html += '<strong>Закрыть до:</strong> ' + new Date(entity.deadline).toLocaleString();
                        html += '</div>';
                    }}
                    html += '</div>';

                    if (entity.body) {{
                        html += '<div class="entity-body">' + escapeHtml(entity.body) + '</div>';
                    }}

                    html += '<div class="entity-details">';

                    if (entity.priority !== undefined) {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">Приоритет:</span>';
                        html += '<span class="entity-priority">' + entity.priority + '</span>';
                        html += '</div>';
                    }}

                    if (entity.severity !== undefined) {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">Приоритет:</span>';
                        html += '<span class="entity-severity">' + entity.severity + '</span>';
                        html += '</div>';
                    }}

                    if (entity.status) {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">Статус:</span>';
                        html += '<span class="entity-status status-' + entity.status + '">';
                        html += entity.status.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                        html += '</span>';
                        html += '</div>';
                    }}

                    if (entity.executor) {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">Исполнитель:</span>';
                        html += escapeHtml(entity.executor);
                        html += '</div>';
                    }}

                    if (entity.qa) {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">QA:</span>';
                        html += escapeHtml(entity.qa);
                        html += '</div>';
                    }}

                    if (entity.reproducible !== undefined) {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">Воспроизводится ли:</span>';
                        html += entity.reproducible ? 'Да' : 'Нет';
                        html += '</div>';
                    }}

                    if (entity.type === 'info') {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">Прочитано:</span>';
                        html += (entity.read_count || 0) + '/' + (entity.required_users.length || 0);
                        html += '</div>';
                    }}

                    html += '</div>';

                    html += '<div class="entity-actions">';

                    if (entity.source_type === 'channel') {{
                        html += '<button class="entity-action-btn" onclick="openChannel(\\'' + entity.source_id + '\\')">';
                        html += 'Перейти в канал';
                        html += '</button>';
                    }} else if (entity.source_type === 'chat') {{
                        html += '<button class="entity-action-btn" onclick="openDirectChat(\\'' + entity.source_id + '\\')">';
                        html += 'Перейти в чат';
                        html += '</button>';
                    }}

                    html += '</div>';

                    html += '</div>';
                }});

                container.innerHTML = html;
                container.style.display = 'block';
                loading.style.display = 'none';
                if (emptyState) emptyState.style.display = 'none';
            }}

            function escapeHtml(text) {{
                if (!text) return '';
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }}
        </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)
