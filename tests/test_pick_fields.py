"""Extra /pick fields and event types are declared once and handled generically (pick_fields.py, event_types.py).

These tests are the safety net for adding the next field: they check the declarations and the /pick command agree,
that every kind of field validates and saves, and that a brand-new event type with new fields works end to end
without any change to /pick's logic, storage, scoring flow or announcements.
"""
import asyncio
import re

import pytest
from discord import app_commands

import event_types
import pick_fields
import storage
from conftest import RESULTS, TITLES
from event_types import EventType
from pick_fields import PickField
from style import row
from textutil import normalize


def run(coro):
    return asyncio.run(coro)


# ---- the declarations and the /pick command must agree ------------------------------------------------------------------

def pick_payload():
    from bot import PredictionBot

    async def build():
        bot = PredictionBot()
        for ext in ["cogs.season", "cogs.weeks", "cogs.user", "cogs.help", "cogs.backups"]:
            await bot.load_extension(ext)
        payload = bot.tree.get_command("pick").to_dict(bot.tree)
        from discord.ext import commands
        await commands.Bot.close(bot)
        return payload
    return run(build())


def test_every_declared_field_is_a_pick_option_and_every_extra_option_is_declared():
    """If this fails after adding a PickField: add a parameter with the same name to /pick in cogs/user.py (or the
    other way round: declare the field in event_types.py)."""
    options = {o["name"]: o for o in pick_payload()["options"]}
    extras = {name for name in options if not re.fullmatch(r"rank-\d+", name)}
    declared = event_types.all_fields()
    assert extras == set(declared), f"/pick options {sorted(extras)} don't match declared fields {sorted(declared)}"

    for key, field in declared.items():
        option = options[key]
        assert not option.get("required"), f"{key} must be optional: it only applies in some events"
        assert 1 <= len(option["description"]) <= 100 and option["description"].endswith(
            f"({field.min}-{field.max})" if field.kind == "int" else option["description"][-1:])
        if field.kind == "int":
            assert option["type"] == 4 and (option["min_value"], option["max_value"]) == (field.min, field.max)
        elif field.kind == "choice":
            assert [c["value"] for c in option["choices"]] == list(field.choices)
        else:
            assert option["type"] == 3
        if field.kind == "title":
            assert option.get("autocomplete"), f"{key} should suggest titles as the member types"
        for event_name in event_types.event_names_using(field):
            assert event_name in option["description"]


def test_the_command_stays_within_discords_limits_with_room_to_grow():
    options = pick_payload()["options"]
    assert len(options) <= 25, "Discord allows at most 25 options on one command"
    assert len(options) <= 20, "getting close to Discord's 25-option limit: think before adding more fields"
    assert all(len(o["name"]) <= 32 for o in options)


def test_declared_fields_are_well_formed():
    declared = event_types.all_fields()
    for key, field in declared.items():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", key), f"'{key}' isn't a valid option name"
        assert not re.fullmatch(r"rank_\d+", key), f"'{key}' would clash with a rank field"
        assert field.label and field.help
    assert len({e.choice for e in event_types.EVENT_TYPES.values() if e.choice}) == len(
        [e for e in event_types.EVENT_TYPES.values() if e.choice]), "two events share a dropdown name"
    # every event type is offered to hosts in /event-template and /new-event
    from cogs.weeks import WEEK_TYPE_CHOICES
    assert [c.value for c in WEEK_TYPE_CHOICES] == [e.key for e in event_types.EVENT_TYPES.values() if e.choice]
    assert all(len(c.name) <= 100 for c in WEEK_TYPE_CHOICES)


def test_two_different_fields_with_one_name_are_refused(monkeypatch):
    clash = EventType(key="clash", name="Clash", fields=(PickField("ups", "Ups", "something else"),))
    monkeypatch.setitem(event_types.EVENT_TYPES, "clash", clash)
    with pytest.raises(ValueError, match="both called 'ups'"):
        event_types.all_fields()


# ---- the kinds of field -------------------------------------------------------------------------------------------------

def test_every_kind_of_field_validates_what_a_member_types():
    number = PickField("n", "N", "a number", min=2, max=5)
    assert pick_fields.check_value(number, " 3 ", TITLES) == (3, None)
    assert pick_fields.check_value(number, 5, TITLES) == (5, None)
    for bad in ("1", "6", "x", "2.5", ""):
        value, error = pick_fields.check_value(number, bad, TITLES)
        assert value is None and "`n`" in error, bad

    title = PickField("t", "T", "a title", kind="title")
    assert pick_fields.check_value(title, "one piece", TITLES) == ("One Piece", None)
    assert pick_fields.check_value(title, "86", TITLES) == ("86", None)
    value, error = pick_fields.check_value(title, "Not A Show", TITLES)
    assert value is None and "isn't on this season's anime list" in error

    choice = PickField("c", "C", "pick one", kind="choice", choices=("Hype", "Chill"))
    assert pick_fields.check_value(choice, "hype", TITLES) == ("Hype", None)
    value, error = pick_fields.check_value(choice, "sad", TITLES)
    assert value is None and "Hype, Chill" in error

    text = PickField("x", "X", "a note", kind="text", max_length=5)
    assert pick_fields.check_value(text, "  hey ", TITLES) == ("hey", None)
    assert "too long" in pick_fields.check_value(text, "toolong", TITLES)[1]
    assert "can't be empty" in pick_fields.check_value(text, "   ", TITLES)[1]
    with pytest.raises(ValueError):
        PickField("bad", "Bad", "x", kind="choice")                  # a choice field with no choices
    with pytest.raises(ValueError):
        PickField("bad", "Bad", "x", kind="wat")


