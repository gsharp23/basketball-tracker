"""Possessions and the opponent report: who had the ball, how they played it, what it got them.

    python src/possessions.py --video clips/test.mp4                 list the possessions and print the report
    python src/possessions.py --video clips/test.mp4 --add 0:12 0:31 --team blue --offense half_court --defense man
    python src/possessions.py --video clips/test.mp4 --delete 3
    python src/possessions.py --video clips/test.mp4 --our-team white

The ball cannot be found in the video yet, so nothing here is detected: the coach labels
each possession by hand on the Opponent page of the dashboard (automatic recognition is
a later stage). The report then counts them.

data/<video>/game_info.json      what the coach says about the game (shared with the Games page)
    "opponent"   the opponent's name, "date" the date as typed, both "" until filled in
    "our_team"   which team label of teams.json is us ("white"), or null when not chosen yet

data/<video>/possessions.json
    "possessions"   a list, in game order, of
        "id"                 number of the possession, never reused while the file exists
        "start_s", "end_s"   when it began and ended, in seconds of the game video
        "team"               who had the ball: a team label of teams.json
        "offense"            how they played it: a key of OFFENSE_TYPES, or "" (not labelled yet)
        "defense"            what they faced: a key of DEFENSE_TYPES, or "" (not labelled yet)
        "points"             points it got them. null = count the made shots of shots.json
                             in that time (the usual case); a number = typed by the coach
        "note"               a few words from the coach, or ""

OFFENSE_TYPES and DEFENSE_TYPES below are THE place to add the coach's own play names:
each is a list of (key saved in the file, words shown on the page).

This file has no web page in it and can be tested on its own.
"""
import argparse

import config
import shots
from shots import Problem, parse_time, show_time

# The labels the coach can give a possession. Add a line to add a play name; the key is
# what the file holds, the words are what the page shows. Keep the keys once chosen.
OFFENSE_TYPES = [
    ("half_court", "Half-court set"),
    ("fast_break", "Fast break"),
    ("press_break", "Press break"),
    ("inbounds", "Inbounds play"),
    ("other", "Other"),
]
DEFENSE_TYPES = [
    ("man", "Man-to-man"),
    ("zone_23", "2-3 zone"),
    ("zone_other", "Other zone"),
    ("press", "Full-court press"),
    ("trap", "Half-court trap"),
    ("other", "Other"),
]
PRESS_KEY = "press"             # the defense the "pressed after a made basket" callout counts

# A made basket is "followed" by a possession that starts within this many seconds of it.
FOLLOWS_WITHIN_S = 30

def label_for(types, key):
    """The words for a key, or "Not labelled yet" for ""."""
    return dict(types).get(key, "Not labelled yet" if key == "" else key)


# ---------------------------------------------------------------- the two files

def load_info(video):
    """game_info.json: {"opponent", "date", "our_team"} (shots.game_info, shared with the Games screen)."""
    return shots.game_info(video)


def save_info(video, changes):
    """Change some keys of game_info.json (other keys, written by other pages, are kept) and return it all."""
    return shots.save_game_info(video, changes)


def load_possessions(video):
    path = shots.game_folder(video) / "possessions.json"
    if path.exists():
        return shots.read_json(path)
    return {"video": str(video), "next_id": 1, "possessions": []}


def save_possessions(video, data):
    config.save_json(config.data_dir(video) / "possessions.json", data, indent=1)


# ---------------------------------------------------------------- our team, their team

def guess_our_team(video):
    """The team label whose jersey numbers best match the home side of roster.json, or None.

    None when the roster has no names, or when both teams match it equally: the coach
    then picks the team on the page.
    """
    teams = shots.load_teams(video)
    roster = shots.read_json(config.ROSTER_PATH) if config.ROSTER_PATH.exists() else {}
    home = (roster.get("home") or {}).get("players") or {} if isinstance(roster, dict) else {}
    if not home:
        return None
    numbers = {label: set() for label in teams["labels"]}
    for team, number in teams["tags"].values():
        if team is not None and number is not None:
            numbers[team].add(str(number))
    scores = {label: len(found & set(home)) for label, found in numbers.items()}
    best = max(scores, key=scores.get)
    return best if list(scores.values()).count(scores[best]) == 1 else None


