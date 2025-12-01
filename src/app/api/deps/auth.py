import jwt
from fastapi import HTTPException, Request

from src.app.api.http.auth import JWT_SECRET


async def require_auth(request: Request):
    token = request.cookies.get('session')
    if not token:
        raise HTTPException(status_code=401)
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload['sub']
    except Exception:
        raise HTTPException(status_code=401)
