"""Kindergartens taking 3-6-year-olds ("Ü3") within 2 km of each ring centre.

OSM is the base for both cities. In Karlsruhe the city's POI list (categories
"Kindergärten" and "Kindertagesstätten") is merged in when it could be
downloaded; Ulm and Neu-Ulm publish no such list. Each facility is classified
by :mod:`kita_rules`; excluded ones (Krippen, Tagespflege, Horte, ...) are
written to a separate file for review but not published.

Outputs per city in ``data/layers/<city>/``: ``kitas.geojsonl`` (shown on the
map) and ``kitas_excluded.geojsonl``; a summary goes to ``data/kitas_info.json``.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry import Point, shape

from .annotations import read_geojsonl, to_utm, to_wgs84
from .config import City, Project, data_dir
from .download import kitas_ka_path
from .frames import city_frame
from .kita_rules import CONFIRMED, EXCLUDED, PROBABLE, classify, dedupe, merge_sources
from .metrics import circle_utm
from .osm import feature_line, layer_dir

log = logging.getLogger(__name__)

# "The 2 km radius" of the map: the innermost distance ring.
RADIUS_KM = 2.0
# Facilities just outside the circle still take part in matching, so a
# facility inside is not left unmatched because its partner lies outside.
MATCH_MARGIN_M = 150.0
OFFICIAL_LABEL = "Stadt Karlsruhe"
# City-map categories; "Kindergärten" are 3-6 by definition, the "Kindertagesstätten"
# list mixes Krippen and mixed-age Kitas, so its names go through the rules.
OFFICIAL_CONFIRMED = {"Kindergärten"}


def osm_url(osm_id: str) -> str:
    """Link to an OSM object from the ids written by :mod:`osm`.

    Area ids from osmium encode the source: ``2 * way_id`` or ``2 * relation_id + 1``.
    """
    kind, num = osm_id[0], int(osm_id[1:])
    if kind == "n":
        path = f"node/{num}"
    else:
        path = f"way/{num // 2}" if num % 2 == 0 else f"relation/{num // 2}"
    return f"https://www.openstreetmap.org/{path}"


def osm_items(city_id: str) -> list[dict[str, Any]]:
    """Classified OSM childcare facilities of one city (UTM coordinates)."""
    items = []
    for props, g in read_geojsonl(layer_dir(city_id) / "childcare.geojsonl"):
        status, reason = classify(props)
        p = to_utm(g)
        items.append(
            {
                "name": props.get("name", ""),
                "x": p.x,
                "y": p.y,
                "status": status,
                "reason": reason,
                "source": "OSM",
                "osm": osm_url(props["osm_id"]),
            }
        )
    return items


def official_items(project: Project, city: City) -> list[dict[str, Any]] | None:
    """Classified entries of the official list, or ``None`` if not available.

    All categories must be present and usable: a partial list would make
    missing entries look like OSM-only facilities. The source is optional, so
    an unusable answer (an ArcGIS error body sent with HTTP 200, coordinates
    in another CRS) falls back to OSM instead of failing the build.
    """
    if city.id != "ka":
        return None
    extent = shapely.box(*city_frame(project, city).data_bbox_wgs84)
    items = []
    for category in project.sources["kitas_ka"]["urls"]:
        path = kitas_ka_path(category)
        if not path.exists():
            return None
        fc = json.loads(path.read_text(encoding="utf-8"))
        if fc.get("properties", {}).get("exceededTransferLimit"):
            log.warning("Karlsruhe list %r is truncated by the server's record limit", category)
        geoms = [(f, shape(f["geometry"])) for f in fc.get("features", []) if f.get("geometry")]
        geoms = [(f, g) for f, g in geoms if extent.intersects(g)]
        if not geoms:
            log.warning("Karlsruhe list %r has no entries in the map extent; using OSM only", category)
            return None
        for f, g in geoms:
            name = (f.get("properties") or {}).get("name") or ""
            if category in OFFICIAL_CONFIRMED:
                status, reason = CONFIRMED, f"Stadt Karlsruhe: Kategorie „{category}“"
            else:
                status, reason = classify({"amenity": "kindergarten", "name": name})
                if status == PROBABLE:
                    reason = "Stadt Karlsruhe: Kindertagesstätte, Altersgruppen nicht angegeben"
            p = to_utm(g.representative_point())
            items.append(
                {
                    "name": name,
                    "x": p.x,
                    "y": p.y,
                    "status": status,
                    "reason": reason,
                    "source": OFFICIAL_LABEL,
                    "category": category,
                }
            )
    return items


def _write(path: Path, items: list[dict[str, Any]], keys: tuple[str, ...]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for i in items:
            props = {k: i[k] for k in keys if i.get(k)}
            fh.write(feature_line(props, to_wgs84(Point(i["x"], i["y"]))))


def build_city(project: Project, city: City) -> dict[str, Any]:
    """Classify, merge and clip the facilities of one city; write its layers.

    Returns
    -------
    dict
        Counts for ``data/kitas_info.json``.
    """
    circle = circle_utm(city, RADIUS_KM * 1000)
    near = circle.buffer(MATCH_MARGIN_M)

    def within(items: list[dict[str, Any]], area: shapely.Geometry) -> list[dict[str, Any]]:
        return [i for i in items if area.contains(Point(i["x"], i["y"]))]

    osm = dedupe(within(osm_items(city.id), near))
    official = official_items(project, city)
    info: dict[str, Any] = {"official_list": official is not None}
    if official is not None:
        official = dedupe(within(official, near))
        merged = merge_sources(osm, official)
        inside = within(official, circle)
        info["official_in_radius"] = len(inside)
        info["official_matched_osm"] = sum(1 for i in within(merged, circle) if "+" in i["source"])
        info["official_only"] = sum(1 for i in within(merged, circle) if i["source"] == OFFICIAL_LABEL)
    else:
        merged = osm
    # Pairing may leave one facility twice (e.g. its Krippe and Kita mapped apart).
    final = dedupe(within(merged, circle))
    kept = [i for i in final if i["status"] != EXCLUDED]
    excluded = [i for i in final if i["status"] == EXCLUDED]
    d = layer_dir(city.id)
    _write(d / "kitas.geojsonl", kept, ("name", "status", "reason", "source", "osm"))
    # Not published: names of excluded entries can be private Tagespflege persons.
    _write(d / "kitas_excluded.geojsonl", excluded, ("name", "status", "reason", "source", "osm", "category"))
    status = Counter(i["status"] for i in final)
    info.update(
        {
            "confirmed": status[CONFIRMED],
            "probable": status[PROBABLE],
            "excluded": status[EXCLUDED],
            "excluded_reasons": dict(Counter(i["reason"] for i in excluded).most_common()),
            "sources": dict(Counter(i["source"] for i in kept)),
        }
    )
    return info


def run(project: Project) -> dict[str, Any]:
    """Build the Kita layers for all cities and write ``data/kitas_info.json``."""
    if RADIUS_KM * 1000 not in project.ring_radii_m:
        raise ValueError(f"Kita radius {RADIUS_KM} km is not one of the distance rings")
    result: dict[str, Any] = {"radius_km": RADIUS_KM, "cities": {}}
    for city in project.cities:
        info = build_city(project, city)
        log.info("Kitas %s: %s", city.id, info)
        result["cities"][city.id] = info
    (data_dir() / "kitas_info.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    return result
