"""Shots: the coach tags each shot, the tool adds where it was taken from and what it was worth.

    python src/shots.py --video clips/test.mp4                 list the shots, print the points table,
                                                               save the scoring heat maps
    python src/shots.py --video clips/test.mp4 --add 1:23.4 --team <team> --number 5 --made
    python src/shots.py --video clips/test.mp4 --delete 3
    python src/shots.py --video clips/test.mp4 --undo

The ball cannot be found reliably in the video, so nothing here is detected: the coach
says when a shot was taken, who took it and whether it went in (on the "Tag shots" screen of the
dashboard, web/shots.html through api_shots.py, or with --add). The tool then fills in
    the moment      the nearest moment of the video that was looked at (about 10 a second)
    the position    where that player stood, from positions.json; if he has no position
                    just then, the nearest moment within about a second; otherwise the
                    shot is kept with "position unknown"
    the points      free throw 1; field goal 2 or 3, from the position and the three-point
                    line of the NEARER hoop. The coach can always overrule it.

The tool does not know which hoop a team shoots at. Every shot is measured to the
NEARER hoop, which is the hoop shot at for any shot from the team's own front court.
The one case it gets wrong is a heave from deep in the team's back court (inside the
three-point line of its OWN hoop, more than 60 ft out): that is taken for a 2, and the
coach sets 3 points by hand. (An earlier version asked the coach for the ends in each
period instead. A period left unchanged at half time then turned every 2 into a 3, so it
was taken out. An older shots.json may still hold an "ends" entry; it is ignored.)

data/<video>/shots.json
    "shots"    a list, in game order, of
        "id"              number of the shot, never reused while the file exists
        "time_s", "frame" the moment, in seconds and as a frame number of the game video
        "period"          "1", "2", "3", "4" or "OT": a note for the coach; nothing is worked out from it
        "team"            the team's label, as in teams.json
        "number"          jersey number as text, or null when it is not known
        "tag"             the player tag (tracking ID) that was picked in the picture, or
                          null when team and number were typed
        "kind"            "field goal" or "free throw"
        "made"            true or false
        "x_ft", "y_ft"    where it was taken from, in court feet; null = position unknown
        "position_frame"  the frame that position was read at
        "worth"           1, 2 or 3: the points it counts if made
        "worth_from"      "position" (worked out), "coach" (chosen by hand) or "free throw"
        "source"          "coach" for a hand-tagged shot. A later automatic shot detector
                          can write the same file with its own word here.
data/<video>/shots_undo.json     the file as it was before the last change, for undo
outputs/<video>/scoring_heat_map_<team>.png     where each team's points came from

This file has no web page in it and can be tested on its own.
"""
import argparse
import bisect
import datetime
import contextlib
import io
import math

import cv2
import numpy as np

import config
import court_model
import stats            # for reading the roster and matching it to the teams

PERIODS = ["1", "2", "3", "4", "OT"]
KINDS = ["field goal", "free throw"]

# How far from the chosen moment a player's position may be borrowed, in seconds.
NEAR_SECONDS = 1.0
# A position is typically 1 to 2 ft off, more when the legs are hidden. A shot this close
# to the three-point line is pointed out, so the coach can check whether it was a 2 or a 3.
NEAR_LINE_FT = 3.0
# The scoring heat map only gets shading from this many made shots on; below that every
# shot is just drawn as a mark, because a smooth picture of a handful of shots misleads.
SHADING_FROM = 20
# Size of one square of the shading, in feet.
HEAT_CELL_FT = 2

UNDO_FILE = "shots_undo.json"
NOT_READY = ("This game has not been processed far enough to tag shots.\n"
             "Open the Games screen and start it: shots can be tagged once the players have been found, "
             "sorted into teams and their numbers read.")
CHOOSE_WORTH = ("The position of this shot is not known, so the tool cannot tell a 2 from a 3.\n"
                "Choose \"2 points\" or \"3 points\" and save it again. (On the command line: add --worth 2 or --worth 3.)")


class Problem(Exception):
    """Something the coach can put right. The text is plain and says what to do."""


# ---------------------------------------------------------------- reading the game's files

def game_folder(video):
    """data/<video>/ without creating it (config.data_dir would create it just by looking)."""
    if not video:
        raise Problem("Choose a game first, in the Game box at the top of the page.")
    return config.DATA_DIR / config.video_stem(video)


def read_json(path):
    """config.load_json, but a damaged file becomes a Problem instead of stopping the program."""
    try:
        return config.load_json(path)
    except SystemExit as stop:
        raise Problem(str(stop))


def game_info(video):
    """What the coach said about a game, from data/<stem>/game_info.json, every key present.

    {"opponent": who the game was against ("" until typed), "date": "2026-01-24" (default: the day the
    video file was last changed), "our_team": the label of our team in this game ("white") or None}.
    Shared by the Games, Game results, Opponent and Tag shots screens.
    """
    saved = read_json(game_folder(video) / "game_info.json") if (game_folder(video) / "game_info.json").exists() else {}
    saved = saved if isinstance(saved, dict) else {}
    try:
        changed = datetime.date.fromtimestamp(config.video_path(video).stat().st_mtime).isoformat()
    except OSError:
        changed = ""
    return {"opponent": str(saved.get("opponent") or "").strip(), "date": str(saved.get("date") or changed),
            "date_is_guess": not saved.get("date"),          # the file's date, not the game's, until typed
            "our_team": saved.get("our_team") or None}


