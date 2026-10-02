"""Browser smoke test of the built site (Playwright, headless Chromium)."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.site
# Not test-results/: pytest-playwright clears that directory at session start.
SHOTS = Path(__file__).resolve().parent.parent / "screenshots"


def mpp_pair(page: Page) -> tuple[float, float]:
    """Ground metres per CSS pixel of both panels, as reported by the app."""
    ka, ulm = page.evaluate("[document.body.dataset.mppKa, document.body.dataset.mppUlm]")
    return float(ka), float(ulm)


def assert_same_scale(page: Page) -> None:
    ka, ulm = mpp_pair(page)
    assert abs(ka / ulm - 1) < 1e-3, f"m/px differ: Karlsruhe {ka}, Ulm {ulm}"


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [400, 1300])
def test_page_loads_without_errors_and_keeps_scale(page: Page, site_url: str, width: int, scheme: str) -> None:
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})

    page.goto(site_url)
    page.wait_for_selector('body[data-ready="true"]', timeout=90_000)
    page.wait_for_timeout(1500)
    assert_same_scale(page)
    # The default frame is 12 km wide.
    assert "Bildbreite 12 km" in page.inner_text("#readout")

    # Zoom in on Karlsruhe with the wheel, then pan Ulm: scale must stay equal.
    ka = page.locator("#map-ka").bounding_box()
    page.mouse.move(ka["x"] + ka["width"] / 2, ka["y"] + ka["height"] / 2)
    page.mouse.wheel(0, -600)
    page.wait_for_timeout(1200)
    assert_same_scale(page)
    ulm = page.locator("#map-ulm").bounding_box()
    page.mouse.move(ulm["x"] + ulm["width"] / 2, ulm["y"] + ulm["height"] / 2)
    page.mouse.down()
    page.mouse.move(ulm["x"] + ulm["width"] / 2 + 60, ulm["y"] + ulm["height"] / 2 + 140, steps=8)
    page.mouse.up()
    page.wait_for_timeout(800)
    assert_same_scale(page)

    page.click("#reset")
    page.wait_for_timeout(500)
    assert "Zoomfaktor ×1" in page.inner_text("#readout")

    assert page.locator("#chips .chip").count() == 11
    bus = page.locator('#chips .chip[data-group="bus"]')
    before = bus.get_attribute("aria-pressed")
    bus.click()
    assert bus.get_attribute("aria-pressed") != before
    bus.click()

    page.wait_for_selector("#compare-table .row", timeout=10_000)
    assert page.locator("#compare-table .row").count() > 10
    # The default 2 km circle includes the Kita counts.
    assert "Ü3 bestätigt" in page.inner_text("#compare-table")
    assert page.locator("#sources li").count() >= 8

    page.wait_for_timeout(1500)
    SHOTS.mkdir(exist_ok=True)
    page.screenshot(path=str(SHOTS / f"screenshot-{width}-{scheme}.png"))
    assert not errors, f"console errors: {errors}"
