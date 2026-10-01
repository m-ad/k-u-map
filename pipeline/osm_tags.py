"""Pure tag-classification rules for OSM features.

Kept free of I/O so the rules can be unit-tested; ``osm.py`` applies them while
streaming the PBF.
"""

from __future__ import annotations

from collections.abc import Mapping

Tags = Mapping[str, str]

# Cycle infrastructure categories, ordered for deterministic output.
CYCLE_KINDS = ("busway", "lane", "path", "street", "track")

_SIDE_KEYS = ("cycleway", "cycleway:left", "cycleway:right", "cycleway:both")
_SIDE_VALUES = {
    "track": "track",
    "opposite_track": "track",
    "lane": "lane",
    "opposite_lane": "lane",
    "share_busway": "busway",
    "opposite_share_busway": "busway",
}
_PATH_HIGHWAYS = {"path", "footway", "pedestrian", "bridleway", "track"}
_NO_ACCESS = {"private", "no"}


def classify_cycle(tags: Tags) -> list[str]:
    """Cycle infrastructure categories carried by a way.

    Categories: ``track`` (separated cycleway, standalone or road-side),
    ``path`` (foot/cycle path with ``bicycle=designated``), ``lane`` (painted lane),
    ``busway`` (bus lane shared with bikes), ``street`` (Fahrradstraße).
    ``cycleway*=separate`` is skipped because the separate way is counted itself;
    ``shared_lane`` (sharrows) is not counted as infrastructure.

    Parameters
    ----------
    tags
        OSM tags of a way.

    Returns
    -------
    list of str
        Sorted unique categories; empty if the way carries none.
    """
    hw = tags.get("highway")
    if hw is None or hw in ("construction", "proposed", "platform"):
        return []
    if tags.get("access") in _NO_ACCESS and tags.get("bicycle") not in ("yes", "designated"):
        return []

    kinds: set[str] = set()
    if hw == "cycleway":
        kinds.add("track")
    elif hw in _PATH_HIGHWAYS and tags.get("bicycle") == "designated":
        kinds.add("path")
    if tags.get("bicycle_road") == "yes" or tags.get("cyclestreet") == "yes":
        kinds.add("street")
    for key in _SIDE_KEYS:
        kind = _SIDE_VALUES.get(tags.get(key, ""))
        if kind:
            kinds.add(kind)
    return sorted(kinds)


_ROAD_CLASSES = {
    "motorway": "motorway",
    "motorway_link": "motorway",
    "trunk": "trunk",
    "trunk_link": "trunk",
    "primary": "primary",
    "primary_link": "primary",
    "secondary": "secondary",
    "secondary_link": "secondary",
    "tertiary": "tertiary",
    "tertiary_link": "tertiary",
    "unclassified": "minor",
    "residential": "minor",
    "living_street": "minor",
    "road": "minor",
    "service": "service",
    "pedestrian": "pedestrian",
    "footway": "path",
    "path": "path",
    "cycleway": "path",
    "bridleway": "path",
    "steps": "path",
    "corridor": "path",
    "track": "track",
}


def classify_road(tags: Tags) -> str | None:
    """Rendering class of a highway way, or ``None`` if it is not drawn as a line.

    Parameters
    ----------
    tags
        OSM tags of a way.

    Returns
    -------
    str or None
        One of motorway, trunk, primary, secondary, tertiary, minor, service,
        pedestrian, path, track.
    """
    if tags.get("area") == "yes":
        return None
    return _ROAD_CLASSES.get(tags.get("highway", ""))


def classify_rail(tags: Tags) -> str | None:
    """Rail class: ``rail``, ``tram``, ``rail_service``/``tram_service`` (yards, sidings) or ``None``.

    Parameters
    ----------
    tags
        OSM tags of a way.

    Returns
    -------
    str or None
        Rendering class.
    """
    r = tags.get("railway")
    service = tags.get("service") in ("yard", "siding", "spur", "crossover")
    if r == "tram":
        # Depot and siding tracks are not part of the passenger network.
        return "tram_service" if service else "tram"
    if r in ("rail", "light_rail", "narrow_gauge", "subway"):
        return "rail_service" if service else "rail"
    return None


