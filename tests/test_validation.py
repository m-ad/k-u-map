"""Validation of the pipeline outputs (run after ``python -m pipeline build``).

These guard against silent data problems: empty layers, mis-placed labels,
GTFS coordinates outside the cities, or broken vector tiles.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import zipfile
from collections import Counter

import mapbox_vector_tile
import pytest
import rasterio
import rasterio.features
import shapely
from pmtiles.reader import MmapSource, Reader, all_tiles
from shapely.geometry import Point

from pipeline.annotations import read_geojsonl
from pipeline.annotations import to_utm as to_utm_geom
from pipeline.config import data_dir, load_project
from pipeline.frames import city_frame
from pipeline.osm import layer_dir

pytestmark = pytest.mark.data
PROJECT = load_project()
CITIES = [c.id for c in PROJECT.cities]

# Minimum feature counts per city; well below the observed values but far above
# what a broken extract or filter would produce.
MIN_FEATURES = {
    "buildings": 20_000,
    "roads": 20_000,
    "landuse": 2_000,
    "water": 50,
    "waterway": 200,
    "rail": 200,
    "cycle": 500,
    "border": 1,
    "admin": 10,
    "places": 20,
    "contours": 200,
    "transit_lines": 10,
    "transit_stops": 100,
    "districts": 15,
    "labels": 30,
    "refpoints": 3,
    "rings": 6,
}


@pytest.mark.parametrize("city", CITIES)
@pytest.mark.parametrize("layer", sorted(MIN_FEATURES))
def test_layer_is_not_empty(city: str, layer: str) -> None:
    n = sum(1 for _ in read_geojsonl(layer_dir(city) / f"{layer}.geojsonl"))
    assert n >= MIN_FEATURES[layer], f"{city}/{layer}: only {n} features"


@pytest.mark.parametrize("city", CITIES)
def test_cycle_layer_has_all_major_categories(city: str) -> None:
    kinds = Counter(p["kind"] for p, _ in read_geojsonl(layer_dir(city) / "cycle.geojsonl"))
    for kind in ("track", "path", "lane", "street"):
        assert kinds[kind] > 0, f"{city}: no cycle infrastructure of kind {kind}"


@pytest.mark.parametrize("city", CITIES)
def test_named_rivers_present(city: str) -> None:
    expected = {"ka": {"Rhein", "Alb"}, "ulm": {"Donau", "Iller", "Blau"}}[city]
    names = {p.get("name") for p, _ in read_geojsonl(layer_dir(city) / "waterway.geojsonl")}
    assert expected <= names


# Minimum of the per-tile maximum feature count at z14. Line layers are set high:
# a regression once left exactly one line feature per tile.
ARCHIVE_LAYERS = {
    "base": {"landuse": 20, "water": 3, "waterway": 10, "roads": 100, "rail": 10, "districts": 2, "municipalities": 1},
    "buildings": {"buildings": 100},
    "cycle": {"cycle": 10},
    "transit": {"transit_lines": 3, "transit_stops": 5},
    "contours": {"contours": 10},
}


@pytest.mark.parametrize("archive", sorted(ARCHIVE_LAYERS))
def test_vector_tiles_carry_all_layers(archive: str) -> None:
    per_layer_max: Counter[str] = Counter()
    with open(data_dir() / "tiles" / f"{archive}.pmtiles", "rb") as fh:
        reader = Reader(MmapSource(fh))
        for (z, _, _), data in all_tiles(reader.get_bytes):
            if z != 14:
                continue
            tile = mapbox_vector_tile.decode(gzip.decompress(data))
            for name, layer in tile.items():
                per_layer_max[name] = max(per_layer_max[name], len(layer["features"]))
    for layer, minimum in ARCHIVE_LAYERS[archive].items():
        assert per_layer_max[layer] >= minimum, f"{archive}/{layer}: max {per_layer_max[layer]} features per z14 tile"


def test_terrain_tiles_cover_both_frames() -> None:
    from pipeline.dem import lonlat_to_tile

    with open(data_dir() / "tiles" / "terrain.pmtiles", "rb") as fh:
        reader = Reader(MmapSource(fh))
        for city in PROJECT.cities:
            x, y = lonlat_to_tile(city.lon, city.lat, 13)
            assert reader.get(13, x, y) is not None, f"no terrain tile at the {city.id} centre"


def _districts(city: str) -> dict[str, shapely.Geometry]:
    return {p["name"]: g for p, g in read_geojsonl(layer_dir(city) / "districts.geojsonl")}


def _municipalities(city: str) -> dict[str, shapely.Geometry]:
    return {p["name"]: g for p, g in read_geojsonl(layer_dir(city) / "municipalities.geojsonl")}


@pytest.mark.parametrize("city", CITIES)
def test_labels_fall_inside_their_boundary_polygons(city: str) -> None:
    districts = _districts(city)
    munis = _municipalities(city)
    checked = 0
    for p, pt in read_geojsonl(layer_dir(city) / "labels.geojsonl"):
        if p["kind"] in ("district", "quarter", "neighbourhood"):
            assert districts[p["district"]].contains(pt), f"{p['name']} outside district {p['district']}"
            assert munis[p["municipality"]].buffer(1e-4).contains(pt), f"{p['name']} outside {p['municipality']}"
            checked += 1
    assert checked >= 15


def test_ambiguous_ulm_names_are_assigned_to_the_right_town() -> None:
    munis = _municipalities("ulm")
    labels = {p["name"]: (p, pt) for p, pt in read_geojsonl(layer_dir("ulm") / "labels.geojsonl")}
    # OSM's "Stadtmitte" is Neu-Ulm's district, south of the Danube.
    assert munis["Neu-Ulm"].contains(labels["Stadtmitte (Neu-Ulm)"][1])
    assert munis["Neu-Ulm"].contains(labels["Weststadt (Neu-Ulm)"][1])
    for name in ("Mitte", "Weststadt", "Söflingen", "Kuhberg", "Fischerviertel", "Michelsberg", "Oststadt"):
        assert munis["Ulm"].contains(labels[name][1]), name


@pytest.mark.parametrize("city", PROJECT.cities, ids=lambda c: c.id)
def test_configured_districts_exist_as_labels(city) -> None:
    names = {p["name"] for p, _ in read_geojsonl(layer_dir(city.id) / "labels.geojsonl")}
    missing = (set(city.primary_districts) | set(city.secondary_districts)) - names
    assert not missing, f"config/project.toml names without label: {missing}"


@pytest.mark.parametrize("city", PROJECT.cities, ids=lambda c: c.id)
def test_gtfs_geometry_lies_within_the_frame(city) -> None:
    frame = city_frame(PROJECT, city)
    extent = shapely.box(*frame.data_bbox_wgs84).buffer(1e-6)
    view = shapely.box(*frame.frame_bbox_wgs84)
    lines = list(read_geojsonl(layer_dir(city.id) / "transit_lines.geojsonl"))
    assert all(extent.contains(g) for _, g in lines)
    # Most of the network must be inside the default view, not just touching it.
    inside = sum(g.intersection(view).length for _, g in lines)
    assert inside / sum(g.length for _, g in lines) > 0.5
    stops = [g for _, g in read_geojsonl(layer_dir(city.id) / "transit_stops.geojsonl")]
    assert sum(view.contains(s) for s in stops) >= 100


def test_swu_feed_shapes_are_in_ulm() -> None:
    # Validates the raw feed (coordinate order, right city) before any clipping.
    w, s, e, n = city_frame(PROJECT, PROJECT.city("ulm")).data_bbox_wgs84
    with zipfile.ZipFile(data_dir() / "cache" / "gtfs" / "swu.zip") as zf:
        rows = list(csv.DictReader(io.TextIOWrapper(zf.open("shapes.txt"), encoding="utf-8-sig")))
    inside = sum(w <= float(r["shape_pt_lon"]) <= e and s <= float(r["shape_pt_lat"]) <= n for r in rows)
    assert inside / len(rows) > 0.9


def test_gtfs_info_reports_networks() -> None:
    info = json.loads((data_dir() / "gtfs_info.json").read_text())
    assert "2026" in info["ulm_networks"] or "2027" in info["ulm_networks"]
    assert info["feeds"]["kvv"]["lines"] > 20


@pytest.mark.parametrize("city", PROJECT.cities, ids=lambda c: c.id)
def test_official_dem_covers_the_frame(city) -> None:
    frame = city_frame(PROJECT, city)
    with rasterio.open(data_dir() / "dem" / f"{city.id}_source.tif") as src:
        source = src.read(1)
        mask = rasterio.features.geometry_mask(
            [shapely.box(*frame.frame_utm)], out_shape=source.shape, transform=src.transform, invert=True
        )
    official = ((source == 1) | (source == 2))[mask].mean()
    assert official > 0.95, f"{city.id}: only {official:.1%} of the frame has official DGM"


def test_metrics_are_plausible() -> None:
    m = json.loads((data_dir() / "metrics.json").read_text())["cities"]
    for city in CITIES:
        r = m[city]["radii"]
        assert set(r) == {"2", "4", "6"}
        for v in r.values():
            assert 2 < v["building_share_pct"] < 60
            assert v["tram_km"] > 0
            assert v["cycle_km_total"] > 0
        # Shares fall and lengths grow with the radius.
        assert r["2"]["building_share_pct"] > r["6"]["building_share_pct"]
        assert r["2"]["tram_km"] <= r["4"]["tram_km"] <= r["6"]["tram_km"]
    # The topographic contrast the map is meant to show.
    assert (
        m["ulm"]["radii"]["4"]["elevation_m"]["range_p5_p95"] > 3 * m["ka"]["radii"]["4"]["elevation_m"]["range_p5_p95"]
    )


def test_reference_points_are_inside_osm_features() -> None:
    # Coordinates in config/project.toml must sit on the features they name.
    ulm = PROJECT.city("ulm")
    hbf = next(p for p in ulm.points if p.id == "hbf")
    stations = [
        (p, g) for p, g in read_geojsonl(layer_dir("ulm") / "stations.geojsonl") if p["name"] == "Ulm Hauptbahnhof"
    ]
    assert stations and Point(hbf.lon, hbf.lat).distance(stations[0][1]) < 0.002


@pytest.mark.parametrize("city", PROJECT.cities, ids=lambda c: c.id)
def test_tram_stations_lie_on_osm_tracks(city) -> None:
    # Cross-checks two independent sources: GTFS tram/Stadtbahn stations must sit
    # next to OSM track. This caught tram track tagged on street ways being dropped.
    tracks = [
        to_utm_geom(g)
        for p, g in read_geojsonl(layer_dir(city.id) / "rail.geojsonl")
        if p["class"] in ("tram", "rail", "tram_service")
    ]
    tree = shapely.STRtree(tracks)
    view = shapely.box(*city_frame(PROJECT, city).frame_bbox_wgs84)
    stations = [
        to_utm_geom(g)
        for p, g in read_geojsonl(layer_dir(city.id) / "transit_stops.geojsonl")
        if "tram" in p["modes"] and view.contains(g)
    ]
    near = sum(1 for s in stations if tree.query(s, predicate="dwithin", distance=60.0).size > 0)
    assert len(stations) >= 10
    assert near / len(stations) > 0.95, f"{city.id}: only {near}/{len(stations)} tram stations near OSM track"
