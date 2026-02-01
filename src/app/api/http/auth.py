from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps.database import get_session
from src.app.api.http.router import router
from src.app.storage.models import User

pwd = CryptContext(schemes=['bcrypt'], deprecated='auto')
JWT_SECRET = 'change_me'
JWT_ALGORITHM = 'HS256'
JWT_EXP = 3600


@router.get('/', response_class=HTMLResponse)
async def default_page():
    response = RedirectResponse(url='/login', status_code=302)
    return response


@router.get('/login', response_class=HTMLResponse)
async def login_page():
    return """
    <html>
      <head>
        <title>QTP</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
          body {
            display: grid;
            place-items: center;
            height: 100vh;
            font-family: Arial, sans-serif;
            background-color: #f0f2f5;
          }
          .login-container {
            display: grid;
            gap: 15px;
            padding: 30px;
            background-color: white;
            border-radius: 10px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            width: 300px;
          }
          .login-container form {
            display: grid;
            gap: 10px;
          }
          label { font-weight: bold; }
          input {
            padding: 8px;
            border: 1px solid #ccc;
            border-radius: 5px;
            width: 100%;
          }
          button {
            padding: 10px;
            border: none;
            border-radius: 5px;
            background-color: #007bff;
            color: white;
            font-weight: bold;
            cursor: pointer;
          }
          button:hover { background-color: #0056b3; }
          .toast {
            visibility: hidden;
            min-width: 200px;
            background-color: #f44336;
            color: white;
            text-align: center;
            border-radius: 5px;
            padding: 10px;
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 1;
            opacity: 0;
            transition: opacity 0.5s, visibility 0.5s;
          }
          .toast.show {
            visibility: visible;
            opacity: 1;
          }
          .toast .close {
            margin-left: 10px;
            cursor: pointer;
            font-weight: bold;
          }
        </style>
      </head>
      <body>
        <div class="login-container">
          <form id="loginForm">
            <label>Имя пользователя</label>
            <input type="text" name="username" required>
            <label>Пароль</label>
            <input type="password" name="password" required>
            <button type="submit">Войти</button>
          </form>
        </div>
        <div id="toast" class="toast">
          <span id="toastMessage"></span>
          <span class="close" onclick="this.parentElement.style.display='none';">&times;</span>
        </div>
        <script>
          const form = document.getElementById('loginForm');
          const toast = document.getElementById('toast');
          const toastMessage = document.getElementById('toastMessage');

          form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(form);
            const data = new URLSearchParams(formData);

            const response = await fetch('/login', {
              method: 'POST',
              body: data
            });

            if (response.redirected) {
              window.location.href = response.url;
            } else if (!response.ok) {
              const msg = await response.text();
              toastMessage.textContent = msg || 'Некорректный логин или пароль';
              toast.classList.add('show');
              setTimeout(() => toast.classList.remove('show'), 3000);
            }
          });
        </script>
      </body>
    </html>
    """


@router.post('/login')
async def login_submit(
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    q = select(User).where(User.login == username)
    r = await session.execute(q)
    user = r.scalar_one_or_none()

    if not user or not pwd.verify(password, user.password_hash):
        return HTMLResponse('Некорректный логин или пароль', status_code=401)

    token = jwt.encode(
        {'sub': user.login, 'exp': datetime.now(UTC) + timedelta(seconds=JWT_EXP)}, JWT_SECRET, algorithm=JWT_ALGORITHM
    )

    response = RedirectResponse(url='/task-explorer/', status_code=302)
    response.set_cookie('session', token, httponly=True, secure=False, samesite='lax', max_age=JWT_EXP)
    return response


@router.post('/logout')
async def logout(response: Response):
    """Выход из системы - удаление сессионной куки"""
    response.delete_cookie('session')
    return RedirectResponse(url='/logged-out', status_code=302)


@router.get('/logged-out', response_class=HTMLResponse)
async def logged_out_page():
    """Страница с сообщением об успешном выходе"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Logged Out</title>
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
            .button {
                display: inline-block;
                margin-top: 20px;
                padding: 10px 20px;
                background-color: #007bff;
                color: white;
                text-decoration: none;
                border-radius: 5px;
            }
            .button:hover {
                background-color: #0056b3;
            }
        </style>
    </head>
    <body>
        <div class="message-box">
            <h2>Выход из системы успешен</h2>
            <p>Вы были успешно разлогинены.</p>
            <div class="countdown">Перенаправление на страницу авторизации через <span id="countdown">3</span></div>
            <a href="/login" class="button">Перейти на страницу авторизации</a>
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
