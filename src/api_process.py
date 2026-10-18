"""Processing & training's first screens: the Dashboard, the Games library and Process a game
(web/dashboard.html, web/games.html, web/process.html).

    GET /api/process/detector          the sidebar's box: {"name", "label", "version", "score", "score_words"}
    GET /api/process/overview          the Dashboard: "tiles", "chart", "process" (the game to continue), "improve"
                                       (the detector work to continue), "season", "colab_url"
    GET /api/process/games             the Games library: "groups" [{"id", "name", "current", "games": [ROW...]}],
                                       "games_total", "seasons_total", "clips_folder", "colab_url"
    GET /api/process/game/{stem}       Process a game: one game's five steps (STEPS below), "current", "status_line",
                                       "block" ({"text", "ok"}), "game" (ROW), "colab_url"

ROW: library.game_row + api_seasons.with_status (status, court_marked...) + "title" ("vs Riverside · 24 Jan 2026" or
the video's name), "length_words", "size_words", "step" (1..5, the step the game is on, 6 when done), "where"
(the one line the library shows: "Step 3 of 5 · ready to send to Colab"), "done" (bool), "drive" (drive_link.status
without its long message).
STEPS: five rows, each {"number", "title", "explanation", "state": "done" | "live" | "locked" | "waiting" | "busy",
"button" (words, or null), "action" ({"kind": "link" | "post" | "colab", "href"/"path"}), "locked_reason", "done_link"
({"words", "href"}), "note" (an orange line, or null)}.

Every number comes from library.py, api_seasons.with_status, drive_link.status, training_data and
training_versions. Nothing here writes a file.
"""
import datetime
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

import api_seasons
import config
import dash_common
import drive_link
import library
import main as pipeline
import training_data as td
import training_versions as tv

router = APIRouter(prefix="/api/process")

CONFIRMED_TO_BUILD = 120          # the Dashboard's rule for building a version (the Dataset screen enforces its own)
COPY_BYTES_PER_SECOND = 8e6        # a rough speed of the copy into the Drive folder, for "about N minutes"
COLAB_HOURS_PER_VIDEO_MINUTE = 4 / 60   # a 60-minute game is about 4 hours on a T4 (the court stage is the slow one)


# ---------------------------------------------------------------- small words

def fmt_bytes(count):
    count = float(count or 0)
    return f"{count / 1e9:.2f} GB" if count >= 1e9 else f"{count / 1e6:.0f} MB"


def fmt_length(seconds):
    if not seconds:
        return "?"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def fmt_day(text):
    """"2026-10-11" or "2026-10-11 18:32:07" -> "11 Oct"; "" when there is none."""
    text = str(text or "")[:10]
    try:
        return datetime.date.fromisoformat(text).strftime("%d %b").lstrip("0")
    except ValueError:
        return ""


def ago_words(text):
    minutes = drive_link.minutes_since(text) if text else None
    if minutes is None:
        return ""
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{round(minutes)} min ago"
    if minutes < 36 * 60:
        return f"{round(minutes / 60)} h ago"
    return f"{round(minutes / 1440)} days ago"


def game_title(row):
    date = library.game_info(row["stem"])["date"] if row.get("date_is_guess") is None else row["date"]
    when = fmt_day(row.get("date")) + (f" {str(row.get('date'))[:4]}" if row.get("date") else "")
    if row.get("opponent"):
        return f"vs {row['opponent']} · {when}".strip(" ·")
    return row["stem"] + (f" · {when}" if when and not row.get("date_is_guess") else "")


# ---------------------------------------------------------------- the detector in use

def detector_box():
    """What the sidebar box and the first tile say about the detector in use."""
    name = td.detector_name()
    card = next((model for model in dash_common.model_files() if model["name"] == name), None) or {}
    version = card.get("dataset_version")
    score = card.get("map50")             # written by "Score this version" (item 11); None until then
    return {"name": name, "label": dash_common.model_label(name, card), "version": version,
            "built_words": f"built from v{version}" if version else "downloaded, not trained here",
            "score": score, "score_words": f"score {score:.2f}" if isinstance(score, (int, float)) else "not scored yet"}


