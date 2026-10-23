from datetime import date, datetime
from typing import Any

import pytest

from soybean_sim.simulation import next_month


@pytest.mark.parametrize(
    ("month", "expected"),
    [
        (date(2023, 1, 1), date(2023, 2, 1)),
        (date(2023, 2, 1), date(2023, 3, 1)),
        (date(2024, 1, 1), date(2024, 2, 1)),
        (date(2024, 2, 1), date(2024, 3, 1)),
        (date(2023, 12, 1), date(2024, 1, 1)),
        (date(9999, 11, 1), date(9999, 12, 1)),
    ],
)
def test_next_calendar_month(month: date, expected: date) -> None:
    """Advance real calendar boundaries without depending on month length."""
    assert next_month(month) == expected


@pytest.mark.parametrize(
    "month", [date(2024, 2, 29), datetime(2024, 2, 1), "2024-02-01", 0, None]
)
def test_rejects_ambiguous_months(month: Any) -> None:
    """Require a first-of-month date, consistent with snapshots and price records."""
    with pytest.raises(ValueError, match="first day of a month"):
        next_month(month)


def test_rejects_calendar_overflow() -> None:
    """Report attempts to advance past the last representable calendar month."""
    with pytest.raises(ValueError, match="maximum supported year"):
        next_month(date(9999, 12, 1))
