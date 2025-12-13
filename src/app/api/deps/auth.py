import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps.database import get_session
from src.app.api.http.auth import JWT_SECRET
from src.app.storage.models import User


async def require_auth(request: Request):
    token = request.cookies.get('session')
    if not token:
        raise HTTPException(status_code=401)
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload['sub']
    except Exception:
        raise HTTPException(status_code=401)


async def require_admin(user_login: str = Depends(require_auth), session: AsyncSession = Depends(get_session)) -> str:
    """Проверяет, является ли пользователь администратором"""
    user_query = select(User).where(User.login == user_login)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User not found')

    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Admin access required')

    return user_login