def test_values_survive_being_stored_as_text_and_read_back():
    fields = (PickField("n", "N", "a number"), PickField("t", "T", "a title", kind="title"))
    stored = pick_fields.to_stored({"n": 7, "t": "One Piece"})
    assert stored == {"n": "7", "t": "One Piece"}
    assert pick_fields.typed(fields, stored) == {"n": 7, "t": "One Piece"}
    assert pick_fields.typed(fields, {"n": "garbage", "other": "1"}) == {}, "unreadable or unknown entries are dropped"


def test_resolve_needs_required_fields_once_keeps_untouched_ones_and_refuses_foreign_ones():
    ups, downs = event_types.UPS, event_types.DOWNS
    event = event_types.UPDOWN
    assert pick_fields.resolve(event, {"ups": 2, "downs": 3}, {}, TITLES) == ([], {"ups": 2, "downs": 3})
    errors, _ = pick_fields.resolve(event, {"ups": 2}, {}, TITLES)
    assert errors == ["This is a Ups and Downs week, so `downs` is needed."]
    errors, _ = pick_fields.resolve(event, {}, {}, TITLES)
    assert errors == ["This is a Ups and Downs week, so `ups` and `downs` are both needed."]
    # a later edit of one field keeps the other
    assert pick_fields.resolve(event, {"downs": 1}, {"ups": 2, "downs": 3}, TITLES) == ([], {"ups": 2, "downs": 1})
    # the event's own cross-field rule
    errors, _ = pick_fields.resolve(event, {"ups": 9, "downs": 9}, {}, TITLES)
    assert "can't add up to more than 10" in errors[0]
    # a field from another event
    errors, _ = pick_fields.resolve(event_types.STANDARD, {"ups": 2, "downs": 3}, {}, TITLES)
    assert errors == ["`ups` and `downs` don't apply in a standard week. Run `/pick` again without them."]
    errors, _ = pick_fields.resolve(event_types.STANDARD, {"ups": 2}, {}, TITLES)
    assert errors == ["`ups` doesn't apply in a standard week. Run `/pick` again without it."]
    # a bad value is refused and says which
    errors, _ = pick_fields.resolve(event, {"ups": 11, "downs": 1}, {}, TITLES)
    assert errors == ["`ups` must be from 0 to 10, not 11."]


def test_unknown_event_types_behave_like_plain_weeks():
    mystery = event_types.get("mystery")
    assert mystery.name == "mystery" and mystery.fields == () and mystery.score is None
    assert event_types.type_label("mystery") == "mystery" and event_types.type_label("updown") == "Ups and Downs"
    assert pick_fields.resolve(mystery, {}, {}, TITLES) == ([], {})


# ---- a brand-new event type plugs in with nothing else changed ---------------------------------------------------------------

HOT_TAKE = PickField("hot_take", "Hot take", "the title that will take the top spot", kind="title", required=False)
MOOD = PickField("mood", "Mood", "how you feel about this week", kind="choice", choices=("hype", "chill"))
WHY = PickField("why", "Why", "one line on why", kind="text", max_length=20, required=False)


def _bonus_score(week_id, predictions, actual):
    extras = storage.list_pick_extras(week_id)
    results, hits = [], 0
    for p in predictions:
        take = extras.get(p["user_id"], {}).get("hot_take", "")
        hit = bool(take) and normalize(take) == normalize(actual[0])
        hits += hit
        results.append((3 if hit else 0, [{"position": "Hot take", "predicted": take or "-", "points": 3 if hit else 0,
                                           "reason": "called the #1" if hit else "missed"}]))
    return results, [("Bonus Round", [row("Hot takes that hit", hits)])]


BONUS = EventType(
    key="bonus", name="Bonus Round", choice="bonus round", fields=(HOT_TAKE, MOOD, WHY),
    pick_help="Add a hot take and your mood.",
    check=lambda v: ["No hype takes on Sundays."] if v.get("mood") == "hype" and v.get("why") == "sunday" else [],
    start_problem=lambda: "",
    announce=lambda week_id: ("Bonus round! Call the #1 as your hot take.", [row("Bonus", "3 pts for calling the #1")], []),
    score=_bonus_score,
)


