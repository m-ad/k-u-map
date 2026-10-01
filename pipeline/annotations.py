"""District boundaries, labels, reference points and distance rings.

Karlsruhe uses the official Stadtteile from the Transparenzportal. Ulm's open
data portal has no district boundaries, so Ulm and Neu-Ulm use OSM
``admin_level=10`` (plus admin_level=9 Ortschaften not subdivided further).
Every label is tied to the polygon that contains it; tests check this.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry import LineString, Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import polylabel, transform

from .config import City, Project, data_dir
from .frames import TO_UTM, TO_WGS84, bike_minutes, city_frame, geodesic_ring
from .osm import feature_line, layer_dir

log = logging.getLogger(__name__)

# OSM municipality polygons per panel: (name, admin_level). Ulm is a Stadtkreis
# (level 6 in German OSM), Neu-Ulm a Große Kreisstadt (level 8).
MUNICIPALITIES = {
    "ka": [("Karlsruhe", 6)],
    "ulm": [("Ulm", 6), ("Neu-Ulm", 8)],
}
# Generic names that need the town as suffix to be unambiguous on a shared map.
AMBIGUOUS = {"Stadtmitte", "Mitte", "Weststadt", "Oststadt", "Nordstadt", "Südstadt", "Innenstadt"}


@dataclass
class District:
    """One district polygon with its display label."""

    name: str
    label: str
    municipality: str
    geom: BaseGeometry  # WGS84


def read_geojsonl(path: Path) -> Iterator[tuple[dict[str, Any], BaseGeometry]]:
    """Iterate (properties, geometry) over a GeoJSONL file."""
    if not path.exists():
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            f = json.loads(line)
            yield f["properties"], shape(f["geometry"])


def to_utm(g: BaseGeometry) -> BaseGeometry:
    """Project a WGS84 geometry to UTM 32N."""
    return transform(TO_UTM.transform, g)


def to_wgs84(g: BaseGeometry) -> BaseGeometry:
    """Project a UTM 32N geometry to WGS84."""
    return transform(TO_WGS84.transform, g)


def label_point(g: BaseGeometry) -> Point:
    """Pole of inaccessibility of the largest part, computed in metres.

    Unlike the centroid, this always lies inside the polygon (also for
    crescent-shaped districts along a river).
    """
    utm = to_utm(g)
    parts = list(getattr(utm, "geoms", [utm]))
    biggest = max(parts, key=lambda p: p.area)
    return to_wgs84(polylabel(biggest, tolerance=5.0))


def municipality_polygons(city_id: str) -> dict[str, BaseGeometry]:
    """OSM polygons of the municipalities shown in a panel."""
    wanted = MUNICIPALITIES[city_id]
    out: dict[str, BaseGeometry] = {}
    for props, g in read_geojsonl(layer_dir(city_id) / "admin.geojsonl"):
        for name, level in wanted:
            if props["name"] == name and props["admin_level"] == level:
                out[name] = g
    missing = {n for n, _ in wanted} - out.keys()
    if missing:
        raise ValueError(f"municipality polygons missing in OSM data: {missing}")
    return out


def districts_ka(project: Project) -> list[District]:
    """Official Karlsruhe Stadtteile."""
    path = data_dir() / "cache" / "districts" / "ka_stadtteile.json"
    fc = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for f in fc["features"]:
        name = f["properties"]["NAME"]
        out.append(District(name, name, "Karlsruhe", shapely.make_valid(shape(f["geometry"]))))
    return out


def districts_osm(city_id: str) -> list[District]:
    """OSM districts (admin_level 10, plus undivided level-9 Ortschaften) of each municipality."""
    munis = municipality_polygons(city_id)
    admin = list(read_geojsonl(layer_dir(city_id) / "admin.geojsonl"))
    out: list[District] = []
    for muni, mgeom in munis.items():
        prepared = shapely.prepared.prep(mgeom.buffer(0.0005))
        lvl10 = [(p, g) for p, g in admin if p["admin_level"] == 10 and prepared.contains(g.representative_point())]
        lvl9 = [(p, g) for p, g in admin if p["admin_level"] == 9 and prepared.contains(g.representative_point())]
        covered = shapely.union_all([g for _, g in lvl10]) if lvl10 else None
        chosen = list(lvl10)
        for p, g in lvl9:
            share = 0.0 if covered is None else to_utm(g.intersection(covered)).area / to_utm(g).area
            if share < 0.5:
                chosen.append((p, g))
        for p, g in chosen:
            out.append(District(p["name"], p["name"], muni, g))
    # Disambiguate names that occur twice or are generic, by suffixing the smaller town.
    names = [d.name for d in out]
    primary_muni = MUNICIPALITIES[city_id][0][0]
    for d in out:
        if d.municipality != primary_muni and (names.count(d.name) > 1 or d.name in AMBIGUOUS):
            d.label = f"{d.name} ({d.municipality})"
    return out


def tier_of(label: str, city: City) -> int:
    """2 = primary interest, 1 = secondary, 0 = other."""
    if label in city.primary_districts:
        return 2
    if label in city.secondary_districts:
        return 1
    return 0


def build_city(project: Project, city: City) -> dict[str, int]:
    """Write districts, labels, reference points and rings for one city.

    Returns
    -------
    dict
        Feature counts per output layer.
    """
    d_dir = layer_dir(city.id)
    districts = districts_ka(project) if city.id == "ka" else districts_osm(city.id)
    frame = city_frame(project, city)
    extent = shapely.box(*frame.data_bbox_wgs84)

    # Municipality outlines: union of the official districts for KA (so labels and
    # boundaries are consistent), OSM polygons for Ulm/Neu-Ulm.
    if city.id == "ka":
        munis = {"Karlsruhe": shapely.union_all([d.geom for d in districts])}
    else:
        munis = municipality_polygons(city.id)

    counts: dict[str, int] = {}
    with open(d_dir / "districts.geojsonl", "w", encoding="utf-8") as fh:
        for d in districts:
            props = {"name": d.label, "municipality": d.municipality, "tier": tier_of(d.label, city)}
            fh.write(feature_line(props, d.geom))
    counts["districts"] = len(districts)
    with open(d_dir / "municipalities.geojsonl", "w", encoding="utf-8") as fh:
        for name, g in munis.items():
            fh.write(feature_line({"name": name}, g))

    labels: list[tuple[dict[str, Any], Point]] = []
    admin_names = {p["name"] for p, _ in read_geojsonl(d_dir / "admin.geojsonl")}
    district_names = {d.name for d in districts} | {d.label for d in districts} | admin_names
    for d in districts:
        pt = label_point(d.geom)
        area_km2 = to_utm(d.geom).area / 1e6
        labels.append(
            (
                {
                    "name": d.label,
                    "kind": "district",
                    "tier": tier_of(d.label, city),
                    "municipality": d.municipality,
                    "district": d.label,
                    "area_km2": round(area_km2, 2),
                },
                pt,
            )
        )

    # OSM quarter/neighbourhood names add finer places (Kuhberg, Fischerviertel, ...).
    for props, pt in read_geojsonl(d_dir / "places.geojsonl"):
        name, place = props["name"], props["place"]
        if not extent.contains(pt):
            continue
        if place in ("suburb", "quarter", "neighbourhood", "borough"):
            home = next((d for d in districts if d.geom.contains(pt)), None)
            if home is None or name in district_names or any(x in name for x in project.label_exclude):
                continue
            label = name
            if home.municipality != MUNICIPALITIES[city.id][0][0] and name in AMBIGUOUS:
                label = f"{name} ({home.municipality})"
            labels.append(
                (
                    {
                        "name": label,
                        "kind": "quarter" if place != "neighbourhood" else "neighbourhood",
                        "tier": tier_of(label, city),
                        "municipality": home.municipality,
                        "district": home.label,
                    },
                    pt,
                )
            )
        elif place in ("city", "town", "village"):
            inside = next((m for m, g in munis.items() if g.contains(pt)), None)
            # Villages incorporated into the cities are covered by district labels.
            if inside is not None and place == "village":
                continue
            labels.append(({"name": name, "kind": place, "tier": 0, "municipality": inside or ""}, pt))

    with open(d_dir / "labels.geojsonl", "w", encoding="utf-8") as fh:
        for props, pt in labels:
            fh.write(feature_line(props, pt))
    counts["labels"] = len(labels)

    with open(d_dir / "refpoints.geojsonl", "w", encoding="utf-8") as fh:
        for p in city.points:
            fh.write(feature_line({"id": p.id, "name": p.name, "kind": p.kind}, Point(p.lon, p.lat)))
    counts["refpoints"] = len(city.points)

    c = city.ring_center
    with open(d_dir / "rings.geojsonl", "w", encoding="utf-8") as fh:
        for r in project.ring_radii_m:
            minutes = bike_minutes(r, project.bike_speed_kmh)
            props = {
                "radius_km": r / 1000,
                "label": f"{r / 1000:g} km · ≈ {minutes} min Rad (Luftlinie)",
            }
            fh.write(feature_line(props, LineString(geodesic_ring(c.lon, c.lat, r))))
    counts["rings"] = len(project.ring_radii_m)
    return counts


def run(project: Project) -> dict[str, dict[str, int]]:
    """Build annotation layers for all cities."""
    out = {}
    for city in project.cities:
        out[city.id] = build_city(project, city)
        log.info("annotations %s: %s", city.id, out[city.id])
    return out
