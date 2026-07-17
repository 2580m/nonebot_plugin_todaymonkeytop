"""群聊「今日猴榜」插件。

本插件会将当天群消息的 ``message_id -> 发送者`` 映射持久化到 SQLite。NapCat 上报
``group_msg_emoji_like`` 时，插件调用 ``get_emoji_likes`` 获取该消息现在
拥有的完整猴子回应列表，并将数量作为消息快照保存。因此重复事件和撤销回应
都不会导致榜单被重复计数。

依赖：
    - nonebot2 >= 2.5.0
    - nonebot-adapter-onebot
    - nonebot-plugin-apscheduler
    - NapCat（需启用 OneBot 11 的群消息和 notice 事件）
"""

from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TextIO
from zoneinfo import ZoneInfo

import nonebot
from nonebot import logger, on_command, on_message, on_notice, require
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent
from nonebot.permission import SUPERUSER
from nonebot.rule import is_type
from sqlalchemy import delete, func, select

# NoneBot 没有内置定时器；该插件是发布每日榜单所必需的依赖。
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler
require("nonebot_plugin_orm")
from nonebot_plugin_orm import get_session

from .models import MonkeyDailyReport, MonkeyMessage, MonkeyReaction


PLUGIN_NAME = "今日猴榜"
MONKEY_EMOJI_ID = "128053"
TOP_LIMIT = 10
TIME_ZONE = ZoneInfo("Asia/Shanghai")


class _PluginLogger:
    """写入文件 ``data/nonebot_plugin_todaymonkeytop/log.txt`` 的插件专用日志器。

    - 运行时追加写入；调用 ``refresh()`` 后截断文件，仅保留本次调用的日志。
    - 所有等级的方法签名与 NoneBot logger 一致，可无缝替换。
    """

    def __init__(self) -> None:
        self._file: TextIO | None = None
        self._path: Path | None = None

    @property
    def _log_path(self) -> Path:
        if self._path is None:
            try:
                base = Path(nonebot.get_driver().config.data_dir)
            except (AttributeError, KeyError, RuntimeError):
                base = Path("data")
            path = base / "nonebot_plugin_todaymonkeytop"
            path.mkdir(parents=True, exist_ok=True)
            self._path = path / "log.txt"
        return self._path

    def _open(self, mode: str = "a") -> None:
        if self._file is not None and not self._file.closed:
            self._file.close()
        self._file = open(self._log_path, mode, encoding="utf-8")

    def refresh(self) -> None:
        """截断日志文件，开始记录新一次调用的日志。"""
        self._open("w")

    def _write(self, level: str, msg: str, args: tuple[Any, ...] = ()) -> None:
        if self._file is None or self._file.closed:
            self._open("a")
        ts = _now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            text = msg.format(*args) if args else msg
        except (KeyError, IndexError):
            text = msg
        line = f"[{ts}] [{level}] {text}\n"
        try:
            self._file.write(line)
            self._file.flush()
        except OSError:
            pass  # 文件不可写时静默忽略

    def info(self, msg: str, *args: Any) -> None:
        self._write("INFO", msg, args)

    def warning(self, msg: str, *args: Any) -> None:
        self._write("WARNING", msg, args)

    def error(self, msg: str, *args: Any) -> None:
        self._write("ERROR", msg, args)

    def exception(self, msg: str, *args: Any) -> None:
        self._write("ERROR", msg, args)
        tb_text = traceback.format_exc()
        if tb_text and tb_text.strip() not in ("", "NoneType: None"):
            for tb_line in tb_text.rstrip().split("\n"):
                self._write("ERROR", tb_line)

    def debug(self, msg: str, *args: Any) -> None:
        self._write("DEBUG", msg, args)


# 替换 NoneBot logger，所有插件日志写入文件而非控制台
logger = _PluginLogger()  # noqa: F811 — 有意覆盖 import 的 logger


def _now() -> datetime:
    """返回上海时区的当前时间。"""
    return datetime.now(TIME_ZONE)


def _day_from_timestamp(timestamp: Any) -> str:
    """将 OneBot 时间戳转换为上海日期；缺失或异常时使用当前日期。"""
    try:
        return datetime.fromtimestamp(int(timestamp), TIME_ZONE).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return _now().date().isoformat()


