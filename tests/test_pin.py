"""Pin parameter validation and propagation through the fingerprint generator."""
import pytest

from invisible_core._fpforge import generate_profile
from invisible_core.prefs import translate_profile_to_prefs


def test_pin_screen_width_propagates_to_prefs():
    p = generate_profile(seed=42, pin={"screen.width": 2560, "screen.height": 1440})
    prefs = translate_profile_to_prefs(p)
    assert prefs["zoom.stealth.screen.width"] == 2560
    assert prefs["zoom.stealth.screen.height"] == 1440


def test_pin_gpu_renderer_propagates():
    target = "ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 Direct3D11)"
    p = generate_profile(seed=42, pin={"gpu.renderer": target})
    # The Profile carries the pinned value regardless of platform; the prefs
    # translation may suppress it on Windows for hash-coherence reasons.
    assert p.gpu.renderer == target


def test_pin_hardware_concurrency_propagates():
    p = generate_profile(seed=42, pin={"hardware.concurrency": 16})
    assert p.hardware.concurrency == 16


def test_pin_audio_sample_rate_propagates():
    p = generate_profile(seed=42, pin={"audio.sample_rate": 48000})
    assert p.audio.sample_rate == 48000


def test_pin_unknown_key_raises():
    with pytest.raises(ValueError, match="not valid|unknown"):
        generate_profile(seed=42, pin={"nonexistent.field": 123})


def test_pin_unknown_group_raises():
    with pytest.raises(ValueError, match="unknown group"):
        generate_profile(seed=42, pin={"madeup.field": "x"})


def test_pin_unknown_field_in_known_group_raises():
    with pytest.raises(ValueError, match="unknown field"):
        generate_profile(seed=42, pin={"screen.not_a_real_field": 100})


def test_pin_key_without_dot_raises():
    """Top-level keys must be in the allowlist; arbitrary flat keys reject."""
    with pytest.raises(ValueError, match="not valid"):
        generate_profile(seed=42, pin={"madeup": 1})


def test_pin_top_level_dark_theme_accepted():
    p = generate_profile(seed=42, pin={"dark_theme": True})
    assert p.dark_theme is True


def test_pin_overrides_seed_value():
    """The same seed produces different output once a pin is applied."""
    natural = generate_profile(seed=42)
    pinned = generate_profile(seed=42, pin={"screen.width": natural.screen.width + 100})
    assert pinned.screen.width == natural.screen.width + 100
    assert pinned.screen.width != natural.screen.width


def test_pin_reproducibility_within_same_seed():
    a = generate_profile(seed=42, pin={"screen.width": 1920, "audio.sample_rate": 48000})
    b = generate_profile(seed=42, pin={"screen.width": 1920, "audio.sample_rate": 48000})
    assert a.screen.width == b.screen.width
    assert a.audio.sample_rate == b.audio.sample_rate
    assert a.gpu.renderer == b.gpu.renderer
