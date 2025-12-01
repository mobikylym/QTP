from fastapi import Depends
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps.auth import require_auth
from src.app.api.http.entities import get_session
from src.app.api.http.router import router
from src.app.storage.models import User


@router.get('/', response_class=HTMLResponse)
async def home(user=Depends(require_auth)):
    return """
    <html>
        <head>
          <style>
            body, html {
                margin: 0;
                height: 100%;
                font-family: Arial, sans-serif;
            }
            .container {
                display: grid;
                grid-template-columns: 250px 1fr;
                height: 100vh;
            }
            .sidebar {
                background-color: #2f3136;
                color: white;
                display: flex;
                flex-direction: column;
                padding: 10px;
                gap: 10px;
            }
            .sidebar button, .sidebar .section {
                background: none;
                color: white;
                border: none;
                padding: 10px;
                text-align: left;
                cursor: pointer;
                border-radius: 5px;
            }
            .sidebar button:hover, .sidebar .section:hover {
                background-color: #40444b;
            }
            .content {
                padding: 20px;
                overflow-y: auto;
            }
            .hidden { display: none; }
          </style>
        </head>
        <body>
          <div class="container">
            <div class="sidebar">
              <!-- Админ панель -->
              <button id="admin-btn" class="hidden">Admin Panel</button>
        
              <button id="profile-btn">Profile</button>
              <div class="section" id="task-explorer">Task Explorer</div>
              <div class="section" id="notifications">Notifications</div>
              <div class="section" id="channels">Channels</div>
              <button id="logout-btn">Logout</button>
            </div>
            <div class="content" id="main-content">
              <h2>Welcome!</h2>
              <p>Select a section from the left sidebar.</p>
            </div>
          </div>
        
          <script>
            async function initUser() {
                const res = await fetch('/api/user/info');
                const info = await res.json();
                if (info.is_admin) {
                    document.getElementById('admin-btn').classList.remove('hidden');
                }
              }
              initUser();
            // пример управления вкладками
            document.getElementById('task-explorer').onclick = () => {
                document.getElementById('main-content').innerHTML = '<h2>Task Explorer</h2>';
            };
            document.getElementById('notifications').onclick = () => {
                document.getElementById('main-content').innerHTML = '<h2>Notifications</h2>';
            };
            document.getElementById('channels').onclick = async () => {
                const content = document.getElementById('main-content');
                content.innerHTML = '<h2>Loading channels...</h2>';
                const res = await fetch('/api/user/channels'); // эндпоинт для каналов пользователя
                const channels = await res.json();
                content.innerHTML = '<h2>Channels</h2>' + channels.map(c => `<div>${c.name}</div>`).join('');
            };
          </script>
        </body>
    </html>
    """


@router.get('/api/user/info', response_class=JSONResponse)
async def user_info(user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)):
    q = select(User).where(User.login == user_login)
    result = await session.execute(q)
    user = result.scalar_one()
    return {'is_admin': user.is_admin}


@router.get('/api/user/channels', response_class=JSONResponse)
async def user_channels(user_login=Depends(require_auth), session: AsyncSession = Depends(get_session)):
    q = select(User).where(User.login == user_login)
    result = await session.execute(q)
    user = result.scalar_one()

    # предполагаем, что у User есть relationship channels
    channels = [{'id': ch.id, 'name': ch.name} for ch in user.channels]
    return channels
