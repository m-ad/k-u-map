"""Tests for DEM gridding and Terrarium encoding."""

import numpy as np
import pytest

from pipeline.dem import accumulate, grid_for, lonlat_to_tile, terrarium_encode, tile_bounds_merc


def decode(rgb: np.ndarray) -> np.ndarray:
    r, g, b = (rgb[..., i].astype(np.float64) for i in range(3))
    return r * 256 + g + b / 256 - 32768


def test_terrarium_roundtrip_within_4mm() -> None:
    h = np.array([[-12.3, 0.0, 115.27], [478.94, 615.5, 1234.567]])
    assert np.allclose(decode(terrarium_encode(h)), h, atol=1 / 256)


def test_grid_snaps_to_resolution() -> None:
    g = grid_for((563_501.3, 5_352_002.7, 583_499.9, 5_370_001.1))
    assert g.x0 % 5 == 0 and g.y1 % 5 == 0
    assert g.x0 <= 563_501.3 and g.x0 + g.width * 5 >= 583_499.9
    assert g.y1 >= 5_370_001.1 and g.y1 - g.height * 5 <= 5_352_002.7


def test_accumulate_block_means_1m_pixels_into_5m_cells() -> None:
    g = grid_for((0, 0, 10, 10))
    sums = np.zeros(g.width * g.height)
    counts = np.zeros_like(sums)
    xs, ys = np.meshgrid(np.arange(10) + 0.5, np.arange(10) + 0.5)
    zs = np.where(xs < 5, 100.0, 200.0)
    accumulate(g, sums, counts, xs.ravel(), ys.ravel(), zs.ravel())
    mean = (sums / counts).reshape(g.height, g.width)
    assert counts.tolist() == [25, 25, 25, 25]
    assert mean.tolist() == [[100, 200], [100, 200]]


def test_tile_index_for_ulm() -> None:
    x, y = lonlat_to_tile(9.99245, 48.39852, 13)
    minx, miny, maxx, maxy = tile_bounds_merc(13, x, y)
    # The tile must contain the Münster in Web Mercator coordinates.
    import math

    mx = 9.99245 * 20037508.342789244 / 180
    my = math.log(math.tan(math.pi / 4 + math.radians(48.39852) / 2)) * 6378137
    assert minx <= mx <= maxx and miny <= my <= maxy
    assert maxx - minx == pytest.approx(2 * 20037508.342789244 / 2**13)
