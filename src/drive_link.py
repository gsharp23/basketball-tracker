"""The link between this laptop and Google Colab: a Google Drive folder that Google Drive for desktop keeps on this computer.

    python src/drive_link.py                    is Drive connected, what is in the folder, every game on Colab
    python src/drive_link.py --send test        copy clips/test.mp4 (and its court files) into the inbox, with progress
    python src/drive_link.py --status test      one game's status, in the words the pages show
    python src/drive_link.py --import test      bring a finished game's results back now
    python src/drive_link.py --poll             one pass of what the dashboard does every minute
    --drive-root FOLDER                         use this folder instead of the one in the settings (a test on the laptop)

No web code in here: api_drive.py answers the browser with these functions. Nothing here talks to
the internet: Google Drive for desktop carries the files; this module only copies them to and from
a folder on this computer.

The Drive folder (settings "drive_folder_path"; colab_worker.py documents the same layout from the Colab side)
    inbox/<stem>.mp4            a game for Colab (written as <stem>.mp4.part while it is being copied)
    inbox/<stem>/               its court files, when the court is marked here: court_setup.json,
                                court_calibration.json, hoops.json, court_views/
    outbox/<stem>/status.json   what Colab says: "state" running | done | needs_court | failed, "progress",
                                "how_far", "stages", "message", "started", "finished", "updated"
    outbox/<stem>/data|outputs|crops   the results, brought back into data/<stem>, outputs/<stem>, crops/<stem>
    models/player_detector.pt   the player finder Colab uses; copied there with the first game when it is missing
    worker.log                  everything the Colab worker printed

What this laptop remembers: data/_dashboard/drive.json
    {"games": {"<stem>": {"sent", "video_bytes", "court_sent", "imported", "imported_state",
                          "imported_finished", "kept"}},
     "last_import": {"stem", "when", "state", "copied", "kept", "deleted_video"},
     "last_poll": {"when", "did": [...]}}

How results come back without the coach doing anything
    start_watching() runs a thread inside the dashboard (api_drive.py starts it when the dashboard
    starts). Every minute it reads outbox/<stem>/status.json for each game marked processed_on
    "colab" whose results are not all here, and imports them as soon as Colab says done or
    needs_court. A local file newer than Colab's copy is kept and named in the report; the coach's
    own files (shots, possessions, game info) are never replaced. With "drive_delete_after" on, the
    video is deleted from the inbox after a "done" import. The outbox is never deleted.
"""
import argparse
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

import colab_worker       # the folder layout the Colab side expects: COURT_FILES, COURT_VIEWS, changed(), read_status()
import config
import court_mapper
import dash_common
import library
import main

Problem = library.Problem

CHUNK = 8 * 1024 * 1024            # a video is copied 8 MB at a time, so progress can be shown
STALE_MINUTES = 20                 # Colab reports every minute while it runs; silence this long is worth saying
POLL_SECONDS = 60                  # how often the watcher looks in the outbox
MODEL_NAME = "player_detector.pt"  # the file name the Colab worker expects in models/
# Files only the coach writes on this laptop: never replaced by Colab's copy.
COACH_FILES = {"game_info.json", "shots.json", "possessions.json"}
# Plain words for the pipeline's stages, as Colab names them in its progress line.
STAGE_WORDS = {"detect": "finding the players", "court": "mapping the court", "teams": "sorting the players into teams",
               "jerseys": "reading the jersey numbers", "tactical": "drawing the court view", "stats": "working out the stats"}
FINISHED_STATES = ("done", "needs_court")

OVERRIDE_ROOT = None      # set by --drive-root; the settings decide otherwise

_sending = {}             # stem -> the copy going on now (see send_game)
_record_lock = threading.Lock()
_last_poll = {"when": None, "did": []}
_watching = False


# ---------------------------------------------------------------- the Drive folder

def drive_root():
    """The Drive folder as a Path, or None when no folder is set."""
    if OVERRIDE_ROOT:
        return Path(OVERRIDE_ROOT)
    path = dash_common.load_settings()["drive_folder_path"]
    return Path(path) if path else None


