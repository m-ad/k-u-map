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
    download.download_dem(project, manifest)


def run_osm(project: Project, manifest: Manifest, *, osm_source: str) -> None:
    """Extract OSM layers (buildings, roads, cycle infrastructure, ...) per city."""
    from . import osm

    osm.run(project)
