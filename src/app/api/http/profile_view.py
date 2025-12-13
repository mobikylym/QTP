import json

from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps.auth import require_auth
from src.app.api.deps.database import get_session
from src.app.api.http.router import router
from src.app.core.security import hash_password
from src.app.storage.models import DepartmentEnum, User


@router.get('/profile/', response_class=HTMLResponse)
async def profile_page(user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)):
    """Страница профиля пользователя"""
    # Получаем текущего пользователя
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    # Генерируем HTML страницу
    return generate_profile_page_html(user)


def generate_profile_page_html(user: User) -> HTMLResponse:
    """Генерирует HTML страницу для профиля пользователя"""
    # Экранируем данные для безопасной вставки в JavaScript
    user_id_escaped = json.dumps(str(user.id))
    user_login_escaped = json.dumps(user.login)
    user_display_name_escaped = json.dumps(user.display_name)
    user_department_escaped = json.dumps(user.department.value)

    # Получаем все доступные отделы
    departments = [dept.value for dept in DepartmentEnum]

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Profile - {user.display_name}</title>
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
                padding: 5px 20px;
                border-bottom: 1px solid #ddd;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            h2 {{
                margin: 10px 0px;
            }}
            .profile-content {{
                flex: 1;
                padding: 30px;
                background-color: #fff;
                max-width: 800px;
                margin: 0 auto;
                width: 100%;
            }}
            .profile-card {{
                background-color: #f9f9f9;
                border-radius: 10px;
                padding: 30px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
            }}
            .profile-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 30px;
                padding-bottom: 20px;
                border-bottom: 1px solid #eee;
            }}
            .profile-title {{
                font-size: 24px;
                font-weight: bold;
                color: #333;
            }}
            .profile-info {{
                display: flex;
                flex-direction: column;
                gap: 20px;
            }}
            .info-row {{
                display: flex;
                align-items: center;
                padding: 15px;
                background-color: white;
                border-radius: 8px;
                border: 1px solid #eee;
            }}
            .info-label {{
                font-weight: bold;
                color: #555;
                min-width: 150px;
            }}
            .info-value {{
                color: #333;
                flex: 1;
            }}
            .edit-btn {{
                padding: 10px 20px;
                background-color: #5865f2;
                color: white;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-weight: bold;
                font-size: 14px;
            }}
            .edit-btn:hover {{
                background-color: #4752c4;
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
            .form-input, .form-select {{
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
            .save-btn {{
                padding: 8px 16px;
                background-color: #57f287;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-weight: bold;
            }}
            .save-btn:hover {{
                background-color: #46d975;
            }}
            .password-hint {{
                font-size: 12px;
                color: #888;
                margin-top: 5px;
            }}
            .alert {{
                padding: 10px;
                border-radius: 4px;
                margin-bottom: 15px;
                display: none;
            }}
            .alert-success {{
                background-color: #d4edda;
                color: #155724;
                border: 1px solid #c3e6cb;
            }}
            .alert-error {{
                background-color: #f8d7da;
                color: #721c24;
                border: 1px solid #f5c6cb;
            }}
            .modal-alert {{
                margin-bottom: 20px;
            }}

            /* Responsive fixes */
            @media (max-width: 600px) {{
                .container {{
                    grid-template-columns: 1fr;
                }}
                .sidebar {{
                    display: none;
                }}
                .profile-content {{
                    padding: 15px;
                }}
                .profile-card {{
                    padding: 20px;
                }}
                .info-row {{
                    flex-direction: column;
                    align-items: flex-start;
                    gap: 5px;
                }}
                .info-label {{
                    min-width: auto;
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
                    <h2>User Profile</h2>
                </div>

                <div class="profile-content">
                    <div class="profile-card">
                        <div class="profile-header">
                            <div class="profile-title">Profile Information</div>
                            <button class="edit-btn" id="edit-profile-btn">Edit Profile</button>
                        </div>
                        
                        <div class="alert" id="page-alert-message"></div>
                        
                        <div class="profile-info">
                            <div class="info-row">
                                <div class="info-label">Login:</div>
                                <div class="info-value" id="user-login">{user.login}</div>
                            </div>
                            <div class="info-row">
                                <div class="info-label">Display Name:</div>
                                <div class="info-value" id="user-display-name">{user.display_name}</div>
                            </div>
                            <div class="info-row">
                                <div class="info-label">Department:</div>
                                <div class="info-value" id="user-department">{user.department.value}</div>
                            </div>
                            <div class="info-row">
                                <div class="info-label">Account Created:</div>
                                <div class="info-value">{user.created_at.strftime('%Y-%m-%d %H:%M') if user.created_at else 'N/A'}</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Модальное окно для редактирования профиля -->
        <div class="modal-overlay" id="edit-modal-overlay">
            <div class="modal" id="edit-modal">
                <div class="modal-header">
                    <div class="modal-title">Edit Profile</div>
                    <button class="close-modal" id="close-modal">&times;</button>
                </div>
                
                <div class="alert modal-alert" id="modal-alert-message"></div>
                
                <form id="edit-profile-form">
                    <div class="form-group">
                        <label class="form-label" for="edit-login">Login</label>
                        <input type="text" class="form-input" id="edit-login" value="{user.login}" required>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="edit-display-name">Display Name</label>
                        <input type="text" class="form-input" id="edit-display-name" value="{user.display_name}" required>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="edit-password">New Password</label>
                        <input type="password" class="form-input" id="edit-password" placeholder="Leave blank to keep current password">
                        <div class="password-hint">Password must be at least 6 characters long</div>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="edit-password-confirm">Confirm New Password</label>
                        <input type="password" class="form-input" id="edit-password-confirm" placeholder="Confirm new password">
                    </div>

                    <div class="modal-footer">
                        <button type="button" class="cancel-btn" id="cancel-modal">Cancel</button>
                        <button type="submit" class="save-btn">Save Changes</button>
                    </div>
                </form>
            </div>
        </div>

        <script>
            // Сохраняем данные пользователя
            const userData = {{
                id: {user_id_escaped},
                login: {user_login_escaped},
                displayName: {user_display_name_escaped},
                department: {user_department_escaped}
            }};

            // Функция загрузки навигации (аналогичная чатам)
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

                    // Profile section (активная)
                    html += '<div id="profile-btn" class="nav-item active">Profile</div>';
                    html += '<div id="notes-btn" class="nav-item">Notes</div>';
                    html += '<div id="task-explorer" onclick="openTaskExplorer()" class="nav-item">Task Explorer</div>';

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

            // Настройка модального окна редактирования
            function setupEditModal() {{
                const modalOverlay = document.getElementById('edit-modal-overlay');
                const closeModal = document.getElementById('close-modal');
                const cancelModal = document.getElementById('cancel-modal');
                const editBtn = document.getElementById('edit-profile-btn');
                const editForm = document.getElementById('edit-profile-form');
                const modalAlert = document.getElementById('modal-alert-message');

                if (!modalOverlay || !closeModal || !cancelModal || !editBtn || !editForm) return;

                // Функция для показа уведомлений в модальном окне
                function showModalAlert(message, type) {{
                    modalAlert.textContent = message;
                    modalAlert.className = 'alert alert-' + type + ' modal-alert';
                    modalAlert.style.display = 'block';
                    
                    // Автоматически скрыть через 5 секунд
                    setTimeout(() => {{
                        modalAlert.style.display = 'none';
                    }}, 5000);
                }}

                // Функция для показа уведомлений на странице
                function showPageAlert(message, type) {{
                    const pageAlert = document.getElementById('page-alert-message');
                    pageAlert.textContent = message;
                    pageAlert.className = 'alert alert-' + type;
                    pageAlert.style.display = 'block';
                    
                    setTimeout(() => {{
                        pageAlert.style.display = 'none';
                    }}, 2000);
                }}

                // Открытие модального окна
                editBtn.addEventListener('click', () => {{
                    // Сбрасываем уведомления при открытии модального окна
                    modalAlert.style.display = 'none';
                    modalOverlay.style.display = 'flex';
                    
                    // Заполняем поля текущими значениями
                    document.getElementById('edit-login').value = userData.login;
                    document.getElementById('edit-display-name').value = userData.displayName;
                    document.getElementById('edit-password').value = '';
                    document.getElementById('edit-password-confirm').value = '';
                }});

                // Закрытие модального окна
                function closeModalFunc() {{
                    modalOverlay.style.display = 'none';
                    editForm.reset();
                }}

                modalOverlay.addEventListener('click', (e) => {{
                    if (e.target === modalOverlay) {{
                        closeModalFunc();
                    }}
                }});

                closeModal.addEventListener('click', closeModalFunc);
                cancelModal.addEventListener('click', closeModalFunc);

                // Отправка формы
                editForm.addEventListener('submit', async (e) => {{
                    e.preventDefault();

                    const login = document.getElementById('edit-login').value.trim();
                    const displayName = document.getElementById('edit-display-name').value.trim();
                    const password = document.getElementById('edit-password').value;
                    const passwordConfirm = document.getElementById('edit-password-confirm').value;

                    // Валидация
                    if (!login || !displayName) {{
                        showModalAlert('Login and display name are required', 'error');
                        return;
                    }}

                    if (password) {{
                        if (password.length < 6) {{
                            showModalAlert('Password must be at least 6 characters long', 'error');
                            return;
                        }}
                        if (password !== passwordConfirm) {{
                            showModalAlert('Passwords do not match', 'error');
                            return;
                        }}
                    }}

                    const formData = {{
                        login: login,
                        display_name: displayName
                    }};

                    // Добавляем пароль только если он был изменен
                    if (password) {{
                        formData.password = password;
                    }}

                    try {{
                        const response = await fetch('/api/user/update', {{
                            method: 'PATCH',
                            headers: {{
                                'Content-Type': 'application/json'
                            }},
                            body: JSON.stringify(formData)
                        }});

                        if (response.ok) {{
                            const result = await response.json();
                            
                            // Показываем успешное сообщение на странице
                            showPageAlert('Profile updated successfully!', 'success');

                            // Обновляем данные на странице
                            document.getElementById('user-login').textContent = result.login || login;
                            document.getElementById('user-display-name').textContent = result.display_name || displayName;

                            // Обновляем данные в userData
                            userData.login = result.login || login;
                            userData.displayName = result.display_name || displayName;

                            // Закрываем модальное окно
                            closeModalFunc();

                        }} else {{
                            const error = await response.json();
                            showModalAlert(error.detail || 'Failed to update profile', 'error');
                        }}
                    }} catch (error) {{
                        console.error('Error updating profile:', error);
                        showModalAlert('Error updating profile', 'error');
                    }}
                }});
            }}

            // Инициализация при загрузке страницы
            document.addEventListener('DOMContentLoaded', () => {{
                try {{
                    loadNavigation();
                    setupEditModal();
                }} catch (error) {{
                    console.error('Error initializing page:', error);
                }}
            }});
        </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@router.patch('/api/user/update')
async def update_user(
    update_data: dict, user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)
):
    """Обновление данных текущего пользователя"""
    # Получаем текущего пользователя
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one()

    # Проверяем уникальность логина, если он изменяется
    if update_data.get('login') and update_data.get('login') != user.login:
        existing_user_query = select(User).where(User.login == update_data.get('login'))
        existing_user_result = await session.execute(existing_user_query)
        existing_user = existing_user_result.scalar_one_or_none()

        if existing_user:
            raise HTTPException(status_code=400, detail='Login already taken')

    # Обновляем данные
    if update_data.get('login'):
        user.login = update_data.get('login')
    if update_data.get('display_name'):
        user.display_name = update_data.get('display_name')

    # Обновляем пароль, если предоставлен
    if update_data.get('password'):
        user.password_hash = hash_password(update_data.get('password'))

    await session.commit()
    await session.refresh(user)

    return {
        'id': str(user.id),
        'login': user.login,
        'display_name': user.display_name,
        'department': user.department.value,
        'is_admin': user.is_admin,
    }
