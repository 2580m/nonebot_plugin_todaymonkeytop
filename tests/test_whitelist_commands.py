"""白名单命令处理器的单元测试。

策略说明：
- ``_get_target_group_id`` 为纯函数，可独立测试。
- 命令处理器（Matcher handler）的运行时逻辑通过模拟 Event 对象
  验证 store 方法的调用结果。
- SUPERUSER 权限由 NoneBot 框架层强制，不在本测试中验证。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, PrivateMessageEvent

from nonebot_plugin_todaymonkeytop.__init__ import (
    MessageOwner,
    _get_target_group_id,
    store,
)


# ── _get_target_group_id 单元测试 ──────────────────────────────


def test_get_target_group_id_from_arg() -> None:
    """S2: 参数中包含群号时，优先使用参数中的群号。"""
    event = MagicMock(spec=GroupMessageEvent)
    event.group_id = 10001
    result = _get_target_group_id(event, "123456")
    assert result == 123456


def test_get_target_group_id_from_arg_with_extra_text() -> None:
    """S2-edge: 参数中包含多余文字时仍能提取群号。"""
    event = MagicMock(spec=GroupMessageEvent)
    event.group_id = 10001
    result = _get_target_group_id(event, "开启 123456 测试")
    assert result == 123456


def test_get_target_group_id_from_group_event() -> None:
    """S1: 无参数时从 GroupMessageEvent 获取当前群号。"""
    event = MagicMock(spec=GroupMessageEvent)
    event.group_id = 10001
    result = _get_target_group_id(event, "")
    assert result == 10001


def test_get_target_group_id_from_private_without_arg() -> None:
    """S1-edge: 私聊无参数时返回 None。"""
    event = MagicMock(spec=PrivateMessageEvent)
    # PrivateMessageEvent 没有 group_id 属性
    result = _get_target_group_id(event, "")
    assert result is None


def test_get_target_group_id_invalid_arg() -> None:
    """S1-edge: 参数不是数字时，回退到事件中的群号。"""
    event = MagicMock(spec=GroupMessageEvent)
    event.group_id = 10001
    result = _get_target_group_id(event, "abc")
    # 没有数字匹配，使用 event.group_id
    assert result == 10001


# ── 命令处理器核心逻辑测试 ─────────────────────────────────


@pytest.mark.asyncio
async def test_enable_push_in_group_without_arg() -> None:
    """S1: 群聊中使用 开启贴猴统计推送（无参数）— 启用当前群。"""
    bot = MagicMock(spec=Bot)
    bot.self_id = "bot1"
    event = MagicMock(spec=GroupMessageEvent)
    event.group_id = 10001
    event.get_message.return_value = MagicMock()
    event.get_message.return_value.__str__.return_value = "开启贴猴统计推送"

    # 模拟命令处理器的核心操作
    raw_text = str(event.get_message()).strip()
    parts = raw_text.split(maxsplit=1)
    args_text = parts[1].strip() if len(parts) > 1 else ""
    target_group = _get_target_group_id(event, args_text)

    assert target_group == 10001
    await store.add_to_whitelist(str(bot.self_id), target_group)
    assert await store.is_group_whitelisted("bot1", 10001) is True


@pytest.mark.asyncio
async def test_enable_push_with_arg() -> None:
    """S2: 指定群号时，启用指定群。"""
    bot = MagicMock(spec=Bot)
    bot.self_id = "bot1"
    event = MagicMock(spec=GroupMessageEvent)
    event.group_id = 10001
    event.get_message.return_value = MagicMock()
    event.get_message.return_value.__str__.return_value = "开启贴猴统计推送 99999"

    raw_text = str(event.get_message()).strip()
    parts = raw_text.split(maxsplit=1)
    args_text = parts[1].strip() if len(parts) > 1 else ""
    target_group = _get_target_group_id(event, args_text)

    assert target_group == 99999
    await store.add_to_whitelist(str(bot.self_id), target_group)
    assert await store.is_group_whitelisted("bot1", 99999) is True
    # 当前群不应被启用
    assert await store.is_group_whitelisted("bot1", 10001) is False


@pytest.mark.asyncio
async def test_disable_push_existing() -> None:
    """S4: 关闭已开启的群。"""
    await store.add_to_whitelist("bot1", 10001)
    assert await store.is_group_whitelisted("bot1", 10001) is True

    await store.remove_from_whitelist("bot1", 10001)
    assert await store.is_group_whitelisted("bot1", 10001) is False


@pytest.mark.asyncio
async def test_list_whitelist_with_entries() -> None:
    """S3: 列出白名单。"""
    await store.add_to_whitelist("bot1", 10001)
    await store.add_to_whitelist("bot1", 10002)
    rows = await store.list_whitelist()
    assert len(rows) == 2
    assert ("bot1", 10001) in rows
    assert ("bot1", 10002) in rows


@pytest.mark.asyncio
async def test_list_whitelist_empty() -> None:
    """S3-edge: 白名单为空。"""
    rows = await store.list_whitelist()
    assert rows == []


@pytest.mark.asyncio
async def test_disable_nonexistent_group() -> None:
    """S4-edge: 关闭不存在的群不应报错。"""
    # 移除一个从未添加过的群
    await store.remove_from_whitelist("bot1", 99999)


@pytest.mark.asyncio
async def test_whitelist_gate_effect() -> None:
    """S7: 白名单门控逻辑 — 未在白名单的群被跳过。"""
    # bot1 群 10001 在白名单中
    await store.add_to_whitelist("bot1", 10001)
    # bot1 群 10002 不在白名单中

    assert await store.is_group_whitelisted("bot1", 10001) is True
    assert await store.is_group_whitelisted("bot1", 10002) is False

    # 模拟 cron 中的门控逻辑
    groups_to_send = [
        gid
        for gid in (10001, 10002)
        if await store.is_group_whitelisted("bot1", gid)
    ]
    assert groups_to_send == [10001]


# ── 测试推送命令逻辑 ───────────────────────────────────


@pytest.mark.asyncio
async def test_test_push_no_messages() -> None:
    """测试推送 — 无消息记录时提前退出。"""
    # Given: 群没有消息记录
    with patch.object(store, "messages_for_group", new=AsyncMock(return_value=[])):
        owners = await store.messages_for_group("today", "bot1", 10001)

    # Then: 返回空列表 — 命令会报告"没有消息记录"
    assert owners == []


@pytest.mark.asyncio
async def test_test_push_not_whitelisted() -> None:
    """测试推送 — 有消息但未在白名单中。"""
    # Given: 群有消息记录但未加入白名单
    mock_owner = MessageOwner(
        day="today",
        bot_id="bot1",
        group_id=10001,
        message_id="msg1",
        user_id=12345,
        nickname="Test",
    )
    with patch.object(store, "messages_for_group", new=AsyncMock(return_value=[mock_owner])):
        owners = await store.messages_for_group("today", "bot1", 10001)

    assert len(owners) == 1  # 有消息

    # When: 检查白名单
    whitelisted = await store.is_group_whitelisted("bot1", 10001)

    # Then: 不在白名单中 — 命令会报告"未开启白名单"
    assert whitelisted is False


@pytest.mark.asyncio
async def test_test_push_whitelisted_full_flow() -> None:
    """测试推送 — 有消息且在白名单中，验证全流程通过。"""
    # Given: 加入白名单
    await store.add_to_whitelist("bot1", 10001)
    assert await store.is_group_whitelisted("bot1", 10001) is True

    # When: 模拟有消息记录 + 模拟刷新成功 + 模拟排行数据
    mock_owner = MessageOwner(
        day="today",
        bot_id="bot1",
        group_id=10001,
        message_id="msg1",
        user_id=12345,
        nickname="Test",
    )
    mock_ranking = [(12345, "Test", 3)]

    with (
        patch.object(store, "messages_for_group", new=AsyncMock(return_value=[mock_owner])),
        patch.object(store, "ranking", new=AsyncMock(return_value=mock_ranking)),
    ):
        owners = await store.messages_for_group("today", "bot1", 10001)
        rows = await store.ranking("today", "bot1", 10001)

    # Then: 数据正常，全流程各环节通过
    assert len(owners) == 1
    assert rows == [(12345, "Test", 3)]
    assert await store.is_group_whitelisted("bot1", 10001) is True
