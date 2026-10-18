"""The kinds of weekly event a host can open, and what is special about each.

Every event ranks a top 10. An event type can add to that, and everything about the addition lives in one EventType:
  - fields:         extra /pick fields (see pick_fields.py) that members fill in for this event
  - check:          rules that span those fields, e.g. "ups plus downs can't exceed 10"
  - start_problem:  why the event can't be opened right now, if it can't
  - score_problem:  why it can't be scored yet, if it can't
  - announce:       extra text for the "predictions are open" post
  - score:          extra points (and a section for the results post) on top of the ranked scoring
Hooks are plain synchronous functions that may read storage; the bot runs them in a worker thread.

To add an event type: write its hooks, add an EventType to EVENT_TYPES below, and it appears in /event-template and
/new-event, in the announcements, the guide and the results post on its own.
"""
from dataclasses import dataclass
from typing import Callable, Optional

from discord.utils import escape_markdown as esc

import scoring
import storage
import style
from pick_fields import PickField, from_stored
from style import row


@dataclass(frozen=True)
class EventType:
    key: str                    # stored on the week, e.g. "updown"
    name: str                   # how it reads in announcements, e.g. "Ups and Downs"
    choice: str = ""            # its entry in the week_type dropdown of /event-template and /new-event
    fields: tuple = ()          # PickFields members fill in on /pick
    pick_help: str = ""         # a paragraph for the guide a bare /pick shows
    check: Optional[Callable] = None            # (values: dict) -> [error messages]
    start_problem: Optional[Callable] = None    # () -> str, '' when the event can be opened
    score_problem: Optional[Callable] = None    # (week_id) -> str, '' when it can be scored
    announce: Optional[Callable] = None         # (week_id) -> (how_text | None, [rows], [(heading, lines)])
    score: Optional[Callable] = None            # (week_id, predictions, actual) -> ([(points, rows)], [(heading, lines)])


# ---- Ups and Downs --------------------------------------------------------------------------------------------------

UPS = PickField("ups", "Ups", "how many titles will climb in the chart")
DOWNS = PickField("downs", "Downs", "how many titles will fall in the chart")


def _updown_check(values: dict) -> list:
    if values["ups"] + values["downs"] > 10:
        return [f"Ups ({values['ups']}) and downs ({values['downs']}) can't add up to more than 10, the titles on the chart."]
    return []


def _chart_problem(previous, start: bool) -> str:
    """Ups and downs are measured against the chart of the event right before, which must have results."""
    if previous is None:
        return (
            "Ups and Downs compares with last week's chart, so it can't be the first event of a season."
            if start
            else "Ups and Downs compares with last week's chart, but there's no earlier week to compare with."
        )
    if not any(storage.get_results(previous["week_id"]) or []):
        label = style.week_label(previous["week_id"])
        tail = ", then start this one." if start else "."
        return (
            f"Ups and Downs compares with {label}'s chart, but {label} has no results yet. "
            f"Enter them first with `/end-week week:{previous['week_id']}`{tail}"
        )
    return ""


def _updown_start_problem() -> str:
    weeks = storage.list_weeks()
    return _chart_problem(weeks[-1] if weeks else None, start=True)


def _updown_score_problem(week_id: str) -> str:
    return _chart_problem(storage.previous_week(week_id), start=False)


def _updown_announce(week_id: str):
    previous = storage.previous_week(week_id)
    if previous is None:
        return None, [], []
    last = style.week_label(previous["week_id"])
    chart = storage.get_results(previous["week_id"]) or []
    how = (
        "Use /pick to submit your ranked top 10 and your `ups` and `downs` guesses, "
        "e.g. /pick rank-1: Title ups: 3 downs: 4. Both guesses are needed."
    )
    return (
        how,
        [row("Ups and Downs", scoring.updown_explainer(last))],
        [(f"{last}'s chart", [row(i, esc(t) if t else "-") for i, t in enumerate(chart, start=1)])],
    )


def _movement_lines(movement: dict) -> list:
    """How the chart moved, for the results post."""
    names = lambda titles: ", ".join(esc(t) for t in titles) or "-"  # noqa: E731
    lines = [
        row("Ups", f"**{movement['actual_ups']}**" + (f" ({names(movement['ups'])})" if movement["ups"] else "")),
        row("Downs", f"**{movement['actual_downs']}**" + (f" ({names(movement['downs'])})" if movement["downs"] else "")),
    ]
    if movement["same"]:
        lines.append(row("Unchanged", names(movement["same"])))
    if movement["new"]:
        lines.append(row("New", names(movement["new"])))
    if movement["dropped"]:
        lines.append(row("Dropped off", names(movement["dropped"])))
    return lines


def _updown_score(week_id: str, predictions: list, actual: list):
    previous = storage.previous_week(week_id)
    movement = scoring.chart_movement(storage.get_results(previous["week_id"]) or [], actual)
    extras = storage.list_pick_extras(week_id)
    bonuses = []
    for p in predictions:
        stored = extras.get(p["user_id"], {})
        ups, downs = from_stored(UPS, stored.get("ups")), from_stored(DOWNS, stored.get("downs"))
        pick = (ups, downs) if ups is not None and downs is not None else None
        bonuses.append(scoring.compute_updown_score(pick, movement))
    return bonuses, [(f"Ups and Downs vs {style.week_label(previous['week_id'])}", _movement_lines(movement))]


UPDOWN = EventType(
    key="updown",
    name="Ups and Downs",
    choice="ups and downs",
    fields=(UPS, DOWNS),
    pick_help=(
        "This is an Ups and Downs week, so also fill in `ups` and `downs`: how many titles you think will climb "
        "and how many will fall compared with last week's chart, e.g. `/pick rank-1: Title ups: 3 downs: 4`. "
        "Both are needed the first time; after that you can change one on its own."
    ),
    check=_updown_check,
    start_problem=_updown_start_problem,
    score_problem=_updown_score_problem,
    announce=_updown_announce,
    score=_updown_score,
)

STANDARD = EventType(key="standard", name="standard", choice="standard")
SPECIAL = EventType(key="special", name="special", choice="special")

EVENT_TYPES = {e.key: e for e in (STANDARD, SPECIAL, UPDOWN)}


# ---- looking things up -----------------------------------------------------------------------------------------------

def get(key) -> EventType:
    """The event type for a stored week type. An unknown one (say a type from a newer or older version) behaves like
    a plain ranked week instead of breaking."""
    return EVENT_TYPES.get(str(key)) or EventType(key=str(key), name=str(key))


def type_label(key) -> str:
    return get(key).name


def all_fields() -> dict:
    """{key: PickField} for every extra field of every event type. A key can be shared, but only by the same field."""
    found = {}
    for event in EVENT_TYPES.values():
        for field in event.fields:
            if found.setdefault(field.key, field) != field:
                raise ValueError(f"two different pick fields are both called '{field.key}'")
    return found


def event_names_using(field: PickField) -> list:
    return [e.name for e in EVENT_TYPES.values() if any(f.key == field.key for f in e.fields)]
