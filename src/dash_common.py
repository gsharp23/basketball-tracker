"""Helpers shared by the dashboard (dashboard.py) and its pages (dash_stats.py,
dash_shots.py, dash_train.py).

A page module starts with
    import dash_common
    from dash_common import gr          # Gradio, with the settings below already in place

What is in here
    gr                          Gradio itself: import it from here, never with "import gradio"
    DASH_DIR, work_dir(name)    data/_dashboard/ and a named working folder inside it
    SERVED_DIRS, NOT_SERVED     the SD-card folders the browser may be shown files from, and the one kept back
    sd_card_ok()                are the linked SD-card folders reachable?

    load_branding()             school name, mascot, logo and artwork from the branding folder
    brand_colours(logo)         the logo's main colours, for theming
    make_theme(), make_css(colours), picture_data(path), logo_data(path)     the look of the pages
    note(text), warning(text)   small boxes of plain text for a gr.HTML
    CSS class "hoops-big"       for large buttons: gr.Button("...", variant="primary", elem_classes="hoops-big")

    video_files(), list_games() the videos in clips/, and the status of each stage for each
    stand_in_court(video)       was this game's court marked with stand-in clicks, not by the coach?
    game_choices(), chosen_game(), remember_game(video)       the "Game" box at the top of a page

    start_job(name, command)    run a long command in the background
    follow_job(name)            its printed lines, as they come, for live progress
    job_running(name), job_exit_code(name), stop_job(name), stop_all_jobs()
    running_videos(), is_running(video), what_is_running()    is another run going?

Privacy: the school's name, logo and colours are read from the branding folder on the
SD card while the program runs. They must never be written into this code.
"""
import base64
import html
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

import config
import court_mapper
import main         # for its own "is this stage done" checks; importing it runs nothing

# The dashboard's own working folders. A folder in data/ whose name starts with "_" is not a game.
DASH_DIR = config.DATA_DIR / "_dashboard"

# Gradio reads these two settings while it is being imported, so they are set first.
# No usage reports are sent, and the pictures it keeps for the browser go to the SD card,
# not to C: (the pictures show children, and C: is nearly full).
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
if config.DATA_DIR.is_dir():
    (DASH_DIR / "tmp").mkdir(parents=True, exist_ok=True)
    os.environ["GRADIO_TEMP_DIR"] = str((DASH_DIR / "tmp").resolve())
import gradio as gr  # noqa: E402  (must come after the settings above)

# The browser may only be shown files from these two folders: the result videos and check
# pictures (outputs/) and the game videos (clips/). Pictures a page draws itself go through
# Gradio's own temp folder, which is always allowed. Nothing in data/, crops/ or models/ is served.
SERVED_DIRS = [config.OUTPUTS_DIR, config.CLIPS_DIR]
# Kept back inside outputs/: the dashboard's own folder (test pictures of the pages are kept there,
# and no page shows them). It is named both through the project's link and as it is on the SD
# card, so that either address is refused. The list is kept this short on purpose: Gradio checks
# every file it sends against each entry, and each check costs time on the SD card.
NOT_SERVED = [config.OUTPUTS_DIR / "_dashboard", (config.OUTPUTS_DIR / "_dashboard").resolve()]

VIDEO_TYPES = (".mp4", ".mov", ".m4v", ".avi", ".mkv")


def sd_card_ok():
    """True when all five linked SD-card folders can be reached."""
    folders = [config.CLIPS_DIR, config.MODELS_DIR, config.DATA_DIR, config.OUTPUTS_DIR, config.CROPS_DIR]
    return all(folder.is_dir() for folder in folders)


def work_dir(name):
    """The dashboard working folder data/_dashboard/<name>/, created if missing."""
    folder = DASH_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ---------------------------------------------------------------- branding

# Used when there is no branding folder, or no logo in it.
NEUTRAL_COLOURS = {"main": "#1c2530", "accent": "#e8590c", "plate": "#ffffff"}


