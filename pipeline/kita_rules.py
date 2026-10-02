"""Rules deciding whether a childcare facility takes children aged 3-6 ("Ü3").

Open data rarely states age groups: about 5 % of the OSM childcare facilities
in the map extents carry ``min_age``/``max_age`` (2026), and Ulm/Neu-Ulm publish
no open Kita dataset. The rules therefore combine explicit tags with German naming
conventions and return a status with a human-readable reason:

``confirmed``
    Evidence that 3-6-year-olds are taken: age tags, ``nursery=no``,
    ISCED level 02, or "Kindergarten" in the name (3-6 by definition).
``probable``
    A Kita-type or unclear name on ``amenity=kindergarten``; usually has 3-6
    groups, but some are Krippen (0-3) only.
``excluded``
    Krippe/Krabbelstube, Tagespflege, Hort, Tagesgruppe, play areas, or
    ``amenity=childcare`` without a kindergarten name.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

CONFIRMED = "confirmed"
PROBABLE = "probable"
EXCLUDED = "excluded"
_RANK = {CONFIRMED: 2, PROBABLE: 1, EXCLUDED: 0}
_UNNAMED = "ohne Namen, Altersgruppen nicht belegt"

_KINDERGARTEN = re.compile(r"kindergarten", re.I)
_EXCLUDE = (
    (re.compile(r"krippe|krabbel", re.I), "Krippe (unter 3) laut Name"),
    (re.compile(r"tagespflege|tagesmutter|tagesmütter|tagesvater", re.I), "Kindertagespflege laut Name"),
    (re.compile(r"hort\b", re.I), "Hort (Schulkinder) laut Name"),
    (re.compile(r"tagesgruppe", re.I), "Tagesgruppe (Jugendhilfe) laut Name"),
    (re.compile(r"småland|smaland|spielparadies|indoorspielplatz", re.I), "Spielbereich, keine Kita"),
)
_KITA = re.compile(
    r"\bkita\b|kindertagesst|kindertageseinr|tageseinrichtung für kinder|kinderhaus|kinderladen|familienzentrum",
    re.I,
)


def _age(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.split(";")[0].strip().replace(",", "."))
    except ValueError:
        return None


def _fmt(v: float) -> str:
    return f"{v:g}"


def classify(tags: Mapping[str, str]) -> tuple[str, str]:
    """Classify a facility as confirmed / probable / excluded Ü3 kindergarten.

    Parameters
    ----------
    tags
        OSM-style tags (``amenity``, ``name``, ``min_age``, ``max_age``,
        ``nursery``, ``isced:level``).

    Returns
    -------
    status, reason
        Status constant and a short German explanation for the map popup.
    """
    lo, hi = _age(tags.get("min_age")), _age(tags.get("max_age"))
    if lo is not None and lo >= 6:
        return EXCLUDED, f"Alter ab {_fmt(lo)} (OSM): Schulkinder"
    if hi is not None and hi <= 3:
        return EXCLUDED, f"Alter bis {_fmt(hi)} (OSM): Krippe"
    if lo is not None and lo >= 3:
        return CONFIRMED, f"Alter ab {_fmt(lo)} (OSM)"
    if lo is not None and hi is not None and hi > 3:
        return CONFIRMED, f"Alter {_fmt(lo)}–{_fmt(hi)} (OSM)"

    nursery = tags.get("nursery")
    if nursery == "only":
        return EXCLUDED, "nur Krippe (OSM nursery=only)"
    if nursery == "no":
        return CONFIRMED, "ohne Krippe (OSM nursery=no)"
    levels = {v.strip() for v in tags.get("isced:level", "").split(";")}
    if "02" in levels:
        return CONFIRMED, "Elementarbereich (ISCED 02)"
    if levels == {"01"}:
        return EXCLUDED, "Krippenbereich (ISCED 01)"

    name = tags.get("name", "") or ""
    if _KINDERGARTEN.search(name):
        return CONFIRMED, "„Kindergarten“ im Namen"
    for pattern, reason in _EXCLUDE:
        if pattern.search(name):
            return EXCLUDED, reason
    if tags.get("amenity") == "childcare":
        # In German OSM data this tag is used for Krippen, Tagespflege and Horte.
        return EXCLUDED, "amenity=childcare ohne Kindergarten-Namen"
    if _KITA.search(name):
        return PROBABLE, "Kita, Altersgruppen nicht belegt"
    if name:
        return PROBABLE, "Name ohne Hinweis auf Altersgruppen"
    return PROBABLE, _UNNAMED


def _norm(name: str) -> str:
    return re.sub(r"\W+", " ", name.lower()).strip()


def _evidence(item: Mapping[str, Any]) -> int:
    # "probable" is only the default for an untagged, unnamed facility, not evidence.
    return -1 if item.get("reason") == _UNNAMED else _RANK[item["status"]]


def dedupe(items: list[dict[str, Any]], same_name_m: float = 150.0, near_m: float = 60.0) -> list[dict[str, Any]]:
    """Merge duplicate mappings of one facility (node + building, split areas).

    Items need ``name``, ``x``, ``y`` (metres) and ``status``. Two items are the
    same facility if they share a normalised name within ``same_name_m``, or lie
    within ``near_m`` and one is unnamed or its name is contained in the other
    ("Drachenhöhle" / "Kita Drachenhöhle"). Different names that merely share a
    stem ("Herz-Jesu Kindergarten" / "Herz-Jesu Kindertagesstätte") are kept:
    mappers use them for separate facilities. The merged entry keeps the first
    position in the list; status, reason and name come from the item with the
    stronger evidence (on a tie, the longer name is kept).

    Returns
    -------
    list
        Deduplicated items in input order.
    """
    kept: list[dict[str, Any]] = []
    for item in items:
        match = None
        a = _norm(item["name"])
        for i, k in enumerate(kept):
            d = math.hypot(item["x"] - k["x"], item["y"] - k["y"])
            b = _norm(k["name"])
            if a and a == b and d <= same_name_m:
                match = i
                break
            if d <= near_m and (not a or not b or f" {a} " in f" {b} " or f" {b} " in f" {a} "):
                match = i
                break
        if match is None:
            kept.append(item)
            continue
        k = kept[match]
        ei, ek = _evidence(item), _evidence(k)
        merged = dict(item if ei > ek else k)
        merged["x"], merged["y"] = k["x"], k["y"]
        if ei == ek:
            merged["name"] = max(k["name"], item["name"], key=len)
        elif not merged["name"]:
            merged["name"] = item["name"] or k["name"]
        kept[match] = merged
    return kept


def merge_sources(
    osm: list[dict[str, Any]], official: list[dict[str, Any]], max_m: float = 80.0
) -> list[dict[str, Any]]:
    """Cross-check OSM facilities against an official list.

    Names differ between the sources ("Kath. Kindergarten St. Peter" vs
    "Kindergarten St. Peter und Paul"), so items are paired by distance only:
    closest pairs first, each item used at most once. A pair keeps the OSM
    position, takes status, reason and name from the item with the stronger
    evidence (OSM on a tie) and lists both sources; unpaired official items
    are appended.

    Parameters
    ----------
    osm, official
        Items with ``name``, ``x``, ``y`` (metres), ``status``, ``reason``
        and ``source``. ``osm`` should include excluded items, so the official
        list can overrule a misleading OSM tag.
    max_m
        Largest distance treated as the same facility.

    Returns
    -------
    list
        OSM items (in input order, merged where paired), then unpaired
        official items.
    """
    pairs = sorted(
        (math.hypot(o["x"] - f["x"], o["y"] - f["y"]), i, j) for i, o in enumerate(osm) for j, f in enumerate(official)
    )
    match: dict[int, int] = {}
    used: set[int] = set()
    for d, i, j in pairs:
        if d > max_m:
            break
        if i in match or j in used:
            continue
        match[i] = j
        used.add(j)
    out = []
    for i, o in enumerate(osm):
        if i not in match:
            out.append(o)
            continue
        f = official[match[i]]
        better = f if _RANK[f["status"]] > _RANK[o["status"]] else o
        out.append(
            {
                **o,
                "name": better["name"] or o["name"] or f["name"],
                "status": better["status"],
                "reason": better["reason"],
                "source": f"{o['source']} + {f['source']}",
            }
        )
    out.extend(f for j, f in enumerate(official) if j not in used)
    return out
