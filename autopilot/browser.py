"""Attach Playwright to the user's real, already-logged-in Chrome.

Chrome must be running with remote debugging enabled. Start it via:
    python run.py chrome
(or manually:  chrome.exe --remote-debugging-port=9222 --user-data-dir=<profile copy>)
"""
from playwright.sync_api import sync_playwright


def attach(cfg):
    port = cfg["apply"]["chrome_debug_port"]
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://localhost:{port}")
    except Exception:
        pw.stop()
        raise RuntimeError(
            f"Could not attach to Chrome on port {port}.\n"
            "Start Chrome first:  python run.py chrome\n"
            "Then log in to LinkedIn and Naukri in that window and retry."
        )
    context = browser.contexts[0]
    return pw, browser, context
