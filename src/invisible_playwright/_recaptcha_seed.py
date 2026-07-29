"""Deterministic reCAPTCHA cookie pre-seed.

Consumes the Bayesian-sampled `browsing_history` from the persona Profile
(see `_fpforge/_sampler.py:derive_browsing_history`). For each visited
site, builds 1-5 realistic cookies whose composition is chosen by the
site's `cookie_profile` tag (analytics-only / consent / bot-defense
/ etc.). All values seeded deterministically from the persona
seed, so a given persona always presents the SAME cookies across sessions.

In addition, always seeds 5 cookies on .google.com (NID, CONSENT, SOCS,
_GRECAPTCHA, ENID). Excludes 1P_JAR which was deprecated by Google in 2022
- including it now is an anachronism flag.

Public API:
    await seed_recaptcha_cookies_async(context, profile, timezone=None)
    seed_recaptcha_cookies_sync(context, profile, timezone=None)

`profile` is an `_fpforge.Profile`; `timezone` is the IANA tz (e.g.
"Europe/Rome") used to derive the CONSENT cookie's language token, so a
European-tz persona gets CONSENT in their language not en+FX.
"""
from __future__ import annotations

import datetime
import random
import time
from typing import Any, List, Optional

# URL-safe base64 alphabet (no padding chars).
_B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_HEX_ALPHABET = "0123456789abcdef"


def _sub_seed(seed: int, tag: str) -> int:
    """FNV-1a mix → independent PRNG streams per logical bucket from one seed."""
    h = 0xcbf29ce484222325 ^ (seed & 0xFFFFFFFF)
    for c in tag.encode("ascii"):
        h ^= c
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h or 0xdeadbeef


def _b64_rand(rng: random.Random, length: int) -> str:
    return "".join(rng.choice(_B64_ALPHABET) for _ in range(length))


def _hex_rand(rng: random.Random, length: int) -> str:
    return "".join(rng.choice(_HEX_ALPHABET) for _ in range(length))


def _yyyymmdd_utc(ts: int) -> str:
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y%m%d")


# IANA timezone -> (country_code, lang) for CONSENT cookie coherence.
# Real EU users get CONSENT with `<lang>+<COUNTRY>+NNN`; non-EU gets `en+FX+NNN`.
# Default fallback `en+FX+NNN` for any tz not in this map.
_TZ_TO_REGION = {
    "Europe/Rome":      ("IT", "it"),
    "Europe/Berlin":    ("DE", "de"),
    "Europe/Paris":     ("FR", "fr"),
    "Europe/Madrid":    ("ES", "es"),
    "Europe/London":    ("GB", "en"),
    "Europe/Amsterdam": ("NL", "nl"),
    "Europe/Brussels":  ("BE", "fr"),
    "Europe/Vienna":    ("AT", "de"),
    "Europe/Zurich":    ("CH", "de"),
    "Europe/Dublin":    ("IE", "en"),
    "Europe/Lisbon":    ("PT", "pt"),
    "Europe/Stockholm": ("SE", "sv"),
    "Europe/Oslo":      ("NO", "no"),
    "Europe/Copenhagen": ("DK", "da"),
    "Europe/Helsinki":  ("FI", "fi"),
    "Europe/Warsaw":    ("PL", "pl"),
    "Europe/Prague":    ("CZ", "cs"),
    "Europe/Athens":    ("GR", "el"),
    "Asia/Tokyo":       ("FX", "ja"),
    "Asia/Shanghai":    ("FX", "zh"),
    "Asia/Hong_Kong":   ("FX", "zh"),
    "Asia/Seoul":       ("FX", "ko"),
}


def _consent_region_lang(timezone: Optional[str]) -> tuple:
    """Map IANA tz → (region_token, lang_2char) for CONSENT cookie.
    Default `("FX", "en")` for US/unknown."""
    if timezone and timezone in _TZ_TO_REGION:
        return _TZ_TO_REGION[timezone]
    return ("FX", "en")


# ---------------------------------------------------------------------------
# .google.com cookie batch (always present, regardless of browsing history)
# ---------------------------------------------------------------------------

