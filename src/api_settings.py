"""The Settings screen's own API (web/settings.html + settings.js).

    PUT  /api/settings/school      body {"school_name", "mascot"} -> saved in the branding folder's branding.json
    PUT  /api/settings/logo?name=logo.png   the picture itself as the body (PNG, JPG or SVG, up to 5 MB)
                                   -> saved into the branding folder and named in branding.json
    GET  /api/settings/check       "Check everything": a list of {"name", "ok", "text"}
    GET  /api/settings/notebook    the Colab notebook as a download

The shared settings (court size, where to run, Google Drive, roster) are in dash_api.py.
"""
import importlib.util
import platform
import sys
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import config
import dash_common

router = APIRouter(prefix="/api/settings")

NOTEBOOK = config.PROJECT_ROOT / "colab" / "train_detector.ipynb"
PICTURE_TYPES = {".png": "logo.png", ".jpg": "logo.jpg", ".jpeg": "logo.jpg", ".svg": "logo.svg"}
MAX_LOGO_BYTES = 5_000_000


@router.put("/school")
def save_school(body: dict):
    try:
        dash_common.save_branding(body)
    except OSError:
        raise HTTPException(503, "Could not write to the branding folder.")
    brand = dash_common.load_branding()
    return {"school_name": brand["school_name"], "mascot": brand["mascot"]}


@router.put("/logo")
async def save_logo(request: Request, name: str = "logo.png"):
    """Keep the dropped picture as the school logo. The picture goes to the branding folder on this laptop, never anywhere else."""
    suffix = Path(name).suffix.lower()
    if suffix not in PICTURE_TYPES:
        raise HTTPException(400, "The logo must be a PNG, JPG or SVG picture.")
    data = await request.body()
    if not data:
        raise HTTPException(400, "That file is empty.")
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(400, "That picture is too big: use one under 5 MB.")
    if not looks_like_picture(data, suffix):
        raise HTTPException(400, "That file is not a PNG, JPG or SVG picture inside. Try another file.")
    target = dash_common.BRANDING_DIR / PICTURE_TYPES[suffix]
    try:
        dash_common.BRANDING_DIR.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        temp.write_bytes(data)
        if target.is_file():            # keep the old logo as logo_previous.<ext>, in case of a wrong drop
            shutil.copyfile(target, target.with_name(target.stem + "_previous" + target.suffix))
        temp.replace(target)            # never a half-written logo under the real name
        said = dash_common.read_json(dash_common.BRANDING_DIR / "branding.json", {})
        said = said if isinstance(said, dict) else {}
        said["logo"] = target.name
        config.save_json(dash_common.BRANDING_DIR / "branding.json", said, indent=2)
    except OSError:
        raise HTTPException(503, "Could not write to the branding folder.")
    return {"logo_url": f"/files/branding/{target.name}"}


def looks_like_picture(data, suffix):
    """True when the first bytes are what a PNG, JPEG or SVG file starts with."""
    if suffix == ".png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if suffix in (".jpg", ".jpeg"):
        return data[:2] == b"\xff\xd8"
    return data.lstrip()[:1] == b"<"        # .svg: text starting with a tag


def check(name, ok, text):
    return {"name": name, "ok": bool(ok), "text": text}


@router.get("/check")
def check_everything():
    """Look at everything the tool needs and say, in plain words, what is fine and what is not."""
    results = []
    folders = {"clips": config.CLIPS_DIR, "models": config.MODELS_DIR, "data": config.DATA_DIR,
               "outputs": config.OUTPUTS_DIR, "crops": config.CROPS_DIR}
    missing = [name for name, folder in folders.items() if not folder.is_dir()]
    storage = dash_common.storage_info()
    if missing:
        results.append(check("Data folders", False, f"Cannot reach the folders: {', '.join(missing)}. "
                             "See \"If a data folder cannot be reached\" in README.md."))
    else:
        results.append(check("Data folders", True, f"All five folders can be reached, in {storage['folder']}."))

    videos = dash_common.video_files()
    results.append(check("Videos", bool(videos), f"{len(videos)} video file(s) in the clips folder."
                         if videos else "No videos in the clips folder yet."))

    models = [("Player finder", config.PLAYER_MODEL), ("Small general model", config.YOLOV8N_MODEL),
              ("Ball finder (optional)", config.MODELS_DIR / "ball_detector_model.pt")]
    for label, path in models:
        size = f"{path.stat().st_size / 1e6:.0f} MB" if path.is_file() else ""
        results.append(check(label, path.is_file() or "optional" in label,
                             f"{path.name}: {size}" if path.is_file() else f"{path.name} is missing from the models folder."))
    reader = config.MODELS_DIR / "easyocr"
    results.append(check("Number reader", reader.is_dir(), "Its files are in the models folder."
                         if reader.is_dir() else "Its files (models/easyocr) are missing; the first run would download them."))

    packages = ["torch", "ultralytics", "easyocr", "cv2", "numpy", "pandas", "fastapi", "uvicorn", "imageio_ffmpeg"]
    absent = [name for name in packages if importlib.util.find_spec(name) is None]
    results.append(check("Environment", not absent,
                         f"Python {platform.python_version()} at {sys.prefix}" if not absent
                         else f"Missing from the environment: {', '.join(absent)}. See \"Reinstall from scratch\" in README.md."))
    try:
        ffmpeg = Path(config.ffmpeg_path())
        results.append(check("Video tool (ffmpeg)", ffmpeg.is_file(), ffmpeg.name if ffmpeg.is_file() else "Not found."))
    except Exception as problem:            # imageio_ffmpeg can raise its own kinds of error
        results.append(check("Video tool (ffmpeg)", False, f"Not found: {problem}"))

    pages = [kind["script"] for name, kind in dash_common.JOB_KINDS.items() if kind["name"] == "click_page"]
    lost = [script for script in pages if not (config.PROJECT_ROOT / "src" / script).is_file()]
    open_page = dash_common.job_running("click_page")
    results.append(check("Click pages (mark the court)", not lost,
                         ("One is open now. " if open_page else "") + "All three are in place."
                         if not lost else f"Missing: {', '.join(lost)}."))

    brand = dash_common.load_branding()
    results.append(check("School branding", bool(brand["school_name"] or brand["logo"]),
                         "Name and logo found in the branding folder." if brand["logo"] and brand["school_name"]
                         else "No branding folder yet: the pages show \"Hoops Tracker\". Add the logo and name above."))
    roster = dash_common.load_roster()
    named = sum(len(team["players"]) for team in roster.values())
    results.append(check("Roster", True, f"{named} player(s) with a number. Names never leave this laptop."))
    busy = dash_common.what_is_running()
    results.append(check("Processing", True, f"The laptop is busy with {busy}." if busy else "Nothing is being processed now."))
    return {"ok": all(item["ok"] for item in results), "checks": results}


@router.get("/notebook")
def notebook():
    if not NOTEBOOK.is_file():
        raise HTTPException(404, "The Colab notebook is missing from the colab folder.")
    return FileResponse(NOTEBOOK, media_type="application/x-ipynb+json", filename=NOTEBOOK.name)
