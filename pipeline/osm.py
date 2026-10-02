"""Extract map layers from the clipped OSM extracts with pyosmium.

Writes one newline-delimited GeoJSON file (WGS84) per layer and city to
``data/layers/<city>/<layer>.geojsonl``. Tag rules live in :mod:`osm_tags`.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import IO, Any

import osmium
import shapely
from shapely.geometry.base import BaseGeometry

from .config import Project, data_dir
from .frames import Bounds, city_frame
from .osm_tags import (
    WATERWAY_LINES,
    classify_landuse,
    classify_rail,
    classify_road,
    cycle_sides,
    is_water_area,
)

log = logging.getLogger(__name__)

PLACE_KINDS = {"city", "town", "village", "borough", "suburb", "quarter", "neighbourhood", "hamlet"}
ADMIN_LEVELS = {"4", "6", "8", "9", "10", "11"}
OSM_LAYERS = (
    "admin",
    "border",
    "buildings",
    "childcare",
    "cycle",
    "landuse",
    "places",
    "rail",
    "roads",
    "stations",
    "water",
    "waterway",
)
# Facilities the Ü3 kindergarten rules (kita_rules.py) classify, and the tags they read.
CHILDCARE_AMENITIES = {"kindergarten", "childcare"}
CHILDCARE_TAGS = ("name", "amenity", "min_age", "max_age", "nursery", "isced:level")
AREA_KEYS = ("building", "landuse", "natural", "leisure", "amenity", "waterway", "boundary", "place")


def layer_dir(city_id: str) -> Path:
    """Directory holding the GeoJSON layers of one city."""
    return data_dir() / "layers" / city_id


def feature_line(props: dict[str, Any], geom: BaseGeometry) -> str:
    """Serialise one GeoJSON feature as a single line."""
    # shapely.to_geojson is much faster than building dicts via mapping().
    props_json = json.dumps(props, ensure_ascii=False)
    return f'{{"type":"Feature","properties":{props_json},"geometry":{shapely.to_geojson(geom)}}}\n'


class LayerWriter:
    """Lazily opened per-layer GeoJSONL files with a bbox filter."""

    def __init__(self, out_dir: Path, bbox: Bounds) -> None:
        self.out_dir = out_dir
        self.bbox = shapely.box(*bbox)
        self.files: dict[str, IO[str]] = {}
        self.counts: Counter[str] = Counter()
        out_dir.mkdir(parents=True, exist_ok=True)
        # Only remove this stage's own outputs; other stages share the directory.
        for name in OSM_LAYERS:
            (out_dir / f"{name}.geojsonl").unlink(missing_ok=True)

    def write(self, layer: str, props: dict[str, Any], geom: BaseGeometry) -> None:
        """Append a feature if it touches the data extent."""
        if geom.is_empty or not self.bbox.intersects(geom):
            return
        fh = self.files.get(layer)
        if fh is None:
            fh = self.files[layer] = open(self.out_dir / f"{layer}.geojsonl", "w", encoding="utf-8")
        fh.write(feature_line(props, geom))
        self.counts[layer] += 1

    def close(self) -> None:
        """Close all files."""
        for fh in self.files.values():
            fh.close()


def _relation_pass(pbf: Path) -> tuple[set[int], dict[str, dict[str, str]]]:
    """Collect state-border way ids and public-transport line colours.

    Returns
    -------
    border_ways
        Ids of ways that are members of a state (admin_level=4) relation. Every
        such way is a state (or national) border; one membership suffices, so the
        result does not depend on the neighbouring state's relation being in a
        regional extract.
    route_colours
        Mode -> {line ref -> colour} from ``type=route`` relations; the KVV GTFS
        feed carries no colours, the OSM relations carry the official ones.
    """
    members: set[int] = set()
    colours: dict[str, dict[str, str]] = {}
    for rel in osmium.FileProcessor(str(pbf), osmium.osm.RELATION):
        tags = rel.tags
        if tags.get("boundary") == "administrative" and tags.get("admin_level") == "4":
            members.update(m.ref for m in rel.members if m.type == "w")
        elif tags.get("type") == "route" and tags.get("ref") and tags.get("colour"):
            mode = tags.get("route", "")
            colours.setdefault(mode, {}).setdefault(tags["ref"], tags["colour"])
    return members, colours


def _childcare_props(tags: osmium.osm.TagList, osm_id: str) -> dict[str, Any]:
    return {**{k: tags[k] for k in CHILDCARE_TAGS if k in tags}, "osm_id": osm_id}


def _flags(tags: osmium.osm.TagList) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if tags.get("bridge", "no") not in ("no",):
        out["bridge"] = True
    if tags.get("tunnel", "no") not in ("no",) or tags.get("location") == "underground":
        out["tunnel"] = True
    layer = tags.get("layer")
    if layer and layer.lstrip("-").isdigit():
        out["layer"] = int(layer)
    return out


def extract_city(pbf: Path, out_dir: Path, bbox: Bounds) -> Counter[str]:
    """Stream one city's PBF and write all OSM layers.

    Parameters
    ----------
    pbf
        Clipped OSM extract.
    out_dir
        Output directory for GeoJSONL layers.
    bbox
        WGS84 data extent; features outside are dropped.

    Returns
    -------
    Counter
        Feature count per layer.
    """
    border_ways, route_colours = _relation_pass(pbf)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "route_colours.json").write_text(json.dumps(route_colours, ensure_ascii=False, indent=1))
    wkb = osmium.geom.WKBFactory()
    w = LayerWriter(out_dir, bbox)

    def geom_of(factory_fn: Any, obj: Any) -> BaseGeometry | None:
        try:
            return shapely.from_wkb(factory_fn(obj))
        except (RuntimeError, osmium.InvalidLocationError):
            return None

    area_filter = osmium.filter.KeyFilter(*AREA_KEYS)
    fp = osmium.FileProcessor(str(pbf)).with_locations().with_areas(area_filter)
    for o in fp:
        tags = o.tags
        if o.is_node():
            place = tags.get("place")
            name = tags.get("name")
            if place in PLACE_KINDS and name:
                g = geom_of(wkb.create_point, o)
                if g is not None:
                    w.write("places", {"name": name, "place": place, "osm_id": f"n{o.id}"}, g)
            elif tags.get("railway") in ("station", "halt") and name:
                g = geom_of(wkb.create_point, o)
                if g is not None:
                    w.write("stations", {"name": name, "kind": tags.get("railway")}, g)
            if tags.get("amenity") in CHILDCARE_AMENITIES:
                g = geom_of(wkb.create_point, o)
                if g is not None:
                    w.write("childcare", _childcare_props(tags, f"n{o.id}"), g)
        elif o.is_way():
            if o.id in border_ways:
                g = geom_of(wkb.create_linestring, o)
                if g is not None:
                    w.write("border", {}, g)
            if "highway" in tags:
                t = dict(tags)
                cls = classify_road(t)
                if cls is not None:
                    g = geom_of(wkb.create_linestring, o)
                    if g is not None:
                        props = {"class": cls, **_flags(tags)}
                        if tags.get("name"):
                            props["name"] = tags["name"]
                        w.write("roads", props, g)
                        for kind, sides in cycle_sides(t).items():
                            w.write("cycle", {"kind": kind, "sides": sides}, g)
            # Not elif: tram tracks are often tagged on the street way itself
            # (highway=* + railway=tram), and must land in both layers.
            if "railway" in tags:
                cls = classify_rail(tags)
                if cls is not None:
                    g = geom_of(wkb.create_linestring, o)
                    if g is not None:
                        w.write("rail", {"class": cls, **_flags(tags)}, g)
            if tags.get("waterway") in WATERWAY_LINES:
                g = geom_of(wkb.create_linestring, o)
                if g is not None:
                    props = {"class": tags["waterway"]}
                    if tags.get("name"):
                        props["name"] = tags["name"]
                    if tags.get("tunnel") in ("culvert", "yes"):
                        props["tunnel"] = True
                    w.write("waterway", props, g)
        elif o.is_area():
            t = dict(tags)
            g = None
            if "building" in t and t["building"] != "no":
                g = geom_of(wkb.create_multipolygon, o)
                if g is not None:
                    w.write("buildings", {}, g)
            if is_water_area(t):
                g = g or geom_of(wkb.create_multipolygon, o)
                if g is not None:
                    props = {"name": t["name"]} if "name" in t else {}
                    w.write("water", props, g)
            cls = classify_landuse(t)
            if cls is not None:
                g = g or geom_of(wkb.create_multipolygon, o)
                if g is not None:
                    w.write("landuse", {"class": cls}, g)
            if t.get("boundary") == "administrative" and t.get("admin_level") in ADMIN_LEVELS and t.get("name"):
                g = g or geom_of(wkb.create_multipolygon, o)
                if g is not None:
                    w.write(
                        "admin",
                        {"name": t["name"], "admin_level": int(t["admin_level"]), "osm_id": f"a{o.id}"},
                        g,
                    )
            if t.get("place") in PLACE_KINDS and t.get("name"):
                g = g or geom_of(wkb.create_multipolygon, o)
                if g is not None:
                    w.write(
                        "places",
                        {"name": t["name"], "place": t["place"], "osm_id": f"a{o.id}"},
                        g.representative_point(),
                    )
            if t.get("amenity") in CHILDCARE_AMENITIES:
                g = g or geom_of(wkb.create_multipolygon, o)
                if g is not None:
                    w.write("childcare", _childcare_props(tags, f"a{o.id}"), g.representative_point())
    w.close()
    return w.counts


def run(project: Project) -> dict[str, Counter[str]]:
    """Extract layers for all cities.

    Parameters
    ----------
    project
        Project configuration.

    Returns
    -------
    dict
        City id -> per-layer feature counts.
    """
    result = {}
    for city in project.cities:
        pbf = data_dir() / "osm" / f"{city.id}.osm.pbf"
        counts = extract_city(pbf, layer_dir(city.id), city_frame(project, city).data_bbox_wgs84)
        log.info("OSM %s: %s", city.id, dict(sorted(counts.items())))
        result[city.id] = counts
    return result
