from textutil import normalize, num


def compute_score(predicted: list, actual: list, rules: dict):
    """rules: {position(1-10): (exact_points, partial_points)}

    Exact points are awarded when the predicted anime matches the actual anime at that same
    position. Partial points are awarded when it is anywhere else in the actual top 10.
    This is the same arithmetic as the original workbook's formula, which added one amount
    for an exact match and another for being on the chart at all.
    """
    actual_norm = [normalize(a) for a in actual]
    breakdown = []
    total = 0
    for pos in range(1, 11):
        pred = predicted[pos - 1] if pos - 1 < len(predicted) else ""
        pred_norm = normalize(pred)
        exact_points, partial_points = rules.get(pos, (0, 0))

        if not pred_norm:
            points, reason = 0, "no prediction"
        elif pos - 1 < len(actual_norm) and pred_norm == actual_norm[pos - 1]:
            points, reason = exact_points, "exact"
        elif pred_norm in actual_norm:
            points, reason = partial_points, "wrong spot"
        else:
            points, reason = 0, "not in top 10"

        total += points
        breakdown.append({"position": pos, "predicted": pred, "points": points, "reason": reason})

    return total, breakdown


# ---- Ups and Downs ----------------------------------------------------------------------------------------
# Besides the ranked top 10, members guess how many titles will climb (Ups) and how many will fall (Downs)
# compared with the previous week's chart. Each guess earns UPDOWN_MAX points when exact and one point less
# for every step away (off by 1 = 4, by 2 = 3, ... by 5 or more = 0).
UPDOWN_MAX = 5
# A title that wasn't on last week's chart (a new entry), or that fell off it, has no rank to compare. By default
# neither counts as an up or a down; flip these to count new entries as ups and drop-offs as downs.
COUNT_NEW_AS_UPS = False
COUNT_DROPPED_AS_DOWNS = False


def chart_movement(previous: list, current: list) -> dict:
    """How this week's chart moved against last week's: the titles that went up, went down, stayed put, are new, or
    dropped off, plus the resulting counts (actual_ups / actual_downs) that guesses are scored against."""
    before = {normalize(t): i for i, t in enumerate(previous) if t}
    now = {normalize(t) for t in current if t}
    moved = {"ups": [], "downs": [], "same": [], "new": [], "dropped": []}
    for i, title in enumerate(current):
        if not title:
            continue
        was = before.get(normalize(title))
        if was is None:
            moved["new"].append(title)
        elif i < was:
            moved["ups"].append(title)
        elif i > was:
            moved["downs"].append(title)
        else:
            moved["same"].append(title)
    moved["dropped"] = [t for t in previous if t and normalize(t) not in now]
    moved["actual_ups"] = len(moved["ups"]) + (len(moved["new"]) if COUNT_NEW_AS_UPS else 0)
    moved["actual_downs"] = len(moved["downs"]) + (len(moved["dropped"]) if COUNT_DROPPED_AS_DOWNS else 0)
    return moved


def updown_explainer(previous_label: str) -> str:
    """The rules of an Ups and Downs week in plain words, for the announcement."""
    steps = ", ".join(f"off by {n} is {UPDOWN_MAX - n}" for n in range(1, UPDOWN_MAX - 1))
    counted = [
        "a new entry counts as an up" if COUNT_NEW_AS_UPS else "new entries don't count",
        "a title that drops off the chart counts as a down" if COUNT_DROPPED_AS_DOWNS else "titles that drop off don't count",
    ]
    return (
        f"Also guess how many titles on the chart will climb (ups) and how many will fall (downs) compared with "
        f"{previous_label}. An exact guess earns {UPDOWN_MAX} pts, {steps}, and so on down to 0, separately for ups "
        f"and downs. A title only counts as up or down if it was on {previous_label}'s chart too ({counted[0]}; {counted[1]})."
    )


def updown_points(guess: int, actual: int) -> int:
    return max(0, UPDOWN_MAX - abs(int(guess) - int(actual)))


def compute_updown_score(pick, movement: dict):
    """pick: (ups, downs) a member guessed, or None. Returns (points, [two breakdown rows])."""
    rows, total = [], 0
    for index, (label, actual) in enumerate((("Ups", movement["actual_ups"]), ("Downs", movement["actual_downs"]))):
        if pick is None:
            points, shown, reason = 0, "-", "no prediction"
        else:
            guess = int(pick[index])
            points = updown_points(guess, actual)
            shown = f"{guess} (actual {actual})"
            reason = "exact" if guess == actual else f"off by {abs(guess - actual)}"
        total += points
        rows.append({"position": label, "predicted": shown, "points": points, "reason": reason})
    return total, rows


