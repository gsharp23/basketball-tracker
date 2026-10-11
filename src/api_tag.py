"""The "Tag shots and plays" screen's own API (web/tag.html + tag.js): shots and possessions on one page.

Every route takes the game as the stem in the path (/api/tag/test/...). The work is done by
shots.py (the shots: where each was taken from, 2 or 3, undo) and possessions.py (the possessions
and what they came to); this file turns the coach's clicks into calls and problems into plain words.

    GET  /api/tag/{stem}/setup                what the page needs once: the video to watch, the two teams and
                                              which is us, the court, the periods, the coach's play names
    GET  /api/tag/{stem}/boxes?t=&watching=   the player boxes at one moment of the watched video, with where each
                                              player stands and what a shot from there would be worth
    GET  /api/tag/{stem}/shots?watching=      every tagged shot in game order with its words, the points so far, can_undo
    POST /api/tag/{stem}/shots                body {index, team, number, tag, made, kind, worth, period, watching} -> the shot
    PUT  /api/tag/{stem}/shots/{id}           body {changes: {made, kind, worth, period}, watching} -> the shot
    DELETE /api/tag/{stem}/shots/{id}
    POST /api/tag/{stem}/shots/undo           take back the last change to the shots (undo twice puts it back)
    GET  /api/tag/{stem}/possessions          {"possessions" (with points, labels, outcome, shots inside), "count", "labelled"}
    POST /api/tag/{stem}/possessions          body {start, end, team, offense, defense, outcome, points, note} -> the record
    PUT  /api/tag/{stem}/possessions/{id}     body: any of those keys -> the record
    DELETE /api/tag/{stem}/possessions/{id}
    The play names themselves are GET/PUT /api/play-types (dash_api.py).

A moment is a time t (seconds) of the video being watched; shots.py turns it into a "look" (one
of the moments the tool looked at, about 10 a second) and back. The browser draws the boxes
itself from one small JSON answer, so a step is quick: no picture is made on the server.
Player names come from the season's roster, for our team only; the opponent's players are numbers.
"""
from fastapi import APIRouter, HTTPException

import dash_common
import library
import possessions
import shots

router = APIRouter(prefix="/api/tag")


def need_video(stem):
    """"test" -> "clips/test.mp4", or a plain 400 when the game is not in the clips folder."""
    video = library.video_of(stem)
    if not video:
        raise HTTPException(400, "That game is not in the clips folder any more. Choose one on the Games screen.")
    return video


def plainly(action):
    """Run a shots.py / possessions.py / library call; a Problem becomes a plain 400 (409 when the game is not processed yet)."""
    try:
        return action()
    except shots.Problem as problem:                  # possessions.py raises the same class
        raise HTTPException(409 if str(problem) == shots.NOT_READY else 400, str(problem))
    except library.Problem as problem:
        raise HTTPException(400, str(problem))


def names_of(stem, who):
    """{(label, number): name} for OUR players, from the season's roster. The opponent's players stay numbers only."""
    if not who["our_team"]:
        return {}
    info = library.game_info(stem)
    roster = library.roster_for(info["season_id"] or library.current_season_id())
    return {(who["our_team"], number): name for number, name in roster.items() if name}


def teams_reply(stem, who):
    """The two colour groups as the page shows them: label, jersey colour, whether it is us, and a short name."""
    opponent = library.game_info(stem)["opponent"]
    reply = []
    for label, colour in zip(who["labels"], who["colours"]):
        ours = label == who["our_team"]
        reply.append({"label": label, "colour": colour, "ours": ours, "name": "Us" if ours else (opponent or "Them")})
    return reply


def watch_reply(stem, video):
    """The video the coach watches: the game video when the browser can play it, else the tracked one."""
    watched, kind = shots.watch_video(video)
    folder = "clips" if kind == "game" else "outputs"
    path = watched.name if kind == "game" else f"{stem}/{watched.name}"
    return {"url": f"/files/{folder}/{path}", "kind": kind}


@router.get("/{stem}/setup")
def setup(stem: str):
    video = need_video(stem)
    moments = plainly(lambda: shots.load_moments(video))
    who = library.sides(stem)
    return {"video": video, "name": stem, "info": library.game_info(stem), "teams": teams_reply(stem, who),
            "our_team": who["our_team"], "their_team": who["their_team"], "guessed": who["guessed"],
            "watch": watch_reply(stem, video),
            # the boxes are measured in the game video's pictures; the tracked video may be smaller
            "picture": {"width": moments["width"], "height": moments["height"]},
            "court": library.court_of(stem), "positions": moments["positions"],
            "stand_in_court": dash_common.stand_in_court(video), "periods": shots.PERIODS,
            "play_types": library.load_play_types(), "outcomes": possessions.OUTCOMES}


# ---------------------------------------------------------------- shots

@router.get("/{stem}/boxes")
def boxes(stem: str, t: float = 0.0, watching: str = "game"):
    """Who is boxed at this moment, and what a shot by each of them from there would be."""
    video = need_video(stem)
    moments = plainly(lambda: shots.load_moments(video))
    teams = plainly(lambda: shots.load_teams(video))
    names = names_of(stem, library.sides(stem))
    court = shots.court_size(video)
    index = shots.look_at(moments, max(0.0, t), watching)
    players = []
    for player in shots.players_at(video, index):
        found = shots.find_position(moments, teams, index, player["tag"], player["team"], player["number"])
        where = {"x_ft": found[0], "y_ft": found[1]} if found else {"x_ft": None, "y_ft": None}
        view = shots.judge(where, court)
        players.append({"tag": player["tag"], "team": player["team"], "number": player["number"],
                        "name": names.get((player["team"], player["number"])),
                        "box": [round(value, 1) for value in player["box"]],
                        "where": shots.where_text(where, court) if view else None,
                        "worth": view["worth"] if view else None,
                        "near_line": bool(view) and abs(view["outside_three_ft"]) <= shots.NEAR_LINE_FT})
    return {"index": index, "frame": moments["frames"][index], "time": shots.look_time(moments, index, watching),
            "players": players}