def connection():
    """Is the Drive folder there and writable? {"connected", "folder", "windows_path", "message"}."""
    root = drive_root()
    if root is None:
        return {"connected": False, "folder": "", "windows_path": "",
                "message": "Google Drive is not connected: no Drive folder is set. On the Settings page, type where "
                           "Google Drive for desktop keeps the Hoops Tracker folder on this computer."}
    windows = main.windows_path(root)
    try:
        there = root.is_dir()
    except OSError:
        there = False
    if not there:
        return {"connected": False, "folder": str(root), "windows_path": windows,
                "message": f"Google Drive is not connected: the folder {windows} cannot be found. "
                           "Is Google Drive for desktop running?"}
    if not os.access(root, os.W_OK):
        return {"connected": False, "folder": str(root), "windows_path": windows,
                "message": f"Google Drive is not connected: the folder {windows} cannot be written to."}
    return {"connected": True, "folder": str(root), "windows_path": windows, "message": f"Connected: {windows}"}


def is_connected():
    return connection()["connected"]


def require_connected():
    """The Drive folder, or a Problem in plain words."""
    check = connection()
    if not check["connected"]:
        raise Problem(check["message"])
    return Path(check["folder"])


def paths(root):
    """The folders inside the Drive folder (none is created here)."""
    return {"inbox": root / "inbox", "outbox": root / "outbox", "models": root / "models", "log": root / "worker.log"}


def make_folders(root):
    where = paths(root)
    for key in ("inbox", "outbox", "models"):
        where[key].mkdir(exist_ok=True)
    return where


def read_status(root, stem):
    """Colab's status.json for a game, or {} when there is none yet (or it is damaged)."""
    return colab_worker.read_status(colab_worker.Drive(root), stem)


# ---------------------------------------------------------------- what this laptop remembers

def record_file():
    return dash_common.DASH_DIR / "drive.json"


def load_record():
    saved = dash_common.read_json(record_file(), {})
    saved = saved if isinstance(saved, dict) else {}
    games = saved.get("games") if isinstance(saved.get("games"), dict) else {}
    return {"games": games, "last_import": saved.get("last_import"), "last_poll": saved.get("last_poll")}


def remember(stem=None, top=None, **fields):
    """Change the record: the fields of one game (stem given) and/or a top-level entry (top={"last_import": ...})."""
    with _record_lock:
        record = load_record()
        if stem is not None:
            record["games"].setdefault(stem, {}).update(fields)
        if top:
            record.update(top)
        dash_common.DASH_DIR.mkdir(exist_ok=True)
        config.save_json(record_file(), record, indent=2)


# ---------------------------------------------------------------- small helpers

