from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.storage.models import DirectChat, User


async def ensure_all_direct_chats_exist(session: AsyncSession):
    """
    Оптимизированная версия с меньшим количеством запросов к БД
    """
    users_query = select(User)
    result = await session.execute(users_query)
    users = list(result.scalars().all())

    if not users:
        return

    user_ids = [user.id for user in users]
    user_dict = {user.id: user for user in users}

    all_chats_query = select(DirectChat).options(selectinload(DirectChat.users))
    all_chats_result = await session.execute(all_chats_query)
    all_chats = list(all_chats_result.scalars().all())

    self_chats = [chat for chat in all_chats if chat.is_self_chat]
    normal_chats = [chat for chat in all_chats if not chat.is_self_chat]

    users_with_self_chat = set()
    for chat in self_chats:
        if len(chat.users) == 1:
            users_with_self_chat.add(chat.users[0].id)

    for user in users:
        if user.id not in users_with_self_chat:
            new_self_chat = DirectChat(is_self_chat=True)
            new_self_chat.users = [user]
            session.add(new_self_chat)

    existing_pairs = set()
    for chat in normal_chats:
        if len(chat.users) == 2:
            user1_id, user2_id = sorted([chat.users[0].id, chat.users[1].id])
            existing_pairs.add((user1_id, user2_id))

    created_count = 0
    for i in range(len(users)):
        for j in range(i + 1, len(users)):
            user1 = users[i]
            user2 = users[j]

            user1_id, user2_id = sorted([user1.id, user2.id])
            pair_key = (user1_id, user2_id)

            if pair_key not in existing_pairs:
                new_chat = DirectChat(is_self_chat=False)
                new_chat.users = [user1, user2]
                session.add(new_chat)
                created_count += 1

    await session.commit()

    print('Direct chats check completed!')
