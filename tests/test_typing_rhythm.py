"""The keyboard has an owner of its rhythm, drawn from the session seed.

⛔ WHAT IT REPLACES. A keypress went out as two protocol messages back to back,
so a page saw `keydown` and `keyup` about 2.4 ms apart and two characters about
5 ms apart, against the 60-250 ms a hand produces. It is the cheapest tell there
is: a site does not have to probe for it, only to listen, and a password field
is where that listening is already installed.

`delay=` was never the fix. The server dropped it on six of the seven paths that
accept it, and even honoured it would be a constant the caller picked, so every
install passing `delay=100` would share one rhythm. That is the linkage key
`19-cursor-signature.md` is about, moved from the pointer to the keyboard.
"""
from __future__ import annotations

import statistics

from invisible_playwright._behaviour import (
    PointerPersona, TypingPersona, plan_typing,
)


def _gaps(text: str, persona: TypingPersona, runs: int = 60) -> list:
    out = []
    for n in range(runs):
        out.extend(g for _, g in plan_typing(text, persona, nonce=n)[:-1])
    return out


def test_the_same_seed_types_the_same_way_twice():
    """A documented property of this package: same seed, same session. It has
    to survive a new source of randomness being added next to the old one."""
    p = TypingPersona.from_seed(4242)
    assert plan_typing("hello world", p) == plan_typing("hello world", p)


def test_two_seeds_do_not_share_a_rhythm():
    """⛔ THE POINT OF THE PERSONA. A constant is worse than a wrong value: it
    is a key that links every session of every install that shares it."""
    a, b = TypingPersona.from_seed(1), TypingPersona.from_seed(2)
    assert a != b
    assert plan_typing("hello", a) != plan_typing("hello", b)


def test_adding_typing_did_not_move_the_pointer():
    """⛔ THE KNOWN-BAD INPUT OF THE SEED PLUMBING, and the reason `_sub_seed`
    takes a tag at all.

    Drawing from a shared stream would shift every number an existing seed
    produces for the pointer, which is a silent regression of a property users
    rely on. Two tagged streams are independent, and this is what proves the
    typing draw does not consume from the pointer's.
    """
    before = PointerPersona.from_seed(99)
    TypingPersona.from_seed(99)
    plan_typing("some text that consumes a lot of randomness",
                TypingPersona.from_seed(99))
    after = PointerPersona.from_seed(99)
    assert before == after


def test_a_keystroke_lasts_as_long_as_a_finger_does():
    """Not 2.4 ms, and not a second either. The band is wide on purpose: the
    claim is that the values are in the human range, not that they match one
    person."""
    p = TypingPersona.from_seed(7)
    dwells = [d for n in range(60) for d, _ in plan_typing("the quick brown fox",
                                                           p, nonce=n)]
    median = statistics.median(dwells)
    assert 30.0 < median < 260.0, "median dwell %.1f ms" % median
    assert min(dwells) > 3.0, "a dwell of %.1f ms is a protocol round trip" % min(dwells)


def test_the_gaps_have_a_spread_and_a_tail():
    """⛔ A CONSTANT RHYTHM IS STILL A TELL, which is why this asserts on the
    shape and not only on the centre. To watch it fail, set every sigma in the
    persona to zero: the median stays human and the spread goes to nothing."""
    p = TypingPersona.from_seed(11)
    gaps = _gaps("the quick brown fox jumps", p)
    median = statistics.median(gaps)
    assert 40.0 < median < 400.0, "median gap %.1f ms" % median
    spread = statistics.pstdev(gaps) / median
    assert spread > 0.15, "the gaps vary by only %.1f%% of the median" % (spread * 100)
    assert max(gaps) > median * 1.8, "no tail: the longest gap is the median"


def test_alternating_hands_is_faster_than_staying_on_one():
    """⛔ THE STRUCTURE, and the known-bad input of this file: set
    `alternate_hand_factor` equal to `same_hand_factor` and this is the only
    test that notices.

    It is the most robust finding in typing timing, and a flat interval reads
    as machine-made even when its mean is right.
    """
    p = TypingPersona.from_seed(3)
    # `jf` and `kd` alternate hands; `rt` and `we` stay on the left.
    alternating = statistics.median(_gaps("jfjfjfkdkd", p))
    same = statistics.median(_gaps("rtrtrtwewe", p))
    assert alternating < same, (
        "alternating %.1f ms was not faster than same-hand %.1f ms"
        % (alternating, same))


def test_a_repeated_key_is_the_slowest_of_all():
    """The same finger has to leave and come back, so a doubled letter costs
    more than any other digram."""
    p = TypingPersona.from_seed(5)
    repeated = statistics.median(_gaps("lllll", p))
    mixed = statistics.median(_gaps("lalala", p))
    assert repeated > mixed, (
        "a repeated key (%.1f ms) was not slower than an alternating one "
        "(%.1f ms)" % (repeated, mixed))


def test_the_last_character_owns_no_pause():
    """The wait after the final keystroke belongs to whatever happens next, not
    to typing, so planning one here would make every `fill` end in a pause
    nobody asked for."""
    p = TypingPersona.from_seed(13)
    plan = plan_typing("abc", p)
    assert len(plan) == 3
    assert plan[-1][1] == 0.0
    assert all(gap > 0.0 for _, gap in plan[:-1])


def test_an_empty_string_plans_nothing():
    assert plan_typing("", TypingPersona.from_seed(1)) == []