def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def short_time(text):
    """"2026-10-10 18:32:07" -> "18:32" today, "10 Oct 18:32" on another day; "" when there is no time."""
    if not text:
        return ""
    try:
        when = time.strptime(str(text), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(text)
    same_day = time.strftime("%Y-%m-%d", when) == time.strftime("%Y-%m-%d")
    return time.strftime("%H:%M" if same_day else "%d %b %H:%M", when)


def minutes_since(text):
    """How many minutes ago a status time was (None when it cannot be read)."""
    try:
        return (time.time() - time.mktime(time.strptime(str(text), "%Y-%m-%d %H:%M:%S"))) / 60
    except (ValueError, TypeError, OverflowError):
        return None


def fmt_bytes(count):
    count = float(count or 0)
    return f"{count / 1e9:.1f} GB" if count >= 1e9 else f"{count / 1e6:.0f} MB"


def fmt_left(seconds):
    """"about 2 min left" from a number of seconds."""
    if seconds is None:
        return ""
    if seconds < 60:
        return "less than a minute left"
    if seconds < 3600:
        return f"about {round(seconds / 60)} min left"
    return f"about {seconds / 3600:.1f} hours left"


def safe_stem(stem):
    """A game's name as it is used in the Drive folder's file names; anything else is refused."""
    stem = str(stem or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}", stem) or ".." in stem:
        raise Problem("That is not a game name.")
    return stem


def video_for(stem):
    video = library.video_of(stem)
    if not video:
        raise Problem("That game is not in the clips folder any more.")
    return video


def stage_from(line):
    """("jerseys", 4, 6) from Colab's progress line "Stage 4 of 6: jerseys - read 40 of 600 frames"."""
    found = re.match(r"Stage (\d+) of (\d+): (\w+)", str(line or ""))
    return (found[3], int(found[1]), int(found[2])) if found else ("", 0, 0)


# ---------------------------------------------------------------- sending a game

def court_marked(video):
    return not court_mapper.setup_missing(video)


def send_court_files(stem, video, inbox):
    """Copy the court set-up into inbox/<stem>/ (only what changed). True when the court is marked and was sent."""
    if not court_marked(video):
        return False
    source, target = config.data_dir(video), inbox / stem
    target.mkdir(exist_ok=True)
    for name in colab_worker.COURT_FILES:
        if (source / name).exists() and colab_worker.changed(source / name, target / name):
            shutil.copy2(source / name, target / name)
    colab_worker.copy_tree(source / colab_worker.COURT_VIEWS, target / colab_worker.COURT_VIEWS)
    return True


def copy_with_progress(source, target, job):
    """Copy one file in pieces, as <name>.part renamed into place at the end, adding to job["done"] as it goes.

    The worker and Google Drive only see the finished file under its real name; a copy cut short
    (the dashboard closed) leaves a .part file that the next send replaces.
    """
    part = target.with_name(target.name + ".part")
    with open(source, "rb") as reading, open(part, "wb") as writing:
        while True:
            piece = reading.read(CHUNK)
            if not piece:
                break
            writing.write(piece)
            job["done"] += len(piece)
    shutil.copystat(source, part)       # the same modification time, so changed() sees the copy as up to date
    os.replace(part, target)


def send_plan(stem, root):
    """What a send would copy: [(source, target)] for the video (unless it is there already) and the model (once)."""
    video = video_for(stem)
    where = paths(root)
    source = config.video_path(video)
    todo = []
    if colab_worker.changed(source, where["inbox"] / source.name):
        todo.append((source, where["inbox"] / source.name))
    model = config.detector_path()
    if not (where["models"] / MODEL_NAME).exists() and model.exists():
        todo.append((model, where["models"] / MODEL_NAME))
    return video, todo


def send_game(stem, background=True):
    """Send a game to Colab through the Drive folder. Returns {"message", "copying", "files", "court_sent"}.

    The video is copied into inbox/ by a thread (status(stem) shows how far it is and the time
    left), the player model goes with the first game, then the court files when the court is
    marked, and the game is marked processed_on "colab". A video that is in the inbox already is
    not copied again: only the court files are sent, which is how a "needs_court" game is finished.
    Raises Problem with plain words when it cannot (not connected, running here, no room...).
    background=False does the copy in this thread (the command line).
    """
    stem = safe_stem(stem)
    root = require_connected()
    video, todo = send_plan(stem, root)
    if dash_common.is_running(video):
        raise Problem(f"{stem} is being processed on this laptop right now. Wait for it to finish, or stop it, "
                      "before sending it to Colab.")
    job = _sending.get(stem)
    if job and job["finished"] is None:
        raise Problem(f"{stem} is being copied to the Drive folder already.")
    total = sum(source.stat().st_size for source, _ in todo)
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        free = None
    if total and free is not None and free < total + 50_000_000:
        raise Problem(f"Not enough room on the drive that holds the Drive folder: {fmt_bytes(total)} to copy, "
                      f"{fmt_bytes(free)} free.")
    where = make_folders(root)
    job = _sending[stem] = {"stem": stem, "total": total, "done": 0, "started": time.time(), "finished": None,
                            "error": "", "files": [target.name for _, target in todo], "court_sent": False}

    def work():
        try:
            for source, target in todo:
                copy_with_progress(source, target, job)
            job["court_sent"] = send_court_files(stem, video, where["inbox"])
            library.save_game_info(stem, {"processed_on": "colab"})
            remember(stem, sent=now_text(), video_bytes=config.video_path(video).stat().st_size,
                     court_sent=job["court_sent"], imported=None, imported_state=None, imported_finished=None, kept=[])
        except (OSError, SystemExit, Problem) as why:
            job["error"] = f"The copy to the Drive folder stopped: {why}"
            for _, target in todo:
                target.with_name(target.name + ".part").unlink(missing_ok=True)
        finally:
            job["finished"] = time.time()

    thread = threading.Thread(target=work, daemon=True, name=f"drive-send-{stem}")
    thread.start()
    if not background:
        while thread.is_alive():            # the command line: show how far the copy is, once a second
            thread.join(1)
            if job["total"]:
                print("  " + copying_status(job)["message"], flush=True)
        if job["error"]:
            raise Problem(job["error"])

    video_name = config.video_path(video).name
    sizes = {target.name: source.stat().st_size for source, target in todo}     # the video and the model are named apart
    if video_name in sizes:
        message = (f"{'Copying' if background else 'Copied'} {video_name} ({fmt_bytes(sizes[video_name])}) into the Drive folder. "
                   "Google Drive for desktop uploads it from there, and Colab starts on it once its notebook is running.")
    else:
        message = f"{video_name} is in the Drive folder already."
    if MODEL_NAME in sizes:
        message += f" The player model ({fmt_bytes(sizes[MODEL_NAME])}) goes with it (only this once)."
    if court_marked(video):
        message += (" The court files are sent too, so Colab can finish the positions and stats."
                    if "already" not in message else
                    " The court files were sent: Colab carries on with the positions and stats the next time it looks.")
    else:
        message += " The court is not marked yet: Colab does the long steps, then waits for the court."
    return {"message": message, "copying": bool(todo), "files": job["files"], "court_sent": court_marked(video)}


# ---------------------------------------------------------------- the status of a game

def copying_status(job):
    """The words while the copy into the Drive folder is going."""
    how_far = job["done"] / job["total"] if job["total"] else 1.0
    taken = time.time() - job["started"]
    left = fmt_left((job["total"] - job["done"]) / (job["done"] / taken)) if job["done"] and taken > 1 else ""
    percent = round(100 * how_far)
    return {"state": "copying", "words": " · ".join(filter(None, [f"Copying to Drive · {percent}%", left])),
            "message": f"Copying into the Drive folder: {fmt_bytes(job['done'])} of {fmt_bytes(job['total'])}"
                       + (f", {left}." if left else "."),
            "progress": {"how_far": how_far, "left": left, "line": f"{fmt_bytes(job['done'])} of {fmt_bytes(job['total'])}",
                         "stage": "copy", "stage_words": "copying the video", "step_number": 0, "step_count": 0}}


def colab_status(saved, known, in_inbox):
    """The words for what Colab's status.json says about a game (and whether its results are here)."""
    state = str(saved.get("state") or "")
    finished = short_time(saved.get("finished"))
    imported = known.get("imported_state") == state and known.get("imported_finished") == saved.get("finished")
    reply = {"state": state, "progress": None, "stale": False, "can_import": state in FINISHED_STATES and not imported,
             "imported": known.get("imported") if imported else None}
    if state == "running":
        stage, number, count = stage_from(saved.get("progress"))
        how_far = saved.get("how_far")
        how_far = float(how_far) if isinstance(how_far, (int, float)) else None
        stage_words = STAGE_WORDS.get(stage, stage) if stage else "starting"
        words = ["On Colab"] + ([f"{round(100 * how_far)}%"] if how_far is not None else []) + [stage_words]
        message = f"Colab is working on it: step {number} of {count}, {stage_words}." if stage else "Colab has started on it."
        age = minutes_since(saved.get("updated"))
        if age is not None and age > STALE_MINUTES:
            reply["stale"] = True
            words.append(f"no word since {short_time(saved.get('updated'))}")
            message = (f"Colab has not reported since {short_time(saved.get('updated'))}. The notebook may have stopped: "
                       "open it and press Run all again; it carries on where it stopped.")
        reply.update(words=" · ".join(words), message=message,
                     progress={"how_far": how_far, "left": "", "line": str(saved.get("progress") or ""), "stage": stage,
                               "stage_words": stage_words, "step_number": number, "step_count": count})
    elif state == "done":
        if imported:
            reply.update(words=f"Back from Colab · results brought back {short_time(known.get('imported'))}",
                         message=f"The results are here. Colab finished at {finished}.")
        else:
            reply.update(words="Done on Colab · bringing the results back",
                         message=f"Colab finished at {finished}. The results are brought back within a minute "
                                 "(or press Import now).")
        reply["progress"] = {"how_far": 1.0, "left": "", "line": "", "stage": "", "stage_words": "", "step_number": 0, "step_count": 0}
    elif state == "needs_court":
        if imported:
            reply.update(words="Colab needs the court · mark the court here, then send again",
                         message="Detection, teams and jersey numbers are back. Mark the court on the game page and press "
                                 "Run on Colab again: the court files go to Colab and it finishes the positions and stats.")
        else:
            reply.update(words="Colab needs the court · bringing back what is done",
                         message=f"Colab did the long steps by {finished} and waits for the court. What is done is "
                                 "brought back within a minute (or press Import now).")
    elif state == "failed":
        first = str(saved.get("message") or "").splitlines()
        reply.update(words="Failed on Colab", message=(first[0] if first else "Colab stopped with an error.")
                     + " Run the notebook again and it tries once more, or press Run here.")
    else:
        reply.update(state="unknown", words="On Colab · status not understood",
                     message="Colab's status file could not be read. It is written again every minute while Colab runs.")
    if not in_inbox and state in ("running", "needs_court", "failed"):
        reply["message"] += " (The video is no longer in the Drive inbox.)"
    return reply


def status(stem):
    """What the game cards say about a game on Colab, in plain words.

    Returns {"connected", "stem", "state", "words", "message", "progress", "stages", "sent", "updated",
             "finished", "imported", "stale", "can_import", "in_inbox", "court_sent"}.
      state     "not sent" | "copying" | "copy failed" | "waiting" | "running" | "done" | "needs_court" | "failed" | "unknown"
      words     the one-line version for a card: "On Colab · 57% · reading the jersey numbers"
      message   a sentence or two with what to do
      progress  {"how_far": 0..1 or None, "left", "line", "stage", "stage_words", "step_number", "step_count"} or None
    When Drive is not connected: {"connected": False, "state": "not connected", "message": why}.
    """
    stem = safe_stem(stem)
    check = connection()
    base = {"connected": check["connected"], "stem": stem, "state": "", "words": "", "message": "", "progress": None,
            "stages": None, "sent": None, "updated": None, "finished": None, "imported": None, "stale": False,
            "can_import": False, "in_inbox": False, "court_sent": False}
    if not check["connected"]:
        return dict(base, state="not connected", words="Drive not connected", message=check["message"])
    root = Path(check["folder"])
    where = paths(root)
    known = load_record()["games"].get(stem, {})
    base.update(sent=known.get("sent"), court_sent=bool(known.get("court_sent")))
    video = library.video_of(stem)
    video_name = config.video_path(video).name if video else f"{stem}.mp4"
    base["in_inbox"] = (where["inbox"] / video_name).exists()

    job = _sending.get(stem)
    if job and job["finished"] is None:
        return dict(base, **copying_status(job))
    if job and job["error"]:
        return dict(base, state="copy failed", words="Copy to Drive failed",
                    message=job["error"] + " Press Run on Colab to try again.")

    saved = read_status(root, stem)
    if saved.get("state"):
        base.update(stages=saved.get("stages"), updated=saved.get("updated"), finished=saved.get("finished"))
        return dict(base, **colab_status(saved, known, base["in_inbox"]))
    if base["in_inbox"]:
        court = " The court files went with it." if known.get("court_sent") else ""
        return dict(base, state="waiting", words="Sent to Drive · waiting for Colab",
                    message=f"The video is in the Drive folder (sent {short_time(known.get('sent')) or 'by hand'}). Google Drive "
                            "for desktop uploads it; Colab starts on it when its notebook is running (Run all)." + court)
    if known.get("sent"):
        return dict(base, state="not sent", words="Not in the Drive inbox",
                    message=f"The video was sent {short_time(known['sent'])} but is not in the Drive inbox any more, and "
                            "Colab has not reported on it. Press Run on Colab to send it again.")
    return dict(base, state="not sent", words="Not sent to Drive", message="This game has not been sent to Colab.")


# ---------------------------------------------------------------- bringing the results back

def mirror_in(source, target):
    """Copy the files under `source` into `target`: new files, and files Colab's copy is newer than.

    A local file that is newer than Colab's is kept and listed; the coach's own files (COACH_FILES)
    always are. copy2 keeps the modification times, which main.py uses to tell what is up to date.
    Returns (how many files were copied, [kept paths, relative]).
    """
    copied, kept = 0, []
    if not source.is_dir():
        return copied, kept
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.name.endswith(".tmp"):
            continue
        relative = path.relative_to(source)
        local = target / relative
        if path.name in COACH_FILES:            # Colab has nothing to say about the coach's own files
            if local.exists():
                kept.append(str(relative))
            continue
        if local.exists():
            theirs, mine = path.stat(), local.stat()
            if mine.st_mtime > theirs.st_mtime + 1:
                kept.append(str(relative))
                continue
            if mine.st_size == theirs.st_size and abs(mine.st_mtime - theirs.st_mtime) <= 1:
                continue                                    # the same file already
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, local)
        copied += 1
    return copied, kept


