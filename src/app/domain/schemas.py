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


class QuestionCreate(BaseModel):
    channel_id: str
    author_id: str
    title: str
    body: str
    priority: int = 0


class DefectCreate(BaseModel):
    channel_id: str
    author_id: str
    title: str
    body: str
    severity: int
    reproducible: bool


class TaskCreate(BaseModel):
    channel_id: str
    author_id: str
    title: str
    body: str
    severity: int
    reproducible: bool


class EntityBaseOut(BaseModel):
    id: str
    channel_id: str
    author_id: str
    type: EntityTypeEnum
    status: EntityStatusEnum
    title: str
    created_at: datetime
    updated_at: datetime


class QuestionOut(EntityBaseOut):
    body: str
    priority: int
    model_config = ConfigDict(from_attributes=True)


class DefectOut(EntityBaseOut):
    body: str
    severity: int
    reproducible: bool
    model_config = ConfigDict(from_attributes=True)


class TaskOut(EntityBaseOut):
    body: str
    severity: int
    reproducible: bool
    model_config = ConfigDict(from_attributes=True)
