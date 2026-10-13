"""`speechSynthesis.getVoices()` answers what retail Windows Firefox answers.

Measured on retail Windows Firefox 151 on 2026-10-13: David, Mark, Zira, then
David Desktop and Zira Desktop, all `en-US`, every one `localService` true and
none of them `default` (Gecko's SAPI backend never calls SetDefaultVoice).
Until invisible-core 44.39.0 the profile made David the default and listed Zira
before Mark. The list reaches the engine through `zoom.stealth.voices.list`,
so this reads it where a page reads it.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

RETAIL = [
    ["Microsoft David - English (United States)", "en-US", False, True],
    ["Microsoft Mark - English (United States)", "en-US", False, True],
    ["Microsoft Zira - English (United States)", "en-US", False, True],
    ["Microsoft David Desktop - English (United States)", "en-US", False, True],
    ["Microsoft Zira Desktop - English (United States)", "en-US", False, True],
]

READ = """() => new Promise(resolve => {
  const read = () => speechSynthesis.getVoices().map(
      v => [v.name, v.lang, v.default, v.localService]);
  if (read().length) return resolve(read());
  speechSynthesis.addEventListener('voiceschanged', () => resolve(read()),
                                   {once: true});
  setTimeout(() => resolve(read()), 5000);
})"""


def test_the_voices_are_retail_windows_in_its_order_and_none_is_the_default(
        firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             headless=True) as browser:
        page = browser.new_page()
        page.goto("about:blank")
        assert page.evaluate(READ) == RETAIL
