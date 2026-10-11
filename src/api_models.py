"""The Dataset screen's and the Train and Models screen's own API (web/dataset.html, web/models.html).

Everything is under /api/models. The real work is in training_versions.py (the versions, the speed test); this
file answers the two pages, runs the version build in a thread, and talks to the Google Drive folder.

    GET  /api/models/dataset       what the next version would hold, the split, the variations, the versions table
    PUT  /api/models/test-game     body {"game": "<stem>" or null}: keep one whole game for testing (project.json)
    POST /api/models/build         body {"augmentations": ["light", ...]}: build the next version, in a thread
    GET  /api/models/build         how the build is going: {"running", "done", "total", "seconds_left", "checking", "result", "error"}
    GET  /api/models/              the models table, the chart, where it struggles, Drive and Colab, the speed-test job
    POST /api/models/use           body {"name"}: make it the detector in use (settings.detector_model)
    POST /api/models/try           body {"name"}: run it on clips/test.mp4 as the job "try_model" (heavy: one at a time)
    POST /api/models/send          body {"version", "base", "length"}: copy the zip (and the model to start from) to Drive
    GET  /api/models/send          how the copy is going
    POST /api/models/fetch         body {"name"}: copy a finished model (.pt and .json) from Drive's models/ into models/
    GET  /api/models/notebook      the training notebook as a download

The Drive folder (settings.drive_folder_path, kept in step by Google Drive for desktop; the notebook uses the same)
    training/dataset_v<n>.zip      training/request.json {"dataset_version", "base", "length"}      training/status.json
    models/<name>.pt + <name>.json  written by the notebook when it finishes; "fetch" copies them to the laptop

Building a version and copying to Drive load no model, so they run in a thread inside the dashboard and the page
asks every second how far they are. The speed test loads a model, so it is a job (a separate program under the
heavy lock), watched with pollJob('try_model').
"""
import os
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

import config
import dash_common
import main as pipeline      # only for windows_path()
import training_versions as tv

router = APIRouter(prefix="/api/models")

NOTEBOOK = config.PROJECT_ROOT / "colab" / "train_detector.ipynb"
# Colab opens a notebook straight from the public GitHub copy of this project with this address.
COLAB_URL = f"https://colab.research.google.com/github/gsharp23/basketball-tracker/blob/main/colab/{NOTEBOOK.name}"
TEST_CLIP = "clips/test.mp4"
LENGTHS = ("normal", "long")
STALE_MINUTES = 30           # a Colab status line older than this, still saying "running", is probably a closed tab
CONDITION_WORDS = {"dim": "in dim gyms", "blur": "when blurred", "wide": "zoomed out"}

# The speed test: python src/training_versions.py --video clips/test.mp4 --try-model <name>
dash_common.JOB_KINDS["try_model"] = {"script": "training_versions.py", "heavy": True, "name": "try_model",
                                      "options": ["--try-model", "--frames"]}


def windows_name(path):
    return pipeline.windows_path(path)


# ---------------------------------------------------------------- the two long jobs in threads

# One dictionary per job: "running" while the thread works, "error" a plain message when it failed.
building = {"running": False, "done": 0, "total": 0, "seconds_left": None, "checking": False, "result": None, "error": None}
sending = {"running": False, "step": "", "done": 0, "total": 0, "result": None, "error": None}


def run_build(augmentations):
    building.update(running=True, done=0, total=0, seconds_left=None, checking=False, result=None, error=None)
    try:
        for step in tv.build_version(augmentations):
            if "result" in step:
                building["result"] = version_row(step["result"], tv.model_cards())
            else:
                building.update(step)
    except (ValueError, OSError) as problem:
        building["error"] = str(problem)
    except Exception as problem:            # a fault in the program: the page must still get a message
        building["error"] = f"The version could not be built: {problem}"
    finally:
        building["running"] = False