def _google_cookies(rng: random.Random, now: int,
                    timezone: Optional[str] = None) -> List[dict]:
    consent_age = rng.randint(60, 720) * 86400
    region, lang = _consent_region_lang(timezone)
    # NID 3-digit prefix range broadened to 100-540 to cover historical NID
    # versions (137, 105, 511, 525 etc. observed in real captures).
    return [
        {"name": "NID",
         "value": f"{rng.randint(100, 540)}={_b64_rand(rng, 178)}",
         "domain": ".google.com", "path": "/",
         "expires": now + 180 * 86400,
         "httpOnly": True, "secure": True, "sameSite": "None"},
        {"name": "CONSENT",
         "value": f"YES+cb.{_yyyymmdd_utc(now - consent_age)}-"
                  f"{rng.randint(10, 19):02d}-p{rng.randint(0, 9)}."
                  f"{lang}+{region}+{rng.randint(100, 999)}",
         "domain": ".google.com", "path": "/",
         "expires": now + 395 * 86400,
         "secure": True, "sameSite": "Lax"},
        # 1P_JAR removed: Google deprecated it in 2022. Including it now is
        # an anachronism flag for fingerprinters that look at cookie freshness.
        {"name": "SOCS",
         "value": f"CAES{_b64_rand(rng, 56)}",
         "domain": ".google.com", "path": "/",
         "expires": now + 395 * 86400,
         "secure": True, "sameSite": "Lax"},
        {"name": "_GRECAPTCHA",
         "value": _b64_rand(rng, 124),
         "domain": ".google.com", "path": "/",
         "expires": now + 180 * 86400,
         "secure": True, "sameSite": "None"},
        {"name": "ENID",
         "value": _b64_rand(rng, 252),
         "domain": ".google.com", "path": "/",
         "expires": now + 395 * 86400,
         "httpOnly": True, "secure": True, "sameSite": "Lax"},
    ]


# ---------------------------------------------------------------------------
# Per-site cookie generators (recipes keyed by site["cookie_profile"])
# ---------------------------------------------------------------------------

def _norm_domain(domain: str) -> str:
    return domain if domain.startswith(".") else "." + domain


def _ga_cookie(rng: random.Random, now: int, domain: str) -> dict:
    first_age = rng.randint(7, 395) * 86400
    return {"name": "_ga",
            "value": f"GA1.2.{rng.randint(100000000, 999999999)}.{now - first_age}",
            "domain": domain, "path": "/",
            "expires": now + 395 * 86400,
            "secure": True, "sameSite": "Lax"}


def _gid_cookie(rng: random.Random, now: int, domain: str) -> dict:
    return {"name": "_gid",
            "value": f"GA1.2.{rng.randint(100000000, 999999999)}.{now - rng.randint(60, 86400)}",
            "domain": domain, "path": "/",
            "expires": now + 86400,
            "secure": True, "sameSite": "Lax"}


def _cf_bm_cookie(rng: random.Random, now: int, domain: str) -> dict:
    return {"name": "__cf_bm",
            "value": f"{_b64_rand(rng, 43)}.{rng.randint(1700000000, now)}-1-1-1-1",
            "domain": domain, "path": "/",
            "expires": now + 1800,
            "secure": True, "sameSite": "None"}


def _onetrust_cookie(rng: random.Random, now: int, domain: str) -> dict:
    age_d = rng.randint(7, 365)
    iso = datetime.datetime.utcfromtimestamp(now - age_d * 86400).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    return {"name": "OptanonAlertBoxClosed",
            "value": iso,
            "domain": domain, "path": "/",
            "expires": now + 395 * 86400,
            "secure": True, "sameSite": "Lax"}


def _cookieyes_cookie(rng: random.Random, now: int, domain: str) -> dict:
    return {"name": "cookieyes-consent",
            "value": "consentid:" + _b64_rand(rng, 28) +
                     ",consent:yes,action:yes,necessary:yes,functional:yes,analytics:yes",
            "domain": domain, "path": "/",
            "expires": now + 395 * 86400,
            "secure": True, "sameSite": "Lax"}


def _clarity_cookie(rng: random.Random, now: int, domain: str) -> dict:
    return {"name": "_clck",
            "value": f"{_hex_rand(rng, 8)}|2|f{rng.randint(10, 99)}|0|"
                     f"{now - rng.randint(60, 180) * 86400}",
            "domain": domain, "path": "/",
            "expires": now + 365 * 86400,
            "secure": True, "sameSite": "Lax"}


def _fbp_cookie(rng: random.Random, now: int, domain: str) -> dict:
    """Facebook Pixel _fbp = fb.<subdomain_index>.<unix_ms>.<random_int>"""
    return {"name": "_fbp",
            "value": f"fb.1.{(now - rng.randint(60, 30*86400)) * 1000}."
                     f"{rng.randint(100000000, 9999999999)}",
            "domain": domain, "path": "/",
            "expires": now + 90 * 86400,
            "secure": True, "sameSite": "Lax"}


def _gtm_cookie(rng: random.Random, now: int, domain: str) -> dict:
    """_dc_gtm_<container_id>=1 - Google Tag Manager throttle flag."""
    container = f"UA-{rng.randint(10000000, 99999999)}-{rng.randint(1, 9)}"
    return {"name": f"_dc_gtm_{container}",
            "value": "1",
            "domain": domain, "path": "/",
            "expires": now + 60,
            "secure": True, "sameSite": "Lax"}


