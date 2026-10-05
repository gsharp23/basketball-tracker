"""The dashboard: everything the tool can do, in the web browser, with no commands to type.

    python src/dashboard.py                    then open http://127.0.0.1:7860
    python src/dashboard.py --make-shortcut    write "Hoops Tracker.bat" to the Windows desktop

It is served by this laptop to this laptop only (address 127.0.0.1). Nothing is uploaded.

The pages
    /         the start page: the school logo and artwork, and two choices
    /stats    "View the stats": tabs built by dash_stats.py and dash_shots.py
    /train    "Train the model": tabs built by dash_train.py
Each is a real page with its own address, so the browser's Back button works and a page
can be reloaded without losing its place.

This file is the only one that knows the layout. A page module has one function,
build(game), which adds its tabs; it never imports another page module. What the pages
share (the list of games, running a long job, the look) is in dash_common.py.

The school's name, logo, colours and artwork come from the branding folder on the SD
card (data/_dashboard/branding/) while the program runs. They are never written in the
code. Without that folder the dashboard shows a neutral title.
"""
import argparse
import html
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import config
import dash_common
import dash_shots
import dash_stats
import dash_train
import main as pipeline         # only for windows_path()
from dash_common import gr

PORT = 7860
PLAIN_TITLE = "Hoops Tracker"

# A plain basketball, drawn here so that no picture file is needed. Shown on the start
# page when the branding folder has neither artwork nor a logo.
BALL_PICTURE = """
<svg class="hoops-hero" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="basketball">
  <circle cx="100" cy="100" r="92" fill="var(--hoops-accent)" stroke="#ffffff" stroke-width="6"/>
  <g fill="none" stroke="#ffffff" stroke-width="6" stroke-linecap="round">
    <path d="M100 8 V192"/><path d="M8 100 H192"/>
    <path d="M36 34 C78 70 78 130 36 166"/><path d="M164 34 C122 70 122 130 164 166"/>
  </g>
</svg>
"""


# ---------------------------------------------------------------- the pieces of HTML

def logo_plate(brand, extra_class=""):
    """The school logo on a plate of its own background colour ("" if there is no logo)."""
    if not brand["logo"]:
        return ""
    return (f'<span class="hoops-logo-plate {extra_class}">'
            f'<img src="{dash_common.logo_data(brand["logo"])}" alt="school logo"></span>')


def landing_html(brand):
    """The start page: logo, artwork, title, and the two big choices."""
    name, mascot = html.escape(brand["school_name"]), html.escape(brand["mascot"])
    title = mascot or name or PLAIN_TITLE
    if mascot and name:
        under_title = f"{name} &middot; {PLAIN_TITLE}"
    elif name:
        under_title = PLAIN_TITLE
    else:
        under_title = "Game video in, coaching numbers out"

    # The centrepiece: the artwork if there is one, else the logo shown large, else a plain ball.
    if brand["hero"]:
        centre = f'<img class="hoops-hero" src="{dash_common.picture_data(brand["hero"])}" alt="team artwork">'
        corner = logo_plate(brand, "hoops-logo-corner")
    elif brand["logo"]:
        centre, corner = logo_plate(brand, "hoops-logo-centre"), ""
    else:
        centre, corner = BALL_PICTURE, ""

    return f"""
<div class="hoops-landing">
  {corner}
  {centre}
  <h1>{title}</h1>
  <p class="hoops-under-title">{under_title}</p>
  <div class="hoops-choices">
    <a class="hoops-choice" href="stats">
      <div class="hoops-choice-title">View the stats</div>
      <div class="hoops-choice-text">Process a game, then see the videos, the numbers and where the points came from.</div>
    </a>
    <a class="hoops-choice" href="train">
      <div class="hoops-choice-title">Train the model</div>
      <div class="hoops-choice-text">Show the tool where the ball is in your own games, so it can learn to find it.</div>
    </a>
  </div>
</div>
"""


