"""The library: seasons, the teams we play, which game belongs where, and the numbers the screens share.

    python src/library.py                     list the seasons, the teams and every game with its season
    python src/library.py --migrate           make the library files (first run), add the v3 keys to each game
    python src/library.py --season 2026-27    print season_summary() as JSON   (--team <id>, --game <stem> the same)

No web code in here: plain functions over the JSON files in the data folder. dash_api.py
answers the browser with them; the screen modules (api_*.py) call them for their numbers.

The files (all under data/, never in git)
    data/_library/seasons.json
        {"seasons": {"<id>": {"name": "2026-27", "start": "2026-11-01", "current": true,
                              "roster": {"name": "<our team name>", "players": {"5": "Name"}},
                              "schedule": [{"id": "2026-11-10-oak-city", "date": "2026-11-10", "time": "16:15",
                                            "opponent_id": "oak-city", "home": true, "score_us": null, "score_them": null,
                                            "stem": null, "kind": "league", "note": ""}]}},
         "roster_imported": true}        (set once the old root roster.json has been copied in)
        Ids are short slugs made from the name ("2026-27"). Exactly one season is current. The schedule is
        the season's fixtures, with or without a video: "stem" names the video once one is linked, and from
        then on the game's game_info.json and the fixture are kept the same (date, opponent, home, scores).
    data/_library/teams.json
        {"teams": {"<id>": {"name": "Riverside", "short": "RIV", "colour": "#1d5fbf",
                            "notes": {"10": "Goes left"}}}}       the opponents; our own team is not a row
    data/<stem>/game_info.json      what the coach said about one game (INFO_DEFAULTS lists every key)
        "season_id" (None = Unassigned), "opponent_id", "opponent" (the name, kept for display), "date",
        "home" (True/False/None), "our_team" (the detected colour group that is us: "<colour>"/None),
        "score_us", "score_them" (the final score as typed; None until typed), "processed_on"
        ("laptop"/"colab"/None), "conditions": {"gym": "", "light": "good"/"dim"/"", "camera": ""}
    data/_dashboard/play_types.json
        {"offense": ["Half-court set", ...], "defense": ["Man-to-man", ...]}   editable by the coach

Where the numbers come from
    the record and the points           the scores the coach typed; tagged points as a stand-in
    FG%, 3PT%, shot maps, leaders       data/<stem>/shots.json (the shots tagged by hand)
    time on camera, distance            data/<stem>/stats.json (never "minutes played": the camera follows the play)
    what a team runs                    data/<stem>/possessions.json (labelled by hand)
    names                               the season's roster (player names never leave this laptop)
"""
import argparse
import datetime
import json
import re

import config
import dash_common
import main
import possessions
import shots

UNASSIGNED = "unassigned"           # the season id of a game that is not in any season

# Every key of game_info.json, with its value before the coach types anything.
INFO_DEFAULTS = {"season_id": None, "opponent_id": None, "opponent": "", "date": "", "home": None, "our_team": None,
                 "score_us": None, "score_them": None, "processed_on": None,
                 "conditions": {"gym": "", "light": "", "camera": ""}}
LIGHTS = ("", "good", "dim")
PROCESSED_ON = (None, "laptop", "colab")

DEFAULT_PLAY_TYPES = {"offense": ["Half-court set", "Fast break", "Press break", "Inbounds play", "Other"],
                      "defense": ["Man-to-man", "2-3 zone", "Other zone", "Full-court press", "Half-court trap", "Other"]}

# A colour for each new team, in turn, so the cards tell them apart.
TEAM_COLOURS = ["#1d5fbf", "#2f7d4f", "#8f1219", "#b35c00", "#5b3fa3", "#0f7c8c", "#7a6a00", "#444a55"]

# Fewer threes than this and a three-point percentage says nothing, so no leader is named for it.
LEADER_MIN_THREES = 5


class Problem(Exception):
    """Something the coach can put right; the text says what."""


# ---------------------------------------------------------------- the files

def library_dir():
    return config.DATA_DIR / "_library"


def play_types_file():
    return config.DATA_DIR / "_dashboard" / "play_types.json"


def read_file(path, default):
    """A JSON file, or `default` when it is missing. A damaged file is a Problem, not a crash."""
    if not path.exists():
        return default
    try:
        return config.load_json(path)
    except SystemExit as stop:
        raise Problem(str(stop))


def write_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    config.save_json(path, data, indent=2)


def slug(name):
    """A short id from a name: "Riverside High" -> "riverside-high", "2026–27" -> "2026-27"."""
    text = str(name).strip().lower().replace("–", "-").replace("—", "-")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "x"


def new_id(name, taken):
    """A slug that is not in `taken` yet (a number is added when the plain one is)."""
    base = slug(name)
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def today():
    return datetime.date.today()


# ---------------------------------------------------------------- seasons

def season_name_for(date):
    """The season a date falls in, as coaches name it: "2026-27" for anything from August 2026 to July 2027."""
    first = date.year if date.month >= 8 else date.year - 1
    return f"{first}-{str(first + 1)[-2:]}"


def clean_season(saved):
    """One season row with every key present and of the right kind."""
    saved = saved if isinstance(saved, dict) else {}
    roster = saved.get("roster") if isinstance(saved.get("roster"), dict) else {}
    players = roster.get("players") if isinstance(roster.get("players"), dict) else {}
    schedule = saved.get("schedule") if isinstance(saved.get("schedule"), list) else []
    return {"name": str(saved.get("name") or "").strip() or "Season", "start": str(saved.get("start") or ""),
            "current": bool(saved.get("current")),
            "roster": {"name": str(roster.get("name") or ""),
                       "players": {str(number): str(name) for number, name in players.items()}},
            "schedule": [row for row in (clean_fixture(entry) for entry in schedule) if row["id"]]}


def load_seasons():
    """seasons.json as {"seasons": {id: season}, "roster_imported": bool}. Makes the first season if there is none.

    Exactly one season is current: the first run makes one from today's date; if the file
    somehow has none or several marked, the newest one is taken.
    """
    data = read_file(library_dir() / "seasons.json", {})
    data = data if isinstance(data, dict) else {}
    seasons = {str(key): clean_season(row) for key, row in (data.get("seasons") or {}).items()
               if isinstance(data.get("seasons"), dict)}
    changed = False
    if not seasons:
        name = season_name_for(today())
        seasons[slug(name)] = {"name": name, "start": today().isoformat(), "current": True,
                               "roster": {"name": "", "players": {}}}
        changed = True
    current = [key for key, row in seasons.items() if row["current"]]
    if len(current) != 1:
        newest = max(seasons, key=lambda key: seasons[key]["start"])
        for key in seasons:
            seasons[key]["current"] = key == newest
        changed = True
    data = {"seasons": seasons, "roster_imported": bool(data.get("roster_imported"))}
    if changed:
        write_file(library_dir() / "seasons.json", data)
    return data


def save_seasons(data):
    write_file(library_dir() / "seasons.json", data)


def current_season_id():
    seasons = load_seasons()["seasons"]
    return next(key for key, row in seasons.items() if row["current"])


def season_or_problem(seasons, season_id):
    if season_id not in seasons:
        raise Problem("There is no season with that name any more. Reload the page.")
    return seasons[season_id]


def check_roster(roster):
    """A roster as the page sends it, checked: {"name", "players": {number: name}}. Problem when a number is not one."""
    roster = roster if isinstance(roster, dict) else {}
    players = {}
    for number, name in (roster.get("players") or {}).items():
        number, name = str(number).strip(), str(name or "").strip()
        if not number:
            continue
        if not number.isdigit() or len(number) > 3:
            raise Problem(f"\"{number}\" is not a jersey number (use the digits only, like 12).")
        if number in players:
            raise Problem(f"Number {number} is listed twice.")
        players[number] = name
    return {"name": str(roster.get("name") or "").strip()[:60], "players": players}


