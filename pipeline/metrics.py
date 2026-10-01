"""Comparison metrics within 2/4/6 km of each city's ring centre.

All geometry is evaluated in UTM 32N (metres). UTM scale error near Karlsruhe
and Ulm is below 0.05 %, far smaller than the data uncertainty. Circles are
geodesic (see :func:`frames.geodesic_ring`).
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import rasterio
import rasterio.features
import shapely
from shapely.geometry import LineString, Polygon

from .annotations import read_geojsonl, to_utm
from .config import City, Project, data_dir
from .frames import TO_UTM, geodesic_ring
from .osm import layer_dir
from .osm_tags import CYCLE_KINDS

log = logging.getLogger(__name__)

TRAM_CORRIDOR_M = 8.0


def circle_utm(city: City, radius_m: float) -> Polygon:
    """Geodesic circle around the city's ring centre, in UTM metres."""
    c = city.ring_center
    return to_utm(Polygon(geodesic_ring(c.lon, c.lat, radius_m, n=360)))


def network_length(lines: list[LineString], corridor_m: float = TRAM_CORRIDOR_M) -> float:
    """Route length of a network whose parallel tracks are mapped separately.

    OSM maps double tram track as two ways ~3.5 m apart. Ways are taken longest
    first; each contributes only its part outside a corridor around the ways
    already counted, so a parallel second track adds (almost) nothing. Unlike a
    raster skeleton this has no orientation bias.

    Parameters
    ----------
    lines
        Track geometries in metres.
    corridor_m
        Half-width of the corridor; must exceed the track spacing incl. stops
        with centre platforms.

    Returns
    -------
    float
        Length in metres.
    """
    total = 0.0
    corridor = None
    for line in sorted((g for g in lines if not g.is_empty), key=lambda g: g.length, reverse=True):
        part = line if corridor is None else shapely.difference(line, corridor)
        # Ignore slivers where a way just grazes the corridor edge.
        pieces = [q for q in getattr(part, "geoms", [part]) if q.length > corridor_m]
        total += sum(q.length for q in pieces)
        buf = line.buffer(corridor_m, quad_segs=4)
        corridor = buf if corridor is None else corridor.union(buf)
    return total


def city_metrics(project: Project, city: City) -> dict:
    """Compute all comparison metrics for one city.

    Returns
    -------
    dict
        ``{"radii": {"2": {...}, "4": {...}, "6": {...}}, ...}``.
    """
    d = layer_dir(city.id)
    buildings = [to_utm(g) for _, g in read_geojsonl(d / "buildings.geojsonl")]
    btree = shapely.STRtree(buildings)
    trams = [to_utm(g) for p, g in read_geojsonl(d / "rail.geojsonl") if p["class"] == "tram"]
    cycle = [(p["kind"], p.get("sides", 1), to_utm(g)) for p, g in read_geojsonl(d / "cycle.geojsonl")]
    ctree = shapely.STRtree([g for _, _, g in cycle])
    stops = [(p, to_utm(g)) for p, g in read_geojsonl(d / "transit_stops.geojsonl")]
    lines = [(p, to_utm(g)) for p, g in read_geojsonl(d / "transit_lines.geojsonl")]

    with rasterio.open(data_dir() / "dem" / f"{city.id}_5m.tif") as dem:
        elev = dem.read(1)
        dem_transform = dem.transform
    with rasterio.open(data_dir() / "dem" / f"{city.id}_source.tif") as src:
        source = src.read(1)
    c = city.ring_center
    cx, cy = TO_UTM.transform(c.lon, c.lat)
    col, row = ~dem_transform * (cx, cy)
    center_elev = float(elev[int(row), int(col)])

    out: dict = {"center": {"name": c.name, "lon": c.lon, "lat": c.lat, "elevation_m": round(center_elev, 1)}}
    radii = {}
    for r in project.ring_radii_m:
        circle = circle_utm(city, r)
        area = circle.area
        res: dict = {"area_km2": round(area / 1e6, 2)}

        idx = btree.query(circle, predicate="intersects")
        built = shapely.union_all([buildings[i] for i in idx]).intersection(circle)
        res["building_share_pct"] = round(100 * built.area / area, 1)
        res["building_count"] = int(len(idx))

        tram_parts = [t.intersection(circle) for t in trams if t.intersects(circle)]
        flat = [q for t in tram_parts for q in getattr(t, "geoms", [t]) if isinstance(q, LineString)]
        res["tram_km"] = round(network_length(flat) / 1000, 1)

        km = dict.fromkeys(CYCLE_KINDS, 0.0)
        for i in ctree.query(circle, predicate="intersects"):
            kind, sides, g = cycle[i]
            km[kind] += g.intersection(circle).length * sides / 1000
        res["cycle_km"] = {k: round(v, 1) for k, v in km.items()}
        res["cycle_km_total"] = round(sum(km.values()), 1)

        inside = [p for p, g in stops if circle.contains(g)]
        nets = sorted({n for p in inside for n in p["networks"].split(",")})
        res["stops"] = {
            net: {
                "total": sum(1 for p in inside if net in p["networks"].split(",")),
                "tram": sum(1 for p in inside if net in p["networks"].split(",") and "tram" in p["modes"]),
            }
            for net in nets
        }
        res["lines"] = {
            net: sorted(
                {p["ref"] for p, g in lines if p["network"] == net and g.intersects(circle)}, key=lambda x: (len(x), x)
            )
            for net in sorted({p["network"] for p, _ in lines})
        }

        mask = rasterio.features.geometry_mask([circle], out_shape=elev.shape, transform=dem_transform, invert=True)
        official = mask & (source != 3) & np.isfinite(elev)
        vals = elev[official]
        res["elevation_m"] = {
            "min": round(float(vals.min()), 1),
            "max": round(float(vals.max()), 1),
            "p5": round(float(np.percentile(vals, 5)), 1),
            "p95": round(float(np.percentile(vals, 95)), 1),
            "range": round(float(vals.max() - vals.min()), 1),
            "range_p5_p95": round(float(np.percentile(vals, 95) - np.percentile(vals, 5)), 1),
            # Share of the circle more than 30 m above the centre: a rough proxy for
            # "you will climb to get there".
            "share_30m_above_center_pct": round(100 * float((vals > center_elev + 30).mean()), 1),
            "official_coverage_pct": round(100 * float(official.sum() / max(mask.sum(), 1)), 1),
        }
        radii[f"{r / 1000:g}"] = res
    out["radii"] = radii
    return out


def run(project: Project) -> dict:
    """Compute metrics for all cities and write ``data/metrics.json``."""
    result = {
        "generated": dt.date.today().isoformat(),
        "method": {
            "crs": "ETRS89 / UTM 32N (EPSG:25832)",
            "circles": "geodesic, centred on the ring centre of each city",
            "building_share": "union of OSM building footprints inside the circle / circle area",
            "tram_km": "OSM railway=tram without depot/siding tracks; a track counts only outside an "
            f"{TRAM_CORRIDOR_M:g} m corridor around longer tracks, so double track counts once",
            "cycle_km": "OSM ways by category; road-side lanes/tracks counted per side",
            "stops": "GTFS stations (platforms merged by parent station or name) served by tram/bus lines",
            "elevation": "official DGM1 (LGL BW, LDBV BY) resampled to 5 m; Copernicus fill excluded",
        },
        "cities": {},
    }
    for city in project.cities:
        result["cities"][city.id] = city_metrics(project, city)
        log.info("metrics %s: %s", city.id, json.dumps(result["cities"][city.id]["radii"]["2"]))
    (data_dir() / "metrics.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    return result