def results_missing(video, saved):
    """The result files Colab's status promises that are not here yet, by name ([] when they all are).

    Google Drive syncs a folder's files in no set order, and status.json is the smallest, so a game can
    read as "done" before its big result files have arrived. The stages the status calls done must have
    their files (positions, stats...); the videos are not waited for, as a worker run without them never
    sends any. An older status without "stages" promises every stage ("done") or the ones before the court.
    """
    stages = saved.get("stages") if isinstance(saved.get("stages"), dict) else {}
    if stages:
        promised = [stage for stage in main.STAGES if stages.get(stage) == "done"]
    else:
        promised = [stage for stage in main.STAGES if saved.get("state") == "done" or stage not in main.NEEDS_COURT]
    missing = []
    for stage in promised:
        for path in main.stage_files(video, stage)[1]:
            if path.suffix.lower() != ".mp4" and not path.exists():
                missing.append(path.name)
    return missing


def import_results(stem):
    """Bring a game's results back from outbox/<stem>/ into data/, outputs/ and crops/.

    Only when Colab's state is done or needs_court. A local file newer than Colab's copy is kept
    and named in "kept" (the coach's shots, possessions and game info are never replaced). The
    game is marked processed_on "colab"; with the setting "drive_delete_after" on, the video is
    deleted from the inbox after a "done" import (never after needs_court: Colab still needs it).
    Returns {"state", "copied", "kept", "deleted_video", "missing", "message"}; while result files the status
    promises are still missing (Drive has not brought them yet) the import is not recorded, so the next look
    tries again. Raises Problem in plain words.
    """
    stem = safe_stem(stem)
    root = require_connected()
    video = video_for(stem)
    saved = read_status(root, stem)
    state = str(saved.get("state") or "")
    if state == "running":
        how_far = saved.get("how_far")
        percent = f" ({round(100 * how_far)}%)" if isinstance(how_far, (int, float)) else ""
        raise Problem(f"Colab is still working on {stem}{percent}. The results come back when it is done.")
    if state == "failed":
        raise Problem(f"Colab failed on {stem}, so there are no results to bring back. "
                      "Run the notebook again, or press Run here.")
    if state not in FINISHED_STATES:
        raise Problem(f"Colab has not reported anything about {stem} yet.")
    if dash_common.is_running(video):
        raise Problem(f"{stem} is being processed on this laptop right now; the results from Colab would clash with it. "
                      "Wait for it to finish.")
    outbox = paths(root)["outbox"] / stem
    copied, kept = 0, []
    for name, target in (("data", config.data_dir(video)), ("outputs", config.output_dir(video)), ("crops", config.crops_dir(video))):
        count, left_alone = mirror_in(outbox / name, target)
        copied += count
        kept += [f"{name}/{path}" for path in left_alone]
    library.save_game_info(stem, {"processed_on": "colab"})
    when = now_text()
    result = {"stem": stem, "when": when, "state": state, "copied": copied, "kept": kept, "deleted_video": False,
              "missing": results_missing(video, saved)}
    if result["missing"]:
        # Not recorded as imported: the next look (every minute) copies the rest when it has arrived.
        remember(stem, top={"last_import": result}, kept=kept)
        return dict(result, message=f"{copied} file(s) brought back from Colab so far, but the results are not all here yet "
                                    f"({', '.join(result['missing'][:3])}{'...' if len(result['missing']) > 3 else ''}). "
                                    "Google Drive is still bringing them; looking again in a minute.")
    if state == "done" and dash_common.load_settings()["drive_delete_after"]:
        inbox_video = paths(root)["inbox"] / config.video_path(video).name
        if inbox_video.exists():
            inbox_video.unlink()
            result["deleted_video"] = True
    deleted = result["deleted_video"]
    remember(stem, top={"last_import": result}, imported=when, imported_state=state,
             imported_finished=saved.get("finished"), kept=kept)
    if state == "done":
        message = f"{copied} file(s) brought back from Colab; {stem} is processed."
    else:
        message = (f"{copied} file(s) brought back from Colab. {stem} still needs the court: mark it on the game page, "
                   "then press Run on Colab again.")
    if kept:
        message += f" Kept {len(kept)} newer file(s) of this laptop's own: " + ", ".join(kept[:5]) + ("..." if len(kept) > 5 else "") + "."
    if deleted:
        message += " The video was deleted from the Drive inbox."
    return dict(result, message=message)