def create_season(name, start=None, roster=None, current=False):
    """Add a season and return (id, season). Its name must be new."""
    name = str(name or "").strip()[:40]
    if not name:
        raise Problem("Give the season a name, like 2026-27.")
    data = load_seasons()
    if any(row["name"].lower() == name.lower() for row in data["seasons"].values()):
        raise Problem(f"There is already a season called {name}.")
    season_id = new_id(name, data["seasons"])
    row = {"name": name, "start": check_date(start) if start else today().isoformat(), "current": False,
           "roster": check_roster(roster or {})}
    data["seasons"][season_id] = row
    if current:
        for key in data["seasons"]:
            data["seasons"][key]["current"] = key == season_id
    save_seasons(data)
    return season_id, data["seasons"][season_id]


def update_season(season_id, changes):
    """Change a season's name, start, roster or current flag (any of them) and return the season."""
    data = load_seasons()
    row = season_or_problem(data["seasons"], season_id)
    if "name" in changes:
        name = str(changes["name"] or "").strip()[:40]
        if not name:
            raise Problem("A season needs a name.")
        if any(other["name"].lower() == name.lower() for key, other in data["seasons"].items() if key != season_id):
            raise Problem(f"There is already a season called {name}.")
        row["name"] = name
    if "start" in changes:
        row["start"] = check_date(changes["start"]) or row["start"]
    if "roster" in changes:
        row["roster"] = check_roster(changes["roster"])
    if changes.get("current"):
        for key in data["seasons"]:
            data["seasons"][key]["current"] = key == season_id
    elif "current" in changes and row["current"]:
        raise Problem("One season is always the current one: mark another season as current instead.")
    save_seasons(data)
    return row


def set_current_season(season_id):
    return update_season(season_id, {"current": True})


def delete_season(season_id):
    """Remove a season (not the current one). Its games become Unassigned."""
    data = load_seasons()
    row = season_or_problem(data["seasons"], season_id)
    if row["current"]:
        raise Problem("The current season cannot be deleted. Mark another season as current first.")
    # The games first, the season last: if a game's file cannot be read part-way, the season is still there.
    for game in dash_common.video_files():
        path = config.DATA_DIR / game.stem / "game_info.json"
        if path.exists() and read_file(path, {}).get("season_id") == season_id:
            save_game_info(game.stem, {"season_id": None})
    del data["seasons"][season_id]
    save_seasons(data)


def import_root_roster_once():
    """Copy the old roster.json (project root, "home" side) into the current season's roster, the first time only.

    Returns True when something was imported. The pipeline's stats.py may still read roster.json;
    the dashboard does not use it after this.
    """
    data = load_seasons()
    if data["roster_imported"]:
        return False
    old = dash_common.load_roster()["home"]
    current = current_season_id()
    row = data["seasons"][current]
    imported = False
    if old["players"] and not row["roster"]["players"]:
        row["roster"] = {"name": old["name"] if old["name"] != "Home" else row["roster"]["name"],
                         "players": dict(old["players"])}
        imported = True
    data["roster_imported"] = True
    save_seasons(data)
    return imported


def roster_for(season_id):
    """{jersey number: name} of a season's roster ({} for Unassigned or an unknown season)."""
    seasons = load_seasons()["seasons"]
    row = seasons.get(season_id) if season_id else None
    return dict(row["roster"]["players"]) if row else {}


# ---------------------------------------------------------------- teams (the opponents)

def clean_team(saved):
    saved = saved if isinstance(saved, dict) else {}
    notes = saved.get("notes") if isinstance(saved.get("notes"), dict) else {}
    return {"name": str(saved.get("name") or "").strip() or "Team", "short": str(saved.get("short") or "")[:3].upper(),
            "colour": str(saved.get("colour") or TEAM_COLOURS[0]),
            "notes": {str(number): str(text) for number, text in notes.items()}}


def load_teams():
    data = read_file(library_dir() / "teams.json", {})
    teams = data.get("teams") if isinstance(data, dict) and isinstance(data.get("teams"), dict) else {}
    return {"teams": {str(key): clean_team(row) for key, row in teams.items()}}


def save_teams(data):
    write_file(library_dir() / "teams.json", data)


def short_name(name):
    """Up to three capitals from a name: "Riverside High" -> "RH", "Eagles" -> "EAG"."""
    words = re.findall(r"[A-Za-z0-9]+", name)
    if len(words) >= 2:
        return "".join(word[0] for word in words[:3]).upper()
    return name[:3].upper()


def find_team(name):
    """The id of the team with that name (ignoring case), or None."""
    name = str(name or "").strip().lower()
    for key, row in load_teams()["teams"].items():
        if row["name"].lower() == name:
            return key
    return None


def check_colour(colour, fallback):
    colour = str(colour or "").strip().lower()
    return colour if re.fullmatch(r"#[0-9a-f]{6}", colour) else fallback


def create_team(name, short=None, colour=None):
    """Add an opponent and return (id, team). An existing name is a Problem: use find_team first."""
    name = str(name or "").strip()[:60]
    if not name:
        raise Problem("Give the team a name.")
    if find_team(name):
        raise Problem(f"There is already a team called {name}.")
    data = load_teams()
    team_id = new_id(name, data["teams"])
    row = {"name": name, "short": (str(short or "").strip()[:3].upper() or short_name(name)),
           "colour": check_colour(colour, TEAM_COLOURS[len(data["teams"]) % len(TEAM_COLOURS)]), "notes": {}}
    data["teams"][team_id] = row
    save_teams(data)
    return team_id, row


def team_or_create(name):
    """The id of the team with that name, made if it is new. "" gives None."""
    if not str(name or "").strip():
        return None
    return find_team(name) or create_team(name)[0]


def update_team(team_id, changes):
    """Change a team's name, short, colour or notes ({number: text}; an empty text removes the note)."""
    data = load_teams()
    if team_id not in data["teams"]:
        raise Problem("There is no team with that id any more. Reload the page.")
    row = data["teams"][team_id]
    if "name" in changes:
        name = str(changes["name"] or "").strip()[:60]
        if not name:
            raise Problem("A team needs a name.")
        other = find_team(name)
        if other and other != team_id:
            raise Problem(f"There is already a team called {name}.")
        row["name"] = name
    if "short" in changes:
        row["short"] = str(changes["short"] or "").strip()[:3].upper() or short_name(row["name"])
    if "colour" in changes:
        row["colour"] = check_colour(changes["colour"], row["colour"])
    if "notes" in changes and isinstance(changes["notes"], dict):
        for number, text in changes["notes"].items():
            text = str(text or "").strip()[:200]
            if text:
                row["notes"][str(number)] = text
            else:
                row["notes"].pop(str(number), None)
    save_teams(data)
    return row


# ---------------------------------------------------------------- play types

def load_play_types():
    saved = read_file(play_types_file(), {})
    types = {}
    for side in ("offense", "defense"):
        listed = saved.get(side) if isinstance(saved, dict) and isinstance(saved.get(side), list) else None
        listed = [str(item).strip() for item in (listed or []) if str(item).strip()]
        types[side] = listed or list(DEFAULT_PLAY_TYPES[side])
    return types


