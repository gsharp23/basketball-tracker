"""Stage 1, step 6: per-player and per-team numbers from the tracking data.

    python src/stats.py --video clips/test.mp4

Run these first (or run main.py, which does them all in order)
    detect_track.py                       finds and follows the players       -> tracks.json
    team_assigner.py                      which team each tracking ID is on   -> teams.json
    jersey_id.py                          the jersey number of each ID        -> jerseys.json
    court_mapper.py, tactical_view.py     where everyone is, in court feet    -> positions.json

READ THIS BEFORE USING THE NUMBERS
    The camera follows the play and never shows the whole court. A player who is out of
    the picture is not tracked, so nothing here is "minutes played":
      seen       time the player was seen on camera (in the picture)
      on map     time the player also had a position on the court (feet in view)
      distance   feet covered while on the map; the gaps are not filled in
    The ball is not tracked reliably yet, so there are no ball stats of any kind
    (no possession, passes or shots).

What it does
    1. Players. One real player usually has several tracking IDs. IDs with the same team
       AND the same jersey number are joined into one player. An ID without a number is
       never guessed: it stays on its own and is counted as "unidentified" for its team.
    2. Per player: time seen, time on the map, distance, average speed, average spot,
       share of the on-map time in each third of the court and in the paint.
    3. Per team: spacing, the average distance between its players on the map, for the
       whole video and minute by minute (the minute-by-minute list is in stats.json).
    4. Writes data/<video>/stats.json and stats.csv, one position map per team in
       outputs/<video>/, and prints a table. Names come from roster.json when filled in.
"""
import argparse
import csv
import io
import itertools
import json
import math
import os
import sys

import config
import court_model

# A step from one look to the next that would need more than this speed is a tracking
# mistake (the box jumped to another player, or the legs were hidden for a moment), not
# running, so it is left out of the distance. 25 ft/s is about 17 mph.
# Test clip: 2 of 888 steps were faster (28 and 38 ft/s), both of them box mistakes
# seen in the video; the next fastest step was 19 ft/s.
MAX_SPEED_FT_S = 25.0
# Two tracking IDs joined into one player cannot be in two places at once. If they are
# further apart than this in the same frame, one of them probably has a wrong number.
# (Two boxes on the same player were measured 1 to 3 ft apart.)
SAME_PLAYER_FT = 6.0
# Size of one square of the position map, in feet.
MAP_CELL_FT = 2

CSV_COLUMNS = ["team", "number", "name", "seen_s", "on_map_s", "distance_ft", "avg_speed_ft_s",
               "avg_x_ft", "avg_y_ft", "left_third_pct", "middle_third_pct", "right_third_pct",
               "paint_pct", "tracking_ids", "jumps_left_out", "two_places_frames"]


