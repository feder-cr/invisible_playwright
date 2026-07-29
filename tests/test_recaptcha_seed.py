"""Unit tests for the deterministic reCAPTCHA cookie builder.

Validates the contract:
  - 6 .google.com cookies always present
  - Per-site cookies built from a `browsing_history` list (sampled by the
    Bayesian network in _fpforge)
  - Determinism: same (seed, history) → identical content
  - Chrome 400-day cookie cap respected
  - Playwright add_cookies field requirements satisfied
"""
import pytest

from invisible_playwright._recaptcha_seed import (
    build_cookies,
    _sub_seed,
)


pytestmark = pytest.mark.unit


_FIXED_NOW = 1779600000  # 2026-05-23, frozen for determinism


# Sample browsing history for tests (mimics what _fpforge produces).
_SAMPLE_HISTORY = [
    {"name": "github.com",       "category": "dev",  "cookie_profile": "ga_cf"},
    {"name": "stackoverflow.com", "category": "dev", "cookie_profile": "ga_consent_clarity"},
    {"name": "amazon.com",       "category": "shop", "cookie_profile": "ga_consent_clarity"},
    {"name": "wikipedia.org",    "category": "reference", "cookie_profile": "minimal"},
    {"name": "youtube.com",      "category": "media", "cookie_profile": "ga_only"},
]


# ===========================================================================
# 1. Set composition
# ===========================================================================

def test_only_google_cookies_when_no_history():
    """Empty/None history → only the 5 .google.com cookies (1P_JAR removed
    in realism round 2 - deprecated by Google 2022)."""
    cookies = build_cookies(seed=42, browsing_history=None, now=_FIXED_NOW)
    names = sorted(c["name"] for c in cookies)
    assert names == sorted(["NID", "CONSENT", "SOCS",
                            "_GRECAPTCHA", "ENID"])
    assert all(c["domain"] == ".google.com" for c in cookies)


def test_browsing_history_adds_host_cookies():
    """Each history site contributes 1+ cookies on its domain."""
    cookies = build_cookies(seed=42, browsing_history=_SAMPLE_HISTORY, now=_FIXED_NOW)
    google = [c for c in cookies if c["domain"] == ".google.com"]
    assert len(google) == 5  # 1P_JAR removed

    domains = {c["domain"] for c in cookies if c["domain"] != ".google.com"}
    for site in _SAMPLE_HISTORY:
        assert f".{site['name']}" in domains


def test_domain_dot_prefix_normalized():
    """All host cookie domains have a leading dot for sub-domain coverage."""
    cookies = build_cookies(seed=42, browsing_history=_SAMPLE_HISTORY, now=_FIXED_NOW)
    for c in cookies:
        assert c["domain"].startswith("."), f"missing dot: {c['domain']}"


# ===========================================================================
# 2. Cookie profile recipes (each profile yields the expected cookie set)
# ===========================================================================

def test_profile_minimal_yields_ga_only():
    history = [{"name": "x.com", "cookie_profile": "minimal"}]
    cookies = build_cookies(seed=42, browsing_history=history, now=_FIXED_NOW)
    host = [c for c in cookies if c["domain"] == ".x.com"]
    names = [c["name"] for c in host]
    assert names == ["_ga"]


def test_profile_ga_only_yields_ga_and_gid():
    history = [{"name": "x.com", "cookie_profile": "ga_only"}]
    cookies = build_cookies(seed=42, browsing_history=history, now=_FIXED_NOW)
    host = [c for c in cookies if c["domain"] == ".x.com"]
    names = sorted(c["name"] for c in host)
    assert names == ["_ga", "_gid"]


def test_profile_ga_cf_yields_ga_and_cf_bm():
    history = [{"name": "x.com", "cookie_profile": "ga_cf"}]
    cookies = build_cookies(seed=42, browsing_history=history, now=_FIXED_NOW)
    host = [c for c in cookies if c["domain"] == ".x.com"]
    names = sorted(c["name"] for c in host)
    assert names == ["__cf_bm", "_ga"]


