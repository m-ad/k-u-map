"""Download external data with caching and a provenance manifest.

Every file fetched is recorded in ``data/manifest.json`` with URL, local path,
size, SHA-256 and download date, so the site footer can state when each source
was retrieved. Re-runs reuse cached files unless they are older than
``max_age_days`` (sources that change, like GTFS) or a checksum disagrees.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree

import requests

from .config import Project, data_dir
from .frames import Bounds, city_frame

log = logging.getLogger(__name__)

USER_AGENT = "k-u-map/0.1 (+https://github.com/m-ad/k-u-map)"
CHUNK = 1 << 20


@dataclass
class ManifestEntry:
    """Provenance of one downloaded file."""

    source: str
    url: str
    path: str
    bytes: int
    sha256: str
    downloaded: str
    last_modified: str | None = None


class Manifest:
    """JSON file mapping local paths to their provenance."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, ManifestEntry] = {}
        if path.exists():
            for k, v in json.loads(path.read_text()).items():
                self.entries[k] = ManifestEntry(**v)

    def add(self, entry: ManifestEntry) -> None:
        """Insert or replace the entry for ``entry.path``."""
        self.entries[entry.path] = entry

    def get(self, path: Path) -> ManifestEntry | None:
        """Return the entry for a local file, if any."""
        return self.entries.get(str(path))

    def for_source(self, source: str) -> list[ManifestEntry]:
        """All entries belonging to one source id."""
        return [e for e in self.entries.values() if e.source == source]

    def save(self) -> None:
        """Write the manifest atomically."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: asdict(v) for k, v in sorted(self.entries.items())}, indent=1))
        tmp.replace(self.path)


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def md5_file(path: Path) -> str:
    """Hex MD5 of a file (Geofabrik publishes MD5 sums)."""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


class NotFound(Exception):
    """Raised when the server answers 404 (e.g. a DEM tile outside the state)."""


def fetch(
    url: str,
    dest: Path,
    manifest: Manifest,
    source: str,
    *,
    max_age_days: float | None = None,
    sha256: str | None = None,
    retries: int = 4,
    session: requests.Session | None = None,
) -> Path:
    """Download ``url`` to ``dest`` unless a valid cached copy exists.

    Parameters
    ----------
    url
        Remote URL.
    dest
        Local target path.
    manifest
        Manifest receiving the provenance entry.
    source
        Source id from ``sources.toml``.
    max_age_days
        Re-download if the cached copy is older than this. ``None`` keeps it forever,
        which suits immutable inputs such as DEM tiles verified by checksum.
    sha256
        Expected checksum; a mismatch triggers a re-download and then an error.
    retries
        Attempts on network errors, with exponential backoff.
    session
        Optional shared HTTP session.

    Returns
    -------
    Path
        ``dest``.

    Raises
    ------
    NotFound
        If the server answers 404.
    """
    entry = manifest.get(dest)
    if dest.exists() and entry is not None:
        age = dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(entry.downloaded)
        fresh = max_age_days is None or age.total_seconds() < max_age_days * 86400
        if fresh and (sha256 is None or entry.sha256 == sha256):
            return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    http = session or requests.Session()
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            with http.get(url, stream=True, timeout=(30, 300), headers={"User-Agent": USER_AGENT}) as r:
                if r.status_code == 404:
                    raise NotFound(url)
                r.raise_for_status()
                h = hashlib.sha256()
                n = 0
                # Write to a temp file first so an interrupted download never
                # leaves a truncated file that looks cached.
                with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
                    for block in r.iter_content(CHUNK):
                        tmp.write(block)
                        h.update(block)
                        n += len(block)
                digest = h.hexdigest()
                if sha256 is not None and digest != sha256:
                    Path(tmp.name).unlink(missing_ok=True)
                    raise ValueError(f"checksum mismatch for {url}")
                Path(tmp.name).replace(dest)
                manifest.add(
                    ManifestEntry(
                        source=source,
                        url=url,
                        path=str(dest),
                        bytes=n,
                        sha256=digest,
                        downloaded=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                        last_modified=r.headers.get("Last-Modified"),
                    )
                )
                log.info("downloaded %s (%.1f MB)", url, n / 1e6)
                return dest
        except NotFound:
            raise
        except (requests.RequestException, ValueError) as exc:
            if attempt == retries:
                raise
            log.warning("retrying %s after %s", url, exc)
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


# --------------------------------------------------------------------------- OSM


def osm_header_timestamp(pbf: Path) -> str | None:
    """Replication timestamp stored in an OSM PBF header (data "as of" date)."""
    out = subprocess.run(
        ["osmium", "fileinfo", "-g", "header.option.osmosis_replication_timestamp", str(pbf)],
        capture_output=True,
        text=True,
        check=False,
    )
    ts = out.stdout.strip()
    return ts or None


def _osmium_extract(src: Path, bbox: Bounds, dest: Path) -> None:
    w, s, e, n = bbox
    subprocess.run(
        # Completing boundary relations (not only multipolygons) keeps district
        # polygons that cross the clip edge assemblable.
        [
            "osmium", "extract", "--overwrite", "-s", "smart", "-S", "types=multipolygon,boundary",
            "-b", f"{w},{s},{e},{n}", "-o", str(dest), str(src),
        ],
        check=True,
    )


def download_osm(project: Project, manifest: Manifest, mode: str = "geofabrik") -> dict[str, Path]:
    """Fetch OSM data and clip one PBF per city to its data extent.

    Parameters
    ----------
    project
        Project configuration.
    manifest
        Download manifest.
    mode
        ``"geofabrik"`` downloads the regional extracts listed in ``sources.toml``;
        ``"mirror"`` downloads the full Germany extract from the GWDG mirror; any
        other value is taken as the path of a local ``.osm.pbf`` covering both cities.

    Returns
    -------
    dict
        City id -> clipped PBF path.
    """
    src = project.sources["osm"]
    root = data_dir()
    raw_dir = root / "cache" / "osm"
    out: dict[str, Path] = {}
    pad = 0.01  # degrees; keeps features that straddle the rectangle edge intact
    for city in project.cities:
        w, s, e, n = city_frame(project, city).data_bbox_wgs84
        bbox = (w - pad, s - pad, e + pad, n + pad)
        dest = root / "osm" / f"{city.id}.osm.pbf"
        dest.parent.mkdir(parents=True, exist_ok=True)

        if mode == "geofabrik":
            parts = []
            for region in src["regions"][city.id]:
                url = f"{src['base_url']}{region}-latest.osm.pbf"
                pbf = fetch(url, raw_dir / f"{region.replace('/', '_')}.osm.pbf", manifest, "osm", max_age_days=7)
                md5_url = url + ".md5"
                md5_path = pbf.with_suffix(".pbf.md5")
                fetch(md5_url, md5_path, manifest, "osm", max_age_days=7)
                expected = md5_path.read_text().split()[0]
                if md5_file(pbf) != expected:
                    raise ValueError(f"MD5 mismatch for {pbf}")
                part = dest.with_name(f"{city.id}.{region.replace('/', '_')}.osm.pbf")
                _osmium_extract(pbf, bbox, part)
                parts.append(part)
            if len(parts) == 1:
                parts[0].replace(dest)
            else:
                subprocess.run(["osmium", "merge", "--overwrite", "-o", str(dest), *map(str, parts)], check=True)
                for p in parts:
                    p.unlink()
        else:
            if mode == "mirror":
                pbf = fetch(src["mirror_germany_url"], raw_dir / "germany-latest.osm.pbf", manifest, "osm", max_age_days=7)
            else:
                pbf = Path(mode)
                if manifest.get(pbf) is None:
                    manifest.add(
                        ManifestEntry(
                            source="osm",
                            url=f"file://{pbf}",
                            path=str(pbf),
                            bytes=pbf.stat().st_size,
                            sha256="",
                            downloaded=dt.datetime.fromtimestamp(pbf.stat().st_mtime, dt.UTC).isoformat(timespec="seconds"),
                        )
                    )
            _osmium_extract(pbf, bbox, dest)
        out[city.id] = dest
    return out


# ------------------------------------------------------------------- GTFS & misc


def download_gtfs(project: Project, manifest: Manifest) -> dict[str, Path]:
    """Fetch the GTFS feeds (SWU for Ulm/Neu-Ulm, NVBW-KVV for Karlsruhe).

    Returns
    -------
    dict
        Feed id (``swu``, ``swu2027``, ``kvv``) -> zip path.
    """
    root = data_dir() / "cache" / "gtfs"
    feeds = {
        "swu": (project.sources["gtfs_swu"]["url"], "gtfs_swu"),
        "kvv": (project.sources["gtfs_kvv"]["url"], "gtfs_kvv"),
    }
    if project.sources["gtfs_swu"].get("url_2027"):
        feeds["swu2027"] = (project.sources["gtfs_swu"]["url_2027"], "gtfs_swu")
    return {
        fid: fetch(url, root / f"{fid}.zip", manifest, source, max_age_days=3) for fid, (url, source) in feeds.items()
    }


def download_districts(project: Project, manifest: Manifest) -> Path:
    """Fetch the official Karlsruhe Stadtteile GeoJSON."""
    return fetch(
        project.sources["districts_ka"]["url"],
        data_dir() / "cache" / "districts" / "ka_stadtteile.json",
        manifest,
        "districts_ka",
        max_age_days=30,
    )


# ---------------------------------------------------------------------- DEM


def bw_tile_origins(bounds: Bounds) -> list[tuple[int, int]]:
    """South-west corners (km) of the LGL 2 km tiles intersecting a UTM rectangle.

    The LGL grid has odd easting and even northing kilometre corners
    (e.g. ``dgm1_32_573_5360_2_bw``), verified against the portal's tile index.

    Parameters
    ----------
    bounds
        ``(minx, miny, maxx, maxy)`` in UTM 32N metres.

    Returns
    -------
    list of (easting_km, northing_km)
    """
    minx, miny, maxx, maxy = bounds
    e0 = int(minx // 1000)
    e0 -= (e0 - 1) % 2  # snap down to odd
    n0 = int(miny // 1000)
    n0 -= n0 % 2  # snap down to even
    tiles = []
    for e in range(e0, int(maxx // 1000) + 1, 2):
        for n in range(n0, int(maxy // 1000) + 1, 2):
            tiles.append((e, n))
    return tiles


def by_tiles_from_metalink(xml_text: str) -> list[tuple[str, str, str]]:
    """Parse a Bavarian metalink into ``(name, url, sha256)`` triples."""
    ns = {"m": "urn:ietf:params:xml:ns:metalink"}
    root = ElementTree.fromstring(xml_text)
    out = []
    for f in root.findall("m:file", ns):
        name = f.attrib["name"]
        url = f.find("m:url", ns).text  # type: ignore[union-attr]
        h = f.find("m:hash[@type='sha-256']", ns)
        out.append((name, url or "", h.text if h is not None and h.text else ""))
    return out


def _intersects_km_tile(name: str, size_km: int, bounds: Bounds) -> bool:
    e, n = (int(v) for v in Path(name).stem.split("_")[:2])
    minx, miny, maxx, maxy = bounds
    return e * 1000 < maxx and (e + size_km) * 1000 > minx and n * 1000 < maxy and (n + size_km) * 1000 > miny


def download_dem(project: Project, manifest: Manifest, workers: int = 6) -> dict[str, dict[str, list[Path]]]:
    """Fetch official DGM1 tiles (BW, BY) and Copernicus tiles for gap filling.

    Parameters
    ----------
    project
        Project configuration.
    manifest
        Download manifest.
    workers
        Parallel downloads; the LGL server delivers ~1 MB/s per connection.

    Returns
    -------
    dict
        City id -> {"bw": [...], "by": [...], "cop": [...]} local paths.
    """
    root = data_dir() / "cache" / "dem"
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_maxsize=workers)
    session.mount("https://", adapter)
    result: dict[str, dict[str, list[Path]]] = {}

    by_index: list[tuple[str, str, str]] = []
    metalink = fetch(
        project.sources["dem_by"]["metalink_url"], root / "by" / "metalink.meta4", manifest, "dem_by", max_age_days=30
    )
    by_index = by_tiles_from_metalink(metalink.read_text())

    for city in project.cities:
        bounds = city_frame(project, city).data_utm
        jobs: list[tuple[str, str, Path, str | None]] = []
        for e, n in bw_tile_origins(bounds):
            url = project.sources["dem_bw"]["tile_url"].format(e=e, n=n)
            jobs.append(("bw", url, root / "bw" / Path(url).name, None))
        for name, url, sha in by_index:
            if _intersects_km_tile(name, 1, bounds):
                jobs.append(("by", url, root / "by" / name, sha or None))
        w, s, e_, n_ = city_frame(project, city).data_bbox_wgs84
        for lat in range(math.floor(s), math.floor(n_) + 1):
            for lon in range(math.floor(w), math.floor(e_) + 1):
                url = project.sources["dem_copernicus"]["tile_url"].format(lat=lat, lon=lon)
                jobs.append(("cop", url, root / "cop" / Path(url).name, None))

        def run(job: tuple[str, str, Path, str | None]) -> tuple[str, Path | None]:
            kind, url, dest, sha = job
            source = {"bw": "dem_bw", "by": "dem_by", "cop": "dem_copernicus"}[kind]
            try:
                return kind, fetch(url, dest, manifest, source, sha256=sha, session=session)
            except NotFound:
                # BW tiles that lie entirely outside Baden-Württemberg do not exist.
                return kind, None

        paths: dict[str, list[Path]] = {"bw": [], "by": [], "cop": []}
        with ThreadPoolExecutor(workers) as pool:
            for kind, path in pool.map(run, jobs):
                if path is not None:
                    paths[kind].append(path)
        result[city.id] = paths
        log.info("DEM %s: %d BW, %d BY, %d Copernicus tiles", city.id, *(len(paths[k]) for k in ("bw", "by", "cop")))
        manifest.save()
    return result