def shot_reply(shot, names, court, moments, watching):
    """One shot as the list shows it, with the time in the video being watched so a row can go back to the moment."""
    return dict(shot, line=shots.list_line(shot, court), where=shots.where_text(shot, court),
                name=names.get((shot["team"], shot["number"])),
                watch_time=shots.look_time(moments, shots.look_at(moments, shot["time_s"]), watching))


def one_shot(stem, video, shot, watching):
    names = names_of(stem, library.sides(stem))
    return shot_reply(shot, names, shots.court_size(video), shots.load_moments(video), watching)


def shots_reply(stem, video, watching):
    data = shots.load_shots(video)
    names = names_of(stem, library.sides(stem))
    court, moments = shots.court_size(video), shots.load_moments(video)
    return {"shots": [shot_reply(shot, names, court, moments, watching) for shot in data["shots"]],
            "points": {label: sum(shot["worth"] for shot in data["shots"] if shot["made"] and shot["team"] == label)
                       for label in library.team_labels(stem)},
            "can_undo": (shots.game_folder(video) / shots.UNDO_FILE).exists()}


@router.get("/{stem}/shots")
def list_shots(stem: str, watching: str = "game"):
    video = need_video(stem)
    return plainly(lambda: shots_reply(stem, video, watching))


def clean_tag(tag):
    """The player tag picked in the picture as a number, or None when the shooter was typed."""
    if tag in (None, ""):
        return None
    try:
        return int(tag)
    except (TypeError, ValueError):
        raise HTTPException(400, "Click the shooter in the picture again.")


@router.post("/{stem}/shots")
def add_shot(stem: str, body: dict):
    video = need_video(stem)
    moments = plainly(lambda: shots.load_moments(video))
    try:
        index = int(body.get("index"))
        if not 0 <= index < len(moments["frames"]):
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(400, "Pause the video at the shot first.")
    worth = body.get("worth")
    worth = int(worth) if worth in (2, 3, "2", "3") else None
    tag = clean_tag(body.get("tag"))
    shot = plainly(lambda: shots.add_shot(video, index, body.get("team"), body.get("number"), body.get("made"),
                                          body.get("kind") or "field goal", worth, str(body.get("period") or "1"), tag))
    return one_shot(stem, video, shot, body.get("watching") or "game")


@router.put("/{stem}/shots/{shot_id}")
def edit_shot(stem: str, shot_id: int, body: dict):
    video = need_video(stem)
    changes = body.get("changes") if isinstance(body.get("changes"), dict) else {}
    allowed = {key: changes[key] for key in ("made", "kind", "period", "worth") if key in changes}
    if "worth" in allowed and allowed["worth"] not in (2, 3, None):
        raise HTTPException(400, "Points can only be 2 or 3.")
    shot = plainly(lambda: shots.edit_shot(video, shot_id, allowed))
    return one_shot(stem, video, shot, body.get("watching") or "game")


@router.delete("/{stem}/shots/{shot_id}")
def delete_shot(stem: str, shot_id: int):
    video = need_video(stem)
    plainly(lambda: shots.delete_shot(video, shot_id))
    return {"deleted": shot_id}


@router.post("/{stem}/shots/undo")
def undo_shots(stem: str):
    video = need_video(stem)
    plainly(lambda: shots.undo(video))
    return {"undone": True}


# ---------------------------------------------------------------- possessions

def possessions_reply(video):
    """Every possession with its points and labels, plus how many of the team's tagged shots fall inside it."""
    all_shots = shots.load_shots(video)["shots"]
    listed = []
    for record in possessions.with_points(video):
        inside = possessions.shots_in(all_shots, record)
        listed.append(dict(record, shots_inside=len(inside),
                           points_inside=sum(shot["worth"] for shot in inside if shot["made"])))
    labelled = sum(1 for record in listed if record["offense"] and record["defense"])
    return {"possessions": listed, "count": len(listed), "labelled": labelled}


def possession_body(body):
    """The fields of a possession as the page sends them (only the keys that were sent)."""
    fields = {}
    for key in ("start", "end", "team", "offense", "defense", "outcome", "points", "note"):
        if key in body:
            fields[key] = body[key]
    return fields


@router.get("/{stem}/possessions")
def list_possessions(stem: str):
    video = need_video(stem)
    return plainly(lambda: possessions_reply(video))


@router.post("/{stem}/possessions")
def add_possession(stem: str, body: dict):
    video = need_video(stem)
    fields = possession_body(body)
    if not str(fields.get("end") or "").strip():
        raise HTTPException(400, "Say when the possession ended: press End while the video is there, or type the time.")
    return plainly(lambda: possessions.add_possession(
        video, fields.get("start"), fields.get("end"), str(fields.get("team") or ""), fields.get("offense"),
        fields.get("defense"), fields.get("points"), fields.get("note"), fields.get("outcome")))


@router.put("/{stem}/possessions/{possession_id}")
def edit_possession(stem: str, possession_id: int, body: dict):
    video = need_video(stem)
    return plainly(lambda: possessions.edit_possession(video, possession_id, possession_body(body)))


@router.delete("/{stem}/possessions/{possession_id}")
def delete_possession(stem: str, possession_id: int):
    video = need_video(stem)
    plainly(lambda: possessions.delete_possession(video, possession_id))
    return {"deleted": possession_id}