def _today() -> str:
    return _now().date().isoformat()


def _display_name(event: GroupMessageEvent) -> str:
    """优先使用群名片，其次 QQ 昵称。"""
    sender = event.sender
    return str(sender.card or sender.nickname or event.user_id)


@dataclass(frozen=True)
class MessageOwner:
    """一条群消息及其归属用户。"""

    day: str
    bot_id: str
    group_id: int
    message_id: str
    user_id: int
    nickname: str


class MonkeyStore:
    """基于 nonebot-plugin-orm 的持久化读写层。"""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        logger.info("todaymonkeytop: 已启用 nonebot-plugin-orm 持久化")

    async def save_message(self, owner: MessageOwner, message_time: int | None) -> None:
        async with self.lock:
            session = get_session()
            async with session.begin():
                record = await session.get(
                    MonkeyMessage,
                    (owner.bot_id, owner.group_id, owner.message_id),
                )
                if record is None:
                    session.add(
                        MonkeyMessage(
                            bot_id=owner.bot_id,
                            group_id=owner.group_id,
                            message_id=owner.message_id,
                            day=owner.day,
                            user_id=owner.user_id,
                            nickname=owner.nickname,
                            message_time=message_time,
                        )
                    )
                else:
                    record.nickname = owner.nickname

    async def get_message(
        self, bot_id: str, group_id: int, message_id: str
    ) -> MessageOwner | None:
        async with self.lock:
            session = get_session()
            async with session.begin():
                record = await session.get(MonkeyMessage, (bot_id, group_id, message_id))
                if record is None:
                    return None
                return MessageOwner(
                    day=record.day,
                    bot_id=record.bot_id,
                    group_id=record.group_id,
                    message_id=record.message_id,
                    user_id=record.user_id,
                    nickname=record.nickname,
                )

    async def messages_for_group(
        self, day: str, bot_id: str, group_id: int
    ) -> list[MessageOwner]:
        async with self.lock:
            session = get_session()
            async with session.begin():
                statement = (
                    select(MonkeyMessage)
                    .where(
                        MonkeyMessage.day == day,
                        MonkeyMessage.bot_id == bot_id,
                        MonkeyMessage.group_id == group_id,
                    )
                    .order_by(MonkeyMessage.message_time, MonkeyMessage.message_id)
                )
                records = (await session.scalars(statement)).all()
                return [
                    MessageOwner(
                        day=record.day,
                        bot_id=record.bot_id,
                        group_id=record.group_id,
                        message_id=record.message_id,
                        user_id=record.user_id,
                        nickname=record.nickname,
                    )
                    for record in records
                ]

    async def save_reaction_count(
        self, owner: MessageOwner, count: int
    ) -> None:
        async with self.lock:
            session = get_session()
            async with session.begin():
                record = await session.get(
                    MonkeyReaction,
                    (owner.bot_id, owner.group_id, owner.message_id, MONKEY_EMOJI_ID),
                )
                if record is None:
                    session.add(
                        MonkeyReaction(
                            bot_id=owner.bot_id,
                            group_id=owner.group_id,
                            message_id=owner.message_id,
                            emoji_id=MONKEY_EMOJI_ID,
                            day=owner.day,
                            reaction_count=count,
                        )
                    )
                else:
                    record.reaction_count = count

    async def ranking(
        self, day: str, bot_id: str, group_id: int
    ) -> list[tuple[int, str, int]]:
        async with self.lock:
            session = get_session()
            async with session.begin():
                total = func.sum(MonkeyReaction.reaction_count)
                statement = (
                    select(
                        MonkeyMessage.user_id,
                        func.max(MonkeyMessage.nickname).label("nickname"),
                        total.label("reaction_count"),
                    )
                    .join(
                        MonkeyReaction,
                        (MonkeyReaction.bot_id == MonkeyMessage.bot_id)
                        & (MonkeyReaction.group_id == MonkeyMessage.group_id)
                        & (MonkeyReaction.message_id == MonkeyMessage.message_id),
                    )
                    .where(
                        MonkeyMessage.day == day,
                        MonkeyMessage.bot_id == bot_id,
                        MonkeyMessage.group_id == group_id,
                        MonkeyReaction.emoji_id == MONKEY_EMOJI_ID,
                    )
                    .group_by(MonkeyMessage.user_id)
                    .having(total > 0)
                    .order_by(total.desc(), MonkeyMessage.user_id.asc())
                )
                rows = (await session.execute(statement)).all()
                return [
                    (int(row.user_id), str(row.nickname), int(row.reaction_count))
                    for row in rows
                ]

    async def groups_for_day(self, day: str) -> list[tuple[str, int]]:
        async with self.lock:
            session = get_session()
            async with session.begin():
                statement = (
                    select(MonkeyMessage.bot_id, MonkeyMessage.group_id)
                    .where(MonkeyMessage.day == day)
                    .distinct()
                    .order_by(MonkeyMessage.bot_id, MonkeyMessage.group_id)
                )
                return [
                    (str(row.bot_id), int(row.group_id))
                    for row in (await session.execute(statement)).all()
                ]

    async def report_already_sent(self, day: str, bot_id: str, group_id: int) -> bool:
        async with self.lock:
            session = get_session()
            async with session.begin():
                return (
                    await session.get(MonkeyDailyReport, (day, bot_id, group_id))
                ) is not None

    async def mark_report_sent(self, day: str, bot_id: str, group_id: int) -> None:
        async with self.lock:
            session = get_session()
            async with session.begin():
                if await session.get(MonkeyDailyReport, (day, bot_id, group_id)) is None:
                    session.add(
                        MonkeyDailyReport(day=day, bot_id=bot_id, group_id=group_id)
                    )

    async def prune(self, keep_days: int = 14) -> None:
        """删除过期的 SQLite 排行数据。"""
        cutoff = (_now().date() - timedelta(days=keep_days)).isoformat()
        async with self.lock:
            session = get_session()
            async with session.begin():
                await session.execute(
                    delete(MonkeyReaction).where(MonkeyReaction.day < cutoff)
                )
                await session.execute(delete(MonkeyMessage).where(MonkeyMessage.day < cutoff))
                await session.execute(
                    delete(MonkeyDailyReport).where(MonkeyDailyReport.day < cutoff)
                )

    @staticmethod
    def _whitelist_path() -> Path:
        """返回白名单 JSON 文件的路径（按需创建父目录）。"""
        try:
            base = Path(nonebot.get_driver().config.data_dir)
        except (AttributeError, KeyError, RuntimeError):
            base = Path("data")
        path = base / "nonebot_plugin_todaymonkeytop"
        path.mkdir(parents=True, exist_ok=True)
        return path / "push_list.json"

    @staticmethod
    def _load_whitelist() -> list[dict[str, Any]]:
        """从 JSON 文件读取白名单列表。"""
        wl_path = MonkeyStore._whitelist_path()
        if not wl_path.exists():
            return []
        try:
            raw = wl_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, list):
                return data
            return []
        except (json.JSONDecodeError, OSError):
            return []

    @staticmethod
    def _save_whitelist(data: list[dict[str, Any]]) -> None:
        """将白名单列表写入 JSON 文件。"""
        wl_path = MonkeyStore._whitelist_path()
        wl_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def is_group_whitelisted(self, bot_id: str, group_id: int) -> bool:
        """检查群是否在白名单中。"""
        async with self.lock:
            return any(
                item["bot_id"] == bot_id and item["group_id"] == group_id
                for item in MonkeyStore._load_whitelist()
            )

    async def add_to_whitelist(self, bot_id: str, group_id: int) -> None:
        """将群加入推送白名单（幂等）。"""
        async with self.lock:
            data = MonkeyStore._load_whitelist()
            if not any(
                item["bot_id"] == bot_id and item["group_id"] == group_id
                for item in data
            ):
                data.append({"bot_id": bot_id, "group_id": group_id})
                MonkeyStore._save_whitelist(data)

    async def remove_from_whitelist(self, bot_id: str, group_id: int) -> None:
        """将群移出推送白名单（幂等）。"""
        async with self.lock:
            data = MonkeyStore._load_whitelist()
            new_data = [
                item
                for item in data
                if not (item["bot_id"] == bot_id and item["group_id"] == group_id)
            ]
            if len(new_data) != len(data):
                MonkeyStore._save_whitelist(new_data)

    async def list_whitelist(self) -> list[tuple[str, int]]:
        """列出所有在白名单中的群组（按 bot_id, group_id 排序）。"""
        async with self.lock:
            items = [
                (item["bot_id"], item["group_id"])
                for item in MonkeyStore._load_whitelist()
            ]
            items.sort(key=lambda x: (x[0], x[1]))
            return items