# ---------------------------------------------------------------- the watcher

def games_on_colab():
    """The games marked processed_on "colab" whose results are not all here yet, by name."""
    found = []
    for path in dash_common.video_files():
        if library.game_info(path.stem)["processed_on"] != "colab":
            continue
        stages = dash_common.stage_statuses(f"clips/{path.name}")
        if all(state == "done" for state in stages.values()):
            continue
        found.append(path.stem)
    return found


def poll_once():
    """What the watcher does every minute: import every game Colab has finished and this laptop has not imported.

    Returns the lines of what happened (empty when there was nothing to do).
    """
    did = []
    check = connection()
    if not check["connected"]:
        return did
    root = Path(check["folder"])
    known = load_record()["games"]
    for stem in games_on_colab():
        saved = read_status(root, stem)
        if saved.get("state") not in FINISHED_STATES:
            continue
        before = known.get(stem, {})
        if before.get("imported_state") == saved["state"] and before.get("imported_finished") == saved.get("finished"):
            continue
        try:
            did.append(f"{stem}: {import_results(stem)['message']}")
        except (Problem, OSError, SystemExit) as why:
            did.append(f"{stem}: the results could not be brought back: {why}")
    _last_poll.update(when=now_text(), did=did)
    if did:
        remember(top={"last_poll": dict(_last_poll)})
    return did


