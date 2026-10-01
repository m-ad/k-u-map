"""Metric frames in ETRS89 / UTM 32N and geodesic distance rings.

Frames are defined as rectangles in UTM 32N (EPSG:25832) so that "12 km x 10 km"
means the same ground size in both cities; WGS84 bounding boxes are derived from
them for clipping downloads.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pyproj import Geod, Transformer

from .config import City, Project

UTM_EPSG = 25832
TO_UTM = Transformer.from_crs(4326, UTM_EPSG, always_xy=True)
TO_WGS84 = Transformer.from_crs(UTM_EPSG, 4326, always_xy=True)
GEOD = Geod(ellps="GRS80")

Bounds = tuple[float, float, float, float]


@dataclass(frozen=True)
class Frame:
    """Default view rectangle and data extent of one city.

    Attributes
    ----------
    city_id
        City identifier.
    center_utm
        Frame centre in UTM 32N metres.
    frame_utm
        ``(minx, miny, maxx, maxy)`` of the default view.
    data_utm
        Frame grown by the margin; everything is processed within this rectangle.
    """

    city_id: str
    center_utm: tuple[float, float]
    frame_utm: Bounds
    data_utm: Bounds

    @property
    def frame_bbox_wgs84(self) -> Bounds:
        """WGS84 ``(west, south, east, north)`` enclosing the view rectangle."""
        return utm_bounds_to_wgs84(self.frame_utm)

    @property
    def data_bbox_wgs84(self) -> Bounds:
        """WGS84 ``(west, south, east, north)`` enclosing the data rectangle."""
        return utm_bounds_to_wgs84(self.data_utm)

    def frame_contains(self, lon: float, lat: float) -> bool:
        """Whether a WGS84 point lies inside the default view rectangle."""
        x, y = TO_UTM.transform(lon, lat)
        minx, miny, maxx, maxy = self.frame_utm
        return minx <= x <= maxx and miny <= y <= maxy

    def data_contains(self, lon: float, lat: float) -> bool:
        """Whether a WGS84 point lies inside the data rectangle."""
        x, y = TO_UTM.transform(lon, lat)
        minx, miny, maxx, maxy = self.data_utm
        return minx <= x <= maxx and miny <= y <= maxy


def city_frame(project: Project, city: City) -> Frame:
    """Build the frame of a city from the project configuration.

    Parameters
    ----------
    project
        Project configuration (frame size and margin).
    city
        City whose centre defines the frame.

    Returns
    -------
    Frame
        The city's frame.
    """
    cx, cy = TO_UTM.transform(city.lon, city.lat)
    hw, hh, m = project.frame_width_m / 2, project.frame_height_m / 2, project.margin_m
    return Frame(
        city_id=city.id,
        center_utm=(cx, cy),
        frame_utm=(cx - hw, cy - hh, cx + hw, cy + hh),
        data_utm=(cx - hw - m, cy - hh - m, cx + hw + m, cy + hh + m),
    )


def utm_bounds_to_wgs84(bounds: Bounds, densify: int = 50) -> Bounds:
    """Enclosing WGS84 bbox of a UTM rectangle.

    The rectangle's edges are curved in WGS84, so the edges are densified rather
    than transforming only the corners.

    Parameters
    ----------
    bounds
        ``(minx, miny, maxx, maxy)`` in UTM 32N.
    densify
        Number of samples per edge.

    Returns
    -------
    Bounds
        ``(west, south, east, north)`` in degrees.
    """
    minx, miny, maxx, maxy = bounds
    xs: list[float] = []
    ys: list[float] = []
    for i in range(densify + 1):
        t = i / densify
        for x, y in (
            (minx + t * (maxx - minx), miny),
            (minx + t * (maxx - minx), maxy),
            (minx, miny + t * (maxy - miny)),
            (maxx, miny + t * (maxy - miny)),
        ):
            lon, lat = TO_WGS84.transform(x, y)
            xs.append(lon)
            ys.append(lat)
    return (min(xs), min(ys), max(xs), max(ys))


def geodesic_ring(lon: float, lat: float, radius_m: float, n: int = 180) -> list[tuple[float, float]]:
    """Closed ring of points at a fixed geodesic distance from a centre.

    Parameters
    ----------
    lon, lat
        Centre in WGS84 degrees.
    radius_m
        Geodesic radius in metres.
    n
        Number of distinct vertices.

    Returns
    -------
    list of (lon, lat)
        Ring coordinates, first point repeated at the end.
    """
    azimuths = [360.0 * i / n for i in range(n)]
    lons, lats, _ = GEOD.fwd([lon] * n, [lat] * n, azimuths, [radius_m] * n)
    ring = list(zip(lons, lats, strict=True))
    ring.append(ring[0])
    return ring


def bike_minutes(distance_m: float, speed_kmh: float) -> int:
    """Whole minutes to cover a straight-line distance by bike.

    Parameters
    ----------
    distance_m
        Distance in metres.
    speed_kmh
        Assumed average speed.

    Returns
    -------
    int
        Rounded minutes.
    """
    return round(distance_m / 1000.0 / speed_kmh * 60.0)


def utm_zone(lon: float) -> int:
    """Standard UTM zone number of a longitude (no Norway/Svalbard exceptions)."""
    return int(math.floor((lon + 180.0) / 6.0)) + 1
