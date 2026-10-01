"""Load the TOML project configuration into typed objects.

The configuration lives in ``config/project.toml`` (frames, reference points,
district highlighting) and ``config/sources.toml`` (data sources and licenses).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def data_dir() -> Path:
    """Return the working directory for downloads and intermediate products.

    Returns
    -------
    Path
        ``$KUMAP_DATA_DIR`` if set, else ``<repo>/data``. CI points this at a
        cached location so raw downloads survive between runs.
    """
    return Path(os.environ.get("KUMAP_DATA_DIR", ROOT / "data")).resolve()


def dist_dir() -> Path:
    """Return the output directory of the static site."""
    return Path(os.environ.get("KUMAP_DIST_DIR", ROOT / "dist")).resolve()


@dataclass(frozen=True)
class RefPoint:
    """A labelled reference point (landmark, station, zoo)."""

    id: str
    name: str
    kind: str
    lon: float
    lat: float


@dataclass(frozen=True)
class City:
    """One map panel's city with its default frame centre and reference points."""

    id: str
    name: str
    lon: float
    lat: float
    ring_center_id: str
    points: tuple[RefPoint, ...]
    primary_districts: tuple[str, ...] = ()
    secondary_districts: tuple[str, ...] = ()

    @property
    def ring_center(self) -> RefPoint:
        """The reference point the distance rings and metrics are centred on."""
        for p in self.points:
            if p.id == self.ring_center_id:
                return p
        raise KeyError(f"ring centre {self.ring_center_id!r} not among points of {self.id}")


@dataclass(frozen=True)
class Project:
    """Complete project configuration."""

    frame_width_m: float
    frame_height_m: float
    margin_m: float
    ring_radii_m: tuple[float, ...]
    bike_speed_kmh: float
    cities: tuple[City, ...]
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    label_exclude: tuple[str, ...] = ()

    def city(self, city_id: str) -> City:
        """Return the city with the given id."""
        for c in self.cities:
            if c.id == city_id:
                return c
        raise KeyError(city_id)


def load_project(config_dir: Path = CONFIG_DIR) -> Project:
    """Parse ``project.toml`` and ``sources.toml``.

    Parameters
    ----------
    config_dir
        Directory containing both TOML files.

    Returns
    -------
    Project
        The parsed configuration.
    """
    with open(config_dir / "project.toml", "rb") as fh:
        raw = tomllib.load(fh)
    with open(config_dir / "sources.toml", "rb") as fh:
        sources = tomllib.load(fh)

    districts = raw.get("districts", {})
    cities = []
    for c in raw["cities"]:
        d = districts.get(c["id"], {})
        cities.append(
            City(
                id=c["id"],
                name=c["name"],
                lon=float(c["center"][0]),
                lat=float(c["center"][1]),
                ring_center_id=c["ring_center"],
                points=tuple(
                    RefPoint(p["id"], p["name"], p["kind"], float(p["coord"][0]), float(p["coord"][1]))
                    for p in c["points"]
                ),
                primary_districts=tuple(d.get("primary", [])),
                secondary_districts=tuple(d.get("secondary", [])),
            )
        )
    return Project(
        frame_width_m=raw["frame"]["width_km"] * 1000.0,
        frame_height_m=raw["frame"]["height_km"] * 1000.0,
        margin_m=raw["frame"]["margin_km"] * 1000.0,
        ring_radii_m=tuple(r * 1000.0 for r in raw["rings"]["radii_km"]),
        bike_speed_kmh=float(raw["rings"]["bike_speed_kmh"]),
        cities=tuple(cities),
        sources=sources,
        label_exclude=tuple(raw.get("labels", {}).get("exclude_substrings", [])),
    )