def copy_with_progress(source, target, state):
    """Copy a big file in pieces so the page can show how far it is; the file gets its real name only when whole."""
    state.update(total=source.stat().st_size, done=0)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".part")
    with open(source, "rb") as src, open(temp, "wb") as dst:
        while True:
            chunk = src.read(8 << 20)
            if not chunk:
                break
            dst.write(chunk)
            state["done"] += len(chunk)
    if temp.stat().st_size != source.stat().st_size:
        temp.unlink(missing_ok=True)
        raise OSError(f"{target.name} did not copy whole. Is the drive full?")
    os.replace(temp, target)


def run_send(version, base, length, drive):
    sending.update(running=True, step="", done=0, total=0, result=None, error=None)
    try:
        training = Path(drive) / "training"
        zip_path = Path(version["zip_path"])
        sending["step"] = f"Copying dataset v{version['number']} ({version.get('megabytes', '?')} MB) to Drive..."
        copy_with_progress(zip_path, training / f"dataset_v{version['number']}.zip", sending)
        copied_base = False
        if base != "fresh" and not (Path(drive) / "models" / base).is_file():
            sending["step"] = f"Copying the model to start from, {base}, to Drive..."
            copy_with_progress(tv.models_dir() / base, Path(drive) / "models" / base, sending)
            copied_base = True
        config.save_json(training / "request.json",
                         {"dataset_version": version["number"], "base": base, "length": length,
                          "sent": datetime.now().isoformat(timespec="seconds")}, indent=2)
        (training / "status.json").unlink(missing_ok=True)      # an old "finished" line must not be taken for this run
        sending["result"] = {"zip": f"dataset_v{version['number']}.zip", "base_copied": copied_base,
                             "folder": windows_name(training)}
    except (ValueError, OSError) as problem:
        sending["error"] = str(problem)
    except Exception as problem:
        sending["error"] = f"The copy to Drive did not finish: {problem}"
    finally:
        sending["running"] = False


# ---------------------------------------------------------------- the Dataset screen

def game_words(stem):
    """"vs Riverside, 24 Jan 2026" from the game's info, or just the stem (a game that is not in the clips folder)."""
    info = dash_common.read_json(config.DATA_DIR / stem / "game_info.json", {})
    info = info if isinstance(info, dict) else {}
    words = f"vs {info['opponent']}" if info.get("opponent") else stem
    conditions = info.get("conditions") if isinstance(info.get("conditions"), dict) else {}
    return {"game": stem, "words": words, "date": info.get("date") or "", "gym": conditions.get("gym") or ""}


def version_row(info, cards):
    """One line of the versions table: the version.json plus the model trained from it (if any) and the words."""
    trained = [card for card in cards if card["card"].get("dataset_version") == info["number"]]
    model = None
    if trained:
        best = max(trained, key=lambda card: card["card"].get("trained") or "")
        model = {"name": best["name"], "label": model_label(best["name"], best["card"]),
                 "ball": percent((best["card"].get("scores") or {}).get("Ball"))}
    zip_file = info.get("zip_file")
    return {"number": info["number"], "built": info.get("built"), "pictures": info["counts"]["pictures"],
            "counts": info["counts"], "games": info.get("games", []), "gyms": info.get("gyms"),
            "variations": tv.variation_words(info.get("augmentations", [])), "augmentations": info.get("augmentations", []),
            "split": info.get("split", {}), "test_game": info.get("test_game"), "megabytes": info.get("megabytes"),
            "model": model, "zip_url": "/files/data/" + zip_file if zip_file else None,
            "zip_path": windows_name(info["zip_path"]) if info.get("has_zip") else None}


def dataset_reply():
    entries = tv.load_labels()
    project = tv.load_project()
    plan = tv.plan_version()
    games = []
    for row in tv.games_of(entries):
        games.append(dict(row, **game_words(row["game"]), is_test=row["game"] == project["test_game"]))
    cards = tv.model_cards()
    return {"project": project, "games": games, "checked": plan["checked"], "plan": plan,
            "variations": tv.VARIATIONS, "versions": [version_row(info, cards) for info in tv.list_versions()],
            "build": building, "min_pictures": tv.MIN_PICTURES}


@router.get("/dataset")
def dataset():
    try:
        return dataset_reply()
    except (ValueError, OSError) as problem:
        raise HTTPException(400, str(problem))


