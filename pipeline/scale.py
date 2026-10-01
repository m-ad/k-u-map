"""Web-Mercator scale math for MapLibre GL JS.

MapLibre draws the world into ``512 * 2**zoom`` CSS pixels. Ground resolution at
latitude ``phi`` is therefore ``C * cos(phi) / (512 * 2**zoom)`` with ``C`` the
equatorial circumference. Because Ulm (48.4°N) and Karlsruhe (49.0°N) differ in
latitude, equal zoom levels would show Ulm about 1.2 % smaller. The site keeps one
shared metres-per-pixel value and derives each panel's zoom from its own centre
latitude. ``site/js/scale.js`` mirrors these functions; keep the two in sync.
"""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6378137.0
EARTH_CIRCUMFERENCE_M = 2 * math.pi * EARTH_RADIUS_M
WORLD_TILE_PX = 512.0


def meters_per_pixel(zoom: float, lat_deg: float) -> float:
    """Ground metres per CSS pixel at a map centre.

    Parameters
    ----------
    zoom
        MapLibre zoom level.
    lat_deg
        Latitude of the map centre in degrees.

    Returns
    -------
    float
        Metres on the ground per CSS pixel.
    """
    return EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat_deg)) / (WORLD_TILE_PX * 2.0**zoom)


def zoom_for_mpp(mpp: float, lat_deg: float) -> float:
    """Inverse of :func:`meters_per_pixel`.

    Parameters
    ----------
    mpp
        Target metres per CSS pixel.
    lat_deg
        Latitude of the map centre in degrees.

    Returns
    -------
    float
        Zoom level that yields ``mpp`` at ``lat_deg``.
    """
    return math.log2(EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat_deg)) / (WORLD_TILE_PX * mpp))


def synced_zoom(zoom_src: float, lat_src_deg: float, lat_dst_deg: float) -> float:
    """Zoom for a second panel so both show the same metres per pixel.

    Parameters
    ----------
    zoom_src
        Zoom of the panel the user interacted with.
    lat_src_deg, lat_dst_deg
        Centre latitudes of the source and destination panels.

    Returns
    -------
    float
        ``zoom_src + log2(cos(lat_dst) / cos(lat_src))``.
    """
    return zoom_src + math.log2(math.cos(math.radians(lat_dst_deg)) / math.cos(math.radians(lat_src_deg)))


def fit_zoom(width_m: float, height_m: float, width_px: float, height_px: float, lat_deg: float) -> float:
    """Largest zoom at which a ``width_m`` x ``height_m`` rectangle fits the panel.

    Parameters
    ----------
    width_m, height_m
        Ground size of the frame.
    width_px, height_px
        Panel size in CSS pixels.
    lat_deg
        Latitude of the frame centre.

    Returns
    -------
    float
        Zoom level.
    """
    mpp = max(width_m / width_px, height_m / height_px)
    return zoom_for_mpp(mpp, lat_deg)


def nice_scale_length(max_m: float) -> float:
    """Round length (1/2/5 x 10^n metres) not exceeding ``max_m``, for scale bars.

    Parameters
    ----------
    max_m
        Longest acceptable bar length in metres.

    Returns
    -------
    float
        Bar length in metres.
    """
    exp = math.floor(math.log10(max_m))
    for step in (5.0, 2.0, 1.0):
        candidate = step * 10.0**exp
        if candidate <= max_m:
            return candidate
    return 10.0 ** (exp - 1) * 5.0