def sides(video):
    """Which label is us and which is them: {"labels", "colours", "our_team", "their_team", "guessed"}.

    our_team comes from game_info.json; if not chosen yet, the roster guess is used (and
    "guessed" is True) and the page asks the coach to confirm. Both are None when the tool
    has no way to tell.
    """
    teams = shots.load_teams(video)
    ours = load_info(video)["our_team"]
    guessed = False
    if ours not in teams["labels"]:
        # Not chosen yet: the roster guess, else the first team (the Game results page does the same),
        # and the page says it is a guess the coach can change.
        ours, guessed = guess_our_team(video) or (teams["labels"][0] if teams["labels"] else None), True
    theirs = next((label for label in teams["labels"] if label != ours), None) if ours else None
    # teams.json keeps the jersey colours as OpenCV does (blue, green, red); the page wants "#rrggbb"
    colours = ["#%02x%02x%02x" % (int(bgr[2]), int(bgr[1]), int(bgr[0])) for bgr in teams["colours"]]
    return {"labels": teams["labels"], "colours": colours, "our_team": ours, "their_team": theirs, "guessed": guessed}


# ---------------------------------------------------------------- adding, changing, deleting

def check_possession(record, video):
    """Stop with a plain message when the times, team or labels make no sense."""
    teams = shots.load_teams(video)
    if record["end_s"] <= record["start_s"]:
        raise Problem("The possession has to end after it starts. Check the two times.")
    if record["team"] not in teams["labels"]:
        raise Problem("Say which team had the ball.")
    if record["offense"] not in [""] + [key for key, _ in OFFENSE_TYPES]:
        raise Problem("That is not one of the offense types.")
    if record["defense"] not in [""] + [key for key, _ in DEFENSE_TYPES]:
        raise Problem("That is not one of the defense types.")
    if record["points"] is not None and not (0 <= record["points"] <= 10):
        raise Problem("Points for one possession go from 0 to 10. Leave it empty to count the tagged shots.")


def clean_points(points):
    """A typed points value as an int, or None for "count the shots" (blank)."""
    if points is None or str(points).strip() == "":
        return None
    try:
        return int(points)
    except (TypeError, ValueError):
        raise Problem("Points have to be a whole number, or empty to count the tagged shots.")


def add_possession(video, start, end, team, offense="", defense="", points=None, note=""):
    """Record one possession and return it. start and end as "1:23" or seconds."""
    data = load_possessions(video)
    record = {"id": data["next_id"], "start_s": round(parse_time(start), 1), "end_s": round(parse_time(end), 1),
              "team": team, "offense": offense or "", "defense": defense or "",
              "points": clean_points(points), "note": str(note or "").strip()}
    check_possession(record, video)
    data["next_id"] += 1
    data["possessions"].append(record)
    data["possessions"].sort(key=lambda each: (each["start_s"], each["id"]))
    save_possessions(video, data)
    return record


def find_possession(data, possession_id):
    for record in data["possessions"]:
        if record["id"] == possession_id:
            return record
    raise Problem(f"There is no possession {possession_id}. Pick one from the list.")


def edit_possession(video, possession_id, changes):
    """Change some of a possession's fields (start, end, team, offense, defense, points, note) and return it."""
    data = load_possessions(video)
    record = find_possession(data, possession_id)
    for key in ("start", "end"):
        if key in changes:
            record[key + "_s"] = round(parse_time(changes[key]), 1)
    for key in ("team", "offense", "defense", "note"):
        if key in changes:
            record[key] = str(changes[key] or "").strip()
    if "points" in changes:
        record["points"] = clean_points(changes["points"])
    check_possession(record, video)
    data["possessions"].sort(key=lambda each: (each["start_s"], each["id"]))
    save_possessions(video, data)
    return record


def delete_possession(video, possession_id):
    data = load_possessions(video)
    data["possessions"].remove(find_possession(data, possession_id))
    save_possessions(video, data)


# ---------------------------------------------------------------- what the possessions add up to

def shots_in(all_shots, record):
    """The tagged shots of the team with the ball during a possession (the end second belongs to the next one)."""
    return [shot for shot in all_shots
            if shot["team"] == record["team"] and record["start_s"] <= shot["time_s"] < record["end_s"]]


def points_of(record, all_shots):
    """What a possession scored: the coach's number if typed, else the made shots in that time."""
    if record["points"] is not None:
        return record["points"]
    return sum(shot["worth"] for shot in shots_in(all_shots, record) if shot["made"])


def with_points(video):
    """Every possession with "points" filled in and "offense_label"/"defense_label" added, in game order."""
    all_shots = shots.load_shots(video)["shots"]
    listed = []
    for record in load_possessions(video)["possessions"]:
        listed.append(dict(record, points_from="coach" if record["points"] is not None else "shots",
                           points=points_of(record, all_shots),
                           offense_label=label_for(OFFENSE_TYPES, record["offense"]),
                           defense_label=label_for(DEFENSE_TYPES, record["defense"])))
    return listed