def band_html(brand, area_title):
    """The coloured band at the top of an area page: logo, the area's name, and the way back."""
    small = html.escape(brand["school_name"]) or PLAIN_TITLE
    return f"""
<div class="hoops-band">
  {logo_plate(brand)}
  <div class="hoops-band-title"><span class="hoops-band-small">{small}</span>{area_title}</div>
  <a class="hoops-back" href="./">&larr; Back to the start page</a>
</div>
"""


def footer_html():
    """Where things are: is the SD card attached, and that nothing leaves the laptop. Worked out each time a page opens."""
    if dash_common.sd_card_ok():
        folder = config.DATA_DIR.resolve().parent
        where = pipeline.windows_path(folder) or folder
        card = f'SD card: <b class="hoops-good">attached</b> ({html.escape(str(where))} holds the videos and results)'
    else:
        card = ('SD card: <b class="hoops-bad">NOT attached</b>. Close the Hoops Tracker window and start it again '
                'with the card pushed in.')
    return (f'<div class="hoops-footer">{card} &nbsp;&bull;&nbsp; Everything runs on this laptop only: '
            f'no video, picture or name is sent anywhere.</div>')


def missing_card_html():
    """What to show instead of the dashboard when the SD-card folders cannot be reached."""
    folders = [config.CLIPS_DIR, config.MODELS_DIR, config.DATA_DIR, config.OUTPUTS_DIR, config.CROPS_DIR]
    missing = ", ".join(folder.name for folder in folders if not folder.is_dir())
    # The command is the one config.py and the README give, so it is written in one place only.
    command = next(line.strip() for line in config.SD_CARD_HELP.splitlines() if "mount" in line)
    return f"""
<div class="hoops-problem">
  <h1>The SD card was not found</h1>
  <p>Hoops Tracker keeps the videos, the results and the player names on the SD card, and it cannot reach
     the card now (missing: {html.escape(missing)}). This happens after Windows or Ubuntu has restarted.</p>
  <p><b>1.</b> Check that the SD card is pushed in.</p>
  <p><b>2.</b> Close the black Hoops Tracker window and double-click the Hoops Tracker shortcut again.
     The shortcut attaches the card by itself.</p>
  <p><b>If that does not help</b>, open the Ubuntu terminal, type this line (it asks for your Ubuntu
     password), and then start Hoops Tracker again:</p>
  <code>{html.escape(command)}</code>
  <p>On a fresh copy of the project the links to the card have to be made first: see
     "Reinstall from scratch" in README.md.</p>
</div>
"""


# ---------------------------------------------------------------- building the pages

def game_box(demo):
    """The "Game" box at the top of an area page. Returns it; its value is the chosen video.

    It starts empty. When the page opens it is filled with the videos in clips/ and set to
    the game chosen last, which fires its "change" event: that is how the page modules get
    to show the right game from the first moment (see the build() docstrings).
    """
    game = gr.Dropdown(label="Game (everything on this page is about this game)", choices=[], value=None,
                       interactive=True, elem_classes="hoops-game")
    demo.load(dash_common.game_choices, outputs=game)
    game.input(dash_common.remember_game, inputs=game)      # "input" = the coach picked another one
    return game


def build_app(brand):
    """Build the whole dashboard and return it (not started yet)."""
    with gr.Blocks(title=PLAIN_TITLE, analytics_enabled=False, fill_width=True, delete_cache=(3600, 86400)) as demo:
        gr.HTML(landing_html(brand))
        gr.HTML(footer_html)        # given as a function, so it is worked out afresh each time the page opens

    with demo.route("View the stats", "/stats"):
        gr.HTML(band_html(brand, "View the stats"))
        with gr.Column(elem_classes="hoops-body"):
            game = game_box(demo)
            with gr.Tabs():
                dash_stats.build(game)      # adds the tabs Games, Court set-up, Roster, Results
                dash_shots.build(game)      # adds the tab Shots
        gr.HTML(footer_html)

    with demo.route("Train the model", "/train"):
        gr.HTML(band_html(brand, "Train the model"))
        with gr.Column(elem_classes="hoops-body"):
            game = game_box(demo)
            with gr.Tabs():
                dash_train.build(game)      # adds the tabs Label the ball, Export, Models
        gr.HTML(footer_html)
    return demo