def parse_args():
    parser = argparse.ArgumentParser(description="Per-player and per-team stats from the tracking data.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="video file (default clips/test.mp4)")
    parser.add_argument("--no-position-maps", action="store_true", help="do not save the position map pictures")
    return parser.parse_args()


# ---------------------------------------------------------------- reading the inputs

def load_inputs(video):
    """Read the four input files. Stops with a plain message naming the command for a missing one."""
    folder = config.data_dir(video)
    commands = {"tracks.json": f"python src/main.py --video {video}",
                "teams.json": f"python src/main.py --video {video}",
                "jerseys.json": f"python src/main.py --video {video}",
                "positions.json": f"python src/tactical_view.py --video {video}"}
    for name, command in commands.items():
        config.require_file(folder / name, command)
    loaded = {name: config.load_json(folder / name) for name in commands}
    if not loaded["tracks.json"]["frames"]:
        sys.exit(f"tracks.json has no frames in it. Run the detection again:\n  {commands['tracks.json']} --from detect")
    # court_mapper.py writes a first, raw positions.json (with bystanders still in it and
    # shaky paths). tactical_view.py replaces it with the cleaned one, marked "cleaned".
    if "cleaned" not in loaded["positions.json"]:
        sys.exit("positions.json is still the raw one from court_mapper.py (not cleaned or smoothed).\n"
                 f"Run this first:  {commands['positions.json']}")
    # Tracking IDs change when detection is run again, so older files would not match.
    tracks_time = (folder / "tracks.json").stat().st_mtime
    older = [name for name in ("teams.json", "jerseys.json", "positions.json")
             if (folder / name).stat().st_mtime < tracks_time]
    if older:
        print(f"NOTE: {', '.join(older)} older than tracks.json, so may not match it. "
              f"To bring everything up to date:  python src/main.py --video {video}")
    return loaded["tracks.json"], loaded["teams.json"], loaded["jerseys.json"], loaded["positions.json"]


def load_roster():
    """roster.json as a list of (team name, {jersey number: player name}). Empty if unusable."""
    if not config.ROSTER_PATH.exists():
        return []
    try:
        roster = json.loads(config.ROSTER_PATH.read_text(encoding="utf-8"))
        return [(team.get("name", key), {str(number).strip(): name for number, name in team.get("players", {}).items()})
                for key, team in roster.items()]
    except (ValueError, AttributeError):
        print(f"Could not understand {config.ROSTER_PATH.name}; carrying on without names.")
        return []


# ---------------------------------------------------------------- who is who

def group_players(tracks, teams, jerseys):
    """Join tracking IDs into players. Returns a list of {"team", "number", "ids"}.

    team is 0, 1 or None and number is text ("15") or None, as the earlier steps found
    them. Only IDs that have BOTH a team and a number are joined; every other ID stays
    a "player" of its own.
    """
    track_ids = sorted({player["id"] for record in tracks["frames"] for player in record["players"]})
    joined = {}         # (team, number) -> the player those IDs were joined into
    players = []
    for track_id in track_ids:
        team = teams["tracks"].get(str(track_id))
        number = jerseys["tracks"].get(str(track_id), {}).get("number")
        if team is None or number is None:
            players.append({"team": team, "number": number, "ids": [track_id]})
        elif (team, number) in joined:
            joined[(team, number)]["ids"].append(track_id)
        else:
            joined[(team, number)] = {"team": team, "number": number, "ids": [track_id]}
            players.append(joined[(team, number)])
    return players


def identified(player):
    """True for a player with a team and a jersey number (possibly joined from several IDs)."""
    return player["team"] is not None and player["number"] is not None


def match_roster(players, roster):
    """Which roster team wears which jersey colour? Returns {team 0 or 1: (name, {number: player name})}.

    roster.json does not say which colour a team wears (that changes from game to game).
    So try it both ways round and keep the way that puts more of the numbers that were
    read on the roster. Returns {} when the roster is empty or both ways fit equally.
    """
    if len(roster) != 2 or not any(names for _, names in roster):
        return {}
    numbers = {0: set(), 1: set()}
    for player in players:
        if identified(player):
            numbers[player["team"]].add(player["number"])

    def fit(first, second):
        return len(numbers[0] & set(first[1])) + len(numbers[1] & set(second[1]))

    straight, swapped = fit(roster[0], roster[1]), fit(roster[1], roster[0])
    if straight == swapped:
        print("NOTE: the numbers read do not show which roster team wears which colour, so no names are used.")
        return {}
    first, second = (roster[0], roster[1]) if straight > swapped else (roster[1], roster[0])
    return {0: first, 1: second}


# ---------------------------------------------------------------- measuring

def player_path(ids, boxes, spots):
    """One player's court positions {frame: (x, y)}, put together from all of their tracking IDs.

    If two of the IDs are in the same frame, the ID that was tracked longer is used.
    Also returns in how many frames the two were more than SAME_PLAYER_FT apart.
    """
    path, two_places = {}, 0
    for track_id in sorted(ids, key=lambda i: -len(boxes[i])):
        for frame, spot in spots.get(track_id, {}).items():
            if frame not in path:
                path[frame] = spot
            elif math.dist(path[frame], spot) > SAME_PLAYER_FT:
                two_places += 1
    return path, two_places


def in_paint(x, y, court):
    """True inside either lane: the box from the baseline to the free-throw line (12 ft wide on most courts)."""
    length, width = court
    from_baseline = min(x, length - x)
    return 0 <= from_baseline <= court.free_throw and abs(y - width / 2) <= court.lane / 2


def measure(path, stride, look_seconds, court):
    """Distance, speed and where-on-the-court numbers for one path {frame: (x, y)}."""
    length, width = court

    distance, steps, jumps = 0.0, 0, 0
    for frame, spot in path.items():
        before = path.get(frame - stride)
        if before is None:
            continue                # a gap: the player was not on the map one look earlier
        step = math.dist(before, spot)
        if step / look_seconds > MAX_SPEED_FT_S:
            jumps += 1              # faster than anyone runs: a tracking mistake
            continue
        distance += step
        steps += 1

    result = {"on_map_s": round(len(path) * look_seconds, 1),
              "distance_ft": round(distance, 1),
              # speed over the steps that were counted, so gaps do not water it down
              "avg_speed_ft_s": round(distance / (steps * look_seconds), 1) if steps else None,
              "avg_x_ft": None, "avg_y_ft": None,
              "left_third_pct": None, "middle_third_pct": None, "right_third_pct": None, "paint_pct": None,
              "jumps_left_out": jumps}
    if path:
        xs = [x for x, _ in path.values()]
        ys = [y for _, y in path.values()]
        left = sum(x < length / 3 for x in xs)
        right = sum(x > 2 * length / 3 for x in xs)
        paint = sum(in_paint(x, y, court) for x, y in path.values())
        result.update({"avg_x_ft": round(sum(xs) / len(xs), 1), "avg_y_ft": round(sum(ys) / len(ys), 1),
                       "left_third_pct": round(100 * left / len(path)),
                       "middle_third_pct": round(100 * (len(path) - left - right) / len(path)),
                       "right_third_pct": round(100 * right / len(path)),
                       "paint_pct": round(100 * paint / len(path))})
    return result


def team_spacing(paths, look_seconds, fps):
    """How spread out a team is: the average distance between every pair of its players.

    Worked out in every frame that has at least two of the team's players on the map,
    then averaged over those frames. paths is one {frame: (x, y)} per player.
    Unidentified IDs of the team count too. When the tracker puts two boxes on one
    player for a moment, that pair counts as two players a few feet apart; on the
    test clip this changed a team's spacing by 0.05 ft.
    """
    by_frame = {}
    for path in paths:
        for frame, spot in path.items():
            by_frame.setdefault(frame, []).append(spot)
    spacings, players_counted, by_minute = [], 0, {}
    for frame, here in by_frame.items():
        if len(here) < 2:
            continue
        pairs = [math.dist(a, b) for a, b in itertools.combinations(here, 2)]
        spacing = sum(pairs) / len(pairs)
        spacings.append(spacing)
        players_counted += len(here)
        by_minute.setdefault(int(frame / fps // 60), []).append(spacing)
    if not spacings:
        return {"spacing_ft": None, "spacing_seconds": 0.0, "spacing_players": None, "spacing_by_minute": []}
    return {"spacing_ft": round(sum(spacings) / len(spacings), 1),
            "spacing_seconds": round(len(spacings) * look_seconds, 1),
            "spacing_players": round(players_counted / len(spacings), 1),
            "spacing_by_minute": [{"minute": minute, "spacing_ft": round(sum(values) / len(values), 1),
                                   "seconds": round(len(values) * look_seconds, 1)}
                                  for minute, values in sorted(by_minute.items())]}


def build_stats(video, tracks, teams, jerseys, positions, roster):
    """Work everything out. Returns (what goes into stats.json, {team number: all its positions})."""
    stride = tracks["stride"]
    look_seconds = stride / tracks["fps"]       # each look at the video stands for this much time
    court = court_model.court_from(positions["court"])        # (length, width) that knows its markings
    length, width = court
    labels = {int(key): team["label"] for key, team in teams["teams"].items()}

    boxes = {}          # tracking ID -> the frames it has a box in
    for record in tracks["frames"]:
        for player in record["players"]:
            boxes.setdefault(player["id"], set()).add(record["frame"])
    spots = {}          # tracking ID -> {frame: (x, y)} in court feet
    for record in positions["frames"]:
        for player in record["players"]:
            spots.setdefault(player["id"], {})[record["frame"]] = (player["x"], player["y"])

    groups = group_players(tracks, teams, jerseys)
    names = match_roster(groups, roster)
    rows, team_paths = [], {0: [], 1: []}
    for group in groups:
        seen = set().union(*(boxes[track_id] for track_id in group["ids"]))
        path, two_places = player_path(group["ids"], boxes, spots)
        if group["team"] is not None:
            team_paths[group["team"]].append(path)
        name = None
        if identified(group) and group["team"] in names:
            name = names[group["team"]][1].get(group["number"])
        row = {"team": labels.get(group["team"]), "team_index": group["team"], "number": group["number"],
               "name": name, "identified": identified(group), "tracking_ids": group["ids"],
               "seen_s": round(len(seen) * look_seconds, 1)}
        row.update(measure(path, stride, look_seconds, court))
        row["two_places_frames"] = two_places
        rows.append(row)

    # Order: team 0, team 1, no team; numbered players first by number, then the longest seen.
    rows.sort(key=lambda row: (2 if row["team_index"] is None else row["team_index"], not row["identified"],
                               int(row["number"]) if row["identified"] else 0, -row["seen_s"]))

    team_rows = []
    for index in (0, 1):
        mine = [row for row in rows if row["team_index"] == index]
        team_row = {"team": labels[index], "team_index": index,
                    "roster_name": names[index][0] if index in names else None,
                    "players_with_number": sum(row["identified"] for row in mine),
                    "ids_without_number": sum(not row["identified"] for row in mine)}
        team_row.update(team_spacing(team_paths[index], look_seconds, tracks["fps"]))
        team_rows.append(team_row)

    all_boxes = sum(len(frames) for frames in boxes.values())
    named_boxes = sum(len(boxes[track_id]) for row in rows if row["identified"] for track_id in row["tracking_ids"])
    result = {"video": str(video),
              "court": positions["court"],
              "video_seconds": round(len(tracks["frames"]) * look_seconds, 1),
              "seconds_per_look": round(look_seconds, 4),
              "identified_share_pct": round(100 * named_boxes / all_boxes) if all_boxes else 0,
              "notes": ["The camera follows the play: 'seen' is time in the picture, NOT minutes played.",
                        "Distance and speed are only measured while a player is on the map (feet in view); "
                        "gaps are not filled in.",
                        "Left, middle and right third are as the camera sees the court. "
                        "The tool does not know which hoop a team attacks.",
                        "No ball stats: the ball is not tracked reliably yet."],
              "warnings": [],       # things about THIS video the coach should act on; run() adds to it
              "players": rows, "teams": team_rows}
    team_spots = {index: [spot for path in team_paths[index] for spot in path.values()] for index in (0, 1)}
    return result, team_spots


# ---------------------------------------------------------------- writing the files

def save_text(path, text):
    """Write a text file the safe way config.save_json does: temp file, rename, read back."""
    temp = path.with_name(path.name + ".tmp")
    # utf-8-sig lets Excel on Windows show accented names correctly; newline="" leaves
    # the line ends exactly as the csv module made them.
    with open(temp, "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        if f.read() != text:
            raise IOError(f"{path} did not read back the same as it was written. "
                          "The disk may be failing; run the command again.")


def csv_text(result):
    """The player rows as CSV text, one line per player (a blank cell means "not known")."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    # The players with a number first (both teams), then the unidentified tracking IDs:
    # a full game has hundreds of those, and they would bury the second team.
    for row in sorted(result["players"], key=lambda row: not row["identified"]):
        cells = dict(row, team=row["team"] or "no team", tracking_ids=" ".join(str(i) for i in row["tracking_ids"]))
        if not row["identified"]:
            cells["name"] = "(unidentified)"
        writer.writerow(["" if cells[column] is None else cells[column] for column in CSV_COLUMNS])
    return out.getvalue()


def save_position_map(path, label, team_spots, court, look_seconds):
    """One picture: where on the court a team's players were seen, darker = more time."""
    import matplotlib
    matplotlib.use("Agg")           # draw straight into a file; no window is opened
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap

    length, width = court
    # Positions up to 3 ft outside the lines are kept by tactical_view.py; count them in the edge squares.
    xs = np.clip([x for x, _ in team_spots], 0, length)
    ys = np.clip([y for _, y in team_spots], 0, width)
    squares, _, _ = np.histogram2d(xs, ys, bins=[int(length // MAP_CELL_FT), int(width // MAP_CELL_FT)],
                                   range=[[0, length], [0, width]])
    seconds = squares.T * look_seconds
    seconds[seconds == 0] = np.nan          # squares nobody was seen in stay blank

    # One colour from light to dark: darker simply means more time.
    blues = LinearSegmentedColormap.from_list("light_to_dark_blue", ["#cde2fb", "#3987e5", "#0d366b"])
    ink, soft = "#2b2b29", "#6b6b66"
    figure, axes = plt.subplots(figsize=(10, 6.6), dpi=120)
    figure.patch.set_facecolor("white")
    # origin="lower" puts the near (camera) sideline at the bottom, like the tactical video.
    image = axes.imshow(seconds, origin="lower", extent=[0, length, 0, width], cmap=blues, vmin=0)
    for line in court_model.court_lines(length, width, **court_model.extra(court)):
        axes.plot(line[:, 0], line[:, 1], color=ink, linewidth=1.0)
    axes.set_xlim(-2, length + 2)
    axes.set_ylim(-2, width + 2)
    axes.set_aspect("equal")
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("bottom", "left"):
        axes.spines[side].set_color(soft)
    axes.tick_params(colors=soft, labelsize=9)
    axes.set_xlabel("feet from the left baseline (as the camera sees the court)", color=soft)
    axes.set_ylabel("feet from the near (camera) sideline", color=soft)
    axes.set_title(f"{label.capitalize()} team: where its players were seen", color=ink, fontsize=14, loc="left", pad=26)
    axes.text(0, 1.02, f"Only while in the picture with feet in view, so not the whole game. "
                       f"{len(team_spots) * look_seconds:.0f} player-seconds in all.",
              transform=axes.transAxes, color=soft, fontsize=9)
    bar = figure.colorbar(image, ax=axes, shrink=0.8, pad=0.02)
    bar.set_label(f"seconds in each {MAP_CELL_FT} x {MAP_CELL_FT} ft square, all the team's players added up", color=soft)
    bar.ax.tick_params(colors=soft, labelsize=9)
    bar.outline.set_visible(False)
    figure.tight_layout()
    figure.savefig(path)
    plt.close(figure)
    # The SD card has damaged files before: read the picture back to be sure it is whole.
    try:
        plt.imread(path)
    except Exception:
        print(f"WARNING: {path} could not be read back. Run the command again.")


# ---------------------------------------------------------------- the table

def clock(seconds):
    """75.3 -> "1:15"."""
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def show(value, pattern="{}"):
    """A number for the table, or "-" when there is none."""
    return "-" if value is None else pattern.format(value)


def print_table(result):
    """Print the stats (result is the contents of stats.json) as a table a coach can read."""
    court = result["court"]
    players = result["players"]
    print()
    print(f"Player stats for {result['video']}   ({clock(result['video_seconds'])} of video, "
          f"court {court['length_ft']} x {court['width_ft']} ft)")
    print()
    print("  NOT minutes played. The camera follows the play, so a player who is out of the picture")
    print("  is not counted. 'Seen' = time seen on camera. 'On map' = time with a position on the court")
    print("  (feet in view). Distance, speed and the court shares are measured during 'on map' time only.")
    for team in result["teams"]:
        if team["roster_name"]:
            print(f"  Roster: the {team['team']} team is taken to be \"{team['roster_name']}\".")
    print()
    print("  Team     No  Name               Seen  On map  Distance   Speed   Avg spot   Left   Mid  Right  Paint  IDs")
    print("                                  m:ss    m:ss        ft    ft/s    x, y ft      %     %      %      %")
    numbered = [row for row in players if row["identified"]]
    for row in numbered:
        spot = "-" if row["avg_x_ft"] is None else f"{row['avg_x_ft']:.0f}, {row['avg_y_ft']:.0f}"
        print(f"  {row['team']:<8} {row['number']:>2}  {(row['name'] or '-')[:16]:<16} {clock(row['seen_s']):>6}  "
              f"{clock(row['on_map_s']):>6}  {row['distance_ft']:>8.0f}  {show(row['avg_speed_ft_s'], '{:.1f}'):>6}  "
              f"{spot:>9}  {show(row['left_third_pct']):>5} {show(row['middle_third_pct']):>5}  "
              f"{show(row['right_third_pct']):>5}  {show(row['paint_pct']):>5}  {len(row['tracking_ids']):>3}")
    if not numbered:
        print("  (no player has both a team and a jersey number yet)")
    print()
    print("  Unidentified: not in the table, because no jersey number or no team was found (nothing is guessed)")
    for label, index in [(team["team"], team["team_index"]) for team in result["teams"]] + [("no team", None)]:
        others = [row for row in players if not row["identified"] and row["team_index"] == index]
        if others:
            # Name the longest few, so they can be found in tactical.mp4 (where they are labelled "id 7").
            longest = sorted(others, key=lambda row: -row["seen_s"])[:3]
            print(f"    {label + ':':<9} {len(others):>3} tracking {'ID' if len(others) == 1 else 'IDs'}, "
                  f"seen {clock(sum(row['seen_s'] for row in others))} in total. The longest: "
                  + ", ".join(f"id {row['tracking_ids'][0]} ({clock(row['seen_s'])})" for row in longest))
    print(f"  The players in the table account for {result['identified_share_pct']}% of all the time players were seen.")
    print()
    print("  Spacing: the average distance between a team's players that are on the map together")
    for team in result["teams"]:
        if team["spacing_ft"] is None:
            print(f"    {team['team'] + ':':<9} never two players on the map at the same time")
        else:
            print(f"    {team['team'] + ':':<9} {team['spacing_ft']:.1f} ft  (over {clock(team['spacing_seconds'])} with 2 or more "
                  f"on the map; {team['spacing_players']:.1f} of its players counted on average)")
    print()
    print("  Left / Mid / Right are thirds of the court as the camera sees it. Paint = inside either lane.")
    print("  IDs = how many tracking IDs were joined into this player. Speed is in feet per second")
    print("  (10 ft/s is about 7 mph). No ball stats: the ball is not tracked yet.")
    doubles = [row for row in numbered if row["two_places_frames"]]
    for row in doubles:
        print(f"  CHECK: {row['team']} {row['number']} was in two places at once ({row['two_places_frames']} times). "
              f"One of its tracking IDs ({', '.join(str(i) for i in row['tracking_ids'])}) probably has a wrong number.")
    for warning in result["warnings"]:
        print(f"  NOTE: {warning}")


def run(video=config.DEFAULT_VIDEO, position_maps=True, show_table=True):
    """Do the whole step for one video. Returns the stats (as in stats.json)."""
    tracks, teams, jerseys, positions = load_inputs(video)
    result, team_spots = build_stats(video, tracks, teams, jerseys, positions, load_roster())
    # Warnings the earlier stages left in their files: repeated here because on a full
    # game they were printed hours ago.
    for earlier in (tracks, teams):
        if earlier.get("warning"):
            result["warnings"].append(earlier["warning"])
    calibration = config.data_dir(video) / "court_calibration.json"
    if calibration.exists() and config.load_json(calibration).get("provisional"):
        result["warnings"].append("The court landmarks for this video are PROVISIONAL (not clicked by you), so every "
                                  "position-based number is too. Click them yourself:  "
                                  f"python src/select_court_keypoints.py --video {video}")

    folder = config.data_dir(video)
    config.save_json(folder / "stats.json", result, indent=1)       # save_json reads the file back to check it
    save_text(folder / "stats.csv", csv_text(result))
    if show_table:
        print_table(result)
    print()
    print(f"Wrote {folder / 'stats.json'}")
    print(f"Wrote {folder / 'stats.csv'}   (opens in Excel)")
    if position_maps:
        court = court_model.court_from(positions["court"])
        for team in result["teams"]:
            if team_spots[team["team_index"]]:
                path = config.output_dir(video) / f"position_map_{team['team'].replace(' ', '_')}.png"
                save_position_map(path, team["team"], team_spots[team["team_index"]], court, result["seconds_per_look"])
                print(f"Wrote {path}")
    return result


if __name__ == "__main__":
    args = parse_args()
    run(args.video, position_maps=not args.no_position_maps)
