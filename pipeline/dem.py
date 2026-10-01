"""Elevation: mosaic official DGM1 tiles to a 5 m grid, then hillshade tiles and contours.

Inputs are LGL Baden-Württemberg DGM1 (xyz text, 1 m) and Bavarian DGM1
(GeoTIFF, 1 m), both ETRS89/UTM 32N with DHHN2016 heights. Cells without
official data (e.g. Rheinland-Pfalz west of the Rhine) are filled from
Copernicus GLO-30, a surface model, which is only used outside both states.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import logging
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path

import contourpy
import numpy as np
import rasterio
import shapely
from PIL import Image
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from scipy import ndimage

from .config import Project, data_dir
from .frames import TO_WGS84, city_frame
from .osm import feature_line, layer_dir

log = logging.getLogger(__name__)

RES_M = 5.0
CONTOUR_STEP_M = 10.0
CONTOUR_INDEX_M = 50.0
UTM_CRS = "EPSG:25832"


@dataclass
class Grid:
    """A north-up UTM grid accumulating mean elevations."""

    x0: float
    y1: float  # top edge
    width: int
    height: int
    res: float = RES_M

    @property
    def transform(self) -> rasterio.Affine:
        """Affine transform of the grid."""
        return from_origin(self.x0, self.y1, self.res, self.res)


def grid_for(bounds: tuple[float, float, float, float], res: float = RES_M) -> Grid:
    """Grid covering ``bounds`` with edges snapped to multiples of ``res``.

    Snapping makes 5x5 blocks of the 1 m source pixels fall exactly into one cell.
    """
    minx, miny, maxx, maxy = bounds
    x0 = math.floor(minx / res) * res
    y1 = math.ceil(maxy / res) * res
    x1 = math.ceil(maxx / res) * res
    y0 = math.floor(miny / res) * res
    return Grid(x0, y1, int(round((x1 - x0) / res)), int(round((y1 - y0) / res)), res)


def accumulate(grid: Grid, sums: np.ndarray, counts: np.ndarray, x: np.ndarray, y: np.ndarray, z: np.ndarray) -> None:
    """Add point elevations to per-cell sums and counts (block mean)."""
    ix = np.floor((x - grid.x0) / grid.res).astype(np.int64)
    iy = np.floor((grid.y1 - y) / grid.res).astype(np.int64)
    ok = (ix >= 0) & (ix < grid.width) & (iy >= 0) & (iy < grid.height) & np.isfinite(z)
    flat = iy[ok] * grid.width + ix[ok]
    n = grid.width * grid.height
    sums += np.bincount(flat, weights=z[ok], minlength=n)
    counts += np.bincount(flat, minlength=n)


def read_bw_zip(path: Path) -> list[np.ndarray]:
    """Parse the 1 km xyz files inside an LGL 2 km zip into (N, 3) arrays."""
    out = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.endswith(".xyz"):
                # np.fromstring with a separator parses whitespace-delimited text in C;
                # ~10x faster than np.loadtxt for these 29 MB files.
                arr = np.fromstring(zf.read(name).decode("ascii"), sep=" ")
                out.append(arr.reshape(-1, 3))
    return out


def read_by_tif(path: Path) -> np.ndarray:
    """Read a Bavarian 1 km GeoTIFF into an (N, 3) array of pixel centres."""
    with rasterio.open(path) as r:
        z = r.read(1).astype(np.float64)
        if r.nodata is not None:
            z[z == r.nodata] = np.nan
        t = r.transform
        cols = t.c + (np.arange(r.width) + 0.5) * t.a
        rows = t.f + (np.arange(r.height) + 0.5) * t.e
        xs, ys = np.meshgrid(cols, rows)
    return np.column_stack([xs.ravel(), ys.ravel(), z.ravel()])


def mosaic(project: Project, city_id: str) -> tuple[np.ndarray, np.ndarray, Grid]:
    """Build the 5 m elevation grid of a city.

    Returns
    -------
    elevation
        2-D float32 array (NaN only if no source covers a cell).
    source
        uint8 array: 1 = LGL BW, 2 = Bavaria, 3 = Copernicus fill, 0 = none.
    grid
        Grid geometry.
    """
    frame = city_frame(project, project.city(city_id))
    grid = grid_for(frame.data_utm)
    n = grid.width * grid.height
    cache = data_dir() / "cache" / "dem"
    source = np.zeros(n, np.uint8)
    elev = np.full(n, np.nan)

    for kind, code in (("bw", 1), ("by", 2)):
        sums = np.zeros(n)
        counts = np.zeros(n)
        files = sorted((cache / kind).glob("*.zip" if kind == "bw" else "*.tif"))
        for f in files:
            e, nn = (int(v) for v in (f.stem.split("_")[2:4] if kind == "bw" else f.stem.split("_")[:2]))
            size = 2000 if kind == "bw" else 1000
            minx, miny, maxx, maxy = frame.data_utm
            if e * 1000 >= maxx or (e * 1000 + size) <= minx or nn * 1000 >= maxy or (nn * 1000 + size) <= miny:
                continue
            arrays = read_bw_zip(f) if kind == "bw" else [read_by_tif(f)]
            for a in arrays:
                accumulate(grid, sums, counts, a[:, 0], a[:, 1], a[:, 2])
        has = (counts > 0) & np.isnan(elev)
        elev[has] = sums[has] / counts[has]
        source[has] = code
        log.info("DEM %s: %s covers %.1f %% of the grid", city_id, kind, 100 * has.sum() / n)

    elev2 = elev.reshape(grid.height, grid.width)
    src2 = source.reshape(grid.height, grid.width)
    missing = np.isnan(elev2)
    if missing.any():
        fill = np.full_like(elev2, np.nan)
        for f in sorted((cache / "cop").glob("*.tif")):
            tmp = np.full_like(elev2, np.nan)
            with rasterio.open(f) as r:
                reproject(
                    rasterio.band(r, 1),
                    tmp,
                    dst_transform=grid.transform,
                    dst_crs=UTM_CRS,
                    dst_nodata=np.nan,
                    resampling=Resampling.bilinear,
                )
            fill = np.where(np.isnan(fill), tmp, fill)
        use = missing & np.isfinite(fill)
        elev2[use] = fill[use]
        src2[use] = 3
        log.info("DEM %s: Copernicus fills %.1f %%", city_id, 100 * use.sum() / n)
    return elev2.astype(np.float32), src2, grid


def write_geotiff(path: Path, arr: np.ndarray, grid: Grid, nodata: float | None = None) -> None:
    """Write a single-band GeoTIFF in UTM 32N."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=grid.width,
        height=grid.height,
        count=1,
        dtype=arr.dtype,
        crs=UTM_CRS,
        transform=grid.transform,
        compress="deflate",
        predictor=3 if arr.dtype.kind == "f" else 2,
        nodata=nodata,
    ) as dst:
        dst.write(arr, 1)


