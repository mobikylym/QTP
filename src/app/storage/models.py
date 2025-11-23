import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .database import Base


class EntityTypeEnum(str, enum.Enum):
    question = 'question'
    defect = 'defect'
    info = 'info'
    proposal = 'proposal'
    action = 'action'


class EntityStatusEnum(str, enum.Enum):
    created = 'created'
    discussion = 'discussion'
    in_progress = 'in_progress'
    review = 'review'
    closed = 'closed'


def gen_uuid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = 'users'
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    username = Column(String(150), unique=True, nullable=False)
    display_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.now(UTC))


class Channel(Base):
    __tablename__ = 'channels'
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=datetime.now(UTC))


class Entity(Base):
    __tablename__ = 'entities'
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    channel_id = Column(UUID(as_uuid=False), ForeignKey('channels.id'), nullable=False)
    author_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)

    type = Column(Enum(EntityTypeEnum), nullable=False)
    status = Column(Enum(EntityStatusEnum), nullable=False, default=EntityStatusEnum.created)
    title = Column(String(400), nullable=False)
    body = Column(Text, nullable=True)
    priority = Column(Integer, default=0)
    due_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.now(UTC))
    updated_at = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    channel = relationship('Channel', backref='entities')
    author = relationship('User', backref='entities')
    comments = relationship('Comment', back_populates='entity', cascade='all, delete-orphan')
    acks = relationship('Acknowledge', back_populates='entity', cascade='all, delete-orphan')


class Comment(Base):
    __tablename__ = 'comments'
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), nullable=False)
    author_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now(UTC))

    entity = relationship('Entity', back_populates='comments')
    author = relationship('User')


class Acknowledge(Base):
    __tablename__ = 'acknowledges'
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), nullable=False)
    user_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)
    acknowledged = Column(Boolean, default=False)
    acknowledged_at = Column(DateTime, nullable=True)

    entity = relationship('Entity', back_populates='acks')
    user = relationship('User')