def save_game_info(video, changes):
    """Change some keys of game_info.json (other keys are kept) and return the whole thing."""
    path = config.data_dir(video) / "game_info.json"          # makes data/<stem>/ if it is missing
    saved = read_json(path) if path.exists() else {}
    saved = saved if isinstance(saved, dict) else {}
    saved.update(changes)
    config.save_json(path, saved, indent=2)
    return game_info(video)


def court_size(video):
    """(length, width) of this game's court in feet, as chosen when the court was marked."""
    setup = game_folder(video) / "court_setup.json"
    if setup.exists():
        court = read_json(setup)["court"]
        return court["length_ft"], court["width_ft"]
    return config.COURT_LENGTH_FT, config.COURT_WIDTH_FT


def load_teams(video):
    """Who is who in this game, from teams.json, jerseys.json and the roster. Small and quick.

    Returns
        "labels"    the two team labels, as teams.json has them
        "colours"   their jersey colours (blue, green, red), for drawing
        "tags"      {player tag: (team label or None, jersey number or None)}
        "names"     {(team label, jersey number): player name}, from roster.json when it fits
        "roster_names"  {team label: the roster's name for that team}, when the roster fits
    """
    folder = game_folder(video)
    for name in ("tracks.json", "teams.json", "jerseys.json"):
        if not (folder / name).exists():
            raise Problem(NOT_READY)
    teams, jerseys = read_json(folder / "teams.json"), read_json(folder / "jerseys.json")
    labels = [teams["teams"][str(index)]["label"] for index in (0, 1)]
    tags, numbered = {}, []
    for key, team in teams["tracks"].items():
        number = jerseys["tracks"].get(key, {}).get("number")
        tags[int(key)] = (labels[team] if team is not None else None, number)
        if team is not None and number is not None:
            numbered.append({"team": team, "number": number})
    # The roster does not say which colour a team wears; stats.match_roster works it out
    # from the numbers. It prints a note when it cannot, which is not wanted on every click.
    with contextlib.redirect_stdout(io.StringIO()):
        matched = stats.match_roster(numbered, stats.load_roster())
    names = {(labels[index], number): name
             for index, (_, players) in matched.items() for number, name in players.items()}
    return {"labels": labels, "colours": [teams["teams"][str(index)]["color_bgr"] for index in (0, 1)],
            "tags": tags, "names": names,
            "roster_names": {labels[index]: roster_name for index, (roster_name, _) in matched.items()}}


_last_game = {}         # the moments of the game read last. Only one is kept: a full game is big.


def load_moments(video):
    """Every moment of the video that was looked at, with the player boxes and court positions.

    Returns
        "fps", "stride"   frames per second of the game video; every stride-th frame was looked at
        "width", "height" the size of the game video's pictures, which the boxes are measured in
        "frames"          the frame numbers looked at, in order. A "look" is an index into this list.
        "boxes"           {frame: [(player tag, left, top, right, bottom), ...]} in video pixels
        "spots"           {frame: {player tag: (x, y)}} in court feet (empty if the court is not mapped yet)
        "positions"       True when positions.json is there and finished
    Reading a full game takes a few seconds, so the result is kept until the files change.
    """
    folder = game_folder(video)
    paths = [folder / "tracks.json", folder / "positions.json"]
    stamp = [(path.stat().st_mtime, path.stat().st_size) if path.exists() else None for path in paths]
    if _last_game.get("video") == video and _last_game.get("stamp") == stamp:
        return _last_game["moments"]
    if stamp[0] is None:
        raise Problem(NOT_READY)
    tracks = read_json(paths[0])
    if not tracks["frames"]:
        raise Problem("The player detection of this game is empty. Process the game again from the Games screen.")
    moments = {"fps": tracks["fps"], "stride": tracks["stride"], "width": tracks["width"], "height": tracks["height"],
               "frames": [], "boxes": {}, "spots": {}, "positions": False}
    for record in tracks["frames"]:
        moments["frames"].append(record["frame"])
        moments["boxes"][record["frame"]] = [(player["id"], *player["bbox"]) for player in record["players"]]
    del tracks
    if stamp[1] is not None:
        positions = read_json(paths[1])
        # The court stage writes a first, raw positions.json; only the cleaned one is used (as in stats.py).
        if "cleaned" in positions:
            moments["positions"] = True
            for record in positions["frames"]:
                moments["spots"][record["frame"]] = {player["id"]: (player["x"], player["y"]) for player in record["players"]}
    _last_game.clear()
    _last_game.update({"video": video, "stamp": stamp, "moments": moments})
    return moments


# ---------------------------------------------------------------- time and moments

def parse_time(text):
    """ "1:23.4", "1:23", "83.4" or a number -> seconds."""
    try:
        if isinstance(text, (int, float)):
            seconds = float(text)
        else:
            parts = [float(part) for part in str(text).strip().split(":")]
            if not 1 <= len(parts) <= 3:
                raise ValueError
            seconds = sum(part * 60 ** place for place, part in enumerate(reversed(parts)))
        if seconds < 0 or not math.isfinite(seconds):
            raise ValueError
        return seconds
    except ValueError:
        raise Problem(f"Could not read the time \"{text}\". Type it as minutes:seconds, like 1:23 or 1:23.4")


