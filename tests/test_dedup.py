"""副 bot 兜底写库机制（延迟写 + 忽略 bot_id 去重）的单元测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from nonebot_plugin_todaymonkeytop.__init__ import MessageOwner, store
from nonebot_plugin_todaymonkeytop.models import (
    MonkeyMessage,
    MonkeyReaction,
)


def _owner(bot_id: str, group_id: int = 10001, message_id: str = "m1") -> MessageOwner:
    return MessageOwner(
        day="2026-08-11",
        bot_id=bot_id,
        group_id=group_id,
        message_id=message_id,
        user_id=12345,
        nickname=f"user-{bot_id}",
    )


@pytest.mark.asyncio
async def test_save_message_dedup_across_bots(db_session) -> None:
    """S1: 同一 (group_id, message_id) 由两个 bot 先后保存，只保留一条记录。"""
    # Given: 主 bot 先写入
    await store.save_message(_owner("bot_main"), 100)
    # When: 副 bot 延迟后写入同一条消息
    await store.save_message(_owner("bot_backup"), 100)

    # Then: 库里只有一条记录
    async with db_session() as session:
        count = (await session.execute(select(func.count()).select_from(MonkeyMessage))).scalar()
        assert count == 1, f"预期 1 条记录，实际 {count} 条"


@pytest.mark.asyncio
async def test_get_message_finds_any_bot_record(db_session) -> None:
    """S2: 副 bot 查询消息归属时，能找到主 bot 写入的记录。"""
    # Given: 只有主 bot 记录过这条消息
    await store.save_message(_owner("bot_main"), 100)

    # When: 副 bot（从未写过这条消息）查询
    owner = await store.get_message("bot_backup", 10001, "m1")

    # Then: 能查到，bot_id 是实际写入者
    assert owner is not None
    assert owner.bot_id == "bot_main"
    assert owner.message_id == "m1"


@pytest.mark.asyncio
async def test_get_message_returns_none_if_absent(db_session) -> None:
    """S2-edge: 完全没有记录时返回 None。"""
    owner = await store.get_message("bot_main", 99999, "nope")
    assert owner is None


@pytest.mark.asyncio
async def test_save_reaction_dedup_across_bots(db_session) -> None:
    """S3: 同一消息的猴榜计数，两个 bot 写入只保留一条。"""
    # Given: 主 bot 保存计数 5
    await store.save_message(_owner("bot_main"), 100)
    await store.save_reaction_count(_owner("bot_main"), 5)
    # When: 副 bot 延迟后保存同一消息计数（更新为 7）
    await store.save_message(_owner("bot_backup"), 100)
    await store.save_reaction_count(_owner("bot_backup"), 7)

    # Then: 只有一条 reaction 记录，且值是最新的 7
    async with db_session() as session:
        rows = (await session.scalars(select(MonkeyReaction))).all()
        assert len(rows) == 1, f"预期 1 条反应记录，实际 {len(rows)} 条"
        assert rows[0].reaction_count == 7