_LANDUSE = {
    ("landuse", "forest"): "forest",
    ("natural", "wood"): "forest",
    ("leisure", "park"): "park",
    ("leisure", "garden"): "park",
    ("landuse", "recreation_ground"): "park",
    ("landuse", "village_green"): "park",
    ("leisure", "playground"): "park",
    ("leisure", "nature_reserve"): None,
    ("landuse", "grass"): "grass",
    ("landuse", "meadow"): "grass",
    ("natural", "grassland"): "grass",
    ("natural", "heath"): "grass",
    ("natural", "scrub"): "grass",
    ("landuse", "allotments"): "allotments",
    ("landuse", "farmland"): "farmland",
    ("landuse", "orchard"): "farmland",
    ("landuse", "vineyard"): "farmland",
    ("landuse", "farmyard"): "farmland",
    ("landuse", "cemetery"): "cemetery",
    ("amenity", "grave_yard"): "cemetery",
    ("landuse", "residential"): "residential",
    ("landuse", "commercial"): "commercial",
    ("landuse", "retail"): "commercial",
    ("landuse", "industrial"): "industrial",
    ("landuse", "railway"): "industrial",
    ("leisure", "pitch"): "sport",
    ("leisure", "sports_centre"): "sport",
    ("leisure", "stadium"): "sport",
    ("leisure", "golf_course"): "sport",
    ("natural", "wetland"): "wetland",
}
# Order matters when a feature has several keys (e.g. leisure=park + landuse=grass).
_LANDUSE_KEYS = ("leisure", "amenity", "landuse", "natural")


def classify_landuse(tags: Tags) -> str | None:
    """Land-use/green class of an area, or ``None``.

    Parameters
    ----------
    tags
        OSM tags of an area.

    Returns
    -------
    str or None
        One of forest, park, grass, allotments, farmland, cemetery, residential,
        commercial, industrial, sport, wetland.
    """
    for key in _LANDUSE_KEYS:
        v = tags.get(key)
        if v is not None and (key, v) in _LANDUSE:
            cls = _LANDUSE[(key, v)]
            if cls is not None:
                return cls
    return None


def is_water_area(tags: Tags) -> bool:
    """Whether an area is a water body (rivers drawn as polygons, lakes, basins)."""
    return (
        tags.get("natural") == "water"
        or tags.get("waterway") == "riverbank"
        or tags.get("landuse") in ("reservoir", "basin")
    )


WATERWAY_LINES = ("river", "canal", "stream", "ditch", "drain")


def cycle_sides(tags: Tags) -> dict[str, int]:
    """Number of physical facilities per cycle category on a way.

    Road-side lanes/tracks count once per side so that "km of lanes" compares
    like-for-like with cycleways mapped as separate ways on each side.
    Side-specific keys take precedence over the plain ``cycleway`` key; a plain
    ``cycleway=lane`` means both sides unless the road is one-way.

    Parameters
    ----------
    tags
        OSM tags of a way.

    Returns
    -------
    dict
        Category -> facility count (1 or 2).
    """
    kinds = classify_cycle(tags)
    if not kinds:
        return {}
    out: dict[str, int] = {}
    hw = tags.get("highway")
    if hw == "cycleway":
        out["track"] = 1
    elif hw in _PATH_HIGHWAYS and tags.get("bicycle") == "designated":
        out["path"] = 1
    if "street" in kinds:
        out["street"] = 1

    def add(kind: str | None, n: int) -> None:
        if kind:
            out[kind] = out.get(kind, 0) + n

    sided = [k for k in ("cycleway:left", "cycleway:right", "cycleway:both") if k in tags]
    if sided:
        add(_SIDE_VALUES.get(tags.get("cycleway:left", "")), 1)
        add(_SIDE_VALUES.get(tags.get("cycleway:right", "")), 1)
        add(_SIDE_VALUES.get(tags.get("cycleway:both", "")), 2)
    else:
        v = tags.get("cycleway", "")
        oneway = tags.get("oneway") in ("yes", "-1", "1")
        add(_SIDE_VALUES.get(v), 1 if v.startswith("opposite") or oneway else 2)
    return out