def show_time(seconds):
    """83.44 -> "1:23.4"."""
    tenths = int(round(seconds * 10))
    return f"{tenths // 600}:{tenths % 600 // 10:02d}.{tenths % 10}"


def watch_video(video):
    """Which video the coach watches while tagging: (its path, "game" or "tracked").

    The game video itself when the browser can play it (H.264 in an mp4): it is smooth
    (the tracked video has only 10 pictures a second, too few to see a shot go in), and
    the tracked video writes a player tag above each box that is easily taken for a
    jersey number. A game video the browser cannot play is replaced by the tracked
    video, which is always H.264.
    """
    clip = config.video_path(video)
    tracked = config.OUTPUTS_DIR / config.video_stem(video) / "tracked.mp4"
    capture = cv2.VideoCapture(str(clip))
    fourcc = int(capture.get(cv2.CAP_PROP_FOURCC))
    capture.release()
    # a file OpenCV cannot open answers -1 here: then it is no video the browser can play either
    codec = fourcc.to_bytes(4, "little").decode("ascii", "replace").lower() if fourcc > 0 else ""
    if (clip.suffix.lower() in (".mp4", ".m4v") and codec in ("h264", "avc1")) or not tracked.exists():
        return clip, "game"
    return tracked, "tracked"


def look_at(moments, seconds, watching="game"):
    """The look (index into moments["frames"]) nearest to a time of the video being watched."""
    frames = moments["frames"]
    if watching == "tracked":
        # the tracked video has exactly one picture per look
        index = round(seconds * moments["fps"] / moments["stride"])
    else:
        frame = seconds * moments["fps"]
        index = bisect.bisect_left(frames, frame)
        if index > 0 and (index == len(frames) or frame - frames[index - 1] <= frames[index] - frame):
            index -= 1
    return min(max(index, 0), len(frames) - 1)


def look_time(moments, index, watching="game"):
    """The time in the video being watched at which a look is shown, in seconds."""
    if watching == "tracked":
        return index * moments["stride"] / moments["fps"]
    return moments["frames"][index] / moments["fps"]


def players_at(video, index):
    """The players boxed at one look: a list of {"tag", "team", "number", "box"} (box in video pixels)."""
    moments, teams = load_moments(video), load_teams(video)
    players = []
    for tag, *box in moments["boxes"][moments["frames"][index]]:
        team, number = teams["tags"].get(tag, (None, None))
        players.append({"tag": tag, "team": team, "number": number, "box": box})
    return players


def tag_at_point(players, x, y):
    """The player whose box contains a click (video pixels), or None. Of several, the one whose middle is nearest."""
    inside = [player for player in players
              if player["box"][0] <= x <= player["box"][2] and player["box"][1] <= y <= player["box"][3]]
    if not inside:
        return None
    nearest = min(inside, key=lambda player: math.dist(
        (x, y), ((player["box"][0] + player["box"][2]) / 2, (player["box"][1] + player["box"][3]) / 2)))
    return nearest["tag"]


def moment_picture(video, index, chosen=None):
    """The video picture at one look, full size, with every player boxed and labelled, for the browser.

    The label is the jersey number ("#5") on a plate in the team's colour, or "tag 17"
    (the player tag) when no number was read. The chosen player gets a thick yellow box.
    """
    moments, teams = load_moments(video), load_teams(video)
    capture = cv2.VideoCapture(str(config.video_path(video)))
    capture.set(cv2.CAP_PROP_POS_FRAMES, moments["frames"][index])
    ok, picture = capture.read()
    capture.release()
    if not ok:
        raise Problem("That moment could not be read from the video file. Try a moment a little earlier or later.\n"
                      "If no moment can be shown, the video file is damaged: copy it to the clips folder again.")
    unit = picture.shape[1] / 1920          # line widths and text sizes are for a 1920-wide picture
    for player in players_at(video, index):
        left, top, right, bottom = (int(round(value)) for value in player["box"])
        colour = (150, 150, 150)            # grey: no team
        if player["team"] is not None:
            colour = tuple(int(value) for value in teams["colours"][teams["labels"].index(player["team"])])
        is_chosen = player["tag"] == chosen
        # a black line under the coloured one, so the box shows on any background
        cv2.rectangle(picture, (left, top), (right, bottom), (0, 0, 0), int(round((12 if is_chosen else 7) * unit)))
        cv2.rectangle(picture, (left, top), (right, bottom), (0, 255, 255) if is_chosen else colour,
                      int(round((8 if is_chosen else 3) * unit)))
        text = f"#{player['number']}" if player["number"] is not None else f"tag {player['tag']}"
        scale = (1.5 if player["number"] is not None else 1.0) * unit
        (text_width, text_height), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 3)
        plate_top = max(0, top - text_height - int(18 * unit))
        cv2.rectangle(picture, (left, plate_top), (left + text_width + int(14 * unit), plate_top + text_height + int(16 * unit)),
                      (0, 0, 0), -1)
        cv2.rectangle(picture, (left + 3, plate_top + 3),
                      (left + text_width + int(14 * unit) - 3, plate_top + text_height + int(16 * unit) - 3), colour, -1)
        ink = (0, 0, 0) if sum(colour) > 380 else (255, 255, 255)
        cv2.putText(picture, text, (left + int(7 * unit), plate_top + text_height + int(8 * unit)),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, ink, 3, cv2.LINE_AA)
    return cv2.cvtColor(picture, cv2.COLOR_BGR2RGB)


