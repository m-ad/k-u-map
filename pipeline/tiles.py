"""Package layers as PMTiles archives (vector via tippecanoe, terrain via Python).

Layers are split into several archives so the browser only fetches what is
switched on: MapLibre requests tiles only for sources with a visible layer.
Both cities go into the same archives; a ``city`` property tells them apart.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import rasterio
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

from .config import Project, data_dir
from .dem import Grid, terrain_tiles
from .frames import city_frame
from .osm import layer_dir

log = logging.getLogger(__name__)

# Per-feature minimum zooms keep low-zoom tiles light without dropping features
# at the zooms where they matter.
ROAD_MINZOOM = {
    "motorway": 8,
    "trunk": 8,
    "primary": 8,
    "secondary": 9,
    "tertiary": 10,
    "minor": 11,
    "pedestrian": 12,
    "track": 12,
    "service": 13,
    "path": 13,
}
WATERWAY_MINZOOM = {"river": 8, "canal": 8, "stream": 11, "ditch": 13, "drain": 13}
RAIL_MINZOOM = {"rail": 8, "tram": 10, "rail_service": 12, "tram_service": 13}


def minzoom_for(layer: str, props: dict) -> int | None:
    """Minimum zoom for a feature, or None for the archive default."""
    if layer == "roads":
        return ROAD_MINZOOM.get(props.get("class", ""), 12)
    if layer == "waterway":
        return WATERWAY_MINZOOM.get(props.get("class", ""), 12)
    if layer == "rail":
        return RAIL_MINZOOM.get(props.get("class", ""), 12)
    if layer == "contours":
        return 10 if props.get("index") else 12
    if layer == "transit_stops":
        return 10 if "tram" in props.get("modes", "") else 12
    return None


@dataclass(frozen=True)
class Archive:
    """A PMTiles archive made from one or more layers."""

    name: str
    layers: tuple[str, ...]
    minzoom: int
    maxzoom: int
    extra_args: tuple[str, ...] = ()


ARCHIVES = (
    Archive(
        "base",
        ("landuse", "water", "waterway", "roads", "rail", "border", "districts", "municipalities", "stations"),
        8,
        15,
        ("--detect-shared-borders", "--no-simplification-of-shared-nodes", "--coalesce-densest-as-needed"),
    ),
    # Buildings dominate the payload. At low zooms tippecanoe merges sub-pixel
    # footprints, which keeps the urban texture visible at the default frame zoom
    # (about z10.7 on a phone, z11.4 on a desktop half-screen).
    Archive("buildings", ("buildings",), 10, 15, ("--coalesce-smallest-as-needed", "--detect-shared-borders")),
    Archive("cycle", ("cycle",), 10, 15, ("--no-simplification-of-shared-nodes",)),
    Archive(
        "transit", ("transit_lines", "transit_stops"), 8, 15, ("-r1", "--no-feature-limit", "--no-tile-size-limit")
    ),
    Archive("contours", ("contours",), 10, 15, ("--coalesce-densest-as-needed",)),
)


def prepare_layer(project: Project, layer: str, out: Path) -> tuple[int, set[int]]:
    """Concatenate one layer of all cities, adding ``city`` and a ``_mz`` minzoom.

    The per-feature ``"tippecanoe": {"minzoom": n}`` member would be the natural
    choice, but tippecanoe 2.49 (Ubuntu 24.04) then keeps only one line feature
    per tile; ``_mz`` plus a ``$zoom`` feature filter avoids that bug.

    Returns
    -------
    count, minzooms
        Number of features and the distinct ``_mz`` values written.
    """
    n = 0
    zooms: set[int] = set()
    with open(out, "w", encoding="utf-8") as dst:
        for city in project.cities:
            src = layer_dir(city.id) / f"{layer}.geojsonl"
            if not src.exists():
                continue
            with open(src, encoding="utf-8") as fh:
                for line in fh:
                    f = json.loads(line)
                    f["properties"]["city"] = city.id
                    mz = minzoom_for(layer, f["properties"])
                    if mz is not None:
                        f["properties"]["_mz"] = mz
                        zooms.add(mz)
                    dst.write(json.dumps(f, ensure_ascii=False, separators=(",", ":")) + "\n")
                    n += 1
    return n, zooms


def zoom_filter(zooms: set[int]) -> list:
    """tippecanoe ``-j`` expression keeping a feature from zoom ``_mz`` upwards."""
    return ["any", ["!has", "_mz"], *(["all", ["==", "_mz", z], [">=", "$zoom", z]] for z in sorted(zooms))]


def build_vector(project: Project, archive: Archive, out_dir: Path) -> Path:
    """Run tippecanoe for one archive."""
    out = out_dir / f"{archive.name}.pmtiles"
    out.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(dir=data_dir()) as tmp:
        args = [
            "tippecanoe",
            "-o",
            str(out),
            "-Z",
            str(archive.minzoom),
            "-z",
            str(archive.maxzoom),
            "-P",
            "-q",
            "--force",
            "-n",
            archive.name,
            *archive.extra_args,
        ]
        filters = {}
        for layer in archive.layers:
            path = Path(tmp) / f"{layer}.geojsonl"
            count, zooms = prepare_layer(project, layer, path)
            if count == 0:
                raise ValueError(f"layer {layer!r} is empty for every city")
            if zooms:
                filters[layer] = zoom_filter(zooms)
            args += ["-L", f"{layer}:{path}"]
        if filters:
            args += ["-j", json.dumps(filters)]
        subprocess.run(args, check=True)
    log.info("tiles %s: %.1f MB", out.name, out.stat().st_size / 1e6)
    return out


def build_terrain(project: Project, out_dir: Path, zooms: range = range(8, 14)) -> Path:
    """Encode the 5 m DEMs as Terrarium WebP tiles in one PMTiles archive."""
    tiles: dict[tuple[int, int, int], bytes] = {}
    west, south, east, north = 180.0, 90.0, -180.0, -90.0
    for city in project.cities:
        with rasterio.open(data_dir() / "dem" / f"{city.id}_5m.tif") as r:
            elev = r.read(1)
            t = r.transform
            grid = Grid(t.c, t.f, r.width, r.height, t.a)
        bbox = city_frame(project, city).data_bbox_wgs84
        tiles.update(terrain_tiles(elev, grid, bbox, zooms))
        west, south = min(west, bbox[0]), min(south, bbox[1])
        east, north = max(east, bbox[2]), max(north, bbox[3])

    out = out_dir / "terrain.pmtiles"
    with open(out, "wb") as fh:
        writer = Writer(fh)
        for (z, x, y), data in sorted(tiles.items(), key=lambda kv: zxy_to_tileid(*kv[0])):
            writer.write_tile(zxy_to_tileid(z, x, y), data)
        c = project.cities[0]
        writer.finalize(
            {
                "tile_type": TileType.WEBP,
                "tile_compression": Compression.NONE,
                "min_lon_e7": int(west * 1e7),
                "min_lat_e7": int(south * 1e7),
                "max_lon_e7": int(east * 1e7),
                "max_lat_e7": int(north * 1e7),
                "center_zoom": 11,
                "center_lon_e7": int(c.lon * 1e7),
                "center_lat_e7": int(c.lat * 1e7),
            },
            {"name": "terrain", "encoding": "terrarium", "tile_size": 512},
        )
    log.info("tiles terrain.pmtiles: %d tiles, %.1f MB", len(tiles), out.stat().st_size / 1e6)
    return out


def run(project: Project) -> dict[str, float]:
    """Build all archives into ``data/tiles``; returns sizes in MB."""
    out_dir = data_dir() / "tiles"
    out_dir.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for archive in ARCHIVES:
        p = build_vector(project, archive, out_dir)
        sizes[p.name] = round(p.stat().st_size / 1e6, 2)
    p = build_terrain(project, out_dir)
    sizes[p.name] = round(p.stat().st_size / 1e6, 2)
    (out_dir / "sizes.json").write_text(json.dumps(sizes, indent=1))
    return sizes
