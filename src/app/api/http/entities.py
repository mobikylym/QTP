from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from src.app.domain import schemas as dto
from src.app.services.entity_service import EntityService
from src.app.storage.database import AsyncSessionLocal
from src.app.storage.models import EntityStatusEnum

router = APIRouter()


async def get_session() -> AsyncGenerator[Any, Any]:
    async with AsyncSessionLocal() as session:
        yield session


@router.get('/health', response_class=JSONResponse)
async def health_check():
    return {'status': 'ok'}


@router.post('/entities', response_model=dto.EntityOut)
async def create_entity(payload: dto.EntityCreate, session: AsyncSession = Depends(get_session)):
    svc = EntityService(session)
    # Basic checks: channel exists?
    e = await svc.create_entity(payload)
    # load comments empty etc. Return DTO
    return dto.EntityOut.model_validate(e)


@router.get('/channels/{channel_id}/entities', response_model=list[dto.EntityOut])
async def list_entities(
    channel_id: str, limit: int = 50, offset: int = 0, session: AsyncSession = Depends(get_session)
):
    repo = EntityService(session).repo
    rows = await repo.list_entities_for_channel(channel_id, limit=limit, offset=offset)
    return [dto.EntityOut.model_validate(r) for r in rows]


@router.post('/entities/{entity_id}/comments', response_model=dto.CommentOut)
async def add_comment(entity_id: str, payload: dto.CommentCreate, session: AsyncSession = Depends(get_session)):
    svc = EntityService(session)
    # ensure entity exists
    ent = await svc.repo.get_entity(entity_id)
    if not ent:
        raise HTTPException(status_code=404, detail='entity not found')
    c = await svc.add_comment(entity_id=entity_id, author_id=payload.author_id, body=payload.body)
    return dto.CommentOut.model_validate(c)


@router.post('/entities/{entity_id}/ack')
async def ack_entity(entity_id: str, user_id: str, session: AsyncSession = Depends(get_session)):
    repo = EntityService(session).repo
    await repo.acknowledge(entity_id, user_id)
    await session.commit()
    return {'status': 'acknowledged'}


@router.patch('/entities/{entity_id}/status')
async def change_status(entity_id: str, new_status: EntityStatusEnum, session: AsyncSession = Depends(get_session)):
    svc = EntityService(session)
    ent = await svc.repo.get_entity(entity_id)
    if not ent:
        raise HTTPException(status_code=404, detail='entity not found')
    await svc.change_status(entity_id, new_status)
    return {'status': 'ok'}