def find_position(moments, teams, index, tag, team, number):
    """Where the shooter stood: (x, y, frame) in court feet, or None when it is not known.

    Looks at the chosen moment first, then further and further away from it, up to
    NEAR_SECONDS. The player tag that was picked is tried first, then the other tags
    that carry the same team and jersey number (one real player usually has several).
    """
    tags = [tag] if tag is not None else []
    if number is not None:
        tags += [other for other, who in teams["tags"].items() if who == (team, number) and other != tag]
    frames = moments["frames"]
    reach = int(round(NEAR_SECONDS * moments["fps"] / moments["stride"]))
    for step in range(reach + 1):
        # at the same distance the earlier moment first: before the jump the feet are on the floor
        for look in ([index] if step == 0 else [index - step, index + step]):
            if 0 <= look < len(frames):
                spots = moments["spots"].get(frames[look], {})
                for candidate in tags:
                    if candidate in spots:
                        return spots[candidate][0], spots[candidate][1], frames[look]
    return None


# ---------------------------------------------------------------- the court: which hoop, 2 or 3

def fold(x, y, end, length, width):
    """A court position as seen by a shooter facing the hoop at that end.

    Returns (feet from the baseline under that hoop, feet across from the sideline on
    his left). Turning the right end round like this lays both ends on top of each
    other, so shots at either hoop can be shown on one half court.
    """
    return (x, y) if end == "left" else (length - x, width - y)


def feet_outside_three(from_baseline, across, width):
    """How far outside the three-point line a spot is, in feet (negative: inside the line)."""
    sideways = abs(across - width / 2)
    if from_baseline <= court_model.HOOP_FROM_BASELINE_FT:
        return sideways - court_model.THREE_POINT_FT        # beside the hoop the line runs straight to the baseline
    return math.hypot(from_baseline - court_model.HOOP_FROM_BASELINE_FT, sideways) - court_model.THREE_POINT_FT


def judge(shot, court):
    """What a shot's position says about it, or None when the position is unknown.

    The shot is measured to the NEARER hoop (see the top of this file for the one rare
    case where that is the wrong hoop).

    Returns
        "end"                 the end of the nearer hoop, "left" or "right" as the camera sees the court
        "from_baseline", "across"   the position folded onto one half court (see fold)
        "from_hoop_ft"        distance to the middle of that hoop
        "outside_three_ft"    feet outside the three-point line (negative: inside)
        "worth"               2 or 3
    """
    if shot["x_ft"] is None:
        return None
    length, width = court
    end = "left" if shot["x_ft"] < length / 2 else "right"
    from_baseline, across = fold(shot["x_ft"], shot["y_ft"], end, length, width)
    outside = feet_outside_three(from_baseline, across, width)
    return {"end": end, "from_baseline": from_baseline, "across": across,
            "from_hoop_ft": math.hypot(from_baseline - court_model.HOOP_FROM_BASELINE_FT, across - width / 2),
            "outside_three_ft": outside, "worth": 3 if outside > 0 else 2}


def settle_worth(shot, court):
    """Fill in what a shot counts if made: free throw 1; field goal as the coach chose, else from the position."""
    if shot["kind"] == "free throw":
        shot["worth"], shot["worth_from"] = 1, "free throw"
    elif shot["worth_from"] == "coach" and shot["worth"] in (2, 3):
        pass
    else:
        view = judge(shot, court)
        if view is None:
            raise Problem(CHOOSE_WORTH)
        shot["worth"], shot["worth_from"] = view["worth"], "position"


# ---------------------------------------------------------------- the shots file

def load_shots(video):
    """The contents of shots.json (see the top of this file), or an empty one if nothing is tagged yet."""
    path = game_folder(video) / "shots.json"
    if path.exists():
        return read_json(path)
    length, width = court_size(video)
    return {"video": str(video), "court": {"length_ft": length, "width_ft": width}, "next_id": 1, "shots": []}


def save_shots(video, data):
    """Write shots.json, after keeping the file as it was in shots_undo.json."""
    folder = config.data_dir(video)
    config.save_json(folder / UNDO_FILE, load_shots(video), indent=1)
    config.save_json(folder / "shots.json", data, indent=1)


def undo(video):
    """Take back the last change (a shot added, changed or deleted). Undo twice puts it back."""
    path = game_folder(video) / UNDO_FILE
    if not path.exists():
        raise Problem("There is nothing to undo.")
    save_shots(video, read_json(path))      # this keeps the present file as the new undo


def clean_number(number):
    """A typed jersey number as text ("5"), or None when the box was left empty.

    "05" becomes "5", which is how the number reader and the roster write it; only "0"
    and "00" keep their zeros. Otherwise the same player would be counted under two numbers.
    """
    text = str(number if number is not None else "").strip().lstrip("#")
    if text == "":
        return None
    if not text.isdigit() or len(text) > 2:
        raise Problem(f"\"{number}\" is not a jersey number. Type the number only, like 5 or 23.")
    return text if text in ("0", "00") else text.lstrip("0")


