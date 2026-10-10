"""Shared paths, constants and small helpers for the basketball tracker.

Every stage imports this instead of hard-coding paths:

    import config
    tracks = config.load_json(config.data_dir(video) / "tracks.json")

What is in here
    PROJECT_ROOT, CLIPS_DIR, MODELS_DIR, DATA_DIR, OUTPUTS_DIR, CROPS_DIR   folders
    ROSTER_PATH, DEFAULT_VIDEO                                              files
    PLAYER_MODEL, YOLOV8N_MODEL          model weight files
    CLASS_NAMES                          class numbers of the player model
    FRAME_STRIDE                         look at every 3rd frame by default
    COURT_LENGTH_FT, COURT_WIDTH_FT      court size in feet (change for your gym)

    video_path(video)     full path of a video given as "clips/test.mp4"
    require_video(video)  the same, but stop with a plain message if the file is not there
    video_stem(video)     "clips/test.mp4" -> "test"
    data_dir(video)       data/<stem>/     (created if missing)
    output_dir(video)     outputs/<stem>/  (created if missing)
    lock_video(video)     stop if another program is already working on this video
    load_json(path)       read a JSON file; stop with a plain message if it is damaged
    save_json(path, obj)  write a JSON file safely and check it by reading it back
    require_file(path, command)   stop with a plain message if an input is missing
    ffmpeg_path()         the ffmpeg program that comes with imageio-ffmpeg
    device()              "cpu" on the laptop, "cuda:0" on a computer with a graphics card (Colab)

The big folders (clips, models, data, outputs, crops) are links to the data folder (see README.md).
"""
import fcntl
import json
import os
import sys
from pathlib import Path

# The repo folder, worked out from where this file is, so scripts work from any folder.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

CLIPS_DIR = PROJECT_ROOT / "clips"
MODELS_DIR = PROJECT_ROOT / "models"
DATA_DIR = PROJECT_ROOT / "data"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
CROPS_DIR = PROJECT_ROOT / "crops"

ROSTER_PATH = PROJECT_ROOT / "roster.json"
DEFAULT_VIDEO = "clips/test.mp4"

# Basketball-trained model: finds players, referees, hoop (and, unreliably, the ball).
PLAYER_MODEL = MODELS_DIR / "player_detector.pt"
# Official small model: fast, but only knows "person".
YOLOV8N_MODEL = MODELS_DIR / "yolov8n.pt"

# Class numbers used by player_detector.pt.
CLASS_NAMES = {0: "Ball", 1: "Clock", 2: "Hoop", 3: "Overlay", 4: "Player", 5: "Ref", 6: "Scoreboard"}

# Look at every 3rd frame (about 10 per second). Every frame would take 3x as long.
FRAME_STRIDE = 3

# US high school court. Change these two numbers if your court is a different size.
COURT_LENGTH_FT = 84
COURT_WIDTH_FT = 50

# Shown whenever one of the linked folders cannot be reached.
SD_CARD_HELP = ("That folder is a link to the data folder (ls -l shows where it points), and the folder\n"
                "it points to cannot be reached. If the data was moved, make the links again: see\n"
                "\"If a data folder cannot be reached\" in README.md. Data on a removable drive (D:)\n"
                "has to be attached first:  sudo mount -t drvfs D: /mnt/d")

# The video locks this program holds (see lock_video).
_video_locks = {}


def video_path(video):
    """Full path of a video. "clips/test.mp4" means that file inside the project."""
    path = Path(video)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def require_video(video):
    """Full path of a video. Stops with a plain message (not a traceback) if the file is not there."""
    path = video_path(video)
    if not path.parent.is_dir():
        sys.exit(f"Cannot reach the folder {path.parent}\n{SD_CARD_HELP}")
    if not path.exists():
        sys.exit(f"Cannot find the video {path}\nCheck the file name (the videos are in the clips folder).")
    return path


def video_stem(video):
    """File name without folder or extension: "clips/test.mp4" -> "test"."""
    return Path(video).stem


def card_folder(top, video):
    """The folder <top>/<stem>/ for one video, created if missing.

    top itself (data, outputs) is a link to the data folder and is never created here:
    if it cannot be reached the data folder is missing, and we say so.
    """
    if not top.is_dir():
        sys.exit(f"Cannot reach the folder {top}\n{SD_CARD_HELP}")
    folder = top / video_stem(video)
    folder.mkdir(exist_ok=True)
    return folder


def data_dir(video):
    """Folder for this video's data files (data/<stem>/). Created if missing."""
    return card_folder(DATA_DIR, video)


def crops_dir(video):
    """Folder for this video's jersey check pictures (crops/<stem>/). Created if missing."""
    return card_folder(CROPS_DIR, video)


def output_dir(video):
    """Folder for this video's output videos and pictures (outputs/<stem>/). Created if missing."""
    return card_folder(OUTPUTS_DIR, video)


def lock_video(video):
    """Stop if another program is already working on this video.

    Two long jobs on one video would overwrite each other's saved progress, and the
    laptop does not have the memory for two models. The lock is an empty file in /tmp
    that this program holds on to; Linux lets go of it when the program ends, however
    it ends. Asking twice in one program is harmless.
    """
    stem = video_stem(video)
    if stem in _video_locks:
        return
    handle = open(f"/tmp/hoops-video-{stem}.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)      # NB = do not wait for it
    except OSError:
        sys.exit(f"Another run on {video} is already going, probably in another terminal.\n"
                 "Let it finish (or stop it there with Ctrl+C), then run this again.")
    _video_locks[stem] = handle


def load_json(path):
    """Read a JSON file and return its contents.

    Stops with a plain message if the file is damaged (cut short or full of rubbish),
    which a failing SD card did to files before.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except ValueError:
        sys.exit(f"{path} is damaged and cannot be read.\n"
                 "Delete that file, then run  python src/main.py --video <your video>  again:\n"
                 "it makes the file again, or tells you which step to redo.")


def save_json(path, obj, indent=None):
    """Write obj to a JSON file safely.

    A failing SD card corrupted files before, so: write to a temporary file first,
    rename it into place (an interrupted write can never leave a half-written
    file under the real name), then read it back and check it is what we wrote.
    Use indent=2 for small files a person may want to read.
    """
    path = Path(path)
    text = json.dumps(obj, indent=indent)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    with open(path, "r", encoding="utf-8") as f:
        if f.read() != text:
            raise IOError(f"{path} did not read back the same as it was written. "
                          "The disk may be failing; run the command again.")


def require_file(path, command):
    """Stop with a plain message (not a traceback) if an input file is missing.

    command is the earlier step that makes the file, for example
    "python src/detect_track.py --video clips/test.mp4".
    """
    if not Path(path).exists():
        sys.exit(f"Cannot find {path}\nRun this first:  {command}")


def device():
    """Where the two models run: "cpu" on the laptop, "cuda:0" on a computer with a graphics card.

    The environment variable HOOPS_DEVICE decides: "auto" (the default) takes the graphics
    card when torch can see one, else the CPU; "cpu" forces the CPU even when there is a
    graphics card. On the laptop there is no graphics card, so nothing changes there.
    """
    wanted = os.environ.get("HOOPS_DEVICE", "").strip().lower() or "auto"
    if wanted != "auto":
        return wanted
    import torch
    return "cuda:0" if torch.cuda.is_available() else "cpu"


def ffmpeg_path():
    """Path of the ffmpeg program bundled with imageio-ffmpeg (there is no system ffmpeg)."""
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()
