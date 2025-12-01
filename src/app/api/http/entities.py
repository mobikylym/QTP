from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from src.app.api.http.router import router
from src.app.domain import schemas as dto
from src.app.services.entity_service import EntityService
from src.app.storage.database import AsyncSessionLocal
from src.app.storage.models import DefectStatus, QuestionStatus, TaskStatus


async def get_session() -> AsyncGenerator[Any, Any]:
    async with AsyncSessionLocal() as session:
        yield session


@router.get('/health', response_class=JSONResponse)
async def health_check():
    return {'status': 'ok'}


@router.post('/entities/question', response_model=dto.QuestionOut)
async def create_question(payload: dto.QuestionCreate, session: AsyncSession = Depends(get_session)):
    svc = EntityService(session)
    ent = await svc.create_question(payload)
    return dto.QuestionOut.model_validate(ent)


@router.post('/entities/defect', response_model=dto.DefectOut)
async def create_defect(payload: dto.DefectCreate, session: AsyncSession = Depends(get_session)):
    svc = EntityService(session)
    ent = await svc.create_defect(payload)
    return dto.DefectOut.model_validate(ent)


@router.post('/entities/task', response_model=dto.TaskOut)
async def create_defect(payload: dto.TaskCreate, session: AsyncSession = Depends(get_session)):
    svc = EntityService(session)
    ent = await svc.create_task(payload)
    return dto.TaskOut.model_validate(ent)


@router.get('/channels/{channel_id}/entities', response_model=list[dto.QuestionOut | dto.DefectOut | dto.TaskOut])
async def list_entities(channel_id: str, limit: int = 50, offset: int = 0, session=Depends(get_session)):
    svc = EntityService(session)
    return await svc.list_for_channel(channel_id, limit, offset)


@router.post('/entities/{entity_id}/comments', response_model=dto.CommentOut)
async def add_comment(entity_id: str, payload: dto.CommentCreate, session: AsyncSession = Depends(get_session)):
    svc = EntityService(session)
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
async def change_status(
    entity_id: str, new_status: QuestionStatus | DefectStatus | TaskStatus, session: AsyncSession = Depends(get_session)
):
    svc = EntityService(session)
    ent = await svc.repo.get_entity(entity_id)
    if not ent:
        raise HTTPException(status_code=404, detail='entity not found')
    await svc.update_status(entity_id, new_status)
    return {'status': 'ok'}
