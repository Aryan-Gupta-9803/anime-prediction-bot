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


# ---- Minority Multiplier ---------------------------------------------------------------------------------
# The knobs are all here. A title in the real top 3 earns a multiplier from how few players had it in
# their ten: MAX_MULTIPLIER when only one player did, 1x when CROWD_SHARE or more of the players did, and
# a straight line (in share of players) between, rounded to the nearest 0.5 so the numbers stay easy to follow.
MAX_MULTIPLIER = 4.0
CROWD_SHARE = 0.40
TOP_N = 3
MIN_PLAYERS = 5          # with fewer players nothing can be called a minority, so everything stays 1x


def minority_multiplier(picked: int, players: int) -> float:
    """Multiplier (1.0 to MAX_MULTIPLIER, always a multiple of 0.5) for a title that `picked` of `players`
    players had. One player alone gets the maximum; CROWD_SHARE of the players or more gets 1x. For 10
    players that is 1 -> 4x, 2 -> 3x, 3 -> 2x, 4 or more -> 1x."""
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
    """Scores every player's ten with compute_score, then multiplies the points earned on each of the real
    top 3 titles (exact spot or wrong spot) by that title's multiplier. Penalties (negative points) and
    zero-point rows are left alone. Multipliers are multiples of 0.5, so a score can have a half point (7.5);
    points are never rounded.

    predictions: one list of ten titles per player. Returns (results, notes): results is one
    (total, breakdown) per player in the same order, and rows that were multiplied carry a "multiplier"
    key; notes is [(label, text)] describing each top-3 title's multiplier, for the results post.
    """
    players = len(predictions)
    multipliers, notes = {}, []
    top = [t for t in actual[:TOP_N] if t]
    if players < MIN_PLAYERS:
        if top:
            notes.append(("Multipliers", f"off this week: {players} player(s), needs {MIN_PLAYERS}"))
    else:
        for title in top:
            picked = sum(1 for p in predictions if normalize(title) in {normalize(x) for x in p if x})
            mult = minority_multiplier(picked, players)
            multipliers[normalize(title)] = mult
            notes.append((title, f"x{mult:g} ({picked} of {players} players had it)"))

    results = []
    for p in predictions:
        _, breakdown = compute_score(p, actual, rules)
        total = 0
        for item in breakdown:
            mult = multipliers.get(normalize(item["predicted"]), 1.0) if item["predicted"] else 1.0
            if mult != 1.0 and item["points"] > 0:
                item["points"] = num(item["points"] * mult)       # a multiple of 0.5, so exact in floating point
                item["multiplier"] = mult
            total += item["points"]
        results.append((num(total), breakdown))
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