@router.put("/test-game")
def set_test_game(body: dict):
    """Keep one whole game for testing: body {"game": "<stem>"} or {"game": null} to keep none."""
    game = body.get("game") if isinstance(body, dict) else None
    if game is not None:
        game = str(game)
        if game not in {row["game"] for row in tv.games_of(tv.load_labels())}:
            raise HTTPException(400, f"No pictures have been picked from \"{game}\" yet.")
    try:
        tv.save_project({"test_game": game})
    except OSError:
        raise HTTPException(503, "Could not write to the training folder.")
    return dataset_reply()


@router.post("/build")
def build(body: dict):
    """Build the next version with these variations ticked: body {"augmentations": ["light", "blur", ...]}."""
    if building["running"]:
        raise HTTPException(409, "A version is being built already. Wait for it to finish.")
    keys = {item["key"] for item in tv.VARIATIONS}
    wanted = body.get("augmentations") if isinstance(body, dict) else None
    if not isinstance(wanted, list):
        wanted = tv.default_augmentations()
    chosen = [key for key in wanted if key in keys]
    try:
        tv.plan_version(chosen)     # the plain checks happen in the thread; this only confirms the labels can be read
    except (ValueError, OSError) as problem:
        raise HTTPException(400, str(problem))
    building["running"] = True          # before the thread starts: a build that ends first must not leave this on
    threading.Thread(target=run_build, args=(chosen,), daemon=True).start()
    return {"started": True}


@router.get("/build")
def build_progress():
    return building


# ---------------------------------------------------------------- the Train and Models screen

def percent(score):
    """A class score ({"recall": 0.78, ...}) as a whole-number percent of the things found, or None."""
    value = score.get("recall") if isinstance(score, dict) else None
    return None if value is None else round(100 * float(value))


model_label = dash_common.model_label      # "v3", or "Player finder" for a downloaded model: the same words on every screen


def model_row(model, in_use, classes):
    card = model["card"]
    scores = card.get("scores") if isinstance(card.get("scores"), dict) else {}
    by_condition = scores.get("by_condition") if isinstance(scores.get("by_condition"), dict) else {}
    per_class = {name: percent(scores.get(name)) for name in classes}
    conditions = {condition: {name: percent(found.get(name)) for name in classes}
                  for condition, found in by_condition.items() if isinstance(found, dict)}
    return {"name": model["name"], "label": model_label(model["name"], card), "in_use": model["name"] == in_use,
            "trained": card.get("trained"), "downloaded": not card.get("trained"),
            "dataset_version": card.get("dataset_version"), "scored_on_dataset": card.get("scored_on_dataset"),
            "base": card.get("base"), "epochs": card.get("epochs"), "picture_size": card.get("picture_size"),
            "scores": per_class, "by_condition": conditions, "ball_dim": conditions.get("dim", {}).get("Ball"),
            "precision": {name: (round(100 * scores[name]["precision"]) if isinstance(scores.get(name), dict)
                                 and scores[name].get("precision") is not None else None) for name in classes},
            "has_scores": any(value is not None for value in per_class.values()),
            "test": card.get("test") if isinstance(card.get("test"), dict) else {},
            "speed_fps": card.get("speed_fps"), "tried_on": card.get("tried_on") if isinstance(card.get("tried_on"), dict) else None,
            "notes": card.get("notes") if isinstance(card.get("notes"), list) else [], "megabytes": model["megabytes"]}


def struggles_of(row):
    """The three lowest scores of a model, in words: [{"words": "ball in dim gyms", "percent": 61, "condition": "dim"}]."""
    found = []
    for name, value in row["scores"].items():
        if value is not None:
            found.append({"words": name.lower(), "percent": value, "condition": None})
    for condition, per_class in row["by_condition"].items():
        for name, value in per_class.items():
            if value is not None:
                found.append({"words": f"{name.lower()} {CONDITION_WORDS.get(condition, condition)}", "percent": value, "condition": condition})
    return sorted(found, key=lambda item: item["percent"])[:3]


