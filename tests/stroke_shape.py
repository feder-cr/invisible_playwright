"""What a page can tell about a stroke from the mousemoves it received, without counting them.

How many mousemoves reach a page is not the stroke's: the pacer drops a sample rather than send it
late, so the same stroke is 15 events on an idle machine and 6 to 9 on a loaded one (measured on
Windows with 32 busy processes; the Linux gate with four workers gave 9). A test that wants "a
stroke, not a jump" asks for its SHAPE: it set out from somewhere else, it came in steps, it ended
on the target, and nowhere did two consecutive events repeat.
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

#: A stroke starts at least this far from where it ends. The departure of the test's seed is
#: about 870 px away; a jump or a stroke that "began" at the target is 0.
MIN_REACH_PX = 150.0
#: Departure, at least one step on the way, landing.
MIN_EVENTS = 3


def stroke_problems(moves: Sequence[Sequence[float]], target: Tuple[float, float]) -> List[str]:
    """What is wrong with `moves` as a stroke to `target`; an empty list is a stroke."""
    problems: List[str] = []
    if len(moves) < MIN_EVENTS:
        problems.append("only %d mousemove(s): no departure, no steps" % len(moves))
        return problems
    if list(moves[-1]) != list(target):
        problems.append("the last mousemove %r is not the target %r" % (list(moves[-1]), list(target)))
    reach = math.hypot(moves[0][0] - target[0], moves[0][1] - target[1])
    if reach < MIN_REACH_PX:
        problems.append("the first mousemove %r is only %.0f px from the target" % (list(moves[0]), reach))
    repeated = [(i, list(m)) for i, m in enumerate(moves) if i and list(m) == list(moves[i - 1])]
    if repeated:
        problems.append("the same mousemove twice in a row: %r" % repeated)
    return problems
