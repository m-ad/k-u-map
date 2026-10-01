"""Shared fixtures: data/site availability and a range-capable test server."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from pipeline.config import data_dir, dist_dir


def _has_data() -> bool:
    return (data_dir() / "metrics.json").exists() and (data_dir() / "tiles" / "base.pmtiles").exists()


def _has_site() -> bool:
    return (dist_dir() / "index.html").exists() and (dist_dir() / "data" / "meta.json").exists()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Data/site tests need a pipeline run first. Locally they are skipped when the
    # outputs are missing; CI sets KUMAP_REQUIRE_BUILD=1 so a missing build fails.
    require = os.environ.get("KUMAP_REQUIRE_BUILD") == "1"
    for item in items:
        if "data" in item.keywords and not _has_data() and not require:
            item.add_marker(pytest.mark.skip(reason="no pipeline output in data/; run `python -m pipeline build`"))
        if "site" in item.keywords and not _has_site() and not require:
            item.add_marker(pytest.mark.skip(reason="no built site in dist/; run `python -m pipeline build`"))


@pytest.fixture(scope="session")
def site_url() -> Iterator[str]:
    """Serve ``dist/`` with HTTP Range support for the duration of the session."""
    from pipeline.serve import start

    server, url = start(dist_dir())
    yield url
    server.shutdown()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    """Use a pre-installed Chromium if given and software WebGL for headless runs."""
    args = dict(browser_type_launch_args)
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if exe and Path(exe).exists():
        args["executable_path"] = exe
    args["args"] = [*args.get("args", []), "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
    return args
