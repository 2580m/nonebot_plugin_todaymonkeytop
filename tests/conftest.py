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
import pytest_asyncio
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
def _mock_bot_groups_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """将所有 bot 身份组读写重定向到临时目录，测试间自动隔离。"""
    fake_path = tmp_path / "bot_id_groups.json"

    def mocked_path() -> Path:
        fake_path.parent.mkdir(parents=True, exist_ok=True)
        return fake_path

    monkeypatch.setattr(
        MonkeyStore,
        "_bot_groups_path",
        mocked_path,
    )
    return fake_path


@pytest.fixture(autouse=True)
def _mock_mode_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """将协程模式状态读写重定向到临时目录，测试间自动隔离。"""
    fake_path = tmp_path / "mode.json"

    def mocked_path() -> Path:
        fake_path.parent.mkdir(parents=True, exist_ok=True)
        return fake_path

    monkeypatch.setattr(
        MonkeyStore,
        "_mode_path",
        mocked_path,
    )
    return fake_path


@pytest.fixture(autouse=True)
def _reset_store_lock():
    """每个测试用例前重置 store 实例锁，避免跨用例异步锁干扰。"""
    store.lock = asyncio.Lock()
    yield


@pytest_asyncio.fixture
async def db_engine():
    """内存 SQLite 引擎，用于测试 MonkeyStore 的数据库方法。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    from nonebot_plugin_orm import Model

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Model.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine, monkeypatch: pytest.MonkeyPatch):
    """将 store 的 get_session() 指向内存库。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    def _get_session():
        return factory()

    monkeypatch.setattr(
        "nonebot_plugin_todaymonkeytop.__init__.get_session",
        _get_session,
    )
    yield factory
