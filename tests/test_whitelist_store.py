"""MonkeyStore 推送白名单方法的单元测试。"""

from __future__ import annotations

import pytest

from nonebot_plugin_todaymonkeytop.__init__ import store


@pytest.mark.asyncio
async def test_add_to_whitelist_new_entry() -> None:
    """S1: 添加新群到白名单后，is_group_whitelisted 返回 True。"""
    await store.add_to_whitelist("bot1", 10001)
    assert await store.is_group_whitelisted("bot1", 10001) is True


@pytest.mark.asyncio
async def test_add_to_whitelist_duplicate() -> None:
    """S1-edge: 重复添加同一个群不应抛异常（幂等）。"""
    await store.add_to_whitelist("bot1", 10001)
    # 第二次添加不应报错
    await store.add_to_whitelist("bot1", 10001)
    assert await store.is_group_whitelisted("bot1", 10001) is True


@pytest.mark.asyncio
async def test_is_group_whitelisted_false() -> None:
    """S1-edge: 未添加的群返回 False。"""
    assert await store.is_group_whitelisted("bot1", 99999) is False


@pytest.mark.asyncio
async def test_remove_from_whitelist_existing() -> None:
    """S4: 移除已存在的白名单群。"""
    await store.add_to_whitelist("bot1", 10001)
    assert await store.is_group_whitelisted("bot1", 10001) is True
    await store.remove_from_whitelist("bot1", 10001)
    assert await store.is_group_whitelisted("bot1", 10001) is False


@pytest.mark.asyncio
async def test_remove_from_whitelist_nonexistent() -> None:
    """S4-edge: 移除不存在的群不应抛异常（幂等）。"""
    await store.remove_from_whitelist("bot1", 99999)  # 不应报错


@pytest.mark.asyncio
async def test_list_whitelist_empty() -> None:
    """S3-edge: 白名单为空时返回空列表。"""
    assert await store.list_whitelist() == []


@pytest.mark.asyncio
async def test_list_whitelist_with_entries() -> None:
    """S3: 多个群在白名单中，按顺序列出。"""
    await store.add_to_whitelist("bot1", 10003)
    await store.add_to_whitelist("bot1", 10001)
    await store.add_to_whitelist("bot1", 10002)
    rows = await store.list_whitelist()
    assert rows == [("bot1", 10001), ("bot1", 10002), ("bot1", 10003)]


@pytest.mark.asyncio
async def test_multiple_bots_whitelist() -> None:
    """S7-edge: 不同 bot_id 的白名单互不影响。"""
    await store.add_to_whitelist("bot1", 10001)
    await store.add_to_whitelist("bot2", 10001)
    assert await store.is_group_whitelisted("bot1", 10001) is True
    assert await store.is_group_whitelisted("bot2", 10001) is True
    # 不同 bot 的相同 group_id 互不干扰
    await store.remove_from_whitelist("bot1", 10001)
    assert await store.is_group_whitelisted("bot1", 10001) is False
    assert await store.is_group_whitelisted("bot2", 10001) is True
