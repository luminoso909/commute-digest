from datetime import UTC, date, datetime
from subprocess import CompletedProcess

import pytest

from commute_digest import autodl_scheduler
from commute_digest.schedule import BEIJING, is_slot, latest_slot, next_slot, slot_title


@pytest.mark.parametrize(
    ("now", "first_date", "expected"),
    [
        ("2026-11-24T06:44:00+00:00", date(2026, 11, 25), "2026-11-25T05:00:00+08:00"),
        ("2026-11-24T20:59:59+00:00", None, "2026-11-25T05:00:00+08:00"),
        ("2026-11-24T21:00:00+00:00", None, "2026-11-25T17:00:00+08:00"),
        ("2026-11-25T09:00:01+00:00", None, "2026-11-26T05:00:00+08:00"),
    ],
)
def test_next_run_uses_beijing_time_and_respects_the_first_date(now, first_date, expected):
    assert (
        autodl_scheduler.next_run(datetime.fromisoformat(now), first_date).isoformat() == expected
    )


@pytest.mark.parametrize(
    ("hour", "expected", "chinese_title", "english_title"),
    [
        (4, "2026-11-24T17:00:00+08:00", "通勤晚报", "Commute Evening Digest"),
        (5, "2026-11-25T05:00:00+08:00", "通勤晨报", "Commute Morning Digest"),
        (16, "2026-11-25T05:00:00+08:00", "通勤晨报", "Commute Morning Digest"),
        (17, "2026-11-25T17:00:00+08:00", "通勤晚报", "Commute Evening Digest"),
    ],
)
def test_slot_at_or_before_now_is_the_issue_boundary(hour, expected, chinese_title, english_title):
    now = datetime(2026, 11, 25, hour, tzinfo=BEIJING)
    slot = latest_slot(now.astimezone(UTC))
    assert slot.isoformat() == expected
    assert is_slot(slot.astimezone(UTC))
    assert slot_title(slot, "zh") == chinese_title
    assert slot_title(slot, "en") == english_title
    assert next_slot(slot) > now
    assert not is_slot(slot.replace(tzinfo=None))
    assert not is_slot(slot.replace(second=1))


def test_autodl_passes_the_scheduled_boundary_after_waking(monkeypatch, capsys):
    class Finished(Exception):
        pass

    class Clock:
        now_beijing = datetime(2026, 11, 25, 4, 59, 59, tzinfo=BEIJING)

        @classmethod
        def now(cls, tz):
            return cls.now_beijing.astimezone(tz)

    def sleep(seconds):
        assert seconds == 1
        Clock.now_beijing = datetime(2026, 11, 25, 5, 0, 2, tzinfo=BEIJING)

    def run(command, **kwargs):
        if command[-3:] == ["-m", "commute_digest", "widget-feed"]:
            return CompletedProcess(command, 1)
        assert command[-5:] == [
            "-m",
            "commute_digest",
            "deliver",
            "--slot",
            "2026-11-25T05:00:00+08:00",
        ]
        raise Finished

    monkeypatch.setattr(autodl_scheduler.sys, "argv", ["autodl_scheduler.py"])
    monkeypatch.setattr(autodl_scheduler, "datetime", Clock)
    monkeypatch.setattr(autodl_scheduler, "sleep", sleep)
    monkeypatch.setattr(autodl_scheduler.subprocess, "run", run)
    with pytest.raises(Finished):
        autodl_scheduler.main()
    assert "widget_feed_exit_code=1" in capsys.readouterr().out
