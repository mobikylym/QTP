from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.security import hash_password
from src.app.storage.models import DepartmentEnum, User


async def ensure_admin_exists(session: AsyncSession):
    query = select(User).where(User.login == 'admin', User.is_admin.is_(True))
    result = await session.execute(query)
    admin = result.scalar_one_or_none()

    if admin:
        return

    new_admin = User(
        login='admin',
        display_name='Administrator',
        is_admin=True,
        department=DepartmentEnum.owners,
        password_hash=hash_password('admin'),
    )

    session.add(new_admin)
    await session.commit()
