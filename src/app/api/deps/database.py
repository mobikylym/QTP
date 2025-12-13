from collections.abc import AsyncGenerator
from typing import Any

from src.app.storage.database import AsyncSessionLocal


async def get_session() -> AsyncGenerator[Any, Any]:
    async with AsyncSessionLocal() as session:
        yield session