def shares(records, key, types):
    """Share of the records by one label: [{"key", "label", "count", "share", "points_each"}], the labelled ones only."""
    labelled = [record for record in records if record[key]]
    rows = []
    for type_key, words in types:
        mine = [record for record in labelled if record[key] == type_key]
        if mine:
            rows.append({"key": type_key, "label": words, "count": len(mine), "share": len(mine) / len(labelled),
                         "points_each": sum(record["points"] for record in mine) / len(mine)})
    return rows


def pressed_after_baskets(records, their_shots, our_team):
    """How often they pressed after they scored: (pressed, baskets).

    A basket is a made field goal of theirs; it "has" the first of our possessions that
    starts within FOLLOWS_WITHIN_S after it. Only baskets with such a possession are
    counted, so the two numbers compare like with like.
    """
    ours = sorted((record for record in records if record["team"] == our_team and record["defense"]),
                  key=lambda record: record["start_s"])
    pressed = baskets = 0
    for shot in their_shots:
        if not (shot["made"] and shot["kind"] == "field goal"):
            continue
        following = next((record for record in ours
                          if shot["time_s"] <= record["start_s"] <= shot["time_s"] + FOLLOWS_WITHIN_S), None)
        if following is not None:
            baskets += 1
            pressed += following["defense"] == PRESS_KEY
    return pressed, baskets


# ---------------------------------------------------------------- where the points came from

def area_of(view, width):
    """Where a shot was taken from, in the words the Tag shots list uses (shots.area_name), capitalised."""
    return shots.area_name(view, width).capitalize()


def shot_marks(video, team):
    """One team's tagged shots as the page draws them: position on one half court, area, who.

    Each mark: {"id", "time_s", "key" (jersey number or "tag 17"), "number", "made", "worth",
    "kind", "across", "from_baseline" (feet; null when the position is unknown), "area"}.
    """
    data, court = shots.load_shots(video), shots.court_size(video)
    marks = []
    for shot in data["shots"]:
        if shot["team"] != team:
            continue
        view = shots.judge(shot, court)
        if shot["kind"] == "free throw":
            area = "Free throws"
        elif view is None:
            area = "Position unknown"
        else:
            area = area_of(view, court[1])
        marks.append({"id": shot["id"], "time_s": shot["time_s"], "key": shots.player_key(shot),
                      "number": shot["number"], "made": shot["made"], "worth": shot["worth"], "kind": shot["kind"],
                      "across": view["across"] if view else None,
                      "from_baseline": view["from_baseline"] if view else None, "area": area})
    return marks


def points_by_area(marks):
    """[{"area", "points"}], most points first, from the made shots among the marks."""
    totals = {}
    for mark in marks:
        if mark["made"]:
            totals[mark["area"]] = totals.get(mark["area"], 0) + mark["worth"]
    return [{"area": area, "points": points} for area, points in sorted(totals.items(), key=lambda item: -item[1])]


def their_players(video, team, marks):
    """The opponent's players, by jersey number: points, shots, threes, time on camera, where they score from.

    Players come from stats.json (everyone with a number on that team) and from the tagged
    shots (a shooter without a number is listed by player tag). Most points first.
    """
    stats_path = shots.game_folder(video) / "stats.json"
    seen = {}
    if stats_path.exists():
        saved = shots.read_json(stats_path)
        for player in saved.get("players", []):
            if player.get("team") == team and player.get("identified"):
                seen[str(player["number"])] = player.get("seen_s")
    players = {key: {"key": key, "number": None if key.startswith("tag") else key, "seen_s": seen.get(key),
                     "points": 0, "made": 0, "tried": 0, "threes_made": 0, "threes_tried": 0, "areas": {}}
               for key in seen}
    for mark in marks:
        player = players.setdefault(mark["key"], {"key": mark["key"], "number": mark["number"], "seen_s": None,
                                                  "points": 0, "made": 0, "tried": 0, "threes_made": 0,
                                                  "threes_tried": 0, "areas": {}})
        if mark["kind"] == "field goal":
            player["tried"] += 1
            player["made"] += mark["made"]
            if mark["worth"] == 3:
                player["threes_tried"] += 1
                player["threes_made"] += mark["made"]
        if mark["made"]:
            player["points"] += mark["worth"]
            player["areas"][mark["area"]] = player["areas"].get(mark["area"], 0) + mark["worth"]
    rows = []
    for player in players.values():
        best = max(player["areas"], key=player["areas"].get) if player["areas"] else None
        rows.append(dict(player, scores_from=best, areas=None))
    rows.sort(key=lambda row: (-row["points"], -(row["seen_s"] or 0), row["key"]))
    return rows


# ---------------------------------------------------------------- the report

