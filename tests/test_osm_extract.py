"""End-to-end test of the OSM layer extraction on a tiny synthetic extract."""

from __future__ import annotations

from pathlib import Path

from pipeline.annotations import read_geojsonl
from pipeline.osm import extract_city

OSM_XML = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" version="1" lat="48.3990" lon="9.9900"/>
  <node id="2" version="1" lat="48.3990" lon="9.9950"/>
  <node id="3" version="1" lat="48.3995" lon="9.9900"/>
  <node id="4" version="1" lat="48.3995" lon="9.9950"/>
  <node id="5" version="1" lat="48.4000" lon="9.9900"/>
  <node id="6" version="1" lat="48.4000" lon="9.9950"/>
  <!-- Tram track tagged on the street way itself, as common in Ulm. -->
  <way id="10" version="1">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="primary"/>
    <tag k="railway" v="tram"/>
    <tag k="name" v="Neue Straße"/>
  </way>
  <way id="11" version="1">
    <nd ref="3"/><nd ref="4"/>
    <tag k="highway" v="cycleway"/>
  </way>
  <way id="12" version="1">
    <nd ref="5"/><nd ref="6"/>
    <tag k="railway" v="tram"/>
    <tag k="service" v="yard"/>
  </way>
</osm>
"""


def test_tram_on_street_way_is_kept_in_both_layers(tmp_path: Path) -> None:
    src = tmp_path / "mini.osm"
    src.write_text(OSM_XML)
    out = tmp_path / "layers"
    counts = extract_city(src, out, (9.98, 48.39, 10.0, 48.41))

    roads = list(read_geojsonl(out / "roads.geojsonl"))
    rail = list(read_geojsonl(out / "rail.geojsonl"))
    cycle = list(read_geojsonl(out / "cycle.geojsonl"))
    assert [p["class"] for p, _ in roads] == ["primary", "path"]
    assert sorted(p["class"] for p, _ in rail) == ["tram", "tram_service"]
    assert [p["kind"] for p, _ in cycle] == ["track"]
    assert counts["rail"] == 2
