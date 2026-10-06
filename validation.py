import difflib
import re

from textutil import normalize, strip_numbering


def parse_lines(raw: str) -> list:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _hint(text: str, valid_titles: list) -> str:
    close = difflib.get_close_matches(text, valid_titles, n=1, cutoff=0.6)
    return f" Did you mean '{close[0]}'?" if close else ""


def resolve_title(line: str, valid_by_norm: dict):
    """Matches a typed line to a title on the list: as typed first (so a title that really
    starts with a number still works), then with '1.' / '1)' list numbering removed."""
    for candidate in (line, strip_numbering(line)):
        found = valid_by_norm.get(normalize(candidate))
        if found:
            return found
    return None


def validate_slots(entries: list, valid_titles: list, first_pos: int = 1, noun: str = "Rank"):
    """Validates the text boxes of a ranked form. A box may be left empty, but anything typed must
    be a title on the season list and must not repeat another box.

    Returns (errors, canonical): canonical has one entry per box, in the list's own spelling,
    with '' for empty boxes. Any error means the whole submission is refused.
    """
    valid_by_norm = {normalize(t): t for t in valid_titles}
    errors, canonical, seen = [], [], {}
    for offset, raw in enumerate(entries):
        pos = first_pos + offset
        text = raw.strip()
        if not text:
            canonical.append("")
            continue
        found = resolve_title(text, valid_by_norm)
        if found is None:
            shown = strip_numbering(text) or text
            errors.append(f"{noun} #{pos} ('{shown}') isn't on this season's anime list.{_hint(shown, valid_titles)}")
            canonical.append(shown)
            continue
        n = normalize(found)
        if n in seen:
            errors.append(f"{noun} #{pos} ('{found}') repeats {noun.lower()} #{seen[n]}.")
        else:
            seen[n] = pos
        canonical.append(found)
    return errors, canonical


CLEAR = "(leave empty)"


def is_clear(text: str) -> bool:
    """True for the 'empty this rank' choice that every rank field suggests (a lone '-' works too)."""
    return normalize(text) in (normalize(CLEAR), "-")


def merge_rank_edits(base: list, given: dict, valid_titles: list, noun: str = "Rank"):
    """Applies a quick edit to a member's existing ten ranks.

    `base` is what they have now (ten entries, '' for empty); `given` maps a position 1-10 to the text
    they typed for it (CLEAR empties that rank). Ranks they didn't touch keep their title, except that
    a title they just placed somewhere else moves, leaving its old rank empty.

    Returns (errors, canonical, notes): any error means the whole edit is refused, notes describe moves.
    """
    entries = [""] * 10
    for pos, text in given.items():
        entries[pos - 1] = "" if is_clear(text) else text
    errors, explicit = validate_slots(entries, valid_titles, 1, noun)
    placed = {normalize(t): pos for pos, t in enumerate(explicit, start=1) if t and pos in given}

    canonical, notes = [], []
    for pos in range(1, 11):
        if pos in given:
            canonical.append(explicit[pos - 1])
            continue
        title = base[pos - 1] if pos <= len(base) else ""
        new_pos = placed.get(normalize(title)) if title else None
        if new_pos is not None:
            notes.append(f"'{title}' moved from {noun.lower()} #{pos} to #{new_pos}, so #{pos} is now empty.")
            title = ""
        canonical.append(title)
    return errors, canonical, notes


def parse_category_pairs(raw: str):
    """Parses 'Category: guess' lines. Returns (pairs, errors) where pairs is a list of
    (category, guess). A line with nothing after the colon is skipped (category left blank)."""
    pairs, errors = [], []
    for line in parse_lines(raw):
        if ":" not in line:
            errors.append(f"'{line}' needs a colon, e.g. 'Category: Guess'.")
            continue
        category, guess = (part.strip() for part in line.split(":", 1))
        if not category:
            errors.append(f"'{line}' is missing a category name before the colon.")
            continue
        if guess:
            pairs.append((category, guess))
    return pairs, errors


def validate_ballot_pick(pairs: list, valid_categories: list):
    """Checks category names against the event's categories. Returns (ok, errors, canonical)
    where canonical maps each event category name to the guess. Any error disqualifies the
    whole submission, same policy as validate_pick."""
    errors = []
    valid_by_norm = {normalize(c): c for c in valid_categories}
    seen = set()
    canonical = {}

    for raw_category, guess in pairs:
        n = normalize(raw_category)
        name = valid_by_norm.get(n)
        if name is None:
            errors.append(f"'{raw_category}' isn't a category for this event.{_hint(raw_category, valid_categories)}")
            continue
        if n in seen:
            errors.append(f"'{name}' is listed more than once.")
            continue
        seen.add(n)
        canonical[name] = guess

    return (len(errors) == 0), errors, canonical


def parse_category_definitions(raw: str):
    """Parses 'Category | Points' lines for /new-ballot. Returns (categories, errors)."""
    categories, errors, seen = [], [], set()
    for line in parse_lines(raw):
        if "|" not in line:
            errors.append(f"'{line}' needs a '|', e.g. 'Anime | 10'.")
            continue
        name, points = (part.strip() for part in line.split("|", 1))
        if not name:
            errors.append(f"'{line}' is missing a category name.")
            continue
        if not re.fullmatch(r"-?\d{1,4}", points):
            errors.append(f"'{line}': points must be a whole number.")
            continue
        n = normalize(name)
        if n in seen:
            errors.append(f"'{name}' is listed more than once.")
            continue
        seen.add(n)
        categories.append((name, int(points)))
    return categories, errors