def report(video):
    """Everything the opponent report shows, as plain data (see the Opponent page for what each part means)."""
    who = sides(video)
    theirs, ours = who["their_team"], who["our_team"]
    records = with_points(video)
    all_shots = shots.load_shots(video)["shots"]
    their_shots = [shot for shot in all_shots if shot["team"] == theirs] if theirs else []
    field_goals = [shot for shot in their_shots if shot["kind"] == "field goal"]
    threes = [shot for shot in field_goals if shot["worth"] == 3]
    marks = shot_marks(video, theirs) if theirs else []
    labelled = [record for record in records if record["offense"] and record["defense"]]
    pressed, baskets = pressed_after_baskets(records, their_shots, ours) if ours else (0, 0)
    return {
        "info": load_info(video),
        "teams": who,
        "tiles": {"points": sum(shot["worth"] for shot in their_shots if shot["made"]),
                  "field_goals": [sum(shot["made"] for shot in field_goals), len(field_goals)],
                  "threes": [sum(shot["made"] for shot in threes), len(threes)],
                  "labelled": len(labelled)},
        "defense": shares([record for record in records if record["team"] == ours], "defense", DEFENSE_TYPES),
        "offense": shares([record for record in records if record["team"] == theirs], "offense", OFFENSE_TYPES),
        "press": {"pressed": pressed, "baskets": baskets},
        "not_labelled": len(records) - len(labelled),
        "court": dict(zip(("length_ft", "width_ft"), shots.court_size(video))),
        "marks": marks,
        "players": their_players(video, theirs, marks) if theirs else [],
        "shots_tagged": len(their_shots),
    }


# ---------------------------------------------------------------- the command line

def parse_args():
    parser = argparse.ArgumentParser(description="Possessions labelled by hand, and the opponent report made from them.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="video file (default clips/test.mp4)")
    parser.add_argument("--add", nargs=2, metavar=("START", "END"), help="record a possession between these times, like 0:12 0:31")
    parser.add_argument("--team", help="with --add: the team that had the ball, as the tool labels it")
    parser.add_argument("--offense", default="", choices=[""] + [key for key, _ in OFFENSE_TYPES], help="with --add")
    parser.add_argument("--defense", default="", choices=[""] + [key for key, _ in DEFENSE_TYPES], help="with --add")
    parser.add_argument("--points", help="with --add: points scored, instead of counting the tagged shots")
    parser.add_argument("--delete", type=int, metavar="ID", help="delete the possession with this number")
    parser.add_argument("--our-team", help="say which team label is us (saved in game_info.json)")
    return parser.parse_args()


def run(args):
    video = args.video
    config.require_video(video)
    teams = shots.load_teams(video)
    if args.our_team:
        if args.our_team not in teams["labels"]:
            raise Problem(f"The teams of this game are: {', '.join(teams['labels'])}")
        save_info(video, {"our_team": args.our_team})
        print(f"Our team is now \"{args.our_team}\".")
    if args.add:
        if not args.team:
            raise Problem(f"--add needs --team. The teams of this game are: {', '.join(teams['labels'])}")
        record = add_possession(video, args.add[0], args.add[1], args.team, args.offense, args.defense, args.points)
        print(f"Saved possession {record['id']}.")
    if args.delete is not None:
        delete_possession(video, args.delete)
        print(f"Deleted possession {args.delete}.")

    who = sides(video)
    print()
    print(f"Our team: {who['our_team'] or 'not chosen yet (use --our-team)'}; their team: {who['their_team'] or '?'}")
    records = with_points(video)
    print(f"Possessions labelled for {video}: {len(records)}")
    rows = [[record["id"], show_time(record["start_s"]), show_time(record["end_s"]), record["team"],
             record["offense_label"], record["defense_label"], record["points"]] for record in records]
    if rows:
        shots.print_rows(["No.", "Start", "End", "Team", "Offense", "Defense", "Points"], rows)
    if who["their_team"]:
        made = report(video)
        print()
        print(f"Their points tagged: {made['tiles']['points']}; field goals {made['tiles']['field_goals'][0]}/{made['tiles']['field_goals'][1]}")
        for name, rows in (("Their defense (share of our possessions)", made["defense"]),
                           ("Their offense (share of their possessions, points per possession)", made["offense"])):
            print(name)
            for row in rows:
                print(f"  {row['label']:<18} {100 * row['share']:3.0f}%   {row['points_each']:.1f} pts")
        print(f"Pressed after {made['press']['pressed']} of {made['press']['baskets']} made baskets.")


if __name__ == "__main__":
    try:
        run(parse_args())
    except Problem as problem:
        raise SystemExit(str(problem))
