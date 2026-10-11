"""The dashboard: everything the tool can do, in the web browser, with no commands to type.

    python src/dashboard.py                    then open http://127.0.0.1:7860
    python src/dashboard.py --open-browser     the same, and open the Windows browser by itself
    python src/dashboard.py --make-shortcut    write "Hoops Tracker.bat" to the Windows desktop

It is served by this laptop to this laptop only (address 127.0.0.1). Nothing is uploaded.

How it is put together
    web/            the screens: plain HTML, CSS and JavaScript, one html + js per screen,
                    web/app.css and web/app.js shared. Served as they are, index.html first.
                    index.html is the start page and the only door between the two areas,
                    "Game analysis" (seasons, teams, games, players) and "Model training".
    /api/...        JSON answers the screens ask for. dash_api.py holds the shared ones
                    (games, seasons, teams, jobs, settings, branding) and its docstring is the
                    contract; api_settings.py and the seven screen modules named in
                    SCREEN_ROUTERS are included too, each only if its file exists.
    src/library.py  the data model: seasons, teams, game info, the numbers the screens share.
    /files/<folder>/<path>   files from the data folders for the browser: videos (with seeking),
                    pictures, result files. Only outputs/, crops/, clips/, data/ and the
                    branding folder; never the home folder or the code.

The school's name, logo, colours and artwork come from the branding folder
(data/_dashboard/branding/) while the program runs. They are never written in the
code. Without that folder the dashboard shows a neutral title.
"""
import argparse
import html
import importlib
import importlib.util
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import config
import dash_common

PORT = 7860
WEB_DIR = config.PROJECT_ROOT / "web"
SRC_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Hoops Tracker", docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- the API routers

# The screen modules of v3 (see the contract in dash_api.py), in the order they are included.
# Each is included only if its file exists, so a screen can be added without touching this file.
SCREEN_ROUTERS = ["api_seasons", "api_teams", "api_game", "api_tag", "api_training", "api_models", "api_drive"]


def include_routers():
    """dash_api.py and api_settings.py always; each screen module of SCREEN_ROUTERS when its file is there.

    A screen module that exists but fails to import stops the start with its own error, so the
    mistake is seen at once rather than hidden behind a missing screen.
    """
    for name in ["dash_api", "api_settings"]:
        app.include_router(importlib.import_module(name).router)
    missing = []
    for name in SCREEN_ROUTERS:
        if importlib.util.find_spec(name) is None:
            missing.append(name)
            continue
        module = importlib.import_module(name)
        if not hasattr(module, "router"):
            sys.exit(f"src/{name}.py has no `router`. Every screen module defines router = APIRouter(prefix=...).")
        app.include_router(module.router)
    if missing:
        print("Screens not built yet (no file in src/): " + ", ".join(f"{name}.py" for name in missing), flush=True)


# ---------------------------------------------------------------- the browser-tab icon

# A plain basketball, drawn here: no image file may be kept in the repo (see .gitignore).
FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<circle cx="16" cy="16" r="14" fill="#d81e27"/>
<g fill="none" stroke="#141112" stroke-width="2"><path d="M16 2v28M2 16h28M6 6c5 5 5 15 0 20M26 6c-5 5-5 15 0 20"/></g>
</svg>"""


@app.get("/favicon.svg")
def favicon():
    return Response(FAVICON, media_type="image/svg+xml")


# ---------------------------------------------------------------- files from the data folders

@app.api_route("/files/{folder}/{path:path}", methods=["GET", "HEAD"])
def serve_file(folder: str, path: str):
    """A file from one of the served folders. FileResponse answers Range requests, so videos can seek."""
    root = dash_common.SERVED_DIRS.get(folder)
    if root is None:
        raise HTTPException(404, "Not a folder the dashboard serves.")
    target = root / path
    try:
        root_real = root.resolve(strict=True)
        folder_real = target.parent.resolve(strict=True)
    except OSError:
        raise HTTPException(404, "No such file.")
    # The check is on the real path of the file's folder (the folders are links to the data folder), so ".."
    # cannot lead out. The file itself may be a link to a video kept elsewhere on the laptop (clips/game1.mp4).
    if (folder_real != root_real and root_real not in folder_real.parents) or not target.is_file():
        raise HTTPException(404, "No such file.")
    return FileResponse(target)


# ---------------------------------------------------------------- when a data folder cannot be reached

def missing_card_page():
    """One plain page saying which data folder cannot be reached and what to do."""
    folders = {"clips": config.CLIPS_DIR, "models": config.MODELS_DIR, "data": config.DATA_DIR,
               "outputs": config.OUTPUTS_DIR, "crops": config.CROPS_DIR}
    missing = [name for name, folder in folders.items() if not folder.is_dir()]
    links = "".join(f"<li><code>{html.escape(name)}</code> points to <code>{html.escape(str(where))}</code></li>"
                    for name, where in ((name, os.readlink(folders[name]) if folders[name].is_symlink() else "(not a link)")
                                        for name in missing))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hoops Tracker</title><link rel="stylesheet" href="/app.css"></head>
<body class="plain-page"><main class="problem">
  <h1>A data folder was not found</h1>
  <p>Hoops Tracker keeps the videos, the results and the pictures in five folders on this laptop, and it cannot
     reach {"this one" if len(missing) == 1 else "these"} now: {html.escape(", ".join(missing))}.</p>
  <ul>{links}</ul>
  <p><b>1.</b> In Windows Explorer, check that the folder it points to is still there and has not been renamed or moved.</p>
  <p><b>2.</b> If the data is on a removable drive, plug it in, then close the black Hoops Tracker window and
     double-click the Hoops Tracker shortcut again.</p>
  <p><b>If the data was moved</b>, the links have to be made again: see "If a data folder cannot be reached"
     in README.md. Then reload this page.</p>
</main></body></html>"""


