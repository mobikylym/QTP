import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Table, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .database import Base

user_channels = Table(
    'user_channels',
    Base.metadata,
    Column('user_id', UUID(as_uuid=False), ForeignKey('users.id'), primary_key=True),
    Column('channel_id', UUID(as_uuid=False), ForeignKey('channels.id'), primary_key=True),
)


class EntityTypeEnum(str, enum.Enum):
    question = 'question'
    defect = 'defect'
    task = 'task'
    info = 'info'
    proposal = 'proposal'
    action = 'action'


class QuestionStatus(str, enum.Enum):
    created = 'created'
    answered = 'answered'
    closed = 'closed'


class DefectStatus(str, enum.Enum):
    created = 'created'
    in_progress = 'in_progress'
    ready_for_test = 'ready_for_test'
    in_testing = 'in_testing'
    closed = 'closed'


class TaskStatus(str, enum.Enum):
    created = 'created'
    in_progress = 'in_progress'
    ready_for_test = 'ready_for_test'
    in_testing = 'in_testing'
    closed = 'closed'


class EntityStatusEnum(str, enum.Enum):
    created = 'created'
    discussion = 'discussion'
    in_progress = 'in_progress'
    review = 'review'
    closed = 'closed'


class DepartmentEnum(str, enum.Enum):
    owners = 'owners'
    management = 'management'
    development = 'development'
    qa = 'qa'
    design = 'design'
    sale = 'sale'
    support = 'support'
    accounting = 'accounting'


class ChannelGroupEnum(str, enum.Enum):
    general = 'general'
    management = 'management'
    dev = 'dev'
    qa = 'qa'
    design = 'design'
    sale = 'sale'
    support = 'support'
    accounting = 'accounting'


def gen_uuid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = 'users'
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    login = Column(String(150), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(200), nullable=False)
    department = Column(Enum(DepartmentEnum), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now(UTC))
    is_admin = Column(Boolean, default=False)

    channels = relationship(
        'Channel',
        secondary=user_channels,
        back_populates='users',
        lazy='selectin',
    )


class Channel(Base):
    __tablename__ = 'channels'
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String(200), nullable=False)
    group = Column(Enum(ChannelGroupEnum), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now(UTC))

    users = relationship(
        'User',
        secondary=user_channels,
        back_populates='channels',
        lazy='selectin',
    )

    topics = relationship('Topic', back_populates='channel', cascade='all, delete-orphan')


class Topic(Base):
    __tablename__ = 'topics'
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    channel_id = Column(UUID(as_uuid=False), ForeignKey('channels.id'), nullable=False)
    author_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now(UTC))

    channel = relationship('Channel', back_populates='topics')
    author = relationship('User')


class QuestionEntity(Base):
    __tablename__ = 'entity_questions'

    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), primary_key=True)
    body = Column(Text)
    priority = Column(Integer)

    status = Column(Enum(QuestionStatus), default=QuestionStatus.created)

    entity = relationship('Entity', back_populates='question')


class DefectEntity(Base):
    __tablename__ = 'entity_defects'

    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), primary_key=True)
    body = Column(Text)
    severity = Column(Integer)
    reproducible = Column(Boolean)

    status = Column(Enum(DefectStatus), default=DefectStatus.created)

    entity = relationship('Entity', back_populates='defect')


class TaskEntity(Base):
    __tablename__ = 'entity_tasks'

    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), primary_key=True)
    body = Column(Text)
    severity = Column(Integer)
    reproducible = Column(Boolean)

    status = Column(Enum(TaskStatus), default=TaskStatus.created)

    entity = relationship('Entity', back_populates='task')


class Entity(Base):
    __tablename__ = 'entities'

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    type = Column(Enum(EntityTypeEnum), nullable=False)

    channel_id = Column(UUID(as_uuid=False), ForeignKey('channels.id'))
    author_id = Column(UUID(as_uuid=False), ForeignKey('users.id'))

    title = Column(String(400), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=datetime.now(UTC), onupdate=datetime.now(UTC))

    channel = relationship('Channel')
    author = relationship('User')

    comments = relationship('Comment', back_populates='entity', cascade='all, delete-orphan')
    acks = relationship('Acknowledge', back_populates='entity', cascade='all, delete-orphan')

    question = relationship('QuestionEntity', uselist=False)
    defect = relationship('DefectEntity', uselist=False)
    task = relationship('TaskEntity', uselist=False)


class Comment(Base):
    __tablename__ = 'comments'
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), nullable=False)
    author_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now(UTC))

    entity = relationship('Entity', back_populates='comments')
    author = relationship('User')


class Acknowledge(Base):
    __tablename__ = 'acknowledges'
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), nullable=False)
    user_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)
    acknowledged = Column(Boolean, default=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)

    entity = relationship('Entity', back_populates='acks')
    user = relationship('User')
