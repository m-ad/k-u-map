"""Assemble the static site in ``dist/``.

Copies the hand-written client from ``site/``, vendors MapLibre, PMTiles and the
Noto Sans fonts from ``node_modules`` (so nothing loads from a CDN at runtime),
and writes the data the client reads: PMTiles archives, small GeoJSON overlays,
``metrics.json`` and ``meta.json`` (frames, networks, sources and dates).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import subprocess
from pathlib import Path

from .config import ROOT, Project, data_dir, dist_dir
from .download import Manifest
from .frames import city_frame
from .osm import layer_dir

log = logging.getLogger(__name__)

NODE = ROOT / "node_modules"
VENDOR = {
    "vendor/maplibre/maplibre-gl.mjs": "maplibre-gl/dist/maplibre-gl.mjs",
    "vendor/maplibre/maplibre-gl-shared.mjs": "maplibre-gl/dist/maplibre-gl-shared.mjs",
    "vendor/maplibre/maplibre-gl-worker.mjs": "maplibre-gl/dist/maplibre-gl-worker.mjs",
    "vendor/maplibre/maplibre-gl.css": "maplibre-gl/dist/maplibre-gl.css",
    "vendor/maplibre/LICENSE.txt": "maplibre-gl/LICENSE.txt",
    "vendor/pmtiles/pmtiles.js": "pmtiles/dist/pmtiles.js",
    "fonts/noto-sans-latin-400-normal.woff2": "@fontsource/noto-sans/files/noto-sans-latin-400-normal.woff2",
    "fonts/noto-sans-latin-600-normal.woff2": "@fontsource/noto-sans/files/noto-sans-latin-600-normal.woff2",
    "fonts/noto-sans-latin-700-normal.woff2": "@fontsource/noto-sans/files/noto-sans-latin-700-normal.woff2",
    "fonts/noto-sans-latin-400-italic.woff2": "@fontsource/noto-sans/files/noto-sans-latin-400-italic.woff2",
    "fonts/LICENSE": "@fontsource/noto-sans/LICENSE",
}
OVERLAYS = ("labels", "refpoints", "rings", "kitas")
SOURCE_ORDER = (
    "osm",
    "gtfs_swu",
    "gtfs_kvv",
    "districts_ka",
    "kitas_ka",
    "dem_bw",
    "dem_by",
    "dem_copernicus",
    "fonts",
    "software",
)


def ensure_node_modules() -> None:
    """Install the pinned JS dependencies if they are missing."""
    if not (NODE / "maplibre-gl").exists():
        subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=ROOT, check=True)


def geojsonl_to_fc(src: Path, dst: Path) -> int:
    """Convert newline-delimited features to a FeatureCollection file."""
    feats = [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines() if line.strip()]
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":"))
    )
    return len(feats)


def _fmt_yyyymmdd(v: str) -> str:
    return f"{v[6:8]}.{v[4:6]}.{v[0:4]}" if len(v) == 8 else v


def build_meta(project: Project, manifest: Manifest) -> dict:
    """Collect everything the client needs besides the tiles."""
    gtfs = json.loads((data_dir() / "gtfs_info.json").read_text())
    stamps_path = data_dir() / "osm" / "timestamps.json"
    stamps = json.loads(stamps_path.read_text()) if stamps_path.exists() else {}
    coverage = json.loads((data_dir() / "dem" / "coverage.json").read_text())
    kitas = json.loads((data_dir() / "kitas_info.json").read_text())
    ka_list = kitas["cities"].get("ka", {})

    def downloaded(source: str) -> str | None:
        dates = [e.downloaded for e in manifest.for_source(source)]
        # DEM tiles may come from the CI cache without a fresh manifest entry.
        return max(dates) if dates else coverage.get("downloaded", {}).get(source)

    osm_date = min(filter(None, stamps.values()), default=None)
    data_dates = {
        "osm": osm_date[:10] if osm_date else None,
        "gtfs_swu": "Version {v}, gültig {a}–{b}".format(
            v=gtfs["feeds"]["swu"]["version"],
            a=_fmt_yyyymmdd(gtfs["feeds"]["swu"]["start"]),
            b=_fmt_yyyymmdd(gtfs["feeds"]["swu"]["end"]),
        ),
        "gtfs_kvv": "Version {v}, gültig {a}–{b}".format(
            v=gtfs["feeds"]["kvv"]["version"],
            a=_fmt_yyyymmdd(gtfs["feeds"]["kvv"]["start"]),
            b=_fmt_yyyymmdd(gtfs["feeds"]["kvv"]["end"]),
        ),
        "kitas_ka": "{} Einträge im {:g}-km-Kreis, davon {} auch in OSM".format(
            ka_list.get("official_in_radius", 0), kitas["radius_km"], ka_list.get("official_matched_osm", 0)
        ),
        "dem_bw": "Anteil KA {:.0%}, Ulm {:.0%}".format(coverage["ka"]["lgl_bw"], coverage["ulm"]["lgl_bw"]),
        "dem_by": "Anteil Ulm/Neu-Ulm {:.0%}".format(coverage["ulm"]["ldbv_by"]),
        "dem_copernicus": "nur Lückenfüllung, Anteil KA {:.0%}, Ulm {:.0%}".format(
            coverage["ka"]["copernicus"], coverage["ulm"]["copernicus"]
        ),
    }
    sources = []
    for sid in SOURCE_ORDER:
        if sid == "kitas_ka" and not ka_list.get("official_list"):
            # Optional source, not reachable for this build: nothing to attribute.
            continue
        s = project.sources[sid]
        sources.append(
            {
                "id": sid,
                "name": s["name"],
                "license": s["license"],
                "license_url": s["license_url"],
                "attribution": s["attribution"],
                "homepage": s["homepage"],
                "downloaded": downloaded(sid),
                "data_date": data_dates.get(sid),
            }
        )
    cities = {}
    for c in project.cities:
        f = city_frame(project, c)
        cities[c.id] = {
            "name": c.name,
            "center": [c.lon, c.lat],
            "frame_bbox": [round(v, 6) for v in f.frame_bbox_wgs84],
            "data_bbox": [round(v, 6) for v in f.data_bbox_wgs84],
            "ring_center": [c.ring_center.lon, c.ring_center.lat],
        }
    networks = gtfs["ulm_networks"] or ["2026"]
    return {
        "generated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "frame": {"width_m": project.frame_width_m, "height_m": project.frame_height_m, "margin_m": project.margin_m},
        "cities": cities,
        "networks": {"ulm": networks, "ka": ["current"]},
        "gtfs": gtfs["feeds"],
        "dem": coverage,
        "sources": sources,
        "kitas": {"radius_km": kitas["radius_km"], "official_list_ka": bool(ka_list.get("official_list"))},
    }


def run(project: Project, manifest: Manifest) -> Path:
    """Write the complete site to ``dist/``."""
    ensure_node_modules()
    out = dist_dir()
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "site", out)
    for dst, src in VENDOR.items():
        target = out / dst
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(NODE / src, target)

    # The pmtiles npm package ships no LICENSE file; record its licence explicitly.
    pm_version = json.loads((NODE / "pmtiles" / "package.json").read_text())["version"]
    (out / "vendor" / "pmtiles" / "LICENSE.txt").write_text(
        f"pmtiles {pm_version} (https://github.com/protomaps/PMTiles), licensed under BSD-3-Clause.\n"
    )

    data_out = out / "data"
    data_out.mkdir(parents=True, exist_ok=True)
    for pm in sorted((data_dir() / "tiles").glob("*.pmtiles")):
        shutil.copy2(pm, data_out / pm.name)
    for city in project.cities:
        for name in OVERLAYS:
            geojsonl_to_fc(layer_dir(city.id) / f"{name}.geojsonl", data_out / city.id / f"{name}.geojson")
    shutil.copy2(data_dir() / "metrics.json", data_out / "metrics.json")
    (data_out / "meta.json").write_text(json.dumps(build_meta(project, manifest), indent=1, ensure_ascii=False))
    # GitHub Pages would otherwise run Jekyll and skip files starting with "_".
    (out / ".nojekyll").write_text("")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    log.info("site written to %s (%.1f MB)", out, size / 1e6)
    return out
