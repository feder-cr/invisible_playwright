"""The first WebGL context of a fresh browser, ours or Playwright's own Firefox, in the same job: null or not, and the browser's own reason.

    python scripts/webgl_first_context_probe.py MODE OUT.jsonl [WARM]

MODE: ours (InvisiblePlaywright, headless=True: our Xvfb path, seed 42, as tests/test_fingerprint_consistency.py) | stock-headed | stock-headless
(Playwright's own Firefox, launched headed under the job's Xvfb, or with its own headless) | retail-headed (the official Firefox release, path in RETAIL_FIREFOX,
driven by Selenium and geckodriver, headed under the job's Xvfb: the browser a user has). WARM: how many cheap evaluations come first on the
page (the module ran 38 tests on the browser before the WebGL one). One JSON line is appended: mode, webgl/webgl2 null or not, the statusMessages,
the version of the browser, the milliseconds the whole round took.
"""
import json
import os
import sys
import time

mode, out = sys.argv[1], sys.argv[2]
warm = int(sys.argv[3]) if len(sys.argv) > 3 else 38

JS = """() => {
  const errors = [];
  const c = document.createElement('canvas');
  c.addEventListener('webglcontextcreationerror', e => errors.push(e.statusMessage || ''), false);
  const gl = c.getContext('webgl');
  const c2 = document.createElement('canvas');
  c2.addEventListener('webglcontextcreationerror', e => errors.push('webgl2: ' + (e.statusMessage || '')), false);
  const gl2 = c2.getContext('webgl2');
  return {webgl: !!gl, webgl2: !!gl2, errors, lost: gl ? gl.isContextLost() : null};
}"""
WARM_JS = "() => [navigator.userAgent, Math.cos(1e308), Math.sin(Math.PI), screen.width, performance.now()].length"

row = {"mode": mode, "warm": warm}
t0 = time.monotonic()
try:
    if mode == "ours":
        from invisible_playwright import InvisiblePlaywright
        with InvisiblePlaywright(seed=42, binary_path=os.environ["INVPW_BINARY_PATH"], headless=True) as browser:
            page = browser.new_context().new_page()
            page.goto("about:blank", timeout=30000)
            for _ in range(warm):
                page.evaluate(WARM_JS)
            row.update(page.evaluate(JS))
    elif mode == "retail-headed":
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        options = Options()
        options.binary_location = os.environ["RETAIL_FIREFOX"]
        driver = webdriver.Firefox(options=options)
        try:
            row["browser_version"] = driver.capabilities.get("browserVersion", "")
            driver.get("about:blank")
            for _ in range(warm):
                driver.execute_script("return (" + WARM_JS + ")()")
            row.update(driver.execute_script("return (" + JS + ")()"))
        finally:
            driver.quit()
    else:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=(mode == "stock-headless"))
            row["browser_version"] = browser.version
            page = browser.new_context().new_page()
            page.goto("about:blank", timeout=30000)
            for _ in range(warm):
                page.evaluate(WARM_JS)
            row.update(page.evaluate(JS))
            browser.close()
except Exception as e:      # a launch that failed is a different finding: recorded, not counted as a null
    row["exception"] = str(e).splitlines()[0][:200]
row["ms"] = round((time.monotonic() - t0) * 1000)
with open(out, "a", encoding="utf-8") as f:
    f.write(json.dumps(row) + "\n")
