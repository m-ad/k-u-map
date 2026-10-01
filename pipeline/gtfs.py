"""Turn GTFS feeds into transit line and stop layers.

For each city feed: find stops inside the data extent, the trips serving them,
and the shapes of those trips; then merge shapes per line (mode + ref + network)
and clip them to the extent. Line colours come from ``routes.txt`` (SWU) or, where
the feed has none (KVV), from OSM route relations, else from a fixed palette.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import logging
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import shapely
from shapely.geometry import LineString, Point

from .config import Project, data_dir
from .frames import city_frame
from .gtfs_rules import NETWORK_CUTOFF, is_special_line, mode_of, service_periods
from .osm import feature_line, layer_dir

log = logging.getLogger(__name__)

# Fallback colours for lines without official colour (qualitative, legible on light
# and dark backgrounds).
PALETTE = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#ff7f0e")


@dataclass(frozen=True)
class FeedSpec:
    """One GTFS feed assigned to a city."""

    feed_id: str
    city_id: str
    path: Path
    # Fixed network label, or None to derive "2026"/"2027" from service dates.
    network: str | None


def _rows(zf: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    if name not in zf.namelist():
        return iter(())
    fh = io.TextIOWrapper(zf.open(name), encoding="utf-8-sig", newline="")
    return csv.DictReader(fh)


def _columns(zf: zipfile.ZipFile, name: str, cols: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
    # DictReader is too slow for the 486 MB KVV shapes.txt; index columns once.
    fh = io.TextIOWrapper(zf.open(name), encoding="utf-8-sig", newline="")
    reader = csv.reader(fh)
    header = next(reader)
    idx = [header.index(c) for c in cols]
    for row in reader:
        yield tuple(row[i] for i in idx)


def _colour(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lstrip("#")
    if len(v) == 6 and all(c in "0123456789abcdefABCDEF" for c in v):
        return "#" + v.lower()
    return None


def _named_colour(value: str) -> str | None:
    # OSM colour tags are usually hex but sometimes CSS names.
    return _colour(value) or (value.lower() if value.isalpha() else None)


def _distinct_variants(geoms: list[LineString], extent: shapely.Polygon, tol_deg: float = 1e-4) -> shapely.MultiLineString | None:
    """Clip shape variants and keep only the sections not already covered.

    ``union_all`` on hundreds of nearly coincident variants explodes in memory
    (noding lines against lines), so each variant is instead cut against a ~10 m
    corridor around the variants kept so far (line-polygon difference is cheap).
    """
    parts: list[LineString] = []
    for g in geoms:
        c = shapely.intersection(g, extent).simplify(0.00001)
        parts.extend(p for p in getattr(c, "geoms", [c]) if isinstance(p, LineString) and not p.is_empty)
    parts.sort(key=lambda p: p.length, reverse=True)
    kept: list[LineString] = []
    corridor = None
    for p in parts:
        new = p if corridor is None else shapely.difference(p, corridor)
        pieces = [q for q in getattr(new, "geoms", [new]) if isinstance(q, LineString) and q.length > tol_deg]
        if not pieces:
            continue
        kept.extend(pieces)
        buf = p.buffer(tol_deg, quad_segs=2)
        corridor = buf if corridor is None else corridor.union(buf)
    return shapely.MultiLineString(kept) if kept else None


def palette_colour(ref: str) -> str:
    """Deterministic fallback colour for a line ref."""
    return PALETTE[int(hashlib.md5(ref.encode()).hexdigest(), 16) % len(PALETTE)]


def process_feed(
    spec: FeedSpec, bbox: tuple[float, float, float, float], osm_colours: dict[str, dict[str, str]]
) -> dict:
    """Extract lines and stations of one feed within a WGS84 bbox.

    Parameters
    ----------
    spec
        Feed specification.
    bbox
        WGS84 data extent ``(west, south, east, north)``.
    osm_colours
        Mode -> {ref -> colour} from OSM route relations.

    Returns
    -------
    dict
        ``lines`` and ``stops`` (lists of (props, geometry)) and ``info``.
    """
    w, s, e, n = bbox
    extent = shapely.box(w, s, e, n)
    zf = zipfile.ZipFile(spec.path)

    feed_info = next(_rows(zf, "feed_info.txt"), {})
    agencies = {r.get("agency_id", ""): r["agency_name"] for r in _rows(zf, "agency.txt")}
    routes = {}
    for r in _rows(zf, "routes.txt"):
        mode = mode_of(r["route_type"])
        ref = (r.get("route_short_name") or r.get("route_long_name") or "").strip()
        agency = agencies.get(r.get("agency_id", ""), next(iter(agencies.values()), ""))
        # On-demand taxi services have no fixed route. Regional trains are left to
        # the OSM rail layer: the SWU feed has none, and showing them only for
        # Karlsruhe would skew the comparison.
        if mode in (None, "rail") or not ref or is_special_line(ref) or "taxi" in agency.lower():
            continue
        routes[r["route_id"]] = {
            "ref": ref,
            "name": r.get("route_long_name", ""),
            "mode": mode,
            "color": _colour(r.get("route_color")),
            "text_color": _colour(r.get("route_text_color")),
            "agency": agency,
        }

    periods = service_periods(_rows(zf, "calendar.txt"), _rows(zf, "calendar_dates.txt"))
    all_dates = Counter(p for ps in periods.values() for p in ps)

    trips: dict[str, tuple[str, str, frozenset[str]]] = {}
    for trip_id, route_id, service_id, shape_id in _columns(
        zf, "trips.txt", ("trip_id", "route_id", "service_id", "shape_id")
    ):
        if route_id not in routes:
            continue
        nets = frozenset({spec.network}) if spec.network else frozenset(periods.get(service_id, ()))
        if nets:
            trips[trip_id] = (route_id, shape_id, nets)

    stops: dict[str, dict] = {}
    for r in _rows(zf, "stops.txt"):
        if r.get("location_type", "") not in ("", "0"):
            continue
        lon, lat = float(r["stop_lon"]), float(r["stop_lat"])
        if w <= lon <= e and s <= lat <= n:
            stops[r["stop_id"]] = {"name": r["stop_name"], "lon": lon, "lat": lat, "parent": r.get("parent_station", "")}

    served: dict[str, set[tuple[str, str]]] = defaultdict(set)  # stop -> {(route, network)}
    shape_trips: Counter[tuple[str, str, str]] = Counter()  # (route, shape, network) -> trips
    for trip_id, stop_id in _columns(zf, "stop_times.txt", ("trip_id", "stop_id")):
        if stop_id in stops and trip_id in trips:
            route_id, shape_id, nets = trips[trip_id]
            for net in nets:
                served[stop_id].add((route_id, net))
                shape_trips[(route_id, shape_id, net)] += 0  # mark as relevant
    # Count trips per shape for relevant shapes (used to drop one-off variants).
    relevant_shapes = {sh for (_, sh, _) in shape_trips}
    for route_id, shape_id, nets in trips.values():
        if shape_id in relevant_shapes:
            for net in nets:
                if (route_id, shape_id, net) in shape_trips:
                    shape_trips[(route_id, shape_id, net)] += 1

    coords: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for shape_id, lat, lon, seq in _columns(zf, "shapes.txt", ("shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence")):
        if shape_id in relevant_shapes:
            coords[shape_id].append((int(seq), float(lon), float(lat)))
    shapes = {}
    for shape_id, pts in coords.items():
        pts.sort()
        if len(pts) >= 2:
            shapes[shape_id] = LineString([(x, y) for _, x, y in pts])

    # Group shapes per line; a "line" is mode + ref + network, merging route_ids
    # that the feed splits by operator or variant.
    groups: dict[tuple[str, str, str], dict] = {}
    for (route_id, shape_id, net), count in shape_trips.items():
        geom = shapes.get(shape_id)
        if geom is None:
            continue
        r = routes[route_id]
        key = (r["mode"], r["ref"], net)
        g = groups.setdefault(key, {"route": r, "geoms": [], "trips": 0})
        g["geoms"].append(geom)
        g["trips"] += count

    lines = []
    for (mode, ref, net), g in sorted(groups.items()):
        r = g["route"]
        colour = r["color"]
        source = "gtfs"
        if colour is None:
            for osm_mode in {"tram": ("tram", "light_rail", "train"), "rail": ("train", "light_rail"), "bus": ("bus",)}[mode]:
                c = osm_colours.get(osm_mode, {}).get(ref)
                if c and _named_colour(c):
                    colour, source = _named_colour(c), "osm"
                    break
        if colour is None:
            colour, source = palette_colour(ref), "palette"
        clipped = _distinct_variants(g["geoms"], extent)
        if clipped is None:
            continue
        props = {
            "ref": ref,
            "name": r["name"],
            "mode": mode,
            "network": net,
            "color": colour,
            "text_color": r["text_color"] or "#ffffff",
            "color_source": source,
            "trips": g["trips"],
        }
        lines.append((props, clipped))

    # Stations: group platforms by parent station, else by name.
    stations: dict[str, dict] = {}
    for stop_id, rs in served.items():
        st = stops[stop_id]
        key = st["parent"] or st["name"]
        acc = stations.setdefault(key, {"name": st["name"], "pts": [], "routes": set()})
        acc["pts"].append((st["lon"], st["lat"]))
        acc["routes"].update(rs)
    stop_feats = []
    for acc in stations.values():
        xs, ys = zip(*acc["pts"], strict=True)
        modes = sorted({routes[rid]["mode"] for rid, _ in acc["routes"]})
        nets = sorted({net for _, net in acc["routes"]})
        refs = sorted({routes[rid]["ref"] for rid, _ in acc["routes"]}, key=lambda x: (len(x), x))
        props = {"name": acc["name"], "modes": ",".join(modes), "networks": ",".join(nets), "routes": " ".join(refs)}
        stop_feats.append((props, Point(sum(xs) / len(xs), sum(ys) / len(ys))))

    info = {
        "feed_id": spec.feed_id,
        "publisher": feed_info.get("feed_publisher_name", ""),
        "version": feed_info.get("feed_version", ""),
        "start": feed_info.get("feed_start_date", ""),
        "end": feed_info.get("feed_end_date", ""),
        "service_periods": dict(all_dates),
        "networks": sorted({net for (_, _, net) in groups}),
        "lines": len(lines),
        "stations": len(stop_feats),
        "colour_sources": dict(Counter(p["color_source"] for p, _ in lines)),
    }
    return {"lines": lines, "stops": stop_feats, "info": info}


def feeds_for(project: Project) -> list[FeedSpec]:
    """GTFS feeds per city; the SWU 2027 slot is used only when configured."""
    cache = data_dir() / "cache" / "gtfs"
    specs = [
        FeedSpec("kvv", "ka", cache / "kvv.zip", "current"),
        # network=None: split SWU trips into 2026/2027 by their service dates, so a
        # combined feed published before the switch-over yields both networks.
        FeedSpec("swu", "ulm", cache / "swu.zip", None),
    ]
    if project.sources["gtfs_swu"].get("url_2027"):
        specs.append(FeedSpec("swu2027", "ulm", cache / "swu2027.zip", "2027"))
    return specs


def run(project: Project) -> dict:
    """Process all feeds and write ``transit_lines``/``transit_stops`` layers.

    Returns
    -------
    dict
        Feed id -> info dict (also written to ``data/gtfs_info.json``).
    """
    infos = {}
    per_city: dict[str, dict[str, list]] = defaultdict(lambda: {"lines": [], "stops": []})
    for spec in feeds_for(project):
        city = project.city(spec.city_id)
        colours_path = layer_dir(city.id) / "route_colours.json"
        osm_colours = json.loads(colours_path.read_text()) if colours_path.exists() else {}
        res = process_feed(spec, city_frame(project, city).data_bbox_wgs84, osm_colours)
        per_city[city.id]["lines"] += res["lines"]
        per_city[city.id]["stops"] += res["stops"]
        infos[spec.feed_id] = res["info"]
        log.info("GTFS %s: %s", spec.feed_id, res["info"])

    for city_id, data in per_city.items():
        d = layer_dir(city_id)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "transit_lines.geojsonl", "w", encoding="utf-8") as fh:
            for props, geom in data["lines"]:
                fh.write(feature_line(props, geom))
        with open(d / "transit_stops.geojsonl", "w", encoding="utf-8") as fh:
            for props, geom in data["stops"]:
                fh.write(feature_line(props, geom))

    ulm_nets = sorted({n for fid in infos if fid.startswith("swu") for n in infos[fid]["networks"]})
    summary = {
        "feeds": infos,
        "ulm_networks": ulm_nets,
        "network_cutoff": NETWORK_CUTOFF.isoformat(),
        "generated": dt.date.today().isoformat(),
    }
    (data_dir() / "gtfs_info.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    return summary
