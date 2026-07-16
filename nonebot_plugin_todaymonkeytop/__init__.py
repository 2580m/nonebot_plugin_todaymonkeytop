"""nonebot_plugin_todaymonkeytop - 今日猴榜

统计今天群内消息收到的🐵表情回复，生成排行榜。

工作原理:
  1. 依赖 nonebot_plugin_chatrecorder 获取今日消息列表
  2. 通过 uninfo ORM 批量解析发送者 user_id
  3. 调用 NapCat get_emoji_likes API 查询每条消息的🐵表情回复
  4. 聚合每人的🐵表情收入并排行

依赖:
  - NapCatQQ (get_emoji_likes API)
  - nonebot_plugin_chatrecorder (消息记录)
  - nonebot_plugin_uninfo (会话数据 ORM)
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

from nonebot import on_command, logger, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, permission
from nonebot.params import CommandArg

require("nonebot_plugin_chatrecorder")
require("nonebot_plugin_uninfo")

from nonebot_plugin_chatrecorder import get_message_records
from nonebot_plugin_chatrecorder.model import MessageRecord
from nonebot_plugin_orm import get_session
from nonebot_plugin_uninfo.orm import SessionModel, UserModel
from sqlalchemy import select

# ==================== 常量 ====================

BATCH_CONCURRENCY = 10
PROGRESS_INTERVAL = 50


# ==================== 工具函数 ====================


def _today_start_cst_utc() -> datetime:
    """获取今天 00:00:00 (UTC+8) 对应的 UTC datetime。"""
    now = datetime.now(timezone(timedelta(hours=8)))
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


async def _build_msg_user_map(records: list[MessageRecord]) -> dict[str, int]:
    """通过 uninfo ORM 批量查询 message_id → user_id。"""
    persist_ids = list({r.session_persist_id for r in records})
    if not persist_ids:
        return {}

    async with get_session() as db_session:
        stmt = (
            select(SessionModel.id, UserModel.user_id)
            .join(UserModel, UserModel.id == SessionModel.user_persist_id)
            .where(SessionModel.id.in_(persist_ids))
        )
        rows = (await db_session.execute(stmt)).all()
        session_user_map: dict[int, int] = {
            row[0]: int(row[1]) for row in rows if row[1] is not None
        }

    mapping: dict[str, int] = {}
    for r in records:
        uid = session_user_map.get(r.session_persist_id)
        if uid is not None:
            mapping[r.message_id] = uid
    return mapping


async def _check_message(
    bot: Bot,
    record: MessageRecord,
    emoji_ids: list[str],
    msg_user_map: dict[str, int],
    group_id: int,
) -> tuple[int, int, int] | None:
    """检查单条消息，返回 (user_id, monkey_count, failed_count) 或 None。"""
    target_uid = msg_user_map.get(record.message_id)
    if target_uid is None:
        return None

    monkey_count = 0
    failed = 0
    for eid in emoji_ids:
        logger.info(
            f"monkeytop: 查 msg={record.message_id} "
            f"gid={group_id} emoji={eid}"
        )
        try:
            result: Any = await bot.call_api(
                "get_emoji_likes",
                message_id=record.message_id,
                emoji_id=str(eid),
                group_id=str(group_id),
                count=20,
            )
            logger.info(
                f"monkeytop: 响应: {str(result)[:300]}"
            )
            if not isinstance(result, dict):
                logger.warning(
                    f"monkeytop: result 不是 dict: type={type(result).__name__}"
                )
                failed += 1
                continue
            data = result.get("data")
            if data is None:
                logger.warning(
                    f"monkeytop: data=None msg={record.message_id}"
                )
            elif isinstance(data, dict):
                likes_list = data.get("emoji_like_list")
                if isinstance(likes_list, list):
                    monkey_count += len(likes_list)
                    logger.info(
                        f"monkeytop: msg={record.message_id} 找到 "
                        f"{len(likes_list)} 条表情回复"
                    )
                else:
                    logger.warning(
                        f"monkeytop: emoji_like_list 不是 list: "
                        f"type={type(likes_list).__name__} "
                        f"data_keys={list(data.keys())}"
                    )
            else:
                logger.warning(
                    f"monkeytop: data 不是 dict: type={type(data).__name__}"
                )
        except Exception as exc:
            logger.error(
                f"monkeytop: 失败 msg={record.message_id} emoji={eid}: {exc}"
            )
            failed += 1

    return (target_uid, monkey_count, failed)


# ==================== 今日猴榜命令 ====================

_monkey_cmd = on_command("今日猴榜", permission=permission.GROUP, priority=10, block=True)


@_monkey_cmd.handle()
async def _handle_monkey_ranking(
    bot: Bot,
    event: GroupMessageEvent,
    args: Any = CommandArg(),
) -> None:
    """处理 '今日猴榜' 命令，统计并展示排行榜。"""
    group_id = event.group_id
    await _monkey_cmd.send("🐵 正在统计今日猴榜，请稍候...")

    # ---- 从 chatrecorder 加载今日消息 ----
    today_start = _today_start_cst_utc()

    records: list[MessageRecord] = await get_message_records(
        scene_ids=[str(group_id)],
        time_start=today_start,
        types=["message"],
    )

    if not records:
        await _monkey_cmd.finish("今天群里还没有消息记录喵～")

    # ---- 解析自定义表情 ID ----
    custom_ids: list[str] | None = None
    text = args.extract_plain_text().strip()
    if text:
        custom_ids = [x.strip() for x in text.replace("，", ",").split(",") if x.strip()]

    emoji_ids = custom_ids or ["128053"]
    total_msgs = len(records)
    total_calls = total_msgs * len(emoji_ids)

    await _monkey_cmd.send(
        f"今日共 {total_msgs} 条消息，需检查 {total_calls} 次 API 调用\n"
        f"表情ID: {', '.join(emoji_ids)}\n"
        f"正在并发处理（每批 {BATCH_CONCURRENCY} 条），请稍候..."
    )

    # ---- 构建 message_id → user_id 映射 ----
    msg_user_map = await _build_msg_user_map(records)
    if not msg_user_map:
        await _monkey_cmd.finish("无法解析消息发送者信息喵～")

    # ---- 并发检查所有消息 ----
    user_counts: dict[int, int] = defaultdict(int)
    total_checked = 0
    total_failed = 0
    first_errors: list[str] = []
    ERROR_LOG_LIMIT = 3
    last_progress = 0

    for batch_start in range(0, total_msgs, BATCH_CONCURRENCY):
        batch = records[batch_start:batch_start + BATCH_CONCURRENCY]
        tasks = [
            _check_message(bot, r, emoji_ids, msg_user_map, group_id)
            for r in batch
        ]
        batch_results = await asyncio.gather(*tasks)

        for record, result in zip(batch, batch_results):
            if result is not None:
                uid, count, failed = result
                if count:
                    user_counts[uid] += count
                total_checked += 1
                total_failed += failed
                if failed and len(first_errors) < ERROR_LOG_LIMIT:
                    first_errors.append(
                        f"msg={record.message_id} "
                        f"failed={failed}/{len(emoji_ids)}"
                    )

        # 进度汇报
        processed = batch_start + len(batch)
        if processed - last_progress >= PROGRESS_INTERVAL or processed >= total_msgs:
            last_progress = processed
            done_calls = processed * len(emoji_ids)
            try:
                await _monkey_cmd.send(
                    f"⏳ 进度: {processed}/{total_msgs} 条消息"
                    f"（{done_calls}/{total_calls} API 调用）"
                )
            except Exception:
                pass

    for err in first_errors:
        logger.warning(f"monkeytop: get_emoji_likes 部分失败 - {err}")

    if not user_counts:
        tail = ""
        if total_failed:
            tail = (
                f"\n（{total_failed} 次 API 调用失败，"
                f"可能消息已过期或 NapCat 版本不支持）"
            )
        await _monkey_cmd.finish(
            f"检查完毕（{total_checked} 条消息）"
            f"，今天没有人收到🐵表情喵～{tail}"
        )

    # ---- 排序 + 查昵称 ----
    sorted_users = sorted(user_counts.items(), key=lambda x: -x[1])

    lines = ["🐵【今日猴榜】🐵", "━" * 20]
    medals = ["🥇", "🥈", "🥉"]

    for i, (uid, count) in enumerate(sorted_users[:10]):
        try:
            info: Any = await bot.call_api(
                "get_stranger_info", user_id=uid, no_cache=True
            )
            name = info.get("nickname", str(uid)) if isinstance(info, dict) else str(uid)
        except Exception:
            name = str(uid)
        prefix = medals[i] if i < 3 else f"  {i + 1}. "
        lines.append(f"{prefix} {name}  × {count} 🐵")

    extra_lines: list[str] = [
        "━" * 20,
        f"共检查 {total_checked} 条消息",
    ]
    if len(sorted_users) > 10:
        extra_lines.append(f"（还有 {len(sorted_users) - 10} 人未显示）")
    if total_failed:
        extra_lines.append(f"（{total_failed} 次 API 调用失败）")
    extra_lines.append(f"表情ID: {', '.join(emoji_ids)}")

    await _monkey_cmd.finish("\n".join(lines + extra_lines))


# ==================== 测试命令 ====================

_test_cmd = on_command("猴榜测试", permission=permission.GROUP, priority=10, block=True)


@_test_cmd.handle()
async def _handle_test(bot: Bot, event: GroupMessageEvent) -> None:
    """用当前消息的 message_id 测试 fetch_emoji_like 是否可用。"""
    msg_id = event.message_id
    await _test_cmd.send(f"正在测试 msg={msg_id}...")

    for eid in ("128053", "76", "0"):
        for etype in (1, 2):
            try:
                result = await bot.call_api(
                    "fetch_emoji_like",
                    message_id=msg_id,
                    emojiId=eid,
                    emojiType=etype,
                    count=20,
                    cookie="",
                )
                data = result.get("data") if isinstance(result, dict) else {}
                likes = data.get("emojiLikesList")
                count = len(likes) if isinstance(likes, list) else "N/A"
                await _test_cmd.send(
                    f"fetch_emoji_like(msg={msg_id}, id={eid}, type={etype}) → "
                    f"count={count}, keys={list(data.keys())[:5]}"
                )
                return  # 找到能用的就不继续试了
            except Exception as exc:
                await _test_cmd.send(
                    f"fetch_emoji_like(msg={msg_id}, id={eid}, type={etype}) → "
                    f"失败: {exc}"
                )

