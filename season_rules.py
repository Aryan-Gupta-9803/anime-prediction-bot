"""Seasonal scoring rules: a host picks one with /start and it applies to every week of that season.

To add a rule, write a function `score(predictions, actual, rules) -> (results, notes)` (see
scoring.compute_minority_scores), add a SeasonRule for it to RULES, and it shows up in /start,
/help and the announcements by itself. `predictions` is one list of ten titles per player; `results`
is one (total, breakdown) per player, in order; `notes` is [(label, text)] shown with the results.
"""
from dataclasses import dataclass
from typing import Callable

import scoring

STANDARD_KEY = "standard"


@dataclass(frozen=True)
class SeasonRule:
    key: str
    name: str            # shown in /start's choices and in announcements
    explain: str         # what members read; keep it under ~500 characters
    score: Callable


def _score_standard(predictions: list, actual: list, rules: dict):
    return [scoring.compute_score(p, actual, rules) for p in predictions], []


STANDARD = SeasonRule(
    key=STANDARD_KEY,
    name="Standard (no extra rule)",
    explain="Normal scoring: points for an exact spot, fewer for a title on the chart in the wrong spot.",
    score=_score_standard,
)

MINORITY = SeasonRule(
    key="minority-multiplier",
    name="Minority Multiplier",
    explain=(
        "For the real top 3, each title earns a bonus multiplier based on how few players had it in their ten. "
        f"If {round(scoring.CROWD_SHARE * 100)}% or more of the players had it, it's 1x. If only one player "
        f"had it, it's {scoring.MAX_MULTIPLIER:g}x. In between it slides in steps of 0.5x. "
        "It multiplies the points you earn on that title, exact spot or not, so scores can have half points. "
        f"Weeks with fewer than {scoring.MIN_PLAYERS} players have no multipliers."
    ),
    score=scoring.compute_minority_scores,
)

RULES = {r.key: r for r in (STANDARD, MINORITY)}


def get_rule(key) -> SeasonRule:
    """The rule for a stored key; a blank or unknown key (e.g. a rule removed later) means standard."""
    return RULES.get(str(key or ""), STANDARD)


def is_standard(rule: SeasonRule) -> bool:
    return rule.key == STANDARD_KEY