def start_watching(seconds=POLL_SECONDS):
    """Start the thread that brings results back every minute. Once per program; api_drive.py calls it at start-up."""
    global _watching
    if _watching:
        return
    _watching = True

    def watch():
        while True:
            time.sleep(seconds)
            try:
                poll_once()
            except (Exception, SystemExit) as why:        # whatever goes wrong, the watcher must keep going
                _last_poll.update(when=now_text(), did=[f"The look in the Drive folder failed: {why}"])

    threading.Thread(target=watch, daemon=True, name="drive-watch").start()


# ---------------------------------------------------------------- the whole picture

def summary():
    """Everything the Settings page and GET /api/drive show: the connection, what is in the folder, the games.

    {"connected", "folder", "windows_path", "message", "delete_after", "inbox": [{"name", "bytes", "copying"}],
     "outbox": [{"stem", "state", "finished", "updated"}], "model": {"name", "bytes"} or None,
     "worker_log": {"updated", "last_line"} or None, "last_import", "last_poll", "watching", "games": [status()...]}
    """
    settings = dash_common.load_settings()
    reply = dict(connection(), delete_after=bool(settings["drive_delete_after"]), inbox=[], outbox=[], model=None,
                 worker_log=None, last_import=load_record()["last_import"], last_poll=dict(_last_poll),
                 watching=_watching, games=[])
    stems = set(_sending)
    for path in dash_common.video_files():
        if library.game_info(path.stem)["processed_on"] == "colab":
            stems.add(path.stem)
    if not reply["connected"]:
        reply["games"] = [status(stem) for stem in sorted(stems)]
        return reply
    where = paths(Path(reply["folder"]))
    try:
        if where["inbox"].is_dir():
            for path in sorted(where["inbox"].iterdir()):
                name = path.name
                copying = name.endswith(".part")
                if path.is_file() and (copying or path.suffix.lower() in colab_worker.VIDEO_TYPES):
                    reply["inbox"].append({"name": name[:-5] if copying else name, "bytes": path.stat().st_size, "copying": copying})
        if where["outbox"].is_dir():
            for path in sorted(where["outbox"].iterdir()):
                if path.is_dir():
                    saved = read_status(where["outbox"].parent, path.name)
                    reply["outbox"].append({"stem": path.name, "state": saved.get("state") or "unknown",
                                            "finished": saved.get("finished"), "updated": saved.get("updated")})
        model = where["models"] / MODEL_NAME
        if model.is_file():
            reply["model"] = {"name": MODEL_NAME, "bytes": model.stat().st_size}
        if where["log"].is_file():
            lines = [line for line in dash_common.tail(where["log"], 4000).splitlines() if line.strip()]
            reply["worker_log"] = {"updated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(where["log"].stat().st_mtime)),
                                   "last_line": lines[-1] if lines else ""}
    except OSError as why:
        reply["message"] += f" (Some of the folder could not be read: {why})"
    reply["games"] = [status(stem) for stem in sorted(stems)]
    return reply


