from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ..storage.models import EntityStatusEnum, EntityTypeEnum


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str | None


class CommentCreate(BaseModel):
    author_id: str
    body: str


class CommentOut(BaseModel):
    id: str
    author_id: str
    body: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EntityCreate(BaseModel):
    channel_id: str
    author_id: str
    type: EntityTypeEnum
    title: str
    body: str | None = None
    priority: int = 0
    due_at: datetime | None = None


class EntityOut(BaseModel):
    id: str
    channel_id: str
    author_id: str
    type: EntityTypeEnum
    status: EntityStatusEnum
    title: str
    body: str | None
    priority: int
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime
    comments: list[CommentOut] = []

    model_config = ConfigDict(from_attributes=True)
