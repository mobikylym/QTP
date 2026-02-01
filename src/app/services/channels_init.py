from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.storage.models import (
    Channel,
    ChannelGroupEnum,
    DepartmentEnum,
    EntityTypeEnum,
    User,
    user_channels,
)

DEFAULT_CHANNELS = {
    ChannelGroupEnum.general: {
        'channels': {
            'main': [
                EntityTypeEnum.question,
                EntityTypeEnum.defect,
                EntityTypeEnum.task,
                EntityTypeEnum.info,
                EntityTypeEnum.proposal,
                EntityTypeEnum.action_point,
            ],
            'vacation': [
                EntityTypeEnum.question,
                EntityTypeEnum.info,
            ],
            'release-stream': [
                EntityTypeEnum.info,
                EntityTypeEnum.action_point,
            ],
            'social': [
                EntityTypeEnum.question,
                EntityTypeEnum.defect,
                EntityTypeEnum.task,
                EntityTypeEnum.info,
                EntityTypeEnum.proposal,
                EntityTypeEnum.action_point,
            ],
        },
        'departments': [
            DepartmentEnum.owners,
            DepartmentEnum.management,
            DepartmentEnum.development,
            DepartmentEnum.qa,
            DepartmentEnum.design,
            DepartmentEnum.sale,
            DepartmentEnum.support,
            DepartmentEnum.accounting,
        ],
    },
    ChannelGroupEnum.management: {
        'channels': {
            'sprint': [
                EntityTypeEnum.info,
                EntityTypeEnum.question,
                EntityTypeEnum.proposal,
                EntityTypeEnum.action_point,
            ],
            'roadmap': [
                EntityTypeEnum.info,
                EntityTypeEnum.question,
                EntityTypeEnum.proposal,
                EntityTypeEnum.action_point,
            ],
            'tasks': [
                EntityTypeEnum.question,
                EntityTypeEnum.defect,
                EntityTypeEnum.task,
                EntityTypeEnum.info,
                EntityTypeEnum.proposal,
                EntityTypeEnum.action_point,
            ],
        },
        'departments': [
            DepartmentEnum.owners,
            DepartmentEnum.management,
        ],
    },
    ChannelGroupEnum.dev: {
        'channels': {
            'dev-team': list(EntityTypeEnum),
            'backend': list(EntityTypeEnum),
            'frontend': list(EntityTypeEnum),
            'dev-ops': list(EntityTypeEnum),
        },
        'departments': [
            DepartmentEnum.owners,
            DepartmentEnum.management,
            DepartmentEnum.development,
            DepartmentEnum.qa,
            DepartmentEnum.design,
            DepartmentEnum.support,
        ],
    },
    ChannelGroupEnum.qa: {
        'channels': {
            'qa-team': [
                EntityTypeEnum.info,
                EntityTypeEnum.question,
                EntityTypeEnum.proposal,
                EntityTypeEnum.action_point,
            ]
        },
        'departments': [
            DepartmentEnum.owners,
            DepartmentEnum.qa,
        ],
    },
    ChannelGroupEnum.design: {
        'channels': {
            'design-team': [
                EntityTypeEnum.info,
                EntityTypeEnum.question,
                EntityTypeEnum.proposal,
                EntityTypeEnum.action_point,
            ]
        },
        'departments': [
            DepartmentEnum.owners,
            DepartmentEnum.design,
        ],
    },
    ChannelGroupEnum.sale: {
        'channels': {
            'customers-stream': [
                EntityTypeEnum.info,
                EntityTypeEnum.question,
                EntityTypeEnum.proposal,
                EntityTypeEnum.action_point,
            ],
            'sales-management': list(EntityTypeEnum),
        },
        'departments': [
            DepartmentEnum.owners,
            DepartmentEnum.accounting,
            DepartmentEnum.sale,
        ],
    },
    ChannelGroupEnum.support: {
        'channels': {'customers-support': list(EntityTypeEnum)},
        'departments': [
            DepartmentEnum.owners,
            DepartmentEnum.management,
            DepartmentEnum.development,
            DepartmentEnum.qa,
            DepartmentEnum.support,
        ],
    },
    ChannelGroupEnum.accounting: {
        'channels': {
            'metrics': [
                EntityTypeEnum.question,
                EntityTypeEnum.info,
            ],
            'weekly-report': [
                EntityTypeEnum.info,
                EntityTypeEnum.question,
                EntityTypeEnum.proposal,
                EntityTypeEnum.action_point,
            ],
            'questions': [
                EntityTypeEnum.question,
                EntityTypeEnum.info,
            ],
        },
        'departments': [
            DepartmentEnum.owners,
            DepartmentEnum.accounting,
        ],
    },
}


async def ensure_default_channels(session: AsyncSession):
    existing_channels = await session.execute(select(Channel))
    existing_channels = {ch.name: ch for ch in existing_channels.scalars().all()}

    users = await session.execute(select(User))
    users = users.scalars().all()

    users_by_department = {}
    for u in users:
        users_by_department.setdefault(u.department, []).append(u)

    created_channels = []

    for group, cfg in DEFAULT_CHANNELS.items():
        for ch_name, allowed_types in cfg['channels'].items():
            if ch_name in existing_channels:
                continue

            new_ch = Channel(
                name=ch_name,
                group=group,
                allowed_entity_types=[t.value for t in allowed_types],
            )
            session.add(new_ch)
            created_channels.append((new_ch, group, cfg['departments']))

    if created_channels:
        await session.flush()

    for channel, group, allowed_deps in created_channels:
        for dep in allowed_deps:
            for user in users_by_department.get(dep, []):
                stmt = insert(user_channels).values(user_id=user.id, channel_id=channel.id).on_conflict_do_nothing()
                await session.execute(stmt)

    await session.commit()

    print('Default channels check completed!')
