"""The Opponent page's own API: the opponent report and the possessions the coach labels by hand.

Routes (all under /api/opponent; every one takes ?video=clips/test.mp4)
    GET    /report                     everything the report shows: possessions.report()
    GET    /possessions                {"possessions", "labels", "teams"}: the list with points filled in,
                                       the label choices (OFFENSE_TYPES, DEFENSE_TYPES) and who is who
    POST   /possessions                body {"start", "end", "team", "offense", "defense", "points", "note"} -> the record
    PUT    /possessions/{id}           body: any of those keys -> the record
    DELETE /possessions/{id}
    PUT    /our_team                   body {"our_team": "white"}: which team label is us (game_info.json)
    GET    /video                      {"url", "watching"}: the video to watch while labelling (game or tracked)

The real work is in possessions.py (and shots.py for the shots); this file only turns
the coach's clicks into calls and problems into plain messages.
"""
from fastapi import APIRouter, HTTPException

import config
import dash_common
import possessions
import shots

router = APIRouter(prefix="/api/opponent")


def checked(video):
    """The video, or a plain 400 when it is not a game in the clips folder."""
    if not dash_common.is_clip_video(video):
        raise HTTPException(400, "Choose a game first: that video is not in the clips folder.")
    return video


def plainly(action):
    """Run a possessions.py call; a Problem the coach can put right becomes a plain 400 (409 when not processed yet)."""
    try:
        return action()
    except shots.Problem as problem:
        raise HTTPException(409 if str(problem) == shots.NOT_READY else 400, str(problem))


def labels():
    return {"offense": possessions.OFFENSE_TYPES, "defense": possessions.DEFENSE_TYPES, "press": possessions.PRESS_KEY}


@router.get("/report")
def report(video: str = ""):
    video = checked(video)
    return plainly(lambda: dict(possessions.report(video), labels=labels()))


@router.get("/possessions")
def list_possessions(video: str = ""):
    video = checked(video)
    return plainly(lambda: {"possessions": possessions.with_points(video), "labels": labels(),
                            "teams": possessions.sides(video)})


@router.post("/possessions")
def add_possession(body: dict, video: str = ""):
    video = checked(video)
    return plainly(lambda: possessions.add_possession(
        video, body.get("start"), body.get("end"), str(body.get("team") or ""), body.get("offense"),
        body.get("defense"), body.get("points"), body.get("note")))


@router.put("/possessions/{possession_id}")
def edit_possession(possession_id: int, body: dict, video: str = ""):
    video = checked(video)
    return plainly(lambda: possessions.edit_possession(video, possession_id, body))


@router.delete("/possessions/{possession_id}")
def delete_possession(possession_id: int, video: str = ""):
    video = checked(video)
    plainly(lambda: possessions.delete_possession(video, possession_id))
    return {"deleted": possession_id}


@router.put("/our_team")
def our_team(body: dict, video: str = ""):
    video = checked(video)

    def choose():
        labels_of_game = shots.load_teams(video)["labels"]
        chosen = body.get("our_team")
        if chosen not in labels_of_game:
            raise shots.Problem("Pick one of the two teams of this game: " + " or ".join(labels_of_game) + ".")
        possessions.save_info(video, {"our_team": chosen})
        return possessions.sides(video)
    return plainly(choose)


@router.get("/video")
def video_to_watch(video: str = ""):
    video = checked(video)
    path, watching = shots.watch_video(video)
    folder = "clips" if watching == "game" else "outputs"
    inside = path.name if watching == "game" else f"{config.video_stem(video)}/{path.name}"
    return {"url": f"/files/{folder}/{inside}", "watching": watching}
