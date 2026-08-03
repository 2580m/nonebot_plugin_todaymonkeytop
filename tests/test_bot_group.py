"""bot 身份组（bot_id 合并查询）存储方法的单元测试。"""

from __future__ import annotations

import pytest

from nonebot_plugin_todaymonkeytop.__init__ import store


@pytest.mark.asyncio
async def test_resolve_single_bot_no_group() -> None:
    """S1-edge: 未注册任何组时，返回仅包含自身的列表。"""
    assert await store.resolve_bot_ids("bot1") == ["bot1"]


@pytest.mark.asyncio
async def test_set_group_and_resolve() -> None:
    """S1: 注册组后，组内任一 bot 查询时返回整个组。"""
    await store.set_bot_group(["bot1", "bot2"])
    assert await store.resolve_bot_ids("bot1") == ["bot1", "bot2"]
    assert await store.resolve_bot_ids("bot2") == ["bot1", "bot2"]
    # 组外的 bot 不受影响
    assert await store.resolve_bot_ids("bot3") == ["bot3"]


@pytest.mark.asyncio
async def test_replace_group_member() -> None:
    """S2: 重新注册时，旧组中的成员移动到新组，旧组不再包含它。"""
    await store.set_bot_group(["bot1", "bot2"])
    await store.set_bot_group(["bot1", "bot3"])
    assert await store.resolve_bot_ids("bot1") == ["bot1", "bot3"]
    assert await store.resolve_bot_ids("bot2") == ["bot2"]
    assert await store.resolve_bot_ids("bot3") == ["bot1", "bot3"]


@pytest.mark.asyncio
async def test_set_group_dedupe_ids() -> None:
    """S2-edge: 注册时重复的 id 会被去重。"""
    await store.set_bot_group(["bot1", "bot1", "bot2"])
    assert await store.resolve_bot_ids("bot1") == ["bot1", "bot2"]


@pytest.mark.asyncio
async def test_multiple_groups_independent() -> None:
    """S3: 多组互不影响。"""
    await store.set_bot_group(["bot1", "bot2"])
    await store.set_bot_group(["bot3", "bot4"])
    assert await store.resolve_bot_ids("bot1") == ["bot1", "bot2"]
    assert await store.resolve_bot_ids("bot3") == ["bot3", "bot4"]
