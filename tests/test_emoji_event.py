"""表情回应事件快照解析测试。"""

from __future__ import annotations

from types import SimpleNamespace

from nonebot_plugin_todaymonkeytop.__init__ import _monkey_count_from_likes


def test_extract_monkey_count_from_event() -> None:
    assert _monkey_count_from_likes(
        [
            {"emoji_id": "128052", "count": 4},
            {"emoji_id": "128053", "count": 3},
        ]
    ) == 3


def test_extract_zero_count_for_removed_monkey() -> None:
    assert _monkey_count_from_likes([{"emoji_id": "128053", "count": 0}]) == 0


def test_ignore_event_without_monkey() -> None:
    assert _monkey_count_from_likes([{"emoji_id": "128052", "count": 1}]) is None


def test_ignore_invalid_monkey_count() -> None:
    assert _monkey_count_from_likes([{"emoji_id": "128053", "count": "bad"}]) is None


def test_extract_monkey_count_from_object() -> None:
    assert _monkey_count_from_likes(
        [SimpleNamespace(emoji_id="128053", count="2")]
    ) == 2
