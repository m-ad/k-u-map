"""Tests for GTFS classification rules and service-date handling."""

import datetime as dt

import pytest

from pipeline.gtfs_rules import NETWORK_CUTOFF, is_special_line, mode_of, service_periods


@pytest.mark.parametrize(
    ("route_type", "mode"),
    [
        ("0", "tram"),
        ("900", "tram"),
        ("2", "rail"),
        ("109", "rail"),
        ("1", "rail"),
        ("3", "bus"),
        ("700", "bus"),
        ("11", "bus"),
        ("4", None),
        ("1000", None),
    ],
)
def test_mode_of(route_type: str, mode: str | None) -> None:
    assert mode_of(route_type) == mode


@pytest.mark.parametrize(
    ("ref", "special"),
    [("E", True), ("N1", True), ("NL2", True), ("N", False), ("1", False), ("S5", False), ("NE", False), ("10", False)],
)
def test_is_special_line(ref: str, special: bool) -> None:
    assert is_special_line(ref) is special


@pytest.mark.parametrize(
    ("ref", "mode", "special"),
    [
        ("SEV 10", "bus", True),
        ("SEV S7/S8", "bus", True),
        ("RE2", "bus", True),
        ("MEX17", "bus", True),
        ("S5", "bus", True),
        ("S5", "tram", False),
        ("125X", "bus", False),
        ("RE2", "rail", False),
    ],
)
def test_rail_replacement_buses_are_special(ref: str, mode: str, special: bool) -> None:
    assert is_special_line(ref, mode) is special


def test_service_periods_split_at_2027_cutoff() -> None:
    assert NETWORK_CUTOFF == dt.date(2027, 1, 1)
    calendar = [
        # Runs Mondays through 2026 only.
        {
            "service_id": "old",
            "monday": "1",
            "tuesday": "0",
            "wednesday": "0",
            "thursday": "0",
            "friday": "0",
            "saturday": "0",
            "sunday": "0",
            "start_date": "20260411",
            "end_date": "20261231",
        },
        # Starts with the new network.
        {
            "service_id": "new",
            "monday": "1",
            "tuesday": "1",
            "wednesday": "1",
            "thursday": "1",
            "friday": "1",
            "saturday": "0",
            "sunday": "0",
            "start_date": "20270101",
            "end_date": "20271231",
        },
        # Weekly service spanning the cut-over.
        {
            "service_id": "both",
            "monday": "0",
            "tuesday": "0",
            "wednesday": "0",
            "thursday": "0",
            "friday": "0",
            "saturday": "1",
            "sunday": "0",
            "start_date": "20261201",
            "end_date": "20270131",
        },
    ]
    dates = [
        # An added date in 2027 makes "old" run then too.
        {"service_id": "old", "date": "20270104", "exception_type": "1"},
        # Removing the only 2026 Saturdays leaves "both" as 2027-only.
        *(
            {"service_id": "both", "date": d, "exception_type": "2"}
            for d in ("20261205", "20261212", "20261219", "20261226")
        ),
        # A service known only from calendar_dates.
        {"service_id": "extra", "date": "20261224", "exception_type": "1"},
    ]
    periods = service_periods(calendar, dates)
    assert periods["old"] == {"2026", "2027"}
    assert periods["new"] == {"2027"}
    assert periods["both"] == {"2027"}
    assert periods["extra"] == {"2026"}