store = MonkeyStore()


async def _get_monkey_count(bot: Bot, message_id: str) -> int:
    """调用 NapCat API，返回消息当前被贴猴子的用户数。"""
    result: Any = await bot.call_api(
        "get_emoji_likes",
        message_id=message_id,
        emoji_id=MONKEY_EMOJI_ID,
        count=0,  # NapCat 文档：0 表示读取全部
    )
    if not isinstance(result, dict):
        raise TypeError(f"get_emoji_likes 返回了 {type(result).__name__}，期望 dict")
    # NapCat 的 HTTP 原始响应有 data 包装；NoneBot OneBot 适配器的
    # bot.call_api() 会将该包装解开，直接返回业务数据。两种形式都兼容。
    data = result.get("data")
    payload = data if isinstance(data, dict) else result
    likes = payload.get("emoji_like_list")
    if not isinstance(likes, list):
        raise ValueError(f"get_emoji_likes 缺少 emoji_like_list：{result!r}")
    return len(likes)


async def _refresh_message(bot: Bot, owner: MessageOwner) -> bool:
    """刷新一条消息的猴子快照；失败会记录完整上下文并返回 False。"""
    try:
        count = await _get_monkey_count(bot, owner.message_id)
        await store.save_reaction_count(owner, count)
        logger.info(
            "todaymonkeytop: 刷新回应 gid={} msg={} target={} monkey_count={}",
            owner.group_id,
            owner.message_id,
            owner.user_id,
            count,
        )
        return True
    except Exception:
        logger.exception(
            "todaymonkeytop: 刷新回应失败 gid={} msg={} target={}",
            owner.group_id,
            owner.message_id,
            owner.user_id,
        )
        return False