def test_a_new_event_type_with_new_fields_works_end_to_end(env, monkeypatch):
    monkeypatch.setitem(event_types.EVENT_TYPES, "bonus", BONUS)

    async def scenario():
        from cogs import user
        await env.setup_channels()
        await env.start_season(TITLES)

        # opening it: the generic start flow uses the event's name and its announce hook
        i = env.inter(env.admin)
        await env.weeks._start_week(i, "bonus", "", 2, 1)
        assert "Started **Week 1** (Bonus Round)" in i.all_text and storage.get_week("week-1")["type"] == "bonus"
        post = env.announce.sent[-1][1].description
        assert "Type :: Bonus Round" in post and "Bonus round! Call the #1" in post and "Bonus :: 3 pts for calling the #1" in post

        # the guide lists the new fields and their help text
        g = env.inter(env.alice)
        await env.user_cog.pick.callback(env.user_cog, g)
        guide = g.sent[-1].embed.description
        assert "Your Bonus Round picks" in guide and "Hot take :: -" in guide and "Mood :: -" in guide
        assert "Add a hot take and your mood." in guide
        h = env.inter(env.alice)
        await env.help.help_cmd.callback(env.help, h)
        assert "Bonus Round" in h.all_text and "`hot_take`, `mood`, `why`" in h.all_text

        # checking what a member typed: titles, choices, text, required and cross-field rules
        titles = TITLES
        assert pick_fields.resolve(BONUS, {"hot_take": "one piece", "mood": "HYPE"}, {}, titles) == (
            [], {"hot_take": "One Piece", "mood": "hype"})
        assert "`mood` is needed" in pick_fields.resolve(BONUS, {"hot_take": "One Piece"}, {}, titles)[0][0]
        assert any("isn't on this season" in e for e in pick_fields.resolve(BONUS, {"hot_take": "Zzz", "mood": "chill"}, {}, titles)[0])
        assert pick_fields.resolve(BONUS, {"mood": "sad"}, {}, titles)[0][0].startswith("`mood` must be one of")
        assert pick_fields.resolve(BONUS, {"mood": "hype", "why": "sunday"}, {}, titles)[0] == ["No hype takes on Sundays."]

        # saving: the member's post shows a section for the event, and the values are stored and read back typed
        for user_obj, take in ((env.alice, "One Piece"), (env.bob, "Bleach")):
            j = env.inter(user_obj)
            extras = {"hot_take": take, "mood": "hype"}
            await user._finish_picks(j, "week-1", RESULTS, "", extras, BONUS)
            assert "Saved" in j.all_text
        mine = storage.get_pick_extras("week-1", str(env.alice.id))
        assert pick_fields.typed(BONUS.fields, mine) == {"hot_take": "One Piece", "mood": "hype"}
        embed = env.picks.sent[0][2].embed.description
        assert "### Bonus Round" in embed and "Hot take :: One Piece" in embed and "Mood :: hype" in embed

        g = env.inter(env.alice)
        await env.user_cog.pick.callback(env.user_cog, g)
        assert "Hot take :: One Piece" in g.sent[-1].embed.description

        # scoring: the event's own hook adds points and a section to the results post
        await env.weeks.lock.callback(env.weeks, env.inter(env.admin))
        k = env.inter(env.admin)
        await env.weeks.end_week.callback(env.weeks, k, "")
        await env.submit(k, "\n".join(RESULTS))
        scores = {s["username"]: s for s in storage.list_scores("week-1")}
        assert storage.score_points(scores["alice"]) == 20 + 3 and storage.score_points(scores["bob"]) == 20
        results = next(e.description for _, e, _ in reversed(env.announce.sent) if e and e.description.startswith("# Results are in"))
        assert "Results are in: Week 1 (Bonus Round)" in results and "### Bonus Round" in results and "Hot takes that hit :: 1" in results
        m = env.inter(env.alice)
        await env.user_cog.my_score.callback(env.user_cog, m, "")
        assert "Hot take :: One Piece" in m.all_text and "(called the #1)" in m.all_text
    run(scenario())


def test_extra_field_options_get_descriptions_suggestions_and_choices_from_the_declarations(monkeypatch):
    monkeypatch.setitem(event_types.EVENT_TYPES, "bonus", BONUS)

    async def scenario():
        from bot import PredictionBot
        from common import pick_field_options
        from discord.ext import commands

        @pick_field_options
        async def demo(interaction, hot_take: str = None, mood: str = None, why: str = None, ups: int = None, downs: int = None):
            pass

        bot = PredictionBot()
        command = app_commands.Command(name="demo", description="demo", callback=demo)
        options = {o["name"]: o for o in command.to_dict(bot.tree)["options"]}
        assert options["hot_take"]["autocomplete"] and "Bonus Round weeks only: the title that will take the top spot" in options["hot_take"]["description"]
        assert [c["value"] for c in options["mood"]["choices"]] == ["hype", "chill"]
        assert not options["why"].get("autocomplete") and "choices" not in options["why"]
        assert options["ups"]["description"] == "Ups and Downs weeks only: how many titles will climb in the chart (0-10)"
        assert all(len(o["description"]) <= 100 for o in options.values())
        await commands.Bot.close(bot)
    run(scenario())