# ---- Minority Multiplier ---------------------------------------------------------------------------------
# A player's own top BONUS_RANKS picks can earn a bonus multiplier from how few players had that same title in
# their own top BONUS_RANKS: MAX_MULTIPLIER when only that one player did, 1x when CROWD_SHARE or more of the
# players did, and a straight line (in share of players) between, rounded to the nearest 0.5 so the numbers
# stay easy to follow.
MAX_MULTIPLIER = 3.0
CROWD_SHARE = 0.40
BONUS_RANKS = 3
MIN_PLAYERS = 5          # with fewer players nothing can be called a minority, so everything stays 1x


def minority_multiplier(picked: int, players: int) -> float:
    """Multiplier (1.0 to MAX_MULTIPLIER, always a multiple of 0.5) for a title that `picked` of `players`
    players had in their top picks. One player alone gets the maximum; CROWD_SHARE of the players or more
    gets 1x. For 10 players that is 1 -> 3x, 2 -> 2.5x, 3 -> 1.5x, 4 or more -> 1x."""
    if players <= 0:
        return 1.0
    if picked <= 1:
        return MAX_MULTIPLIER
    share = picked / players
    if share >= CROWD_SHARE:
        return 1.0
    rarity = (CROWD_SHARE - share) / (CROWD_SHARE - 1 / players)
    return int((1 + (MAX_MULTIPLIER - 1) * rarity) * 2 + 0.5) / 2


def compute_minority_scores(predictions: list, actual: list, rules: dict):
    """Scores every player's ten with compute_score, then multiplies the points a player earns on their
    own rank 1-3 picks (exact spot or wrong spot) by that title's multiplier. The multiplier comes from how
    many players had the same title in their own top 3. Penalties (negative points), zero-point rows and
    ranks 4-10 are left alone. Multipliers are multiples of 0.5, so a score can have a half point (7.5);
    points are never rounded.

    predictions: one list of ten titles per player. Returns (results, notes): results is one
    (total, breakdown) per player in the same order, and rows that were multiplied carry a "multiplier"
    key; notes is [(label, text)] for the results post: the titles that earned a bonus, biggest first.
    """
    players = len(predictions)
    multipliers, notes = {}, []
    if players < MIN_PLAYERS:
        notes.append(("Multipliers", f"off this week: {players} player(s), needs {MIN_PLAYERS}"))
    else:
        counts = {}
        for p in predictions:
            for title in {normalize(t) for t in p[:BONUS_RANKS] if t}:
                counts[title] = counts.get(title, 0) + 1
        multipliers = {title: (minority_multiplier(n, players), n) for title, n in counts.items()}

    results, earned = [], {}
    for p in predictions:
        _, breakdown = compute_score(p, actual, rules)
        total = 0
        for item in breakdown:
            if item["position"] <= BONUS_RANKS and item["predicted"] and item["points"] > 0:
                mult, picked = multipliers.get(normalize(item["predicted"]), (1.0, 0))
                if mult != 1.0:
                    item["points"] = num(item["points"] * mult)       # a multiple of 0.5, so exact in floating point
                    item["multiplier"] = mult
                    earned[normalize(item["predicted"])] = (item["predicted"], mult, picked)
            total += item["points"]
        results.append((num(total), breakdown))

    ordered = sorted(earned.values(), key=lambda e: (-e[1], e[2], e[0].casefold()))
    for title, mult, picked in ordered:          # at most 10: only titles on the real chart can earn points
        notes.append((title, f"x{mult:g} ({picked} of {players} players had it in their top {BONUS_RANKS})"))
    return results, notes


def compute_ballot_score(picks: list, categories: list):
    """picks: [{category, guess}, ...] for one user.
    categories: [{category, points, correct_answer}, ...] for the event.

    Each category is independent: its fixed point value if the guess matches the correct
    answer, 0 otherwise. No ranking, no partial credit."""
    guess_by_category = {normalize(p["category"]): p["guess"] for p in picks}
    breakdown = []
    total = 0
    for c in categories:
        correct = c.get("correct_answer", "")
        guess = guess_by_category.get(normalize(c["category"]), "")
        try:
            points_value = int(c["points"])
        except (TypeError, ValueError):
            points_value = 0

        if not correct:
            points, reason = 0, "not judged yet"
        elif not guess:
            points, reason = 0, "no guess"
        elif normalize(guess) == normalize(correct):
            points, reason = points_value, "correct"
        else:
            points, reason = 0, "incorrect"

        total += points
        breakdown.append(
            {"category": c["category"], "guess": guess, "correct": correct, "points": points, "reason": reason}
        )

    return total, breakdown