def _hssrc_cookie(rng: random.Random, now: int, domain: str) -> dict:
    """HubSpot referrer flag - small int."""
    return {"name": "__hssrc",
            "value": str(rng.randint(1, 5)),
            "domain": domain, "path": "/",
            "expires": now + 1800,
            "secure": True, "sameSite": "Lax"}


def _cookies_for_profile(profile: str, rng: random.Random,
                         now: int, domain: str) -> List[dict]:
    """Map cookie_profile tag (from browsing_pool.json) → concrete cookies.

    Each recipe is a realistic combination observed on real production sites
    in that category. Cookie age and sub-recipe variance (e.g., OneTrust vs
    CookieYes for consent banner) are deterministic from rng.
    """
    domain = _norm_domain(domain)
    if profile == "minimal":
        return [_ga_cookie(rng, now, domain)]
    if profile == "ga_only":
        out = [_ga_cookie(rng, now, domain), _gid_cookie(rng, now, domain)]
        # 30% chance of GTM helper paired with GA
        if rng.random() < 0.3:
            out.append(_gtm_cookie(rng, now, domain))
        return out
    if profile == "ga_cf":
        return [_ga_cookie(rng, now, domain), _cf_bm_cookie(rng, now, domain)]
    if profile == "ga_consent":
        out = [_ga_cookie(rng, now, domain), _gid_cookie(rng, now, domain)]
        out.append(_onetrust_cookie(rng, now, domain) if rng.random() < 0.5
                   else _cookieyes_cookie(rng, now, domain))
        if rng.random() < 0.4:
            out.append(_gtm_cookie(rng, now, domain))
        return out
    if profile == "ga_consent_clarity":
        # Heavy-tracking site profile: GA + Clarity + consent + often FB pixel
        out = [_ga_cookie(rng, now, domain), _gid_cookie(rng, now, domain),
               _clarity_cookie(rng, now, domain)]
        out.append(_onetrust_cookie(rng, now, domain) if rng.random() < 0.5
                   else _cookieyes_cookie(rng, now, domain))
        if rng.random() < 0.5:
            out.append(_fbp_cookie(rng, now, domain))
        if rng.random() < 0.4:
            out.append(_gtm_cookie(rng, now, domain))
        if rng.random() < 0.25:
            out.append(_hssrc_cookie(rng, now, domain))
        return out
    # Unknown profile → safe fallback
    return [_ga_cookie(rng, now, domain)]


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------

def build_cookies(seed: int,
                  browsing_history: Optional[List[dict]] = None,
                  now: Optional[int] = None,
                  timezone: Optional[str] = None) -> List[dict]:
    """Build the full cookie list for a persona.

    Args:
        seed: persona integer seed (from `Profile.seed`)
        browsing_history: list of {name, category, cookie_profile} dicts as
            sampled by `_fpforge.derive_browsing_history`. None → empty list
            (only the 5 google cookies are returned).
        now: unix-seconds timestamp; defaults to current time. Pin for tests.
        timezone: IANA tz used to derive CONSENT cookie's `lang+region` token
            (e.g. "Europe/Rome" → "it+IT", "America/New_York" → "en+FX").
    """
    ts = now if now is not None else int(time.time())
    cookies: List[dict] = []

    # 5 .google.com cookies (always) - CONSENT lang derived from tz
    rng_g = random.Random(_sub_seed(int(seed), "google"))
    cookies.extend(_google_cookies(rng_g, ts, timezone=timezone))

    # Per-site cookies (deterministic from seed × domain)
    for site in (browsing_history or []):
        rng_d = random.Random(_sub_seed(int(seed), f"dom:{site['name']}"))
        cookies.extend(_cookies_for_profile(
            site.get("cookie_profile", "minimal"), rng_d, ts, site["name"]
        ))
    return cookies


def _extract_seed_and_history(profile: Any) -> tuple:
    """Accept a Profile object OR a (seed, history) tuple OR just an int seed."""
    if isinstance(profile, int):
        return int(profile), []
    seed = int(getattr(profile, "seed"))
    history = list(getattr(profile, "browsing_history", []) or [])
    return seed, history


async def seed_recaptcha_cookies_async(context: Any, profile: Any,
                                       timezone: Optional[str] = None) -> None:
    """Async: inject deterministic persona cookies into the context."""
    seed, history = _extract_seed_and_history(profile)
    cookies = build_cookies(seed, history, timezone=timezone)
    try:
        await context.add_cookies(cookies)
    except Exception:
        pass


def seed_recaptcha_cookies_sync(context: Any, profile: Any,
                                timezone: Optional[str] = None) -> None:
    """Sync: inject deterministic persona cookies into the context."""
    seed, history = _extract_seed_and_history(profile)
    cookies = build_cookies(seed, history, timezone=timezone)
    try:
        context.add_cookies(cookies)
    except Exception:
        pass


__all__ = [
    "build_cookies",
    "seed_recaptcha_cookies_async",
    "seed_recaptcha_cookies_sync",
]
