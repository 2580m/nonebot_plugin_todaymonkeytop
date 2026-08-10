"""协程模式（静默模式）状态存储的单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nonebot_plugin_todaymonkeytop.__init__ import (
    MonkeyStore,
    _coop_mode_gate,
    store,
)


@pytest.mark.asyncio
async def test_mode_default_off() -> None:
    """S1-edge: 默认模式为关闭（正常响应命令）。"""
    assert await store.is_coop_mode_enabled() is False


@pytest.mark.asyncio
async def test_toggle_on_and_off() -> None:
    """S1: 开启后再关闭，状态正确切换。"""
    await store.set_coop_mode(True)
    assert await store.is_coop_mode_enabled() is True

    await store.set_coop_mode(False)
    assert await store.is_coop_mode_enabled() is False


@pytest.mark.asyncio
async def test_mode_persisted_across_store_reload() -> None:
    """S2: 状态持久化到 JSON 文件，重新加载后仍为开启。"""
    await store.set_coop_mode(True)

    # 模拟重新加载：重新从文件读取（直接调内部加载方法）
    assert MonkeyStore._load_mode() is True


@pytest.mark.asyncio
async def test_gate_silent_when_enabled() -> None:
    """S3: 协程模式开启时，命令被静默拦截——调用 stop() 而非 finish()。"""
    await store.set_coop_mode(True)
    matcher = MagicMock()
    matcher.stop = AsyncMock()
    matcher.finish = AsyncMock()

    intercepted = await _coop_mode_gate(matcher, "今日猴榜")

    assert intercepted is True
    matcher.stop.assert_awaited_once()  # 静默终止
    matcher.finish.assert_not_awaited()  # 不发送任何消息


@pytest.mark.asyncio
async def test_gate_passes_when_disabled() -> None:
    """S3-edge: 协程模式关闭时，命令正常放行。"""
    await store.set_coop_mode(False)
    matcher = MagicMock()
    matcher.stop = AsyncMock()
    matcher.finish = AsyncMock()

    intercepted = await _coop_mode_gate(matcher, "今日猴榜")

    assert intercepted is False
    matcher.stop.assert_not_awaited()
    matcher.finish.assert_not_awaited()