def drive_status():
    """Is the Google Drive folder connected: {"connected", "path", "windows_path", "training_folder", "message"}."""
    path = dash_common.load_settings()["drive_folder_path"]
    if not path:
        return {"connected": False, "path": "", "windows_path": "", "training_folder": "",
                "message": "Google Drive is not connected: name its folder on the Settings screen, or copy the zip yourself."}
    try:
        info = dash_common.check_drive_folder(path)
    except ValueError as problem:
        return {"connected": False, "path": path, "windows_path": "", "training_folder": "", "message": str(problem)}
    return {"connected": True, "path": info["path"], "windows_path": info["windows_path"],
            "training_folder": windows_name(Path(info["path"]) / "training"), "message": ""}


def colab_status(drive):
    """The Colab line: {"state": "idle" | "running" | "stale" | "done" | "failed", "line", "dot", "updated"}."""
    plain = {"state": "idle", "line": "Colab: not running", "dot": "grey", "updated": None}
    if not drive["connected"]:
        return plain
    status = dash_common.read_json(Path(drive["path"]) / "training" / "status.json", None)
    if not isinstance(status, dict) or not status.get("state"):
        return plain
    try:
        age = (datetime.now() - datetime.fromisoformat(str(status.get("updated")))).total_seconds() / 60
    except ValueError:
        age = 0
    line = str(status.get("line") or "")
    state = str(status["state"])
    if state == "running" and age > STALE_MINUTES:
        return {"state": "stale", "dot": "yellow", "updated": status.get("updated"),
                "line": f"Colab: no news for {age:.0f} min (did the tab close?). Last: {line}"}
    dots = {"running": "yellow", "done": "green", "failed": "red"}
    return {"state": state, "dot": dots.get(state, "grey"), "updated": status.get("updated"), "line": f"Colab: {line}"}


def waiting_in_drive(drive):
    """Models the notebook left in Drive's models/ that are not in the laptop's models folder yet."""
    if not drive["connected"]:
        return []
    folder = Path(drive["path"]) / "models"
    waiting = []
    try:
        for path in sorted(folder.glob("*.pt")) if folder.is_dir() else []:
            if not (tv.models_dir() / path.name).exists():
                waiting.append({"name": path.name, "megabytes": round(path.stat().st_size / 1e6),
                                "has_card": path.with_suffix(".json").is_file()})
    except OSError:
        pass
    return waiting


def models_reply():
    classes = tv.load_project()["classes"]
    in_use = dash_common.load_settings()["detector_model"]
    rows = [model_row(model, in_use, classes) for model in tv.model_cards()]
    # trained models newest first, then the downloaded ones
    trained = sorted([row for row in rows if not row["downloaded"]], key=lambda row: (row["trained"], row["label"]), reverse=True)
    # The small general model (trained on everyday photos, not basketball) is listed only while it is the one in use.
    rows = trained + [row for row in rows if row["downloaded"] and (row["in_use"] or Path(row["name"]).stem != "yolov8n")]
    scored = [row for row in rows if row["has_scores"]]
    chart = [{"label": "Before training" if row["downloaded"] else f"Model {row['label']}", "in_use": row["in_use"],
              "scores": row["scores"]} for row in sorted(scored, key=lambda row: (not row["downloaded"], row["trained"] or ""))]
    chosen = next((row for row in scored if row["in_use"]), scored[0] if scored else None)
    drive = drive_status()
    versions = [{"number": info["number"], "pictures": info["counts"]["pictures"], "built": info.get("built"),
                 "test_game": info.get("test_game"), "has_zip": info["has_zip"]} for info in tv.list_versions()]
    job = dash_common.job_info("try_model")
    return {"models": rows, "classes": classes, "in_use": in_use, "chart": chart,
            "struggles": {"model": chosen["label"] if chosen else None, "items": struggles_of(chosen) if chosen else []},
            "versions": versions, "lengths": LENGTHS, "drive": drive, "colab": colab_status(drive),
            "waiting_in_drive": waiting_in_drive(drive), "colab_url": COLAB_URL, "notebook_name": NOTEBOOK.name,
            "test_clip": TEST_CLIP if dash_common.is_clip_video(TEST_CLIP) else None,
            "try_job": job if job["known"] else None, "send": sending, "busy": dash_common.what_is_running()}


