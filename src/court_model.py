"""The basketball court as numbers: where the painted lines and the landmarks are, in feet.

Court coordinates used by every stage
    x runs along the court, from the LEFT baseline as the camera sees it (0) to the
      right baseline (court length).
    y runs across the court, from the NEAR sideline (camera side, 0) to the FAR
      sideline (the side away from the camera, court width).

What is in here
    PRESETS                          the usual court sizes, in feet
    landmarks(length, width)         20 named points the coach can click (corners, lane, ...)
    court_lines(length, width)       the painted lines as rows of points, for drawing
    on_court(x, y, length, width)    is this position on the court (with a small margin)?
    draw_court(picture, H, ...)      draw the lines onto a video frame

There is no video or file handling in this file, only geometry.
"""
import cv2
import numpy as np

# Markings that are the same on every court size below (US high school rules).
LANE_WIDTH_FT = 12              # the lane (the "key") is 12 ft wide
FREE_THROW_FT = 19              # free-throw line, measured from the baseline
CIRCLE_RADIUS_FT = 6            # centre circle and free-throw circle
THREE_POINT_FT = 19.75          # three-point line, measured from the middle of the hoop
HOOP_FROM_BASELINE_FT = 5.25    # middle of the hoop, measured from the baseline

# Court length and width in feet.
PRESETS = {"high_school": (84, 50), "college_nba": (94, 50), "junior_high": (74, 42)}

# draw_court leaves out anything that would land further than this from the picture.
FAR_AWAY_PX = 6000


def landmarks(length, width):
    """The 20 points the coach can click: name -> {"label": plain English, "court_ft": [x, y]}.

    "far" means the side of the court away from the camera, "near" means the camera side.
    """
    middle = width / 2
    half_lane = LANE_WIDTH_FT / 2
    marks = {}

    def add(name, label, x, y):
        marks[name] = {"label": label, "court_ft": [float(x), float(y)]}

    def add_end(end, baseline_x, direction):
        # direction is +1 at the left end and -1 at the right end: "away from the baseline".
        free_throw_x = baseline_x + direction * FREE_THROW_FT
        title = end.capitalize()
        add(f"{end}_baseline_far_sideline", f"{title} end: far corner (baseline meets far sideline)",
            baseline_x, width)
        add(f"{end}_baseline_near_sideline", f"{title} end: near corner (baseline meets near sideline)",
            baseline_x, 0)
        add(f"{end}_lane_far_baseline", f"{title} end: far lane line meets the baseline",
            baseline_x, middle + half_lane)
        add(f"{end}_lane_near_baseline", f"{title} end: near lane line meets the baseline",
            baseline_x, middle - half_lane)
        add(f"{end}_freethrow_far", f"{title} end: far end of the free-throw line",
            free_throw_x, middle + half_lane)
        add(f"{end}_freethrow_near", f"{title} end: near end of the free-throw line",
            free_throw_x, middle - half_lane)
        add(f"{end}_three_far_baseline", f"{title} end: three-point line meets the baseline, far side",
            baseline_x, middle + THREE_POINT_FT)
        add(f"{end}_three_near_baseline", f"{title} end: three-point line meets the baseline, near side",
            baseline_x, middle - THREE_POINT_FT)

    add_end("left", 0, +1)
    add("halfcourt_far_sideline", "Centre: half-court line meets the far sideline", length / 2, width)
    add("halfcourt_near_sideline", "Centre: half-court line meets the near sideline", length / 2, 0)
    add("centre_circle_far", "Centre: centre circle crosses the half-court line, far side",
        length / 2, middle + CIRCLE_RADIUS_FT)
    add("centre_circle_near", "Centre: centre circle crosses the half-court line, near side",
        length / 2, middle - CIRCLE_RADIUS_FT)
    add_end("right", length, -1)
    return marks


def straight(start, end):
    """Points every 1 ft along a straight line from start to end (feet)."""
    start, end = np.array(start, float), np.array(end, float)
    steps = max(1, int(round(np.linalg.norm(end - start))))
    return start + (end - start) * np.linspace(0, 1, steps + 1)[:, None]


