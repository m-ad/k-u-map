"""Tests for the Web-Mercator scale math that keeps both panels at equal m/px."""

import math

import pytest

from pipeline import scale
from pipeline.config import load_project

KA_LAT = 49.006
ULM_LAT = 48.400


def test_mpp_at_equator_zoom0_matches_512px_world() -> None:
    # MapLibre renders the whole world into 512 px at zoom 0.
    assert scale.meters_per_pixel(0.0, 0.0) == pytest.approx(2 * math.pi * 6378137 / 512)


def test_mpp_halves_per_zoom_level() -> None:
    assert scale.meters_per_pixel(11.0, KA_LAT) == pytest.approx(scale.meters_per_pixel(10.0, KA_LAT) / 2)


def test_zoom_for_mpp_roundtrip() -> None:
    for z in (8.0, 10.7, 13.25, 16.0):
        for lat in (KA_LAT, ULM_LAT, 0.0, 60.0):
            assert scale.zoom_for_mpp(scale.meters_per_pixel(z, lat), lat) == pytest.approx(z, abs=1e-12)


def test_ulm_needs_slightly_higher_zoom_than_karlsruhe() -> None:
    # Ulm lies further south, so cos(phi) is larger and Mercator shows more metres
    # per pixel at equal zoom; it needs ~+0.017 zoom levels to match.
    dz = scale.synced_zoom(11.0, KA_LAT, ULM_LAT) - 11.0
    assert dz == pytest.approx(math.log2(math.cos(math.radians(ULM_LAT)) / math.cos(math.radians(KA_LAT))))
    assert 0.016 < dz < 0.019


@pytest.mark.parametrize("z_src", [9.0, 10.73, 12.0, 15.5])
@pytest.mark.parametrize(("lat_src", "lat_dst"), [(KA_LAT, ULM_LAT), (ULM_LAT, KA_LAT), (49.05, 48.33)])
def test_synced_zoom_gives_equal_mpp_within_0_1_percent(z_src: float, lat_src: float, lat_dst: float) -> None:
    z_dst = scale.synced_zoom(z_src, lat_src, lat_dst)
    a = scale.meters_per_pixel(z_src, lat_src)
    b = scale.meters_per_pixel(z_dst, lat_dst)
    assert abs(a / b - 1) < 1e-3
    # The math is exact; the 0.1 % budget is for the browser round trip.
    assert abs(a / b - 1) < 1e-12


def test_constant_zoom_would_violate_the_tolerance() -> None:
    # Guards against a regression to "same zoom in both panels": that is off by ~1.2 %.
    a = scale.meters_per_pixel(11.0, KA_LAT)
    b = scale.meters_per_pixel(11.0, ULM_LAT)
    assert abs(a / b - 1) > 1e-2


def test_fit_zoom_shows_whole_frame() -> None:
    project = load_project()
    for w_px, h_px in ((400, 360), (650, 700), (1300, 900)):
        z = scale.fit_zoom(project.frame_width_m, project.frame_height_m, w_px, h_px, KA_LAT)
        mpp = scale.meters_per_pixel(z, KA_LAT)
        assert mpp * w_px >= project.frame_width_m - 1e-6
        assert mpp * h_px >= project.frame_height_m - 1e-6
        # ... and is tight in at least one dimension.
        assert min(mpp * w_px / project.frame_width_m, mpp * h_px / project.frame_height_m) == pytest.approx(1.0)


def test_scale_bar_picks_round_lengths() -> None:
    assert scale.nice_scale_length(1234.0) == 1000.0
    assert scale.nice_scale_length(480.0) == 200.0
    assert scale.nice_scale_length(2600.0) == 2000.0
    assert scale.nice_scale_length(7.4) == 5.0
