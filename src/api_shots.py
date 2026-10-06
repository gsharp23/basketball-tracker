"""The Tag shots screen's own API (web/shots.html + shots.js). The work is done by shots.py.

    GET  /api/shots/setup?video=      what the screen needs once: the video to watch, the teams, the court
    GET  /api/shots/boxes?video=&t=   the player boxes at one moment of the watched video, with where each
                                      player stands and what a shot from there would be worth
    GET  /api/shots/list?video=       every tagged shot, in game order, with its words for the list
    POST /api/shots/add               body {video, index, team, number, tag, made, kind, worth, period} -> the shot
    PUT  /api/shots/{id}              body {video, changes: {made, kind, worth, period}} -> the shot
    DELETE /api/shots/{id}?video=
    POST /api/shots/undo              body {video}: take back the last change
    POST /api/shots/heat_maps         body {video}: draw the scoring heat maps -> their /files URLs

A moment is given as a time t (seconds) of the video being watched; the dashboard turns it
into a "look" (one of the moments the tool looked at, about 10 a second) and back. The
browser draws the boxes itself, so a step is as quick as one small JSON answer.
"""
from fastapi import APIRouter, HTTPException

import config
import dash_common
import shots

router = APIRouter(prefix="/api/shots")


def problem_to_http(action, *args, **kwargs):
    """Run a shots.py function; its Problem (plain words for the coach) becomes a 400 answer."""
    try:
        return action(*args, **kwargs)
    except shots.Problem as problem:
        raise HTTPException(400, str(problem))


def need_video(video):
    video = str(video or "")
    if not dash_common.is_clip_video(video):
        raise HTTPException(400, "Choose a game first, on the Games screen.")
    return video


def teams_reply(video):
    """The teams as the screen shows them: label, colour, roster name, and which one is ours."""
    teams = shots.load_teams(video)
    our_name = dash_common.load_roster()["home"]["name"]
    reply = []
    for index, label in enumerate(teams["labels"]):
        roster_name = teams["roster_names"].get(label)
        reply.append({"label": label, "colour": dash_common.hex_colour(teams["colours"][index]),
                      "name": roster_name, "ours": bool(roster_name) and roster_name == our_name})
    return reply


@router.get("/setup")
def setup(video: str):
    video = need_video(video)
    stem = config.video_stem(video)
    moments = problem_to_http(shots.load_moments, video)
    teams = problem_to_http(teams_reply, video)
    watched, kind = shots.watch_video(video)
    folder = "clips" if kind == "game" else "outputs"
    path = watched.name if kind == "game" else f"{stem}/{watched.name}"
    length, width = shots.court_size(video)
    return {"video": video, "name": stem, "teams": teams, "info": shots.game_info(video),
            "watch": {"url": f"/files/{folder}/{path}", "kind": kind},
            # the boxes are measured in the game video's pictures; the tracked video may be smaller
            "picture": {"width": moments["width"], "height": moments["height"]},
            "court": {"length_ft": length, "width_ft": width},
            "positions": moments["positions"], "stand_in_court": dash_common.stand_in_court(video),
            "periods": shots.PERIODS}


@router.get("/boxes")
def boxes(video: str, t: float, watching: str = "game"):
    """Who is boxed at this moment, and what a shot by each of them from there would be."""
    video = need_video(video)
    moments = problem_to_http(shots.load_moments, video)
    teams = problem_to_http(shots.load_teams, video)
    court = shots.court_size(video)
    index = shots.look_at(moments, max(0.0, t), watching)
    players = []
    for player in shots.players_at(video, index):
        found = shots.find_position(moments, teams, index, player["tag"], player["team"], player["number"])
        where = {"x_ft": found[0], "y_ft": found[1]} if found else {"x_ft": None, "y_ft": None}
        view = shots.judge(where, court)
        players.append({"tag": player["tag"], "team": player["team"], "number": player["number"],
                        "name": teams["names"].get((player["team"], player["number"])),
                        "box": [round(value, 1) for value in player["box"]],
                        "where": shots.where_text(where, court) if view else None,
                        "worth": view["worth"] if view else None,
                        "near_line": bool(view) and abs(view["outside_three_ft"]) <= shots.NEAR_LINE_FT})
    return {"index": index, "frame": moments["frames"][index], "time": shots.look_time(moments, index, watching),
            "players": players}


def shot_reply(shot, teams, court, moments, watching):
    """One shot as the list shows it."""
    return dict(shot, line=shots.list_line(shot, court), where=shots.where_text(shot, court),
                name=teams["names"].get((shot["team"], shot["number"])),
                clock=shots.show_time(shot["time_s"]),
                # the time in the video being watched, so a row can take the coach back to the moment
                watch_time=shots.look_time(moments, shots.look_at(moments, shot["time_s"]), watching))


def list_reply(video, watching):
    data = shots.load_shots(video)
    teams, court, moments = shots.load_teams(video), shots.court_size(video), shots.load_moments(video)
    return {"shots": [shot_reply(shot, teams, court, moments, watching) for shot in data["shots"]],
            "points": {team: sum(shot["worth"] for shot in data["shots"] if shot["made"] and shot["team"] == team)
                       for team in teams["labels"]},
            "can_undo": (shots.game_folder(video) / shots.UNDO_FILE).exists()}


@router.get("/list")
def list_shots(video: str, watching: str = "game"):
    return problem_to_http(list_reply, need_video(video), watching)


@router.post("/add")
def add(body: dict):
    video = need_video(body.get("video"))
    try:
        index = int(body.get("index"))
        worth = body.get("worth")
        worth = int(worth) if worth in (2, 3, "2", "3") else None
    except (TypeError, ValueError):
        raise HTTPException(400, "Pause the video at the shot first.")
    shot = problem_to_http(shots.add_shot, video, index, body.get("team"), body.get("number"), body.get("made"),
                           body.get("kind") or "field goal", worth, str(body.get("period") or "1"), body.get("tag"))
    teams, court, moments = shots.load_teams(video), shots.court_size(video), shots.load_moments(video)
    return shot_reply(shot, teams, court, moments, body.get("watching") or "game")


@router.put("/{shot_id}")
def edit(shot_id: int, body: dict):
    video = need_video(body.get("video"))
    changes = body.get("changes") or {}
    allowed = {key: changes[key] for key in ("made", "kind", "period", "worth") if key in changes}
    if "worth" in allowed and allowed["worth"] not in (2, 3, None):
        raise HTTPException(400, "Points can only be 2 or 3.")
    shot = problem_to_http(shots.edit_shot, video, shot_id, allowed)
    teams, court, moments = shots.load_teams(video), shots.court_size(video), shots.load_moments(video)
    return shot_reply(shot, teams, court, moments, body.get("watching") or "game")


@router.delete("/{shot_id}")
def delete(shot_id: int, video: str):
    problem_to_http(shots.delete_shot, need_video(video), shot_id)
    return {"deleted": shot_id}


@router.post("/undo")
def undo(body: dict):
    problem_to_http(shots.undo, need_video(body.get("video")))
    return {"undone": True}


@router.post("/heat_maps")
def heat_maps(body: dict):
    """Draw both teams' scoring heat maps now (a second or two), so the Game screen shows them up to date."""
    video = need_video(body.get("video"))
    paths = problem_to_http(shots.save_heat_maps, video)
    stem = config.video_stem(video)
    return {"pictures": [f"/files/outputs/{stem}/{path.name}" for path in paths]}