def test_profile_ga_consent_yields_three_cookies():
    history = [{"name": "x.com", "cookie_profile": "ga_consent"}]
    cookies = build_cookies(seed=42, browsing_history=history, now=_FIXED_NOW)
    host = [c for c in cookies if c["domain"] == ".x.com"]
    names = sorted(c["name"] for c in host)
    # Always _ga + _gid + one of OneTrust|CookieYes
    assert "_ga" in names and "_gid" in names
    assert any(n in names for n in ("OptanonAlertBoxClosed", "cookieyes-consent"))
    assert len(host) == 3


def test_profile_ga_consent_clarity_yields_at_least_four_cookies():
    """Always _ga + _gid + _clck + consent banner. Optionally _fbp, _dc_gtm_*,
    __hssrc (probabilistic per rng - see test_new_helper_cookies_*)."""
    history = [{"name": "x.com", "cookie_profile": "ga_consent_clarity"}]
    cookies = build_cookies(seed=42, browsing_history=history, now=_FIXED_NOW)
    host = [c for c in cookies if c["domain"] == ".x.com"]
    names = sorted(c["name"] for c in host)
    assert "_ga" in names and "_gid" in names and "_clck" in names
    assert any(n in names for n in ("OptanonAlertBoxClosed", "cookieyes-consent"))
    assert len(host) >= 4  # 4 baseline + 0-3 helpers


def test_unknown_profile_falls_back_to_ga():
    history = [{"name": "x.com", "cookie_profile": "nonexistent_profile"}]
    cookies = build_cookies(seed=42, browsing_history=history, now=_FIXED_NOW)
    host = [c for c in cookies if c["domain"] == ".x.com"]
    assert [c["name"] for c in host] == ["_ga"]


# ===========================================================================
# 3. Determinism
# ===========================================================================

def test_same_seed_and_history_same_content():
    a = build_cookies(seed=42, browsing_history=_SAMPLE_HISTORY, now=_FIXED_NOW)
    b = build_cookies(seed=42, browsing_history=_SAMPLE_HISTORY, now=_FIXED_NOW)
    assert a == b


def test_different_seed_different_content():
    a = build_cookies(seed=42, browsing_history=_SAMPLE_HISTORY, now=_FIXED_NOW)
    b = build_cookies(seed=99, browsing_history=_SAMPLE_HISTORY, now=_FIXED_NOW)
    a_nid = next(c for c in a if c["name"] == "NID")["value"]
    b_nid = next(c for c in b if c["name"] == "NID")["value"]
    assert a_nid != b_nid


def test_history_order_does_not_affect_domain_specific_cookies():
    """Sub-seed is keyed on domain name, not order in history list."""
    h1 = [_SAMPLE_HISTORY[0], _SAMPLE_HISTORY[1]]
    h2 = [_SAMPLE_HISTORY[1], _SAMPLE_HISTORY[0]]
    a = {(c["domain"], c["name"]): c["value"]
         for c in build_cookies(seed=42, browsing_history=h1, now=_FIXED_NOW)
         if c["domain"] != ".google.com"}
    b = {(c["domain"], c["name"]): c["value"]
         for c in build_cookies(seed=42, browsing_history=h2, now=_FIXED_NOW)
         if c["domain"] != ".google.com"}
    assert a == b


def test_sub_seed_distinct_tags_distinct_streams():
    assert _sub_seed(42, "google") != _sub_seed(42, "dom:github.com")
    assert _sub_seed(42, "dom:github.com") != _sub_seed(42, "dom:amazon.com")
    assert _sub_seed(0, "any") != 0  # seed=0 still produces non-zero sub-seed


# ===========================================================================
# 4. Format / structural correctness for the Google batch
# ===========================================================================

def test_nid_format():
    cookies = build_cookies(seed=42, now=_FIXED_NOW)
    nid = next(c for c in cookies if c["name"] == "NID")
    prefix, b64 = nid["value"].split("=", 1)
    assert prefix.isdigit() and len(prefix) == 3
    # Broadened to 100-540 in realism round 2 to cover historical NID versions
    assert 100 <= int(prefix) <= 540
    assert len(b64) == 178


