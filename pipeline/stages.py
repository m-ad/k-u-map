"""Pipeline stages wired to the CLI. Each ``run_<stage>`` reads and writes ``data/``."""

from __future__ import annotations

import logging

from . import download
from .config import Project
from .download import Manifest

log = logging.getLogger(__name__)


def run_download(project: Project, manifest: Manifest, *, osm_source: str) -> None:
    """Fetch every external input (OSM, GTFS, district boundaries, DEM)."""
    download.download_gtfs(project, manifest)
    download.download_districts(project, manifest)
    manifest.save()
    download.download_osm(project, manifest, mode=osm_source)
    manifest.save()
    from . import dem

    if dem.cached_products_valid(project):
        # Processed DEM restored from cache: the 2.6 GB of raw tiles are not needed.
        log.info("DEM products for key %s present; skipping DGM tile downloads", dem.dem_key(project))
    else:
        download.download_dem(project, manifest)


def run_osm(project: Project, manifest: Manifest, *, osm_source: str) -> None:
    """Extract OSM layers (buildings, roads, cycle infrastructure, ...) per city."""
    from . import osm

    osm.run(project)


def run_gtfs(project: Project, manifest: Manifest, *, osm_source: str) -> None:
    """Build transit line and station layers from the GTFS feeds."""
    from . import gtfs

    gtfs.run(project)


def run_dem(project: Project, manifest: Manifest, *, osm_source: str) -> None:
    """Mosaic the DGM tiles and derive contours."""
    from . import dem

    downloaded = {}
    for sid in ("dem_bw", "dem_by", "dem_copernicus"):
        dates = [e.downloaded for e in manifest.for_source(sid)]
        downloaded[sid] = max(dates) if dates else None
    dem.run(project, downloaded)


def run_districts(project: Project, manifest: Manifest, *, osm_source: str) -> None:
    """District boundaries, labels, reference points and distance rings."""
    from . import annotations

    annotations.run(project)


def run_tiles(project: Project, manifest: Manifest, *, osm_source: str) -> None:
    """Package all layers as PMTiles archives."""
    from . import tiles

    log.info("tile sizes (MB): %s", tiles.run(project))


def run_metrics(project: Project, manifest: Manifest, *, osm_source: str) -> None:
    """Compute the comparison metrics (``data/metrics.json``)."""
    from . import metrics

    metrics.run(project)


def run_site(project: Project, manifest: Manifest, *, osm_source: str) -> None:
    """Assemble the static site in ``dist/``."""
    from . import site

    site.run(project, manifest)