async def _refresh_group(bot: Bot, group_id: int, day: str) -> tuple[int, int]:
    """并发刷新一个群当天全部已记录消息的猴子快照。"""
    owners = await store.messages_for_group(day, str(bot.self_id), group_id)
    if not owners:
        return (0, 0)

    semaphore = asyncio.Semaphore(8)

    async def refresh_with_limit(owner: MessageOwner) -> bool:
        async with semaphore:
            return await _refresh_message(bot, owner)

    results = await asyncio.gather(*(refresh_with_limit(owner) for owner in owners))
    success = sum(results)
    failures = len(results) - success
    logger.info(
        "todaymonkeytop: 群刷新完成 gid={} day={} messages={} success={} failures={}",
        group_id,
        day,
        len(owners),
        success,
        failures,
    )
    return success, failures


async def _resolve_owner_from_api(
    bot: Bot, group_id: int, message_id: str
) -> MessageOwner | None:
    """缓存未命中时，尝试用标准 OneBot ``get_msg`` 补回消息发送者。"""
    try:
        result: Any = await bot.call_api("get_msg", message_id=message_id)
        if not isinstance(result, dict) or result.get("user_id") is None:
            logger.warning(
                "todaymonkeytop: get_msg 无法解析发送者 gid={} msg={} result={!r}",
                group_id,
                message_id,
                result,
            )
            return None
        message_group = result.get("group_id")
        if message_group is not None and int(message_group) != group_id:
            logger.warning(
                "todaymonkeytop: get_msg 群号不一致 event_gid={} api_gid={} msg={}",
                group_id,
                message_group,
                message_id,
            )
            return None
        sender = result.get("sender") if isinstance(result.get("sender"), dict) else {}
        nickname = str(sender.get("card") or sender.get("nickname") or result["user_id"])
        owner = MessageOwner(
            day=_day_from_timestamp(result.get("time")),
            bot_id=str(bot.self_id),
            group_id=group_id,
            message_id=message_id,
            user_id=int(result["user_id"]),
            nickname=nickname,
        )
        await store.save_message(owner, int(result["time"]) if result.get("time") else None)
        logger.info(
            "todaymonkeytop: get_msg 回补消息映射 gid={} msg={} target={} day={}",
            group_id,
            message_id,
            owner.user_id,
            owner.day,
        )
        return owner
    except Exception:
        logger.exception(
            "todaymonkeytop: get_msg 回补失败 gid={} msg={}", group_id, message_id
        )
        return None