@router.get("/")
def models():
    try:
        return models_reply()
    except (ValueError, OSError) as problem:
        raise HTTPException(400, str(problem))


def model_or_400(name):
    name = str(name or "").strip()
    if "/" in name or "\\" in name or name not in {model["name"] for model in tv.model_cards()}:
        raise HTTPException(400, f"There is no model file called \"{name}\" in the models folder.")
    return name


@router.post("/use")
def use(body: dict):
    """"Use this one": the pipeline finds players with this model from the next run on."""
    name = model_or_400(body.get("name") if isinstance(body, dict) else "")
    try:
        dash_common.save_settings({"detector_model": name})
    except ValueError as problem:
        raise HTTPException(400, str(problem))
    except (OSError, SystemExit):
        raise HTTPException(503, "Could not write the settings file.")
    return {"detector_model": name}


@router.post("/try")
def try_on_clip(body: dict):
    """"Try on a clip": run the model over the test clip as a job and record its speed in the model's card."""
    name = model_or_400(body.get("name") if isinstance(body, dict) else "")
    if not dash_common.is_clip_video(TEST_CLIP):
        raise HTTPException(400, f"The test clip {TEST_CLIP} is not in the clips folder, so there is nothing to try the model on.")
    try:
        job = dash_common.start_job("try_model", TEST_CLIP, ["--try-model", name])
    except ValueError as problem:
        raise HTTPException(409, str(problem))
    return {"name": job}


@router.post("/send")
def send(body: dict):
    """"1 · Send dataset to Drive": body {"version": 3, "base": "fresh" or "<model>.pt", "length": "normal" or "long"}."""
    body = body if isinstance(body, dict) else {}
    try:
        number = int(body.get("version"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Choose a dataset version first.")
    version = next((info for info in tv.list_versions() if info["number"] == number and info["has_zip"]), None)
    if version is None:
        raise HTTPException(400, f"Version {number} has no zip. Build it on the Dataset screen first.")
    base = str(body.get("base") or "fresh")
    if base != "fresh":
        base = model_or_400(base)
    length = str(body.get("length") or "normal")
    if length not in LENGTHS:
        raise HTTPException(400, "The length must be normal or long.")
    drive = drive_status()
    if not drive["connected"]:
        return {"sent": False, "message": drive["message"], "zip_path": windows_name(version["zip_path"]),
                "zip_folder": windows_name(Path(version["zip_path"]).parent), "drive_folder": "Hoops Tracker/training"}
    if sending["running"]:
        raise HTTPException(409, "A copy to Drive is going already. Wait for it to finish.")
    sending["running"] = True
    threading.Thread(target=run_send, args=(version, base, length, drive["path"]), daemon=True).start()
    return {"sent": True, "started": True}


@router.get("/send")
def send_progress():
    return sending


@router.post("/fetch")
def fetch(body: dict):
    """"Bring the model back": copy <drive>/models/<name>.pt and its .json into the laptop's models folder."""
    name = str((body or {}).get("name") or "").strip()
    drive = drive_status()
    if not drive["connected"]:
        raise HTTPException(400, drive["message"])
    source = Path(drive["path"]) / "models" / name
    if "/" in name or "\\" in name or not name.endswith(".pt") or not source.is_file():
        raise HTTPException(400, f"There is no model called \"{name}\" in the Drive folder's models.")
    target = tv.models_dir() / name
    if target.exists():
        raise HTTPException(409, f"{name} is already in the models folder.")
    state = {}
    try:
        copy_with_progress(source, target, state)
        if source.with_suffix(".json").is_file():
            copy_with_progress(source.with_suffix(".json"), target.with_suffix(".json"), state)
    except OSError as problem:
        raise HTTPException(503, str(problem))
    return models_reply()


@router.get("/notebook")
def notebook():
    if not NOTEBOOK.is_file():
        raise HTTPException(404, "The training notebook is missing from the colab folder.")
    return FileResponse(NOTEBOOK, media_type="application/x-ipynb+json", filename=NOTEBOOK.name)