def save_play_types(types):
    clean = {}
    for side in ("offense", "defense"):
        listed = types.get(side) if isinstance(types, dict) else None
        if not isinstance(listed, list):
            raise Problem(f"The {side} play types must be a list of names.")
        names = []
        for item in listed:
            name = str(item or "").strip()[:40]
            if name and name.lower() not in (seen.lower() for seen in names):
                names.append(name)
        if not names:
            raise Problem(f"Keep at least one {side} play type.")
        clean[side] = names
    write_file(play_types_file(), clean)
    return clean


def play_label(side, value):
    """The words for a possession's offense or defense: a v2 key ("half_court") or a v3 name, as saved."""
    if not value:
        return ""
    keyed = dict(possessions.OFFENSE_TYPES if side == "offense" else possessions.DEFENSE_TYPES)
    return keyed.get(value, str(value))


# ---------------------------------------------------------------- games and their info

def video_of(stem):
    """"test" -> "clips/test.mp4", or None when no such video is in the clips folder."""
    for path in dash_common.video_files():
        if path.stem == stem:
            return f"clips/{path.name}"
    return None


def check_date(text):
    text = str(text or "").strip()
    if not text:
        return ""
    try:
        return datetime.date.fromisoformat(text).isoformat()
    except ValueError:
        raise Problem("The date should look like 2026-01-24.")


def check_score(value, what):
    if value is None or str(value).strip() == "":
        return None
    try:
        score = int(str(value).strip())
    except ValueError:
        raise Problem(f"{what} score has to be a whole number.")
    if not 0 <= score <= 300:
        raise Problem(f"{what} score has to be between 0 and 300.")
    return score


def game_info(stem):
    """What the coach said about a game, every key of INFO_DEFAULTS present (see the top of this file).

    Also "date_is_guess": True while no date was typed (the day the video file was last
    changed stands in), and "opponent" filled from the team when only the id is saved.
    """
    saved = read_file(config.DATA_DIR / stem / "game_info.json", {})
    saved = saved if isinstance(saved, dict) else {}
    info = json.loads(json.dumps(INFO_DEFAULTS))
    for key in INFO_DEFAULTS:
        if key in saved and key != "conditions":
            info[key] = saved[key]
    conditions = saved.get("conditions") if isinstance(saved.get("conditions"), dict) else {}
    for key in INFO_DEFAULTS["conditions"]:
        info["conditions"][key] = str(conditions.get(key) or "")
    info["opponent"] = str(info["opponent"] or "").strip()
    if info["opponent_id"] and not info["opponent"]:
        team = load_teams()["teams"].get(info["opponent_id"])
        info["opponent"] = team["name"] if team else ""
    info["date_is_guess"] = not info["date"]
    if not info["date"]:
        video = video_of(stem)
        try:
            info["date"] = datetime.date.fromtimestamp(config.video_path(video).stat().st_mtime).isoformat() if video else ""
        except OSError:
            info["date"] = ""
    return info


def check_info(stem, changes):
    """The changes to a game's info as the page sends them, checked and in the file's shape."""
    clean = {}
    if "season_id" in changes:
        season_id = changes["season_id"] or None
        if season_id and season_id != UNASSIGNED and season_id not in load_seasons()["seasons"]:
            raise Problem("There is no such season. Reload the page.")
        clean["season_id"] = None if season_id == UNASSIGNED else season_id
    if "opponent_id" in changes or "opponent" in changes:
        team_id = changes.get("opponent_id") or None
        if team_id and team_id not in load_teams()["teams"]:
            raise Problem("There is no such team. Reload the page.")
        if not team_id and "opponent" in changes:
            team_id = team_or_create(str(changes["opponent"])[:60])       # typing a new name makes the team
        clean["opponent_id"] = team_id
        clean["opponent"] = load_teams()["teams"][team_id]["name"] if team_id else ""
    if "date" in changes:
        clean["date"] = check_date(changes["date"])
    if "home" in changes:
        clean["home"] = None if changes["home"] is None else bool(changes["home"])
    if "our_team" in changes:
        labels = team_labels(stem)
        if changes["our_team"] is not None and changes["our_team"] not in labels:
            raise Problem("The teams of this game are: " + " and ".join(labels) + "." if labels
                          else "This game has not been processed yet, so its teams are not known.")
        clean["our_team"] = changes["our_team"]
    for key, what in (("score_us", "Our"), ("score_them", "Their")):
        if key in changes:
            clean[key] = check_score(changes[key], what)
    if "processed_on" in changes:
        if changes["processed_on"] not in PROCESSED_ON:
            raise Problem("\"Processed on\" is the laptop or Colab.")
        clean["processed_on"] = changes["processed_on"]
    if "conditions" in changes:
        given = changes["conditions"] if isinstance(changes["conditions"], dict) else {}
        conditions = dict(game_info(stem)["conditions"])
        for key in conditions:
            if key in given:
                conditions[key] = str(given[key] or "").strip()[:60]
        if conditions["light"] not in LIGHTS:
            raise Problem("The light is \"good\" or \"dim\".")
        clean["conditions"] = conditions
    return clean


def save_game_info(stem, changes, check=True, mirror=True):
    """Change some keys of a game's game_info.json (the others are kept) and return the whole thing.

    The game's fixture on the schedule, if it has one, takes the same date, home, opponent and scores
    (mirror=False when the fixture is the one writing, so the two never call each other)."""
    if not video_of(stem):
        raise Problem("That game is not in the clips folder any more.")
    clean = check_info(stem, changes) if check else changes
    path = config.DATA_DIR / stem / "game_info.json"
    saved = read_file(path, {})
    saved = saved if isinstance(saved, dict) else {}
    saved.update(clean)
    write_file(path, saved)
    if mirror and any(key in clean for key in FIXTURE_GAME_KEYS + ("season_id",)):
        mirror_game_into_fixture(stem, dict(clean, season_id=saved.get("season_id")))
    return game_info(stem)


def assign_game(stem, season_id=None, opponent=None, **more):
    """Put a game in a season and against an opponent (a name: the team is made if new). Returns the info."""
    changes = dict(more)
    if season_id is not None:
        changes["season_id"] = season_id
    if opponent is not None:
        changes["opponent"] = opponent
    return save_game_info(stem, changes)


def list_games():
    """Every video in clips/ as dash_common.list_games() gives it, plus "info" (game_info) and "season_id"."""
    games = []
    known = load_seasons()["seasons"]
    for game in dash_common.list_games():
        game["info"] = game_info(game["name"])
        # A season that no longer exists (deleted, or seasons.json replaced) must not hide the game: it is Unassigned.
        season_id = game["info"]["season_id"]
        game["season_id"] = season_id if season_id in known else UNASSIGNED
        games.append(game)
    return games


def games_in(season_id=None, team_id=None):
    """The games of a season (UNASSIGNED for the ones without), or against a team, oldest first."""
    found = []
    for game in list_games():
        if season_id is not None and game["season_id"] != season_id:
            continue
        if team_id is not None and game["info"]["opponent_id"] != team_id:
            continue
        found.append(game)
    found.sort(key=lambda game: (game["info"]["date"], game["name"]))
    return found


# ---------------------------------------------------------------- the schedule (the season's fixtures)

FIXTURE_KINDS = ("", "league", "non-league", "tournament")
FIXTURE_GAME_KEYS = ("date", "home", "opponent_id", "score_us", "score_them")     # kept the same in game_info.json