@router.get("/detector")
def detector():
    try:
        return detector_box()
    except OSError:
        raise HTTPException(503, "The data folders cannot be read.")


# ---------------------------------------------------------------- one game's five steps

def court_words(stem):
    """"Court lines clicked on 3 pictures · 13 Oct" from the court files, or ""."""
    folder = config.DATA_DIR / stem
    setup = dash_common.read_json(folder / "court_setup.json", {})
    views = len(setup.get("views") or []) if isinstance(setup, dict) else 0
    try:
        when = datetime.date.fromtimestamp((folder / "court_calibration.json").stat().st_mtime).isoformat()
    except OSError:
        when = ""
    if not views:
        return ""
    return f"Court lines clicked on {views} picture{'s' if views != 1 else ''}" + (f" · {fmt_day(when)}" if when else "")


def video_facts(row):
    """size, length and the day the video was added, from the file."""
    try:
        stat = config.video_path(row["video"]).stat()
        size, added = stat.st_size, datetime.date.fromtimestamp(stat.st_mtime).isoformat()
    except OSError:
        size, added = 0, ""
    return {"bytes": size, "size_words": fmt_bytes(size), "length_words": fmt_length(row.get("seconds")), "added": added}


def steps_of(row, drive):
    """The five step rows of Process a game for one game (see the module docstring)."""
    stem = row["stem"]
    facts = video_facts(row)
    finished = row["status"] == "finished"
    state = drive.get("state") or ""
    reported = state in ("running", "done", "needs_court", "failed")
    sent = state in ("waiting",) or reported
    copying = state == "copying"
    colab_url = drive_link.PROCESS_NOTEBOOK_URL
    steps = []

    # 1 · the video
    steps.append({"number": 1, "title": "Add the video", "state": "done",
                  "explanation": " · ".join(part for part in [game_title(row), facts["size_words"], facts["length_words"],
                                                               f"added {fmt_day(facts['added'])}" if facts["added"] else ""] if part),
                  "done_link": {"words": "Change video", "href": f"games.html?game={stem}#edit"}, "button": None, "action": None,
                  "locked_reason": None, "note": None})

    # 2 · the court
    court_done = bool(row.get("court_marked"))
    steps.append({"number": 2, "title": "Mark the court",
                  "state": "done" if court_done or finished else "live",
                  "explanation": (court_words(stem) or "Court lines clicked") + ". Tells the app where the floor is in this camera angle."
                  if court_done else "Click the court lines on a few pictures from the video. Tells the app where the floor is in this camera angle. About ten minutes.",
                  "done_link": {"words": "Redo", "href": f"game.html?game={stem}#court"} if court_done else None,
                  "button": None if court_done else "Mark the court",
                  "action": None if court_done else {"kind": "link", "href": f"game.html?game={stem}#court"},
                  "locked_reason": None, "note": None})

    # 3 · send to Colab
    minutes = max(1, round(facts["bytes"] / COPY_BYTES_PER_SECOND / 60)) if facts["bytes"] else 1
    explanation = (f"Copies the video and the court files to Drive › Hoops Tracker › inbox. About {minutes} minute"
                   f"{'s' if minutes != 1 else ''}. Nothing runs yet.")
    step3 = {"number": 3, "title": "Send to Colab", "explanation": explanation, "done_link": None, "button": None,
             "action": None, "locked_reason": None, "note": None, "state": "locked"}
    if finished or (sent and state != "needs_court"):
        step3["state"] = "done"
        step3["explanation"] = f"In Drive since {drive_link.short_time(drive.get('sent')) or 'earlier'}." if drive.get("sent") else explanation
        if state == "failed":
            step3.update(state="live", button="Send to Colab again",
                         action={"kind": "post", "path": f"/api/drive/send/{stem}"},
                         note=f"Colab stopped with an error: {drive.get('message') or ''}".strip())
    elif copying:
        line = (drive.get("progress") or {}).get("line") or ""
        step3.update(state="busy", button=f"Copying to Drive · {line}" if line else "Copying to Drive...")
    elif state == "needs_court":
        step3.update(state="live" if court_done else "locked", button="Send the court files",
                     action={"kind": "post", "path": f"/api/drive/send/{stem}"},
                     explanation="Colab did the long steps and is waiting for the court. Sends only the court files; Colab finishes the positions and stats next time it runs.",
                     locked_reason=None if court_done else "Locked until the court is marked.")
    elif state == "copy failed":
        step3.update(state="live", button="Send to Colab", action={"kind": "post", "path": f"/api/drive/send/{stem}"},
                     note=drive.get("message") or "The copy to Drive failed. Try again.")
    elif not drive.get("connected", True):
        step3.update(state="locked", locked_reason="Locked: Google Drive is not connected.", note=drive.get("message"))
    elif court_done:
        step3.update(state="live", button="Send to Colab", action={"kind": "post", "path": f"/api/drive/send/{stem}"})
    else:
        step3.update(locked_reason="Locked until the court is marked.")
    steps.append(step3)

    # 4 · Colab
    hours = max(1, round((row.get("seconds") or 0) / 60 * COLAB_HOURS_PER_VIDEO_MINUTE))
    step4 = {"number": 4, "title": "Open Colab and press Run all",
             "explanation": f"Opens the notebook in a browser tab. You press Run all; the app cannot do that for you. About {hours} hour{'s' if hours != 1 else ''}.",
             "done_link": None, "button": None, "action": None, "locked_reason": None, "note": None, "state": "locked"}
    if finished or reported:
        step4["state"] = "done"
        if state == "running":
            progress = drive.get("progress") or {}
            words = progress.get("stage_words") or "starting"
            how_far = progress.get("how_far")
            step4["explanation"] = f"Colab is working on it: {words}" + (f" · {round(100 * how_far)}%" if isinstance(how_far, (int, float)) else "") + "."
            if drive.get("stale"):
                step4["note"] = drive.get("message")
        elif state == "needs_court":
            step4["explanation"] = "Colab did the long steps; it finishes the rest when the court files arrive and the notebook runs."
        step4["done_link"] = {"words": "Open Colab", "href": colab_url}
    elif sent:
        step4.update(state="live", button="Open Colab", action={"kind": "colab", "href": colab_url})
        if drive.get("sent"):
            step4["explanation"] += f" In Drive, waiting for you to press Run all · sent {ago_words(drive.get('sent'))}."
    else:
        step4["locked_reason"] = "Locked until step 3 finishes copying."
    steps.append(step4)

    # 5 · results
    step5 = {"number": 5, "title": "Results come back",
             "explanation": "Nothing to press. The app checks Drive every minute and pulls the stats in. Then the game shows up under Game analysis.",
             "done_link": None, "button": None, "action": None, "locked_reason": None, "note": None, "state": "locked"}
    if finished:
        back = drive.get("imported") or drive.get("finished")
        step5.update(state="done", explanation=f"Results back {fmt_day(back) if back else ''}. The stats are under Game analysis.".replace("  ", " "),
                     done_link={"words": "Open stats →", "href": f"game.html?game={stem}"})
    elif state == "done":
        step5.update(state="waiting", explanation="Colab is done. The results are being brought back now (within a minute).")
    elif state == "running":
        step5["state"] = "waiting"
    elif state == "needs_court":
        step5["locked_reason"] = "Locked until Colab finishes (it is waiting for the court files)."
    else:
        step5["locked_reason"] = "Locked until Colab finishes."
    steps.append(step5)
    return steps


