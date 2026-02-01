import json
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.api.deps.auth import require_admin
from src.app.api.deps.database import get_session
from src.app.api.http.router import router
from src.app.core.security import hash_password
from src.app.storage.models import (
    Acknowledge,
    Channel,
    ChannelGroupEnum,
    Comment,
    DepartmentEnum,
    DirectChat,
    Entity,
    EntityTypeEnum,
    Topic,
    User,
    user_channels, ActionPointEntity, ProposalEntity, TaskEntity, DefectEntity, QuestionEntity, InfoEntity,
    InfoRequiredUser,
)


@router.get('/admin/', response_class=HTMLResponse)
async def admin_panel_page(user_login=Depends(require_admin), session: AsyncSession = Depends(get_session)):
    """Страница админ-панели"""
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    return generate_admin_panel_html(user)


async def get_all_users_grouped(session: AsyncSession) -> dict[str, list[dict]]:
    """Получаем всех пользователей, сгруппированных по департаментам"""
    users_query = select(User).options(selectinload(User.channels)).order_by(User.display_name)
    users_result = await session.execute(users_query)
    users = users_result.scalars().all()

    grouped_users = {}

    for user in users:
        dept_name = user.department.value if user.department else 'No Department'
        if dept_name not in grouped_users:
            grouped_users[dept_name] = []

        user_data = {
            'id': str(user.id),
            'login': user.login,
            'display_name': user.display_name,
            'department': user.department.value if user.department else None,
            'is_admin': user.is_admin,
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'channel_count': len(user.channels),
        }

        grouped_users[dept_name].append(user_data)

    return grouped_users


async def get_all_channels_grouped(session: AsyncSession) -> dict[str, list[dict]]:
    """Получаем все каналы, сгруппированные по группам"""
    channels_query = select(Channel).options(selectinload(Channel.users)).order_by(Channel.name)
    channels_result = await session.execute(channels_query)
    channels = channels_result.scalars().all()

    grouped_channels = {}

    for channel in channels:
        group_name = channel.group.value if channel.group else 'No Group'
        if group_name not in grouped_channels:
            grouped_channels[group_name] = []

        channel_data = {
            'id': str(channel.id),
            'name': channel.name,
            'group': channel.group.value if channel.group else None,
            'allowed_entity_types': channel.allowed_entity_types or [],
            'created_at': channel.created_at.isoformat() if channel.created_at else None,
            'user_count': len(channel.users),
        }

        grouped_channels[group_name].append(channel_data)

    return grouped_channels


async def get_all_departments() -> list[dict]:
    """Получаем все департаменты из Enum"""
    return [{'id': dept.value, 'name': dept.value.replace('_', ' ').title()} for dept in DepartmentEnum]


async def get_all_channel_groups() -> list[dict]:
    """Получаем все группы каналов из Enum"""
    return [{'id': group.value, 'name': group.value.replace('_', ' ').title()} for group in ChannelGroupEnum]


async def get_user_detail(user_id: str, session: AsyncSession) -> dict[str, Any]:
    """Получаем детальную информацию о пользователе"""
    user_query = select(User).where(User.id == user_id).options(selectinload(User.channels))
    user_result = await session.execute(user_query)
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail='User not found')

    channels_by_group = {}
    for channel in user.channels:
        group_name = channel.group.value if channel.group else 'No Group'
        if group_name not in channels_by_group:
            channels_by_group[group_name] = []
        channels_by_group[group_name].append({'id': str(channel.id), 'name': channel.name})

    return {
        'id': str(user.id),
        'login': user.login,
        'display_name': user.display_name,
        'department': user.department.value if user.department else None,
        'is_admin': user.is_admin,
        'created_at': user.created_at.isoformat() if user.created_at else None,
        'channels_by_group': channels_by_group,
    }


async def get_channel_detail(channel_id: str, session: AsyncSession) -> dict[str, Any]:
    """Получаем детальную информацию о канале"""
    channel_query = select(Channel).where(Channel.id == channel_id).options(selectinload(Channel.users))
    channel_result = await session.execute(channel_query)
    channel = channel_result.scalar_one_or_none()

    if not channel:
        raise HTTPException(status_code=404, detail='Channel not found')

    users_by_dept = {}
    for user in channel.users:
        dept_name = user.department.value if user.department else 'No Department'
        if dept_name not in users_by_dept:
            users_by_dept[dept_name] = []
        users_by_dept[dept_name].append({'id': str(user.id), 'login': user.login, 'display_name': user.display_name})

    return {
        'id': str(channel.id),
        'name': channel.name,
        'group': channel.group.value if channel.group else None,
        'allowed_entity_types': channel.allowed_entity_types or [],
        'created_at': channel.created_at.isoformat() if channel.created_at else None,
        'users_by_dept': users_by_dept,
    }


async def delete_channel_cascade(channel_id: str, session: AsyncSession):
    """Удаляет канал и все связанные с ним сущности"""
    entities_subquery = select(Entity.id).where(Entity.channel_id == channel_id)

    await session.execute(
        delete(Acknowledge).where(Acknowledge.entity_id.in_(entities_subquery))
    )

    await session.execute(
        delete(Comment).where(Comment.thread_id.in_(entities_subquery))
    )

    info_subquery = select(InfoEntity.entity_id).join(Entity).where(Entity.channel_id == channel_id)
    await session.execute(
        delete(InfoRequiredUser).where(InfoRequiredUser.info_id.in_(info_subquery))
    )

    await session.execute(
        delete(InfoEntity).where(InfoEntity.entity_id.in_(entities_subquery))
    )
    await session.execute(
        delete(QuestionEntity).where(QuestionEntity.entity_id.in_(entities_subquery))
    )
    await session.execute(
        delete(DefectEntity).where(DefectEntity.entity_id.in_(entities_subquery))
    )
    await session.execute(
        delete(TaskEntity).where(TaskEntity.entity_id.in_(entities_subquery))
    )
    await session.execute(
        delete(ProposalEntity).where(ProposalEntity.entity_id.in_(entities_subquery))
    )
    await session.execute(
        delete(ActionPointEntity).where(ActionPointEntity.entity_id.in_(entities_subquery))
    )

    await session.execute(
        delete(Entity).where(Entity.channel_id == channel_id)
    )

    topics_subquery = select(Topic.id).where(Topic.channel_id == channel_id)
    await session.execute(
        delete(Comment).where(Comment.thread_id.in_(topics_subquery))
    )
    await session.execute(
        delete(Topic).where(Topic.channel_id == channel_id)
    )

    await session.execute(
        delete(user_channels).where(user_channels.c.channel_id == channel_id)
    )

    await session.execute(
        delete(Channel).where(Channel.id == channel_id)
    )

    await session.commit()


