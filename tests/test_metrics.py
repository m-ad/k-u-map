"""Tests for metric helpers."""

import math

import pytest
from shapely.geometry import LineString, MultiLineString

from pipeline.metrics import network_length


@pytest.mark.parametrize("angle_deg", [0, 17, 45, 80])
def test_skeleton_length_of_double_track_counts_once(angle_deg: float) -> None:
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    nx, ny = -dy, dx
    # Two parallel tracks 3.5 m apart, 1 km long, buffered like the tram metric.
    tracks = MultiLineString(
        [
            LineString([(o * nx, o * ny), (1000 * dx + o * nx, 1000 * dy + o * ny)])
            for o in (-1.75, 1.75)
        ]
    )
    assert network_length(list(tracks.geoms)) == pytest.approx(1000.0, rel=0.01)


def test_network_length_counts_separate_branches() -> None:
    # A Y junction: shared 500 m trunk (double track), two 300 m single-track branches.
    trunk = [LineString([(0, o), (500, o)]) for o in (-1.75, 1.75)]
    branches = [LineString([(500, 0), (800, 300)]), LineString([(500, 0), (800, -300)])]
    expected = 500 + 2 * math.hypot(300, 300)
    assert network_length(trunk + branches) == pytest.approx(expected, rel=0.02)


def test_network_length_of_nothing() -> None:
    assert network_length([]) == 0.0
