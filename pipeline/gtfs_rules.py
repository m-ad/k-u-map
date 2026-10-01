"""Pure GTFS rules: transport modes, special lines and service periods."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping

# The joint Ulm/Neu-Ulm city network starts on this date. Trips running before it
# belong to "Netz 2026", trips running on or after it to "Netz ab 2027".
NETWORK_CUTOFF = dt.date(2027, 1, 1)

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def mode_of(route_type: str) -> str | None:
    """Map a GTFS (or extended) route_type to ``tram``, ``rail``, ``bus`` or ``None``.

    Parameters
    ----------
    route_type
        ``routes.txt`` route_type value.

    Returns
    -------
    str or None
        Rendering mode; ferries, cable cars and taxis are ignored.
    """
    t = int(route_type)
    if t == 0 or 900 <= t <= 906:
        return "tram"
    if t in (1, 2, 12) or 100 <= t <= 117 or 400 <= t <= 405:
        return "rail"
    if t in (3, 11) or 200 <= t <= 209 or 700 <= t <= 716 or t == 800:
        return "bus"
    return None


_NIGHT = re.compile(r"^N[L]?\d+$")
_RAIL_REF = re.compile(r"^(RE|RB|IRE|MEX|S)\s?\d+[a-z]?$")


def is_special_line(ref: str, mode: str | None = None) -> bool:
    """Whether a line is a night line, an "Einsatzwagen" or rail replacement.

    Night lines (N1, NL2) and Einsatzwagen (E) duplicate regular routes;
    Schienenersatzverkehr (SEV, or a *bus* carrying a rail line's ref such as
    "RE2") is temporary. All would clutter a structural comparison.
    """
    if ref == "E" or ref.startswith("SEV") or _NIGHT.match(ref):
        return True
    return mode == "bus" and bool(_RAIL_REF.match(ref))


def _parse(d: str) -> dt.date:
    return dt.date(int(d[:4]), int(d[4:6]), int(d[6:8]))


def _period(d: dt.date) -> str:
    return "2027" if d >= NETWORK_CUTOFF else "2026"


def service_periods(
    calendar: Iterable[Mapping[str, str]], calendar_dates: Iterable[Mapping[str, str]]
) -> dict[str, set[str]]:
    """Which network periods each service_id runs in.

    Parameters
    ----------
    calendar
        Rows of ``calendar.txt``.
    calendar_dates
        Rows of ``calendar_dates.txt``.

    Returns
    -------
    dict
        service_id -> subset of {"2026", "2027"} (empty if it never runs).
    """
    active: dict[str, set[dt.date]] = {}
    for row in calendar:
        days = {i for i, wd in enumerate(_WEEKDAYS) if row.get(wd) == "1"}
        start, end = _parse(row["start_date"]), _parse(row["end_date"])
        dates = set()
        d = start
        while d <= end:
            if d.weekday() in days:
                dates.add(d)
            d += dt.timedelta(days=1)
        active[row["service_id"]] = dates
    for row in calendar_dates:
        dates = active.setdefault(row["service_id"], set())
        d = _parse(row["date"])
        if row["exception_type"] == "1":
            dates.add(d)
        else:
            dates.discard(d)
    return {sid: {_period(d) for d in dates} for sid, dates in active.items()}
