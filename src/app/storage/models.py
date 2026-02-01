import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import ARRAY, Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Table, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declared_attr, foreign, relationship
from sqlalchemy.sql import func

from .database import Base


def utc_now():
    """Функция для получения текущего времени в UTC"""
    return datetime.now(UTC)


def gen_uuid():
    return str(uuid.uuid4())


class EntityTypeEnum(str, enum.Enum):
    question = 'question'
    defect = 'defect'
    task = 'task'
    info = 'info'
    proposal = 'proposal'
    action_point = 'action_point'


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


class ProposalStatus(str, enum.Enum):
    created = 'created'
    discussed = 'discussed'
    accepted = 'accepted'
    rejected = 'rejected'


class ActionPointStatus(str, enum.Enum):
    created = 'created'
    in_progress = 'in_progress'
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


user_channels = Table(
    'user_channels',
    Base.metadata,
    Column('user_id', UUID(as_uuid=False), ForeignKey('users.id'), primary_key=True),
    Column('channel_id', UUID(as_uuid=False), ForeignKey('channels.id'), primary_key=True),
)


class User(Base):
    __tablename__ = 'users'

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    login = Column(String(150), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(200), nullable=False)
    department = Column(Enum(DepartmentEnum), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    is_admin = Column(Boolean, default=False)

    channels = relationship(
        'Channel',
        secondary=user_channels,
        back_populates='users',
        lazy='selectin',
    )

    direct_chats = relationship(
        'DirectChat',
        secondary='direct_chat_users',
        back_populates='users',
        lazy='selectin',
    )


class Channel(Base):
    __tablename__ = 'channels'

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String(200), nullable=False)
    group = Column(Enum(ChannelGroupEnum), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    allowed_entity_types = Column(ARRAY(String), nullable=False, default=list)

    users = relationship(
        'User',
        secondary=user_channels,
        back_populates='channels',
        lazy='selectin',
    )

    entities = relationship('Entity', back_populates='channel')

    topics = relationship('Topic', back_populates='channel')


direct_chat_users = Table(
    'direct_chat_users',
    Base.metadata,
    Column('chat_id', UUID(as_uuid=False), ForeignKey('direct_chats.id'), primary_key=True),
    Column('user_id', UUID(as_uuid=False), ForeignKey('users.id'), primary_key=True),
)


class DirectChat(Base):
    __tablename__ = 'direct_chats'

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    is_self_chat = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    users = relationship(
        'User',
        secondary=direct_chat_users,
        back_populates='direct_chats',
        lazy='selectin',
    )

    entities = relationship('Entity', back_populates='direct_chat')
    topics = relationship('Topic', back_populates='direct_chat')


class BaseThreadMixin:
    @declared_attr
    def id(self):
        return Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)

    @declared_attr
    def created_at(self):
        return Column(DateTime(timezone=True), default=utc_now)

    @declared_attr
    def author_id(self):
        return Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)

    @declared_attr
    def comments(self):
        return relationship(
            'Comment',
            primaryjoin=lambda: foreign(Comment.thread_id) == self.id,
            back_populates='entity_thread' if self.__name__ == 'Entity' else None,
            viewonly=True,
            cascade='all, delete-orphan',
        )

    @declared_attr
    def author(self):
        return relationship('User')


class Topic(Base, BaseThreadMixin):
    __tablename__ = 'topics'

    text = Column(Text, nullable=False)

    channel_id = Column(UUID(as_uuid=False), ForeignKey('channels.id'), nullable=True)
    direct_chat_id = Column(UUID(as_uuid=False), ForeignKey('direct_chats.id'), nullable=True)

    channel = relationship('Channel', back_populates='topics')
    direct_chat = relationship('DirectChat', back_populates='topics')

    comments = relationship('Comment', primaryjoin=lambda: foreign(Comment.thread_id) == Topic.id, viewonly=True)