def current_step(steps):
    """The step the game is on: the first that is not done (6 when every step is done)."""
    for step in steps:
        if step["state"] != "done":
            return step["number"]
    return 6


def where_words(row, steps, drive):
    """The Games library's "Where it is" line."""
    number = current_step(steps)
    state = drive.get("state") or ""
    if number == 6:
        back = drive.get("imported") or drive.get("finished")
        return "Done" + (f" · results back {fmt_day(back)}" if back else "")
    if number == 2:
        return "Step 2 of 5 · court not marked"
    if number == 3:
        if state == "copying":
            line = (drive.get("progress") or {}).get("line") or ""
            return "Step 3 of 5 · copying to Drive" + (f" · {line}" if line else "")
        if state == "needs_court":
            return "Step 3 of 5 · Colab waits for the court files" + ("" if row.get("court_marked") else " · court not marked")
        if not drive.get("connected", True):
            return "Step 3 of 5 · Google Drive is not connected"
        return "Step 3 of 5 · ready to send to Colab"
    if number == 4:
        return "Step 4 of 5 · in Drive, waiting for you to press Run all" + (f" · sent {ago_words(drive.get('sent'))}" if drive.get("sent") else "")
    if state == "running":
        progress = drive.get("progress") or {}
        how_far = progress.get("how_far")
        words = "Step 5 of 5 · Colab is working" + (f" · {round(100 * how_far)}%" if isinstance(how_far, (int, float)) else "")
        return words + (" · no word for a while" if drive.get("stale") else "")
    if state == "done":
        return "Step 5 of 5 · results coming back"
    return "Step 5 of 5 · waiting for Colab"


