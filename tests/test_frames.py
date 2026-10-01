"""Tests for the metric frames (UTM 32N) and distance rings."""

import pytest
from pyproj import Geod

from pipeline import frames
from pipeline.config import load_project

PROJECT = load_project()
GEOD = Geod(ellps="GRS80")


@pytest.mark.parametrize("city", PROJECT.cities, ids=lambda c: c.id)
def test_both_cities_lie_in_utm_zone_32(city) -> None:
    # Zone 32 spans 6°E–12°E; a shared metric CRS is what makes "same scale" exact.
    assert frames.utm_zone(city.lon) == 32


@pytest.mark.parametrize("city", PROJECT.cities, ids=lambda c: c.id)
def test_frame_has_configured_metric_size(city) -> None:
    f = frames.city_frame(PROJECT, city)
    minx, miny, maxx, maxy = f.frame_utm
    assert maxx - minx == pytest.approx(12_000.0)
    assert maxy - miny == pytest.approx(10_000.0)
    dminx, dminy, dmaxx, dmaxy = f.data_utm
    assert dmaxx - dminx == pytest.approx(20_000.0)
    assert dmaxy - dminy == pytest.approx(18_000.0)


@pytest.mark.parametrize("city", PROJECT.cities, ids=lambda c: c.id)
def test_frame_contains_all_reference_points(city) -> None:
    f = frames.city_frame(PROJECT, city)
    for p in city.points:
        assert f.frame_contains(p.lon, p.lat), f"{p.name} outside the {city.id} frame"


@pytest.mark.parametrize("city", PROJECT.cities, ids=lambda c: c.id)
def test_wgs84_bbox_encloses_utm_rectangle(city) -> None:
    f = frames.city_frame(PROJECT, city)
    w, s, e, n = f.data_bbox_wgs84
    for x in (f.data_utm[0], f.data_utm[2]):
        for y in (f.data_utm[1], f.data_utm[3]):
            lon, lat = frames.TO_WGS84.transform(x, y)
            assert w <= lon <= e and s <= lat <= n


@pytest.mark.parametrize("city", PROJECT.cities, ids=lambda c: c.id)
@pytest.mark.parametrize("radius_m", PROJECT.ring_radii_m)
def test_rings_are_geodesic_circles(city, radius_m: float) -> None:
    c = city.ring_center
    ring = frames.geodesic_ring(c.lon, c.lat, radius_m)
    for lon, lat in ring:
        _, _, dist = GEOD.inv(c.lon, c.lat, lon, lat)
        assert dist == pytest.approx(radius_m, abs=0.01)
    assert ring[0] == ring[-1]


def test_ring_label_minutes() -> None:
    assert frames.bike_minutes(2000.0, 15.0) == 8
    assert frames.bike_minutes(4000.0, 15.0) == 16
    assert frames.bike_minutes(6000.0, 15.0) == 24