def check_shooter(shot, teams):
    """Stop with a plain message when team, number, kind or period make no sense."""
    if shot["team"] not in teams["labels"]:
        raise Problem("Choose the shooter's team.")
    if shot["number"] is None and shot["tag"] is None:
        raise Problem("Say who shot: click the player in the picture, or type the jersey number.")
    if shot["kind"] not in KINDS or shot["period"] not in PERIODS:
        raise Problem("Choose the kind of shot and the period.")


def place(shot, moments, teams):
    """Fill in where the shot was taken from (or "position unknown") from the player's positions."""
    index = look_at(moments, shot["frame"] / moments["fps"])
    found = find_position(moments, teams, index, shot["tag"], shot["team"], shot["number"])
    shot["x_ft"], shot["y_ft"], shot["position_frame"] = found if found else (None, None, None)


def add_shot(video, index, team, number, made, kind="field goal", worth=None, period="1", tag=None):
    """Record one shot and return it.

    index    the look (moment) the shot was taken at
    team     the shooter's team label; number: jersey number, or None/"" when not known
    tag      the player tag picked in the picture, or None when team and number were typed
    worth    2 or 3 to overrule the tool, None to work it out from the position
    """
    moments, teams = load_moments(video), load_teams(video)
    frame = moments["frames"][index]
    shot = {"id": None, "time_s": round(frame / moments["fps"], 2), "frame": frame, "period": str(period),
            "team": team, "number": clean_number(number), "tag": tag, "kind": kind, "made": bool(made),
            "x_ft": None, "y_ft": None, "position_frame": None,
            "worth": worth, "worth_from": "coach" if worth else "position", "source": "coach"}
    check_shooter(shot, teams)
    place(shot, moments, teams)
    data = load_shots(video)
    settle_worth(shot, court_size(video))
    for other in data["shots"]:
        if all(other[key] == shot[key] for key in ("frame", "team", "number", "tag", "kind")):
            raise Problem(f"That shot is already saved, as shot {other['id']}. "
                          "To change it, click it in the \"Tagged so far\" list.")
    shot["id"] = data["next_id"]
    data["next_id"] += 1
    data["shots"].append(shot)
    data["shots"].sort(key=lambda each: (each["frame"], each["id"]))       # game order
    save_shots(video, data)
    return shot


def find_shot(data, shot_id):
    for shot in data["shots"]:
        if shot["id"] == shot_id:
            return shot
    raise Problem(f"There is no shot {shot_id}. Pick one from the list.")


def edit_shot(video, shot_id, changes):
    """Change what was recorded about a shot and return it.

    changes may hold "made", "kind", "period", "team", "number", and "worth" (2 or 3 to
    choose it by hand, None to have it worked out from the position again).
    The moment and the player picked in the picture stay: to change those, delete the
    shot and tag it again. If the shooter was typed, not picked, and team or number
    change, the position is looked up again for the new player.
    """
    data, teams = load_shots(video), load_teams(video)
    shot = find_shot(data, shot_id)
    typed_before = (shot["team"], shot["number"])
    for key in ("made", "kind", "period", "team"):
        if key in changes:
            shot[key] = changes[key]
    if "number" in changes:
        shot["number"] = clean_number(changes["number"])
    if "worth" in changes:
        shot["worth"], shot["worth_from"] = changes["worth"], "coach" if changes["worth"] else "position"
    check_shooter(shot, teams)
    if shot["tag"] is None and (shot["team"], shot["number"]) != typed_before:
        place(shot, load_moments(video), teams)
    settle_worth(shot, court_size(video))
    save_shots(video, data)
    return shot


def delete_shot(video, shot_id):
    data = load_shots(video)
    data["shots"].remove(find_shot(data, shot_id))
    save_shots(video, data)


# ---------------------------------------------------------------- words and tables

def player_text(teams, team, number, tag):
    """A shooter within his team: "#5 <name>", or "no number (tag 17)"."""
    if number is None:
        return f"no number (tag {tag})"
    name = teams["names"].get((team, number))
    return f"#{number}" + (f" {name}" if name else "")


def who_text(teams, team, number, tag):
    """A shooter in words: "<team> #5 <name>", or "<team> no number (tag 17)"."""
    return f"{team} {player_text(teams, team, number, tag)}"


def position_text(shot, court):
    """Where a shot was taken from, in words, with anything the coach should check."""
    view = judge(shot, court)
    if view is None:
        return "position unknown"
    text = f"{view['from_hoop_ft']:.0f} ft from the nearer hoop"
    if shot["worth_from"] == "position" and abs(view["outside_three_ft"]) <= NEAR_LINE_FT:
        side = "outside" if view["outside_three_ft"] > 0 else "inside"
        text += f" (only {abs(view['outside_three_ft']):.1f} ft {side} the three-point line: check 2 or 3)"
    return text