def _render_ranking(
    rows: list[tuple[int, str, int]], *, day: str, refresh_failures: int = 0
) -> str:
    """渲染不超过十名的纯文本排行榜。"""
    now_text = _now().strftime("%H:%M")
    lines = ["🐵【今日猴榜】🐵", f"统计日期：{day}（截至 {now_text}）", "━━━━━━━━━━━━"]
    if not rows:
        lines.append("今天还没有人收到 🐵 表情。")
    else:
        medals = ("🥇", "🥈", "🥉")
        filtered_rows = [row for row in rows if row[0] != 3862467831]
        if not filtered_rows:
            lines.append("今天还没有人收到 🐵 表情。")
        else:
            for index, (_user_id, nickname, count) in enumerate(
                filtered_rows[:TOP_LIMIT], start=1
            ):
                prefix = medals[index - 1] if index <= len(medals) else f"{index:>2}."
                lines.append(f"{prefix} {nickname} × {count} 🐵")
            if len(filtered_rows) > TOP_LIMIT:
                lines.append(f"另有 {len(filtered_rows) - TOP_LIMIT} 人未展示")
    lines.append("━━━━━━━━━━━━")
    if refresh_failures:
        lines.append(f"⚠️ {refresh_failures} 条消息的回应读取失败，结果可能不完整")
    return "\n".join(lines)


# 所有群消息均保存映射。block=False 避免影响其他插件的消息处理。
group_message_tracker = on_message(
    rule=is_type(GroupMessageEvent), priority=20, block=False
)


@group_message_tracker.handle()
async def _track_group_message(bot: Bot, event: GroupMessageEvent) -> None:
    if str(event.user_id) == str(bot.self_id):
        logger.debug(
            "todaymonkeytop: 忽略机器人自身消息 gid={} msg={}",
            event.group_id,
            event.message_id,
        )
        return

    owner = MessageOwner(
        day=_day_from_timestamp(event.time),
        bot_id=str(bot.self_id),
        group_id=event.group_id,
        message_id=str(event.message_id),
        user_id=event.user_id,
        nickname=_display_name(event),
    )
    await store.save_message(owner, int(event.time) if event.time else None)
    logger.debug(
        "todaymonkeytop: 已记录群消息 gid={} msg={} target={} day={}",
        owner.group_id,
        owner.message_id,
        owner.user_id,
        owner.day,
    )


# NapCat 的 group_msg_emoji_like 是 OneBot notice 扩展事件；保留为通用事件以
# 兼容 adapter-onebot 尚未声明该扩展类型的版本。
emoji_like_listener = on_notice(priority=20, block=False)


@emoji_like_listener.handle()
async def _track_emoji_like(bot: Bot, event: Event) -> None:
    if getattr(event, "notice_type", None) != "group_msg_emoji_like":
        return

    group_id = getattr(event, "group_id", None)
    message_id = getattr(event, "message_id", None)
    likes = getattr(event, "likes", None)
    if group_id is None or message_id is None or not isinstance(likes, list):
        logger.warning("todaymonkeytop: 忽略字段不完整的表情回应事件：{!r}", event)
        return

    def emoji_id_of(item: Any) -> Any:
        if isinstance(item, dict):
            return item.get("emoji_id")
        return getattr(item, "emoji_id", None)

    has_monkey = any(str(emoji_id_of(item)) == MONKEY_EMOJI_ID for item in likes)
    if not has_monkey:
        return

    group_id = int(group_id)
    message_id = str(message_id)
    is_add = getattr(event, "is_add", None)
    logger.info(
        "todaymonkeytop: 收到猴子回应事件 gid={} msg={} operator={} is_add={}",
        group_id,
        message_id,
        getattr(event, "user_id", None),
        is_add,
    )

    owner = await store.get_message(str(bot.self_id), group_id, message_id)
    if owner is None:
        owner = await _resolve_owner_from_api(bot, group_id, message_id)
    if owner is None:
        return
    if owner.user_id == int(bot.self_id):
        logger.debug(
            "todaymonkeytop: 忽略机器人自身消息的回应 gid={} msg={}",
            group_id,
            message_id,
        )
        return
    if owner.day != _today():
        logger.info(
            "todaymonkeytop: 忽略非今日消息的回应 gid={} msg={} message_day={}",
            group_id,
            message_id,
            owner.day,
        )
        return
    await _refresh_message(bot, owner)


