"""Tests for OSM tag classification (cycle infrastructure, roads, land use)."""

import pytest

from pipeline.osm_tags import classify_cycle, classify_landuse, classify_rail, classify_road


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        # Separated infrastructure
        ({"highway": "cycleway"}, ["track"]),
        ({"highway": "secondary", "cycleway:both": "track"}, ["track"]),
        ({"highway": "secondary", "cycleway:right": "track"}, ["track"]),
        ({"highway": "service", "cycleway": "opposite_track"}, ["track"]),
        # Shared or segregated foot/cycle paths
        ({"highway": "footway", "bicycle": "designated"}, ["path"]),
        ({"highway": "path", "bicycle": "designated", "segregated": "yes"}, ["path"]),
        ({"highway": "track", "bicycle": "designated"}, ["path"]),
        ({"highway": "footway", "bicycle": "yes"}, []),
        ({"highway": "path"}, []),
        # On-road lanes, incl. side-specific keys
        ({"highway": "secondary", "cycleway:right": "lane"}, ["lane"]),
        ({"highway": "primary", "cycleway:left": "lane", "cycleway:left:lane": "advisory"}, ["lane"]),
        ({"highway": "tertiary", "cycleway": "lane"}, ["lane"]),
        ({"highway": "service", "cycleway": "opposite_lane"}, ["lane"]),
        # Bus lanes open to bikes
        ({"highway": "secondary", "cycleway": "share_busway"}, ["busway"]),
        ({"highway": "secondary", "cycleway:left": "share_busway", "cycleway:right": "lane"}, ["busway", "lane"]),
        # Bicycle streets (Fahrradstraße)
        ({"highway": "residential", "bicycle_road": "yes"}, ["street"]),
        ({"highway": "residential", "cyclestreet": "yes"}, ["street"]),
        ({"highway": "residential", "bicycle_road": "yes", "cycleway:both": "no"}, ["street"]),
        # Not infrastructure, or mapped elsewhere
        ({"highway": "secondary", "cycleway:both": "separate"}, []),
        ({"highway": "secondary", "cycleway": "no"}, []),
        ({"highway": "secondary", "cycleway": "shared_lane"}, []),
        ({"highway": "cycleway", "access": "private"}, []),
        ({"highway": "construction", "construction": "cycleway"}, []),
        ({"highway": "proposed", "proposed": "cycleway"}, []),
        ({"building": "yes", "bicycle_road": "yes"}, []),
    ],
)
def test_classify_cycle(tags: dict[str, str], expected: list[str]) -> None:
    assert classify_cycle(tags) == expected


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"highway": "motorway"}, "motorway"),
        ({"highway": "motorway_link"}, "motorway"),
        ({"highway": "trunk"}, "trunk"),
        ({"highway": "primary_link"}, "primary"),
        ({"highway": "secondary"}, "secondary"),
        ({"highway": "tertiary"}, "tertiary"),
        ({"highway": "residential"}, "minor"),
        ({"highway": "living_street"}, "minor"),
        ({"highway": "unclassified"}, "minor"),
        ({"highway": "service"}, "service"),
        ({"highway": "pedestrian"}, "pedestrian"),
        ({"highway": "footway"}, "path"),
        ({"highway": "cycleway"}, "path"),
        ({"highway": "steps"}, "path"),
        ({"highway": "track"}, "track"),
        ({"highway": "proposed"}, None),
        ({"highway": "bus_stop"}, None),
        ({"highway": "residential", "area": "yes"}, None),
    ],
)
def test_classify_road(tags: dict[str, str], expected: str | None) -> None:
    assert classify_road(tags) == expected


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"railway": "rail"}, "rail"),
        ({"railway": "rail", "service": "yard"}, "rail_service"),
        ({"railway": "light_rail"}, "rail"),
        ({"railway": "tram"}, "tram"),
        ({"railway": "abandoned"}, None),
        ({"railway": "disused"}, None),
        ({"railway": "platform"}, None),
    ],
)
def test_classify_rail(tags: dict[str, str], expected: str | None) -> None:
    assert classify_rail(tags) == expected


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"landuse": "forest"}, "forest"),
        ({"natural": "wood"}, "forest"),
        ({"leisure": "park"}, "park"),
        ({"landuse": "meadow"}, "grass"),
        ({"landuse": "allotments"}, "allotments"),
        ({"landuse": "farmland"}, "farmland"),
        ({"landuse": "cemetery"}, "cemetery"),
        ({"landuse": "residential"}, "residential"),
        ({"landuse": "retail"}, "commercial"),
        ({"landuse": "industrial"}, "industrial"),
        ({"leisure": "pitch"}, "sport"),
        ({"natural": "wetland"}, "wetland"),
        ({"building": "yes"}, None),
        ({"landuse": "construction"}, None),
    ],
)
def test_classify_landuse(tags: dict[str, str], expected: str | None) -> None:
    assert classify_landuse(tags) == expected


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        # Standalone ways count once regardless of direction.
        ({"highway": "cycleway"}, {"track": 1}),
        ({"highway": "footway", "bicycle": "designated"}, {"path": 1}),
        ({"highway": "residential", "bicycle_road": "yes"}, {"street": 1}),
        # Road-side infrastructure counts per side, so km compare like-for-like with
        # separately mapped cycleways on each side of a street.
        ({"highway": "secondary", "cycleway:both": "lane"}, {"lane": 2}),
        ({"highway": "secondary", "cycleway": "lane"}, {"lane": 2}),
        ({"highway": "secondary", "cycleway:right": "lane"}, {"lane": 1}),
        ({"highway": "secondary", "cycleway:left": "track", "cycleway:right": "lane"}, {"lane": 1, "track": 1}),
        ({"highway": "secondary", "oneway": "yes", "cycleway": "lane"}, {"lane": 1}),
        ({"highway": "service", "cycleway": "opposite_lane"}, {"lane": 1}),
    ],
)
def test_cycle_sides(tags: dict[str, str], expected: dict[str, int]) -> None:
    from pipeline.osm_tags import cycle_sides

    assert cycle_sides(tags) == expected
