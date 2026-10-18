"""Extra fields on /pick, beyond the ten ranks (the "ups" and "downs" of an Ups and Downs week, for example).

A field is declared once, as a PickField, and attached to the event type(s) that use it (see event_types.py). Everything
else is generic: validating what a member typed, carrying a value over when they edit something else, saving it, showing
it on their post and in the guide, refusing it in weeks that don't use it.

Adding a field:
  1. Declare it as a PickField and list it in `fields=` of the event type(s) that use it.
  2. Add a parameter with the same name to `/pick` in cogs/user.py (Discord needs every option written into the
     command). tests/test_pick_fields.py fails until the registry and the command agree.
Descriptions, suggestions and fixed choices come from the PickField, so nothing else needs touching.
"""
from dataclasses import dataclass

from textutil import normalize
from validation import resolve_title

KINDS = ("int", "title", "choice", "text")


@dataclass(frozen=True)
class PickField:
    key: str                  # the /pick option name and the key it is saved under, e.g. "ups"
    label: str                # how it reads to members, e.g. "Ups"
    help: str                 # what to put in it; shown in Discord next to the option (keep it short)
    kind: str = "int"         # "int" (whole number min..max), "title" (a title from the season list),
                              # "choice" (one of `choices`) or "text" (free text up to max_length)
    min: int = 0              # int fields only
    max: int = 10
    choices: tuple = ()       # choice fields only
    max_length: int = 100     # text fields only
    required: bool = True     # must be filled in the first time a member picks in an event that uses it

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"{self.key}: kind must be one of {KINDS}")
        if self.kind == "choice" and not self.choices:
            raise ValueError(f"{self.key}: a choice field needs choices")


# ---- reading and checking a value ---------------------------------------------------------------------------------

def check_value(field: PickField, raw, titles: list):
    """Validates one value. Returns (value, error): value is the cleaned value (an int for int fields, the list's own
    spelling for title fields) or None when there is an error."""
    if field.kind == "int":
        try:
            number = int(str(raw).strip())
        except ValueError:
            return None, f"`{field.key}` must be a whole number from {field.min} to {field.max}."
        if not field.min <= number <= field.max:
            return None, f"`{field.key}` must be from {field.min} to {field.max}, not {number}."
        return number, None
    text = str(raw).strip()
    if not text:
        return None, f"`{field.key}` can't be empty."
    if field.kind == "title":
        found = resolve_title(text, {normalize(t): t for t in titles})
        if found is None:
            return None, f"`{field.key}` ('{text}') isn't on this season's anime list."
        return found, None
    if field.kind == "choice":
        by_norm = {normalize(c): c for c in field.choices}
        found = by_norm.get(normalize(text))
        if found is None:
            return None, f"`{field.key}` must be one of: {', '.join(field.choices)}."
        return found, None
    if len(text) > field.max_length:
        return None, f"`{field.key}` is too long ({len(text)} characters, the limit is {field.max_length})."
    return text, None


def from_stored(field: PickField, stored: str):
    """A saved value (everything is stored as text) back as its real type."""
    if field.kind == "int":
        try:
            return int(stored)
        except (TypeError, ValueError):
            return None
    return stored


def typed(fields, stored: dict) -> dict:
    """{key: value} for the given fields from a {key: text} row; unknown or unreadable entries are dropped."""
    result = {}
    for field in fields:
        if field.key in stored:
            value = from_stored(field, stored[field.key])
            if value is not None:
                result[field.key] = value
    return result


def to_stored(values: dict) -> dict:
    return {key: str(value) for key, value in values.items()}


# ---- the whole check for one /pick ---------------------------------------------------------------------------------

def _listed(keys: list) -> str:
    quoted = [f"`{k}`" for k in keys]
    return quoted[0] if len(quoted) == 1 else " and ".join(quoted) if len(quoted) == 2 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def _names(keys: list) -> str:
    """'`a` is', '`a` and `b` are both', '`a`, `b` and `c` are all' (for 'needed')."""
    if len(keys) == 1:
        return _listed(keys) + " is"
    return f"{_listed(keys)} are {'both' if len(keys) == 2 else 'all'}"


def resolve(event, given: dict, current: dict, titles: list):
    """Works out a member's extra fields for this event.

    given: {key: raw value} for the fields they filled in just now (any event's fields).
    current: {key: value} already saved for this week (typed), kept for fields they didn't touch.
    Returns (errors, values): any error means the whole /pick is refused; values is {key: value} for this event.
    """
    errors, values = [], {}
    mine = {f.key: f for f in event.fields}

    foreign = [key for key in given if key not in mine]
    if foreign:
        errors.append(
            f"{_listed(foreign)} {'doesn' if len(foreign) == 1 else 'don'}'t apply in a {event.name} week. "
            "Run `/pick` again without " + ("it." if len(foreign) == 1 else "them.")
        )

    missing = []
    for field in event.fields:
        if field.key in given:
            value, error = check_value(field, given[field.key], titles)
            if error:
                errors.append(error)
            else:
                values[field.key] = value
        elif field.key in current:
            values[field.key] = current[field.key]
        elif field.required:
            missing.append(field.key)
    if missing:
        errors.append(f"This is a {event.name} week, so {_names(missing)} needed.")

    if not errors and event.check is not None:
        errors.extend(event.check(values))
    return errors, values


def collect(local_vars: dict, all_fields: dict) -> dict:
    """The extra fields a /pick call filled in, picked out of its local variables by name."""
    return {key: local_vars[key] for key in all_fields if local_vars.get(key) is not None}


def describe(field: PickField, event_names: list) -> str:
    """The text Discord shows next to the option (100 characters at most)."""
    where = " / ".join(event_names) + " weeks only: " if event_names else ""
    extent = f" ({field.min}-{field.max})" if field.kind == "int" else ""
    text = f"{where}{field.help}{extent}"
    return text if len(text) <= 100 else text[:97] + "..."
