"""今日猴榜的 ORM 数据模型。"""

from __future__ import annotations

from datetime import datetime

from nonebot import require

require("nonebot_plugin_orm")

from nonebot_plugin_orm import Model
from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class MonkeyMessage(Model):
    """当天被统计的群消息及其发送者。"""

    __tablename__ = "nonebot_plugin_todaymonkeytop_messages"
    __table_args__ = (
        Index(
            "ix_todaymonkeytop_messages_day_bot_group",
            "day",
            "bot_id",
            "group_id",
        ),
    )

    bot_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    group_id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)
    nickname: Mapped[str] = mapped_column(String(255), nullable=False)
    message_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MonkeyReaction(Model):
    """一条消息在某个表情下的当前回应人数快照。"""

    __tablename__ = "nonebot_plugin_todaymonkeytop_reactions"
    __table_args__ = (
        Index(
            "ix_todaymonkeytop_reactions_day_bot_group",
            "day",
            "bot_id",
            "group_id",
        ),
    )

    bot_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    group_id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    emoji_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    reaction_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class MonkeyDailyReport(Model):
    """已成功发送过的日榜，防止同一天重复发送。"""

    __tablename__ = "nonebot_plugin_todaymonkeytop_daily_reports"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    bot_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    group_id: Mapped[int] = mapped_column(primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
