"""The Seasons library, one Season and the Players page (web/seasons.html, season.html, players.html).

    GET /api/seasons/library              the Seasons screen: every season with its record, games, points per game,
                                          the trend line and its last game; the recent games table; the Unassigned count
    GET /api/seasons/games                the Games screen: every game grouped by season (current season first, then
                                          newest), newest game first, the Unassigned games last, each row as below
    GET /api/seasons/{id}/summary         the Season screen: library.season_summary() plus what each game row needs
                                          (status words, the action to offer, progress while running, time estimate),
                                          the same on the "schedule" rows (a fixture without a video: "No video"),
                                          and the clips folder as Windows names it ("unassigned" is allowed)
    GET /api/seasons/{id}/game/{stem}     one game row again, polled while a run is going
    GET /api/seasons/{id}/players         the Players screen: season totals per player (roster players included),
                                          every player's game lines and the season's shot marks

Writing goes through the shared routes of dash_api.py: PUT /api/games/{stem}/info (assign a game, the
score), PUT /api/seasons/{id} (the roster, current), POST /api/seasons (a new season), POST /api/jobs/start
{"kind": "run"} (process a game here). Colab goes through the Drive module (POST /api/drive/send/{stem},
GET /api/drive/status/{stem}); the pages only show what it answers.
"""
from fastapi import APIRouter, HTTPException

import config
import court_mapper
import dash_common
import drive_link
import library
import main

router = APIRouter(prefix="/api/seasons")

# From the README's measured estimate: a 52-minute game is a day or two on this laptop
# (detection 8 to 14 hours, jersey reading 10 or more, court 3, court view 2 to 3).
LAPTOP_HOURS_PER_VIDEO_MINUTE = 0.5

# Plain words for each stage of main.py, for the status of a running game.
STEP_WORDS = {"detect": "finding the players", "court": "mapping the court", "teams": "sorting the players into teams",
              "jerseys": "reading the jersey numbers", "tactical": "drawing the court view", "stats": "working out the stats"}

RECENT_GAMES = 8


def problem(error):
    return HTTPException(400, str(error))


def laptop_estimate(seconds):
    """How long processing takes on this laptop, in words."""
    if seconds is None:
        return "many hours"
    if seconds < 300:
        return "a few minutes"
    hours = seconds / 60 * LAPTOP_HOURS_PER_VIDEO_MINUTE
    if hours < 10:
        return f"about {hours:.0f} hours"
    return f"about {hours / 24:.0f} day{'s' if hours >= 36 else ''} ({hours:.0f} hours)"


def running_words(progress):
    """"step 2 of 6, reading the jersey numbers · 57% · about 5 min left" from a job's progress."""
    words = []
    if progress.get("step"):
        words.append(f"step {progress['step_number']} of {progress['step_count']}, {STEP_WORDS.get(progress['step'], progress['step'])}")
    if progress.get("how_far") is not None:
        words.append(f"{round(100 * progress['how_far'])}%")
    if progress.get("left"):
        words.append(progress["left"])
    return " · ".join(words)


def with_status(row):
    """A library.game_row with the words, the chip colour and the action the tables show for its state.

    "status_words" is short ("Running here · 57%"), "progress_words" the whole line (step, how far, time left);
    "action": "open" (finished), "progress" (running), "mark court", "carry on" (stopped part-way, or "Roster changed"
    when only the stats are older than the roster), "run" (not started); "estimate": how long a run takes on this laptop;
    "court_marked": whether the court set-up is done (the pages ask for it before a game is sent to Colab).
    """
    status, progress = row["status"], None
    if status in ("running", "stopped part-way"):
        progress = dash_common.job_info(f"run_{row['stem']}", last=1)["progress"]
    if status == "finished":
        row.update(status_words="Finished", chip="green", action="open")
    elif status == "running":
        how_far = progress.get("how_far")
        row.update(status_words="Running here" + (f" · {round(100 * how_far)}%" if how_far is not None else ""),
                   chip="blue", action="progress")
    elif status == "needs the court marked":
        row.update(status_words="Needs the court marked", chip="red", action="mark court")
    elif status == "stopped part-way":
        row.update(status_words="Stopped part-way", chip="yellow", action="carry on")
    else:
        row.update(status_words="Not started", chip="grey", action="run")
    row["progress"] = progress
    row["progress_words"] = running_words(progress) if progress else ""
    row["estimate"] = laptop_estimate(row["seconds"])
    row["court_marked"] = not court_mapper.setup_missing(row["video"])        # the court is marked before the send
    if row["roster_changed"]:
        # Not really stopped: every step is done, only the stats are older than the roster.
        row.update(status_words="Roster changed", estimate="seconds",
                   progress_words="Only the stats are older than the roster: Carry on redoes them with the new names, in seconds.")
    return row


def game_rows(season_id):
    return [with_status(library.game_row(game)) for game in library.games_in(season_id=season_id)]


# ---------------------------------------------------------------- the Seasons screen

def season_card(season_id, summary):
    """What one season card shows: the record, games, points per game, the trend, the last game."""
    rows = summary["games"]
    played = [row for row in rows if row["points_us"] is not None]
    return {"id": season_id, "name": summary["season"]["name"], "start": summary["season"]["start"],
            "current": summary["season"]["current"], "roster_players": len(summary["season"]["roster"]["players"]),
            "games": summary["games_count"], "record": summary["record"]["text"],
            "points_per_game": summary["points_per_game"],
            "trend": [row["points_us"] for row in played],                 # oldest first, as the chart draws it
            "last_game": rows[-1] if rows else None}


