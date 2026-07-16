"""群聊「今日猴榜」插件。

本插件会在内存中保留当天群消息的 ``message_id -> 发送者`` 映射。NapCat 上报
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
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import nonebot
from nonebot import logger, on_command, on_message, on_notice, require
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent
from nonebot.rule import is_type

# NoneBot 没有内置定时器；该插件是发布每日榜单所必需的依赖。
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler


PLUGIN_NAME = "今日猴榜"
MONKEY_EMOJI_ID = "128053"
TOP_LIMIT = 10
TIME_ZONE = ZoneInfo("Asia/Shanghai")


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
    """仅驻留在进程内存中的当天数据。

    磁盘 SQLite 持久化已按当前需求停用；进程重启会清空排行榜数据。
    """

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.messages: dict[tuple[str, int, str], MessageOwner] = {}
        self.reaction_counts: dict[tuple[str, int, str, str], int] = {}
        self.sent_reports: set[tuple[str, str, int]] = set()
        logger.info("todaymonkeytop: 已启用纯内存统计，磁盘持久化已停用")

    async def save_message(self, owner: MessageOwner, message_time: int | None) -> None:
        del message_time
        async with self.lock:
            self.messages[(owner.bot_id, owner.group_id, owner.message_id)] = owner

    async def get_message(
        self, bot_id: str, group_id: int, message_id: str
    ) -> MessageOwner | None:
        async with self.lock:
            return self.messages.get((bot_id, group_id, message_id))

    async def messages_for_group(
        self, day: str, bot_id: str, group_id: int
    ) -> list[MessageOwner]:
        async with self.lock:
            rows = [
                owner
                for owner in self.messages.values()
                if owner.day == day
                and owner.bot_id == bot_id
                and owner.group_id == group_id
            ]
        return sorted(rows, key=lambda owner: owner.message_id)

    async def save_reaction_count(
        self, owner: MessageOwner, count: int
    ) -> None:
        async with self.lock:
            self.reaction_counts[
                (owner.bot_id, owner.group_id, owner.message_id, MONKEY_EMOJI_ID)
            ] = count

    async def ranking(
        self, day: str, bot_id: str, group_id: int
    ) -> list[tuple[int, str, int]]:
        async with self.lock:
            totals: dict[int, int] = {}
            names: dict[int, str] = {}
            for owner in self.messages.values():
                if (owner.day, owner.bot_id, owner.group_id) != (day, bot_id, group_id):
                    continue
                count = self.reaction_counts.get(
                    (owner.bot_id, owner.group_id, owner.message_id, MONKEY_EMOJI_ID), 0
                )
                if count:
                    totals[owner.user_id] = totals.get(owner.user_id, 0) + count
                    names[owner.user_id] = owner.nickname or str(owner.user_id)
        return sorted(
            ((user_id, names[user_id], count) for user_id, count in totals.items()),
            key=lambda row: (-row[2], row[0]),
        )

    async def groups_for_day(self, day: str) -> list[tuple[str, int]]:
        async with self.lock:
            groups = {
                (owner.bot_id, owner.group_id)
                for owner in self.messages.values()
                if owner.day == day
            }
        return sorted(groups)

    async def report_already_sent(self, day: str, bot_id: str, group_id: int) -> bool:
        async with self.lock:
            return (day, bot_id, group_id) in self.sent_reports

    async def mark_report_sent(self, day: str, bot_id: str, group_id: int) -> None:
        async with self.lock:
            self.sent_reports.add((day, bot_id, group_id))

    async def prune(self, keep_days: int = 14) -> None:
        """清理过期内存数据，防止长期运行时内存无限增长。"""
        cutoff = (_now().date() - timedelta(days=keep_days)).isoformat()
        async with self.lock:
            self.messages = {
                key: owner for key, owner in self.messages.items() if owner.day >= cutoff
            }
            valid_messages = set(self.messages)
            self.reaction_counts = {
                key: count
                for key, count in self.reaction_counts.items()
                if key[:3] in valid_messages
            }
            self.sent_reports = {
                report for report in self.sent_reports if report[0] >= cutoff
            }


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
    data = result.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"get_emoji_likes 缺少 data：{result!r}")
    likes = data.get("emoji_like_list")
    if not isinstance(likes, list):
        raise ValueError(f"get_emoji_likes 缺少 emoji_like_list：{data!r}")
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
        for index, (user_id, nickname, count) in enumerate(rows[:TOP_LIMIT], start=1):
            prefix = medals[index - 1] if index <= len(medals) else f"{index:>2}."
            lines.append(f"{prefix} {nickname}（{user_id}） × {count} 🐵")
        if len(rows) > TOP_LIMIT:
            lines.append(f"另有 {len(rows) - TOP_LIMIT} 人未展示")
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


@scheduler.scheduled_job(
    "cron",
    hour=23,
    minute=59,
    timezone="Asia/Shanghai",
    id="nonebot_plugin_todaymonkeytop_daily_rank",
)
async def _send_daily_rankings() -> None:
    """在每天 23:59 向当天有记录消息的群发送榜单。"""
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