# 使用 NoneBot 标准命令响应器；命令前缀遵循项目的 command_start 配置。
rank_query = on_command(
    "今日猴榜", rule=is_type(GroupMessageEvent), priority=10, block=True
)


@rank_query.handle()
async def _show_current_ranking(bot: Bot, event: GroupMessageEvent) -> None:
    logger.refresh()
    day = _today()
    logger.info(
        "todaymonkeytop: 收到榜单查询 gid={} requester={} day={}",
        event.group_id,
        event.user_id,
        day,
    )
    await rank_query.send("🐵 正在刷新今日猴榜，请稍候…")
    _, failures = await _refresh_group(bot, event.group_id, day)
    rows = await store.ranking(day, str(bot.self_id), event.group_id)
    await rank_query.finish(_render_ranking(rows, day=day, refresh_failures=failures))


def _get_target_group_id(event: Event, args_text: str) -> int | None:
    """从命令参数或事件中解析目标群号。"""
    import re

    # 尝试从参数中提取群号
    match = re.search(r"\d+", args_text)
    if match:
        try:
            return int(match.group())
        except (ValueError, OverflowError):
            return None
    # 无参数时使用当前群（仅群消息有效）
    if hasattr(event, "group_id"):
        return int(event.group_id)  # type: ignore[union-attr]
    return None


enable_push = on_command(
    "开启贴猴统计推送", permission=SUPERUSER, priority=10, block=True
)


@enable_push.handle()
async def _enable_push(bot: Bot, event: Event) -> None:
    """将群加入推送白名单（SUPERUSER）。"""
    raw_text = str(event.get_message()).strip()
    parts = raw_text.split(maxsplit=1)
    args_text = parts[1].strip() if len(parts) > 1 else ""
    target_group = _get_target_group_id(event, args_text)
    if target_group is None:
        await enable_push.finish("❌ 请在群聊中使用，或在命令后指定群号")

    await store.add_to_whitelist(str(bot.self_id), target_group)
    logger.info(
        "todaymonkeytop: 已添加推送白名单 gid={} bot={}", target_group, bot.self_id
    )
    await enable_push.finish(f"✅ 已开启群 {target_group} 的每日贴猴统计推送")


list_push = on_command(
    "列出贴猴统计推送", permission=SUPERUSER, priority=10, block=True
)


@list_push.handle()
async def _list_push(bot: Bot, event: Event) -> None:
    """列出所有推送白名单群组（SUPERUSER）。"""
    rows = await store.list_whitelist()
    now_text = _now().strftime("%H:%M")
    if not rows:
        await list_push.finish("📋 当前没有开启贴猴统计推送的群")
    lines = ["📋 贴猴统计推送白名单", f"（截至 {now_text}）", "━━━━━━━━━━━━"]
    for bot_id, group_id in rows:
        lines.append(f"• 群 {group_id}（Bot: {bot_id}）")
    lines.append(f"共 {len(rows)} 个群")
    await list_push.finish("\n".join(lines))


disable_push = on_command(
    "关闭贴猴统计推送", permission=SUPERUSER, priority=10, block=True
)


@disable_push.handle()
async def _disable_push(bot: Bot, event: Event) -> None:
    """将群移出推送白名单（SUPERUSER）。"""
    raw_text = str(event.get_message()).strip()
    parts = raw_text.split(maxsplit=1)
    args_text = parts[1].strip() if len(parts) > 1 else ""
    target_group = _get_target_group_id(event, args_text)
    if target_group is None:
        await disable_push.finish("❌ 请在群聊中使用，或在命令后指定群号")

    await store.remove_from_whitelist(str(bot.self_id), target_group)
    logger.info(
        "todaymonkeytop: 已移除推送白名单 gid={} bot={}", target_group, bot.self_id
    )
    await disable_push.finish(f"✅ 已关闭群 {target_group} 的每日贴猴统计推送")


