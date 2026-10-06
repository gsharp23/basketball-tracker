"""Stage 1, step 5: put every player on a top-down court diagram.

    python src/tactical_view.py --video clips/test.mp4
    python src/tactical_view.py --video clips/test.mp4 --no-video     (positions only)

Run these first
    detect_track.py                           finds and follows the players
    select_court.py, select_court_keypoints.py, court_mapper.py      the court mapping
    team_assigner.py and jersey_id.py         optional: without them the dots are grey
                                              and labelled with the tracking ID

What it does
    1. Positions. For every sampled frame it takes each player's foot point (the middle
       of the bottom edge of the box) and turns it into court feet with that frame's
       court mapping. Then it
         - drops positions more than OFF_COURT_FT outside the court (people behind the
           baseline, the crowd, a bad mapping),
         - drops the position of a box that has suddenly grown much taller than that
           player's boxes just before and after: it has taken in another player, so
           its bottom edge is somebody else's feet, and
         - smooths each player's path a little, because the bottom of a box wobbles as
           the legs move and jumps when the legs are hidden for a moment.
       The result is data/<video>/positions.json (feet; x from the left baseline, y from
       the near sideline). court_mapper.py writes a first, raw positions.json; this step
       replaces it with the cleaned one, so run this step after court_mapper.py.
    2. Video. outputs/<video>/tactical.mp4 shows the game on top and the court diagram
       underneath, one dot per player: coloured by team, with the jersey number when it
       was read and "id 7" (the tracking ID) when it was not.

Known limits
    A player whose feet are below the bottom of the picture gets no position (thin box,
    no dot). When a player's legs are hidden behind another player for more than a
    moment the dot is several feet too far from the camera; smoothing cannot fix that,
    and a box that is too SHORT is not dropped (a crouching player looks the same).
    A box that has grown to take in another player is only noticed while it lasts less
    than about 1.5 seconds. Neither YOLO nor the number reader is loaded here: no lock needed.
"""
import argparse
import os
import subprocess
import sys
import time

import cv2
import numpy as np

import config
import court_mapper
import court_model

# --- Positions ---
# A position further outside the court lines than this is dropped: it is someone behind
# the baseline, the crowd, or a bad court mapping. 3 ft still keeps a player who steps
# over a line, and it is the margin court_model.on_court() uses. People sitting or
# standing beside the court are usually well beyond it.
OFF_COURT_FT = 3.0
# Smoothing, counted in "looks" (sampled frames; 10 a second at the usual settings).
# First the middle value of 5 looks (2 either side): that throws out a one- or two-look
# jump. Then the average of 3 looks (1 either side): that takes off the remaining shake.
MEDIAN_REACH = 2
MEAN_REACH = 1
# A box more than TOO_TALL times as tall as the same player's usual box around that
# moment (the middle height of the boxes up to HEIGHT_REACH looks before and after) has
# taken in another player standing in front, and its position is dropped.
# Test clip: 25 of 930 boxes were over 1.2. Of 7 looked at, 5 had taken in a second
# player, 1 was not a player and 1 was a normal box. For one player who stood still
# behind a team-mate this took 15 ft of movement that never happened off a 42 ft total.
TOO_TALL = 1.2
HEIGHT_REACH = 15

# --- The video picture: game view on top, court diagram underneath ---
VIDEO_WIDTH = 1280
DIAGRAM_HEIGHT = 600
EDGE_FT = 5                 # floor shown around the court (more than OFF_COURT_FT, so every dot fits)
LEGEND_WIDTH = 150          # room kept free left and right of the court for the legend
DOT_RADIUS = 12
FONT = cv2.FONT_HERSHEY_SIMPLEX