def status_line(steps, drive):
    """The sentence at the top of Process a game and the block line under it."""
    number = current_step(steps)
    if number == 6:
        return {"text": "Done. Every step is finished.", "block": {"ok": True, "text": "Nothing is blocking you. Open the stats under Game analysis."}}
    step = steps[number - 1]
    blocked = step["state"] == "locked" or step.get("note")
    if step["state"] == "locked":
        block = {"ok": False, "text": step["locked_reason"] or "Locked."}
    elif step.get("note"):
        block = {"ok": False, "text": step["note"]}
    elif step["state"] == "waiting":
        block = {"ok": True, "text": "Nothing to do here: Colab is working. Keep its tab open."}
    else:
        block = {"ok": True, "text": "Nothing is blocking you."}
    later = [str(n) for n in range(number + 1, 6)]
    unlock = ""
    if later:
        unlock = f" Step{'s' if len(later) > 1 else ''} {' and '.join([', '.join(later[:-1]), later[-1]] if len(later) > 1 else later)} unlock{'' if len(later) > 1 else 's'} as you go."
    return {"text": f"Step {number} of 5." + ("" if blocked else unlock), "block": block}


def drive_for(stem):
    """drive_link.status without the long message (kept) and never raising for a game that is not on Drive."""
    try:
        return drive_link.status(stem)
    except (drive_link.Problem, OSError, ValueError) as why:
        return {"connected": False, "state": "not connected", "message": str(why), "progress": None}


def game_rows():
    """Every game as a ROW (see the docstring), in the order of api_seasons: newest first within each season."""
    groups = api_seasons.all_games()
    rows = []
    for group in groups["seasons"] + [{"id": "unassigned", "name": "Not in a season", "current": False, "games": groups["unassigned"]}]:
        for row in group["games"]:
            drive = drive_for(row["stem"])
            steps = steps_of(row, drive)
            facts = video_facts(row)
            rows.append(dict(row, title=game_title(row), length_words=facts["length_words"], size_words=facts["size_words"],
                             bytes=facts["bytes"], step=current_step(steps), where=where_words(row, steps, drive),
                             done=current_step(steps) == 6, season_name=group["name"], season_current=group["current"],
                             drive={key: drive.get(key) for key in ("state", "sent", "stale", "connected", "imported", "finished")}))
    return groups, rows


@router.get("/games")
def games():
    try:
        groups, rows = game_rows()
    except library.Problem as error:
        raise HTTPException(400, str(error))
    except OSError:
        raise HTTPException(503, "The data folders cannot be read.")
    by_season = {}
    for row in rows:
        by_season.setdefault(row["season_id"], []).append(row)
    listed = [{"id": group["id"], "name": group["name"], "current": group["current"], "games": by_season.get(group["id"], [])}
              for group in groups["seasons"]]
    if by_season.get("unassigned"):
        listed.append({"id": "unassigned", "name": "Not in a season", "current": False, "games": by_season["unassigned"]})
    return {"groups": listed, "games_total": len(rows), "seasons_total": len(groups["seasons"]),
            "clips_folder": groups["clips_folder"], "colab_url": drive_link.PROCESS_NOTEBOOK_URL, "busy": groups["busy"]}