def build_missing_card_app():
    """One plain page saying the SD card is not attached and what to do. Shown instead of the dashboard."""
    with gr.Blocks(title=PLAIN_TITLE, analytics_enabled=False, fill_width=True) as demo:
        gr.HTML(missing_card_html())
    # the same message at the addresses of the two areas, in case one of them is still open in the browser
    for name, path in (("View the stats", "/stats"), ("Train the model", "/train")):
        with demo.route(name, path):
            gr.HTML(missing_card_html())
    return demo


# ---------------------------------------------------------------- the Windows shortcut

# The text of "Hoops Tracker.bat". A .bat file cannot be kept in git (only code and text
# are allowed there), so it is kept here and written with --make-shortcut.
# DISTRO, ATTACH_CARD, PROJECT, ACTIVATE and ADDRESS are filled in by shortcut_text().
SHORTCUT = r"""@echo off
title Hoops Tracker
echo.
echo    HOOPS TRACKER
echo.
echo    Starting. Your web browser opens by itself in about half a minute.
echo    If it does not, open this address in the browser yourself:
echo.
echo        ADDRESS
echo.
echo    Keep this window open while you use Hoops Tracker.
echo    TO STOP IT: close this window.
echo    (That also stops a game that is being processed. Nothing saved is lost:
echo    start Hoops Tracker again and press the same button to carry on.)
echo.
rem Attach the SD card to Ubuntu if it is not attached yet. This needs no password.
ATTACH_CARD
rem Start the dashboard inside Ubuntu. This line runs until the dashboard is stopped.
wsl.exe -d DISTRO -e bash -c "cd 'PROJECT' && source 'ACTIVATE' && python src/dashboard.py OPTIONS"
echo.
echo    Hoops Tracker has stopped. You can close this window.
pause >nul
"""


def shortcut_text(options="--open-browser"):
    """The text of the Windows shortcut for this copy of the project."""
    distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu")
    # Where the SD card is attached, worked out from where the data link points (/mnt/d/hoops-tracker/data).
    card = Path(os.readlink(config.DATA_DIR)).parent if config.DATA_DIR.is_symlink() else Path("/")
    if len(card.parts) > 2 and card.parts[1] == "mnt" and len(card.parts[2]) == 1:
        letter = card.parts[2]
        attach = (f'wsl.exe -d {distro} -u root -e sh -c '
                  f'"[ -d \'{card}\' ] || (mkdir -p /mnt/{letter} && mount -t drvfs {letter.upper()}: /mnt/{letter})"')
    else:
        attach = "rem (the data folders are not on a Windows drive, so there is nothing to attach)"
    text = SHORTCUT
    for word, value in (("ATTACH_CARD", attach), ("DISTRO", distro), ("PROJECT", str(config.PROJECT_ROOT)),
                        ("ACTIVATE", str(Path(sys.prefix) / "bin" / "activate")),
                        ("ADDRESS", f"http://127.0.0.1:{PORT}"), ("OPTIONS", options)):
        text = text.replace(word, value)
    return text


