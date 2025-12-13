import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import and_, or_, select, func, case
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
    ProposalEntity,
    QuestionEntity,
    TaskEntity,
    Topic,
    User,
    DepartmentEnum,
    QuestionStatus,
    DefectStatus,
    TaskStatus,
    ProposalStatus,
    ActionPointStatus,
)


def parse_datetime(dt_str: str) -> datetime:
    """Парсит строку datetime в объект datetime."""
    try:
        # Пробуем стандартный ISO формат
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    except ValueError:
        # Если не сработало, пробуем удалить микросекунды или обработать другие форматы
        # Удаляем микросекунды если есть
        if '.' in dt_str:
            dt_str = dt_str.split('.')[0]
        # Добавляем Z если нужно
        if dt_str.endswith('Z'):
            dt_str = dt_str[:-1] + '+00:00'
        # Если нет информации о временной зоне, добавляем UTC
        if not re.search(r'[+-]\d{2}:?\d{2}$', dt_str):
            dt_str += '+00:00'
        return datetime.fromisoformat(dt_str)


@router.get('/task-explorer/', response_class=HTMLResponse)
async def task_explorer_page(
        user_login=Depends(require_auth),
        session: AsyncSession = Depends(get_session)
):
    """Страница Task Explorer с тремя вкладками"""
    # Получаем текущего пользователя
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    # Генерируем HTML страницу
    return generate_task_explorer_html(user)


@router.get('/api/entities-filter', response_class=JSONResponse)
async def filter_entities(
        # Фильтры
        entity_types: Optional[List[str]] = Query(None, description="Типы entity (мультиселект)"),
        channel_ids: Optional[List[str]] = Query(None, description="ID каналов (мультиселект)"),
        chat_ids: Optional[List[str]] = Query(None, description="ID чатов (мультиселект)"),
        author_id: Optional[str] = Query(None, description="ID автора (одиночный выбор)"),
        statuses: Optional[List[str]] = Query(None, description="Статусы (мультиселект)"),
        deadline_from: Optional[datetime] = Query(None, description="Дедлайн от"),
        deadline_to: Optional[datetime] = Query(None, description="Дедлайн до"),
        executor_id: Optional[str] = Query(None, description="ID исполнителя (одиночный выбор)"),
        qa_id: Optional[str] = Query(None, description="ID QA (одиночный выбор)"),
        created_from: Optional[datetime] = Query(None, description="Создано от"),
        created_to: Optional[datetime] = Query(None, description="Создано до"),

        # Параметры для специальных вкладок
        tab: Optional[str] = Query(None, description="Вкладка: my_tasks, outdated, all_filters"),

        user_login=Depends(require_auth),
        session: AsyncSession = Depends(get_session)
):
    """Фильтрация entity по заданным критериям"""
    # Получаем текущего пользователя
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    # Обработка специальных вкладок
    if tab == "my_tasks":
        return await get_my_tasks(user, session)
    elif tab == "outdated":
        return await get_outdated_tasks(user, session)

    # Базовая фильтрация для вкладки "All filters"
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

    return {"entities": entities}


