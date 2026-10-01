"""Command-line entry point: ``uv run python -m pipeline <stage>``.

Stages run in order for ``build``: download -> osm -> gtfs -> districts -> kitas ->
dem -> tiles -> metrics -> site. Each stage can also be run on its own.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable

from .config import data_dir, load_project
from .download import Manifest

STAGES = ("download", "osm", "gtfs", "districts", "kitas", "dem", "tiles", "metrics", "site")


def _stage_fn(name: str) -> Callable[..., None]:
    # Imported lazily so that e.g. "site" works without heavy GIS imports.
    from . import stages

    return getattr(stages, f"run_{name}")


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the requested stages.

    Parameters
    ----------
    argv
        Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns
    -------
    int
        Process exit code.
    """
    parser = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__)
    parser.add_argument("stage", choices=("build", *STAGES), nargs="+", help="stage(s) to run")
    parser.add_argument(
        "--osm-source",
        default=os.environ.get("KUMAP_OSM_SOURCE", "geofabrik"),
        help="'geofabrik' (regional extracts), 'mirror' (GWDG Germany mirror) or a local .osm.pbf path",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    project = load_project()
    manifest = Manifest(data_dir() / "manifest.json")
    todo = STAGES if "build" in args.stage else tuple(s for s in STAGES if s in args.stage)
    for name in todo:
        t0 = time.time()
        logging.info("=== stage %s", name)
        _stage_fn(name)(project, manifest, osm_source=args.osm_source)
        manifest.save()
        logging.info("=== stage %s done in %.0f s", name, time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