def clean_fixture(saved):
    """One fixture row with every key present and of the right kind (an id of "" means: drop it)."""
    saved = saved if isinstance(saved, dict) else {}

    def score(value):
        try:
            return int(value) if value is not None and str(value).strip() != "" else None
        except (TypeError, ValueError):
            return None
    return {"id": str(saved.get("id") or ""), "date": str(saved.get("date") or ""), "time": str(saved.get("time") or "")[:5],
            "opponent_id": str(saved.get("opponent_id") or "") or None,
            "home": None if saved.get("home") is None else bool(saved.get("home")),
            "score_us": score(saved.get("score_us")), "score_them": score(saved.get("score_them")),
            "stem": str(saved.get("stem") or "") or None,
            "kind": saved.get("kind") if saved.get("kind") in FIXTURE_KINDS else "",
            "note": str(saved.get("note") or "")[:120]}


def check_time(text):
    text = str(text or "").strip()
    if not text:
        return ""
    try:
        return datetime.time.fromisoformat(text).strftime("%H:%M")
    except ValueError:
        raise Problem("The time should look like 16:15 (24-hour clock).")


def check_fixture_changes(season, fixture, changes):
    """The changes to a fixture as the page sends them, checked, applied onto `fixture` (a copy is returned)."""
    row = dict(fixture)
    if "date" in changes:
        row["date"] = check_date(changes["date"])
        if not row["date"]:
            raise Problem("A scheduled game needs a date.")
    if "time" in changes:
        row["time"] = check_time(changes["time"])
    if "opponent_id" in changes or "opponent" in changes:
        team_id = changes.get("opponent_id") or None
        if team_id and team_id not in load_teams()["teams"]:
            raise Problem("There is no such team. Reload the page.")
        if not team_id and str(changes.get("opponent") or "").strip():
            team_id = team_or_create(str(changes["opponent"]).strip()[:60])
        if not team_id:
            raise Problem("Name the opponent.")
        row["opponent_id"] = team_id
    if "home" in changes:
        row["home"] = None if changes["home"] is None else bool(changes["home"])
    for key, what in (("score_us", "Our"), ("score_them", "Their")):
        if key in changes:
            row[key] = check_score(changes[key], what)
    if "kind" in changes:
        kind = str(changes["kind"] or "")
        if kind not in FIXTURE_KINDS:
            raise Problem("The kind of game is league, non-league or tournament.")
        row["kind"] = kind
    if "note" in changes:
        row["note"] = str(changes["note"] or "").strip()[:120]
    if "stem" in changes:
        stem = str(changes["stem"] or "").strip() or None
        if stem and not video_of(stem):
            raise Problem("That video is not in the clips folder.")
        if stem and any(other["stem"] == stem and other["id"] != row["id"] for other in season["schedule"]):
            raise Problem("That video is already linked to another game of this season.")
        row["stem"] = stem
    return row


def fixture_or_problem(season, fixture_id):
    for row in season["schedule"]:
        if row["id"] == fixture_id:
            return row
    raise Problem("That scheduled game is not in the season any more. Reload the page.")


def fixture_of_stem(stem):
    """(season_id, fixture) of the fixture a video is linked to, or (None, None)."""
    for season_id, season in load_seasons()["seasons"].items():
        for row in season["schedule"]:
            if row["stem"] == stem:
                return season_id, row
    return None, None


def push_fixture_into_game(season_id, fixture):
    """A linked video takes the fixture's season, opponent, date, home and scores (the game's own typed score wins)."""
    stem = fixture["stem"]
    if not stem or not video_of(stem):
        return
    changes = {"season_id": season_id, "opponent_id": fixture["opponent_id"], "date": fixture["date"], "home": fixture["home"]}
    info = game_info(stem)
    if info["score_us"] is None and info["score_them"] is None:
        changes["score_us"], changes["score_them"] = fixture["score_us"], fixture["score_them"]
    save_game_info(stem, changes, mirror=False)


def mirror_game_into_fixture(stem, info):
    """What the coach typed on a game (date, home, opponent, scores) is copied into its fixture, if it has one."""
    data = load_seasons()
    for season_id, season in data["seasons"].items():
        for row in season["schedule"]:
            if row["stem"] != stem:
                continue
            if info.get("season_id") not in (None, season_id):
                row["stem"] = None                          # the game moved to another season: the fixture lets go
            else:
                for key in FIXTURE_GAME_KEYS:
                    if key in info and (info[key] is not None or key in ("score_us", "score_them")):
                        row[key] = info[key]
            save_seasons(data)
            return


def add_fixture(season_id, fields):
    """Add a game to a season's schedule and return the fixture. fields: date, time, opponent (name) or opponent_id,
    home, score_us, score_them, kind, note, stem (a video to link)."""
    data = load_seasons()
    season = season_or_problem(data["seasons"], season_id)
    row = check_fixture_changes(season, clean_fixture({"id": "new"}), dict(fields))
    if not row["date"]:
        raise Problem("A scheduled game needs a date.")
    if not row["opponent_id"]:
        raise Problem("Name the opponent.")
    team = load_teams()["teams"][row["opponent_id"]]
    row["id"] = new_id(f"{row['date']}-{team['name']}", {other["id"] for other in season["schedule"]})
    season["schedule"].append(row)
    season["schedule"].sort(key=lambda entry: (entry["date"], entry["time"]))
    save_seasons(data)
    if row["stem"]:
        push_fixture_into_game(season_id, row)
    return row


def update_fixture(season_id, fixture_id, changes):
    """Change a fixture (any of its fields; "stem" links or unlinks a video) and return it."""
    data = load_seasons()
    season = season_or_problem(data["seasons"], season_id)
    old = fixture_or_problem(season, fixture_id)
    row = check_fixture_changes(season, old, changes if isinstance(changes, dict) else {})
    season["schedule"][season["schedule"].index(old)] = row
    season["schedule"].sort(key=lambda entry: (entry["date"], entry["time"]))
    save_seasons(data)
    if row["stem"]:
        push_fixture_into_game(season_id, row)
    return row


def delete_fixture(season_id, fixture_id):
    """Take a game off the schedule. A linked video stays in the season (as an unscheduled game)."""
    data = load_seasons()
    season = season_or_problem(data["seasons"], season_id)
    row = fixture_or_problem(season, fixture_id)
    season["schedule"].remove(row)
    save_seasons(data)


def import_schedule(season_id, rows):
    """Add many fixtures at once (a schedule typed or read from a screenshot). A game on the same date against the
    same opponent is already there and is skipped. Returns (added, skipped)."""
    added = skipped = 0
    for fields in rows:
        fields = dict(fields)
        opponent = str(fields.get("opponent") or "").strip()
        date = check_date(fields.get("date"))
        season = load_seasons()["seasons"][season_id]
        teams = load_teams()["teams"]
        same = [row for row in season["schedule"] if row["date"] == date
                and teams.get(row["opponent_id"], {}).get("name", "").lower() == opponent.lower()]
        if same:
            skipped += 1
            continue
        add_fixture(season_id, fields)
        added += 1
    return added, skipped