async def filter_entities_query(
        user: User,
        session: AsyncSession,
        entity_types: Optional[List[str]] = None,
        channel_ids: Optional[List[str]] = None,
        chat_ids: Optional[List[str]] = None,
        author_id: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        deadline_from: Optional[datetime] = None,
        deadline_to: Optional[datetime] = None,
        executor_id: Optional[str] = None,
        qa_id: Optional[str] = None,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Основной запрос фильтрации entity"""

    # Базовый запрос с загрузкой всех связанных данных
    query = (
        select(Entity)
        .options(
            selectinload(Entity.author),
            selectinload(Entity.channel),
            selectinload(Entity.direct_chat),
            selectinload(Entity.comments),
            selectinload(Entity.question),
            selectinload(Entity.defect).options(
                joinedload(DefectEntity.executor),
                joinedload(DefectEntity.qa)
            ),
            selectinload(Entity.task).options(
                joinedload(TaskEntity.executor),
                joinedload(TaskEntity.qa)
            ),
            selectinload(Entity.info).selectinload(
                InfoEntity.required_users
            ).selectinload(InfoRequiredUser.user),
            selectinload(Entity.proposal),
            selectinload(Entity.action_point).options(
                joinedload(ActionPointEntity.executor)
            ),
        )
    )

    # Фильтр по доступности для пользователя
    # Пользователь должен иметь доступ либо к каналу, либо к чату
    channel_conditions = []
    chat_conditions = []

    if channel_ids:
        channel_conditions.append(Entity.channel_id.in_([UUID(cid) for cid in channel_ids]))
    else:
        # Все каналы пользователя
        user_channel_ids = [ch.id for ch in user.channels]
        if user_channel_ids:
            channel_conditions.append(Entity.channel_id.in_(user_channel_ids))

    if chat_ids:
        chat_conditions.append(Entity.direct_chat_id.in_([UUID(cid) for cid in chat_ids]))
    else:
        # Все чаты пользователя
        user_chat_ids = [chat.id for chat in user.direct_chats if not chat.is_self_chat]
        if user_chat_ids:
            chat_conditions.append(Entity.direct_chat_id.in_(user_chat_ids))

    # Объединяем условия доступа: (каналы ИЛИ чаты)
    access_conditions = []
    if channel_conditions:
        access_conditions.append(and_(*channel_conditions))
    if chat_conditions:
        access_conditions.append(and_(*chat_conditions))

    if access_conditions:
        query = query.where(or_(*access_conditions))
    else:
        # Если нет условий доступа, возвращаем пустой список
        return []

    # Фильтр по типам entity
    if entity_types:
        entity_type_enums = [EntityTypeEnum(et) for et in entity_types]
        query = query.where(Entity.type.in_(entity_type_enums))

    # Фильтр по автору
    if author_id:
        query = query.where(Entity.author_id == UUID(author_id))

    # Фильтр по дате создания
    if created_from:
        query = query.where(Entity.created_at >= created_from)
    if created_to:
        query = query.where(Entity.created_at <= created_to)

    # Выполняем запрос
    result = await session.execute(query)
    entities = result.scalars().all()

    # Собираем acknowledges для всех Info entity
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

    # Дополнительная фильтрация на уровне Python для сложных условий
    filtered_entities = []

    for entity in entities:
        # Фильтр по статусам (теперь передаем acknowledges_dict)
        if statuses and not check_entity_status(entity, statuses, user.id, acknowledges_dict):
            continue

        # Фильтр по дедлайну
        if not check_deadline(entity, deadline_from, deadline_to):
            continue

        # Фильтр по исполнителю - только если фильтр применен
        if executor_id:
            if not check_executor(entity, UUID(executor_id)):
                continue

        # Фильтр по QA - только если фильтр применен
        if qa_id:
            if not check_qa(entity, UUID(qa_id)):
                continue

        filtered_entities.append(entity)

    # Преобразуем entity в словари
    result_list = []
    for entity in filtered_entities:
        entity_dict = await entity_to_dict(entity, user.id, session, acknowledges_dict)
        result_list.append(entity_dict)

    # Сортировка
    result_list.sort(key=lambda x: (
        # 1. Сначала сущности с дедлайном (0), потом без (1)
        1 if x.get('deadline') is None else 0,
        # 2. Для сущностей с дедлайном - сортируем по самому дедлайну
        parse_datetime(x.get('deadline')) if x.get('deadline') else datetime.max.replace(tzinfo=timezone.utc),
        # 3. По priority (чем больше, тем выше)
        - (x.get('priority') or 0)
    ))

    return result_list


def check_entity_status(entity: Entity, statuses: List[str], user_id: UUID,
                        acknowledges_dict: Dict[str, Dict[str, Any]]) -> bool:
    """Проверка статуса entity с учетом специальных статусов для Info"""
    entity_status = None

    # Получаем статус в зависимости от типа entity
    if entity.type == EntityTypeEnum.question and entity.question:
        entity_status = entity.question.status.value
    elif entity.type == EntityTypeEnum.defect and entity.defect:
        entity_status = entity.defect.status.value
    elif entity.type == EntityTypeEnum.task and entity.task:
        entity_status = entity.task.status.value
    elif entity.type == EntityTypeEnum.info and entity.info:
        # Для Info проверяем статус прочтения текущим пользователем
        # Проверяем, есть ли пользователь в required_users
        is_required = False
        if entity.info.required_users:
            for ru in entity.info.required_users:
                if str(ru.user_id) == str(user_id):
                    is_required = True
                    break

        if is_required:
            # Проверяем, прочитал ли пользователь через acknowledges_dict
            entity_id_str = str(entity.id)
            user_id_str = str(user_id)
            if (entity_id_str in acknowledges_dict and
                    user_id_str in acknowledges_dict[entity_id_str] and
                    acknowledges_dict[entity_id_str][user_id_str]['acknowledged']):
                entity_status = "read"
            else:
                entity_status = "unread"
        else:
            # Если пользователь не в required_users, пропускаем фильтр по статусу
            # Но добавляем специальный статус для фильтрации
            entity_status = "not_required"
    elif entity.type == EntityTypeEnum.proposal and entity.proposal:
        entity_status = entity.proposal.status.value
    elif entity.type == EntityTypeEnum.action_point and entity.action_point:
        entity_status = entity.action_point.status.value

    # Проверяем соответствие статуса фильтру
    return entity_status in statuses if entity_status else False


def check_info_read(entity: Entity, user_id: UUID) -> bool:
    """Проверяет, прочитал ли пользователь Info entity"""
    if not entity.info:
        return False

    # Для Info entity нужно проверить наличие записи в InfoRequiredUser
    # и затем проверить Acknowledge
    is_required = False
    for ru in entity.info.required_users:
        if str(ru.user_id) == str(user_id):
            is_required = True
            break

    if not is_required:
        return False

    return False


def check_deadline(entity: Entity, deadline_from: Optional[datetime], deadline_to: Optional[datetime]) -> bool:
    """Проверка дедлайна entity"""
    entity_deadline = None

    # Получаем дедлайн в зависимости от типа entity
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

    # Если у entity нет дедлайна, пропускаем фильтр
    if entity_deadline is None:
        return True

    # Проверяем диапазон
    if deadline_from and entity_deadline < deadline_from:
        return False
    if deadline_to and entity_deadline > deadline_to:
        return False

    return True


def check_executor(entity: Entity, executor_id: UUID) -> bool:
    """Проверка исполнителя entity"""
    # Для типов Entity без поля исполнителя сразу возвращаем False
    if entity.type not in [EntityTypeEnum.defect, EntityTypeEnum.task, EntityTypeEnum.action_point]:
        return False

    # Проверяем конкретные типы
    if entity.type == EntityTypeEnum.defect and entity.defect:
        return str(entity.defect.executor_id) == str(executor_id)
    elif entity.type == EntityTypeEnum.task and entity.task:
        return str(entity.task.executor_id) == str(executor_id)
    elif entity.type == EntityTypeEnum.action_point and entity.action_point:
        return str(entity.action_point.executor_id) == str(executor_id)

    return False


def check_qa(entity: Entity, qa_id: UUID) -> bool:
    """Проверка QA entity"""
    # Для типов Entity без поля QA сразу возвращаем False
    if entity.type not in [EntityTypeEnum.defect, EntityTypeEnum.task]:
        return False

    # Проверяем конкретные типы
    if entity.type == EntityTypeEnum.defect and entity.defect:
        return str(entity.defect.qa_id) == str(qa_id)
    elif entity.type == EntityTypeEnum.task and entity.task:
        return str(entity.task.qa_id) == str(qa_id)

    return False


async def entity_to_dict(entity: Entity, user_id: UUID, session: AsyncSession,
                         acknowledges_dict: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Преобразование entity в словарь для отображения"""
    # Базовые данные entity
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

    # Определяем источник (чат или канал)
    if entity.channel:
        entity_data['source_type'] = 'channel'
        entity_data['source_id'] = str(entity.channel.id)
        entity_data['source_name'] = entity.channel.name
    elif entity.direct_chat:
        entity_data['source_type'] = 'chat'
        entity_data['source_id'] = str(entity.direct_chat.id)
        # Находим других пользователей в чате для отображения имени
        other_users = [u for u in entity.direct_chat.users if str(u.id) != str(entity.author_id)]
        if other_users:
            entity_data['source_name'] = other_users[0].display_name
        else:
            entity_data['source_name'] = 'Direct Chat'

    # Добавляем информацию о конкретном типе Entity
    if entity.type == EntityTypeEnum.question and entity.question:
        deadline = None
        if entity.question.deadline:
            if entity.question.deadline.tzinfo is None:
                deadline = entity.question.deadline.replace(tzinfo=timezone.utc).isoformat()
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
                deadline = entity.defect.deadline.replace(tzinfo=timezone.utc).isoformat()
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
                deadline = entity.task.deadline.replace(tzinfo=timezone.utc).isoformat()
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

                # Проверяем, есть ли подтверждение в acknowledges_dict
                entity_id_str = str(entity.id)
                user_id_str = str(ru.user.id)

                if (entity_id_str in acknowledges_dict and
                        user_id_str in acknowledges_dict[entity_id_str] and
                        acknowledges_dict[entity_id_str][user_id_str]['acknowledged']):
                    read_count += 1
                    if user_id_str == str(user_id):
                        has_read = True
                else:
                    all_acknowledged = False

        # Форматируем дедлайн с временной зоной
        deadline = None
        if entity.info.deadline:
            if entity.info.deadline.tzinfo is None:
                # Если нет временной зоны, добавляем UTC
                deadline = entity.info.deadline.replace(tzinfo=timezone.utc).isoformat()
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
                deadline = entity.action_point.deadline.replace(tzinfo=timezone.utc).isoformat()
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


async def get_my_tasks(user: User, session: AsyncSession) -> Dict[str, Any]:
    """Получение задач пользователя для вкладки My Tasks"""
    user_id = user.id

    # Определяем статусы, которые НЕ включаем
    excluded_statuses = [
        'closed', 'rejected', 'accepted', 'read'
    ]

    # Получаем все статусы
    all_statuses = await get_all_statuses(session)
    # Фильтруем исключенные статусы
    included_statuses = [status['id'] for status in all_statuses
                         if status['id'] not in excluded_statuses]

    # Загружаем acknowledges_dict отдельно для всех вызовов
    acknowledges_dict = {}

    # 1. Задачи, где пользователь - автор
    author_tasks = await filter_entities_query(
        user=user,
        session=session,
        author_id=str(user_id),
        statuses=included_statuses,
    )

    # 2. Задачи, где пользователь - исполнитель
    executor_tasks = await filter_entities_query(
        user=user,
        session=session,
        executor_id=str(user_id),
        statuses=included_statuses,
    )

    # 3. Задачи, где пользователь - QA
    qa_tasks = await filter_entities_query(
        user=user,
        session=session,
        qa_id=str(user_id),
        statuses=included_statuses,
    )

    # 4. Info entity, где пользователь - автор или в required_users
    # Сначала получаем все Info entity, доступные пользователю
    info_tasks = await filter_entities_query(
        user=user,
        session=session,
        entity_types=['info'],
    )

    # Фильтруем Info entity по новым правилам
    filtered_info_tasks = []
    for task in info_tasks:
        # Проверяем, является ли пользователь автором
        is_author = task.get('author_id') == str(user_id)

        # Проверяем, находится ли пользователь в required_users
        is_in_required_users = False
        if task.get('required_users'):
            is_in_required_users = any(
                ru['id'] == str(user_id) for ru in task['required_users']
            )

        if is_author:
            # Для автора: показываем только если не все required_users подтвердили
            if not task.get('all_acknowledged', True):
                filtered_info_tasks.append(task)
        elif is_in_required_users:
            # Для пользователя в required_users: показываем только если он не прочитал
            if not task.get('has_read', False):
                filtered_info_tasks.append(task)

    # Объединяем и убираем дубликаты
    all_tasks = {}

    # Добавляем обычные задачи (не info)
    for task in author_tasks + executor_tasks + qa_tasks:
        if task.get('type') != 'info':
            all_tasks[task['id']] = task

    # Добавляем отфильтрованные info задачи
    for task in filtered_info_tasks:
        all_tasks[task['id']] = task

    # Преобразуем в список
    result_list = list(all_tasks.values())

    # ДОБАВЛЯЕМ СОРТИРОВКУ
    result_list.sort(key=lambda x: (
        # 1. Сначала сущности с дедлайном (0), потом без (1)
        1 if x.get('deadline') is None else 0,
        # 2. Для сущностей с дедлайном - сортируем по самому дедлайну
        parse_datetime(x.get('deadline')) if x.get('deadline') else datetime.max.replace(tzinfo=timezone.utc),
        # 3. По priority (чем больше, тем выше)
        - (x.get('priority') or 0)
    ))

    return {"entities": result_list}


async def get_outdated_tasks(user: User, session: AsyncSession) -> Dict[str, Any]:
    """Получение просроченных задач"""
    current_time = datetime.now(timezone.utc)

    # Фильтруем задачи с дедлайном до текущего времени
    outdated_tasks = await filter_entities_query(
        user=user,
        session=session,
        deadline_to=current_time,
    )

    # Фильтруем только те, у которых статус не завершен
    active_statuses = [
        'created', 'in_progress', 'ready_for_test', 'in_testing',
        'discussed', 'answered', 'unread'
    ]

    filtered_tasks = []
    for task in outdated_tasks:
        if task.get('status') in active_statuses:
            filtered_tasks.append(task)

    # ДОБАВЛЯЕМ СОРТИРОВКУ
    filtered_tasks.sort(key=lambda x: (
        # 1. Сначала сущности с дедлайном (0), потом без (1) - но здесь все с дедлайном
        1 if x.get('deadline') is None else 0,
        # 2. Для сущностей с дедлайном - сортируем по самому дедлайну
        parse_datetime(x.get('deadline')) if x.get('deadline') else datetime.max.replace(tzinfo=timezone.utc),
        # 3. По priority (чем больше, тем выше)
        - (x.get('priority') or 0)
    ))

    return {"entities": filtered_tasks}


@router.get('/api/task-explorer/filter-options', response_class=JSONResponse)
async def get_filter_options(
        user_login=Depends(require_auth),
        session: AsyncSession = Depends(get_session)
):
    """Получение всех опций для фильтров"""
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    # Получаем все типы entity
    entity_types = await get_all_entity_types(session)

    # Получаем каналы пользователя
    channels_by_group = {}
    for ch in user.channels:
        if ch.group.value not in channels_by_group:
            channels_by_group[ch.group.value] = []
        channels_by_group[ch.group.value].append({
            'id': str(ch.id),
            'name': ch.name
        })

    # Получаем чаты пользователя
    chats_by_dept = {}
    user_chats_query = (
        select(DirectChat)
        .join(DirectChat.users)
        .where(DirectChat.is_self_chat == False, User.id == user.id)
    )
    chats_result = await session.execute(user_chats_query)
    chats = chats_result.scalars().all()

    for chat in chats:
        # Находим другого пользователя в чате
        other_user = next(u for u in chat.users if u.id != user.id)
        dept = other_user.department.value

        if dept not in chats_by_dept:
            chats_by_dept[dept] = []

        chats_by_dept[dept].append({
            'chat_id': str(chat.id),
            'display_name': other_user.display_name,
            'user_id': str(other_user.id),
        })

    # Получаем всех пользователей (сгруппированных по департаментам)
    users_by_dept = {}
    all_users_query = select(User).order_by(User.department, User.display_name)
    all_users_result = await session.execute(all_users_query)
    all_users = all_users_result.scalars().all()

    for u in all_users:
        dept = u.department.value
        if dept not in users_by_dept:
            users_by_dept[dept] = []

        users_by_dept[dept].append({
            'id': str(u.id),
            'display_name': u.display_name,
            'is_me': u.id == user.id
        })

    # Получаем все статусы
    statuses = await get_all_statuses(session)

    return {
        'entity_types': entity_types,
        'channels': channels_by_group,
        'chats': chats_by_dept,
        'users': users_by_dept,
        'statuses': statuses,
        'current_user_id': str(user.id)
    }


async def get_all_entity_types(session: AsyncSession) -> List[Dict[str, str]]:
    """Получение всех уникальных типов entity"""
    # Используем список для сохранения порядка и set для проверки уникальности
    unique_types = []
    seen_types = set()

    for entity_type in EntityTypeEnum:
        if entity_type.value not in seen_types:
            seen_types.add(entity_type.value)
            unique_types.append({
                'id': entity_type.value,
                'name': entity_type.value.replace('_', ' ').title()
            })

    return unique_types


async def get_all_statuses(session: AsyncSession) -> List[Dict[str, str]]:
    """Получение всех уникальных статусов без дубликатов"""
    statuses = []
    seen_statuses = set()

    # Статусы Question
    for status in QuestionStatus:
        status_value = status.value
        status_name = status_value.replace('_', ' ').title()
        if status_value not in seen_statuses:
            seen_statuses.add(status_value)
            statuses.append({
                'id': status_value,
                'name': status_name,
                'entity_type': 'question'
            })

    # Статусы Defect
    for status in DefectStatus:
        status_value = status.value
        status_name = status_value.replace('_', ' ').title()
        if status_value not in seen_statuses:
            seen_statuses.add(status_value)
            statuses.append({
                'id': status_value,
                'name': status_name,
                'entity_type': 'defect'
            })

    # Статусы Task (будут пропущены, так как совпадают с Defect)
    for status in TaskStatus:
        status_value = status.value
        status_name = status_value.replace('_', ' ').title()
        if status_value not in seen_statuses:
            seen_statuses.add(status_value)
            statuses.append({
                'id': status_value,
                'name': status_name,
                'entity_type': 'task'
            })

    # Статусы Proposal
    for status in ProposalStatus:
        status_value = status.value
        status_name = status_value.replace('_', ' ').title()
        if status_value not in seen_statuses:
            seen_statuses.add(status_value)
            statuses.append({
                'id': status_value,
                'name': status_name,
                'entity_type': 'proposal'
            })

    # Статусы Action Point
    for status in ActionPointStatus:
        status_value = status.value
        status_name = status_value.replace('_', ' ').title()
        if status_value not in seen_statuses:
            seen_statuses.add(status_value)
            statuses.append({
                'id': status_value,
                'name': status_name,
                'entity_type': 'action_point'
            })

    # Специальные статусы для Info
    for status_id, status_name in [('read', 'Read'), ('unread', 'Unread'), ('not_required', 'Not Required')]:
        if status_id not in seen_statuses:
            seen_statuses.add(status_id)
            statuses.append({
                'id': status_id,
                'name': status_name,
                'entity_type': 'info'
            })

    return statuses


def generate_task_explorer_html(user: User) -> HTMLResponse:
    """Генерирует HTML страницу для Task Explorer"""

    # Экранируем данные для безопасной вставки в JavaScript
    user_id_escaped = json.dumps(str(user.id))
    user_display_name_escaped = json.dumps(user.display_name)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Task Explorer</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            /* Стили из chat_channel_view.py с дополнениями */
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

            /* Скрываем старые селекты */
            #filter-entity-types,
            #filter-channels,
            #filter-chats,
            #filter-status {{
                display: none;
            }}

            /* Обновляем стили для остальных фильтров */
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

            /* Улучшаем отображение при множественном выборе в обычных селектах */
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

            /* Стили для отображения entity */
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

            /* Стиль для группы с date inputs */
            .date-filter-group {{
                flex: 1;
                min-width: 300px;
                max-width: 500px;
            }}

            /* Адаптивная верстка для дат */
            @media (max-width: 1200px) {{
                .date-filter-group {{
                    min-width: 100%;
                }}
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
                    <h2>Task Explorer</h2>
                </div>

                <div class="tabs">
                    <button class="tab active" data-tab="my-tasks">My Tasks</button>
                    <button class="tab" data-tab="outdated">Outdated</button>
                    <button class="tab" data-tab="all-filters">All Filters</button>
                </div>

                <div class="filters-panel" id="filters-panel">
                    <div class="filter-row">
                        <!-- Entity Types - компактный фильтр -->
                        <div class="compact-filter-group">
                            <label class="filter-label">Entity Types</label>
                            <div class="compact-filter" id="compact-filter-entity-types">
                                <div class="compact-filter-header" onclick="toggleCompactFilter('entity-types')">
                                    <div class="compact-filter-title">
                                        <span class="filter-name">All Entity Types</span>
                                        <span class="filter-counter" id="counter-entity-types"></span>
                                    </div>
                                    <span class="compact-filter-arrow">▼</span>
                                </div>
                                <div class="compact-filter-content" id="content-entity-types">
                                    <div class="filter-search">
                                        <input type="text" class="filter-search-input" 
                                               placeholder="Search types..." 
                                               onkeyup="filterOptionsFunc('entity-types', this.value)">
                                    </div>
                                    <div class="filter-checkboxes" id="checkboxes-entity-types">
                                        <!-- Заполнится через JavaScript -->
                                    </div>
                                    <div class="filter-actions">
                                        <button type="button" class="select-all-btn" 
                                                onclick="selectAll('entity-types')">Select All</button>
                                        <button type="button" class="clear-selection-btn" 
                                                onclick="clearSelection('entity-types')">Clear</button>
                                    </div>
                                </div>
                            </div>
                            <!-- Скрытый select для обратной совместимости -->
                            <select class="filter-select" id="filter-entity-types" multiple style="display: none;"></select>
                        </div>

                        <!-- Channels - компактный фильтр -->
                        <div class="compact-filter-group">
                            <label class="filter-label">Channels</label>
                            <div class="compact-filter" id="compact-filter-channels">
                                <div class="compact-filter-header" onclick="toggleCompactFilter('channels')">
                                    <div class="compact-filter-title">
                                        <span class="filter-name">All Channels</span>
                                        <span class="filter-counter" id="counter-channels"></span>
                                    </div>
                                    <span class="compact-filter-arrow">▼</span>
                                </div>
                                <div class="compact-filter-content" id="content-channels">
                                    <div class="filter-search">
                                        <input type="text" class="filter-search-input" 
                                               placeholder="Search channels..." 
                                               onkeyup="filterOptionsFunc('channels', this.value)">
                                    </div>
                                    <div class="filter-checkboxes" id="checkboxes-channels">
                                        <!-- Заполнится через JavaScript -->
                                    </div>
                                    <div class="filter-actions">
                                        <button type="button" class="select-all-btn" 
                                                onclick="selectAll('channels')">Select All</button>
                                        <button type="button" class="clear-selection-btn" 
                                                onclick="clearSelection('channels')">Clear</button>
                                    </div>
                                </div>
                            </div>
                            <select class="filter-select" id="filter-channels" multiple style="display: none;"></select>
                        </div>

                        <!-- Chats - компактный фильтр -->
                        <div class="compact-filter-group">
                            <label class="filter-label">Chats</label>
                            <div class="compact-filter" id="compact-filter-chats">
                                <div class="compact-filter-header" onclick="toggleCompactFilter('chats')">
                                    <div class="compact-filter-title">
                                        <span class="filter-name">All Chats</span>
                                        <span class="filter-counter" id="counter-chats"></span>
                                    </div>
                                    <span class="compact-filter-arrow">▼</span>
                                </div>
                                <div class="compact-filter-content" id="content-chats">
                                    <div class="filter-search">
                                        <input type="text" class="filter-search-input" 
                                               placeholder="Search chats..." 
                                               onkeyup="filterOptionsFunc('chats', this.value)">
                                    </div>
                                    <div class="filter-checkboxes" id="checkboxes-chats">
                                        <!-- Заполнится через JavaScript -->
                                    </div>
                                    <div class="filter-actions">
                                        <button type="button" class="select-all-btn" 
                                                onclick="selectAll('chats')">Select All</button>
                                        <button type="button" class="clear-selection-btn" 
                                                onclick="clearSelection('chats')">Clear</button>
                                    </div>
                                </div>
                            </div>
                            <select class="filter-select" id="filter-chats" multiple style="display: none;"></select>
                        </div>

                        <!-- Status - компактный фильтр -->
                        <div class="compact-filter-group">
                            <label class="filter-label">Status</label>
                            <div class="compact-filter" id="compact-filter-status">
                                <div class="compact-filter-header" onclick="toggleCompactFilter('status')">
                                    <div class="compact-filter-title">
                                        <span class="filter-name">All Statuses</span>
                                        <span class="filter-counter" id="counter-status"></span>
                                    </div>
                                    <span class="compact-filter-arrow">▼</span>
                                </div>
                                <div class="compact-filter-content" id="content-status">
                                    <div class="filter-search">
                                        <input type="text" class="filter-search-input" 
                                               placeholder="Search statuses..." 
                                               onkeyup="filterOptionsFunc('status', this.value)">
                                    </div>
                                    <div class="filter-checkboxes" id="checkboxes-status">
                                        <!-- Заполнится через JavaScript -->
                                    </div>
                                    <div class="filter-actions">
                                        <button type="button" class="select-all-btn" 
                                                onclick="selectAll('status')">Select All</button>
                                        <button type="button" class="clear-selection-btn" 
                                                onclick="clearSelection('status')">Clear</button>
                                    </div>
                                </div>
                            </div>
                            <select class="filter-select" id="filter-status" multiple style="display: none;"></select>
                        </div>
                    </div>

                    <div class="filter-row">
                        <!-- Остальные фильтры остаются как есть -->
                        <div class="regular-filter-group">
                            <label class="filter-label">Author</label>
                            <select class="filter-select" id="filter-author">
                                <option value="">All Authors</option>
                                <!-- Заполнится через JavaScript -->
                            </select>
                        </div>

                        <div class="regular-filter-group">
                            <label class="filter-label">Executor</label>
                            <select class="filter-select" id="filter-executor">
                                <option value="">All Executors</option>
                                <!-- Заполнится через JavaScript -->
                            </select>
                        </div>

                        <div class="regular-filter-group">
                            <label class="filter-label">QA</label>
                            <select class="filter-select" id="filter-qa">
                                <option value="">All QA</option>
                                <!-- Заполнится через JavaScript -->
                            </select>
                        </div>
                    </div>

                    <div class="filter-row">
                        <!-- Deadline Range -->
                        <div class="date-filter-group">
                            <label class="filter-label">Deadline Range</label>
                            <div class="date-input-group">
                                <input type="datetime-local" class="date-input" id="filter-deadline-from">
                                <input type="datetime-local" class="date-input" id="filter-deadline-to">
                            </div>
                        </div>

                        <!-- Created Range -->
                        <div class="date-filter-group">
                            <label class="filter-label">Created Range</label>
                            <div class="date-input-group">
                                <input type="datetime-local" class="date-input" id="filter-created-from">
                                <input type="datetime-local" class="date-input" id="filter-created-to">
                            </div>
                        </div>
                    </div>

                    <div class="filters-actions">
                        <button class="clear-filters-btn" id="clear-filters">Clear All Filters</button>
                        <button class="apply-filters-btn" id="apply-filters">Apply Filters</button>
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
                        <h3>No filters applied</h3>
                        <p>Use the filters above to find tasks</p>
                    </div>
                    <div class="loading" id="all-filters-loading" style="display: none;">Applying filters...</div>
                    <div class="entity-list" id="all-filters-list" style="display: none;"></div>
                </div>
            </div>
        </div>

        <script>
            // Сохраняем данные пользователя
            const userData = {{
                userId: {user_id_escaped},
                userDisplayName: {user_display_name_escaped}
            }};

            // Переменные состояния
            let currentTab = 'my-tasks';
            let filterOptions = null;
            let currentOpenFilter = null;
            let filterOptionsData = null;

            // Инициализация страницы
            document.addEventListener('DOMContentLoaded', async () => {{
                try {{
                    await loadNavigation();
                    setupNavigationEvents();
                    setupTabs();
                    await loadFilterOptions();
                    setupFilters();
                    loadMyTasks();

                    // Закрываем выпадающие списки при клике вне их
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

            // Загрузка навигации (та же функция, что и в chat_channel_view.py)
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
                    html += '<div id="notes-btn" class="nav-item">Notes</div>';
                    html += '<div id="task-explorer" class="nav-item active">Task Explorer</div>';

                    // Chats section
                    html += '<div id="chats-toggle" class="nav-item">Chats ▼</div>';
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

                    // Channels section
                    html += '<div id="channels-toggle" class="nav-item">Channels ▼</div>';
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

                    // Logout
                    html += '<div id="logout-btn" class="nav-item" onclick="logout()">Logout</div>';

                    sidebar.innerHTML = html;

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
                        if (chatsSubmenu) {{
                            chatsSubmenu.style.display = chatsSubmenu.style.display === 'none' ? 'flex' : 'none';
                        }}
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

            // Функции навигации
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

            // Настройка вкладок
            function setupTabs() {{
                const tabs = document.querySelectorAll('.tab');
                const tabContents = document.querySelectorAll('.tab-content');
                const filtersPanel = document.getElementById('filters-panel');

                tabs.forEach(tab => {{
                    tab.addEventListener('click', () => {{
                        const tabId = tab.dataset.tab;

                        // Обновляем активную вкладку
                        tabs.forEach(t => t.classList.remove('active'));
                        tab.classList.add('active');

                        // Показываем/скрываем фильтры
                        if (tabId === 'all-filters') {{
                            filtersPanel.classList.add('active');
                        }} else {{
                            filtersPanel.classList.remove('active');
                        }}

                        // Показываем соответствующий контент
                        tabContents.forEach(content => {{
                            content.classList.remove('active');
                        }});

                        document.getElementById(tabId + '-content').classList.add('active');

                        // Загружаем данные для вкладки
                        currentTab = tabId;
                        if (tabId === 'my-tasks') {{
                            loadMyTasks();
                        }} else if (tabId === 'outdated') {{
                            loadOutdatedTasks();
                        }} else if (tabId === 'all-filters') {{
                            // Для вкладки All Filters показываем только если уже применены фильтры
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

                // Закрываем другие открытые фильтры
                if (currentOpenFilter && currentOpenFilter !== filterType) {{
                    const prevHeader = document.getElementById(`compact-filter-${{currentOpenFilter}}`).querySelector('.compact-filter-header');
                    const prevContent = document.getElementById(`content-${{currentOpenFilter}}`);
                    const prevArrow = prevHeader.querySelector('.compact-filter-arrow');

                    prevHeader.classList.remove('active');
                    prevContent.classList.remove('active');
                    prevArrow.style.transform = 'rotate(0deg)';
                }}

                // Переключаем текущий фильтр
                const isOpening = !header.classList.contains('active');
                header.classList.toggle('active', isOpening);
                content.classList.toggle('active', isOpening);
                arrow.style.transform = isOpening ? 'rotate(180deg)' : 'rotate(0deg)';

                currentOpenFilter = isOpening ? filterType : null;

                // Если открываем, фокусируемся на поле поиска
                if (isOpening) {{
                    setTimeout(() => {{
                        const searchInput = content.querySelector('.filter-search-input');
                        if (searchInput) {{
                            searchInput.focus();
                        }}
                    }}, 100);
                }}
            }}

            // Функция для обновления счетчика выбранных элементов
            function updateFilterCounter(filterType) {{
                const checkboxes = document.querySelectorAll(`#checkboxes-${{filterType}} input[type="checkbox"]`);
                const counter = document.getElementById(`counter-${{filterType}}`);
                const header = document.getElementById(`compact-filter-${{filterType}}`).querySelector('.compact-filter-header');
                const filterName = header.querySelector('.filter-name');

                const selectedCount = Array.from(checkboxes).filter(cb => cb.checked).length;

                if (selectedCount > 0) {{
                    counter.textContent = selectedCount;
                    counter.classList.add('show');

                    // Обновляем имя фильтра, если выбран только один элемент
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

                // Также обновляем скрытый select для обратной совместимости
                updateHiddenSelect(filterType);
            }}

            // Функция для получения имени фильтра по умолчанию
            function getFilterDefaultName(filterType) {{
                const names = {{
                    'entity-types': 'All Entity Types',
                    'channels': 'All Channels',
                    'chats': 'All Chats',
                    'status': 'All Statuses'
                }};
                return names[filterType] || `All ${{filterType.replace('-', ' ')}}`;
            }}

            // Функция для обновления скрытого select элемента
            function updateHiddenSelect(filterType) {{
                const hiddenSelect = document.getElementById(`filter-${{filterType}}`);
                const checkboxes = document.querySelectorAll(`#checkboxes-${{filterType}} input[type="checkbox"]`);

                // Очищаем выбранные значения
                Array.from(hiddenSelect.options).forEach(option => {{
                    option.selected = false;
                }});

                // Устанавливаем выбранные значения
                checkboxes.forEach(checkbox => {{
                    if (checkbox.checked) {{
                        const option = Array.from(hiddenSelect.options).find(opt => opt.value === checkbox.value);
                        if (option) {{
                            option.selected = true;
                        }}
                    }}
                }});
            }}

            // Функция для фильтрации опций в выпадающем списке
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

            // Функция для выбора всех опций
            function selectAll(filterType) {{
                const checkboxes = document.querySelectorAll(`#checkboxes-${{filterType}} input[type="checkbox"]`);
                checkboxes.forEach(cb => cb.checked = true);
                updateFilterCounter(filterType);
            }}

            // Функция для очистки выбора
            function clearSelection(filterType) {{
                const checkboxes = document.querySelectorAll(`#checkboxes-${{filterType}} input[type="checkbox"]`);
                checkboxes.forEach(cb => cb.checked = false);
                updateFilterCounter(filterType);
            }}

            // Загрузка опций фильтров
            async function loadFilterOptions() {{
                try {{
                    const response = await fetch('/api/task-explorer/filter-options');
                    if (!response.ok) throw new Error('Failed to load filter options');

                    filterOptionsData = await response.json();

                    // Дедупликация статусов на клиенте для дополнительной защиты
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

            // Заполнение фильтров опциями
            function populateFilterOptions() {{
                if (!filterOptionsData) return;

                // Очищаем контейнеры перед заполнением
                const filterTypes = ['entity-types', 'channels', 'chats', 'status'];
                filterTypes.forEach(type => {{
                    const container = document.getElementById(`checkboxes-${{type}}`);
                    const select = document.getElementById(`filter-${{type}}`);
                    if (container) container.innerHTML = '';
                    if (select) select.innerHTML = '';
                }});

                // Очищаем обычные селекты
                ['author', 'executor'].forEach(type => {{
                    const select = document.getElementById(`filter-${{type}}`);
                    if (select) select.innerHTML = '<option value="">All ' + type.charAt(0).toUpperCase() + type.slice(1) + 's</option>';
                }});

                // Entity Types - простой список
                const entityTypesContainer = document.getElementById('checkboxes-entity-types');
                const entityTypesSelect = document.getElementById('filter-entity-types');

                // Дедупликация на клиенте
                const uniqueEntityTypes = [];
                const seenEntityTypes = new Set();

                filterOptionsData.entity_types.forEach(type => {{
                    if (!seenEntityTypes.has(type.id)) {{
                        seenEntityTypes.add(type.id);
                        uniqueEntityTypes.push(type);
                    }}
                }});

                uniqueEntityTypes.forEach(type => {{
                    // Для компактного фильтра
                    const checkboxItem = document.createElement('div');
                    checkboxItem.className = 'filter-checkbox-item';
                    checkboxItem.innerHTML = `
                        <input type="checkbox" id="entity-type-${{type.id}}" value="${{type.id}}">
                        <label for="entity-type-${{type.id}}" class="filter-checkbox-label">${{type.name}}</label>
                    `;
                    entityTypesContainer.appendChild(checkboxItem);

                    // Для скрытого select (обратная совместимость)
                    const option = document.createElement('option');
                    option.value = type.id;
                    option.textContent = type.name;
                    entityTypesSelect.appendChild(option);
                }});

                // Channels - объединяем все в один список с указанием группы
                const channelsContainer = document.getElementById('checkboxes-channels');
                const channelsSelect = document.getElementById('filter-channels');
                for (const [group, channels] of Object.entries(filterOptionsData.channels)) {{
                    channels.forEach(channel => {{
                        const displayName = `${{channel.name}} (${{group}})`;

                        // Для компактного фильтра
                        const checkboxItem = document.createElement('div');
                        checkboxItem.className = 'filter-checkbox-item';
                        checkboxItem.innerHTML = `
                            <input type="checkbox" id="channel-${{channel.id}}" value="${{channel.id}}">
                            <label for="channel-${{channel.id}}" class="filter-checkbox-label">${{displayName}}</label>
                        `;
                        channelsContainer.appendChild(checkboxItem);

                        // Для скрытого select (обратная совместимость)
                        const option = document.createElement('option');
                        option.value = channel.id;
                        option.textContent = displayName;
                        channelsSelect.appendChild(option);
                    }});
                }}

                // Chats - объединяем все в один список с указанием департамента
                const chatsContainer = document.getElementById('checkboxes-chats');
                const chatsSelect = document.getElementById('filter-chats');
                for (const [dept, chats] of Object.entries(filterOptionsData.chats)) {{
                    chats.forEach(chat => {{
                        const displayName = `${{chat.display_name}} (${{dept}})`;

                        // Для компактного фильтра
                        const checkboxItem = document.createElement('div');
                        checkboxItem.className = 'filter-checkbox-item';
                        checkboxItem.innerHTML = `
                            <input type="checkbox" id="chat-${{chat.chat_id}}" value="${{chat.chat_id}}">
                            <label for="chat-${{chat.chat_id}}" class="filter-checkbox-label">${{displayName}}</label>
                        `;
                        chatsContainer.appendChild(checkboxItem);

                        // Для скрытого select (обратная совместимость)
                        const option = document.createElement('option');
                        option.value = chat.chat_id;
                        option.textContent = displayName;
                        chatsSelect.appendChild(option);
                    }});
                }}

                // Status - уже дедуплицировано на бэкенде и на клиенте
                const statusContainer = document.getElementById('checkboxes-status');
                const statusSelect = document.getElementById('filter-status');

                // Сортируем статусы по имени для лучшего UX
                const sortedStatuses = [...filterOptionsData.statuses].sort((a, b) => 
                    a.name.localeCompare(b.name)
                );

                // Заполняем компактный фильтр
                sortedStatuses.forEach(status => {{
                    const checkboxItem = document.createElement('div');
                    checkboxItem.className = 'filter-checkbox-item';
                    checkboxItem.innerHTML = `
                        <input type="checkbox" id="status-${{status.id}}" value="${{status.id}}">
                        <label for="status-${{status.id}}" class="filter-checkbox-label">${{status.name}}</label>
                    `;
                    statusContainer.appendChild(checkboxItem);

                    // Для скрытого select (обратная совместимость)
                    const option = document.createElement('option');
                    option.value = status.id;
                    option.textContent = status.name;
                    statusSelect.appendChild(option);
                }});

                // Настраиваем обработчики событий для чекбоксов
                setupCheckboxEvents();

                // Пользователи для Author, Executor, QA (остаются как есть)
                const authorSelect = document.getElementById('filter-author');
                const executorSelect = document.getElementById('filter-executor');
                const qaSelect = document.getElementById('filter-qa');

                // Добавляем "Me" в начало каждого списка
                [authorSelect, executorSelect, qaSelect].forEach(select => {{
                    const meOption = document.createElement('option');
                    meOption.value = filterOptionsData.current_user_id;
                    meOption.textContent = 'Me (' + userData.userDisplayName + ')';
                    select.appendChild(meOption);
                }});

                // Добавляем пользователей по департаментам
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

            // Настройка фильтров
            function setupFilters() {{
                const applyBtn = document.getElementById('apply-filters');
                const clearBtn = document.getElementById('clear-filters');

                applyBtn.addEventListener('click', applyFilters);
                clearBtn.addEventListener('click', clearFilters);
            }}

            // Проверка, применены ли фильтры
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

            // Получение выбранных значений из мультиселекта
            function getSelectedValues(selectId) {{
                const select = document.getElementById(selectId);
                return Array.from(select.selectedOptions).map(option => option.value);
            }}

            // Применение фильтров
            async function applyFilters() {{
                if (currentTab !== 'all-filters') return;

                showLoading('all-filters');

                // Собираем параметры фильтров
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

                // Добавляем параметры, если они есть
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

            // Очистка фильтров
            function clearFilters() {{
                // Очищаем компактные фильтры
                const filterTypes = ['entity-types', 'channels', 'chats', 'status'];
                filterTypes.forEach(filterType => {{
                    clearSelection(filterType);
                }});

                // Очищаем обычные селекты
                ['author', 'executor', 'qa'].forEach(filter => {{
                    const select = document.getElementById(`filter-${{filter}}`);
                    if (select) select.value = '';
                }});

                // Очищаем date inputs
                document.querySelectorAll('.date-input').forEach(input => {{
                    input.value = '';
                }});

                // Если на вкладке All Filters, показываем пустое состояние
                if (currentTab === 'all-filters') {{
                    showEmptyState('all-filters');
                }}
            }}

            // Загрузка задач пользователя
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

            // Загрузка просроченных задач
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

            // Показать состояние загрузки
            function showLoading(tabId) {{
                document.getElementById(tabId + '-loading').style.display = 'block';
                document.getElementById(tabId + '-list').style.display = 'none';

                const emptyElement = document.getElementById(tabId + '-empty');
                if (emptyElement) {{
                    emptyElement.style.display = 'none';
                }}
            }}

            // Показать пустое состояние
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

            // Отображение списка entity
            function displayEntities(tabId, entities) {{
                const container = document.getElementById(tabId + '-list');
                const loading = document.getElementById(tabId + '-loading');
                const emptyState = document.getElementById(tabId + '-empty');

                if (entities.length === 0) {{
                    if (emptyState) {{
                        if (tabId === 'my-tasks') {{
                            emptyState.innerHTML = '<div class="empty-state-icon">✅</div><h3>No tasks found</h3><p>You have no active tasks assigned to you</p>';
                        }} else if (tabId === 'outdated') {{
                            emptyState.innerHTML = '<div class="empty-state-icon">⏰</div><h3>No outdated tasks</h3><p>Great! All tasks are up to date</p>';
                        }} else {{
                            emptyState.innerHTML = '<div class="empty-state-icon">🔍</div><h3>No tasks found</h3><p>Try adjusting your filters</p>';
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
                    // Определяем, просрочена ли задача
                    const isOutdated = tabId === 'outdated' || 
                        (entity.deadline && new Date(entity.deadline) < new Date());

                    html += '<div class="entity-card' + (isOutdated ? ' outdated' : '') + '">';
                    html += '<div class="entity-header">';
                    html += '<h3 class="entity-title">' + escapeHtml(entity.title) + '</h3>';
                    html += '<span class="entity-type">' + entity.type + '</span>';
                    html += '</div>';

                    html += '<div class="entity-meta">';
                    html += '<div class="entity-meta-item">';
                    html += '<strong>Author:</strong> ' + escapeHtml(entity.author_display_name);
                    html += '</div>';

                    if (entity.source_name) {{
                        html += '<div class="entity-meta-item">';
                        html += '<strong>Source:</strong> ';
                        html += '<span class="entity-source">' + escapeHtml(entity.source_name) + '</span>';
                        html += '</div>';
                    }}

                    html += '<div class="entity-meta-item">';
                    html += '<strong>Created:</strong> ' + new Date(entity.created_at).toLocaleString();
                    html += '</div>';

                    if (entity.deadline) {{
                        html += '<div class="entity-meta-item">';
                        html += '<strong>Deadline:</strong> ' + new Date(entity.deadline).toLocaleString();
                        html += '</div>';
                    }}
                    html += '</div>';

                    // Отображаем body, если есть
                    if (entity.body) {{
                        html += '<div class="entity-body">' + escapeHtml(entity.body) + '</div>';
                    }}

                    // Детали entity
                    html += '<div class="entity-details">';

                    // Priority
                    if (entity.priority !== undefined) {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">Priority:</span>';
                        html += '<span class="entity-priority">' + entity.priority + '</span>';
                        html += '</div>';
                    }}

                    // Severity
                    if (entity.severity !== undefined) {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">Severity:</span>';
                        html += '<span class="entity-severity">' + entity.severity + '</span>';
                        html += '</div>';
                    }}

                    // Status
                    if (entity.status) {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">Status:</span>';
                        html += '<span class="entity-status status-' + entity.status + '">';
                        html += entity.status.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                        html += '</span>';
                        html += '</div>';
                    }}

                    // Executor
                    if (entity.executor) {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">Executor:</span>';
                        html += escapeHtml(entity.executor);
                        html += '</div>';
                    }}

                    // QA
                    if (entity.qa) {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">QA:</span>';
                        html += escapeHtml(entity.qa);
                        html += '</div>';
                    }}

                    // Reproducible
                    if (entity.reproducible !== undefined) {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">Reproducible:</span>';
                        html += entity.reproducible ? 'Yes' : 'No';
                        html += '</div>';
                    }}

                    // Read count для Info
                    if (entity.type === 'info') {{
                        html += '<div class="entity-field">';
                        html += '<span class="entity-field-label">Read:</span>';
                        html += (entity.read_count || 0) + '/' + (entity.required_users.length || 0);
                        html += '</div>';
                    }}

                    html += '</div>';

                    // Кнопки действий
                    html += '<div class="entity-actions">';

                    // Кнопка перехода к entity
                    if (entity.source_type === 'channel') {{
                        html += '<button class="entity-action-btn" onclick="openChannel(\\'' + entity.source_id + '\\')">';
                        html += 'Go to Channel';
                        html += '</button>';
                    }} else if (entity.source_type === 'chat') {{
                        html += '<button class="entity-action-btn" onclick="openDirectChat(\\'' + entity.source_id + '\\')">';
                        html += 'Go to Chat';
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

            // Вспомогательная функция для экранирования HTML
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