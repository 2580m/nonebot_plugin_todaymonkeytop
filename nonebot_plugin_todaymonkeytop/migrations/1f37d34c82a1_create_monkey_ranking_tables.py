"""create monkey ranking tables

迁移 ID: 1f37d34c82a1
父迁移:
创建时间: 2026-07-16 20:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "1f37d34c82a1"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("nonebot_plugin_todaymonkeytop",)
depends_on: str | Sequence[str] | None = None


def upgrade(name: str = "") -> None:
    if name:
        return

    op.create_table(
        "nonebot_plugin_todaymonkeytop_messages",
        sa.Column("bot_id", sa.String(length=32), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("nickname", sa.String(length=255), nullable=False),
        sa.Column("message_time", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("bot_id", "group_id", "message_id"),
        info={"bind_key": "nonebot_plugin_todaymonkeytop"},
    )
    op.create_index(
        "ix_todaymonkeytop_messages_day_bot_group",
        "nonebot_plugin_todaymonkeytop_messages",
        ["day", "bot_id", "group_id"],
        unique=False,
    )

    op.create_table(
        "nonebot_plugin_todaymonkeytop_reactions",
        sa.Column("bot_id", sa.String(length=32), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("emoji_id", sa.String(length=32), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("reaction_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("bot_id", "group_id", "message_id", "emoji_id"),
        info={"bind_key": "nonebot_plugin_todaymonkeytop"},
    )
    op.create_index(
        "ix_todaymonkeytop_reactions_day_bot_group",
        "nonebot_plugin_todaymonkeytop_reactions",
        ["day", "bot_id", "group_id"],
        unique=False,
    )

    op.create_table(
        "nonebot_plugin_todaymonkeytop_daily_reports",
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("bot_id", sa.String(length=32), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("day", "bot_id", "group_id"),
        info={"bind_key": "nonebot_plugin_todaymonkeytop"},
    )


def downgrade(name: str = "") -> None:
    if name:
        return

    op.drop_table("nonebot_plugin_todaymonkeytop_daily_reports")
    op.drop_index(
        "ix_todaymonkeytop_reactions_day_bot_group",
        table_name="nonebot_plugin_todaymonkeytop_reactions",
    )
    op.drop_table("nonebot_plugin_todaymonkeytop_reactions")
    op.drop_index(
        "ix_todaymonkeytop_messages_day_bot_group",
        table_name="nonebot_plugin_todaymonkeytop_messages",
    )
    op.drop_table("nonebot_plugin_todaymonkeytop_messages")