def schedule_rows(season_id):
    """The season's games in date order: every fixture (with its video's row when one is linked) and every
    video of the season that is on no fixture ("unscheduled": True). Each row has the game_row keys where a
    video exists, else result/points from the fixture's typed score and "stem": None."""
    seasons = load_seasons()["seasons"]
    teams = load_teams()["teams"]
    games = {game["name"]: game for game in games_in(season_id=season_id)}
    season = seasons.get(season_id) if season_id != UNASSIGNED else None
    rows, linked = [], set()
    for fixture in (season["schedule"] if season else []):
        base = {"fixture_id": fixture["id"], "time": fixture["time"], "kind": fixture["kind"], "note": fixture["note"],
                "unscheduled": False}
        stem = fixture["stem"] if fixture["stem"] in games else None
        if stem:
            linked.add(stem)
            row = dict(game_row(games[stem]), **base)
        else:
            typed = fixture["score_us"] is not None and fixture["score_them"] is not None
            team = teams.get(fixture["opponent_id"]) or {}
            row = dict(base, stem=None, video=None, name="", date=fixture["date"], date_is_guess=False, season_id=season_id,
                       opponent=team.get("name", ""), opponent_id=fixture["opponent_id"], home=fixture["home"],
                       status="no video", running=False, seconds=0, shots=0, stand_in_court=False, processed_on=None,
                       roster_changed=False, score_us=fixture["score_us"], score_them=fixture["score_them"],
                       tagged_us=0, tagged_them=0,
                       points_us=fixture["score_us"] if typed else None, points_them=fixture["score_them"] if typed else None,
                       points_from="scores" if typed else None, result=None)
            if typed:
                row["result"] = ("W" if fixture["score_us"] > fixture["score_them"]
                                 else "L" if fixture["score_us"] < fixture["score_them"] else "T")
        rows.append(row)
    for stem, game in games.items():
        if stem not in linked:
            rows.append(dict(game_row(game), fixture_id=None, time="", kind="", note="", unscheduled=True))
    rows.sort(key=lambda row: (row["date"] or "9999", row["time"], row.get("stem") or ""))
    return rows


# ---------------------------------------------------------------- reading one game's result files

def team_labels(stem):
    """The two colour labels of a game ("<colour>", "<colour>"), as teams.json names them; [] before the teams stage."""
    teams = read_file(config.DATA_DIR / stem / "teams.json", {})
    if not isinstance(teams, dict) or "teams" not in teams:
        return []
    return [teams["teams"][key]["label"] for key in ("0", "1") if key in teams["teams"]]


def team_colours(stem):
    """The two jersey colours as "#rrggbb", in the order of team_labels (for drawing)."""
    teams = read_file(config.DATA_DIR / stem / "teams.json", {})
    if not isinstance(teams, dict) or "teams" not in teams:
        return []
    return [dash_common.hex_colour(teams["teams"][key]["color_bgr"]) for key in ("0", "1") if key in teams["teams"]]


def jersey_numbers(stem):
    """{label: set of jersey numbers read on that team}, from teams.json and jerseys.json."""
    teams = read_file(config.DATA_DIR / stem / "teams.json", {})
    jerseys = read_file(config.DATA_DIR / stem / "jerseys.json", {})
    labels = team_labels(stem)
    numbers = {label: set() for label in labels}
    if not labels or not isinstance(jerseys, dict):
        return numbers
    for key, index in (teams.get("tracks") or {}).items():
        number = (jerseys.get("tracks") or {}).get(key, {}).get("number")
        if index is not None and number is not None:
            numbers[labels[index]].add(str(number))
    return numbers


def sides(stem):
    """Which colour group is us and which is them: {"labels", "colours", "our_team", "their_team", "guessed"}.

    our_team is what the coach chose in game_info.json. Until chosen, the group whose jersey
    numbers match the season's roster best ("guessed": True), else the first group.
    """
    labels, info = team_labels(stem), game_info(stem)
    ours, guessed = info["our_team"], False
    if ours not in labels:
        ours, guessed = None, True
        roster = set(roster_for(info["season_id"] or current_season_id()))
        if labels and roster:
            scores = {label: len(found & roster) for label, found in jersey_numbers(stem).items()}
            best = max(scores, key=scores.get)
            if list(scores.values()).count(scores[best]) == 1:
                ours = best
        if ours is None and labels:
            ours = labels[0]
    theirs = next((label for label in labels if label != ours), None) if ours else None
    return {"labels": labels, "colours": team_colours(stem), "our_team": ours, "their_team": theirs, "guessed": guessed}


def shots_of(stem):
    """The tagged shots of a game, as shots.json lists them ([] when nothing is tagged)."""
    saved = read_file(config.DATA_DIR / stem / "shots.json", {})
    return [shot for shot in saved.get("shots", []) if isinstance(shot, dict)] if isinstance(saved, dict) else []


def possessions_of(stem):
    """The possessions of a game with their points filled in and the play labels added ([] when none)."""
    if not (config.DATA_DIR / stem / "possessions.json").exists():
        return []
    try:
        records = possessions.load_possessions(f"clips/{stem}")["possessions"]
    except (shots.Problem, SystemExit):
        return []
    all_shots = shots_of(stem)
    listed = []
    for record in records:
        listed.append(dict(record, points=possessions.points_of(record, all_shots),
                           offense_label=play_label("offense", record.get("offense")),
                           defense_label=play_label("defense", record.get("defense")),
                           turnover=bool(record.get("turnover"))))
    return listed


def stats_of(stem):
    """stats.json ({} before the stats stage)."""
    saved = read_file(config.DATA_DIR / stem / "stats.json", {})
    return saved if isinstance(saved, dict) and "players" in saved else {}


def court_of(stem):
    """{"length_ft", "width_ft"} of the game's court."""
    return dict(zip(("length_ft", "width_ft"), shots.court_size(f"clips/{stem}")))


def marks_of(stem, label):
    """One team's tagged shots folded onto one half court (possessions.shot_marks), [] when it cannot be done."""
    video = video_of(stem)
    if not video or not label:
        return []
    try:
        marks = possessions.shot_marks(video, label)
    except (shots.Problem, SystemExit):
        return []
    for mark in marks:
        mark["game"] = stem
    return marks


def made_tried(shot_list):
    made, tried = sum(1 for shot in shot_list if shot.get("made")), len(shot_list)
    return {"made": made, "tried": tried, "pct": round(100 * made / tried) if tried else None}


def score_tiles(shot_list):
    """Points, field goals, threes and free throws from one team's shots: {"points", "field_goals", "threes", "free_throws"}."""
    field_goals = [shot for shot in shot_list if shot.get("kind") == "field goal"]
    return {"points": sum(int(shot.get("worth") or 0) for shot in shot_list if shot.get("made")),
            "field_goals": made_tried(field_goals),
            "threes": made_tried([shot for shot in field_goals if shot.get("worth") == 3]),
            "free_throws": made_tried([shot for shot in shot_list if shot.get("kind") == "free throw"])}


def player_rows(stem, label, names):
    """The players table of one colour group: the stats' time and distance, the tagged shots' points, the roster's names.

    Each row: {"number", "name", "points", "field_goals", "threes", "free_throws" (each {made, tried, pct}),
    "seen_s", "on_map_s", "distance_ft", "paint_pct", "two_places", "tracking_ids"}, most points first.
    A shooter with a number but no stats row (the stats are not made yet) still gets a row.
    Returns (rows, how many of that group's tags never got a number).
    """
    stats = stats_of(stem)
    by_number = {}
    for shot in shots_of(stem):
        if shot.get("team") == label and shot.get("number") is not None:
            by_number.setdefault(str(shot["number"]), []).append(shot)
    rows, seen_numbers = [], set()
    for row in stats.get("players", []):
        if row.get("team") != label or not row.get("identified"):
            continue
        number = str(row["number"])
        seen_numbers.add(number)
        rows.append(dict(score_tiles(by_number.get(number, [])), number=number, name=names.get(number, ""),
                         seen_s=row.get("seen_s"), on_map_s=row.get("on_map_s"), distance_ft=row.get("distance_ft"),
                         paint_pct=row.get("paint_pct"), two_places=bool(row.get("two_places_frames")),
                         tracking_ids=row.get("tracking_ids", [])))
    for number, mine in by_number.items():
        if number not in seen_numbers:
            rows.append(dict(score_tiles(mine), number=number, name=names.get(number, ""), seen_s=None, on_map_s=None,
                             distance_ft=None, paint_pct=None, two_places=False, tracking_ids=[]))
    rows.sort(key=lambda row: (-row["points"], -(row["seen_s"] or 0), int(row["number"])))
    unnumbered = sum(1 for row in stats.get("players", []) if row.get("team") == label and not row.get("identified"))
    return rows, unnumbered