def arc(centre_x, centre_y, radius, start_degrees, end_degrees):
    """Points about every 1 ft along part of a circle (feet)."""
    steps = max(2, int(round(np.radians(abs(end_degrees - start_degrees)) * radius)))
    angles = np.radians(np.linspace(start_degrees, end_degrees, steps + 1))
    return np.column_stack([centre_x + radius * np.cos(angles), centre_y + radius * np.sin(angles)])


def court_lines(length, width):
    """The painted basketball lines: a list of N x 2 arrays in feet, a point about every 1 ft."""
    middle = width / 2
    half_lane = LANE_WIDTH_FT / 2
    lines = [
        np.vstack([straight([0, 0], [length, 0]), straight([length, 0], [length, width]),
                   straight([length, width], [0, width]), straight([0, width], [0, 0])]),   # boundary
        straight([length / 2, 0], [length / 2, width]),                                     # half-court line
        arc(length / 2, middle, CIRCLE_RADIUS_FT, 0, 360),                                  # centre circle
    ]
    # The two ends are mirror images. direction = +1 at the left end, -1 at the right end.
    for baseline_x, direction in ((0, +1), (length, -1)):
        free_throw_x = baseline_x + direction * FREE_THROW_FT
        hoop_x = baseline_x + direction * HOOP_FROM_BASELINE_FT
        # The half circles bulge away from the baseline: to the right at the left end, to the left at the right end.
        start, end = (-90, 90) if direction > 0 else (90, 270)
        far, near = middle + half_lane, middle - half_lane
        lines.append(straight([baseline_x, far], [free_throw_x, far]))              # far lane line
        lines.append(straight([baseline_x, near], [free_throw_x, near]))            # near lane line
        lines.append(straight([free_throw_x, near], [free_throw_x, far]))           # free-throw line
        lines.append(arc(free_throw_x, middle, CIRCLE_RADIUS_FT, start, end))       # free-throw circle
        lines.append(arc(hoop_x, middle, THREE_POINT_FT, start, end))               # three-point arc
        # the two short straight pieces that join the arc to the baseline
        lines.append(straight([baseline_x, middle + THREE_POINT_FT], [hoop_x, middle + THREE_POINT_FT]))
        lines.append(straight([baseline_x, middle - THREE_POINT_FT], [hoop_x, middle - THREE_POINT_FT]))
    return lines


def on_court(x, y, length, width, margin_ft=3.0):
    """True if the position (feet) is on the court, or no more than margin_ft outside it."""
    return -margin_ft <= x <= length + margin_ft and -margin_ft <= y <= width + margin_ft


def draw_court(picture, H_image_to_court, length, width, colour=(0, 255, 0), thickness=2):
    """Draw the court lines onto a video frame and return it.

    H_image_to_court is the 3x3 matrix that turns a pixel into court feet. To draw we
    need the opposite direction (feet to pixel), which is its inverse.
    """
    height_px, width_px = picture.shape[:2]
    court_to_image = np.linalg.inv(H_image_to_court)
    # A point behind the camera comes out with the opposite sign in its third number.
    # A pixel low in the picture is certainly floor in front of the camera, so it
    # tells us which sign is the right one.
    right_sign = np.sign((H_image_to_court @ [width_px / 2, 0.9 * height_px, 1.0])[2])
    for line in court_lines(length, width):
        projected = np.column_stack([line, np.ones(len(line))]) @ court_to_image.T
        in_front = projected[:, 2] * right_sign > 1e-9
        pixels = projected[:, :2] / np.where(in_front, projected[:, 2], 1.0)[:, None]
        drawable = in_front & (np.abs(pixels).max(axis=1) < FAR_AWAY_PX)
        for i in range(len(pixels) - 1):
            if drawable[i] and drawable[i + 1]:
                start = (int(round(pixels[i][0])), int(round(pixels[i][1])))
                end = (int(round(pixels[i + 1][0])), int(round(pixels[i + 1][1])))
                cv2.line(picture, start, end, colour, thickness, cv2.LINE_AA)
    return picture
