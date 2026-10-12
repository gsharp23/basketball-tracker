"""Stage 1, step 3: work out where the court is in every sampled frame.

    python src/court_mapper.py --video clips/test.mp4
    python src/court_mapper.py --video clips/game1.mp4 --resume     (continue a stopped run)

Run these first (each prints what to do):
    python src/select_court.py            picks the reference pictures ("views")
    python src/select_court_keypoints.py  you click court landmarks on them
    python src/main.py                    finds the players (the long one), then runs this step itself

The idea
    The Veo camera stands still; the picture only turns and zooms. Two pictures from a
    camera like that are related by one 3x3 matrix (a "homography") that says where
    each pixel of one picture is in the other. So:
    1. select_court.py keeps a few reference views and links them all to one "main view".
    2. select_court_keypoints.py: your landmark clicks, on any views, are carried into
       the main view and ONE matrix "main view -> court feet" is solved from them.
    3. This file matches every sampled frame to a reference view by its fixed details
       (the building and the floor markings; players are masked out), which gives
       "frame -> view". Chained with the two matrices above that is "frame -> court feet".

    A frame that matches no view is matched to the frame before it instead ("chained"),
    for at most 10 seconds. After that the frame gets no court mapping at all: nothing
    is guessed. The summary lists those stretches and how to fix them.

What it writes
    data/<video>/court_frames.json   the matrix for every sampled frame (the cache)
    data/<video>/positions.json      every player's position in feet
    outputs/<video>/court_check.jpg  8 frames with the court drawn on, to check by eye

For the other stages
    court_size(video)                    a court_model.Court: (length_ft, width_ft) with .lane, .free_throw, .three
    court_homography(video, frame)       3x3 matrix picture -> court feet, or None
    load_homographies(video)             the same for all sampled frames at once: {frame: matrix or None}
    to_court(H, points_px)               pixels -> feet
    to_image(H, points_ft)               feet -> pixels
    hoops_in_frame(video, frame)         where the marked hoops are in that frame
    setup_missing(video)                 which set-up steps still have to be done
    run(video)                           do the mapping (what the command above does)

No YOLO model and no number reading is loaded here, so no lock is needed.
"""
import argparse
import sys
import time

import cv2
import numpy as np

import config
import court_model

# --- Finding and matching fixed details ("SIFT features") ---
# Do not lower the first two: 1500 features on a third-size picture was measured
# clearly worse on game frames (weaker links and one picture failing the line check).
SIFT_FEATURES = 3000        # keep the 3000 strongest details per picture
SIFT_SCALE = 0.5            # look for them on a half-size picture (4x faster)
RATIO = 0.75                # a match counts only if it is clearly better than the second best
RANSAC_PX = 4.0             # matched points must agree with the matrix to within 4 pixels
BOX_PAD = 12                # grow player boxes by this many pixels before masking them out
LOGO = (0.86, 0.86, 0.99, 0.965)    # the "veo" watermark: left, top, right, bottom as fractions

# --- When is a match trusted? ---
# "Inliers" are the matched points that agree with the matrix. "Spread" is how much of
# the picture width they cover: points bunched in one corner pin the rest down badly.
MIN_INLIERS = 40
MIN_SPREAD = 0.35
GOOD_INLIERS = 150          # with this many there is no need to try further views
GOOD_SPREAD = 0.5

# --- The landmark clicks ---
# Measured on the test clip: a good set of clicks fits to 0.08 ft, on game pictures 0.16 to
# 0.19 ft. Choosing the wrong court size (74 x 42 instead of 84 x 50) gave 0.52 ft.
POOR_FIT_FT = 0.4           # warn when the typical landmark error is larger than this

# --- Mapping a video ---
NEIGHBOURS = 4              # besides the last view used, try its 4 best-linked views
SEARCH_EVERY = 10           # while lost, search all views every 10 sampled frames
MAX_CHAIN = 100             # chain frame-to-frame for at most 100 sampled frames (10 s)
MAX_JUMP_FT = 15            # the middle of the picture cannot move further than this in one step
OFF_COURT_FT = 20           # the middle of the picture must be within this of the court
SAVE_EVERY = 1000           # save progress every 1000 frames so a long run can be resumed
CUT_OFF_PX = 5              # a box this close to the bottom edge has its feet cut off

CHECK_TILES = 8             # frames shown in court_check.jpg
TILE_SIZE = (640, 360)

SIFT = cv2.SIFT_create(nfeatures=SIFT_FEATURES)

# court_frames.json and hoops.json are read once per video and kept here.
_homographies = {}
_hoops = {}


