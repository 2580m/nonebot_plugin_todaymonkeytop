"""测试 fixtures：使用临时文件隔离白名单 JSON 存储。

⚠️ 该 conftest 会在 import 阶段初始化 NoneBot（最小配置），以满足插件模块
   module-level 的 ``require()`` 和 ``get_driver()`` 调用。各测试由
   ``tmp_path`` 自动隔离白名单文件。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import PropertyMock

import nonebot
import pytest
from nonebot import get_driver

# ---- 在导入插件模块前初始化 NoneBot（最小配置） ----
try:
    get_driver()
except ValueError:
    nonebot.init(data_dir="/tmp/test_data")

from nonebot_plugin_todaymonkeytop.__init__ import MonkeyStore, store


@pytest.fixture(autouse=True)
def _mock_whitelist_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """将所有白名单读写重定向到临时目录，测试间自动隔离。"""
    fake_path = tmp_path / "push_list.json"

    def mocked_path() -> Path:
        fake_path.parent.mkdir(parents=True, exist_ok=True)
        return fake_path

    monkeypatch.setattr(
        MonkeyStore,
        "_whitelist_path",
        mocked_path,
    )
    return fake_path


@pytest.fixture(autouse=True)
def _reset_store_lock():
    """每个测试用例前重置 store 实例锁，避免跨用例异步锁干扰。"""
    store.lock = asyncio.Lock()
    yield
