from textutil import normalize


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
