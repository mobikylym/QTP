from datetime import UTC, datetime, timedelta

import jwt
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.app.api.http import (
    admin_panel_view,
    auth,
    chat_channel_view,
    comments,
    entities,
    home,
    messages,
    profile_view,
    task_explorer_view,
)
from src.app.api.http.auth import JWT_ALGORITHM, JWT_EXP, JWT_SECRET
from src.app.api.websocket.ws import manager
from src.app.core.config import settings
from src.app.services.admin_init import ensure_admin_exists
from src.app.services.channels_init import ensure_default_channels
from src.app.services.direct_chats_init import ensure_all_direct_chats_exist
from src.app.storage.database import AsyncSessionLocal, init_models

app = FastAPI(title=settings.APP_NAME)
app.include_router(entities.router)
app.include_router(auth.router)
app.include_router(home.router)
app.include_router(chat_channel_view.router)
app.include_router(profile_view.router)
app.include_router(admin_panel_view.router)
app.include_router(task_explorer_view.router)
app.include_router(messages.router)
app.include_router(comments.router)

REFRESH_THRESHOLD = 1800


@app.on_event('startup')
async def startup_event():
    await init_models()

    async with AsyncSessionLocal() as session:
        await ensure_admin_exists(session)
        await ensure_default_channels(session)
        await ensure_all_direct_chats_exist(session)


@app.websocket('/ws/{room_type}/{room_id}')
async def ws_room(ws: WebSocket, room_type: str, room_id: str):
    await manager.connect(ws, room_type, room_id)
    try:
        while True:
            data = await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws, room_type, room_id)


@app.websocket('/ws/{room_type}/{room_id}/{user_id}')
async def ws_room_user(ws: WebSocket, room_type: str, room_id: str, user_id: str):
    await manager.connect_user(ws, room_type, room_id, user_id)
    try:
        while True:
            data = await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_user(ws, room_type, room_id, user_id)


@app.middleware('http')
async def refresh_session_on_activity(request: Request, call_next):
    """Обновляет сессию за 30 минут до истечения при активности"""
    public_endpoints = [
        '/login',
        '/logout',
        '/session-expired',
        '/logged-out',
    ]

    for endpoint in public_endpoints:
        if request.url.path.startswith(endpoint):
            return await call_next(request)

    session_token = request.cookies.get('session')

    if session_token:
        try:
            options = {'verify_exp': False}
            payload = jwt.decode(session_token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options=options)

            user_login = payload.get('sub')
            exp_timestamp = payload.get('exp')

            if user_login and exp_timestamp:
                current_time = datetime.now(UTC)
                expire_time = datetime.fromtimestamp(exp_timestamp, tz=UTC)
                time_until_expiry = (expire_time - current_time).total_seconds()

                if 0 < time_until_expiry < REFRESH_THRESHOLD:
                    new_token = jwt.encode(
                        {'sub': user_login, 'exp': datetime.now(UTC) + timedelta(seconds=JWT_EXP)},
                        JWT_SECRET,
                        algorithm=JWT_ALGORITHM,
                    )

                    request.state.new_session_token = new_token

        except jwt.InvalidTokenError:
            pass

    response = await call_next(request)

    if hasattr(request.state, 'new_session_token') and response.status_code in [200, 201, 204, 302, 307]:
        response.set_cookie(
            key='session',
            value=request.state.new_session_token,
            httponly=True,
            secure=False,
            samesite='lax',
            max_age=JWT_EXP,
        )

    return response


@app.middleware('http')
async def add_cache_control_headers(request: Request, call_next):
    response = await call_next(request)

    if request.url.path == '/logout':
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'

        response.delete_cookie('session')

    return response


@app.middleware('http')
async def redirect_on_auth_error(request: Request, call_next):
    try:
        response = await call_next(request)

        if response.status_code == 401:
            accept = request.headers.get('accept', '')
            if 'application/json' not in accept and 'text/html' in accept:
                return RedirectResponse(url='/session-expired', status_code=302)

        return response
    except HTTPException as exc:
        if exc.status_code == 401:
            accept = request.headers.get('accept', '')
            if 'application/json' not in accept and 'text/html' in accept:
                return RedirectResponse(url='/session-expired', status_code=302)
        raise exc


@app.get('/session-expired', response_class=HTMLResponse)
async def session_expired_page():
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Сессия истекла</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {
                font-family: Arial, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
                background-color: #f5f5f5;
            }
            .message-box {
                text-align: center;
                padding: 40px;
                background: white;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            .countdown {
                color: #666;
                margin-top: 20px;
                font-size: 0.9em;
            }
        </style>
    </head>
    <body>
        <div class="message-box">
            <h2>Сессия истекла</h2>
            <p>Вам необходимо повторно авторизоваться, чтобы продолжить работать в системе.</p>
            <div class="countdown">Автоматическое перенаправление на страницу авторизации через <span id="countdown">3</span></div>
        </div>

        <script>
            let seconds = 3;
            const countdownElement = document.getElementById('countdown');

            const timer = setInterval(() => {
                seconds--;
                countdownElement.textContent = seconds;

                if (seconds <= 0) {
                    clearInterval(timer);
                    window.location.href = '/login';
                }
            }, 1000);

            setTimeout(() => {
                window.location.href = '/login';
            }, 3000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.get('/.well-known/appspecific/com.chrome.devtools.json')
async def chrome_devtools_config():
    """Пустая конфигурация для Chrome DevTools"""
    return JSONResponse(content={})