# Colours are (blue, green, red).
FLOOR = (170, 205, 228)
OUT_OF_BOUNDS = (140, 175, 200)     # the floor outside the court lines, a little darker
SURROUND = (50, 50, 50)
LINE = (40, 40, 40)
HOOP_COLOUR = (0, 110, 255)
OVERLAY = (0, 255, 0)       # the court lines drawn over the game view
NO_TEAM = (150, 150, 150)
# teams.json stores the jersey colour as it looks on video, which is duller than the real
# shirt (a white one comes out light grey). For drawing, a clear colour with the same name
# reads better.
# A team whose name is not listed here is drawn in its stored colour.
CLEAR_COLOURS = {"white": (255, 255, 255), "black": (25, 25, 25), "grey": (110, 110, 110),
                 "red": (40, 40, 220), "orange": (0, 140, 255), "yellow": (0, 215, 255),
                 "green": (70, 170, 60), "blue": (225, 105, 30), "purple": (170, 60, 150)}

# Hoop and backboard for the diagram (the rim middle comes from court_model).
BACKBOARD_FROM_BASELINE_FT = 4
BACKBOARD_WIDTH_FT = 6
RIM_RADIUS_FT = 0.75


def parse_args():
    parser = argparse.ArgumentParser(description="Player positions in court feet, and a top-down video.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="video file (default clips/test.mp4)")
    parser.add_argument("--no-video", action="store_true", help="only write positions.json, no tactical.mp4")
    return parser.parse_args()


# ---------------------------------------------------------------- positions

def require_inputs(video):
    """Stop with a plain message if detection or the court mapping has not been done."""
    folder = config.data_dir(video)
    config.require_file(folder / "tracks.json", f"python src/main.py --video {video}")
    if not (folder / "court_frames.json").exists():
        steps = court_mapper.setup_missing(video)
        steps.append(f"The video has not been mapped to the court. Run:  python src/court_mapper.py --video {video}")
        sys.exit(f"There is no court mapping for {video} yet, so players cannot be placed on the court.\n"
                 "Do this first, in this order:\n" + "\n".join(f"  {n}. {step}" for n, step in enumerate(steps, 1)))


def load_tracks(video):
    """The contents of tracks.json. Stops with a plain message if detection found no frames."""
    tracks = config.load_json(config.data_dir(video) / "tracks.json")
    if not tracks["frames"]:
        sys.exit("tracks.json has no frames in it. Run the detection again:\n"
                 f"  python src/main.py --video {video} --from detect")
    return tracks


def window(path, frame, reach, stride):
    """The position at this frame together with its neighbours, up to `reach` looks either side.

    A neighbour is only used when its mirror image on the other side exists too. That
    way the result never leans towards the past or the future: the dot does not lag.
    """
    group = [path[frame]]
    for step in range(1, reach + 1):
        before, after = path.get(frame - step * stride), path.get(frame + step * stride)
        if before is not None and after is not None:
            group += [before, after]
    return group


def smooth(path, stride):
    """Smooth one player's path {frame: [x, y]}: first the middle value, then the average."""
    steady = {frame: np.median(window(path, frame, MEDIAN_REACH, stride), axis=0) for frame in path}
    return {frame: np.mean(window(steady, frame, MEAN_REACH, stride), axis=0) for frame in steady}


def grown_boxes(tracks):
    """The boxes that have grown to take in another player: a set of (tracking ID, frame).

    A player's box changes height slowly (the camera zooms, the player comes nearer).
    A box much taller than the same player's boxes just before and after has swallowed
    someone standing in front, and its bottom edge is that other player's feet.
    """
    heights = {}        # tracking ID -> {frame: box height}
    for record in tracks["frames"]:
        for player in record["players"]:
            x1, y1, x2, y2 = player["bbox"]
            if y2 < tracks["height"] - court_mapper.CUT_OFF_PX:      # cut-off boxes get no position anyway
                heights.setdefault(player["id"], {})[record["frame"]] = y2 - y1
    grown = set()
    reach = HEIGHT_REACH * tracks["stride"]
    for track_id, by_frame in heights.items():
        frames = sorted(by_frame)
        for i, frame in enumerate(frames):
            nearby = [by_frame[other] for other in frames[max(0, i - HEIGHT_REACH):i + HEIGHT_REACH + 1]
                      if abs(other - frame) <= reach]
            if len(nearby) >= 5 and by_frame[frame] > TOO_TALL * np.median(nearby):
                grown.add((track_id, frame))
    return grown


def path_length(paths, stride):
    """Feet travelled by all players together, adding up every step from one look to the next."""
    return sum(float(np.linalg.norm(path[frame] - path[frame - stride]))
               for path in paths.values() for frame in path if frame - stride in path)


def write_positions(video):
    """Work out every player's court position, clean it up and write positions.json.

    Returns (positions as written, counts for the summary).
    """
    folder = config.data_dir(video)
    tracks = load_tracks(video)
    homographies = court_mapper.load_homographies(video)
    length, width = court_mapper.court_size(video)

    # 1. Foot point -> court feet, one path per tracking ID: {id: {frame: [x, y]}}.
    paths = {}
    counts = {"frames": len(tracks["frames"]), "mapped": 0, "boxes": 0, "no_mapping": 0,
              "cut_off": 0, "off_court": 0, "off_court_ids": {}, "grown": 0}
    grown = grown_boxes(tracks)
    for record in tracks["frames"]:
        counts["boxes"] += len(record["players"])
        H = homographies.get(record["frame"])
        if H is None:
            counts["no_mapping"] += len(record["players"])
            continue
        counts["mapped"] += 1
        # foot_points leaves out boxes that touch the bottom of the picture (feet cut off).
        ids, points = court_mapper.foot_points(record["players"], tracks["height"])
        counts["cut_off"] += len(record["players"]) - len(ids)
        if not points:
            continue
        for track_id, (x, y) in zip(ids, court_mapper.to_court(H, points)):
            # 2. Drop a box that has taken in another player, and what is well outside the court.
            if (track_id, record["frame"]) in grown:
                counts["grown"] += 1
            elif court_model.on_court(x, y, length, width, OFF_COURT_FT):
                paths.setdefault(track_id, {})[record["frame"]] = np.array([x, y])
            else:
                counts["off_court"] += 1
                counts["off_court_ids"][track_id] = counts["off_court_ids"].get(track_id, 0) + 1

    # 3. Smooth each path.
    smoothed = {track_id: smooth(path, tracks["stride"]) for track_id, path in paths.items()}
    counts["kept"] = sum(len(path) for path in paths.values())
    counts["feet_before"] = path_length(paths, tracks["stride"])
    counts["feet_after"] = path_length(smoothed, tracks["stride"])

    frames = []
    for record in tracks["frames"]:
        if homographies.get(record["frame"]) is None:
            continue                    # frames without a court mapping are left out
        players = []
        for player in record["players"]:
            spot = smoothed.get(player["id"], {}).get(record["frame"])
            if spot is not None:
                players.append({"id": player["id"], "x": round(float(spot[0]), 1), "y": round(float(spot[1]), 1)})
        frames.append({"frame": record["frame"], "players": players})
    positions = {"court": {"length_ft": length, "width_ft": width}, "frames": frames,
                 # not part of the contract: a note of what was done to the numbers
                 "cleaned": {"off_court_margin_ft": OFF_COURT_FT, "dropped_off_court": counts["off_court"],
                             "dropped_grown_boxes": counts["grown"],
                             "smoothing": f"middle value of {2 * MEDIAN_REACH + 1} looks, "
                                          f"then average of {2 * MEAN_REACH + 1}"}}
    config.save_json(folder / "positions.json", positions)      # save_json reads the file back to check it
    return positions, counts


def print_positions_summary(video, positions, counts):
    court = positions["court"]
    print()
    print(f"Positions for {video}  ({court['length_ft']} x {court['width_ft']} ft court)")
    print(f"  frames with a court mapping:  {counts['mapped']} of {counts['frames']}")
    print(f"  player boxes:                 {counts['boxes']}")
    print(f"    placed on the court:        {counts['kept']}")
    print(f"    no position, feet below the bottom of the picture: {counts['cut_off']}")
    print(f"    dropped, more than {OFF_COURT_FT:g} ft outside the court:   {counts['off_court']}")
    print(f"    dropped, box had taken in another player:  {counts['grown']}")
    if counts["no_mapping"]:
        print(f"    no position, frame has no court mapping:  {counts['no_mapping']}")
    if counts["off_court_ids"]:
        worst = sorted(counts["off_court_ids"].items(), key=lambda item: -item[1])[:8]
        print("    dropped positions by tracking ID:  " + ", ".join(f"id {i}: {n}" for i, n in worst))
    if counts["feet_before"]:
        print(f"  distance covered by all players together: {counts['feet_before']:.0f} ft before smoothing, "
              f"{counts['feet_after']:.0f} ft after")
        print("    (most of the difference is shake in the boxes, not running)")
    print(f"  Wrote {config.data_dir(video) / 'positions.json'}")


# ---------------------------------------------------------------- teams and numbers

def note_if_older(video, path):
    """Say so when teams.json or jerseys.json was made before the latest detection."""
    if path.stat().st_mtime < (config.data_dir(video) / "tracks.json").stat().st_mtime:
        # Tracking IDs change when detection is run again, so the file is about other IDs.
        print(f"NOTE: {path.name} is older than tracks.json, so it may not match it. "
              f"To bring everything up to date:  python src/main.py --video {video}")


def load_team_colours(video):
    """({tracking ID: dot colour}, [(team name, colour), ...]) from teams.json. Empty without it."""
    path = config.data_dir(video) / "teams.json"
    if not path.exists():
        print(f"NOTE: no teams yet, so every dot is grey. To colour them:  python src/team_assigner.py --video {video}")
        return {}, []
    note_if_older(video, path)
    saved = config.load_json(path)
    colours = {key: CLEAR_COLOURS.get(team["label"], tuple(team["color_bgr"])) for key, team in saved["teams"].items()}
    by_track = {int(track_id): colours[str(team)] for track_id, team in saved["tracks"].items() if team is not None}
    legend = [(saved["teams"][key]["label"], colours[key]) for key in sorted(saved["teams"])]
    return by_track, legend


def load_numbers(video):
    """{tracking ID: jersey number as text} from jerseys.json. Empty without it."""
    path = config.data_dir(video) / "jerseys.json"
    if not path.exists():
        print("NOTE: no jersey numbers yet, so dots show the tracking ID. To read the numbers:\n"
              f"      python src/main.py --video {video}")
        return {}
    note_if_older(video, path)
    saved = config.load_json(path)
    return {int(track_id): entry["number"] for track_id, entry in saved["tracks"].items() if entry["number"]}


def text_colour(colour):
    """Black writing on a light colour, white writing on a dark one."""
    blue, green, red = colour
    return (0, 0, 0) if 0.114 * blue + 0.587 * green + 0.299 * red > 140 else (255, 255, 255)


# ---------------------------------------------------------------- drawing

def diagram_layout(length, width):
    """How the court sits in the diagram: pixels per foot and where its top-left corner is."""
    scale = min(DIAGRAM_HEIGHT / (width + 2 * EDGE_FT), (VIDEO_WIDTH - 2 * LEGEND_WIDTH) / (length + 2 * EDGE_FT))
    return {"scale": scale, "left": (VIDEO_WIDTH - length * scale) / 2,
            "top": (DIAGRAM_HEIGHT - width * scale) / 2, "court_width": width}


def diagram_point(layout, x, y):
    """Court feet -> pixel in the diagram.

    The near sideline (y = 0, the camera side) is at the bottom and the left baseline
    on the left, so the diagram is the way round the camera sees the court.
    """
    return (int(round(layout["left"] + x * layout["scale"])),
            int(round(layout["top"] + (layout["court_width"] - y) * layout["scale"])))


def blank_diagram(length, width, layout, legend):
    """The court diagram without players. Drawn once; every frame starts from a copy."""
    diagram = np.full((DIAGRAM_HEIGHT, VIDEO_WIDTH, 3), SURROUND, np.uint8)
    cv2.rectangle(diagram, diagram_point(layout, -EDGE_FT, width + EDGE_FT),
                  diagram_point(layout, length + EDGE_FT, -EDGE_FT), OUT_OF_BOUNDS, -1)
    cv2.rectangle(diagram, diagram_point(layout, 0, width), diagram_point(layout, length, 0), FLOOR, -1)
    for line in court_model.court_lines(length, width):
        pixels = np.array([diagram_point(layout, x, y) for x, y in line], np.int32)
        cv2.polylines(diagram, [pixels], False, LINE, 2, cv2.LINE_AA)
    middle = width / 2
    for baseline_x, direction in ((0, +1), (length, -1)):
        board_x = baseline_x + direction * BACKBOARD_FROM_BASELINE_FT
        hoop_x = baseline_x + direction * court_model.HOOP_FROM_BASELINE_FT
        cv2.line(diagram, diagram_point(layout, board_x, middle - BACKBOARD_WIDTH_FT / 2),
                 diagram_point(layout, board_x, middle + BACKBOARD_WIDTH_FT / 2), LINE, 3)
        cv2.circle(diagram, diagram_point(layout, hoop_x, middle),
                   int(round(RIM_RADIUS_FT * layout["scale"])), HOOP_COLOUR, 2, cv2.LINE_AA)

    # Legend: the teams on the left, what the labels mean on the right.
    y = 40
    for name, colour in legend + [("no team yet", NO_TEAM)]:
        cv2.circle(diagram, (24, y - 5), 9, colour, -1, cv2.LINE_AA)
        cv2.circle(diagram, (24, y - 5), 9, text_colour(colour), 1, cv2.LINE_AA)
        cv2.putText(diagram, name, (42, y), FONT, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        y += 30
    right = VIDEO_WIDTH - LEGEND_WIDTH + 12
    for row, text in enumerate(["15 = jersey number", "id 7 = number not", "       read yet", "",
                                "thin box = feet not", "seen or off court:", "no dot"]):
        cv2.putText(diagram, text, (right, 40 + 22 * row), FONT, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return diagram


def draw_diagram(blank, layout, players, colours, numbers, mapped):
    """The court diagram for one frame: a dot per player (players as in positions.json)."""
    diagram = blank.copy()
    if not mapped:
        cv2.putText(diagram, "no court mapping at this moment", (VIDEO_WIDTH // 2 - 250, DIAGRAM_HEIGHT // 2),
                    FONT, 1.0, (0, 0, 255), 2, cv2.LINE_AA)
    for player in players:
        colour = colours.get(player["id"], NO_TEAM)
        centre = diagram_point(layout, player["x"], player["y"])
        cv2.circle(diagram, centre, DOT_RADIUS, colour, -1, cv2.LINE_AA)
        cv2.circle(diagram, centre, DOT_RADIUS, text_colour(colour), 1, cv2.LINE_AA)
        if player["id"] in numbers:
            # jersey number inside the dot
            text = numbers[player["id"]]
            (w, h), _ = cv2.getTextSize(text, FONT, 0.5, 2)
            cv2.putText(diagram, text, (centre[0] - w // 2, centre[1] + h // 2), FONT, 0.5,
                        text_colour(colour), 2, cv2.LINE_AA)
        else:
            # tracking ID above the dot (written twice: a floor-coloured edge keeps it
            # readable where it crosses a court line)
            text = f"id {player['id']}"
            (w, h), _ = cv2.getTextSize(text, FONT, 0.45, 1)
            corner = (centre[0] - w // 2, centre[1] - DOT_RADIUS - 4)
            cv2.putText(diagram, text, corner, FONT, 0.45, FLOOR, 4, cv2.LINE_AA)
            cv2.putText(diagram, text, corner, FONT, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    return diagram


def draw_game_view(frame, size, record, H, placed, colours, numbers, court, clock):
    """The video frame, shrunk, with the court lines and a labelled box on every player.

    placed is the set of tracking IDs that have a dot on the diagram in this frame.
    They get a thick box and a mark at the feet; the others get a thin box.
    """
    view = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    shrink = size[0] / frame.shape[1]
    boxes = [[int(v * shrink) for v in player["bbox"]] for player in record["players"]]
    if H is not None:
        without_lines = view.copy()
        # H expects full-size pixels: first scale the small picture's pixels back up.
        grow = np.diag([1 / shrink, 1 / shrink, 1.0])
        court_model.draw_court(view, H @ grow, court[0], court[1], OVERLAY, 1)
        # The lines are on the floor, so they should not run across the players.
        for x1, y1, x2, y2 in boxes:
            view[y1:y2, x1:x2] = without_lines[y1:y2, x1:x2]
    for player, (x1, y1, x2, y2) in zip(record["players"], boxes):
        colour = colours.get(player["id"], NO_TEAM)
        label = numbers.get(player["id"], f"id {player['id']}")
        on_diagram = player["id"] in placed
        # A dark edge under the coloured box, so a white box shows on the light floor.
        cv2.rectangle(view, (x1, y1), (x2, y2), (0, 0, 0), 4 if on_diagram else 2)
        cv2.rectangle(view, (x1, y1), (x2, y2), colour, 2 if on_diagram else 1)
        (w, h), _ = cv2.getTextSize(label, FONT, 0.55, 2)
        cv2.rectangle(view, (x1 - 1, y1 - h - 8), (x1 + w + 5, y1), (0, 0, 0), -1)
        cv2.rectangle(view, (x1, y1 - h - 7), (x1 + w + 4, y1 - 1), colour, -1)
        cv2.putText(view, label, (x1 + 2, y1 - 4), FONT, 0.55, text_colour(colour), 2, cv2.LINE_AA)
        if on_diagram:
            foot = ((x1 + x2) // 2, y2)
            cv2.circle(view, foot, 5, (0, 0, 0), -1, cv2.LINE_AA)
            cv2.circle(view, foot, 3, colour, -1, cv2.LINE_AA)
    (w, h), _ = cv2.getTextSize(clock, FONT, 0.7, 2)
    cv2.rectangle(view, (6, 6), (14 + w, 18 + h), (0, 0, 0), -1)
    cv2.putText(view, clock, (10, 12 + h), FONT, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return view


# ---------------------------------------------------------------- the video

def open_video_writer(path, size, fps):
    """Start ffmpeg; we feed it raw pictures and it writes an H.264 mp4 that Windows plays.

    The pictures go in as "yuv420p", the colour layout H.264 stores anyway. It is half
    the size of plain blue-green-red pictures, which were measured 1.4 to 6 times
    slower to send on this laptop.
    """
    command = [config.ffmpeg_path(), "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "%dx%d" % size, "-r", f"{fps:.3f}", "-i", "-",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(path)]
    # start_new_session keeps Ctrl+C away from ffmpeg, so we can close its file properly.
    return subprocess.Popen(command, stdin=subprocess.PIPE, start_new_session=True)


def close_video_writer(writer):
    """Tell ffmpeg there are no more pictures and wait for it. True if it finished its file."""
    try:
        writer.stdin.close()
    except BrokenPipeError:
        pass                # ffmpeg has already gone; wait() below reports that
    return writer.wait() == 0


def count_frames(path):
    """How many frames of a video can really be read (the SD card has damaged files before)."""
    capture = cv2.VideoCapture(str(path))
    count = 0
    while capture.grab():
        count += 1
    capture.release()
    return count


def render(video, positions):
    """Write outputs/<video>/tactical.mp4. Returns its path."""
    source = config.require_video(video)
    tracks = load_tracks(video)
    homographies = court_mapper.load_homographies(video)
    court = (positions["court"]["length_ft"], positions["court"]["width_ft"])
    colours, legend = load_team_colours(video)
    numbers = load_numbers(video)
    records = {record["frame"]: record for record in tracks["frames"]}
    spots = {entry["frame"]: entry["players"] for entry in positions["frames"]}

    layout = diagram_layout(*court)
    blank = blank_diagram(court[0], court[1], layout, legend)
    # Game view: full width, same shape as the video, an even number of rows (H.264 needs that).
    view_size = (VIDEO_WIDTH, 2 * round(VIDEO_WIDTH * tracks["height"] / tracks["width"] / 2))
    size = (VIDEO_WIDTH, view_size[1] + DIAGRAM_HEIGHT)
    target = config.output_dir(video) / "tactical.mp4"
    unfinished = config.output_dir(video) / "tactical.part.mp4"     # renamed once it is complete

    print(f"Making the video ({len(records)} frames, {size[0]} x {size[1]}) ...", flush=True)
    writer = open_video_writer(unfinished, size, tracks["fps"] / tracks["stride"])
    capture = cv2.VideoCapture(str(source))
    last = max(records)
    index, written = -1, 0
    started = last_print = time.time()
    problem = None
    try:
        # Read the video from the start. grab() only steps forward; retrieve() makes the
        # picture, and we ask for it only on the sampled frames. Stop at the first frame
        # that cannot be read, whatever the file says its length is.
        while index < last and capture.grab():
            index += 1
            if index not in records:
                continue
            ok, frame = capture.retrieve()
            if not ok:
                break
            players = spots.get(index, [])
            seconds = int(index / tracks["fps"])
            clock = f"{seconds // 60:02d}:{seconds % 60:02d}"
            view = draw_game_view(frame, view_size, records[index], homographies.get(index),
                                  {player["id"] for player in players}, colours, numbers, court, clock)
            diagram = draw_diagram(blank, layout, players, colours, numbers, index in spots)
            picture = np.vstack([view, diagram])
            writer.stdin.write(cv2.cvtColor(picture, cv2.COLOR_BGR2YUV_I420).tobytes())
            written += 1
            if time.time() - last_print >= 30:
                last_print = time.time()
                rate = written / (last_print - started)
                print(f"  {written} of {len(records)} frames ({rate:.1f} per second, "
                      f"about {(len(records) - written) / rate / 60:.0f} min to go)", flush=True)
        if written == 0:
            problem = f"No frame of {source} could be read."
        elif not close_video_writer(writer):
            problem = "ffmpeg could not write the video (is the disk full?)."
        elif count_frames(unfinished) != written:
            problem = "The video did not read back whole. The disk may be failing."
    except KeyboardInterrupt:
        problem = "Stopped by Ctrl+C before the video was finished."
    except BrokenPipeError:
        problem = "ffmpeg stopped while writing the video (is the disk full?)."
    capture.release()
    if problem:
        close_video_writer(writer)
        unfinished.unlink(missing_ok=True)
        sys.exit(f"{problem}\npositions.json is already written. To make the video, run the command again:\n"
                 f"  python src/tactical_view.py --video {video}")
    os.replace(unfinished, target)
    seconds = time.time() - started
    if written < len(records):
        print(f"  NOTE: {source.name} could not be read past frame {index}, so the last "
              f"{len(records) - written} sampled frames are not in the video.")
    print(f"  Wrote {target}")
    print(f"  {written} frames, {written / (tracks['fps'] / tracks['stride']):.0f} seconds of play, "
          f"made in {seconds:.0f} sec ({written / seconds:.1f} frames per second)")
    return target


def run(video=config.DEFAULT_VIDEO, make_video=True):
    """Do the whole step for one video. Returns the positions (as in positions.json)."""
    if make_video:
        config.require_video(video)
    require_inputs(video)
    calibration = config.data_dir(video) / "court_calibration.json"
    if calibration.exists() and config.load_json(calibration).get("provisional"):
        print("NOTE: the court landmarks for this video are PROVISIONAL (not clicked by you), so the")
        print(f"      positions are too. Click them yourself:  python src/select_court_keypoints.py --video {video}")
    positions, counts = write_positions(video)
    print_positions_summary(video, positions, counts)
    if make_video:
        render(video, positions)
    return positions


if __name__ == "__main__":
    args = parse_args()
    try:
        run(args.video, make_video=not args.no_video)
    except KeyboardInterrupt:
        sys.exit("\nStopped by Ctrl+C. Run the command again to start over.")