def area_name(view, width):
    """The spot a shot was taken from, in a coach's words: "paint", "left wing", "right corner"...

    view is what judge() returned: the position as the shooter sees it, the hoop ahead.
    Left and right are the shooter's own. Ideas kept simple: the lane is the paint, the
    baseline strip either side of it is the baseline (a corner when outside the arc),
    the rest is elbow / top of the key inside the arc and wing / top of the key outside.
    """
    forward = view["from_baseline"] - court_model.HOOP_FROM_BASELINE_FT     # feet in front of the hoop
    sideways = view["across"] - width / 2                                  # feet to the shooter's right
    side = "left" if sideways < 0 else "right"
    if view["from_hoop_ft"] < 4:
        return "at the rim"
    if abs(sideways) <= court_model.LANE_WIDTH_FT / 2 and view["from_baseline"] <= court_model.FREE_THROW_FT:
        return "paint"
    if forward < 5:
        return f"{side} corner" if view["outside_three_ft"] > 0 else f"{side} baseline"
    if view["outside_three_ft"] > 10:
        return "way out"
    straight_on = abs(sideways) < 0.45 * forward
    if view["outside_three_ft"] <= 0:
        return "top of the key" if straight_on else f"{side} elbow"
    return "top of the key" if straight_on else f"{side} wing"


def where_text(shot, court):
    """ "From 21 ft, left wing" for the screen, or "Position unknown"."""
    view = judge(shot, court)
    if view is None:
        return "Position unknown"
    return f"From {view['from_hoop_ft']:.0f} ft, {area_name(view, court[1])}"


def list_line(shot, court):
    """What the "Tagged so far" list says about a shot: "Made 2 · paint", "Missed free throw"."""
    result = "Made" if shot["made"] else "Missed"
    if shot["kind"] == "free throw":
        return f"{result} free throw"
    view = judge(shot, court)
    spot = area_name(view, court[1]) if view else "position unknown"
    return f"{result} {shot['worth']} · {spot}"


def shot_text(shot, teams, court):
    """One shot as a sentence, for messages and the list."""
    result = "MADE" if shot["made"] else "missed"
    counts = f"{shot['worth']} point" + ("s" if shot["worth"] != 1 else "") if shot["made"] else f"a {shot['worth']}-point try"
    if shot["kind"] == "free throw":
        return (f"Shot {shot['id']} at {show_time(shot['time_s'])}: {who_text(teams, shot['team'], shot['number'], shot['tag'])}, "
                f"free throw {result}" + (", 1 point" if shot["made"] else ""))
    chosen = " (chosen by you)" if shot["worth_from"] == "coach" else ""
    return (f"Shot {shot['id']} at {show_time(shot['time_s'])}: {who_text(teams, shot['team'], shot['number'], shot['tag'])}, "
            f"field goal {result}, {counts}{chosen}, {position_text(shot, court)}")


SHOT_HEADERS = ["Shot", "Time", "Period", "Team", "Player", "Kind", "Result", "Points", "Taken from"]


def shot_rows(data, teams, court):
    """The shots as rows of text for a table, in game order (headers: SHOT_HEADERS)."""
    rows = []
    for shot in data["shots"]:
        worth = f"{shot['worth']}-pt" + (" (chosen)" if shot["worth_from"] == "coach" else "")
        rows.append([shot["id"], show_time(shot["time_s"]), shot["period"], shot["team"],
                     player_text(teams, shot["team"], shot["number"], shot["tag"]),
                     "free throw" if shot["kind"] == "free throw" else worth,
                     "made" if shot["made"] else "missed", shot["worth"] if shot["made"] else 0,
                     "the free-throw line" if shot["kind"] == "free throw" else position_text(shot, court)])
    return rows


def player_key(shot):
    """What a shot is counted under within its team: the jersey number, or "tag 17" when there is none."""
    return shot["number"] if shot["number"] is not None else f"tag {shot['tag']}"


POINTS_HEADERS = ["Team", "No.", "Name", "2-pt", "2-pt %", "3-pt", "3-pt %", "Free throws", "FT %", "Points"]


def points_table(data, teams):
    """The points table as rows of text (headers: POINTS_HEADERS).

    One row per player who took a shot, then one "TEAM TOTAL" row per team. "3/5" means
    3 made of 5 tried. A shooter without a number is listed by player tag, so one real
    player can appear under several tags.
    """
    lines = {}          # (team, player key) -> {worth: [made, tried]}
    for shot in data["shots"]:
        for key in ((shot["team"], player_key(shot)), (shot["team"], "TEAM TOTAL")):
            counts = lines.setdefault(key, {1: [0, 0], 2: [0, 0], 3: [0, 0]})[shot["worth"]]
            counts[1] += 1
            counts[0] += bool(shot["made"])

    def order(key):
        team, player = key
        kind = 2 if player == "TEAM TOTAL" else 1 if player.startswith("tag") else 0
        return (teams["labels"].index(team) if team in teams["labels"] else 2, kind,
                int(player) if kind == 0 else 0, player)

    rows = []
    for team, player in sorted(lines, key=order):
        counts = lines[(team, player)]
        row = [team, "" if player == "TEAM TOTAL" else player,
               "TEAM TOTAL" if player == "TEAM TOTAL" else teams["names"].get((team, player), "")]
        for worth in (2, 3, 1):
            made, tried = counts[worth]
            row += [f"{made}/{tried}", f"{100 * made / tried:.0f}%" if tried else "-"]
        row.append(sum(worth * counts[worth][0] for worth in (1, 2, 3)))
        rows.append(row)
    return rows


