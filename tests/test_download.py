"""Tests for DEM tile indexing used by the download stage."""

from pipeline.download import bw_tile_origins, by_tiles_from_metalink

METALINK = """<?xml version="1.0" encoding="UTF-8"?>
<metalink xmlns="urn:ietf:params:xml:ns:metalink">
  <file name="582_5364.tif">
    <size>2827638</size>
    <hash type="sha-256">284e8b70</hash>
    <url>https://download1.bayernwolke.de/a/dgm/dgm1/582_5364.tif</url>
    <url>https://download2.bayernwolke.de/a/dgm/dgm1/582_5364.tif</url>
  </file>
</metalink>"""


def test_bw_tile_containing_ulmer_muenster() -> None:
    # Münster at UTM (573463, 5361069) lies in the LGL tile dgm1_32_573_5360_2_bw.
    assert bw_tile_origins((573463, 5361069, 573464, 5361070)) == [(573, 5360)]


def test_bw_tiles_cover_rectangle_without_gaps() -> None:
    bounds = (560_100.0, 5_352_300.0, 580_100.0, 5_370_300.0)
    tiles = bw_tile_origins(bounds)
    assert all(e % 2 == 1 and n % 2 == 0 for e, n in tiles)
    # Every 100 m sample point of the rectangle falls in exactly one tile.
    for x in range(560_100, 580_100, 700):
        for y in range(5_352_300, 5_370_300, 700):
            hits = [t for t in tiles if t[0] * 1000 <= x < t[0] * 1000 + 2000 and t[1] * 1000 <= y < t[1] * 1000 + 2000]
            assert len(hits) == 1


def test_metalink_parsing() -> None:
    assert by_tiles_from_metalink(METALINK) == [
        ("582_5364.tif", "https://download1.bayernwolke.de/a/dgm/dgm1/582_5364.tif", "284e8b70")
    ]