# ---------------------------------------------------------------- one game

def game_row(game):
    """One game as the season and team pages list it: the listing plus the scores and the result.

    "points_us"/"points_them" are the typed scores when both are typed, else the tagged points
    ("points_from": "scores" | "tags" | None); "result" ("W"/"L"/"T") only from typed scores.
    """
    stem, info = game["name"], game["info"]
    who = sides(stem)
    mine = [shot for shot in shots_of(stem) if shot.get("team") == who["our_team"]] if who["our_team"] else []
    theirs = [shot for shot in shots_of(stem) if shot.get("team") == who["their_team"]] if who["their_team"] else []
    tagged_us = sum(int(shot.get("worth") or 0) for shot in mine if shot.get("made"))
    tagged_them = sum(int(shot.get("worth") or 0) for shot in theirs if shot.get("made"))
    typed = info["score_us"] is not None and info["score_them"] is not None
    row = {"stem": stem, "video": game["video"], "name": stem, "date": info["date"], "date_is_guess": info["date_is_guess"],
           "season_id": game["season_id"], "opponent": info["opponent"], "opponent_id": info["opponent_id"],
           "home": info["home"], "status": game["status"], "running": game["running"], "seconds": game["seconds"],
           "shots": game["shots"], "stand_in_court": game["stand_in_court"], "processed_on": info["processed_on"],
           "roster_changed": game["roster_changed"],
           "score_us": info["score_us"], "score_them": info["score_them"], "tagged_us": tagged_us, "tagged_them": tagged_them,
           "points_us": info["score_us"] if typed else (tagged_us if mine or theirs else None),
           "points_them": info["score_them"] if typed else (tagged_them if mine or theirs else None),
           "points_from": "scores" if typed else ("tags" if mine or theirs else None), "result": None}
    if typed:
        row["result"] = "W" if info["score_us"] > info["score_them"] else "L" if info["score_us"] < info["score_them"] else "T"
    return row


def record_of(rows):
    """{"wins", "losses", "ties", "text": "3–1"} from the games' typed results."""
    wins = sum(1 for row in rows if row["result"] == "W")
    losses = sum(1 for row in rows if row["result"] == "L")
    ties = sum(1 for row in rows if row["result"] == "T")
    text = f"{wins}–{losses}" + (f"–{ties}" if ties else "")
    return {"wins": wins, "losses": losses, "ties": ties, "text": text}


def per_game(rows, key):
    """The average of one key over the games that have it, or None."""
    values = [row[key] for row in rows if row[key] is not None]
    return round(sum(values) / len(values), 1) if values else None


def game_summary(stem):
    """Everything the Game screen starts from (api_game.py adds the video's moment-by-moment pieces).

    {"game": the listing row (status, stages, running...), "info": game_info, "season": {"id", "name"} or None,
     "opponent": {"id", "name", "short", "colour", "notes"} or None, "teams": sides(),
     "row": game_row (scores, result, tagged points), "tiles": {"us", "them"} (score_tiles),
     "players": {"us": rows, "them": rows} (player_rows), "players_without_number": {"us", "them"},
     "marks": {"us", "them"} (folded shots for the heat map), "court", "possessions": possessions_of(),
     "videos": {"game": "/files/clips/<file>", "tactical", "tracked" (when made)}, "stats_csv" (as Windows names it),
     "warnings": the stats' warnings, "has_stats", "stand_in_court"}
    """
    game = next((game for game in list_games() if game["name"] == stem), None)
    if game is None:
        raise Problem("That game is not in the clips folder any more.")
    info, who = game["info"], sides(stem)
    seasons, teams = load_seasons()["seasons"], load_teams()["teams"]
    season = seasons.get(info["season_id"]) if info["season_id"] else None
    opponent = teams.get(info["opponent_id"]) if info["opponent_id"] else None
    names = roster_for(info["season_id"] or current_season_id())
    us, us_unnumbered = player_rows(stem, who["our_team"], names) if who["our_team"] else ([], 0)
    them, them_unnumbered = player_rows(stem, who["their_team"], {}) if who["their_team"] else ([], 0)
    all_shots = shots_of(stem)
    mine = [shot for shot in all_shots if shot.get("team") == who["our_team"]]
    theirs = [shot for shot in all_shots if shot.get("team") == who["their_team"]]
    out = config.OUTPUTS_DIR / stem
    videos = {"game": f"/files/{game['video']}"}
    for name in ("tactical", "tracked"):
        if (out / f"{name}.mp4").is_file():
            videos[name] = f"/files/outputs/{stem}/{name}.mp4"
    stats = stats_of(stem)
    csv_path = config.DATA_DIR / stem / "stats.csv"
    return {"game": game, "info": info, "season": dict(season, id=info["season_id"]) if season else None,
            "opponent": dict(opponent, id=info["opponent_id"]) if opponent else None, "teams": who,
            "row": game_row(game), "tiles": {"us": score_tiles(mine), "them": score_tiles(theirs)},
            "players": {"us": us, "them": them}, "players_without_number": {"us": us_unnumbered, "them": them_unnumbered},
            "marks": {"us": marks_of(stem, who["our_team"]), "them": marks_of(stem, who["their_team"])},
            "court": court_of(stem), "possessions": possessions_of(stem), "videos": videos,
            "stats_csv": main.windows_path(csv_path) if csv_path.is_file() else None,
            "warnings": stats.get("warnings", []), "has_stats": bool(stats), "stand_in_court": game["stand_in_court"]}


# ---------------------------------------------------------------- one season

def area_shares(marks):
    """Share of the points from the paint, from threes and from free throws: {"paint", "threes", "free_throws"} in %."""
    points = sum(mark["worth"] for mark in marks if mark["made"])
    if not points:
        return {"paint": None, "threes": None, "free_throws": None}
    paint = sum(mark["worth"] for mark in marks if mark["made"] and mark["area"].lower() in ("paint", "at the rim"))
    threes = sum(mark["worth"] for mark in marks if mark["made"] and mark["worth"] == 3)
    free = sum(mark["worth"] for mark in marks if mark["made"] and mark["kind"] == "free throw")
    return {"paint": round(100 * paint / points), "threes": round(100 * threes / points), "free_throws": round(100 * free / points)}


def season_players(rows, names):
    """Season totals per jersey number over the given game rows (our team): the Players page and the leaders.

    Each: {"number", "name", "games" (games the number was seen or shot in), "points", "points_per_game",
    "field_goals", "threes", "free_throws" ({made, tried, pct}), "seen_s", "distance_ft", "distance_per_game_ft"}.
    """
    totals = {}
    for row in rows:
        who = sides(row["stem"])
        if not who["our_team"]:
            continue
        players, _ = player_rows(row["stem"], who["our_team"], names)
        for player in players:
            total = totals.setdefault(player["number"], {
                "number": player["number"], "name": player["name"], "games": 0, "points": 0, "seen_s": 0.0, "distance_ft": 0.0,
                "field_goals": {"made": 0, "tried": 0}, "threes": {"made": 0, "tried": 0}, "free_throws": {"made": 0, "tried": 0}})
            total["games"] += 1
            total["points"] += player["points"]
            total["seen_s"] += player["seen_s"] or 0
            total["distance_ft"] += player["distance_ft"] or 0
            for key in ("field_goals", "threes", "free_throws"):
                total[key]["made"] += player[key]["made"]
                total[key]["tried"] += player[key]["tried"]
    listed = []
    for total in totals.values():
        for key in ("field_goals", "threes", "free_throws"):
            tried = total[key]["tried"]
            total[key]["pct"] = round(100 * total[key]["made"] / tried) if tried else None
        total["points_per_game"] = round(total["points"] / total["games"], 1) if total["games"] else None
        total["distance_per_game_ft"] = round(total["distance_ft"] / total["games"]) if total["games"] else None
        total["seen_s"] = round(total["seen_s"], 1)
        total["distance_ft"] = round(total["distance_ft"])
        listed.append(total)
    listed.sort(key=lambda total: (-total["points"], -total["seen_s"], int(total["number"])))
    return listed