@router.get("/game/{stem}")
def game(stem: str):
    try:
        row = next((r for group in api_seasons.all_games()["seasons"] for r in group["games"] if r["stem"] == stem), None)
        if row is None:
            row = next((r for r in api_seasons.all_games()["unassigned"] if r["stem"] == stem), None)
    except OSError:
        raise HTTPException(503, "The data folders cannot be read.")
    if row is None:
        raise HTTPException(404, "That game is not in the clips folder any more.")
    drive = drive_for(stem)
    steps = steps_of(row, drive)
    facts = video_facts(row)
    line = status_line(steps, drive)
    return {"game": dict(row, title=game_title(row), length_words=facts["length_words"], size_words=facts["size_words"]),
            "steps": steps, "current": current_step(steps), "status_line": line["text"], "block": line["block"],
            "colab_url": drive_link.PROCESS_NOTEBOOK_URL, "where": where_words(row, steps, drive)}


# ---------------------------------------------------------------- the Dashboard

def versions_list():
    found = []
    for info in tv.list_versions():
        found.append({"number": info["number"], "built": info.get("built"), "pictures": info["counts"]["pictures"],
                      "score": info.get("score")})          # "score" {map50, per_class, scored} arrives with item 11
    found.sort(key=lambda version: version["number"])
    return found


def improve_card(counts, games_with_pictures, versions, job):
    """The Dashboard's IMPROVE THE DETECTOR card: where the detector work stands and the one button."""
    captured, confirmed, unchecked = counts["pictures"], counts["checked"], counts["unchecked"]
    next_number = (versions[-1]["number"] + 1) if versions else 1
    running = bool(job.get("running") or job.get("starting"))
    if running:
        status, step_line = "Taking pictures and drawing first boxes", "Step 1 of 6 · the pictures are being prepared."
        button, href = "Continue: Watch the pictures job", "upload.html"
    elif not captured:
        status, step_line = "No pictures yet", "Step 1 of 6 · take pictures from a game to start."
        button, href = "Continue: Take pictures", "upload.html"
    elif unchecked:
        status, step_line = f"Confirming boxes · {confirmed} of {captured}", f"Step 2 of 6 · {unchecked} picture{'s' if unchecked != 1 else ''} still waiting to be confirmed."
        button, href = f"Continue: Confirm boxes ({unchecked} left)", "annotate.html"
    else:
        status, step_line = f"Boxes confirmed · {confirmed} of {captured}", f"Step 3 of 6 · build version v{next_number} from the confirmed pictures."
        button, href = f"Continue: Build version v{next_number}", "dataset.html"
    if confirmed < CONFIRMED_TO_BUILD:
        block = {"ok": False, "text": f"Build v{next_number} unlocks at {CONFIRMED_TO_BUILD} confirmed pictures (you have {confirmed})."}
    else:
        block = {"ok": True, "text": "Nothing is blocking you."}
    return {"status": status, "step_line": step_line, "block": block, "button": button, "href": href,
            "small": "Optional. Do this when the stats show missed balls or players, then re-process the game.",
            "pictures": {"captured": captured, "confirmed": confirmed, "unchecked": unchecked, "games": games_with_pictures}}


