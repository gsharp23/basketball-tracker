"""Score this version: run a trained model on the pictures a dataset version kept aside for testing, and store
the result with the version (mAP50 overall and per class) so the Dashboard chart and the versions list can show
it. Scores are honest only when the version kept a whole game aside (its "test_game").

    python src/model_score.py --video clips/test.mp4 --score-version 5 --model detector_v5.pt
        (the --video is the job framework's; any clip in the clips folder does. The dashboard runs this as the
         "score_model" job, one model at a time on this laptop.)

Writes into data/_training/versions/v<n>/version.json:
    "score": {"map50": 0.58, "per_class": {"Ball": 0.41, ...}, "scored": "2026-10-18", "model": "detector_v5.pt",
              "test_pictures": 150, "test_game": "game2" or null, "picture_size": 1280}
and "map50" into models/<model>.json (the model's card), so the detector box can say "score 0.58".
mAP50 is the usual detection score: how well the model's boxes match the marked ones at half overlap, 1.0 is perfect.
"""
import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("YOLO_OFFLINE", "1")
os.environ.setdefault("ULTRALYTICS_SAFE_LOAD", "1")

import config
import detect_track
import training_versions as tv


def score(number, model_name):
    folder = tv.versions_dir() / f"v{number}"
    info = tv.read_json(folder / "version.json", {})
    zip_path = folder / "dataset.zip"
    if not zip_path.is_file():
        sys.exit(f"Version {number} has no dataset.zip (build it on the Dataset screen first).")
    model_path = tv.models_dir() / model_name
    if "/" in model_name or not model_path.is_file():
        sys.exit(f"There is no model called {model_name} in the models folder.")
    size = detect_track.picture_size_of(model_path)
    print(f"Scoring {model_name} on the test pictures of version {number} (pictures {size} wide)...", flush=True)
    work = Path(tempfile.mkdtemp(prefix="hoops_score_"))
    try:
        with zipfile.ZipFile(zip_path) as archive:
            wanted = [name for name in archive.namelist()
                      if name == "data.yaml" or name.startswith("images/test/") or name.startswith("labels/test/")]
            for name in wanted:
                archive.extract(name, work)
        pictures = sum(1 for name in wanted if name.startswith("images/test/"))
        if not pictures:
            sys.exit(f"Version {number} has no test pictures to score on.")
        print(f"  {pictures} test pictures" + (f", all from {info.get('test_game')} (a game the model never saw)" if info.get("test_game") else
                                             " (cut out of the same games the model trained on: a weaker test)"), flush=True)
        import fcntl
        lock = open("/tmp/hoops-heavy.lock", "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        from ultralytics import YOLO
        model = YOLO(str(model_path))
        result = model.val(data=str(work / "data.yaml"), split="test", imgsz=size, device=config.device(), verbose=False,
                           plots=False, project=str(work / "runs"), name="score", exist_ok=True)
        names = result.names if isinstance(result.names, dict) else dict(enumerate(result.names))
        per_class = {}
        for index, ap in zip(list(result.box.ap_class_index), list(result.box.ap50)):
            per_class[str(names.get(int(index), index))] = round(float(ap), 3)
        for name in names.values():
            per_class.setdefault(str(name), 0.0)              # a class with no test box scores nothing
        map50 = round(float(result.box.map50), 3)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    scored = {"map50": map50, "per_class": per_class, "scored": datetime.date.today().isoformat(), "model": model_name,
              "test_pictures": pictures, "test_game": info.get("test_game"), "picture_size": size}
    info["score"] = scored
    config.save_json(folder / "version.json", info, indent=2)
    card_path = model_path.with_suffix(".json")
    card = tv.read_json(card_path, {})
    card = card if isinstance(card, dict) else {}
    card["map50"] = map50
    card["scored_on_version"] = number
    config.save_json(card_path, card, indent=2)
    words = ", ".join(f"{name} {value:.2f}" for name, value in per_class.items())
    print(f"  Score {map50:.2f} ({words}). Written into version {number} and the model's card.", flush=True)
    print("=== Scoring is done", flush=True)
    return scored


def parse_args():
    parser = argparse.ArgumentParser(description="Score a model on a dataset version's test pictures.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="any clip (the job framework needs one)")
    parser.add_argument("--score-version", type=int, required=True, help="the dataset version whose test pictures to use")
    parser.add_argument("--model", required=True, help="the model file in models/, like detector_v5.pt")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    score(args.score_version, args.model)