def leaders_of(players):
    """The three leaders the season page shows: [{"number", "name", "label", "value", "text"}] (missing when no data)."""
    leaders = []
    scorers = [player for player in players if player["points"] > 0]
    if scorers:
        best = max(scorers, key=lambda player: player["points_per_game"])
        leaders.append({"number": best["number"], "name": best["name"], "label": "Points per game",
                        "value": best["points_per_game"], "text": f"{best['points_per_game']:.1f}"})
    shooters = [player for player in players if player["threes"]["tried"] >= LEADER_MIN_THREES]
    if shooters:
        best = max(shooters, key=lambda player: player["threes"]["pct"])
        leaders.append({"number": best["number"], "name": best["name"], "label": "Three-point %",
                        "value": best["threes"]["pct"], "text": f"{best['threes']['pct']}%"})
    movers = [player for player in players if player["distance_ft"] > 0]
    if movers:
        best = max(movers, key=lambda player: player["distance_per_game_ft"])
        miles = best["distance_per_game_ft"] / 5280
        text = f"{miles:.1f} mi" if miles >= 0.1 else f"{best['distance_per_game_ft']} ft"
        leaders.append({"number": best["number"], "name": best["name"], "label": "Distance per game",
                        "value": best["distance_per_game_ft"], "text": text})
    return leaders


def season_summary(season_id):
    """Everything the Season page shows (UNASSIGNED for the games in no season).

    {"season": {"id", "name", "start", "current", "roster"}, "games": [game_row...] oldest first (the videos),
     "games_count", "schedule": schedule_rows() (every fixture and every video, oldest first),
     "record" ({wins, losses, ties, text}: typed scores only, over the schedule),
     "points_per_game", "allowed_per_game" (typed scores, tagged points as a stand-in; None without either),
     "field_goals", "threes", "free_throws" ({made, tried, pct} over our tagged shots),
     "points_by_game": [{"stem", "label": "vs X", "us", "them", "from"}] for the chart,
     "shots": {"count", "marks" (folded, for the season shot map), "court", "shares": area_shares},
     "players": season_players (the Players page), "leaders": leaders_of,
     "provisional": True when any game's court was marked with stand-in clicks}
    """
    seasons = load_seasons()["seasons"]
    if season_id == UNASSIGNED:
        season = {"id": UNASSIGNED, "name": "Unassigned", "start": "", "current": False, "roster": {"name": "", "players": {}},
                  "schedule": []}
        names = roster_for(current_season_id())
    else:
        season = dict(season_or_problem(seasons, season_id), id=season_id)
        names = season["roster"]["players"]
    rows = [game_row(game) for game in games_in(season_id=season_id)]
    schedule = schedule_rows(season_id)          # the fixtures, with the videos' rows where linked
    our_shots, marks = [], []
    for row in rows:
        who = sides(row["stem"])
        if who["our_team"]:
            our_shots += [shot for shot in shots_of(row["stem"]) if shot.get("team") == who["our_team"]]
            marks += marks_of(row["stem"], who["our_team"])
    tiles = score_tiles(our_shots)
    players = season_players(rows, names)
    played = [row for row in schedule if row["points_us"] is not None or row["points_them"] is not None]
    return {"season": season, "games": rows, "games_count": len(rows), "schedule": schedule,
            "record": record_of(schedule),
            "points_per_game": per_game(schedule, "points_us"), "allowed_per_game": per_game(schedule, "points_them"),
            "field_goals": tiles["field_goals"], "threes": tiles["threes"], "free_throws": tiles["free_throws"],
            "points_by_game": [{"stem": row["stem"], "label": f"vs {row['opponent']}" if row["opponent"] else (row["stem"] or row["date"]),
                                "us": row["points_us"], "them": row["points_them"], "from": row["points_from"],
                                "date": row["date"]} for row in played],
            "shots": {"count": len(our_shots), "marks": marks,
                      "court": court_of(rows[0]["stem"]) if rows else {"length_ft": config.COURT_LENGTH_FT, "width_ft": config.COURT_WIDTH_FT},
                      "shares": area_shares(marks)},
            "players": players, "leaders": leaders_of(players),
            "provisional": any(row["stand_in_court"] for row in rows)}


# ---------------------------------------------------------------- one team (an opponent)

def play_shares(pairs, side):
    """How possessions split by play type: [{"label", "count", "share", "points_each", "fg_pct", "turnover_pct"}].

    pairs: (possession with "points" filled in, the tagged shots of ITS game), so a possession's
    shots are looked up in the right game. side is "offense" or "defense"; only the labelled
    possessions count. Rows follow the order of play_types.json; a label not in that list comes last.
    """
    labelled = [(record, all_shots) for record, all_shots in pairs if record.get(side)]
    if not labelled:
        return []
    order = load_play_types()[side]
    by_label = {}
    for record, all_shots in labelled:
        by_label.setdefault(play_label(side, record[side]), []).append((record, all_shots))
    rows = []
    for label in sorted(by_label, key=lambda label: (order.index(label) if label in order else len(order), label)):
        mine = by_label[label]
        field_goals = [shot for record, all_shots in mine for shot in possessions.shots_in(all_shots, record)
                       if shot.get("kind") == "field goal"]
        rows.append({"label": label, "count": len(mine), "share": round(100 * len(mine) / len(labelled)),
                     "points_each": round(sum(record["points"] for record, _ in mine) / len(mine), 2),
                     "fg_pct": round(100 * sum(1 for shot in field_goals if shot.get("made")) / len(field_goals)) if field_goals else None,
                     "turnover_pct": round(100 * sum(1 for record, _ in mine if record.get("turnover")) / len(mine))})
    return rows


def their_players(team_id, rows, notes):
    """The opponent's players by jersey number over the games against them, most points per game first.

    Each: {"number", "games", "points", "points_per_game", "field_goals", "threes" ({made, tried, pct}),
    "scores_from" (the area most of the points came from, or None), "note" (the coach's words)}.
    """
    totals = {}
    for row in rows:
        who = sides(row["stem"])
        if not who["their_team"]:
            continue
        players, _ = player_rows(row["stem"], who["their_team"], {})
        areas = {}
        for mark in marks_of(row["stem"], who["their_team"]):
            if mark["made"] and mark["number"] is not None:
                areas.setdefault(str(mark["number"]), {}).setdefault(mark["area"], 0)
                areas[str(mark["number"])][mark["area"]] += mark["worth"]
        for player in players:
            total = totals.setdefault(player["number"], {"number": player["number"], "games": 0, "points": 0,
                                                         "field_goals": {"made": 0, "tried": 0}, "threes": {"made": 0, "tried": 0}, "areas": {}})
            total["games"] += 1
            total["points"] += player["points"]
            for key in ("field_goals", "threes"):
                total[key]["made"] += player[key]["made"]
                total[key]["tried"] += player[key]["tried"]
            for area, points in areas.get(player["number"], {}).items():
                total["areas"][area] = total["areas"].get(area, 0) + points
    listed = []
    for total in totals.values():
        for key in ("field_goals", "threes"):
            tried = total[key]["tried"]
            total[key]["pct"] = round(100 * total[key]["made"] / tried) if tried else None
        best = max(total["areas"], key=total["areas"].get) if total["areas"] else None
        listed.append(dict(total, points_per_game=round(total["points"] / total["games"], 1) if total["games"] else None,
                           scores_from=best.capitalize() if best else None, note=notes.get(total["number"], ""), areas=None))
    listed.sort(key=lambda total: (-(total["points_per_game"] or 0), int(total["number"])))
    return listed


