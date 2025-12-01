from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    Acknowledge,
    Channel,
    Comment,
    DefectEntity,
    Entity,
    QuestionEntity,
    TaskEntity,
    User,
)


class Repository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # --- Users / Channels (helpful for prototype) ---
    async def get_or_create_user(self, username: str, display_name: str | None = None) -> User:
        q = select(User).where(User.login == username)
        r = await self.session.execute(q)
        user = r.scalar_one_or_none()
        if user:
            return user
        user = User(login=username, display_name=display_name)
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_channel(self, channel_id: str) -> Channel | None:
        q = select(Channel).where(Channel.id == channel_id)
        r = await self.session.execute(q)
        return r.scalar_one_or_none()

    async def create_channel(self, name: str) -> Channel:
        ch = Channel(name=name)
        self.session.add(ch)
        await self.session.flush()
        return ch

    # --- Entities ---
    async def create_entity(self, entity: Entity) -> Entity:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def get_entity(self, entity_id: str) -> Entity | None:
        q = select(Entity).where(Entity.id == entity_id)
        r = await self.session.execute(q)
        return r.scalar_one_or_none()

    async def list_entities_for_channel(self, channel_id: str, limit=50, offset=0):
        q = (
            select(Entity)
            .where(Entity.channel_id == channel_id)
            .options(
                selectinload(Entity.question),
                selectinload(Entity.defect),
                selectinload(Entity.task),
            )
            .order_by(Entity.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        r = await self.session.execute(q)
        return r.scalars().all()

    # --- Comments ---
    async def add_comment(self, comment: Comment) -> Comment:
        self.session.add(comment)
        await self.session.flush()
        return comment

    # --- Acks ---
    async def acknowledge(self, entity_id: str, user_id: str):
        # create or update ack
        q = select(Acknowledge).where(Acknowledge.entity_id == entity_id, Acknowledge.user_id == user_id)
        r = await self.session.execute(q)
        ack = r.scalar_one_or_none()
        if ack:
            ack.acknowledged = True
            ack.acknowledged_at = datetime.now(UTC)
        else:
            ack = Acknowledge(
                entity_id=entity_id, user_id=user_id, acknowledged=True, acknowledged_at=datetime.now(UTC)
            )
            self.session.add(ack)
        await self.session.flush()
        return ack

    async def get_question(self, entity_id: str) -> QuestionEntity | None:
        q = select(QuestionEntity).where(QuestionEntity.entity_id == entity_id)
        r = await self.session.execute(q)
        return r.scalar_one_or_none()

    async def get_defect(self, entity_id: str) -> DefectEntity | None:
        q = select(DefectEntity).where(DefectEntity.entity_id == entity_id)
        r = await self.session.execute(q)
        return r.scalar_one_or_none()

    async def get_task(self, entity_id: str) -> TaskEntity | None:
        q = select(TaskEntity).where(TaskEntity.entity_id == entity_id)
        r = await self.session.execute(q)
        return r.scalar_one_or_none()

    async def update_question_status(self, entity_id: str, new_status):
        q = (
            update(QuestionEntity)
            .where(QuestionEntity.entity_id == entity_id)
            .values(status=new_status)
            .execution_options(synchronize_session='fetch')
        )
        await self.session.execute(q)
        await self.session.flush()

    async def update_defect_status(self, entity_id: str, new_status):
        q = (
            update(DefectEntity)
            .where(DefectEntity.entity_id == entity_id)
            .values(status=new_status)
            .execution_options(synchronize_session='fetch')
        )
        await self.session.execute(q)
        await self.session.flush()

    async def update_task_status(self, entity_id: str, new_status):
        q = (
            update(TaskEntity)
            .where(TaskEntity.entity_id == entity_id)
            .values(status=new_status)
            .execution_options(synchronize_session='fetch')
        )
        await self.session.execute(q)
        await self.session.flush()
