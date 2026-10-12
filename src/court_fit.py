"""Measure a court from the coach's landmark clicks.

Gyms are not all the same size, and their markings differ (a 10 ft lane, a shorter three-point
line). When the landmarks are clicked against the wrong court, every click lands a foot or two from
where the court model says it should be, in the same way on every picture. This module tries the
sizes and markings a court can have, keeps the combination that makes the clicks agree best, and
reports it with the error before and after. The clicks themselves are never changed.

    measure(setup, landmarks) -> {"court": the court dict to put in court_setup.json, "rms_ft", "before_rms_ft",
                                  "words": one plain sentence, "warnings": [...]}
    Raises ValueError with a plain reason when the clicks cannot tell (one end only, no centre point...).
"""
import court_mapper
import court_model

# What is tried. Sizes in feet; the markings as painted on US courts (high school, college, NBA, FIBA-ish).
LENGTHS = [x / 2 for x in range(140, 193)]          # 70 to 96 ft, every half foot
WIDTHS = [x / 2 for x in range(76, 105)]            # 38 to 52 ft
LANES = [10, 12, 14, 16]
FREE_THROWS = [18, 18.5, 19, 19.5, 20]
THREES = [19.75, 20.5, 21, 21.65, 22.15, 23.75]
STEP_COARSE = 2.0                                     # the first pass over the sizes, in feet
GOOD_RMS_FT = 0.6                                     # below this the fit is as good as clicking gets


def ends_clicked(landmarks):
    """Which ends have clicks: {"left": n, "right": n, "centre": n}."""
    counts = {"left": 0, "right": 0, "centre": 0}
    for mark in landmarks:
        name = mark["name"]
        counts["left" if name.startswith("left_") else "right" if name.startswith("right_") else "centre"] += 1
    return counts


def error_for(setup, landmarks, length, width, lane, free_throw, three):
    try:
        return court_mapper.solve_court(setup, landmarks, length, width, lane, free_throw, three)["rms_ft"]
    except ValueError:
        return None


def best_of(setup, landmarks, choices):
    """The (rms, choice) with the smallest error over choices = [(length, width, lane, free_throw, three)]."""
    best = None
    for choice in choices:
        rms = error_for(setup, landmarks, *choice)
        if rms is not None and (best is None or rms < best[0]):
            best = (rms, choice)
    return best


def measure(setup, landmarks):
    counts = ends_clicked(landmarks)
    if counts["left"] < 4 or counts["right"] < 4:
        raise ValueError("Measuring needs at least 4 landmarks at EACH end of the court "
                         f"(left end: {counts['left']}, right end: {counts['right']} so far).")
    if counts["centre"] < 1:
        raise ValueError("Measuring needs at least one centre landmark (the centre circle on the half-court line), "
                         "or the court's length cannot be told.")
    court = setup["court"]
    now = court_model.court_from(court)
    before = error_for(setup, landmarks, now[0], now[1], now.lane, now.free_throw, now.three)
    if before is None:
        raise ValueError("The clicks do not give a court yet (see the message above).")

    # 1. The size, at the usual markings, every 2 ft; then every half foot around the best.
    coarse = [(L, W, None, None, None) for L in LENGTHS if (L * 2) % (STEP_COARSE * 2) == 0 for W in WIDTHS if (W * 2) % (STEP_COARSE * 2) == 0]
    rms, (L, W, _, _, _) = best_of(setup, landmarks, coarse)
    fine = [(l, w, None, None, None) for l in LENGTHS if abs(l - L) <= STEP_COARSE for w in WIDTHS if abs(w - W) <= STEP_COARSE]
    rms, (L, W, _, _, _) = best_of(setup, landmarks, fine)
    # 2. The markings at that size.
    marks = [(L, W, lane, free_throw, three) for lane in LANES for free_throw in FREE_THROWS for three in THREES]
    rms, (L, W, lane, free_throw, three) = best_of(setup, landmarks, marks)
    # 3. The size once more with those markings.
    fine = [(l, w, lane, free_throw, three) for l in LENGTHS if abs(l - L) <= 1.5 for w in WIDTHS if abs(w - W) <= 1.5]
    rms, (L, W, lane, free_throw, three) = best_of(setup, landmarks, fine)

    def plain(value):
        return int(value) if float(value).is_integer() else float(value)
    measured = {"length_ft": plain(L), "width_ft": plain(W), "lane_ft": plain(lane), "free_throw_ft": plain(free_throw),
                "three_point_ft": plain(three), "preset": "custom"}
    for name, size in court_model.PRESETS.items():
        if (L, W) == size and (lane, free_throw, three) == (court_model.LANE_WIDTH_FT, court_model.FREE_THROW_FT, court_model.THREE_POINT_FT):
            measured["preset"] = name
    warnings = []
    if rms > 1.0:
        warnings.append(f"Even the best-fitting court leaves a typical error of {rms:.2f} ft: some clicks are probably "
                        "on the wrong landmark. Check the ones marked in the table.")
    if rms > before - 0.05:
        warnings.append("Measuring did not improve on the size you chose: the clicks already agree with it.")
    words = (f"Measured: {L:g} x {W:g} ft, lane {lane:g} ft, free-throw line {free_throw:g} ft from the baseline, "
             f"three-point line {three:g} ft. Typical error {rms:.2f} ft (was {before:.2f} ft with the size chosen).")
    return {"court": measured, "rms_ft": round(rms, 2), "before_rms_ft": round(before, 2), "words": words, "warnings": warnings}