# ---------------------------------------------------------------- the command line

def parse_args():
    parser = argparse.ArgumentParser(description="The Google Drive link to Colab: send a game, see its status, bring results back.")
    parser.add_argument("--send", metavar="GAME", help="copy clips/<GAME>.mp4 and its court files into the Drive inbox")
    parser.add_argument("--status", metavar="GAME", help="one game's status, in the words the pages show")
    parser.add_argument("--import", dest="bring", metavar="GAME", help="bring a finished game's results back now")
    parser.add_argument("--poll", action="store_true", help="one pass of what the dashboard does every minute")
    parser.add_argument("--drive-root", help="use this folder instead of the one in the settings")
    return parser.parse_args()


def print_summary():
    picture = summary()
    print(picture["message"])
    if not picture["connected"]:
        return
    print(f"Inbox: {len(picture['inbox'])} video(s)" + "".join(f"\n    {row['name']}  {fmt_bytes(row['bytes'])}"
                                                                + ("  (copying)" if row["copying"] else "") for row in picture["inbox"]))
    print(f"Outbox: {len(picture['outbox'])} game(s)" + "".join(f"\n    {row['stem']}  {row['state']}" for row in picture["outbox"]))
    print("Model in Drive: " + (f"{picture['model']['name']} ({fmt_bytes(picture['model']['bytes'])})" if picture["model"]
                                else f"missing (it is copied with the first game: models/{MODEL_NAME})"))
    if picture["worker_log"]:
        print(f"Colab's last line ({picture['worker_log']['updated']}): {picture['worker_log']['last_line']}")
    print("Games on Colab:" + ("".join(f"\n    {row['stem']}: {row['words']}" for row in picture["games"]) or " none"))


def run(args):
    global OVERRIDE_ROOT
    OVERRIDE_ROOT = args.drive_root
    if args.send:
        reply = send_game(args.send, background=False)
        print(reply["message"])
    elif args.status:
        reply = status(args.status)
        print(f"{reply['stem']}: {reply['words']}\n{reply['message']}")
    elif args.bring:
        print(import_results(args.bring)["message"])
    elif args.poll:
        did = poll_once()
        print("\n".join(did) if did else "Nothing to bring back.")
    else:
        print_summary()


if __name__ == "__main__":
    try:
        run(parse_args())
    except Problem as why:
        sys.exit(str(why))