def test_consent_format():
    cookies = build_cookies(seed=42, now=_FIXED_NOW)
    consent = next(c for c in cookies if c["name"] == "CONSENT")
    assert consent["value"].startswith("YES+cb.")
    assert "+FX+" in consent["value"]


# ===========================================================================
# 5. Chrome 400-day cookie cap compliance
# ===========================================================================

def test_all_expiries_within_400_day_cap():
    """Chrome 104+ caps cookie expiry to 400 days. Cookies > 400d silently
    truncated / dropped. We tighten everything to <=395d (except __cf_bm
    which is short-lived telemetry)."""
    cookies = build_cookies(seed=42, browsing_history=_SAMPLE_HISTORY, now=_FIXED_NOW)
    max_allowed = _FIXED_NOW + 400 * 86400
    for c in cookies:
        # Short-lived telemetry cookies are fine
        if c["name"] in ("__cf_bm", "1P_JAR", "_gid"):
            continue
        assert c["expires"] <= max_allowed, (
            f"Cookie {c['name']} expires {c['expires'] - _FIXED_NOW}s "
            f"(> 400d cap) - would be silently dropped"
        )


# ===========================================================================
# 6. Playwright add_cookies field requirements
# ===========================================================================

def test_all_cookies_have_required_playwright_fields():
    cookies = build_cookies(seed=42, browsing_history=_SAMPLE_HISTORY, now=_FIXED_NOW)
    for c in cookies:
        assert c.get("name"), f"missing name: {c}"
        assert c.get("value") is not None, f"missing value: {c}"
        assert c.get("domain"), f"missing domain: {c}"
        assert c.get("path") == "/", f"path != / for {c['name']}"


def test_modern_cookies_marked_secure():
    """Cookies with sameSite=None require secure=True under Firefox/Chrome.
    Also generally needed for cookies set via Playwright add_cookies without
    a navigation context."""
    cookies = build_cookies(seed=42, browsing_history=_SAMPLE_HISTORY, now=_FIXED_NOW)
    for c in cookies:
        if c.get("sameSite") == "None":
            assert c.get("secure") is True, f"{c['name']} None+!secure invalid"


def test_httponly_on_signed_cookies():
    cookies = build_cookies(seed=42, now=_FIXED_NOW)
    nid  = next(c for c in cookies if c["name"] == "NID")
    enid = next(c for c in cookies if c["name"] == "ENID")
    assert nid.get("httpOnly") is True
    assert enid.get("httpOnly") is True


# ===========================================================================
# 7. End-to-end with real fpforge Profile
# ===========================================================================

def test_with_real_fpforge_profile():
    """End-to-end: generate a real Profile, ensure browsing_history is populated
    and build_cookies works against it."""
    from invisible_core._fpforge import generate_profile
    prof = generate_profile(seed=42)
    assert isinstance(prof.browsing_history, list)
    # The Bayesian network samples ~15-30 sites per persona
    assert 5 <= len(prof.browsing_history) <= 50, \
        f"unexpected history length: {len(prof.browsing_history)}"
    # Each entry has the expected fields
    for site in prof.browsing_history:
        assert "name" in site and "category" in site and "cookie_profile" in site
    # build_cookies works against the real profile
    cookies = build_cookies(seed=prof.seed, browsing_history=prof.browsing_history,
                            now=_FIXED_NOW)
    # 6 google + at least 1 cookie per visited site
    assert len(cookies) >= 6 + len(prof.browsing_history)


def test_same_seed_same_browsing_history_via_fpforge():
    """Profile.browsing_history is deterministic from seed (Bayesian sampler)."""
    from invisible_core._fpforge import generate_profile
    a = generate_profile(seed=42).browsing_history
    b = generate_profile(seed=42).browsing_history
    assert a == b


# ===========================================================================
# 8. Realism improvements (2026-05-24 round 2)
# ===========================================================================