def windows_desktop():
    """The Windows desktop folder as Ubuntu sees it. Windows is asked, because the desktop may be inside OneDrive."""
    try:
        asked = subprocess.run(["powershell.exe", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                               capture_output=True, text=True, cwd="/mnt/c", timeout=60)
        folder = subprocess.run(["wslpath", "-u", asked.stdout.strip()], capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        folder = ""
    if not folder or not Path(folder).is_dir():
        sys.exit("Could not find the Windows desktop folder from Ubuntu.\n"
                 "Run this from the Ubuntu terminal on the laptop itself, with the environment switched on.")
    return Path(folder)


def make_shortcut():
    """Write "Hoops Tracker.bat" to the Windows desktop (again)."""
    target = windows_desktop() / "Hoops Tracker.bat"
    with open(target, "w", encoding="ascii", newline="\r\n") as f:     # Windows line ends
        f.write(shortcut_text())
    print(f"Wrote the shortcut:  {target}")
    print("Double-click \"Hoops Tracker\" on the Windows desktop to start the dashboard.")


# ---------------------------------------------------------------- starting it

def already_answering(port):
    """True if something is already serving on this laptop at that port (most likely a dashboard started earlier)."""
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def open_browser(address):
    """Open the address in the Windows default browser. (The dashboard runs inside Ubuntu, the browser in Windows.)"""
    try:
        subprocess.run(["cmd.exe", "/c", "start", address], cwd="/mnt/c", timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        print(f"Could not open the browser by itself. Open this address yourself:  {address}")


def stop_like_ctrl_c(signal_number, frame):
    """Closing the window, or the computer asking the program to end, is treated like Ctrl+C."""
    raise KeyboardInterrupt


def parse_args():
    parser = argparse.ArgumentParser(description="The Hoops Tracker dashboard, in the web browser, on this laptop only.")
    parser.add_argument("--port", type=int, default=PORT, help=f"port of the address (default {PORT})")
    parser.add_argument("--open-browser", action="store_true", help="open the Windows browser at the address once it answers")
    parser.add_argument("--make-shortcut", action="store_true",
                        help='write "Hoops Tracker.bat" to the Windows desktop and stop')
    return parser.parse_args()


def run():
    args = parse_args()
    if args.make_shortcut:
        make_shortcut()
        return
    address = f"http://127.0.0.1:{args.port}"
    if already_answering(args.port):
        print(f"Hoops Tracker is already running (something answers at {address}).")
        print("Use that one. To start afresh, first close the window it was started from.")
        if args.open_browser:
            open_browser(address)
        return

    if dash_common.sd_card_ok():
        brand = dash_common.load_branding()
        demo = build_app(brand)
        css = dash_common.make_css(dash_common.brand_colours(brand["logo"]))
        # Pictures and videos in these folders are sent to the browser as they are, not copied first.
        gr.set_static_paths(dash_common.SERVED_DIRS)
        allowed = [str(folder) for folder in dash_common.SERVED_DIRS]
        kept_back = [str(folder) for folder in dash_common.NOT_SERVED]
    else:
        demo = build_missing_card_app()
        css = dash_common.make_css(dash_common.NEUTRAL_COLOURS)
        allowed, kept_back = [], []

    signal.signal(signal.SIGHUP, stop_like_ctrl_c)      # the window it runs in was closed
    signal.signal(signal.SIGTERM, stop_like_ctrl_c)     # asked to end
    try:
        # 127.0.0.1 and share=False: this laptop only, never the internet.
        demo.launch(server_name="127.0.0.1", server_port=args.port, share=False, inbrowser=False, quiet=True,
                    prevent_thread_lock=True, theme=dash_common.make_theme(), css=css, footer_links=[],
                    allowed_paths=allowed, blocked_paths=kept_back, ssr_mode=False, pwa=False)
        print()
        print(f"Hoops Tracker is running. Open this address in your web browser:   {address}")
        print("It can only be reached from this laptop.")
        print("To stop it: press Ctrl+C here, or close this window.", flush=True)
        if args.open_browser:
            open_browser(address)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping Hoops Tracker...")
    finally:
        # Also reached on Ctrl+C: no server and no background job is left running.
        dash_common.stop_all_jobs()
        demo.close(verbose=False)


if __name__ == "__main__":
    run()
