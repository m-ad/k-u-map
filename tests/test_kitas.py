"""End-to-end test of the Kita stage on synthetic OSM and city data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.annotations import read_geojsonl
from pipeline.config import load_project
from pipeline.download import kitas_ka_path
from pipeline.kitas import build_city, osm_url
from pipeline.osm import layer_dir

PROJECT = load_project()
KA = PROJECT.city("ka")
LON, LAT = KA.ring_center.lon, KA.ring_center.lat
# Rough degrees per metre at 49° N; precise enough for synthetic positions.
DLAT, DLON = 1 / 111_200, 1 / 72_950


def _point(props: dict, east_m: float, north_m: float) -> dict:
    coords = [LON + east_m * DLON, LAT + north_m * DLAT]
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": coords}}


def _write_lines(path: Path, feats: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in feats), encoding="utf-8")


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KUMAP_DATA_DIR", str(tmp_path))
    _write_lines(
        layer_dir("ka") / "childcare.geojsonl",
        [
            _point({"name": "Villa Bambini", "amenity": "childcare", "osm_id": "n1"}, 0, 100),
            _point({"name": "Kita Sonne", "amenity": "kindergarten", "osm_id": "a20"}, 500, 0),
            _point({"name": "Kindergarten Fern", "amenity": "kindergarten", "osm_id": "n3"}, 0, 3000),
        ],
    )
    return tmp_path


def _write_official() -> None:
    lists = {
        "Kindergärten": [_point({"name": "Kinderhaus Villa Bambini"}, 0, 120)],
        "Kindertagesstätten": [_point({"name": "Kinderkrippe Mini"}, 0, -800), _point({"name": "Kita Neu"}, -1000, 0)],
    }
    for category, feats in lists.items():
        path = kitas_ka_path(category)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False))


def _kept() -> dict[str, dict]:
    return {p["name"]: p for p, _ in read_geojsonl(layer_dir("ka") / "kitas.geojsonl")}


def test_osm_only_build_keeps_radius_and_drops_excluded(data: Path) -> None:
    info = build_city(PROJECT, KA)
    assert info["official_list"] is False
    kept = _kept()
    assert set(kept) == {"Kita Sonne"}  # Villa Bambini excluded, Kindergarten Fern outside 2 km
    assert kept["Kita Sonne"]["status"] == "probable"
    assert kept["Kita Sonne"]["osm"] == "https://www.openstreetmap.org/way/10"
    excluded = [p["name"] for p, _ in read_geojsonl(layer_dir("ka") / "kitas_excluded.geojsonl")]
    assert excluded == ["Villa Bambini"]


def test_official_list_confirms_and_adds_facilities(data: Path) -> None:
    _write_official()
    info = build_city(PROJECT, KA)
    assert info["official_list"] is True
    assert info["official_in_radius"] == 3
    kept = _kept()
    assert set(kept) == {"Kinderhaus Villa Bambini", "Kita Sonne", "Kita Neu"}
    bambini = kept["Kinderhaus Villa Bambini"]
    assert (bambini["status"], bambini["source"]) == ("confirmed", "OSM + Stadt Karlsruhe")
    assert kept["Kita Neu"]["source"] == "Stadt Karlsruhe"
    assert kept["Kita Neu"]["status"] == "probable"
    assert (info["confirmed"], info["probable"], info["excluded"]) == (1, 2, 1)


def test_osm_url_decodes_area_ids() -> None:
    assert osm_url("n5") == "https://www.openstreetmap.org/node/5"
    assert osm_url("a20") == "https://www.openstreetmap.org/way/10"
    assert osm_url("a21") == "https://www.openstreetmap.org/relation/10"


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": 500, "message": "Unable to complete operation."}},  # ArcGIS error with HTTP 200
        {
            "type": "FeatureCollection",
            "features": [
                _point({"name": "Kita Utm"}, 0, 0)
                | {"geometry": {"type": "Point", "coordinates": [456000.0, 5428000.0]}}
            ],
        },
    ],
    ids=["error-body", "wrong-crs"],
)
def test_unusable_official_list_falls_back_to_osm(data: Path, body: dict) -> None:
    _write_official()
    kitas_ka_path("Kindertagesstätten").write_text(json.dumps(body))
    info = build_city(PROJECT, KA)
    assert info["official_list"] is False
    assert set(_kept()) == {"Kita Sonne"}