def print_rows(headers, rows):
    """Print rows of text as a plain table with the columns lined up."""
    cells = [[str(cell) for cell in row] for row in [headers] + rows]
    widths = [max(len(row[column]) for row in cells) for column in range(len(headers))]
    for row in cells:
        print("  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())


# ---------------------------------------------------------------- the scoring heat map

def draw_heat_map(data, court, team, player=None, name="", path=None):
    """Draw where a team's (or one player's) points came from and return the picture (red-green-blue).

    data      the contents of shots.json
    team      team label; player: a jersey number or "tag 17" (see player_key), None for the whole team
    name      the player's name for the title, if known
    path      where to save the picture as a PNG as well (None: do not save)

    Both ends of the court are folded onto ONE half court, drawn as the shooter sees
    it with the hoop at the top, because a team shoots at one end and then the other.
    Every field goal with a known position is one mark: a made shot is a filled circle
    with its points in it, a miss a grey cross. Free throws are not drawn (they all
    come from the same line). Shading is added only from SHADING_FROM made shots on.
    """
    import matplotlib
    matplotlib.use("Agg")           # draw straight into a picture; no window is opened
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.lines import Line2D

    length, width = court
    half = length / 2
    mine = [shot for shot in data["shots"] if shot["team"] == team and (player is None or player_key(shot) == player)]
    field_goals = [shot for shot in mine if shot["kind"] == "field goal"]
    free_throws = [shot for shot in mine if shot["kind"] == "free throw"]
    drawn = []          # (feet across, feet from the baseline, made, worth)
    for shot in field_goals:
        view = judge(shot, court)
        if view is not None:
            drawn.append((view["across"], view["from_baseline"], shot["made"], shot["worth"]))
    made = [mark for mark in drawn if mark[2]]
    missed = [mark for mark in drawn if not mark[2]]
    unknown = len(field_goals) - len(drawn)

    ink, soft, mark_colour = "#2b2b29", "#6b6b66", "#0d366b"
    figure, axes = plt.subplots(figsize=(8.2, 8.6), dpi=110)
    figure.patch.set_facecolor("white")
    figure.subplots_adjust(left=0.05, right=0.95, top=0.81, bottom=0.13)

    shaded = len(made) >= SHADING_FROM
    if shaded:
        # points scored in each square, spread out a little so that near-by shots add up
        squares, _, _ = np.histogram2d([mark[1] for mark in made], [mark[0] for mark in made],
                                       bins=[int(half // HEAT_CELL_FT), int(width // HEAT_CELL_FT)],
                                       range=[[0, half], [0, width]], weights=[mark[3] for mark in made])
        smooth = cv2.GaussianBlur(squares.astype(np.float32), (0, 0), 1.5)
        # one colour, from nothing (no points) to dark (most points): darker simply means more
        shades = LinearSegmentedColormap.from_list("light_to_dark", ["#ffffff", "#bcd7f7", "#3987e5"])
        axes.imshow(smooth, extent=[0, width, half, 0], cmap=shades, vmin=0, interpolation="bilinear", zorder=1)

    for line in court_model.court_lines(length, width):
        # only the left half of the court, turned so that its baseline is at the top
        across = np.where(line[:, 0] <= half + 0.01, line[:, 1], np.nan)
        axes.plot(across, line[:, 0], color=ink, linewidth=1.1, zorder=2)
    hoop = plt.Circle((width / 2, court_model.HOOP_FROM_BASELINE_FT), 0.75, fill=False, color=ink, linewidth=1.1, zorder=2)
    axes.add_patch(hoop)
    axes.plot([width / 2 - 3, width / 2 + 3], [4, 4], color=ink, linewidth=1.6, zorder=2)     # the backboard

    axes.scatter([mark[0] for mark in missed], [mark[1] for mark in missed], marker="x", s=70, color="#8a8a85",
                 linewidths=1.6, zorder=3)
    axes.scatter([mark[0] for mark in made], [mark[1] for mark in made], s=[150 if mark[3] == 2 else 250 for mark in made],
                 color=mark_colour, edgecolors="white", linewidths=1.5, zorder=4)
    for across, from_baseline, _, worth in made:
        axes.text(across, from_baseline, str(worth), color="white", fontsize=8 if worth == 2 else 10, fontweight="bold",
                  ha="center", va="center_baseline", zorder=5)
    if not drawn:
        axes.text(width / 2, half * 0.72, "No shot with a known position yet", color=soft, fontsize=13, ha="center")

    axes.set_xlim(-2, width + 2)
    axes.set_ylim(half + 1.5, -2)           # upside down: the baseline and the hoop at the top
    axes.set_aspect("equal")
    axes.axis("off")

    who = f"{team.capitalize()} team" if player is None else f"{team.capitalize()} {'#' if player.isdigit() else ''}{player} {name}".strip()
    figure.text(0.05, 0.955, f"{who}: where the points came from", color=ink, fontsize=15, fontweight="bold")
    points = sum(mark[3] for mark in made)
    lines = [(f"{len(drawn)} shot{'s' if len(drawn) != 1 else ''} drawn: {len(made)} made for {points} points, "
              f"{len(missed)} missed.", ink)]
    left_out = [f"{unknown} with position unknown"] if unknown else []
    if free_throws:
        left_out.append(f"{len(free_throws)} free throw{'s' if len(free_throws) != 1 else ''} "
                        f"({sum(shot['made'] for shot in free_throws)} made)")
    if left_out:
        lines.append(("Not drawn: " + " and ".join(left_out) + ".", ink))
    if shaded:
        lines.append(("Shading: darker = more points scored from around there.", soft))
    else:
        lines.append((f"Each mark is one shot. No shading: it needs {SHADING_FROM} made shots to mean anything.", soft))
    lines.append(("Both ends of the court are shown as one, as the shooter sees it: the hoop is at the top.", soft))
    for row, (text, colour) in enumerate(lines):
        figure.text(0.05, 0.92 - 0.028 * row, text, color=colour, fontsize=10.5)
    figure.text(0.05, 0.03, "Positions are estimates: usually within a foot or two, sometimes 4 to 8 ft off when the legs were hidden.",
                color=soft, fontsize=9.5)
    figure.legend(handles=[
        Line2D([], [], marker="o", linestyle="", markersize=11, color=mark_colour, markeredgecolor="white", label="made 2"),
        Line2D([], [], marker="o", linestyle="", markersize=14, color=mark_colour, markeredgecolor="white", label="made 3"),
        Line2D([], [], marker="x", linestyle="", markersize=8, color="#8a8a85", markeredgewidth=1.6, label="missed")],
        loc="lower left", bbox_to_anchor=(0.04, 0.055), ncol=3, frameon=False, fontsize=10.5, labelcolor=ink)

    figure.canvas.draw()
    picture = np.asarray(figure.canvas.buffer_rgba())[:, :, :3].copy()
    if path is not None:
        figure.savefig(path)
        # The SD card has damaged files before: read the picture back to be sure it is whole.
        if cv2.imread(str(path)) is None:
            plt.close(figure)
            raise Problem(f"{path} could not be read back after saving. The disk may be failing; try again.")
    plt.close(figure)
    return picture


def heat_map_path(video, team):
    return config.output_dir(video) / f"scoring_heat_map_{team.replace(' ', '_')}.png"


def save_heat_maps(video):
    """Save one scoring heat map per team in outputs/<video>/ and return the paths."""
    data, teams, court = load_shots(video), load_teams(video), court_size(video)
    paths = []
    for team in teams["labels"]:
        draw_heat_map(data, court, team, path=heat_map_path(video, team))
        paths.append(heat_map_path(video, team))
    return paths


# ---------------------------------------------------------------- the command line

def parse_args():
    parser = argparse.ArgumentParser(description="Tagged shots: list them, add or delete one, points table, scoring heat maps.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="video file (default clips/test.mp4)")
    parser.add_argument("--add", metavar="TIME", help="record a shot at this time of the game video, like 1:23.4")
    parser.add_argument("--team", help="with --add: the shooter's team, as the tool labels it")
    parser.add_argument("--number", help="with --add: the shooter's jersey number")
    result = parser.add_mutually_exclusive_group()
    result.add_argument("--made", action="store_true", help="with --add: the shot went in")
    result.add_argument("--missed", action="store_true", help="with --add: the shot missed")
    parser.add_argument("--free-throw", action="store_true", help="with --add: a free throw (otherwise a field goal)")
    parser.add_argument("--worth", type=int, choices=[2, 3], help="with --add: 2 or 3 points, instead of working it out")
    parser.add_argument("--period", default="1", choices=PERIODS, help="with --add: the period (default 1)")
    parser.add_argument("--delete", type=int, metavar="SHOT", help="delete the shot with this number")
    parser.add_argument("--undo", action="store_true", help="take back the last change")
    return parser.parse_args()


def run(args):
    video = args.video
    config.require_video(video)
    teams, court = load_teams(video), court_size(video)
    if args.add:
        if not (args.made or args.missed) or not args.team or not args.number:
            raise Problem("--add needs --team, --number and --made or --missed.\n"
                          f"The teams of this game are: {', '.join(teams['labels'])}")
        index = look_at(load_moments(video), parse_time(args.add))
        shot = add_shot(video, index, args.team, args.number, args.made, "free throw" if args.free_throw else "field goal",
                        args.worth, args.period)
        print("Saved. " + shot_text(shot, teams, court))
    if args.delete is not None:
        delete_shot(video, args.delete)
        print(f"Deleted shot {args.delete}.")
    if args.undo:
        undo(video)
        print("The last change was taken back.")

    data = load_shots(video)
    print()
    print(f"Shots tagged for {video}: {len(data['shots'])}")
    if not data["shots"]:
        print("  None yet. Tag them on the Shots screen of the dashboard:  python src/dashboard.py")
        return
    print_rows(SHOT_HEADERS, shot_rows(data, teams, court))
    print()
    print("Points   (3/5 = 3 made of 5 tried; a percentage from a few shots means little)")
    print_rows(POINTS_HEADERS, points_table(data, teams))
    print()
    print("  Positions are estimates, so a 2 or 3 worked out near the three-point line can be wrong.")
    print("  A shot with position unknown counts in the table but is not on the map.")
    for path in save_heat_maps(video):
        print(f"Wrote {path}")


if __name__ == "__main__":
    try:
        run(parse_args())
    except Problem as problem:
        raise SystemExit(str(problem))