class Entity(Base, BaseThreadMixin):
    __tablename__ = 'entities'

    type = Column(Enum(EntityTypeEnum), nullable=False)
    title = Column(String(400), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=func.datetime.now(UTC))

    channel_id = Column(UUID(as_uuid=False), ForeignKey('channels.id'), nullable=True)
    direct_chat_id = Column(UUID(as_uuid=False), ForeignKey('direct_chats.id'), nullable=True)

    channel = relationship('Channel', back_populates='entities')
    direct_chat = relationship('DirectChat', back_populates='entities')

    question = relationship('QuestionEntity', uselist=False)
    defect = relationship('DefectEntity', uselist=False)
    task = relationship('TaskEntity', uselist=False)
    info = relationship('InfoEntity', uselist=False)
    proposal = relationship('ProposalEntity', uselist=False)
    action_point = relationship('ActionPointEntity', uselist=False)


class QuestionEntity(Base):
    __tablename__ = 'entity_questions'

    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), primary_key=True)

    body = Column(Text)
    priority = Column(Integer)
    status = Column(Enum(QuestionStatus), default=QuestionStatus.created)
    deadline = Column(DateTime(timezone=True), nullable=True)

    entity = relationship('Entity', back_populates='question')


class DefectEntity(Base):
    __tablename__ = 'entity_defects'

    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), primary_key=True)

    body = Column(Text)
    severity = Column(Integer)
    reproducible = Column(Boolean)
    status = Column(Enum(DefectStatus), default=DefectStatus.created)
    deadline = Column(DateTime(timezone=True), nullable=True)

    executor_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=True)
    qa_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=True)

    executor = relationship('User', foreign_keys=[executor_id])
    qa = relationship('User', foreign_keys=[qa_id])

    entity = relationship('Entity', back_populates='defect')


class TaskEntity(Base):
    __tablename__ = 'entity_tasks'

    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), primary_key=True)

    body = Column(Text)
    severity = Column(Integer)
    reproducible = Column(Boolean)
    status = Column(Enum(TaskStatus), default=TaskStatus.created)
    deadline = Column(DateTime(timezone=True), nullable=True)

    executor_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=True)
    qa_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=True)

    executor = relationship('User', foreign_keys=[executor_id])
    qa = relationship('User', foreign_keys=[qa_id])

    entity = relationship('Entity', back_populates='task')


class InfoEntity(Base):
    __tablename__ = 'entity_info'

    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), primary_key=True)

    body = Column(Text)
    deadline = Column(DateTime(timezone=True), nullable=True)

    required_users = relationship('InfoRequiredUser', cascade='all, delete-orphan', back_populates='info')

    entity = relationship('Entity', back_populates='info')


class InfoRequiredUser(Base):
    __tablename__ = 'info_required_users'

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    info_id = Column(UUID(as_uuid=False), ForeignKey('entity_info.entity_id'), nullable=False)
    user_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)

    info = relationship('InfoEntity', back_populates='required_users')
    user = relationship('User')


class ProposalEntity(Base):
    __tablename__ = 'entity_proposals'

    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), primary_key=True)

    body = Column(Text)
    priority = Column(Integer)
    status = Column(Enum(ProposalStatus), default=ProposalStatus.created)

    entity = relationship('Entity', back_populates='proposal')


class ActionPointEntity(Base):
    __tablename__ = 'entity_action_points'

    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), primary_key=True)

    body = Column(Text)
    priority = Column(Integer)
    status = Column(Enum(ActionPointStatus), default=ActionPointStatus.created)
    deadline = Column(DateTime(timezone=True), nullable=True)

    executor_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=True)

    entity = relationship('Entity', back_populates='action_point')

    executor = relationship('User', foreign_keys=[executor_id])


class Comment(Base):
    __tablename__ = 'comments'

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    thread_id = Column(UUID(as_uuid=False), nullable=False)
    author_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)

    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    author = relationship('User')

    entity_thread = relationship(
        'Entity', primaryjoin=lambda: foreign(Comment.thread_id) == Entity.id, back_populates='comments', viewonly=True
    )

    topic_thread = relationship('Topic', primaryjoin=lambda: foreign(Comment.thread_id) == Topic.id, viewonly=True)


class Acknowledge(Base):
    __tablename__ = 'acknowledges'

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    entity_id = Column(UUID(as_uuid=False), ForeignKey('entities.id'), nullable=False)
    user_id = Column(UUID(as_uuid=False), ForeignKey('users.id'), nullable=False)

    acknowledged = Column(Boolean, default=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)

    entity = relationship('Entity')
    user = relationship('User')
