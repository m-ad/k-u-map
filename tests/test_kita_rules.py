"""Tests for the Ü3 kindergarten classification rules."""

from __future__ import annotations

import pytest

from pipeline.kita_rules import CONFIRMED, EXCLUDED, PROBABLE, classify, dedupe, merge_sources


def kg(name: str | None = None, amenity: str = "kindergarten", **tags: str) -> dict[str, str]:
    t = {"amenity": amenity, **tags}
    if name is not None:
        t["name"] = name
    return t


@pytest.mark.parametrize(
    ("tags", "status"),
    [
        # Age tags are the strongest evidence.
        (kg("Haus Sonnenschein", min_age="3", max_age="6"), CONFIRMED),
        (kg("Haus Sonnenschein", min_age="1", max_age="6"), CONFIRMED),  # mixed Kita takes 3-6
        (kg("Haus Sonnenschein", min_age="0", max_age="3"), EXCLUDED),  # Krippe
        (kg("Haus Sonnenschein", max_age="3"), EXCLUDED),
        (kg("Haus Sonnenschein", min_age="6"), EXCLUDED),  # school-age care
        (kg("Haus Sonnenschein", nursery="only"), EXCLUDED),
        (kg("Haus Sonnenschein", nursery="no"), CONFIRMED),
        (kg("Haus Sonnenschein", **{"isced:level": "02"}), CONFIRMED),
        (kg("Haus Sonnenschein", **{"isced:level": "01"}), EXCLUDED),
        # "Kindergarten" in the name means 3-6 in German usage.
        (kg("Katholischer Kindergarten St. Hildegard"), CONFIRMED),
        (kg("Waldkindergarten Hardtwald"), CONFIRMED),
        (kg("Katholischer Kindergarten St. Bonifatius", amenity="childcare"), CONFIRMED),
        # Kita-type names usually include 3-6 groups, but some are 0-3 only.
        (kg("Städtische Kindertagesstätte Sybelstraße"), PROBABLE),
        (kg("Kita Südstadtstrolche"), PROBABLE),
        (kg("Montessori-Kinderhaus"), PROBABLE),
        (kg("Tageseinrichtung für Kinder Alpenstraße 40"), PROBABLE),
        # Unclear names on amenity=kindergarten stay, marked unconfirmed.
        (kg("Villa Kunterbunt"), PROBABLE),
        (kg(None), PROBABLE),
        # Not Ü3 kindergartens.
        (kg("Kinderkrippe Zauberland"), EXCLUDED),
        (kg("Krippe der evang. integrativen Kindertagesstätte Zachäus-Nest"), EXCLUDED),
        (kg("Krabbelstube Mäuse"), EXCLUDED),
        (kg("Kindertagespflege Gartenzwerge"), EXCLUDED),
        (kg("Großtagespflege Pusteblume"), EXCLUDED),
        (kg("Städtischer Schülerhort"), EXCLUDED),
        (kg("Hort der Arbeiterwohlfahrt"), EXCLUDED),
        (kg("Tagesgruppe Regenbogenfische"), EXCLUDED),
        (kg("IKEA Småland"), EXCLUDED),
        # amenity=childcare without a kindergarten name is mostly Krippe/Tagespflege/Hort.
        (kg("Villa Bambini", amenity="childcare"), EXCLUDED),
        # Explicit age data beats the name.
        (kg("Kinderkrippe und Kindergarten Arche", min_age="1", max_age="6"), CONFIRMED),
        (kg("Kindergarten Mini", max_age="3"), EXCLUDED),
    ],
)
def test_classify(tags: dict[str, str], status: str) -> None:
    got, reason = classify(tags)
    assert got == status, reason
    assert reason


def test_hort_word_inside_other_words_is_not_excluded() -> None:
    # "Hort" must match as a word; e.g. "Horthaus" is unusual but "Ahornweg" must not trip it.
    assert classify(kg("Kindergarten Ahornweg"))[0] == CONFIRMED


def test_dedupe_merges_same_name_nearby_and_unnamed_duplicates() -> None:
    items = [
        {"name": "Krippe Zauberwald", "x": 0.0, "y": 0.0, "status": EXCLUDED},
        {"name": "Krippe Zauberwald", "x": 40.0, "y": 0.0, "status": EXCLUDED},
        {"name": "Kindergarten St. Paul", "x": 1000.0, "y": 0.0, "status": CONFIRMED},
        {"name": "", "x": 1020.0, "y": 0.0, "status": PROBABLE},  # node next to the named area
        {"name": "", "x": 3000.0, "y": 0.0, "status": PROBABLE},  # genuinely separate
        {"name": "Kindergarten St. Paul", "x": 5000.0, "y": 0.0, "status": CONFIRMED},  # same name, far away
    ]
    out = dedupe(items)
    assert [(i["name"], i["x"]) for i in out] == [
        ("Krippe Zauberwald", 0.0),
        ("Kindergarten St. Paul", 1000.0),
        ("", 3000.0),
        ("Kindergarten St. Paul", 5000.0),
    ]


