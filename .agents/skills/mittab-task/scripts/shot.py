"""Headless Chrome screenshots of the local dev server. Invoke through the `shot` wrapper.

Each PATH OUT pair is captured with the same options. OUT is the full-resolution PNG for the PR;
a small JPEG preview is also written for the agent to look at (cheap to view).
"""

import argparse
import base64
import os
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def make_driver(mobile=False, width=1440, height=1000):
    # Selenium Manager discovers installed Chrome or downloads Chrome for Testing
    # and a matching driver on first use; no pre-warmed local cache is required.
    # Avoid selecting a stale chromedriver from PATH for a newer managed Chrome.
    os.environ.setdefault("SE_SKIP_DRIVER_IN_PATH", "true")
    os.environ.setdefault("SE_AVOID_STATS", "true")
    options = Options()
    for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--hide-scrollbars"):
        options.add_argument(arg)
    if mobile:
        options.add_experimental_option("mobileEmulation", {
            "deviceMetrics": {"width": 390, "height": 844, "pixelRatio": 2},
            "userAgent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                          "Mobile/15E148 Safari/604.1"),
        })
    else:
        options.add_argument(f"--window-size={width},{height}")
    service = Service()
    return webdriver.Chrome(service=service, options=options)


def login(driver, base, username="tab", password="password"):
    driver.get(f"{base}/public/login/")
    wait = WebDriverWait(driver, 15)
    wait.until(EC.presence_of_element_located((By.NAME, "username"))).send_keys(username)
    driver.find_element(By.NAME, "password").send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "button[type=submit], input[type=submit]").click()
    wait.until(lambda d: "/public/login/" not in d.current_url)


def capture(driver, full):
    if not full:
        return driver.get_screenshot_as_png()
    metrics = driver.execute_cdp_cmd("Page.getLayoutMetrics", {})
    size = metrics.get("cssContentSize") or metrics["contentSize"]
    viewport = metrics.get("cssLayoutViewport") or metrics["layoutViewport"]
    data = driver.execute_cdp_cmd("Page.captureScreenshot", {
        "format": "png",
        "captureBeyondViewport": True,
        "clip": {"x": 0, "y": 0, "width": viewport["clientWidth"],
                 "height": min(size["height"], 8000), "scale": 1},
    })
    return base64.b64decode(data["data"])


def write_preview(png_path, preview_dir):
    try:
        from PIL import Image
    except ImportError:
        return None
    preview = Path(preview_dir) / f"preview-{png_path.stem}.jpg"
    with Image.open(png_path) as image:
        image = image.convert("RGB")
        image.thumbnail((900, 2400))
        image.save(preview, "JPEG", quality=70)
    return preview


def main():
    parser = argparse.ArgumentParser(
        description="Screenshot dev-server pages: shot [opts] PATH OUT.png [PATH OUT.png ...]")
    parser.add_argument("pairs", nargs="+", help="alternating URL path and output PNG")
    parser.add_argument("--base", required=True)
    parser.add_argument("--preview-dir", required=True)
    parser.add_argument("--mobile", action="store_true", help="iPhone-sized emulation (390x844)")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--full", action="store_true", help="capture the full page height")
    parser.add_argument("--login", action="store_true", help="log in as tab/password first")
    parser.add_argument("--wait", help="CSS selector to wait for before capturing")
    parser.add_argument("--click", action="append", default=[],
                        help="CSS selector; click every match before capturing (repeatable)")
    parser.add_argument("--js", help="JavaScript to run before capturing")
    parser.add_argument("--element", help="CSS selector; capture only this element")
    args = parser.parse_intermixed_args()
    if len(args.pairs) % 2:
        parser.error("arguments must be PATH OUT.png pairs")

    driver = make_driver(args.mobile, args.width, args.height)
    try:
        if args.login:
            login(driver, args.base)
        for path, out in zip(args.pairs[::2], args.pairs[1::2]):
            out = Path(out).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            driver.get(args.base + path if path.startswith("/") else path)
            WebDriverWait(driver, 20).until(
                lambda d: d.execute_script("return document.readyState") == "complete")
            if args.wait:
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, args.wait)))
            for selector in args.click:
                for element in driver.find_elements(By.CSS_SELECTOR, selector):
                    driver.execute_script("arguments[0].click()", element)
            if args.js:
                driver.execute_script(args.js)
            time.sleep(0.5)
            if args.element:
                element = driver.find_element(By.CSS_SELECTOR, args.element)
                driver.execute_script("arguments[0].scrollIntoView({block: 'start'})", element)
                out.write_bytes(element.screenshot_as_png)
            else:
                out.write_bytes(capture(driver, args.full))
            preview = write_preview(out, args.preview_dir)
            note = f"  preview: {preview}" if preview else ""
            print(f"{out} ({os.path.getsize(out) // 1024} KB, page: {driver.current_url}){note}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