@app.middleware("http")
async def need_the_card(request: Request, call_next):
    """While a data folder cannot be reached every screen shows the page above, and the API says so."""
    path = request.url.path
    if not dash_common.sd_card_ok() and path != "/api/health" and not path.endswith((".css", ".js")):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "A data folder cannot be reached. Reload the page: it says which one."}, 503)
        return HTMLResponse(missing_card_page(), 503)
    return await call_next(request)


# ---------------------------------------------------------------- the Windows shortcut

# The text of "Hoops Tracker.bat". A .bat file cannot be kept in git (only code and text
# are allowed there), so it is kept here and written with --make-shortcut.
# DISTRO, ATTACH_CARD, PROJECT, ACTIVATE, ADDRESS and OPTIONS are filled in by shortcut_text().
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
    # Where the data folder is, worked out from where the data link points. On a removable Windows drive
    # (/mnt/d/hoops-tracker/data) the shortcut attaches the drive to Ubuntu first; on Ubuntu's own disk
    # (/home/.../hoops-data/data) there is nothing to attach.
    card = Path(os.readlink(config.DATA_DIR)).parent if config.DATA_DIR.is_symlink() else Path("/")
    if len(card.parts) > 2 and card.parts[1] == "mnt" and len(card.parts[2]) == 1:
        letter = card.parts[2]
        attach = ("rem Attach the drive with the data to Ubuntu if it is not attached yet. This needs no password.\n"
                  f'wsl.exe -d {distro} -u root -e sh -c '
                  f'"[ -d \'{card}\' ] || (mkdir -p /mnt/{letter} && mount -t drvfs {letter.upper()}: /mnt/{letter})"')
    else:
        attach = "rem (the data folders are on Ubuntu's own disk, so there is nothing to attach)"
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


# ---------------------------------------------------------------- starting and stopping

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


def open_browser_when_ready(address, port):
    """Wait (in the background) until the server answers, then open the browser."""
    def wait_then_open():
        for _ in range(60):
            if already_answering(port):
                open_browser(address)
                return
            time.sleep(1)
    threading.Thread(target=wait_then_open, daemon=True).start()


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
        sys.exit(1)

    include_routers()
    # The screens themselves, last, so that /api and /files are looked at first.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    # 127.0.0.1: this laptop only, never the network.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))

    def stop(signal_number, frame):
        """Closing the window (SIGHUP) or being asked to end (SIGTERM) stops the server like Ctrl+C."""
        server.should_exit = True
    signal.signal(signal.SIGHUP, stop)
    signal.signal(signal.SIGTERM, stop)

    print()
    print(f"Hoops Tracker is running. Open this address in your web browser:   {address}")
    print("It can only be reached from this laptop.")
    print("To stop it: press Ctrl+C here, or close this window.", flush=True)
    if not dash_common.sd_card_ok():
        print("A data folder cannot be reached: the page says which one and what to do.", flush=True)
    if args.open_browser:
        open_browser_when_ready(address, args.port)
    try:
        server.run()            # uvicorn turns Ctrl+C into a clean stop
    finally:
        # Also reached on Ctrl+C: no background job is left running.
        print("\nStopping Hoops Tracker...")
        dash_common.stop_all_jobs()


if __name__ == "__main__":
    run()