def load_branding(folder=None):
    """What the branding folder says about the school. Never fails.

    Returns {"school_name": text, "mascot": text, "logo": Path or None, "hero": Path or None}.
    Anything missing or unreadable comes back empty, and the pages then show a neutral title.
    The folder is data/_dashboard/branding/ unless another one is given here or in the
    environment variable HOOPS_BRANDING_DIR (used for testing).
    """
    folder = Path(folder or os.environ.get("HOOPS_BRANDING_DIR") or DASH_DIR / "branding")
    try:
        with open(folder / "branding.json", "r", encoding="utf-8") as f:
            said = json.load(f)
    except (OSError, ValueError):
        said = {}
    if not isinstance(said, dict):
        said = {}
    brand = {"school_name": str(said.get("school_name") or ""), "mascot": str(said.get("mascot") or "")}
    for key, usual_name in (("logo", "logo.png"), ("hero", "hero.svg")):
        path = folder / str(said.get(key) or usual_name)
        brand[key] = path if path.is_file() else None
    return brand


def hex_colour(bgr):
    """An OpenCV colour (blue, green, red) as the "#rrggbb" text CSS wants."""
    blue, green, red = (int(round(float(value))) for value in bgr)
    return f"#{red:02x}{green:02x}{blue:02x}"


def brand_colours(logo_path):
    """The logo's main colours, read from the picture, for theming the pages.

    Returns {"main": a dark colour for the title bands, "accent": the strongest bright
    colour for buttons, "plate": the logo's own background, to put behind it}, each as
    "#rrggbb". Falls back to NEUTRAL_COLOURS for whatever cannot be found.
    """
    colours = dict(NEUTRAL_COLOURS)
    picture = cv2.imread(str(logo_path), cv2.IMREAD_UNCHANGED) if logo_path else None
    if picture is None:
        return colours
    if picture.ndim == 2:
        picture = cv2.cvtColor(picture, cv2.COLOR_GRAY2BGR)
    pixels = picture[:, :, :3].reshape(-1, 3).astype(int)
    solid = picture[:, :, 3].reshape(-1) >= 128 if picture.shape[2] == 4 else np.ones(len(pixels), bool)

    # The logo's background: the colour of its top-left corner, unless that is see-through.
    if solid[0]:
        colours["plate"] = hex_colour(pixels[0])
        background = pixels[0]
    else:
        background = np.array([255, 255, 255])
    ink = pixels[solid & (np.abs(pixels - background).sum(axis=1) > 60)]     # everything that is not background
    if len(ink) < 50:
        return colours

    # Sort the pixels into coarse groups of similar colour, most common first.
    groups, counts = np.unique(ink // 32, axis=0, return_counts=True)
    order = np.argsort(-counts)
    groups, counts = groups[order] * 32 + 16, counts[order]
    brightness = groups.max(axis=1)
    strength = groups.max(axis=1) - groups.min(axis=1)       # 0 for grey, large for a pure colour

    def group_mean(index):
        """The real average colour of the pixels in one group."""
        return ink[(ink // 32 == groups[index] // 32).all(axis=1)].mean(axis=0)

    enough = counts >= 0.01 * len(ink)                       # ignore the thin blended edges
    dark = [i for i in range(len(groups)) if enough[i] and brightness[i] < 120]
    dark_colour = group_mean(dark[0]) if dark else None
    strong = [i for i in range(len(groups)) if enough[i] and strength[i] >= 96 and brightness[i] >= 120]
    bright_colour = group_mean(strong[0]) if strong else None
    if dark_colour is None and bright_colour is not None:
        dark_colour = bright_colour * 0.3                    # no dark colour in the logo: a dark shade of its colour
    if dark_colour is not None:
        colours["main"] = hex_colour(dark_colour)
    if bright_colour is not None:
        colours["accent"] = hex_colour(bright_colour)
    elif dark_colour is not None and dark_colour.max() - dark_colour.min() >= 60:
        # a logo in one dark colour: a lighter shade of it
        colours["accent"] = hex_colour(np.minimum(dark_colour * 1.8 + 40, 255))
    return colours


def text_on(colour):
    """White or near-black, whichever reads better on the given "#rrggbb" colour."""
    red, green, blue = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#111111" if 0.299 * red + 0.587 * green + 0.114 * blue > 150 else "#ffffff"


def picture_data(path):
    """A small picture file as text that can be written straight into the page's HTML.

    That way the logo and artwork need no file address, and nothing else is served.
    """
    kinds = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}
    kind = kinds.get(Path(path).suffix.lower(), "image/png")
    with open(path, "rb") as f:
        return f"data:{kind};base64," + base64.b64encode(f.read()).decode("ascii")


def logo_data(path):
    """The logo for the page (like picture_data), with its empty border cut off.

    A logo file often has a wide plain margin, which would make the logo itself tiny on
    the page. What counts as "drawn on" is anything unlike the top-left corner, or
    anything solid when that corner is see-through.
    """
    picture = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if picture is None or picture.ndim != 3:
        return picture_data(path)
    if picture.shape[2] == 4 and picture[0, 0, 3] < 128:
        drawn = picture[:, :, 3] >= 128
    else:
        drawn = np.abs(picture[:, :, :3].astype(int) - picture[0, 0, :3].astype(int)).sum(axis=2) > 60
    rows, columns = np.nonzero(drawn)
    if len(rows) == 0:
        return picture_data(path)
    margin = (rows.max() - rows.min()) // 8 + 2         # a little air around it
    trimmed = picture[max(rows.min() - margin, 0):rows.max() + margin + 1,
                      max(columns.min() - margin, 0):columns.max() + margin + 1]
    ok, png = cv2.imencode(".png", trimmed)
    return "data:image/png;base64," + base64.b64encode(png.tobytes()).decode("ascii") if ok else picture_data(path)


# ---------------------------------------------------------------- the look of the pages

def make_theme():
    """The Gradio theme: plain system fonts, and the same look in light and dark mode."""
    # The default theme would make the browser fetch fonts from Google; these are on the laptop.
    theme = gr.themes.Default(font=["Segoe UI", "Arial", "sans-serif"], font_mono=["Consolas", "monospace"])
    # Gradio switches to dark colours when Windows is in dark mode. The pages are designed
    # around the logo's colours, so every "dark" setting is given its light value.
    dark_names = [name for name in dir(theme) if name.endswith("_dark") and not name.startswith("_")]
    return theme.set(**{name: getattr(theme, name[:-len("_dark")]) for name in dark_names})


# All the dashboard's own styling, in one place. MAIN, ACCENT, ON_ACCENT and PLATE are
# replaced with the logo's colours by make_css(). Class names start with "hoops-".
CSS = """
:root, .dark {
    --hoops-main: MAIN; --hoops-accent: ACCENT; --hoops-on-accent: ON_ACCENT; --hoops-plate: PLATE;
    --hoops-sporty: Impact, "Arial Narrow Bold", "Franklin Gothic Bold", "Arial Black", sans-serif;
}
/* Gradio's own colours for buttons, ticks and the chosen tab follow the logo too. */
.gradio-container {
    --button-primary-background-fill: var(--hoops-accent);
    --button-primary-background-fill-hover: var(--hoops-main);
    --button-primary-text-color: var(--hoops-on-accent);
    --button-primary-text-color-hover: #ffffff;
    --button-primary-border-color: var(--hoops-accent);
    --color-accent: var(--hoops-accent);
    --checkbox-background-color-selected: var(--hoops-accent);
    --checkbox-border-color-selected: var(--hoops-accent);
    --slider-color: var(--hoops-accent);
    --loader-color: var(--hoops-accent);
    --body-text-size: 16px;
    padding: 0 !important; max-width: none !important; background: #f2f3f5 !important;
}
/* Gradio's own menu of pages. (gr.Navbar(visible=False) does not hide it in Gradio 6.29.) */
.nav-holder { display: none !important; }
.gradio-container .main, .gradio-container .wrap, .gradio-container main.contain { padding: 0 !important; }
.gradio-container main.contain > .column { gap: 0 !important; }
.gradio-container .html-container { padding: 0 !important; }      /* our own HTML sets its own spacing */

/* ---- the start page ---- */
.gradio-container:has(.hoops-landing) { background: var(--hoops-main) !important; }
.hoops-landing {
    position: relative; overflow: clip; color: #ffffff; text-align: center;
    padding: 28px 32px 64px 32px; min-height: 100vh; box-sizing: border-box;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    /* a soft light behind the artwork, so it does not float on a flat colour */
    background: radial-gradient(ellipse 55% 42% at 50% 34%, rgba(255, 255, 255, 0.13), rgba(255, 255, 255, 0) 72%);
}
/* two slanted stripes in the accent colour behind everything */
.hoops-landing::before, .hoops-landing::after {
    content: ""; position: absolute; top: -20%; height: 140%; transform: skewX(-18deg); z-index: 0;
}
.hoops-landing::before { left: 9%; width: 54px; background: var(--hoops-accent); opacity: 0.95; }
.hoops-landing::after { left: calc(9% + 74px); width: 14px; background: #ffffff; opacity: 0.85; }
.hoops-landing > * { position: relative; z-index: 1; }
.hoops-logo-plate {
    display: inline-block; background: var(--hoops-plate); border-radius: 12px; padding: 6px 12px; line-height: 0;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.35);
}
.hoops-landing .hoops-logo-corner { position: absolute; top: 22px; right: 28px; padding: 4px 6px; }
.hoops-landing .hoops-logo-corner img { height: 72px; }
.hoops-landing .hoops-logo-centre img { height: 150px; max-width: 80vw; }
.hoops-hero { height: 37vh; min-height: 200px; max-width: 80vw; filter: drop-shadow(0 10px 24px rgba(0, 0, 0, 0.45)); }
.hoops-landing h1 {
    font-family: var(--hoops-sporty); font-style: italic; font-weight: 900; text-transform: uppercase;
    font-size: clamp(44px, 6.5vw, 92px); line-height: 0.95; letter-spacing: 1px; margin: 18px 0 6px 0;
    color: #ffffff !important; text-shadow: 4px 4px 0 var(--hoops-accent);
}
.hoops-landing .hoops-under-title {
    font-size: 20px; letter-spacing: 4px; text-transform: uppercase; font-weight: 600; margin: 0 0 30px 0;
    color: #ffffff !important; opacity: 0.9;
}
.hoops-choices { display: flex; gap: 28px; justify-content: center; flex-wrap: wrap; width: 100%; }
a.hoops-choice {
    display: block; width: min(420px, 90vw); padding: 26px 30px 24px 30px; text-align: left; text-decoration: none;
    background: #ffffff; color: #111111 !important; border-radius: 14px; border-bottom: 8px solid var(--hoops-accent);
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35); transition: transform 0.12s, background 0.12s;
}
a.hoops-choice:hover, a.hoops-choice:focus {
    transform: translateY(-4px); background: var(--hoops-accent); color: var(--hoops-on-accent) !important;
}
a.hoops-choice .hoops-choice-title {
    font-family: var(--hoops-sporty); font-style: italic; text-transform: uppercase; font-size: 38px; line-height: 1.05;
    display: flex; justify-content: space-between; align-items: center;
}
a.hoops-choice .hoops-choice-title::after {      /* the arrow */
    content: "\\2192"; font-style: normal; font-family: Arial, sans-serif; color: var(--hoops-accent);
}
a.hoops-choice:hover .hoops-choice-title::after, a.hoops-choice:focus .hoops-choice-title::after {
    color: var(--hoops-on-accent);
}
a.hoops-choice .hoops-choice-text { font-size: 17px; margin-top: 8px; opacity: 0.85; }

/* ---- the top of an area page ---- */
.hoops-band {
    background: var(--hoops-main); padding: 14px 32px; display: flex; align-items: center; gap: 20px;
    border-bottom: 6px solid var(--hoops-accent);
}
.hoops-band img { height: 56px; }
.hoops-band .hoops-logo-plate { padding: 2px 4px; border-radius: 8px; box-shadow: none; }
.hoops-band .hoops-band-title {
    font-family: var(--hoops-sporty); font-style: italic; text-transform: uppercase; font-size: 34px; line-height: 1; flex: 1;
    color: #ffffff !important;
}
.hoops-band .hoops-band-small {
    font-family: "Segoe UI", Arial, sans-serif; font-style: normal; font-weight: 600; font-size: 14px; letter-spacing: 2px;
    color: #ffffff !important; opacity: 0.85; display: block; margin-bottom: 5px;
}
a.hoops-back {
    color: #ffffff !important; text-decoration: none !important; font-size: 18px; font-weight: 600; white-space: nowrap;
    border: 2px solid #ffffff; border-radius: 10px; padding: 10px 18px;
}
a.hoops-back:hover, a.hoops-back:focus { background: #ffffff; color: var(--hoops-main) !important; }
.hoops-body { padding: 18px 32px 8px 32px !important; max-width: 1400px; margin: 0 auto !important; gap: 14px !important; }
.form:has(> .hoops-game) { max-width: 520px; }
.hoops-body .tab-container button { font-size: 18px !important; font-weight: 600; padding: 12px 18px !important; }
/* Gradio's small grey labels carry real instructions here, so they are larger and darker. */
.hoops-body span[data-testid="block-info"], .hoops-body label[data-testid="block-label"] {
    font-size: 16px !important; color: #111111 !important; font-weight: 600;
}
.hoops-body input, .hoops-body textarea, .hoops-body label > span { font-size: 16px !important; }
.hoops-game input { font-size: 19px !important; font-weight: 700 !important; }
/* Gradio hides a video's play button and time bar until the mouse is over it; here they always show. */
#hoops-shots-video .controls { opacity: 1 !important; }

/* ---- pieces the pages use ---- */
.hoops-big { min-height: 64px !important; font-size: 21px !important; font-weight: 700 !important; }
/* tables in the ordinary font, not the typewriter one Gradio uses for them */
.table-wrap th, .table-wrap td, .table-wrap .virtual-row { font-family: var(--font) !important; font-size: 16px !important; }
.hoops-note, .hoops-warning { border-radius: 10px; padding: 14px 18px; font-size: 17px; line-height: 1.45; color: #111111; }
.hoops-note { background: #ffffff; border-left: 8px solid var(--hoops-main); }
.hoops-warning { background: #fff4d6; border-left: 8px solid #d98a00; }
.hoops-footer { text-align: center; font-size: 15px; color: #444444 !important; padding: 14px 32px 20px 32px; }
.hoops-footer b.hoops-good { color: #1b7a34 !important; }
.hoops-footer b.hoops-bad { color: #b3261e !important; }
/* on the start page the footer lies over the bottom of the dark picture */
.column:has(.hoops-landing) .hoops-footer {
    position: fixed; left: 0; right: 0; bottom: 0; color: #ffffff !important; z-index: 2;
}
.column:has(.hoops-landing) .hoops-footer b.hoops-good { color: #7be495 !important; }
.column:has(.hoops-landing) .hoops-footer b.hoops-bad { color: #ffb4a8 !important; }

/* ---- the "SD card not found" page ---- */
.hoops-problem {
    max-width: 820px; margin: 8vh auto; background: #ffffff; border-radius: 14px; padding: 30px 38px;
    font-size: 18px; line-height: 1.5; color: #111111; border-top: 10px solid #d98a00;
}
.hoops-problem h1 { font-size: 34px; margin: 0 0 12px 0; color: #111111; }
.hoops-problem code {
    display: block; background: #1c2530; color: #ffffff; padding: 14px 18px; border-radius: 8px; font-size: 18px; margin: 10px 0;
}
"""


def make_css(colours):
    """The dashboard's CSS with the given colours (from brand_colours) filled in."""
    css = CSS
    for word, value in (("ON_ACCENT", text_on(colours["accent"])), ("MAIN", colours["main"]),
                        ("ACCENT", colours["accent"]), ("PLATE", colours["plate"])):
        css = css.replace(word, value)
    return css


def note(text):
    """HTML for a plain box of guidance, for a gr.HTML. The text is shown as written, line by line."""
    return '<div class="hoops-note">' + html.escape(text).replace("\n", "<br>") + "</div>"


def warning(text):
    """HTML for a yellow warning box, for a gr.HTML."""
    return '<div class="hoops-warning">' + html.escape(text).replace("\n", "<br>") + "</div>"


# ---------------------------------------------------------------- the games

def video_files():
    """The video files in clips/, in name order (an empty list if the folder cannot be reached)."""
    if not config.CLIPS_DIR.is_dir():
        return []
    return sorted((path for path in config.CLIPS_DIR.iterdir()
                   if path.suffix.lower() in VIDEO_TYPES and path.is_file()), key=lambda path: path.name.lower())


_status_cache = {}      # video -> (what its files looked like, the stage statuses worked out from them)


def files_fingerprint(video):
    """Name, time and size of every file a game's status depends on. If this is unchanged, so is the status."""
    stem = config.video_stem(video)
    seen = []
    for folder in (config.DATA_DIR / stem, config.DATA_DIR / stem / "tracks_parts", config.OUTPUTS_DIR / stem):
        if folder.is_dir():
            for entry in sorted(os.scandir(folder), key=lambda e: e.name):
                info = entry.stat()
                seen.append((entry.path, info.st_mtime, info.st_size))
    for path in (config.video_path(video), config.ROSTER_PATH):
        if path.exists():
            seen.append((str(path), path.stat().st_mtime, path.stat().st_size))
    return seen


def stage_statuses(video):
    """{"detect": "done" | "half" | "todo", ...} for one video, as main.py itself judges it.

    main.py has to read a game's big result files to be sure, so the answer is kept until
    one of the game's files changes. A video nothing has been run on has no data folder
    yet, and none is made just by looking.
    """
    if not (config.DATA_DIR / config.video_stem(video)).is_dir():
        return {stage: "todo" for stage in main.STAGES}
    fingerprint = files_fingerprint(video)
    if video not in _status_cache or _status_cache[video][0] != fingerprint:
        try:
            stages = {stage: main.stage_status(video, stage) for stage in main.STAGES}
        except (SystemExit, OSError, KeyError, TypeError):
            # a damaged or half-written result file: the stage has to be looked at again
            stages = {stage: "unknown" for stage in main.STAGES}
        _status_cache[video] = (fingerprint, stages)
    return dict(_status_cache[video][1])


def list_games():
    """Every video in clips/, in name order, with what has been done for it.

    Each game is a dict:
        "video"         "clips/game1.mp4"   (what main.py --video and the click pages take)
        "name"          "game1"             (also the name of its folders in data/ and outputs/)
        "running"       True while main.py or one of the long stages is working on it
        "court_marked"  True when the reference pictures and the landmarks are both saved
        "stages"        {"detect": "done" | "half" | "todo" | "unknown", ...}; None while running,
                        because its files are changing then
        "status"        one of "not started", "running", "needs the court marked",
                        "stopped part-way", "finished"
    """
    games = []
    busy = running_videos()
    for path in video_files():
        video = f"clips/{path.name}"
        game = {"video": video, "name": path.stem, "running": path.stem in busy, "stages": None}
        has_folder = (config.DATA_DIR / path.stem).is_dir()
        game["court_marked"] = has_folder and not court_mapper.setup_missing(video)
        if game["running"]:
            game["status"] = "running"
        else:
            stages = game["stages"] = stage_statuses(video)
            before_court = [stages[stage] for stage in main.STAGES if stage not in main.NEEDS_COURT]
            if all(status == "done" for status in stages.values()):
                game["status"] = "finished"
            elif all(status == "todo" for status in stages.values()):
                game["status"] = "not started"
            elif not game["court_marked"] and all(status == "done" for status in before_court):
                game["status"] = "needs the court marked"
            else:
                game["status"] = "stopped part-way"
        games.append(game)
    return games


def stand_in_court(video):
    """True when this game's court landmarks are stand-in clicks, not the coach's own.

    select_court_keypoints.py writes "provisional" into court_calibration.json for such
    clicks. Every position worked out from them is provisional too, and the pages say so.
    """
    try:
        with open(config.DATA_DIR / config.video_stem(video) / "court_calibration.json", "r", encoding="utf-8") as f:
            return bool(json.load(f).get("provisional"))
    except (OSError, ValueError, AttributeError):
        return False


def chosen_game():
    """The video the coach chose last ("clips/game1.mp4"), or None. Kept in data/_dashboard/chosen_game.json."""
    try:
        with open(DASH_DIR / "chosen_game.json", "r", encoding="utf-8") as f:
            video = json.load(f).get("video")
    except (OSError, ValueError, AttributeError):
        return None
    return video if isinstance(video, str) and config.video_path(video).is_file() else None


def remember_game(video):
    """Keep the coach's choice of game, so every page and the next start open on the same one."""
    if video and video != chosen_game():
        DASH_DIR.mkdir(exist_ok=True)
        config.save_json(DASH_DIR / "chosen_game.json", {"video": video}, indent=2)


def game_choices():
    """Fill the "Game" box: every video in clips/, with the one chosen last selected.

    Returns a gr.Dropdown update. Its choices are ("game1", "clips/game1.mp4") pairs: the
    coach sees the short name, the pages get the video as main.py wants it.
    """
    choices = [(path.stem, f"clips/{path.name}") for path in video_files()]
    value = chosen_game()
    if value not in [video for _, video in choices]:
        value = choices[0][1] if choices else None
    return gr.Dropdown(choices=choices, value=value)


# ---------------------------------------------------------------- long jobs in the background

_jobs = {}      # name -> {"process": the running program, "log": the file its output goes to, "heavy": bool}


def start_job(name, command, heavy=False):
    """Start a long command in the background. Returns False if a job of that name is still running.

    name      a short name for the job, letters and digits, for example "run_game1"
    command   the program and its options as a list, for example
              [sys.executable, "src/main.py", "--video", "clips/game1.mp4"]   (run from the project folder)
    heavy     True for a job that loads the player finder or the number reader. The laptop
              has the memory for one of those at a time: ask what_is_running() first.

    Everything the command prints goes to data/_dashboard/logs/<name>.log. A page shows
    it with follow_job(name), and can come back to it after the browser page was closed
    or reloaded. The job belongs to the dashboard: stopping the dashboard stops it too
    (the stages save their progress and carry on next time).
    """
    if job_running(name):
        return False
    log = work_dir("logs") / f"{name}.log"
    with open(log, "w", encoding="utf-8") as output:
        # PYTHONUNBUFFERED: a Python program then prints every line at once, not in blocks
        # start_new_session: Ctrl+C in the dashboard's terminal then goes to the dashboard only, which
        # stops its jobs itself (stop_all_jobs). Otherwise a job would get Ctrl+C twice, and the
        # second one would cut off the saving that the first one started.
        process = subprocess.Popen([str(part) for part in command], cwd=config.PROJECT_ROOT, stdout=output,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                                   env=dict(os.environ, PYTHONUNBUFFERED="1"))
    _jobs[name] = {"process": process, "log": log, "heavy": heavy}
    return True


def job_running(name):
    """True while the job of that name, started by this dashboard, is still going."""
    return name in _jobs and _jobs[name]["process"].poll() is None


def job_exit_code(name):
    """None while the job is running (or was never started); afterwards 0 if it ended well, another number if not."""
    return _jobs[name]["process"].poll() if name in _jobs else None


def follow_job(name):
    """Yield what a job prints, for live progress, until the job ends.

    Each item is a list of new lines: first everything printed so far, then about once a
    second whatever is new. (Each update of a progress bar that rewrites its own line
    arrives as a line of its own.) Use it in a Gradio event that is a generator, for example
        shown = []
        for lines in dash_common.follow_job("run_game1"):
            shown += lines
            yield "\\n".join(shown[-30:])
    Yields nothing if no job of that name was started since the dashboard started.
    """
    if name not in _jobs:
        return
    process = _jobs[name]["process"]
    unfinished = ""                         # the start of a line that has not been printed to its end yet
    with open(_jobs[name]["log"], "r", encoding="utf-8", errors="replace") as log:
        while True:
            ended = process.poll() is not None      # asked BEFORE reading, so the last lines are never missed
            *lines, unfinished = (unfinished + log.read()).split("\n")
            if ended and unfinished:
                lines.append(unfinished)
            if lines:
                yield lines
            if ended:
                return
            time.sleep(1)


def stop_job(name, wait=20):
    """Stop a job the way Ctrl+C would, so a stage saves its progress. Waits up to `wait` seconds for it to end."""
    if not job_running(name):
        return
    process = _jobs[name]["process"]
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=wait)
    except subprocess.TimeoutExpired:
        process.kill()          # it did not react: end it
        process.wait()


def stop_all_jobs():
    """Stop every job this dashboard started. Called when the dashboard itself stops."""
    for name in list(_jobs):
        stop_job(name)


def running_videos():
    """Names ("game1") of the videos some program is working on right now, wherever it was started.

    main.py and the long stages hold a lock file /tmp/hoops-video-<name>.lock while they
    work (config.lock_video). Linux lists every lock that is held in /proc/locks, by the
    file's number, so we can look without touching the lock ourselves.
    """
    try:
        with open("/proc/locks", "r", encoding="utf-8") as f:
            # a line looks like "2: FLOCK  ADVISORY  WRITE 201 08:30:33562186 0 EOF"; the last part of
            # the 6th word is the file's number. "->" marks a program waiting for a lock, not holding it.
            held = {line.split()[5].split(":")[-1] for line in f if "FLOCK" in line and "->" not in line}
    except OSError:
        return []
    return [path.name[len("hoops-video-"):-len(".lock")] for path in Path("/tmp").glob("hoops-video-*.lock")
            if str(path.stat().st_ino) in held]


def is_running(video):
    """True when some program (this dashboard or a terminal) is working on this video right now."""
    return config.video_stem(video) in running_videos()


def what_is_running():
    """Plain words for the heavy job going on right now, or "" when the laptop is free for one.

    Looks at this dashboard's own heavy jobs and at runs started from a terminal.
    """
    for name, job in _jobs.items():
        if job["heavy"] and job_running(name):
            return f"the job '{name}' started from this dashboard"
    videos = running_videos()
    if videos:
        return "the processing of " + " and ".join(videos)
    return ""
