from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import src.app.domain.schemas as dto
from src.app.api.websocket.ws import manager
from src.app.storage.models import (
    Acknowledge,
    Comment,
    DefectEntity,
    Entity,
    EntityTypeEnum,
    QuestionEntity,
    TaskEntity,
)
from src.app.storage.repository import Repository


class EntityService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = Repository(session)

    async def create_question(self, data: dto.QuestionCreate):
        e = Entity(
            type=dto.EntityTypeEnum.question,
            channel_id=data.channel_id,
            author_id=data.author_id,
            title=data.title,
        )
        self.session.add(e)
        await self.session.flush()

        q = QuestionEntity(
            entity_id=e.id,
            body=data.body,
            priority=data.priority,
        )
        self.session.add(q)

        await self.session.commit()
        await self.session.refresh(e)

        await manager.broadcast(
            data.channel_id, {'type': 'entity.created', 'entity': dto.QuestionOut.model_validate(e).model_dump()}
        )
        return dto.QuestionOut.model_validate(e)

    async def create_defect(self, data: dto.DefectCreate):
        e = Entity(
            type=dto.EntityTypeEnum.defect,
            channel_id=data.channel_id,
            author_id=data.author_id,
            title=data.title,
        )
        self.session.add(e)
        await self.session.flush()

        d = DefectEntity(
            entity_id=e.id,
            body=data.body,
            severity=data.severity,
            reproducible=data.reproducible,
        )
        self.session.add(d)

        await self.session.commit()
        await self.session.refresh(e)

        await manager.broadcast(
            data.channel_id, {'type': 'entity.created', 'entity': dto.DefectOut.model_validate(e).model_dump()}
        )
        return dto.DefectOut.model_validate(e)

    async def create_task(self, data: dto.TaskCreate):
        e = Entity(
            type=dto.EntityTypeEnum.task,
            channel_id=data.channel_id,
            author_id=data.author_id,
            title=data.title,
        )
        self.session.add(e)
        await self.session.flush()

        d = TaskEntity(
            entity_id=e.id,
            body=data.body,
            severity=data.severity,
            reproducible=data.reproducible,
        )
        self.session.add(d)

        await self.session.commit()
        await self.session.refresh(e)

        await manager.broadcast(
            data.channel_id, {'type': 'entity.created', 'entity': dto.TaskOut.model_validate(e).model_dump()}
        )
        return dto.TaskOut.model_validate(e)

    async def add_comment(self, entity_id: str, author_id: str, body: str) -> Comment:
        c = Comment(entity_id=entity_id, author_id=author_id, body=body)
        self.session.add(c)
        await self.session.flush()
        return c

    async def acknowledge(self, entity_id: str, user_id: str):
        q = select(Acknowledge).where(
            Acknowledge.entity_id == entity_id,
            Acknowledge.user_id == user_id,
        )
        r = await self.session.execute(q)
        ack = r.scalar_one_or_none()

        if ack:
            ack.acknowledged = True
            ack.acknowledged_at = datetime.now(UTC)
        else:
            ack = Acknowledge(
                entity_id=entity_id,
                user_id=user_id,
                acknowledged=True,
                acknowledged_at=datetime.now(UTC),
            )
            self.session.add(ack)

    async def update_status(self, entity_id: str, new_status):
        base = await self.repo.get_entity(entity_id)
        if base.type == EntityTypeEnum.question:
            await self.repo.update_question_status(entity_id, new_status)
        elif base.type == EntityTypeEnum.defect:
            await self.repo.update_defect_status(entity_id, new_status)
        elif base.type == EntityTypeEnum.task:
            await self.repo.update_task_status(entity_id, new_status)

    async def build_entity(self, base: Entity):
        if base.type == EntityTypeEnum.question:
            ext = base.question
            return dto.QuestionOut(
                id=base.id,
                channel_id=base.channel_id,
                author_id=base.author_id,
                title=base.title,
                created_at=base.created_at,
                updated_at=base.updated_at,
                body=ext.body,
                priority=ext.priority,
                type=base.type,
                status=ext.status,
            )

        if base.type == EntityTypeEnum.defect:
            ext = base.defect
            return dto.DefectOut(
                id=base.id,
                channel_id=base.channel_id,
                author_id=base.author_id,
                title=base.title,
                created_at=base.created_at,
                updated_at=base.updated_at,
                body=ext.body,
                severity=ext.severity,
                reproducible=ext.reproducible,
                type=base.type,
                status=ext.status,
            )

        if base.type == EntityTypeEnum.task:
            ext = base.task
            return dto.TaskOut(
                id=base.id,
                channel_id=base.channel_id,
                author_id=base.author_id,
                title=base.title,
                created_at=base.created_at,
                updated_at=base.updated_at,
                body=ext.body,
                severity=ext.severity,
                reproducible=ext.reproducible,
                type=base.type,
                status=ext.status,
            )

    async def list_for_channel(self, channel_id, limit, offset):
        bases = await self.repo.list_entities_for_channel(channel_id, limit, offset)
        out = []
        for b in bases:
            full = await self.build_entity(b)
            out.append(full)
        return out