# ------------------------------------------------------------------ contours


def contours(elev: np.ndarray, grid: Grid, step: float = CONTOUR_STEP_M) -> list[tuple[dict, shapely.LineString]]:
    """Contour lines in WGS84 with ``ele`` and ``index`` (every 50 m) attributes.

    A light Gaussian smoothing (sigma 10 m) removes 1 m-scale micro-relief that
    otherwise makes 10 m contours on the flat Rhine plain jagged.
    """
    z = elev.astype(np.float64)
    valid = np.isfinite(z)
    filled = np.where(valid, z, np.nanmean(z))
    smooth = ndimage.gaussian_filter(filled, sigma=2.0)
    smooth[~valid] = np.nan
    gen = contourpy.contour_generator(z=smooth, name="serial", line_type=contourpy.LineType.Separate)
    lo = math.ceil(np.nanmin(smooth) / step) * step
    hi = math.floor(np.nanmax(smooth) / step) * step
    out: list[tuple[dict, shapely.LineString]] = []
    for level in np.arange(lo, hi + step / 2, step):
        for seg in gen.lines(level):
            if len(seg) < 4:
                continue
            # contourpy works in array index space (col, row) -> UTM -> WGS84.
            xs = grid.x0 + (seg[:, 0] + 0.5) * grid.res
            ys = grid.y1 - (seg[:, 1] + 0.5) * grid.res
            utm = shapely.LineString(np.column_stack([xs, ys])).simplify(2.0)
            if utm.length < 50:
                continue
            lon, lat = TO_WGS84.transform(*np.asarray(utm.coords).T)
            props = {"ele": int(level), "index": bool(level % CONTOUR_INDEX_M == 0)}
            out.append((props, shapely.LineString(np.column_stack([lon, lat]))))
    return out