def process_card(rows):
    """The Dashboard's PROCESS A GAME card: the game closest to done that is not done yet."""
    todo = [row for row in rows if not row["done"]]
    if not todo:
        return {"title": "Every game is processed", "status": "Nothing waiting.", "block": {"ok": True, "text": "Nothing is blocking you."},
                "button": "Add a game video", "href": "games.html#add", "small": "A new video starts at step 1.", "stem": None}
    # The furthest along first; among equals the newest video.
    todo.sort(key=lambda row: (-row["step"], row.get("date") or "", row["stem"]), reverse=False)
    todo.sort(key=lambda row: -row["step"])
    row = todo[0]
    drive = drive_for(row["stem"])
    steps = steps_of(row, drive)
    line = status_line(steps, drive)
    step = steps[row["step"] - 1] if row["step"] <= 5 else None
    short = {2: "court not marked", 3: "court marked, ready to send to Colab", 4: "in Drive, waiting for Run all", 5: "Colab is working"}
    if step and step["state"] == "busy":
        short[3] = "copying to Drive"
    words = {"Mark the court": "Mark the court", "Send to Colab": f"Send {row['opponent'] or row['stem']} to Colab",
             "Open Colab": "Open Colab and press Run all"}
    button = None
    href = f"process.html?game={row['stem']}"
    if step and step["button"]:
        button = "Continue: " + words.get(step["button"], step["button"])
    elif step and step["state"] == "waiting":
        button = "Continue: See where it is"
    else:
        button = "Continue"
    return {"title": row["title"], "stem": row["stem"], "status": f"Step {row['step']} of 5 · {short.get(row['step'], row['where'])}.",
            "block": line["block"], "button": button, "href": href,
            "small": f"{sum(1 for r in rows if not r['done'])} game{'s' if sum(1 for r in rows if not r['done']) != 1 else ''} not done yet. The Games tab lists them all."}


@router.get("/overview")
def overview():
    try:
        groups, rows = game_rows()
        labels = td.load_labels()
        records = td.records_of(labels)
        counts = td.status_counts(records)
        with_pictures = len(td.games_with_frames())
        versions = versions_list()
        job = dash_common.job_info("pick_frames")
        box = detector_box()
        season = library.load_seasons()["seasons"][library.current_season_id()]["name"]
    except library.Problem as error:
        raise HTTPException(400, str(error))
    except OSError:
        raise HTTPException(503, "The data folders cannot be read.")
    done = sum(1 for row in rows if row["done"])
    waiting_run_all = sum(1 for row in rows if row["step"] == 4)
    not_started = sum(1 for row in rows if row["step"] <= 2)
    on_colab = sum(1 for row in rows if row["step"] == 5)
    latest = versions[-1] if versions else None
    next_number = (latest["number"] + 1) if latest else 1
    games_line = " · ".join(part for part in [
        f"{waiting_run_all} waiting for Run all" if waiting_run_all else "", f"{on_colab} on Colab" if on_colab else "",
        f"{not_started} not started" if not_started else ""] if part) or "nothing waiting"
    tiles = {
        "detector": {"value": box["label"], "line": f"{box['built_words']} · {box['score_words']}", "name": box["name"]},
        "games": {"value": f"{done} of {len(rows)}", "line": games_line},
        "pictures": {"value": f"{counts['pictures']} captured", "line": f"{counts['checked']} confirmed · from {with_pictures} game{'s' if with_pictures != 1 else ''}"},
        "versions": {"value": str(len(versions)), "line": (f"latest v{latest['number']} · {fmt_day(latest['built'])} · " if latest else "") + f"v{next_number} not started"},
    }
    chart = {"versions": [{"number": v["number"], "label": f"v{v['number']}", "built": fmt_day(v["built"]), "pictures": v["pictures"],
                           "score": (v["score"] or {}).get("map50") if isinstance(v.get("score"), dict) else None} for v in versions]
             + [{"number": next_number, "label": f"v{next_number}", "built": "", "pictures": None, "score": None, "not_built": True}],
             "in_use": box, "per_class": ((next((v for v in versions if v["number"] == box["version"]), {}) or {}).get("score") or {}).get("per_class"),
             "classes": tv.load_project()["classes"]}
    return {"season": season, "tiles": tiles, "chart": chart, "process": process_card(rows),
            "improve": improve_card(counts, with_pictures, versions, job), "colab_url": drive_link.PROCESS_NOTEBOOK_URL,
            "footer": "Colab only runs when you press Run all in its tab. The app prepares the files and opens the notebook; it cannot press the button for you."}
