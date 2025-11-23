from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.websocket.ws import manager
from src.app.domain.schemas import EntityCreate, EntityOut
from src.app.storage.models import Comment, Entity, EntityStatusEnum
from src.app.storage.repository import Repository


class EntityService:
    def __init__(self, session: AsyncSession):
        self.repo = Repository(session)
        self.session = session

    async def create_entity(self, data: EntityCreate) -> Entity:
        entity = Entity(
            channel_id=data.channel_id,
            author_id=data.author_id,
            type=data.type,
            title=data.title,
            body=data.body,
            priority=data.priority,
            due_at=data.due_at,
            status=EntityStatusEnum.created,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        e = await self.repo.create_entity(entity)
        await self.session.commit()

        await manager.broadcast(
            data.channel_id, {'type': 'entity.created', 'entity': EntityOut.model_validate(e).model_dump()}
        )
        return e

    async def add_comment(self, entity_id: str, author_id: str, body: str) -> Comment:
        comment = Comment(entity_id=entity_id, author_id=author_id, body=body, created_at=datetime.now(UTC))
        c = await self.repo.add_comment(comment)
        await self.session.commit()
        return c

    async def change_status(self, entity_id: str, new_status: EntityStatusEnum):
        await self.repo.update_entity_status(entity_id, new_status)
        await self.session.commit()
