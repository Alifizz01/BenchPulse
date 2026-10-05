"""Screenshots for the README, from a running demo:

    python -m benchpulse demo --no-browser &
    python docs/make_screenshots.py            # uses your installed Chrome
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8600/"
OUT = Path(__file__).parent

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", args=["--lang=en-GB"])
    for scheme in ("light", "dark"):
        page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme=scheme, device_scale_factor=1)
        page.goto(URL)
        page.wait_for_selector(".card")
        page.wait_for_timeout(800)
        page.screenshot(path=OUT / f"board-{scheme}.png")
        if scheme == "light":
            page.locator("section").nth(1).screenshot(path=OUT / "schedule.png")
            page.locator(".card", has_text="HIL-RACK-01").screenshot(path=OUT / "card-clash.png")
            page.locator(".card", has_text="HIL-RACK-03").get_by_text("Reserve").click()
            page.fill("input[name=who]", "Alif")
            page.fill("input[name=purpose]", "CAN timeout fault injection")
            page.wait_for_timeout(300)
            page.locator("#bookDialog").screenshot(path=OUT / "reserve.png")
        page.close()
    phone = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    phone.goto(URL)
    phone.wait_for_selector(".card")
    phone.wait_for_timeout(600)
    phone.screenshot(path=OUT / "phone.png")
    browser.close()
print("ok")