test_push = on_command(
    "测试贴猴统计推送", permission=SUPERUSER, priority=10, block=True
)


@test_push.handle()
async def _test_push(bot: Bot, event: Event) -> None:
    """测试指定群的每日推送是否正常（SUPERUSER）。"""
    logger.refresh()
    raw_text = str(event.get_message()).strip()
    parts = raw_text.split(maxsplit=1)
    args_text = parts[1].strip() if len(parts) > 1 else ""
    target_group = _get_target_group_id(event, args_text)
    if target_group is None:
        await test_push.finish("❌ 请在群聊中使用，或在命令后指定群号")

    day = _today()
    bot_id = str(bot.self_id)

    # 1. 检查是否有消息记录
    owners = await store.messages_for_group(day, bot_id, target_group)
    if not owners:
        await test_push.finish(
            f"⚠️ 群 {target_group} 今天没有消息记录，无法测试推送"
        )

    # 2. 检查白名单
    whitelisted = await store.is_group_whitelisted(bot_id, target_group)
    if not whitelisted:
        await test_push.finish(
            f"❌ 群 {target_group} 未开启白名单，每日 23:59 不会收到自动推送。\n"
            f"请使用「开启贴猴统计推送」加入白名单"
        )

    # 3. 刷新并发送测试推送
    await test_push.send(
        f"🔄 正在刷新群 {target_group} 的猴榜数据并发送测试推送…"
    )
    _, failures = await _refresh_group(bot, target_group, day)
    rows = await store.ranking(day, bot_id, target_group)
    message = _render_ranking(rows, day=day, refresh_failures=failures)
    await bot.send_group_msg(group_id=target_group, message=message)

    logger.info(
        "todaymonkeytop: 测试推送成功 gid={} bot={} entries={}",
        target_group,
        bot_id,
        len(rows),
    )
    status = "⚠️ 部分消息刷新失败" if failures else "✅ 全部刷新成功"
    await test_push.finish(
        f"✅ 测试推送完成！\n"
        f"• 群 {target_group} 已收到今日猴榜（{len(rows)} 人上榜）\n"
        f"• {status}"
    )


@scheduler.scheduled_job(
    "cron",
    hour=23,
    minute=59,
    timezone="Asia/Shanghai",
    id="nonebot_plugin_todaymonkeytop_daily_rank",
)
async def _send_daily_rankings() -> None:
    """在每天 23:59 向当天有记录消息的群发送榜单。"""
    logger.refresh()
    day = _today()
    groups = await store.groups_for_day(day)
    bots = nonebot.get_bots()
    logger.info(
        "todaymonkeytop: 开始每日结算 day={} groups={} online_bots={}",
        day,
        len(groups),
        len(bots),
    )
    for bot_id, group_id in groups:
        if await store.report_already_sent(day, bot_id, group_id):
            logger.info(
                "todaymonkeytop: 今日榜单已发送，跳过 gid={} bot={}", group_id, bot_id
            )
            continue
        if not await store.is_group_whitelisted(bot_id, group_id):
            logger.info(
                "todaymonkeytop: 群 {} 未在白名单，跳过每日推送",
                group_id,
            )
            continue
        raw_bot = bots.get(bot_id)
        if not isinstance(raw_bot, Bot):
            logger.warning(
                "todaymonkeytop: 无法发送 gid={}，机器人 {} 当前不在线",
                group_id,
                bot_id,
            )
            continue
        try:
            _, failures = await _refresh_group(raw_bot, group_id, day)
            rows = await store.ranking(day, bot_id, group_id)
            message = _render_ranking(rows, day=day, refresh_failures=failures)
            await raw_bot.send_group_msg(group_id=group_id, message=message)
            await store.mark_report_sent(day, bot_id, group_id)
            logger.info(
                "todaymonkeytop: 每日榜单发送成功 gid={} bot={} entries={}",
                group_id,
                bot_id,
                len(rows),
            )
        except Exception:
            logger.exception(
                "todaymonkeytop: 每日榜单发送失败 gid={} bot={}", group_id, bot_id
            )
    await store.prune()
    logger.info("todaymonkeytop: 每日结算完成 day={}", day)