def team_summary(team_id):
    """Everything the Team page shows, built from every game against that opponent.

    {"team": {"id", "name", "short", "colour", "notes"}, "games": [game_row...] oldest first, "record",
     "points_against_per_game" (their points: typed scores, tagged as a stand-in),
     "possessions_labelled" (theirs with an offense label), "our_possessions_labelled" (ours with a defense label),
     "offense": play_shares of their possessions by offense type (share, points per possession, FG%, turnover %),
     "defense": play_shares of OUR possessions by the defense they played (our points per possession, our turnovers),
     "press": {"pressed", "baskets", "share"} (how often they pressed after a made basket),
     "shots": {"count", "marks", "court"} (their tagged shots, folded), "players": their_players,
     "provisional": True when any game's court was marked with stand-in clicks}
    """
    teams = load_teams()["teams"]
    if team_id not in teams:
        raise Problem("There is no team with that id any more. Reload the page.")
    team = dict(teams[team_id], id=team_id)
    rows = [game_row(game) for game in games_in(team_id=team_id)]
    theirs, ours, their_shots, marks, pressed, baskets = [], [], [], [], 0, 0
    for row in rows:
        who = sides(row["stem"])
        records, all_shots = possessions_of(row["stem"]), shots_of(row["stem"])
        mine = [shot for shot in all_shots if shot.get("team") == who["their_team"]]
        their_shots += mine
        marks += marks_of(row["stem"], who["their_team"])
        theirs += [(record, all_shots) for record in records if record.get("team") == who["their_team"]]
        ours += [(record, all_shots) for record in records if record.get("team") == who["our_team"]]
        if who["our_team"]:
            game_pressed, game_baskets = possessions.pressed_after_baskets(records, mine, who["our_team"])
            pressed, baskets = pressed + game_pressed, baskets + game_baskets
    offense, defense = play_shares(theirs, "offense"), play_shares(ours, "defense")
    return {"team": team, "games": rows, "record": record_of(rows), "games_count": len(rows),
            "points_against_per_game": per_game(rows, "points_them"),
            "possessions_labelled": sum(1 for record, _ in theirs if record.get("offense")),
            "our_possessions_labelled": sum(1 for record, _ in ours if record.get("defense")),
            "offense": offense, "defense": defense,
            "press": {"pressed": pressed, "baskets": baskets, "share": round(100 * pressed / baskets) if baskets else None},
            "shots": {"count": len(their_shots), "marks": marks,
                      "court": court_of(rows[0]["stem"]) if rows else {"length_ft": config.COURT_LENGTH_FT, "width_ft": config.COURT_WIDTH_FT}},
            "players": their_players(team_id, rows, team["notes"]),
            "provisional": any(row["stand_in_court"] for row in rows)}


# ---------------------------------------------------------------- the first run

def migrate(say=print):
    """Make the library files and give every game the v3 keys. Safe to run again: it only adds, never overwrites.

    1. seasons.json: a current season named from today's date, if there is none.
    2. The old root roster.json is copied into the current season's roster, once.
    3. teams.json: made empty if missing. A game whose info names an opponent (v2) but has no
       opponent_id gets a team row for it.
    4. play_types.json: the default lists, if missing.
    5. Every game that has a data folder gets the missing keys of game_info.json with their defaults
       (a game with no season_id shows under "Unassigned"; nothing is assigned by guessing).
    Returns a list of plain sentences saying what was added.
    """
    done = []
    existed = (library_dir() / "seasons.json").exists()
    seasons = load_seasons()
    if not existed:
        current = seasons["seasons"][current_season_id()]
        done.append(f"Made data/_library/seasons.json with the current season {current['name']}.")
    if import_root_roster_once():
        done.append("Copied the roster from roster.json into the current season.")
    if not (library_dir() / "teams.json").exists():
        save_teams({"teams": {}})
        done.append("Made data/_library/teams.json (no opponents yet).")
    if not play_types_file().exists():
        save_play_types(DEFAULT_PLAY_TYPES)
        done.append("Made data/_dashboard/play_types.json with the usual offense and defense types.")
    for path in dash_common.video_files():
        stem = path.stem
        info_path = config.DATA_DIR / stem / "game_info.json"
        if not info_path.parent.is_dir():
            continue                                  # a game nothing has been run on has no folder: nothing to add
        saved = read_file(info_path, {})
        saved = saved if isinstance(saved, dict) else {}
        added = {key: json.loads(json.dumps(value)) for key, value in INFO_DEFAULTS.items() if key not in saved}
        if saved.get("opponent") and not saved.get("opponent_id"):
            added["opponent_id"] = team_or_create(saved["opponent"])
            done.append(f"{stem}: made a team row for its opponent \"{saved['opponent']}\".")
        if added:
            saved.update(added)
            write_file(info_path, saved)
            done.append(f"{stem}: added {', '.join(sorted(added))} to game_info.json.")
    for line in done:
        say(line)
    if not done:
        say("Nothing to add: the library is already in place.")
    return done


# ---------------------------------------------------------------- the command line

def print_library():
    seasons = load_seasons()["seasons"]
    print("Seasons")
    for key, row in sorted(seasons.items(), key=lambda item: item[1]["start"], reverse=True):
        print(f"  {key:<16} {row['name']:<12} {'current' if row['current'] else '':<8} roster: {len(row['roster']['players'])} players")
    teams = load_teams()["teams"]
    print(f"Teams ({len(teams)})")
    for key, row in sorted(teams.items(), key=lambda item: item[1]["name"].lower()):
        print(f"  {key:<16} {row['short']:<4} {row['name']}")
    print("Games")
    for game in list_games():
        info = game["info"]
        season = seasons[info["season_id"]]["name"] if info["season_id"] in seasons else "Unassigned"
        score = f"{info['score_us']}–{info['score_them']}" if info["score_us"] is not None and info["score_them"] is not None else "no score"
        print(f"  {game['name']:<12} {season:<12} {info['date']:<11} vs {info['opponent'] or '?':<20} {score:<10} {game['status']}")


def parse_args():
    parser = argparse.ArgumentParser(description="The library of seasons, teams and games.")
    parser.add_argument("--migrate", action="store_true", help="make the library files and add the v3 keys to each game")
    parser.add_argument("--season", metavar="ID", help="print the season summary as JSON (\"unassigned\" for the games in no season)")
    parser.add_argument("--team", metavar="ID", help="print the team summary as JSON")
    parser.add_argument("--game", metavar="STEM", help="print the game summary as JSON")
    return parser.parse_args()


def run(args):
    if args.migrate:
        migrate()
    elif args.season:
        print(json.dumps(season_summary(args.season), indent=1))
    elif args.team:
        print(json.dumps(team_summary(args.team), indent=1))
    elif args.game:
        print(json.dumps(game_summary(args.game), indent=1))
    else:
        print_library()


if __name__ == "__main__":
    try:
        run(parse_args())
    except Problem as problem:
        raise SystemExit(str(problem))
