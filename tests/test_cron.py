"""Parser de expressões cron."""

from datetime import datetime

import pytest

from bgo_scheduler.cron import CronError, CronSpec

# 2026-07-07 é uma terça-feira.


def test_weekdays_at_nine():
    c = CronSpec("0 9 * * 1-5")
    assert c.next_after(datetime(2026, 7, 7, 10, 0)) == datetime(2026, 7, 8, 9, 0)


def test_friday_rolls_to_monday():
    c = CronSpec("0 9 * * 1-5")
    assert c.next_after(datetime(2026, 7, 10, 10, 0)) == datetime(2026, 7, 13, 9, 0)


def test_step_every_15_minutes():
    c = CronSpec("*/15 * * * *")
    assert c.next_after(datetime(2026, 7, 7, 10, 7)) == datetime(2026, 7, 7, 10, 15)


def test_first_of_month():
    c = CronSpec("0 7 1 * *")
    assert c.next_after(datetime(2026, 7, 7, 10, 0)) == datetime(2026, 8, 1, 7, 0)


def test_matches():
    c = CronSpec("30 8 * * *")
    assert c.matches(datetime(2026, 7, 7, 8, 30))
    assert not c.matches(datetime(2026, 7, 7, 8, 31))


def test_sunday_is_0_and_7():
    assert CronSpec("0 0 * * 0").dows == CronSpec("0 0 * * 7").dows


@pytest.mark.parametrize("expr", ["0 9 * *", "60 0 * * *", "0 24 * * *", "* * * * 8", "a b c d e"])
def test_invalid_expressions(expr):
    with pytest.raises(CronError):
        CronSpec(expr)