def test_no_1p_jar_cookie():
    """1P_JAR was deprecated by Google in 2022. Including it is an
    anachronism flag for fingerprinters that look at cookie freshness."""
    cookies = build_cookies(seed=42, browsing_history=_SAMPLE_HISTORY, now=_FIXED_NOW)
    names = {c["name"] for c in cookies}
    assert "1P_JAR" not in names


def test_nid_prefix_broadened_range():
    """NID 3-digit prefix should cover historical versions (137/105/511/525
    seen in real captures) - range 100-540, not just 500-540."""
    seen_prefixes = set()
    for seed in range(200):
        cookies = build_cookies(seed=seed, now=_FIXED_NOW)
        nid = next(c for c in cookies if c["name"] == "NID")
        prefix = int(nid["value"].split("=", 1)[0])
        seen_prefixes.add(prefix)
    assert min(seen_prefixes) < 500, f"NID range never goes below 500 ({sorted(seen_prefixes)[:5]})"
    assert max(seen_prefixes) <= 540


def test_consent_lang_from_timezone_eu():
    """CONSENT cookie's `lang+region` token derived from IANA timezone."""
    cookies = build_cookies(seed=42, now=_FIXED_NOW, timezone="Europe/Rome")
    consent = next(c for c in cookies if c["name"] == "CONSENT")
    assert ".it+IT+" in consent["value"], f"expected it+IT in: {consent['value']}"


def test_consent_lang_default_fx():
    """Unknown / US timezone → default `en+FX` (non-EU fallback)."""
    cookies = build_cookies(seed=42, now=_FIXED_NOW, timezone="America/New_York")
    consent = next(c for c in cookies if c["name"] == "CONSENT")
    assert ".en+FX+" in consent["value"]


def test_consent_lang_de_for_berlin():
    cookies = build_cookies(seed=42, now=_FIXED_NOW, timezone="Europe/Berlin")
    consent = next(c for c in cookies if c["name"] == "CONSENT")
    assert ".de+DE+" in consent["value"]


def test_consent_lang_no_timezone_default():
    """timezone=None → default en+FX."""
    cookies = build_cookies(seed=42, now=_FIXED_NOW)
    consent = next(c for c in cookies if c["name"] == "CONSENT")
    assert ".en+FX+" in consent["value"]


def test_new_helper_cookies_appear_in_ga_consent_clarity():
    """ga_consent_clarity recipe should sometimes include _fbp, _dc_gtm_*, __hssrc
    (probabilistic per rng). Check across many seeds that they appear."""
    saw_fbp = False
    saw_gtm = False
    saw_hssrc = False
    history = [{"name": "site.com", "cookie_profile": "ga_consent_clarity"}]
    for seed in range(100):
        cookies = build_cookies(seed=seed, browsing_history=history, now=_FIXED_NOW)
        names = {c["name"] for c in cookies if c["domain"] == ".site.com"}
        if "_fbp" in names: saw_fbp = True
        if any(n.startswith("_dc_gtm_") for n in names): saw_gtm = True
        if "__hssrc" in names: saw_hssrc = True
    assert saw_fbp, "_fbp never appeared in 100 seeds (rng pick broken)"
    assert saw_gtm, "_dc_gtm_* never appeared in 100 seeds"
    assert saw_hssrc, "__hssrc never appeared in 100 seeds"


def test_fbp_format():
    """_fbp format: fb.<idx>.<unix_ms>.<random_int>"""
    history = [{"name": "x.com", "cookie_profile": "ga_consent_clarity"}]
    # Try multiple seeds until we hit a seed that includes _fbp (50% chance)
    for seed in range(20):
        cookies = build_cookies(seed=seed, browsing_history=history, now=_FIXED_NOW)
        fbp = next((c for c in cookies if c["name"] == "_fbp"), None)
        if fbp:
            parts = fbp["value"].split(".")
            assert parts[0] == "fb"
            assert parts[1].isdigit()
            assert parts[2].isdigit() and len(parts[2]) >= 13  # unix ms
            assert parts[3].isdigit()
            return
    raise AssertionError("never got _fbp across 20 seeds - distribution broken")
