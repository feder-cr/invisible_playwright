"""stroke_problems: what a page can say about a stroke without counting its events.

The e2e that asked for more than 10 mousemoves failed on the Linux gate with 9 events from a
loaded runner while the stroke was a real one (it left from far away, came in steps, ended on the
target). This file holds that page's events, as data, and the cases the shape rule must still
refuse.
"""
from __future__ import annotations

import pytest

from stroke_shape import stroke_problems

pytestmark = pytest.mark.unit

TARGET = (400, 240)
#: What the page of the failed Linux job received (job 129606776422, seed 4251).
FROM_THE_GATE = [[1116, 731], [1049, 689], [1007, 661], [827, 541], [747, 486],
                 [550, 347], [383, 225], [385, 227], [400, 240]]


def the_old_rule(moves):
    """The criterion of the test before: more than 10 events."""
    return len(moves) > 10


def test_the_stroke_the_gate_received_is_a_stroke_and_the_old_rule_refused_it():
    assert not the_old_rule(FROM_THE_GATE)
    assert stroke_problems(FROM_THE_GATE, TARGET) == []


def test_a_stroke_with_more_events_is_one_too():
    long_one = [[1116 - 40 * i, 731 - 28 * i] for i in range(17)] + [[400, 240]]
    assert stroke_problems(long_one, TARGET) == []


@pytest.mark.parametrize("moves, what", [
    ([[400, 240]], "a jump: one event on the target"),
    ([[1116, 731], [400, 240]], "two events: no step on the way"),
    ([[1116, 731], [900, 600], [500, 300]], "ends beside the target"),
    ([[410, 250], [405, 245], [400, 240]], "began at the target"),
    ([[1116, 731], [900, 600], [900, 600], [400, 240]], "a repeated mousemove"),
    ([], "nothing"),
])
def test_what_is_not_a_stroke_is_refused(moves, what):
    assert stroke_problems(moves, TARGET), what