# ------------------------------------------------------------ terrarium tiles

MERC_HALF = 20037508.342789244


def tile_bounds_merc(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Web-Mercator bounds of an XYZ tile."""
    size = 2 * MERC_HALF / 2**z
    minx = -MERC_HALF + x * size
    maxy = MERC_HALF - y * size
    return (minx, maxy - size, minx + size, maxy)


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    """XYZ tile containing a WGS84 point."""
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def terrarium_encode(h: np.ndarray) -> np.ndarray:
    """Encode heights in Mapzen Terrarium RGB (h = R*256 + G + B/256 - 32768)."""
    v = np.clip(h + 32768.0, 0, 65535.99)
    r = np.floor(v / 256.0)
    g = np.floor(v - r * 256.0)
    b = np.floor((v - np.floor(v)) * 256.0)
    return np.stack([r, g, b], axis=-1).astype(np.uint8)


def terrain_tiles(
    elev: np.ndarray,
    grid: Grid,
    bbox_wgs84: tuple[float, float, float, float],
    zooms: range,
    tile_px: int = 512,
    quantum: float = 0.1,
) -> dict[tuple[int, int, int], bytes]:
    """Render Terrarium tiles (lossless WebP) covering ``bbox_wgs84``.

    Cells outside the DEM are filled with the nearest valid height so the
    client-side hillshade fades to flat instead of showing a cliff at the edge.
    """
    z = elev.astype(np.float32)
    invalid = ~np.isfinite(z)
    if invalid.any():
        idx = ndimage.distance_transform_edt(invalid, return_distances=False, return_indices=True)
        z = z[tuple(idx)]
    tiles: dict[tuple[int, int, int], bytes] = {}
    w, s, e, n = bbox_wgs84
    for zoom in zooms:
        x0, y0 = lonlat_to_tile(w, n, zoom)
        x1, y1 = lonlat_to_tile(e, s, zoom)
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                minx, miny, maxx, maxy = tile_bounds_merc(zoom, tx, ty)
                dst = np.full((tile_px, tile_px), np.nan, np.float32)
                reproject(
                    z,
                    dst,
                    src_transform=grid.transform,
                    src_crs=UTM_CRS,
                    src_nodata=np.nan,
                    dst_transform=from_origin(minx, maxy, (maxx - minx) / tile_px, (maxy - miny) / tile_px),
                    dst_crs="EPSG:3857",
                    dst_nodata=np.nan,
                    resampling=Resampling.bilinear,
                )
                if np.isnan(dst).all():
                    continue
                if np.isnan(dst).any():
                    idx = ndimage.distance_transform_edt(np.isnan(dst), return_distances=False, return_indices=True)
                    dst = dst[tuple(idx)]
                # 0.1 m steps are below the DGM's +-0.15 m accuracy but make the
                # fractional channel compressible: ~3x smaller tiles than raw PNG.
                dst = np.round(dst / quantum) * quantum
                buf = io.BytesIO()
                Image.fromarray(terrarium_encode(dst), "RGB").save(buf, "WEBP", lossless=True, quality=100, method=6)
                tiles[(zoom, tx, ty)] = buf.getvalue()
    return tiles


# Bump when the mosaic logic changes so cached DEM products are rebuilt.
DEM_VERSION = 1


def dem_key(project: Project) -> str:
    """Identity of the DEM products: frames, resolution, code version and year.

    The year makes CI refresh the cached DEM annually, matching the LGL's
    yearly laser-scan updates, without downloading 2.6 GB of tiles every month.
    """
    payload = {
        "v": DEM_VERSION,
        "res": RES_M,
        "year": dt.date.today().year,
        "frames": {c.id: [round(v, 1) for v in city_frame(project, c).data_utm] for c in project.cities},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def cached_products_valid(project: Project) -> bool:
    """Whether ``data/dem`` already holds products for the current :func:`dem_key`."""
    d = data_dir() / "dem"
    key = d / "key.txt"
    files = [d / f"{c.id}_{kind}.tif" for c in project.cities for kind in ("5m", "source")]
    return key.exists() and key.read_text().strip() == dem_key(project) and all(f.exists() for f in files)


def read_products(city_id: str) -> tuple[np.ndarray, np.ndarray, Grid]:
    """Load a previously written elevation and source grid."""
    d = data_dir() / "dem"
    with rasterio.open(d / f"{city_id}_5m.tif") as r:
        elev = r.read(1)
        t = r.transform
        grid = Grid(t.c, t.f, r.width, r.height, t.a)
    with rasterio.open(d / f"{city_id}_source.tif") as r:
        src = r.read(1)
    return elev, src, grid


def run(project: Project, downloaded: dict[str, str | None] | None = None) -> dict:
    """Build DEM products for all cities.

    Writes ``data/dem/<city>_5m.tif``, ``data/dem/<city>_source.tif`` and
    ``data/layers/<city>/contours.geojsonl``; returns coverage statistics. If
    products for the current :func:`dem_key` exist (e.g. restored from the CI
    cache), the mosaic step is skipped and only contours are regenerated.

    Parameters
    ----------
    project
        Project configuration.
    downloaded
        DEM source id -> download timestamp, kept with the products so the site
        footer can still date them when the raw tiles are not re-downloaded.
    """
    out = data_dir() / "dem"
    reuse = cached_products_valid(project)
    previous = json.loads((out / "coverage.json").read_text()) if (out / "coverage.json").exists() else {}
    stats: dict = {}
    for city in project.cities:
        if reuse:
            elev, src, grid = read_products(city.id)
            log.info("DEM %s: reusing cached products (key %s)", city.id, dem_key(project))
        else:
            elev, src, grid = mosaic(project, city.id)
            write_geotiff(out / f"{city.id}_5m.tif", elev, grid, nodata=float("nan"))
            write_geotiff(out / f"{city.id}_source.tif", src, grid, nodata=0)
        layer_dir(city.id).mkdir(parents=True, exist_ok=True)
        feats = contours(elev, grid)
        with open(layer_dir(city.id) / "contours.geojsonl", "w", encoding="utf-8") as fh:
            for props, g in feats:
                fh.write(feature_line(props, g))
        total = src.size
        stats[city.id] = {
            "lgl_bw": round(float((src == 1).sum()) / total, 4),
            "ldbv_by": round(float((src == 2).sum()) / total, 4),
            "copernicus": round(float((src == 3).sum()) / total, 4),
            "min": round(float(np.nanmin(elev)), 1),
            "max": round(float(np.nanmax(elev)), 1),
            "contours": len(feats),
        }
        log.info("DEM %s: %s", city.id, stats[city.id])
    dates = {k: v for k, v in (downloaded or {}).items() if v} or previous.get("downloaded", {})
    stats["downloaded"] = dates
    (out / "coverage.json").write_text(json.dumps(stats, indent=1))
    (out / "key.txt").write_text(dem_key(project))
    return stats