@router.get("/library")
def seasons_library():
    """Every season as a card (current first, then newest first), the recent games, the Unassigned games."""
    try:
        seasons = library.load_seasons()["seasons"]
        cards, recent = [], []
        for season_id in seasons:
            summary = library.season_summary(season_id)
            cards.append(season_card(season_id, summary))
            recent += [dict(with_status(row), season_name=summary["season"]["name"]) for row in summary["games"]]
        unassigned = game_rows(library.UNASSIGNED)
        recent += [dict(row, season_name="") for row in unassigned]
    except library.Problem as error:
        raise problem(error)
    cards.sort(key=lambda card: card["start"], reverse=True)      # newest first...
    cards.sort(key=lambda card: not card["current"])              # ...and the current season before all
    recent.sort(key=lambda row: (row["date"], row["stem"]), reverse=True)
    return {"seasons": cards, "current": library.current_season_id(), "recent": recent[:RECENT_GAMES],
            "unassigned": len(unassigned), "games_total": len(recent)}


# ---------------------------------------------------------------- the Games screen

@router.get("/games")
def all_games():
    """Every game, grouped by season, for the Games screen; each row has the status words and the action."""
    seasons = library.load_seasons()["seasons"]
    groups = []
    for season_id, season in seasons.items():
        rows = game_rows(season_id)
        rows.reverse()                                                 # newest game first
        groups.append({"id": season_id, "name": season["name"], "start": season["start"],
                       "current": season["current"], "games": rows})
    groups.sort(key=lambda group: group["start"], reverse=True)       # newest season first...
    groups.sort(key=lambda group: not group["current"])               # ...and the current season before all
    unassigned = game_rows(library.UNASSIGNED)
    unassigned.reverse()
    return {"seasons": groups, "unassigned": unassigned, "clips_folder": main.windows_path(config.CLIPS_DIR),
            "busy": dash_common.what_is_running(), "run_where": dash_common.load_settings()["run_where"],
            "colab_url": drive_link.PROCESS_NOTEBOOK_URL,
            "games_total": sum(len(group["games"]) for group in groups) + len(unassigned)}


# ---------------------------------------------------------------- one season

def summary_or_400(season_id):
    try:
        return library.season_summary(season_id)
    except library.Problem as error:
        raise problem(error)


@router.get("/{season_id}/summary")
def season_summary(season_id: str):
    summary = summary_or_400(season_id)
    summary["games"] = [with_status(row) for row in summary["games"]]
    # The schedule's rows with a video get the same status words; a fixture without one says so itself.
    summary["schedule"] = [with_status(row) if row.get("stem") else dict(row, status_words="No video", chip="grey", action=None,
                                                                          progress_words="", estimate="")
                           for row in summary["schedule"]]
    summary["clips_folder"] = main.windows_path(config.CLIPS_DIR)
    summary["busy"] = dash_common.what_is_running()
    summary["run_where"] = dash_common.load_settings()["run_where"]
    return summary


@router.get("/{season_id}/game/{stem}")
def season_game(season_id: str, stem: str):
    """One game row of the season, for the table to refresh while the game is processed."""
    for game in library.games_in(season_id=season_id):
        if game["name"] == stem:
            return with_status(library.game_row(game))
    raise HTTPException(400, "That game is not in this season any more. Reload the page.")


# ---------------------------------------------------------------- the Players screen

def game_lines(rows, names):
    """{jersey number: [one line per game the player was seen or shot in]}, oldest game first."""
    lines = {}
    for row in rows:
        who = library.sides(row["stem"])
        if not who["our_team"]:
            continue
        players, _ = library.player_rows(row["stem"], who["our_team"], names)
        for player in players:
            lines.setdefault(player["number"], []).append({
                "stem": row["stem"], "date": row["date"], "opponent": row["opponent"], "result": row["result"],
                "score_us": row["score_us"], "score_them": row["score_them"], "points": player["points"],
                "field_goals": player["field_goals"], "threes": player["threes"], "free_throws": player["free_throws"],
                "seen_s": player["seen_s"], "distance_ft": player["distance_ft"]})
    return lines


@router.get("/{season_id}/players")
def season_players(season_id: str):
    summary = summary_or_400(season_id)
    names = summary["season"]["roster"]["players"]
    players = list(summary["players"])
    seen = {player["number"] for player in players}
    # A roster player who has not shown up in any game yet is listed too, so the whole roster is on the page.
    for number, name in names.items():
        if number not in seen:
            players.append({"number": number, "name": name, "games": 0, "points": 0, "points_per_game": None,
                            "field_goals": {"made": 0, "tried": 0, "pct": None}, "threes": {"made": 0, "tried": 0, "pct": None},
                            "free_throws": {"made": 0, "tried": 0, "pct": None}, "seen_s": 0, "distance_ft": 0,
                            "distance_per_game_ft": None})
    return {"season": summary["season"], "players": players, "lines": game_lines(summary["games"], names),
            "marks": summary["shots"]["marks"], "court": summary["shots"]["court"],
            "games_count": summary["games_count"], "provisional": summary["provisional"]}