def parse_args():
    parser = argparse.ArgumentParser(description="Work out where the court is in every sampled frame.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="video file (default clips/test.mp4)")
    parser.add_argument("--resume", action="store_true", help="continue a run that was stopped")
    return parser.parse_args()


# ---------------------------------------------------------------- matching pictures

def features(picture, boxes=()):
    """Fixed details of a picture: (points N x 2 in full-size pixels, descriptors N x 128).

    boxes are player and referee boxes [x1, y1, x2, y2]; nothing is taken from inside
    them (people move), nor from the veo watermark.
    """
    grey = cv2.cvtColor(picture, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(grey, None, fx=SIFT_SCALE, fy=SIFT_SCALE, interpolation=cv2.INTER_AREA)
    height, width = small.shape
    mask = np.full((height, width), 255, np.uint8)
    for x1, y1, x2, y2 in boxes:
        top_left = (int((x1 - BOX_PAD) * SIFT_SCALE), int((y1 - BOX_PAD) * SIFT_SCALE))
        bottom_right = (int((x2 + BOX_PAD) * SIFT_SCALE), int((y2 + BOX_PAD) * SIFT_SCALE))
        cv2.rectangle(mask, top_left, bottom_right, 0, -1)
    cv2.rectangle(mask, (int(LOGO[0] * width), int(LOGO[1] * height)),
                  (int(LOGO[2] * width), int(LOGO[3] * height)), 0, -1)
    keypoints, descriptors = SIFT.detectAndCompute(small, mask)
    points = np.array([k.pt for k in keypoints], np.float32).reshape(-1, 2) / SIFT_SCALE
    if descriptors is None:
        descriptors = np.zeros((0, 128), np.float32)
    return points, descriptors


def match(points_a, desc_a, points_b, desc_b, width, height):
    """Match picture A to picture B.

    Returns {"H": 3x3 matrix taking A pixels to B pixels, "inliers": how many points
    agree, "spread": share of the picture width they cover}, or None if there is no
    believable match.
    """
    if len(points_a) < 8 or len(points_b) < 8:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(desc_a, desc_b, k=2)
    keep = [p[0] for p in pairs if len(p) == 2 and p[0].distance < RATIO * p[1].distance]
    if len(keep) < 8:
        return None
    a = np.float32([points_a[m.queryIdx] for m in keep])
    b = np.float32([points_b[m.trainIdx] for m in keep])
    H, agrees = cv2.findHomography(a, b, cv2.RANSAC, RANSAC_PX, maxIters=2000, confidence=0.999)
    if H is None:
        return None
    # A real turn-and-zoom keeps the picture outline a sensible four-sided shape.
    # A twisted or tiny or huge outline means the matrix is nonsense.
    outline = np.float32([[0, 0], [width, 0], [width, height], [0, height]]).reshape(-1, 1, 2)
    mapped = cv2.perspectiveTransform(outline, H)
    area = cv2.contourArea(mapped) / (width * height)
    if not cv2.isContourConvex(mapped) or not 0.1 < area < 10:
        return None
    inliers = a[agrees.ravel() == 1]
    spread = (inliers[:, 0].max() - inliers[:, 0].min()) / width
    return {"H": H, "inliers": len(inliers), "spread": float(spread)}


def usable(m):
    """Enough agreeing points, spread widely enough, to trust the match."""
    return m["inliers"] >= MIN_INLIERS and m["spread"] >= MIN_SPREAD


def good(m):
    """So many agreeing points that no other view needs to be tried."""
    return m["inliers"] >= GOOD_INLIERS and m["spread"] >= GOOD_SPREAD


def normalise(H, width, height):
    """Put a picture -> court matrix in a standard form.

    A homography means the same thing when all nine numbers are multiplied by any
    number, including a negative one. We fix the size (so files look alike) and the
    sign (so a pixel low in the picture, which is floor in front of the camera, gets a
    positive third number). carry_points() relies on every stored matrix having that sign.
    """
    H = np.asarray(H, float)
    H = H / np.linalg.norm(H)
    if (H @ [width / 2, 0.9 * height, 1.0])[2] < 0:
        H = -H
    return H


def link_views(view_features, width, height):
    """Link all reference views into one tree around a "main view".

    view_features is {frame: (points, descriptors)}. Returns
        main       the view with the most matches to the others
        to_main    {frame: 3x3 matrix taking that view's pixels to main-view pixels}
        links      [[view a, view b, inliers], ...] for every pair that matched
        unlinked   views that match nothing (they cannot be used)
    """
    frames = sorted(view_features)
    between = {}        # (a, b) -> matrix taking view a pixels to view b pixels
    links = []
    for i, a in enumerate(frames):
        for b in frames[i + 1:]:
            m = match(*view_features[a], *view_features[b], width, height)
            if m is not None and usable(m):
                between[(a, b)] = m["H"]
                between[(b, a)] = np.linalg.inv(m["H"])
                links.append([a, b, m["inliers"]])
    total = {frame: 0 for frame in frames}
    for a, b, inliers in links:
        total[a] += inliers
        total[b] += inliers
    main = max(frames, key=lambda frame: total[frame])
    # Grow the tree from the main view: always add the view that has the strongest
    # link to a view already in the tree, and go through that view to reach the main one.
    to_main = {main: np.eye(3)}
    while True:
        best = None
        for a, b, inliers in links:
            for new, parent in ((a, b), (b, a)):
                if new not in to_main and parent in to_main and (best is None or inliers > best[0]):
                    best = (inliers, new, parent)
        if best is None:
            break
        _, new, parent = best
        to_main[new] = to_main[parent] @ between[(new, parent)]
    unlinked = [frame for frame in frames if frame not in to_main]
    return main, to_main, links, unlinked


# ---------------------------------------------------------------- the court calibration

def fit_court(points_main, court_ft, width, height):
    """Solve the one matrix "main-view pixel -> court feet" from the clicked landmarks.

    points_main are the clicks carried into the main view, each kept as three numbers
    (x, y, w) WITHOUT dividing by w: a landmark clicked on a view that looks far to the
    side of the main view has a small or even negative w, and dividing would break it.
    This is the standard "direct linear transform": every landmark gives two equations
    in the nine unknown numbers of the matrix; the best answer for all of them together
    is the last row of the SVD.
    """
    # Move pixels to roughly -0.5 .. 0.5 and feet to roughly 0 .. 2, so that all the
    # numbers in the equations are of similar size (otherwise the answer is less exact).
    T = np.array([[1 / width, 0, -0.5], [0, 1 / width, -height / (2 * width)], [0, 0, 1.0]])
    q = points_main @ T.T
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    rows = []
    for (a, b, c), (X, Y) in zip(q, np.asarray(court_ft, float) / 50.0):
        rows.append([a, b, c, 0, 0, 0, -X * a, -X * b, -X * c])
        rows.append([0, 0, 0, a, b, c, -Y * a, -Y * b, -Y * c])
    G = np.linalg.svd(np.array(rows))[2][-1].reshape(3, 3)
    return np.diag([50.0, 50.0, 1.0]) @ G @ T


def mostly_on_one_line(points):
    """True if all the points, or all but one, lie on one straight line.

    Landmarks like that cannot pin the court down, however many there are.
    """
    points = np.unique(np.round(np.asarray(points, float), 3), axis=0)
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            direction = points[j] - points[i]
            offsets = points - points[i]
            # distance of every point from the line through points i and j
            distance = np.abs(direction[0] * offsets[:, 1] - direction[1] * offsets[:, 0]) / np.linalg.norm(direction)
            if (distance < 0.01).sum() >= len(points) - 1:
                return True
    return False


def solve_court(setup, landmarks, length, width, lane=None, free_throw=None, three=None):
    """Turn the clicked landmarks into the court calibration.

    setup is the contents of court_setup.json, landmarks a list of
    {"name", "view", "px": [x, y]}. Returns {"main_to_court": 3x3, "errors_ft": one per
    landmark, "rms_ft", "max_ft", "warnings": [plain sentences]}.
    Raises ValueError with a plain message when the clicks cannot give a court.
    """
    model = court_model.landmarks(length, width, lane, free_throw, three)
    picture_width, picture_height = setup["width"], setup["height"]
    to_main = {view["frame"]: np.array(view["to_main"]) for view in setup["views"]}
    for mark in landmarks:
        if mark["name"] not in model:
            raise ValueError(f"\"{mark['name']}\" is not a landmark this tool knows.")
        if mark["view"] not in to_main:
            raise ValueError(f"A landmark was clicked on the reference picture of frame {mark['view']}, "
                             "which is no longer used.")
    names = sorted(set(mark["name"] for mark in landmarks))
    if len(names) < 4:
        raise ValueError(f"At least 4 different landmarks are needed; there are {len(names)} so far.")
    # Court feet always come from the court model at the CURRENT court size.
    court = np.array([model[mark["name"]]["court_ft"] for mark in landmarks])
    if mostly_on_one_line(court):
        raise ValueError("All of the landmarks (or all but one) are on one straight line of the court. "
                         "Add landmarks away from that line, for example the two ends of the free-throw line.")
    carried = np.array([to_main[mark["view"]] @ [mark["px"][0], mark["px"][1], 1.0] for mark in landmarks])
    main_to_court = fit_court(carried, court, picture_width, picture_height)

    # Mirror check. The camera is on the near sideline, so going right in the picture
    # must go up in court x, and going down in the picture must come towards the camera
    # (down in court y). A fit that has this the other way round is a flipped court.
    view = landmarks[0]["view"]
    cx, cy = picture_width / 2, 0.75 * picture_height
    here, right, down = to_court(main_to_court @ to_main[view], [[cx, cy], [cx + 10, cy], [cx, cy + 10]])
    step_right, step_down = right - here, down - here
    if step_right[0] * step_down[1] - step_right[1] * step_down[0] >= 0:
        raise ValueError("These landmarks give a mirrored court. Check that left-end and right-end "
                         "landmarks, and far and near ones, were not swapped.")

    predicted = carried @ main_to_court.T
    predicted = predicted[:, :2] / predicted[:, 2:3]
    errors = np.linalg.norm(predicted - court, axis=1)
    warnings = []
    for mark, error in zip(landmarks, errors):
        if error > 1.0:
            warnings.append(f"Check this click: \"{model[mark['name']]['label']}\" on the picture of frame "
                            f"{mark['view']} is {error:.1f} ft away from where the other landmarks put it.")
    # Does the video show a part of the court that has no landmarks anywhere near it?
    seen_x = []
    for frame, matrix in to_main.items():
        seen = to_court(main_to_court @ matrix, [[0.1 * picture_width, 0.75 * picture_height],
                                                 [0.9 * picture_width, 0.75 * picture_height]])
        seen_x.extend(seen[:, 0])
    if min(seen_x) < court[:, 0].min() - 20:
        warnings.append("The LEFT end of the court is in the video but has no landmarks. Positions there can be "
                        "2 to 5.5 ft off. Click at least 4 landmarks at the left end.")
    if max(seen_x) > court[:, 0].max() + 20:
        warnings.append("The RIGHT end of the court is in the video but has no landmarks. Positions there can be "
                        "2 to 5.5 ft off. Click at least 4 landmarks at the right end.")
    rms = float(np.sqrt((errors ** 2).mean()))
    if rms > POOR_FIT_FT:
        warnings.append(f"The landmarks fit the court only roughly (typical error {rms:.2f} ft; a good set is under "
                        "0.3 ft). Check the court size you chose, and that every click is on the landmark named.")
    return {"main_to_court": main_to_court,
            "errors_ft": [round(float(e), 2) for e in errors],
            "rms_ft": round(rms, 2),
            "max_ft": round(float(errors.max()), 2),
            "warnings": warnings}


# ---------------------------------------------------------------- reading the set-up files

def load_setup(video):
    """The contents of court_setup.json (court size, reference views and how they link)."""
    path = config.data_dir(video) / "court_setup.json"
    config.require_file(path, f"python src/select_court.py --video {video}")
    return config.load_json(path)


def load_view_picture(video, view):
    """The saved reference picture of one view (an entry of setup["views"])."""
    path = config.data_dir(video) / view["picture"]
    picture = cv2.imread(str(path))
    if picture is None:
        sys.exit(f"Cannot read the reference picture {path}\n"
                 f"Make the reference pictures again:  python src/select_court.py --video {video} --rescan")
    return picture


def load_view_features(video, setup):
    """{frame: (points, descriptors)} for every reference view, from the saved pictures.

    This takes about 0.2 s per view, so nothing is kept in a file.
    """
    return {view["frame"]: features(load_view_picture(video, view)) for view in setup["views"]}


def load_calibration(video):
    """The court calibration, solved afresh from the stored clicks.

    Returns {"court": (length_ft, width_ft), "view_to_court": {frame: 3x3 matrix taking
    that view's pixels to court feet}, "fit": what solve_court returned,
    "provisional": True if the clicks were not made by the coach}.
    """
    setup = load_setup(video)
    path = config.data_dir(video) / "court_calibration.json"
    config.require_file(path, f"python src/select_court_keypoints.py --video {video}")
    saved = config.load_json(path)
    court = court_size(video)
    length, width = court
    try:
        fit = solve_court(setup, saved["landmarks"], length, width, **court_model.extra(court))
    except ValueError as problem:
        sys.exit(f"The court landmarks in {path} cannot be used: {problem}\n"
                 f"Mark the court again:  python src/select_court_keypoints.py --video {video}")
    view_to_court = {view["frame"]: normalise(fit["main_to_court"] @ np.array(view["to_main"]),
                                              setup["width"], setup["height"])
                     for view in setup["views"]}
    return {"court": court, "view_to_court": view_to_court, "fit": fit,
            "provisional": bool(saved.get("provisional"))}


def court_size(video):
    """The court the coach chose in select_court.py: a court_model.Court, (length_ft, width_ft) that also knows
    the lane and the lines; the defaults in config.py when there is no set-up yet."""
    path = config.data_dir(video) / "court_setup.json"
    if path.exists():
        return court_model.court_from(config.load_json(path)["court"])
    return court_model.Court(config.COURT_LENGTH_FT, config.COURT_WIDTH_FT)


def setup_missing(video):
    """Plain sentences naming the court set-up steps still to do (an empty list when ready)."""
    todo = []
    folder = config.data_dir(video)
    if not (folder / "court_setup.json").exists():
        todo.append(f"The reference pictures of the court have not been chosen. "
                    f"Run:  python src/select_court.py --video {video}")
    if not (folder / "court_calibration.json").exists():
        todo.append(f"The court landmarks have not been clicked. "
                    f"Run:  python src/select_court_keypoints.py --video {video}")
    return todo


# ---------------------------------------------------------------- pixels <-> feet

def to_court(H, points_px):
    """Turn pixels [[x, y], ...] into court feet (N x 2 array) with a picture -> court matrix."""
    points = np.asarray(points_px, np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(points, np.asarray(H, np.float64)).reshape(-1, 2)


def to_image(H, points_ft):
    """Turn court feet [[x, y], ...] into pixels (N x 2 array) with a picture -> court matrix."""
    points = np.asarray(points_ft, np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(points, np.linalg.inv(np.asarray(H, np.float64))).reshape(-1, 2)


def carry_points(H_from, H_to, points_px, width=None, height=None):
    """Carry pixels from one picture into another, given both pictures' court matrices.

    Returns one [x, y] per point, or None for a point that the other picture does not
    show (behind the camera or, when width and height are given, outside the picture).
    Because the camera only turns and zooms, this works for ANY point, also above the
    floor (a hoop, say). Both matrices must be in the standard form of normalise().
    """
    between = np.linalg.inv(H_to) @ H_from
    carried = []
    for x, y in points_px:
        r = between @ [x, y, 1.0]
        if r[2] <= 0:
            carried.append(None)
            continue
        x_new, y_new = r[0] / r[2], r[1] / r[2]
        if width is not None and not (0 <= x_new < width and 0 <= y_new < height):
            carried.append(None)
            continue
        carried.append([float(x_new), float(y_new)])
    return carried


# ---------------------------------------------------------------- mapping a video

def try_views(points, descriptors, candidates, view_features, width, height):
    """Match a frame to each candidate view in turn.

    Stops at the first good match. Otherwise returns the best usable one.
    Returns (view, match) or None.
    """
    best = None
    for view in candidates:
        m = match(points, descriptors, *view_features[view], width, height)
        if m is None or not usable(m):
            continue
        if good(m):
            return view, m
        if best is None or m["inliers"] > best[1]["inliers"]:
            best = (view, m)
    return best


def register(points, descriptors, state, views):
    """Find the court matrix of one frame. Returns (H or None, how, view or None, inliers).

    state remembers what happened on the frames before (which view matched last, the
    previous frame's details for chaining). views holds what is fixed for the whole run.
    """
    width, height = views["width"], views["height"]
    found = None
    # A. Usually the camera has hardly moved: try the view that matched last time,
    #    then the views that overlap it most.
    tried = []
    if state["view"] is not None:
        tried = [state["view"]] + views["neighbours"][state["view"]]
        found = try_views(points, descriptors, tried, views["features"], width, height)
    # B. Lost (or just starting): try every other view, but only now and then, because
    #    that is slow when there are many views.
    state["since_search"] += 1
    if found is None and (state["view"] is None or state["since_search"] >= SEARCH_EVERY):
        others = [view for view in views["search_order"] if view not in tried]
        found = try_views(points, descriptors, others, views["features"], width, height)
        state["since_search"] = 0

    H, how, view, inliers = None, "none", None, 0
    if found is not None:
        # C. frame -> view -> court
        view, m = found
        H, how, inliers = views["to_court"][view] @ m["H"], "view", m["inliers"]
    elif state["previous"] is not None and state["chained"] < MAX_CHAIN:
        # D. No view matches: match to the previous frame instead and reuse its court matrix.
        previous = state["previous"]
        m = match(points, descriptors, previous["points"], previous["descriptors"], width, height)
        if m is not None and usable(m):
            H, how, inliers = previous["H"] @ m["H"], "chained", m["inliers"]

    # F. Two sanity checks. Failing either means "no mapping" rather than a wrong one.
    if H is not None:
        H = normalise(H, width, height)
        middle = to_court(H, [[width / 2, 0.75 * height]])[0]
        length, court_width = views["court"]
        if not court_model.on_court(middle[0], middle[1], length, court_width, OFF_COURT_FT):
            H = None
        elif state["previous"] is not None and np.linalg.norm(middle - state["previous"]["middle"]) > MAX_JUMP_FT:
            H = None
    if H is None:
        state["previous"] = None
        state["chained"] = 0
        return None, "none", None, 0

    state["previous"] = {"points": points, "descriptors": descriptors, "H": H, "middle": middle}
    if how == "view":
        state["view"] = view
        state["chained"] = 0
    else:
        state["chained"] += 1
    return H, how, view, inliers


def foot_points(players, height):
    """Where each player stands in the picture: the middle of the bottom edge of the box.

    Boxes that touch the bottom of the picture are left out: the feet are cut off.
    Returns (list of player ids, list of [x, y]).
    """
    ids, points = [], []
    for player in players:
        x1, y1, x2, y2 = player["bbox"]
        if y2 >= height - CUT_OFF_PX:
            continue
        ids.append(player["id"])
        points.append([(x1 + x2) / 2, y2])
    return ids, points


def check_tile(picture, H, players, court):
    """One small picture for court_check.jpg: the court drawn on, feet marked in court feet."""
    height, width = picture.shape[:2]
    tile = cv2.resize(picture, TILE_SIZE, interpolation=cv2.INTER_AREA)
    if H is None:
        cv2.putText(tile, "no court mapping", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
        return tile
    # The tile is smaller than the frame: first scale tile pixels up to frame pixels.
    grow = np.diag([width / TILE_SIZE[0], height / TILE_SIZE[1], 1.0])
    court_model.draw_court(tile, H @ grow, court[0], court[1], thickness=1, **court_model.extra(court))
    ids, points = foot_points(players, height)
    if points:
        for (x, y), (court_x, court_y) in zip(points, to_court(H, points)):
            spot = (int(x * TILE_SIZE[0] / width), int(y * TILE_SIZE[1] / height))
            cv2.circle(tile, spot, 3, (0, 0, 255), -1)
            cv2.putText(tile, f"{court_x:.0f},{court_y:.0f}", (spot[0] - 18, spot[1] + 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1, cv2.LINE_AA)
    return tile


def save_frames_file(video, court, records, complete, summary=None):
    """Write court_frames.json (the cache of one matrix per sampled frame)."""
    config.save_json(config.data_dir(video) / "court_frames.json",
                     {"video": video, "court": court_model.court_from(court).as_dict(),
                      "complete": complete, "frames": records, "summary": summary or {}})
    _homographies.pop(video, None)


def minutes_seconds(seconds):
    """75.3 -> "01:15"."""
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def unmapped_stretches(records):
    """Runs of frames in a row with no court mapping: [(first frame, last frame, how many)], longest first."""
    stretches = []
    start = None
    for i, record in enumerate(records):
        if record["H"] is None and start is None:
            start = i
        if record["H"] is not None and start is not None:
            stretches.append((records[start]["frame"], records[i - 1]["frame"], i - start))
            start = None
    if start is not None:
        stretches.append((records[start]["frame"], records[-1]["frame"], len(records) - start))
    return sorted(stretches, key=lambda stretch: -stretch[2])


def setup_changed(video, saved):
    """True if court_frames.json (contents: saved) was made from an older set-up or detection.

    Its frames must then not be mixed with newly mapped ones: after a change of court
    size or landmarks the same spot on the floor would get two different positions.
    """
    folder = config.data_dir(video)
    made = (folder / "court_frames.json").stat().st_mtime
    newer = [name for name in ("court_setup.json", "court_calibration.json", "tracks.json")
             if (folder / name).stat().st_mtime > made]
    # The court size or markings changed since: compared on the keys the saved file has (older files
    # know only the length and the width).
    wanted = court_size(video).as_dict()
    changed = any(saved["court"].get(key) != value for key, value in wanted.items() if key in saved["court"])
    return bool(newer) or changed


def run(video=config.DEFAULT_VIDEO, resume=False):
    """Map every sampled frame of the video to the court. Returns the summary."""
    config.require_video(video)
    folder = config.data_dir(video)
    tracks_file = folder / "tracks.json"
    config.require_file(tracks_file, f"python src/main.py --video {video}")
    setup = load_setup(video)
    calibration = load_calibration(video)
    config.lock_video(video)
    tracks = config.load_json(tracks_file)
    if not tracks["frames"]:
        sys.exit("tracks.json has no frames in it. Run the detection again:\n"
                 f"  python src/main.py --video {video} --from detect")
    width, height, fps = tracks["width"], tracks["height"], tracks["fps"]
    if (width, height) != (setup["width"], setup["height"]):
        sys.exit("The court set-up was made for a different picture size than this video.\n"
                 f"Make it again:  python src/select_court.py --video {video} --rescan")
    court = calibration["court"]
    if calibration["provisional"]:
        print("NOTE: the court landmarks for this video are PROVISIONAL (not clicked by you).")
        print(f"      Click them yourself:  python src/select_court_keypoints.py --video {video}")
    for warning in calibration["fit"]["warnings"]:
        print(f"WARNING: {warning}")

    print(f"Reading the {len(setup['views'])} reference pictures ...")
    view_features = load_view_features(video, setup)
    strength = {frame: {} for frame in view_features}     # strength[a][b] = inliers of the link a-b
    for a, b, inliers in setup["links"]:
        strength[a][b] = inliers
        strength[b][a] = inliers
    views = {
        "width": width, "height": height, "court": court,
        "features": view_features,
        "to_court": calibration["view_to_court"],
        # for each view, its best-linked other views
        "neighbours": {frame: sorted(strength[frame], key=lambda other: -strength[frame][other])[:NEIGHBOURS]
                       for frame in view_features},
        # all views, the best-connected first
        "search_order": sorted(view_features, key=lambda frame: -sum(strength[frame].values())),
    }

    by_frame = {record["frame"]: record for record in tracks["frames"]}
    sampled = sorted(by_frame)
    records = []
    frames_file = folder / "court_frames.json"
    if resume and frames_file.exists():
        saved = config.load_json(frames_file)
        if setup_changed(video, saved):
            print("The court set-up or the detection has changed since the stopped run,")
            print("so the mapping starts again from the first frame.")
        else:
            records = saved["frames"]
            print(f"Resuming: {len(records)} frames were already done.")
    done = {record["frame"]: record for record in records}
    todo = [frame for frame in sampled if frame not in done]
    check_frames = set(sampled[int(i)] for i in np.linspace(0, len(sampled) - 1, CHECK_TILES))
    tiles = {}

    state = {"view": None, "since_search": 0, "previous": None, "chained": 0}
    print(f"Mapping {len(todo)} frames of {video} to the court ...")
    started = time.time()
    capture = cv2.VideoCapture(str(config.video_path(video)))
    index = -1
    new = 0
    stopped = False
    try:
        # Read the video from the start, frame by frame. grab() only steps forward;
        # retrieve() makes the picture, and we ask for it only on frames we need.
        # We stop at the first frame that cannot be read, whatever the file claims its
        # length is (test.mp4 says 612 frames but only 599 can be read).
        while index < sampled[-1] and capture.grab():
            index += 1
            if index in done:
                if index in check_frames:
                    ok, picture = capture.retrieve()
                    if ok:
                        H = done[index]["H"]
                        tiles[index] = check_tile(picture, None if H is None else np.array(H),
                                                  by_frame[index]["players"], court)
                continue
            if index not in by_frame:
                continue
            ok, picture = capture.retrieve()
            if not ok:
                break
            record = by_frame[index]
            boxes = [item["bbox"] for item in record["players"] + record["refs"]]
            points, descriptors = features(picture, boxes)
            H, how, view, inliers = register(points, descriptors, state, views)
            records.append({"frame": index, "H": None if H is None else H.tolist(),
                            "how": how, "view": view, "inliers": inliers})
            if index in check_frames:
                tiles[index] = check_tile(picture, H, record["players"], court)
            new += 1
            if new % 100 == 0:
                rate = new / (time.time() - started)
                print(f"  {new} of {len(todo)} frames ({rate:.1f} per second, "
                      f"about {minutes_seconds((len(todo) - new) / rate)} to go)")
            if new % SAVE_EVERY == 0:
                save_frames_file(video, court, records, complete=False)
    except KeyboardInterrupt:
        stopped = True
    capture.release()
    if stopped:
        save_frames_file(video, court, records, complete=False)
        sys.exit(f"\nStopped after {len(records)} frames. Nothing is lost. Continue with:\n"
                 f"  python src/court_mapper.py --video {video} --resume")
    if not records:
        sys.exit(f"No frame of {video} could be read.")

    # Summary and output files.
    mapped = [record for record in records if record["H"] is not None]
    summary = {"frames": len(records), "mapped": len(mapped),
               "by_view": sum(record["how"] == "view" for record in records),
               "chained": sum(record["how"] == "chained" for record in records),
               "not_mapped": len(records) - len(mapped),
               "seconds": round(time.time() - started, 1),
               # the 3 longest stretches with no court mapping, as times in the video
               "unmapped": [{"from": minutes_seconds(first / fps), "to": minutes_seconds(last / fps), "frames": count,
                             "middle": minutes_seconds((first + last) / 2 / fps)}
                            for first, last, count in unmapped_stretches(records)[:3]]}
    save_frames_file(video, court, records, complete=True, summary=summary)
    positions = write_positions(video)          # reads the file just saved
    feet = [player for frame in positions["frames"] for player in frame["players"]]
    on_court = sum(court_model.on_court(player["x"], player["y"], court[0], court[1]) for player in feet)
    summary["foot_points"] = len(feet)
    summary["on_court_share"] = round(on_court / len(feet), 3) if feet else None
    save_frames_file(video, court, records, complete=True, summary=summary)     # again, now with the full summary
    check_file = save_check_picture(video, tiles)

    print()
    print(f"Court mapping for {video}  ({court[0]} x {court[1]} ft court)")
    print(f"  frames looked at:        {summary['frames']}")
    print(f"  mapped to the court:     {summary['mapped']}  ({100 * summary['mapped'] / summary['frames']:.1f}%)")
    print(f"     by a reference view:  {summary['by_view']}")
    print(f"     chained from the frame before: {summary['chained']}")
    print(f"  no court mapping:        {summary['not_mapped']}")
    if feet:
        print(f"  player foot points on the court (within 3 ft): {on_court} of {len(feet)} "
              f"({100 * on_court / len(feet):.1f}%)")
    print(f"  time taken: {minutes_seconds(summary['seconds'])}")
    print_unmapped(video, summary)
    print(f"  Wrote {frames_file}")
    print(f"        {folder / 'positions.json'}")
    print(f"  Check by eye: {check_file}")
    print("  The green court should sit on the painted lines in every small picture.")
    return summary


def print_unmapped(video, summary):
    """Print the longest stretches with no court mapping and how to fix each (main.py repeats this at the end)."""
    stretches = summary.get("unmapped", [])
    if not stretches:
        return
    print("  Longest stretches with no court mapping:")
    for stretch in stretches:
        print(f"     {stretch['from']} to {stretch['to']} ({stretch['frames']} frames). To add a reference picture there:")
        print(f"       python src/select_court.py --video {video} --add-time {stretch['middle']}")
    print(f"  Then run this again:  python src/main.py --video {video}")


def save_check_picture(video, tiles):
    """Write outputs/<video>/court_check.jpg from the small pictures kept during the run."""
    ordered = [tiles[frame] for frame in sorted(tiles)]
    if not ordered:
        return None
    while len(ordered) % 4:
        ordered.append(np.zeros_like(ordered[0]))
    sheet = np.vstack([np.hstack(ordered[i:i + 4]) for i in range(0, len(ordered), 4)])
    path = config.output_dir(video) / "court_check.jpg"
    cv2.imwrite(str(path), sheet)
    # The SD card has damaged files before: read the picture back to be sure it is whole.
    back = cv2.imread(str(path))
    if back is None or back.shape != sheet.shape:
        raise IOError(f"{path} did not read back correctly. The disk may be failing; run the command again.")
    return path


# ---------------------------------------------------------------- reading the results

def load_homographies(video):
    """{frame: 3x3 picture -> court matrix, or None} for every sampled frame (read once per video)."""
    if video not in _homographies:
        folder = config.data_dir(video)
        path = folder / "court_frames.json"
        config.require_file(path, f"python src/court_mapper.py --video {video}")
        for name in ("court_setup.json", "court_calibration.json"):
            if (folder / name).exists() and (folder / name).stat().st_mtime > path.stat().st_mtime:
                print(f"NOTE: {name} was changed after the court mapping was made. "
                      f"Run again:  python src/court_mapper.py --video {video}")
        saved = config.load_json(path)
        if not saved["complete"]:
            print(f"NOTE: the court mapping of {video} was stopped part-way. "
                  f"Finish it:  python src/court_mapper.py --video {video} --resume")
        _homographies[video] = {record["frame"]: None if record["H"] is None else np.array(record["H"])
                                for record in saved["frames"]}
    return _homographies[video]


def court_homography(video, frame_index):
    """The 3x3 picture -> court matrix of one frame.

    None when that frame has no court mapping, or is not one of the sampled frames.
    Nothing is worked out here; it only reads what run() saved.
    """
    return load_homographies(video).get(frame_index)


def write_positions(video):
    """Write data/<video>/positions.json: every player's position in court feet, per frame."""
    folder = config.data_dir(video)
    tracks = config.load_json(folder / "tracks.json")
    homographies = load_homographies(video)
    court = court_size(video)
    frames = []
    for record in tracks["frames"]:
        H = homographies.get(record["frame"])
        if H is None:
            continue                     # frames without a court mapping are left out
        ids, points = foot_points(record["players"], tracks["height"])
        players = []
        if points:
            for track_id, (x, y) in zip(ids, to_court(H, points)):
                players.append({"id": track_id, "x": round(float(x), 1), "y": round(float(y), 1)})
        frames.append({"frame": record["frame"], "players": players})
    positions = {"court": court.as_dict(), "frames": frames}
    config.save_json(folder / "positions.json", positions)
    return positions


def hoops_in_frame(video, frame_index):
    """Where the marked hoops are in one frame: {"left": [[x, y], [x, y]] or None, "right": ...}.

    The two points are the two ends of the rim, in pixels of that frame. None means the
    hoop was not marked, the frame has no court mapping, or the frame does not show it.
    """
    if video not in _hoops:
        path = config.data_dir(video) / "hoops.json"
        config.require_file(path, f"python src/select_hoops.py --video {video}")
        setup = load_setup(video)
        view_to_court = load_calibration(video)["view_to_court"]
        marked = {}
        for end, hoop in config.load_json(path)["hoops"].items():
            if hoop is not None and hoop["view"] in view_to_court:
                marked[end] = (view_to_court[hoop["view"]], hoop["rim_px"])
        _hoops[video] = {"marked": marked, "size": (setup["width"], setup["height"])}
    result = {"left": None, "right": None}
    H = court_homography(video, frame_index)
    if H is None:
        return result
    width, height = _hoops[video]["size"]
    for end, (view_matrix, rim_px) in _hoops[video]["marked"].items():
        rim = carry_points(view_matrix, H, rim_px, width, height)
        if rim[0] is not None and rim[1] is not None:
            result[end] = rim
    return result


def forget(video):
    """Drop what this module remembers about a video (the set-up tools call this after saving)."""
    _homographies.pop(video, None)
    _hoops.pop(video, None)


if __name__ == "__main__":
    args = parse_args()
    run(args.video, resume=args.resume)