def generate_admin_panel_html(user: User) -> HTMLResponse:
    """Генерирует HTML страницу для админ-панели"""
    user_id_escaped = json.dumps(str(user.id))
    user_display_name_escaped = json.dumps(user.display_name)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Панель администратора</title>
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
                overflow-y: auto;
                overflow-x: hidden;
            }}
            .header {{
                background-color: #f0f0f0;
                padding: 10px 20px;
                border-bottom: 1px solid #ddd;
                margin-bottom: 20px;
            }}
            h2 {{
                margin: 10px 0px;
            }}
            .admin-actions {{
                display: flex;
                gap: 10px;
            }}
            .admin-btn {{
                padding: 5px 10px;
                background-color: #5865f2;
                color: white;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-weight: bold;
            }}
            .admin-btn:hover {{
                background-color: #4752c4;
            }}
            .admin-btn.delete {{
                background-color: #f04747;
            }}
            .admin-btn.delete:hover {{
                background-color: #d84040;
            }}
            .admin-section {{
                margin: 0px 20px 20px 20px;
                border: 1px solid #ddd;
                border-radius: 8px;
                overflow: hidden;
                flex-shrink: 0;
            }}
            .section-header {{
                background-color: #f0f0f0;
                padding: 5px 10px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                cursor: pointer;
                border-bottom: 1px solid #ddd;
            }}
            .section-title {{
                font-size: 18px;
                font-weight: bold;
                color: #333;
            }}
            .section-toggle {{
                font-size: 20px;
                color: #666;
            }}
            .section-content {{
                padding: 5px;
                background-color: white;
                display: none;
                max-height: 600px;
                overflow-y: auto;
                overflow-x: hidden;
            }}
            .section-content.expanded {{
                display: block;
            }}
            .list-group {{
                margin-bottom: 15px;
            }}
            .group-title {{
                font-weight: bold;
                color: #555;
                padding-bottom: 5px;
                border-bottom: 1px solid #eee;
            }}
            .list-item {{
                padding: 5px;
                margin-bottom: 8px;
                background-color: #f9f9f9;
                border-left: 3px solid #5865f2;
                border-radius: 4px;
                cursor: pointer;
                transition: background-color 0.2s;
            }}
            .list-item:hover {{
                background-color: #f0f0f0;
            }}
            .item-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 5px;
            }}
            .item-name {{
                font-weight: bold;
                color: #333;
            }}
            .item-meta {{
                font-size: 12px;
                color: #888;
            }}
            .item-details {{
                font-size: 13px;
                color: #666;
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
                z-index: 2000;
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
                font-size: 14px;
                box-sizing: border-box;
            }}
            .form-checkbox {{
                margin-right: 8px;
            }}
            .checkbox-label {{
                display: flex;
                align-items: center;
                margin-bottom: 5px;
                cursor: pointer;
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
            .create-btn, .save-btn {{
                padding: 8px 16px;
                background-color: #57f287;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-weight: bold;
            }}
            .create-btn:hover, .save-btn:hover {{
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
            .drawer-actions {{
                padding: 15px 20px;
                border-top: 1px solid #ddd;
                background-color: #f9f9f9;
                display: flex;
                gap: 10px;
            }}
            .drawer-section {{
                margin-bottom: 20px;
                padding: 15px;
                background-color: #f9f9f9;
                border-radius: 5px;
            }}
            .drawer-section-title {{
                font-weight: bold;
                margin-bottom: 10px;
                color: #333;
            }}
            .drawer-item {{
                margin-bottom: 8px;
                padding: 8px;
                background-color: white;
                border-radius: 4px;
                border-left: 3px solid #5865f2;
            }}
            .alert {{
                padding: 10px 15px;
                border-radius: 5px;
                margin-bottom: 15px;
                display: none;
            }}
            .alert-error {{
                background-color: #f8d7da;
                color: #721c24;
                border: 1px solid #f5c6cb;
            }}
            .alert-success {{
                background-color: #d4edda;
                color: #155724;
                border: 1px solid #c3e6cb;
            }}
            .modal-alert {{
                margin: 0 0 15px 0;
            }}
            .password-hint {{
                font-size: 12px;
                color: #888;
                margin-top: 4px;
            }}
            .checkbox-list {{
                max-height: 200px;
                overflow-y: auto;
                padding: 10px;
                background-color: #f9f9f9;
                border-radius: 4px;
                border: 1px solid #eee;
            }}
            .checkbox-group {{
                margin-bottom: 15px;
            }}
            .checkbox-group-title {{
                font-weight: bold;
                margin-bottom: 8px;
                color: #555;
            }}
            .admin-badge {{
                display: inline-block;
                padding: 2px 8px;
                background-color: #f04747;
                color: white;
                border-radius: 10px;
                font-size: 11px;
                margin-left: 8px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="sidebar" id="sidebar"></div>

            <div class="main-content">
                <div class="header">
                    <h2>Панель администратора</h2>
                    <div class="admin-actions">
                        <button class="admin-btn" id="add-user-btn">Добавить пользователя</button>
                        <button class="admin-btn" id="add-channel-btn">Добавить канал</button>
                    </div>
                </div>

                <div class="admin-section" id="users-section">
                    <div class="section-header" onclick="toggleSection('users')">
                        <div class="section-title">Пользователи</div>
                        <div class="section-toggle">▼</div>
                    </div>
                    <div class="section-content" id="users-content">
                        <div style="text-align: center; padding: 20px; color: #888;">
                            Loading users...
                        </div>
                    </div>
                </div>

                <div class="admin-section" id="channels-section">
                    <div class="section-header" onclick="toggleSection('channels')">
                        <div class="section-title">Каналы</div>
                        <div class="section-toggle">▼</div>
                    </div>
                    <div class="section-content" id="channels-content">
                        <div style="text-align: center; padding: 20px; color: #888;">
                            Loading channels...
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="modal-overlay" id="create-user-modal-overlay">
            <div class="modal" id="create-user-modal">
                <div class="modal-header">
                    <div class="modal-title">Создать пользователя</div>
                    <button class="close-modal" id="close-create-user-modal">&times;</button>
                </div>
                
                <div class="alert modal-alert" id="create-user-alert-message"></div>
                
                <form id="create-user-form">
                    <div class="form-group">
                        <label class="form-label" for="user-login">Логин</label>
                        <input type="text" class="form-input" id="user-login" required>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="user-display-name">Отображаемое имя</label>
                        <input type="text" class="form-input" id="user-display-name" required>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="user-password">Пароль</label>
                        <input type="password" class="form-input" id="user-password" required>
                        <div style="font-size: 12px; color: #888; margin-top: 4px;">
                            Пароль должен содержать как минимум 6 символов
                        </div>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="user-password-confirm">Подтверждение пароля</label>
                        <input type="password" class="form-input" id="user-password-confirm" required>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="user-department">Отдел</label>
                        <select class="form-select" id="user-department" required>
                            <option value="">Выберите подразделение...</option>
                            <!-- Departments will be populated via JavaScript -->
                        </select>
                    </div>

                    <div class="form-group">
                        <label class="checkbox-label">
                            <input type="checkbox" class="form-checkbox" id="user-is-admin">
                            <span>Права администратора</span>
                        </label>
                    </div>

                    <div class="form-group">
                        <label class="form-label">Каналы</label>
                        <div class="checkbox-list" id="user-channels-list">
                            <!-- Channels grouped by groups will be populated via JavaScript -->
                        </div>
                    </div>

                    <div class="modal-footer">
                        <button type="button" class="cancel-btn" id="cancel-create-user">Отменить</button>
                        <button type="submit" class="create-btn">Создать пользователя</button>
                    </div>
                </form>
            </div>
        </div>

        <div class="modal-overlay" id="edit-user-modal-overlay">
            <div class="modal" id="edit-user-modal">
                <div class="modal-header">
                    <div class="modal-title">Редактировать пользователя</div>
                    <button class="close-modal" id="close-edit-user-modal">&times;</button>
                </div>
                
                <div class="alert modal-alert" id="edit-user-alert-message"></div>
                
                <form id="edit-user-form">
                    <input type="hidden" id="edit-user-id">

                    <div class="form-group">
                        <label class="form-label" for="edit-user-login">Логин</label>
                        <input type="text" class="form-input" id="edit-user-login" required>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="edit-user-display-name">Отображаемое имя</label>
                        <input type="text" class="form-input" id="edit-user-display-name" required>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="edit-user-password">Новый пароль</label>
                        <input type="password" class="form-input" id="edit-user-password" 
                               placeholder="Оставьте пустым для сохранения текущего пароля">
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="edit-user-password-confirm">Подтверждение нового пароля</label>
                        <input type="password" class="form-input" id="edit-user-password-confirm" 
                               placeholder="Повторите новый пароль">
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="edit-user-department">Отдел</label>
                        <select class="form-select" id="edit-user-department" required>
                            <option value="">Выберите подразделение...</option>
                            <!-- Departments will be populated via JavaScript -->
                        </select>
                    </div>

                    <div class="form-group">
                        <label class="checkbox-label">
                            <input type="checkbox" class="form-checkbox" id="edit-user-is-admin">
                            <span>Права администратора</span>
                        </label>
                    </div>

                    <div class="form-group">
                        <label class="form-label">Каналы</label>
                        <div class="checkbox-list" id="edit-user-channels-list">
                            <!-- Channels grouped by groups will be populated via JavaScript -->
                        </div>
                    </div>

                    <div class="modal-footer">
                        <button type="button" class="cancel-btn" id="cancel-edit-user">Отменить</button>
                        <button type="submit" class="save-btn">Сохранить изменения</button>
                    </div>
                </form>
            </div>
        </div>

        <div class="modal-overlay" id="create-channel-modal-overlay">
            <div class="modal" id="create-channel-modal">
                <div class="modal-header">
                    <div class="modal-title">Создание канала</div>
                    <button class="close-modal" id="close-create-channel-modal">&times;</button>
                </div>
                <form id="create-channel-form">
                    <div class="form-group">
                        <label class="form-label" for="channel-name">Наименование канала</label>
                        <input type="text" class="form-input" id="channel-name" required>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="channel-group">Группа</label>
                        <select class="form-select" id="channel-group" required>
                            <option value="">Выберите группу...</option>
                            <!-- Groups will be populated via JavaScript -->
                        </select>
                    </div>

                    <div class="form-group">
                        <label class="form-label">Доступные типы сущностей</label>
                        <div class="checkbox-list" id="channel-entity-types-list">
                            <!-- Entity types will be populated via JavaScript -->
                        </div>
                    </div>

                    <div class="form-group">
                        <label class="form-label">Пользователи</label>
                        <div class="checkbox-list" id="channel-users-list">
                            <!-- Users grouped by departments will be populated via JavaScript -->
                        </div>
                    </div>

                    <div class="modal-footer">
                        <button type="button" class="cancel-btn" id="cancel-create-channel">Отменить</button>
                        <button type="submit" class="create-btn">Создать канал</button>
                    </div>
                </form>
            </div>
        </div>

        <div class="drawer-overlay" id="user-detail-drawer-overlay">
            <div class="drawer" id="user-detail-drawer">
                <div class="drawer-header">
                    <div class="drawer-title">Информация о пользователе</div>
                    <button class="close-drawer" id="close-user-drawer">&times;</button>
                </div>
                <div class="drawer-content" id="user-detail-content"></div>
                <div class="drawer-actions">
                    <button class="admin-btn" id="edit-user-btn">Редактировать пользователя</button>
                </div>
            </div>
        </div>

        <div class="drawer-overlay" id="channel-detail-drawer-overlay">
            <div class="drawer" id="channel-detail-drawer">
                <div class="drawer-header">
                    <div class="drawer-title">Информация о канале</div>
                    <button class="close-drawer" id="close-channel-drawer">&times;</button>
                </div>
                <div class="drawer-content" id="channel-detail-content"></div>
                <div class="drawer-actions">
                    <button class="admin-btn delete" id="delete-channel-btn">Удалить канал</button>
                </div>
            </div>
        </div>

        <script>
            const adminData = {{
                userId: {user_id_escaped},
                userDisplayName: {user_display_name_escaped}
            }};

            let allUsersData = {{}};
            let allChannelsData = {{}};
            let allDepartments = [];
            let allChannelGroups = [];
            let allEntityTypes = [];
            let currentUserDetail = null;
            let currentChannelDetail = null;

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

                    html += '<div id="admin-btn" class="nav-item active">Панель администратора</div>';

                    html += '<div id="profile-btn" onclick="openProfile()" class="nav-item">Профиль</div>';
                    html += '<div id="notes-btn" class="nav-item">Заметки</div>';
                    html += '<div id="task-explorer" onclick="openTaskExplorer()" class="nav-item">Обозреватель задач</div>';

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

            async function loadAdminData() {{
                try {{
                    const [usersRes, channelsRes, deptsRes, groupsRes, entityTypesRes] = await Promise.all([
                        fetch('/api/admin/users'),
                        fetch('/api/admin/channels'),
                        fetch('/api/admin/departments'),
                        fetch('/api/admin/channel-groups'),
                        fetch('/api/entity-types')
                    ]);

                    if (usersRes.ok) {{
                        allUsersData = await usersRes.json();
                        renderUsersList();
                    }}

                    if (channelsRes.ok) {{
                        allChannelsData = await channelsRes.json();
                        renderChannelsList();
                    }}

                    if (deptsRes.ok) {{
                        allDepartments = await deptsRes.json();
                    }}

                    if (groupsRes.ok) {{
                        allChannelGroups = await groupsRes.json();
                    }}

                    if (entityTypesRes && entityTypesRes.ok) {{
                        allEntityTypes = await entityTypesRes.json();
                    }}

                }} catch (error) {{
                    console.error('Error loading admin data:', error);
                }}
            }}

            function renderUsersList() {{
                const content = document.getElementById('users-content');
                if (!content || !allUsersData) return;

                let html = '';

                for (const [deptName, users] of Object.entries(allUsersData)) {{
                    html += '<div class="list-group">';
                    html += '<div class="group-title">' + escapeHtml(deptName) + ' (' + users.length + ')</div>';

                    users.forEach(user => {{
                        html += '<div class="list-item" onclick="openUserDetail(\\'' + user.id + '\\')">';
                        html += '<div class="item-header">';
                        html += '<div class="item-name">' + escapeHtml(user.display_name);
                        if (user.is_admin) {{
                            html += ' <span class="admin-badge">ADMIN</span>';
                        }}
                        html += '</div>';
                        html += '<div class="item-meta">' + (user.created_at ? new Date(user.created_at).toLocaleDateString() : '') + '</div>';
                        html += '</div>';
                        html += '<div class="item-details">';
                        html += 'Логин: ' + escapeHtml(user.login) + ' | ';
                        html += 'Каналов: ' + user.channel_count;
                        html += '</div>';
                        html += '</div>';
                    }});

                    html += '</div>';
                }}

                if (Object.keys(allUsersData).length === 0) {{
                    html = '<div style="text-align: center; padding: 20px; color: #888;">No users found</div>';
                }}

                content.innerHTML = html;
            }}

            function renderChannelsList() {{
                const content = document.getElementById('channels-content');
                if (!content || !allChannelsData) return;

                let html = '';

                for (const [groupName, channels] of Object.entries(allChannelsData)) {{
                    html += '<div class="list-group">';
                    html += '<div class="group-title">' + escapeHtml(groupName) + ' (' + channels.length + ')</div>';

                    channels.forEach(channel => {{
                        html += '<div class="list-item" onclick="openChannelDetail(\\'' + channel.id + '\\')">';
                        html += '<div class="item-header">';
                        html += '<div class="item-name">' + escapeHtml(channel.name) + '</div>';
                        html += '<div class="item-meta">' + (channel.created_at ? new Date(channel.created_at).toLocaleDateString() : '') + '</div>';
                        html += '</div>';
                        html += '<div class="item-details">';
                        html += 'Типы сущностей: ' + (channel.allowed_entity_types.length > 0 ? 
                            channel.allowed_entity_types.join(', ') : 'All') + ' | ';
                        html += 'Пользователей: ' + channel.user_count;
                        html += '</div>';
                        html += '</div>';
                    }});

                    html += '</div>';
                }}

                if (Object.keys(allChannelsData).length === 0) {{
                    html = '<div style="text-align: center; padding: 20px; color: #888;">No channels found</div>';
                }}

                content.innerHTML = html;
            }}

            async function openUserDetail(userId) {{
                try {{
                    document.getElementById('channel-detail-drawer-overlay').style.display = 'none';

                    const response = await fetch('/api/admin/users/' + userId);
                    if (!response.ok) throw new Error('Failed to load user details');

                    currentUserDetail = await response.json();

                    const content = document.getElementById('user-detail-content');
                    let html = '';

                    html += '<div class="drawer-section">';
                    html += '<div class="drawer-section-title">Общая информация</div>';
                    html += '<div class="drawer-item">';
                    html += '<strong>Логин:</strong> ' + escapeHtml(currentUserDetail.login);
                    html += '</div>';
                    html += '<div class="drawer-item">';
                    html += '<strong>Отображаемое имя:</strong> ' + escapeHtml(currentUserDetail.display_name);
                    html += '</div>';
                    html += '<div class="drawer-item">';
                    html += '<strong>Отдел:</strong> ' + 
                        (currentUserDetail.department ? 
                            escapeHtml(currentUserDetail.department.replace(/_/g, ' ').replace(/\\b\\w/g, l => l.toUpperCase())) : 
                            'None');
                    html += '</div>';
                    html += '<div class="drawer-item">';
                    html += '<strong>Является ли администратором:</strong> ' + (currentUserDetail.is_admin ? 'Да' : 'Нет');
                    html += '</div>';
                    html += '<div class="drawer-item">';
                    html += '<strong>Создан:</strong> ' + (currentUserDetail.created_at ? 
                        new Date(currentUserDetail.created_at).toLocaleString() : 'Unknown');
                    html += '</div>';
                    html += '</div>';

                    if (currentUserDetail.channels_by_group && Object.keys(currentUserDetail.channels_by_group).length > 0) {{
                        html += '<div class="drawer-section">';
                        html += '<div class="drawer-section-title">Каналы</div>';

                        for (const [groupName, channels] of Object.entries(currentUserDetail.channels_by_group)) {{
                            html += '<div style="margin-bottom: 10px;">';
                            html += '<div style="font-weight: bold; margin-bottom: 5px;">' + 
                                escapeHtml(groupName.replace(/_/g, ' ').replace(/\\b\\w/g, l => l.toUpperCase())) + '</div>';

                            channels.forEach(channel => {{
                                html += '<div class="drawer-item" style="margin-left: 10px;">';
                                html += escapeHtml(channel.name);
                                html += '</div>';
                            }});

                            html += '</div>';
                        }}

                        html += '</div>';
                    }} else {{
                        html += '<div class="drawer-section">';
                        html += '<div class="drawer-section-title">Каналы</div>';
                        html += '<div class="drawer-item">No channels</div>';
                        html += '</div>';
                    }}

                    content.innerHTML = html;

                    document.getElementById('user-detail-drawer-overlay').style.display = 'block';

                }} catch (error) {{
                    console.error('Error loading user details:', error);
                    alert('Failed to load user details');
                }}
            }}

            async function openChannelDetail(channelId) {{
                try {{
                    document.getElementById('user-detail-drawer-overlay').style.display = 'none';

                    const response = await fetch('/api/admin/channels/' + channelId);
                    if (!response.ok) throw new Error('Failed to load channel details');

                    currentChannelDetail = await response.json();

                    const content = document.getElementById('channel-detail-content');
                    let html = '';

                    html += '<div class="drawer-section">';
                    html += '<div class="drawer-section-title">Общая информация</div>';
                    html += '<div class="drawer-item">';
                    html += '<strong>Наименование:</strong> ' + escapeHtml(currentChannelDetail.name);
                    html += '</div>';
                    html += '<div class="drawer-item">';
                    html += '<strong>Группа:</strong> ' + 
                        (currentChannelDetail.group ? 
                            escapeHtml(currentChannelDetail.group.replace(/_/g, ' ').replace(/\\b\\w/g, l => l.toUpperCase())) : 
                            'None');
                    html += '</div>';
                    html += '<div class="drawer-item">';
                    html += '<strong>Доступные типы сущностей:</strong> ' + 
                        (currentChannelDetail.allowed_entity_types.length > 0 ? 
                         currentChannelDetail.allowed_entity_types.join(', ') : 'All');
                    html += '</div>';
                    html += '<div class="drawer-item">';
                    html += '<strong>Создан:</strong> ' + (currentChannelDetail.created_at ? 
                        new Date(currentChannelDetail.created_at).toLocaleString() : 'Unknown');
                    html += '</div>';
                    html += '</div>';

                    if (currentChannelDetail.users_by_dept && Object.keys(currentChannelDetail.users_by_dept).length > 0) {{
                        html += '<div class="drawer-section">';
                        html += '<div class="drawer-section-title">Пользователи (' + 
                            Object.values(currentChannelDetail.users_by_dept).flat().length + ')</div>';

                        for (const [deptName, users] of Object.entries(currentChannelDetail.users_by_dept)) {{
                            html += '<div style="margin-bottom: 10px;">';
                            html += '<div style="font-weight: bold; margin-bottom: 5px;">' + 
                                escapeHtml(deptName.replace(/_/g, ' ').replace(/\\b\\w/g, l => l.toUpperCase())) + 
                                ' (' + users.length + ')</div>';

                            users.forEach(user => {{
                                html += '<div class="drawer-item" style="margin-left: 10px;">';
                                html += escapeHtml(user.display_name) + ' (' + escapeHtml(user.login) + ')';
                                html += '</div>';
                            }});

                            html += '</div>';
                        }}

                        html += '</div>';
                    }} else {{
                        html += '<div class="drawer-section">';
                        html += '<div class="drawer-section-title">Пользователи</div>';
                        html += '<div class="drawer-item">Нет пользователей</div>';
                        html += '</div>';
                    }}

                    content.innerHTML = html;

                    document.getElementById('channel-detail-drawer-overlay').style.display = 'block';

                }} catch (error) {{
                    console.error('Error loading channel details:', error);
                    alert('Failed to load channel details');
                }}
            }}

            function toggleSection(section) {{
                const content = document.getElementById(section + '-content');
                const toggle = document.querySelector('#' + section + '-section .section-toggle');

                if (content.classList.contains('expanded')) {{
                    content.classList.remove('expanded');
                    toggle.textContent = '▼';
                }} else {{
                    content.classList.add('expanded');
                    toggle.textContent = '▲';
                }}
            }}

            function setupModals() {{
                const createUserModal = document.getElementById('create-user-modal-overlay');
                const closeCreateUserModal = document.getElementById('close-create-user-modal');
                const cancelCreateUser = document.getElementById('cancel-create-user');
                const addUserBtn = document.getElementById('add-user-btn');
                const createUserForm = document.getElementById('create-user-form');
                
                function showCreateUserAlert(message, type) {{
                    const modalAlert = document.getElementById('create-user-alert-message');
                    modalAlert.textContent = message;
                    modalAlert.className = 'alert alert-' + type + ' modal-alert';
                    modalAlert.style.display = 'block';
                    
                    setTimeout(() => {{
                        modalAlert.style.display = 'none';
                    }}, 5000);
                }}
                
                function showEditUserAlert(message, type) {{
                    const modalAlert = document.getElementById('edit-user-alert-message');
                    modalAlert.textContent = message;
                    modalAlert.className = 'alert alert-' + type + ' modal-alert';
                    modalAlert.style.display = 'block';
                    
                    setTimeout(() => {{
                        modalAlert.style.display = 'none';
                    }}, 5000);
                }}

                if (createUserModal && closeCreateUserModal && cancelCreateUser && addUserBtn && createUserForm) {{
                    function closeCreateUserModalFunc() {{
                        createUserModal.style.display = 'none';
                        createUserForm.reset();
                        document.getElementById('create-user-alert-message').style.display = 'none';
                    }}

                    addUserBtn.addEventListener('click', async () => {{
                        document.getElementById('create-user-alert-message').style.display = 'none';
                        const deptSelect = document.getElementById('user-department');
                        deptSelect.innerHTML = '<option value="">Выберите подразделение...</option>';
                        allDepartments.forEach(dept => {{
                            const option = document.createElement('option');
                            option.value = dept.id;
                            option.textContent = dept.name;
                            deptSelect.appendChild(option);
                        }});

                        const channelsList = document.getElementById('user-channels-list');
                        channelsList.innerHTML = '';

                        for (const [groupName, channels] of Object.entries(allChannelsData)) {{
                            const groupDiv = document.createElement('div');
                            groupDiv.className = 'checkbox-group';
                            groupDiv.innerHTML = '<div class="checkbox-group-title">' + 
                                escapeHtml(groupName.replace(/_/g, ' ').replace(/\\b\\w/g, l => l.toUpperCase())) + '</div>';

                            channels.forEach(channel => {{
                                const label = document.createElement('label');
                                label.className = 'checkbox-label';
                                label.innerHTML = `
                                    <input type="checkbox" class="form-checkbox" value="${{channel.id}}">
                                    <span>${{escapeHtml(channel.name)}}</span>
                                `;
                                groupDiv.appendChild(label);
                            }});

                            channelsList.appendChild(groupDiv);
                        }}

                        createUserModal.style.display = 'flex';
                    }});

                    createUserModal.addEventListener('click', (e) => {{
                        if (e.target === createUserModal) {{
                            closeCreateUserModalFunc();
                        }}
                    }});

                    closeCreateUserModal.addEventListener('click', closeCreateUserModalFunc);
                    cancelCreateUser.addEventListener('click', closeCreateUserModalFunc);

                    createUserForm.addEventListener('submit', async (e) => {{
                        e.preventDefault();

                        const formData = {{
                            login: document.getElementById('user-login').value,
                            display_name: document.getElementById('user-display-name').value,
                            password: document.getElementById('user-password').value,
                            department: document.getElementById('user-department').value,
                            is_admin: document.getElementById('user-is-admin').checked,
                            channel_ids: []
                        }};

                        const channelCheckboxes = document.querySelectorAll('#user-channels-list input[type="checkbox"]:checked');
                        channelCheckboxes.forEach(cb => {{
                            formData.channel_ids.push(cb.value);
                        }});

                        const password = document.getElementById('user-password').value;
                        const passwordConfirm = document.getElementById('user-password-confirm').value;

                        if (password.length < 6) {{
                            showCreateUserAlert('Пароль должен содержать как минимум 6 символов', 'error');
                            return;
                        }}

                        if (password !== passwordConfirm) {{
                            showCreateUserAlert('Пароли не совпадают', 'error');
                            return;
                        }}

                        try {{
                            const response = await fetch('/api/admin/users', {{
                                method: 'POST',
                                headers: {{
                                    'Content-Type': 'application/json'
                                }},
                                body: JSON.stringify(formData)
                            }});

                            if (response.ok) {{
                                closeCreateUserModalFunc();
                                await loadAdminData();
                                await refreshNavigation();
                            }} else {{
                                const error = await response.json();
                                alert('Failed to create user: ' + (error.detail || 'Unknown error'));
                            }}
                        }} catch (error) {{
                            console.error('Error creating user:', error);
                            alert('Error creating user');
                        }}
                    }});
                }}

                const editUserModal = document.getElementById('edit-user-modal-overlay');
                const closeEditUserModal = document.getElementById('close-edit-user-modal');
                const cancelEditUser = document.getElementById('cancel-edit-user');
                const editUserBtn = document.getElementById('edit-user-btn');
                const editUserForm = document.getElementById('edit-user-form');

                if (editUserModal && closeEditUserModal && cancelEditUser && editUserBtn && editUserForm) {{
                    function closeEditUserModalFunc() {{
                        editUserModal.style.display = 'none';
                        editUserForm.reset();
                        document.getElementById('edit-user-alert-message').style.display = 'none';
                    }}

                    editUserBtn.addEventListener('click', () => {{
                        if (!currentUserDetail) return;
                        
                        document.getElementById('edit-user-alert-message').style.display = 'none';

                        document.getElementById('edit-user-id').value = currentUserDetail.id;
                        document.getElementById('edit-user-login').value = currentUserDetail.login;
                        document.getElementById('edit-user-display-name').value = currentUserDetail.display_name;

                        const deptSelect = document.getElementById('edit-user-department');
                        deptSelect.innerHTML = '<option value="">Выберите подразделение...</option>';
                        allDepartments.forEach(dept => {{
                            const option = document.createElement('option');
                            option.value = dept.id;
                            option.textContent = dept.name;
                            if (currentUserDetail.department && dept.id === currentUserDetail.department) {{
                                option.selected = true;
                            }}
                            deptSelect.appendChild(option);
                        }});

                        document.getElementById('edit-user-is-admin').checked = currentUserDetail.is_admin;

                        const channelsList = document.getElementById('edit-user-channels-list');
                        channelsList.innerHTML = '';

                        const userChannelIds = [];
                        if (currentUserDetail.channels_by_group) {{
                            for (const channels of Object.values(currentUserDetail.channels_by_group)) {{
                                channels.forEach(channel => {{
                                    userChannelIds.push(channel.id);
                                }});
                            }}
                        }}

                        for (const [groupName, channels] of Object.entries(allChannelsData)) {{
                            const groupDiv = document.createElement('div');
                            groupDiv.className = 'checkbox-group';
                            groupDiv.innerHTML = '<div class="checkbox-group-title">' + 
                                escapeHtml(groupName.replace(/_/g, ' ').replace(/\\b\\w/g, l => l.toUpperCase())) + '</div>';

                            channels.forEach(channel => {{
                                const isChecked = userChannelIds.includes(channel.id);
                                const label = document.createElement('label');
                                label.className = 'checkbox-label';
                                label.innerHTML = `
                                    <input type="checkbox" class="form-checkbox" value="${{channel.id}}" ${{isChecked ? 'checked' : ''}}>
                                    <span>${{escapeHtml(channel.name)}}</span>
                                `;
                                groupDiv.appendChild(label);
                            }});

                            channelsList.appendChild(groupDiv);
                        }}

                        editUserModal.style.display = 'flex';
                    }});

                    editUserModal.addEventListener('click', (e) => {{
                        if (e.target === editUserModal) {{
                            closeEditUserModalFunc();
                        }}
                    }});

                    closeEditUserModal.addEventListener('click', closeEditUserModalFunc);
                    cancelEditUser.addEventListener('click', closeEditUserModalFunc);

                    editUserForm.addEventListener('submit', async (e) => {{
                        e.preventDefault();

                        const userId = document.getElementById('edit-user-id').value;
                        const formData = {{
                            login: document.getElementById('edit-user-login').value,
                            display_name: document.getElementById('edit-user-display-name').value,
                            department: document.getElementById('edit-user-department').value,
                            is_admin: document.getElementById('edit-user-is-admin').checked,
                            channel_ids: []
                        }};

                        const channelCheckboxes = document.querySelectorAll('#edit-user-channels-list input[type="checkbox"]:checked');
                        channelCheckboxes.forEach(cb => {{
                            formData.channel_ids.push(cb.value);
                        }});

                        const password = document.getElementById('edit-user-password').value;
                        const passwordConfirm = document.getElementById('edit-user-password-confirm').value;

                        if (password) {{
                            if (password.length < 6) {{
                                showEditUserAlert('Пароль должен содержать как минимум 6 символов', 'error');
                                return;
                            }}

                            if (password !== passwordConfirm) {{
                                showEditUserAlert('Пароли не совпадают', 'error');
                                return;
                            }}

                            formData.password = password;
                        }}

                        try {{
                            const response = await fetch('/api/admin/users/' + userId, {{
                                method: 'PATCH',
                                headers: {{
                                    'Content-Type': 'application/json'
                                }},
                                body: JSON.stringify(formData)
                            }});

                            if (response.ok) {{
                                closeEditUserModalFunc();
                                await loadAdminData();
                                await refreshNavigation();
                                document.getElementById('user-detail-drawer-overlay').style.display = 'none';
                                await openUserDetail(userId);
                            }} else {{
                                const error = await response.json();
                                alert('Failed to update user: ' + (error.detail || 'Unknown error'));
                            }}
                        }} catch (error) {{
                            console.error('Error updating user:', error);
                            alert('Error updating user');
                        }}
                    }});
                }}

                const createChannelModal = document.getElementById('create-channel-modal-overlay');
                const closeCreateChannelModal = document.getElementById('close-create-channel-modal');
                const cancelCreateChannel = document.getElementById('cancel-create-channel');
                const addChannelBtn = document.getElementById('add-channel-btn');
                const createChannelForm = document.getElementById('create-channel-form');

                if (createChannelModal && closeCreateChannelModal && cancelCreateChannel && addChannelBtn && createChannelForm) {{
                    function closeCreateChannelModalFunc() {{
                        createChannelModal.style.display = 'none';
                        createChannelForm.reset();
                    }}

                    addChannelBtn.addEventListener('click', async () => {{
                        const groupSelect = document.getElementById('channel-group');
                        groupSelect.innerHTML = '<option value="">Выберите группу...</option>';
                        allChannelGroups.forEach(group => {{
                            const option = document.createElement('option');
                            option.value = group.id;
                            option.textContent = group.name;
                            groupSelect.appendChild(option);
                        }});

                        const entityTypesList = document.getElementById('channel-entity-types-list');
                        entityTypesList.innerHTML = '';

                        const entityTypesGroup = document.createElement('div');
                        entityTypesGroup.className = 'checkbox-group';
                        entityTypesGroup.innerHTML = '<div class="checkbox-group-title">Выберите нужные типы</div>';

                        if (allEntityTypes.length > 0) {{
                            allEntityTypes.forEach(entityType => {{
                                const label = document.createElement('label');
                                label.className = 'checkbox-label';
                                label.innerHTML = `
                                    <input type="checkbox" class="form-checkbox" value="${{entityType.id}}" checked>
                                    <span>${{escapeHtml(entityType.name)}}</span>
                                `;
                                entityTypesGroup.appendChild(label);
                            }});
                        }}

                        entityTypesList.appendChild(entityTypesGroup);

                        const usersList = document.getElementById('channel-users-list');
                        usersList.innerHTML = '';

                        for (const [deptName, users] of Object.entries(allUsersData)) {{
                            const groupDiv = document.createElement('div');
                            groupDiv.className = 'checkbox-group';
                            groupDiv.innerHTML = '<div class="checkbox-group-title">' + 
                                escapeHtml(deptName.replace(/_/g, ' ').replace(/\\b\\w/g, l => l.toUpperCase())) + '</div>';

                            users.forEach(user => {{
                                const label = document.createElement('label');
                                label.className = 'checkbox-label';
                                label.innerHTML = `
                                    <input type="checkbox" class="form-checkbox" value="${{user.id}}">
                                    <span>${{escapeHtml(user.display_name)}} (${{escapeHtml(user.login)}})</span>
                                `;
                                groupDiv.appendChild(label);
                            }});

                            usersList.appendChild(groupDiv);
                        }}

                        createChannelModal.style.display = 'flex';
                    }});

                    createChannelModal.addEventListener('click', (e) => {{
                        if (e.target === createChannelModal) {{
                            closeCreateChannelModalFunc();
                        }}
                    }});

                    closeCreateChannelModal.addEventListener('click', closeCreateChannelModalFunc);
                    cancelCreateChannel.addEventListener('click', closeCreateChannelModalFunc);

                    createChannelForm.addEventListener('submit', async (e) => {{
                        e.preventDefault();

                        const formData = {{
                            name: document.getElementById('channel-name').value,
                            group: document.getElementById('channel-group').value,
                            allowed_entity_types: [],
                            user_ids: []
                        }};

                        const entityTypeCheckboxes = document.querySelectorAll('#channel-entity-types-list input[type="checkbox"]:checked');
                        entityTypeCheckboxes.forEach(cb => {{
                            formData.allowed_entity_types.push(cb.value);
                        }});

                        const userCheckboxes = document.querySelectorAll('#channel-users-list input[type="checkbox"]:checked');
                        userCheckboxes.forEach(cb => {{
                            formData.user_ids.push(cb.value);
                        }});

                        try {{
                            const response = await fetch('/api/admin/channels', {{
                                method: 'POST',
                                headers: {{
                                    'Content-Type': 'application/json'
                                }},
                                body: JSON.stringify(formData)
                            }});

                            if (response.ok) {{
                                closeCreateChannelModalFunc();
                                await loadAdminData();
                                await refreshNavigation();
                            }} else {{
                                const error = await response.json();
                                alert('Failed to create channel: ' + (error.detail || 'Unknown error'));
                            }}
                        }} catch (error) {{
                            console.error('Error creating channel:', error);
                            alert('Error creating channel');
                        }}
                    }});
                }}
            }}

            function setupDrawers() {{
                const userDrawerOverlay = document.getElementById('user-detail-drawer-overlay');
                const closeUserDrawer = document.getElementById('close-user-drawer');

                if (userDrawerOverlay && closeUserDrawer) {{
                    userDrawerOverlay.addEventListener('click', (e) => {{
                        if (e.target === userDrawerOverlay) {{
                            userDrawerOverlay.style.display = 'none';
                        }}
                    }});

                    closeUserDrawer.addEventListener('click', () => {{
                        userDrawerOverlay.style.display = 'none';
                    }});
                }}

                const channelDrawerOverlay = document.getElementById('channel-detail-drawer-overlay');
                const closeChannelDrawer = document.getElementById('close-channel-drawer');
                const deleteChannelBtn = document.getElementById('delete-channel-btn');

                if (channelDrawerOverlay && closeChannelDrawer && deleteChannelBtn) {{
                    channelDrawerOverlay.addEventListener('click', (e) => {{
                        if (e.target === channelDrawerOverlay) {{
                            channelDrawerOverlay.style.display = 'none';
                        }}
                    }});

                    closeChannelDrawer.addEventListener('click', () => {{
                        channelDrawerOverlay.style.display = 'none';
                    }});

                    deleteChannelBtn.addEventListener('click', async () => {{
                        if (!currentChannelDetail || !confirm('Вы уверены, что хотите удалить выбранный канал? Это безвозвратно удалит всё содержимое канала.')) {{
                            return;
                        }}

                        try {{
                            const response = await fetch('/api/admin/channels/' + currentChannelDetail.id, {{
                                method: 'DELETE'
                            }});

                            if (response.ok) {{
                                channelDrawerOverlay.style.display = 'none';
                                await loadAdminData();
                                await refreshNavigation();
                            }} else {{
                                const error = await response.json();
                                alert('Failed to delete channel: ' + (error.detail || 'Unknown error'));
                            }}
                        }} catch (error) {{
                            console.error('Error deleting channel:', error);
                            alert('Error deleting channel');
                        }}
                    }});
                }}
            }}
            
            async function refreshNavigation() {{
                try {{
                    await loadNavigation();
                }} catch (error) {{
                    console.error('Error refreshing navigation:', error);
                }}
            }}

            function escapeHtml(text) {{
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }}

            document.addEventListener('DOMContentLoaded', () => {{
                try {{
                    loadNavigation();
                    loadAdminData();
                    setupModals();
                    setupDrawers();

                    setTimeout(() => {{
                        toggleSection('users');
                        toggleSection('channels');
                    }}, 100);
                }} catch (error) {{
                    console.error('Error initializing Панель администратора:', error);
                }}
            }});
        </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@router.get('/api/admin/users')
async def get_admin_users(
    user_login=Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> dict[str, list[dict]]:
    """Получение всех пользователей для админ-панели"""
    return await get_all_users_grouped(session)


@router.get('/api/admin/channels')
async def get_admin_channels(
    user_login=Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> dict[str, list[dict]]:
    """Получение всех каналов для админ-панели"""
    return await get_all_channels_grouped(session)


@router.get('/api/admin/departments')
async def get_departments(
    user_login=Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    """Получение всех департаментов"""
    return await get_all_departments()


@router.get('/api/admin/channel-groups')
async def get_channel_groups(
    user_login=Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    """Получение всех групп каналов"""
    return await get_all_channel_groups()


@router.get('/api/admin/users/{user_id}')
async def get_admin_user_detail(
    user_id: str, user_login=Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Получение детальной информации о пользователе"""
    return await get_user_detail(user_id, session)


@router.get('/api/admin/channels/{channel_id}')
async def get_admin_channel_detail(
    channel_id: str, user_login=Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Получение детальной информации о канале"""
    return await get_channel_detail(channel_id, session)


@router.post('/api/admin/users')
async def create_user(user_data: dict, user_login=Depends(require_admin), session: AsyncSession = Depends(get_session)):
    """Создание нового пользователя"""
    try:
        existing_user_query = select(User).where(User.login == user_data['login'])
        existing_user_result = await session.execute(existing_user_query)
        existing_user = existing_user_result.scalar_one_or_none()

        if existing_user:
            raise HTTPException(status_code=400, detail='User with this login already exists')

        try:
            department_enum = DepartmentEnum(user_data['department'])
        except ValueError:
            raise HTTPException(status_code=400, detail='Invalid department value')

        user_dict = {
            'login': user_data['login'],
            'display_name': user_data['display_name'],
            'password_hash': hash_password(user_data['password']),
            'department': department_enum,
            'is_admin': user_data.get('is_admin', False),
        }

        result = await session.execute(insert(User).values(**user_dict))
        await session.flush()  # Получаем ID пользователя без коммита

        user_id = result.inserted_primary_key[0]

        channel_ids = user_data.get('channel_ids', [])
        if channel_ids:
            for channel_id in channel_ids:
                await session.execute(insert(user_channels).values(user_id=user_id, channel_id=channel_id))

        await create_direct_chats_for_new_user(session, user_id)

        await session.commit()

        return {'message': 'User created successfully', 'user_id': str(user_id)}

    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


async def create_direct_chats_for_new_user(session: AsyncSession, new_user_id: Any):
    """
    Создает личные чаты для нового пользователя:
    1. Self-чат с самим собой
    2. Чаты со всеми существующими пользователями
    """
    new_user_query = select(User).where(User.id == new_user_id)
    new_user_result = await session.execute(new_user_query)
    new_user = new_user_result.scalar_one_or_none()

    if not new_user:
        return

    existing_users_query = select(User).where(User.id != new_user_id)
    existing_users_result = await session.execute(existing_users_query)
    existing_users = list(existing_users_result.scalars().all())

    existing_self_chat_query = select(DirectChat).where(
        DirectChat.is_self_chat == True, DirectChat.users.any(User.id == new_user_id)
    )
    existing_self_chat_result = await session.execute(existing_self_chat_query)
    existing_self_chat = existing_self_chat_result.scalar_one_or_none()

    if not existing_self_chat:
        new_self_chat = DirectChat(is_self_chat=True)
        new_self_chat.users = [new_user]
        session.add(new_self_chat)
        await session.flush()

    existing_chats_query = (
        select(DirectChat)
        .where(DirectChat.is_self_chat == False, DirectChat.users.any(User.id == new_user_id))
        .options(selectinload(DirectChat.users))
    )

    existing_chats_result = await session.execute(existing_chats_query)
    existing_chats = list(existing_chats_result.scalars().all())

    existing_pairs = set()
    for chat in existing_chats:
        if len(chat.users) == 2:
            user_ids = sorted([user.id for user in chat.users])
            existing_pairs.add(tuple(user_ids))

    for existing_user in existing_users:
        user_ids = sorted([new_user_id, existing_user.id])
        pair_key = tuple(user_ids)

        if pair_key not in existing_pairs:
            new_chat = DirectChat(is_self_chat=False)
            new_chat.users = [new_user, existing_user]
            session.add(new_chat)

    await session.flush()


@router.patch('/api/admin/users/{user_id}')
async def update_user(
    user_id: str, user_data: dict, user_login=Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    """Обновление пользователя"""
    try:
        user_query = select(User).where(User.id == user_id)
        user_result = await session.execute(user_query)
        user = user_result.scalar_one_or_none()

        if not user:
            raise HTTPException(status_code=404, detail='User not found')

        update_data = {}

        if 'login' in user_data:
            update_data['login'] = user_data['login']

        if 'display_name' in user_data:
            update_data['display_name'] = user_data['display_name']

        if 'department' in user_data:
            try:
                update_data['department'] = DepartmentEnum(user_data['department'])
            except ValueError:
                raise HTTPException(status_code=400, detail='Invalid department value')

        if 'is_admin' in user_data:
            update_data['is_admin'] = user_data['is_admin']

        if 'password' in user_data and user_data['password']:
            update_data['password_hash'] = hash_password(user_data['password'])

        if update_data:
            await session.execute(update(User).where(User.id == user_id).values(**update_data))

        if 'channel_ids' in user_data:
            await session.execute(delete(user_channels).where(user_channels.c.user_id == user_id))

            channel_ids = user_data['channel_ids']
            if channel_ids:
                for channel_id in channel_ids:
                    await session.execute(insert(user_channels).values(user_id=user_id, channel_id=channel_id))

        await session.commit()

        return {'message': 'User updated successfully'}

    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/api/admin/channels')
async def create_channel(
    channel_data: dict, user_login=Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    """Создание нового канала"""
    try:
        try:
            group_enum = ChannelGroupEnum(channel_data['group'])
        except ValueError:
            raise HTTPException(status_code=400, detail='Invalid channel group value')

        allowed_entity_types = channel_data.get('allowed_entity_types', [])
        for entity_type in allowed_entity_types:
            try:
                EntityTypeEnum(entity_type)
            except ValueError:
                raise HTTPException(status_code=400, detail=f'Invalid entity type: {entity_type}')

        channel_dict = {'name': channel_data['name'], 'group': group_enum, 'allowed_entity_types': allowed_entity_types}

        result = await session.execute(insert(Channel).values(**channel_dict))
        await session.commit()

        channel_id = result.inserted_primary_key[0]

        user_ids = channel_data.get('user_ids', [])
        if user_ids:
            for user_id in user_ids:
                await session.execute(insert(user_channels).values(user_id=user_id, channel_id=channel_id))

        await session.commit()

        return {'message': 'Channel created successfully', 'channel_id': str(channel_id)}

    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.delete('/api/admin/channels/{channel_id}')
async def delete_channel(
    channel_id: str, user_login=Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    """Удаление канала и всех связанных сущностей"""
    try:
        await delete_channel_cascade(channel_id, session)
        return {'message': 'Channel deleted successfully'}

    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
