from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.http.entities import get_session
from src.app.api.http.router import router
from src.app.storage.models import User

pwd = CryptContext(schemes=['bcrypt'], deprecated='auto')
JWT_SECRET = 'change_me'
JWT_EXP = 3600


@router.get('/login', response_class=HTMLResponse)
async def login_page():
    return """
    <html>
      <head>
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
            <label>Username</label>
            <input type="text" name="username" required>
            <label>Password</label>
            <input type="password" name="password" required>
            <button type="submit">Login</button>
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
        {'sub': user.login, 'exp': datetime.now(UTC) + timedelta(seconds=JWT_EXP)}, JWT_SECRET, algorithm='HS256'
    )

    response = RedirectResponse(url='/', status_code=302)
    response.set_cookie('session', token, httponly=True, secure=False, samesite='lax', max_age=JWT_EXP)
    return response