def test_dedupe_prefers_stronger_evidence() -> None:
    items = [
        {"name": "Kita Sonne", "x": 0.0, "y": 0.0, "status": PROBABLE},
        {"name": "Kita Sonne", "x": 10.0, "y": 0.0, "status": CONFIRMED},
    ]
    assert dedupe(items)[0]["status"] == CONFIRMED


def _item(name: str, x: float, status: str, source: str) -> dict:
    return {"name": name, "x": x, "y": 0.0, "status": status, "reason": f"{source}: {status}", "source": source}


def test_merge_sources_upgrades_matched_osm_items_and_adds_missing_ones() -> None:
    osm = [
        _item("Villa Bambini", 0.0, EXCLUDED, "OSM"),  # amenity=childcare, but the city lists it as Kindergarten
        _item("Kita Sonne", 1000.0, PROBABLE, "OSM"),
        _item("Kindergarten Mond", 2000.0, CONFIRMED, "OSM"),
    ]
    official = [
        _item("Kinderhaus Villa Bambini", 30.0, CONFIRMED, "Stadt"),
        _item("Kindergarten Mond", 2010.0, PROBABLE, "Stadt"),
        _item("Kindergarten Neu", 3000.0, CONFIRMED, "Stadt"),
    ]
    out = merge_sources(osm, official, max_m=80.0)
    assert [(i["name"], i["x"], i["status"], i["source"]) for i in out] == [
        # The stronger evidence supplies status, reason and name.
        ("Kinderhaus Villa Bambini", 0.0, CONFIRMED, "OSM + Stadt"),
        ("Kita Sonne", 1000.0, PROBABLE, "OSM"),
        ("Kindergarten Mond", 2000.0, CONFIRMED, "OSM + Stadt"),
        ("Kindergarten Neu", 3000.0, CONFIRMED, "Stadt"),
    ]
    assert out[0]["reason"] == "Stadt: confirmed"


def test_merge_sources_matches_each_osm_item_once_to_the_nearest() -> None:
    osm = [_item("A", 0.0, PROBABLE, "OSM")]
    official = [_item("far", 60.0, CONFIRMED, "Stadt"), _item("near", 10.0, CONFIRMED, "Stadt")]
    out = merge_sources(osm, official, max_m=80.0)
    assert [(i["name"], i["source"]) for i in out] == [("near", "OSM + Stadt"), ("far", "Stadt")]


def test_dedupe_merges_a_name_contained_in_a_nearby_name() -> None:
    items = [
        {"name": "Drachenhöhle", "x": 0.0, "y": 0.0, "status": PROBABLE},  # node inside the area below
        {"name": "Kita Drachenhöhle", "x": 22.0, "y": 0.0, "status": PROBABLE},
        # Different facility types of one parish, mapped as separate buildings: kept.
        {"name": "Herz-Jesu Kindergarten", "x": 1000.0, "y": 0.0, "status": CONFIRMED},
        {"name": "Herz-Jesu Kindertagesstätte", "x": 1032.0, "y": 0.0, "status": PROBABLE},
        # Containment alone is not enough at a distance.
        {"name": "Kindergarten Drachenhöhle", "x": 500.0, "y": 0.0, "status": CONFIRMED},
    ]
    out = dedupe(items)
    assert [i["name"] for i in out] == [
        "Kita Drachenhöhle",
        "Herz-Jesu Kindergarten",
        "Herz-Jesu Kindertagesstätte",
        "Kindergarten Drachenhöhle",
    ]


def test_dedupe_takes_the_name_with_the_evidence() -> None:
    # The Krippe section of a Kita: the merged point must not carry the Krippe's name.
    items = [
        {"name": "Krippe der Kita Zachäus", "x": 0.0, "y": 0.0, "status": EXCLUDED, "reason": "Krippe"},
        {"name": "Kita Zachäus", "x": 20.0, "y": 0.0, "status": PROBABLE, "reason": "Kita"},
    ]
    assert [(i["name"], i["status"]) for i in dedupe(items)] == [("Kita Zachäus", PROBABLE)]
    # An unnamed node next to a named Krippe is that Krippe, not an unclear Kita.
    unnamed = classify(kg(None))[1]
    items = [
        {"name": "", "x": 0.0, "y": 0.0, "status": PROBABLE, "reason": unnamed},
        {"name": "Krippe Mond", "x": 15.0, "y": 0.0, "status": EXCLUDED, "reason": "Krippe"},
    ]
    assert [(i["name"], i["status"]) for i in dedupe(items)] == [("Krippe Mond", EXCLUDED)]
