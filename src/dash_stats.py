"""The "View the stats" tabs of the dashboard: Games, Court set-up, Roster, Results.

dashboard.py calls build(game) once; everything else here is plain functions that the
buttons call. Each tab has one "refresh" function that reads the files on the SD card
and returns what the tab shows, so a page that is opened again always shows the truth.

    Games          refresh_games()    start, follow and stop main.py for the chosen game
    Court set-up   refresh_court()    start the three click pages, one at a time
    Roster         load_roster(), save_roster()      jersey numbers and names -> roster.json
    Results        refresh_results()  the two videos, the stats table, the position maps

Long work is never done inside the dashboard. It is started as a program of its own
(dash_common.start_job), and a timer on the page looks every few seconds at what that
program has printed. So closing the browser page does not stop the work, and opening
the page again finds it.

Nothing here makes a game's folders just by looking, and nothing stops the dashboard
when a file is missing or damaged: the page says what is wrong instead.
"""
import functools
import html
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

import cv2

import config
import dash_common
import main as pipeline         # the stage names and windows_path(); importing it runs nothing
import stats as stats_stage     # for clock(), so times look the same as in the printed table
from dash_common import gr

NO_CARD = ("The SD card cannot be reached. Push the card in, close the Hoops Tracker window "
           "and start Hoops Tracker again.")
NO_GAME = "No game is chosen. Choose one in the Game box at the top of the page."

# The six steps of main.py in plain words, and what dash_common.list_games() says about each.
STAGE_NAMES = {"detect": "Find the players", "court": "Map the court", "teams": "Teams",
               "jerseys": "Jersey numbers", "tactical": "Top-down video", "stats": "Stats"}
STATE_WORDS = {"done": "done", "half": "part done", "todo": "to do", "unknown": "damaged file?"}

# README, "Limitations": a 52-minute game was worked out at 23 to 30 hours or more on this
# laptop (detection 8-14 h, jersey reading 10 h or more, court 3 h, top-down video 2-3 h).
# It has never been run to the end, so this is an estimate. Other lengths are scaled from it.
README_GAME_MINUTES = 52
README_GAME_HOURS = (23, 30)
LONG_VIDEO_MINUTES = 5          # ask "are you sure?" before starting anything longer than this

CLICK_JOB = "court_page"        # the job name of the click page that is open (one at a time)
LINK_STYLE = ("display:inline-block; background:var(--hoops-accent); color:var(--hoops-on-accent) !important; "
              "font-size:21px; font-weight:700; padding:16px 26px; border-radius:10px; text-decoration:none;")


# ---------------------------------------------------------------- small helpers

def data_folder(video):
    """data/<game>/ for a video. Not created here: a game nobody has started has no folder."""
    return config.DATA_DIR / config.video_stem(video)


def output_folder(video):
    return config.OUTPUTS_DIR / config.video_stem(video)


def read_json(path):
    """A JSON file's contents, or None when it is missing or damaged.

    (config.load_json stops the whole program on a damaged file. That is right for
    main.py, but the dashboard has to stay up and say what is wrong.)
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def tail(path, size=400_000):
    """The last part of a text file as text ("" if the file is not there)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)                            # go to the end, to learn the length
            f.seek(max(f.tell() - size, 0))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def box(inner_html):
    """A white guidance box like dash_common.note(), for text that has its own HTML in it."""
    return f'<div class="hoops-note">{inner_html}</div>'


def file_url(path):
    """The address the browser on this laptop fetches a file on the SD card from.

    The file is sent as it is, in place (see gr.set_static_paths in dashboard.py). The
    file's time is added so that the browser does not show an old copy of a picture
    or video that has been made again.
    """
    return f"/gradio_api/file={quote(str(path))}?v={int(Path(path).stat().st_mtime)}"


def picture_html(path, caption):
    """A picture with a line of text above it. A click opens it full size in a new browser tab."""
    address = file_url(path)
    return (f'<div style="margin:14px 0;"><div style="font-size:17px; margin-bottom:6px;">{html.escape(caption)}</div>'
            f'<a href="{address}" target="_blank"><img src="{address}" alt="{html.escape(caption)}" '
            f'style="max-width:100%; border:1px solid #c9ccd1; border-radius:8px;"></a></div>')


def video_html(path, caption):
    """A video player for an mp4 on the SD card. The file is played as it is: nothing is copied or converted."""
    return (f'<div style="flex:1; min-width:320px;"><div style="font-size:17px; font-weight:600; margin-bottom:6px;">'
            f'{html.escape(caption)}</div><video controls preload="metadata" src="{file_url(path)}" '
            f'style="width:100%; max-height:70vh; background:#000000; border-radius:8px;"></video></div>')


# ---------------------------------------------------------------- Games: the list

_lengths = {}       # (file, time, size) -> seconds of video, so each video is opened only once


def video_seconds(path):
    """How long a video is, in seconds (None if it cannot be read)."""
    key = (str(path), path.stat().st_mtime, path.stat().st_size)
    if key not in _lengths:
        video = cv2.VideoCapture(str(path))
        fps, frames = video.get(cv2.CAP_PROP_FPS), video.get(cv2.CAP_PROP_FRAME_COUNT)
        video.release()
        _lengths[key] = frames / fps if fps and frames else None
    return _lengths[key]


def length_words(seconds):
    if seconds is None:
        return "cannot be read"
    return f"{seconds:.0f} sec" if seconds < 90 else f"{seconds / 60:.0f} min"


def time_estimate(seconds):
    """How long all the steps take for a video of this length, in plain words (scaled from the README's figures)."""
    low, high = (hours * seconds / 60 / README_GAME_MINUTES for hours in README_GAME_HOURS)
    if high < 1.5:
        return f"about {low * 60:.0f} to {high * 60:.0f} minutes, or more"
    return f"about {low:.0f} to {high:.0f} hours, or more"


def all_games():
    """dash_common.list_games(), or an empty list when the SD card cannot be reached."""
    try:
        return dash_common.list_games() if dash_common.sd_card_ok() else []
    except OSError:         # the card went away while we were looking
        return []


def games_table(games):
    """One row per video: its name, its length, its status and the state of each step."""
    rows = []
    for game in games:
        if game["stages"]:
            steps = [STATE_WORDS[game["stages"][stage]] for stage in pipeline.STAGES]
        else:
            steps = ["working"] * len(pipeline.STAGES)      # its files are changing: see the progress below
        court = "no"
        if game["court_marked"]:
            court = "stand-in" if dash_common.stand_in_court(game["video"]) else "yes"
        rows.append([game["name"], length_words(video_seconds(config.video_path(game["video"]))), game["status"]]
                    + steps + [court])
    return rows


def what_next(game):
    """One plain sentence about the chosen game: where it stands and what to do now."""
    name = html.escape(game["name"])
    advice = {
        "not started": "Nothing has been done for it yet. Fill in the <b>Roster</b> tab first if you can, then press "
                       "<b>Process this game</b> to start.",
        "running": "It is being processed now. How far it is shows below.",
        "needs the court marked": "The players are found. Now mark the court in the <b>Court set-up</b> tab, "
                                  "then press <b>Process this game</b> again.",
        "stopped part-way": "Some steps are still to do (see the table). Press <b>Process this game</b> to carry on: "
                            "finished steps are skipped.",
        "finished": "Every step is done. Look at the <b>Results</b> tab.",
    }
    status, text = game["status"], advice[game["status"]]
    if status == "finished" and dash_common.stand_in_court(game["video"]):
        status = "finished, but with stand-in court marks"
        text = ("Every step is done, but the court of this game was marked with stand-in clicks, not by you, so every "
                "position is provisional. Do step 2 in the <b>Court set-up</b> tab yourself, then press "
                "<b>Process this game</b> again. The <b>Results</b> tab shows what there is so far.")
    elif any(state == "unknown" for state in (game["stages"] or {}).values()):
        text += (" A result file of this game could not be read (\"damaged file?\" in the table): "
                 "<b>Process this game</b> makes it again.")
    return box(f"<b>Chosen game: {name}</b> &mdash; {status}. {text}")


# ---------------------------------------------------------------- Games: following a run

_stopping = set()       # the videos whose run is being stopped right now ("Stop" was pressed, the program is still saving)


def job_name(video):
    """The name of the background job that processes this game."""
    return "run_" + config.video_stem(video)


def run_state(video):
    """"running", "starting" (our own job has not reached its first step yet) or "" for one game."""
    if dash_common.is_running(video):
        return "running"
    return "starting" if dash_common.job_running(job_name(video)) else ""


def run_lines(video):
    """What the latest run on this game printed, as a list of lines.

    A run started from this dashboard: the job's own log in data/_dashboard/logs/. It
    has the same lines as run_log.txt, and also a failure that happened before main.py
    got as far as opening run_log.txt.
    Any other run (started in a terminal, or before the dashboard was restarted): the
    last run in the game's run_log.txt.
    """
    name = job_name(video)
    ours = dash_common.job_running(name) or (dash_common.job_exit_code(name) is not None
                                             and not dash_common.is_running(video))
    if ours:
        return tail(dash_common.DASH_DIR / "logs" / f"{name}.log").splitlines()
    text = tail(data_folder(video) / "run_log.txt")
    return text[text.rfind("\n===== ") + 1:].splitlines()     # main.py starts every run with a "=====" line


def read_progress(lines):
    """What the printed lines of one run say: which step, how far, how long is left, how it ended.

    Returns {"step": "detect", "count": "Step 1 of 6", "how_far": 0.0 to 1.0 or None,
             "left": "about 5 min" or "", "detail": the latest progress line, "ended": "finished" | "court" | "stopped" | ""}.
    """
    progress = {"step": "", "count": "", "how_far": None, "left": "", "detail": "", "ended": ""}
    for line in lines:
        new_step = re.match(r"=== Stage (\d+) of (\d+): (\w+)", line)
        if new_step:
            progress.update(step=new_step[3], count=f"Step {new_step[1]} of {new_step[2]}",
                            how_far=None, left="", detail="")
            continue
        # "  frame 300 of 6000 (5%) | ...", "  200 of 900 frames (...", "  read 40 of 600 frames"
        counted = re.match(r"\s+(?:frame |read )?(\d+) of (\d+)", line)
        # "... | about 5 min left", "... about 2:10 to go)", "... at most 40 min to go"
        left = re.search(r"(about|at most) ([^,|()]+?) (left|to go)", line)
        if counted and int(counted[2]):
            progress["how_far"] = min(int(counted[1]) / int(counted[2]), 1.0)
        if left:
            progress["left"] = f"{left[1]} {left[2]}"
        if counted or left:
            progress["detail"] = line.strip()
        if "already done - skipped" in line:
            progress["detail"] = "already done"
        if line.startswith("=== All stages are done"):
            progress["ended"] = "finished"
        elif line.startswith("STOPPED: the court has not been marked"):
            progress["ended"] = "court"
        elif line.startswith(("Stopped ", "KeyboardInterrupt")):       # the second: stopped while still loading
            progress["ended"] = "stopped"
    return progress


def progress_html(video):
    """The progress box of the Games tab: what the run on the chosen game is doing, or how the last one ended."""
    state = run_state(video)
    if state and video in _stopping:
        return box("<b>Stopping...</b> The program is saving its progress. This can take a minute or two.")
    if state == "starting":
        return box("<b>Starting...</b> The program is loading. The first lines appear here within a minute.")
    lines = run_lines(video)
    if not lines:
        return box("<b>Working...</b>") if state else ""
    progress = read_progress(lines)
    step = html.escape(STAGE_NAMES.get(progress["step"], progress["step"]))

    if state == "running":
        text = f"<b>Working. {progress['count']}: {step}</b>" if step else "<b>Working.</b>"
        if progress["detail"] == "already done":
            text += " (already done, skipped)"
        if progress["how_far"] is not None:
            percent = round(100 * progress["how_far"])
            text += (f'<div style="background:#e3e5e8; border-radius:8px; height:24px; margin:10px 0 6px 0; overflow:hidden;">'
                     f'<div style="width:{percent}%; height:100%; background:var(--hoops-accent);"></div></div>'
                     f"{percent}% of this step.")
        if progress["left"]:
            text += (f" The program's own guess for this step: <b>{html.escape(progress['left'])}</b> to go"
                     " (the later steps come on top of that).")
        if not court_marked(video):
            text += ("<br>The court is not marked for this game yet. You can do that now, in the "
                     "<b>Court set-up</b> tab, while this is running.")
    elif progress["ended"] == "finished":
        text = "<b>The last run finished: every step is done.</b> Look at the Results tab."
        if dash_common.stand_in_court(video):
            text += " The positions are provisional: the court was marked with stand-in clicks (see above)."
    elif progress["ended"] == "court":
        text = ("<b>The last run stopped because the court is not marked yet.</b> The players, teams and jersey numbers "
                "are saved. Mark the court in the Court set-up tab, then press Process this game again.")
    elif progress["ended"] == "stopped":
        text = ("<b>The last run was stopped.</b> Finished steps are kept and the long steps carry on where they "
                "stopped: press Process this game.")
    else:
        text = ("<b>The last run stopped before the end.</b> The lines below say why, if the program could say. "
                "Finished steps are kept: press Process this game to carry on.")

    # What the program flagged during this run, said once each: easy to miss among hours of lines.
    # (Not the two notes about the court marks: the dashboard says those in its own words.)
    flagged = list(dict.fromkeys(line.strip() for line in lines if line.lstrip().startswith(("NOTE:", "WARNING:"))
                                 and "PROVISIONAL" not in line and "has not been marked" not in line))
    if flagged:
        text += ('<div style="margin-top:10px;"><b>The program flagged this during the run:</b><br>'
                 + "<br>".join(html.escape(line) for line in flagged) + "</div>")
    if state == "running":
        return box(text)
    # The program's own last lines are for finding a fault. They are folded away, and only
    # open by themselves when the run broke off for a reason the dashboard does not know.
    last_lines = html.escape("\n".join([line for line in lines if line.strip()][-12:]))
    return box(f'{text}<details{"" if progress["ended"] else " open"} style="margin-top:12px;">'
               f'<summary style="font-size:15px; color:#555555; cursor:pointer;">Details for troubleshooting: the last '
               f'lines the program printed (they use the program\'s own words and terminal commands; here the buttons '
               f'do the same)</summary>'
               f'<pre style="font-size:13px; line-height:1.35; background:#f2f3f5; padding:10px 12px; border-radius:8px; '
               f'overflow-x:auto; white-space:pre-wrap; margin:6px 0 0 0;">{last_lines}</pre></details>')


def refresh_games(video):
    """Everything the Games tab shows, read afresh: (the chosen game, the table, the progress box, the timer).

    Called when the page opens, when another game is chosen, by the buttons, and by the
    timer every few seconds while the chosen game is being processed. The timer is
    switched on here while there is a run to watch and off when there is none.
    """
    if not dash_common.sd_card_ok():
        return dash_common.warning(NO_CARD), [], "", gr.Timer(active=False)
    games = all_games()
    chosen = [game for game in games if game["video"] == video]
    if not chosen:
        return dash_common.note("No game is chosen. " + (
            "Choose one in the Game box at the top of the page." if games else
            "There are no videos yet: see \"How to add a game\" below.")), games_table(games), "", gr.Timer(active=False)
    watching = bool(run_state(video))
    if watching:
        chosen[0]["status"] = "running"         # also while our own job is still loading
    return what_next(chosen[0]), games_table(games), progress_html(video), gr.Timer(active=watching)


# ---------------------------------------------------------------- Games: start and stop

def busy_with():
    """Plain words for the heavy job the laptop is busy with, or "" when it is free for one."""
    for path in dash_common.video_files():
        if dash_common.job_running(job_name(path)):
            return f"the processing of {path.stem}"         # our own job, named by its game
    return dash_common.what_is_running()                    # a job of another page, or a run from a terminal


def cannot_start(video):
    """A plain reason why processing cannot be started now, or "" when it can."""
    if not dash_common.sd_card_ok():
        return NO_CARD
    if not video or not config.video_path(video).is_file():
        return NO_GAME
    busy = busy_with()
    if busy:
        return (f"The laptop is already busy with {busy}. It only has the memory for one job at a time. "
                "Wait for that to finish, or stop it first.")
    return ""


def press_process(video):
    """"Process this game" was pressed. A long video is asked about first; a short or finished one starts at once.

    Returns (the progress box, the "are you sure?" box, its text, the timer).
    """
    problem = cannot_start(video)
    if problem:
        return dash_common.warning(problem), gr.Group(visible=False), "", gr.skip()
    seconds = video_seconds(config.video_path(video))
    finished = all(state == "done" for state in dash_common.stage_statuses(video).values())
    if finished or (seconds is not None and seconds <= LONG_VIDEO_MINUTES * 60):
        return start_processing(video)
    how_long = (f"This video is {length_words(seconds)} long. All the steps together take {time_estimate(seconds)}, "
                "on this laptop." if seconds else "The length of this video cannot be read, so it may take very long.")
    return gr.skip(), gr.Group(visible=True), dash_common.warning(
        f"{how_long}\nThat figure is worked out from a 20-second test clip: a full game has never been run to the end.\n"
        "Steps that are already done are skipped. The laptop must stay switched on, awake and plugged in, and the "
        "Hoops Tracker window must stay open.\nYou can stop at any time. Finished steps are kept, and the long steps "
        "carry on where they stopped."), gr.skip()


def start_processing(video):
    """Start main.py for the chosen game as a background job. Same four results as press_process()."""
    problem = cannot_start(video)       # asked again: time has passed if the coach was reading the question
    if problem:
        return dash_common.warning(problem), gr.Group(visible=False), "", gr.skip()
    dash_common.start_job(job_name(video), [sys.executable, "src/main.py", "--video", video], heavy=True)
    return progress_html(video), gr.Group(visible=False), "", gr.Timer(active=True)


def press_stop(video):
    """"Stop" was pressed: stop the run on the chosen game the way Ctrl+C would, so that it saves its progress."""
    if not video:
        yield dash_common.warning(NO_GAME)
    elif dash_common.job_running(job_name(video)):
        _stopping.add(video)
        yield progress_html(video)          # says "Stopping..."
        try:
            dash_common.stop_job(job_name(video), wait=120)     # a step may need a while to save what it has
        finally:
            _stopping.discard(video)
        yield progress_html(video)
    elif dash_common.is_running(video):
        yield dash_common.warning("This run was not started from this Hoops Tracker window, so it cannot be stopped "
                                  "from here. Stop it in the window it was started in, with Ctrl+C.")
    else:
        busy = busy_with()
        yield dash_common.note("Nothing is running for this game." + (
            f" The laptop is busy with {busy}. To stop the processing of a game, choose that game in the Game box first."
            if busy else ""))


# ---------------------------------------------------------------- Court set-up

# The three click pages, in order: (the script, the file it saves, the button, what to do on the page).
# The guidance is the README's "Mark the court", in plain words.
COURT_PAGES = [
    ("select_court.py", "court_setup.json", "Step 1: court size and reference pictures",
     "The tool looks through the video and picks a few pictures that show different parts of the gym. "
     "Choose the court size (high school, 84 x 50 ft, is already filled in; type your own numbers for another court). "
     "Untick any picture that is blurred or does not show the court. Then press \"Save and close\". "
     "For a full game, getting the page ready takes a few minutes."),
    ("select_court_keypoints.py", "court_calibration.json", "Step 2: click the court landmarks",
     "Pick a picture, choose a landmark from the list, click it in the picture, then click it again exactly in the "
     "enlarged picture. Use the basketball lines, not the lines of other sports. Do at least 4 landmarks at each end "
     "of the court that the video shows, 10 to 14 in all, and include the two points where the half-court line "
     "crosses the centre circle. After 4 landmarks a green court is drawn: it should sit on the real lines in every "
     "picture. Then press \"Save and close\"."),
    ("select_hoops.py", "hoops.json", "Step 3: mark the hoops (optional)",
     "For each hoop the video shows, click the two ends of the rim (each click is repeated in the enlarged picture). "
     "You can skip this step for now: the positions and the stats do not need it. It is for finding shots "
     "automatically, later. (Stand-in marks here do no harm: redo them only if you want to.)"),
]
CHECK_PICTURES = [
    ("court_calibration_check.jpg", "Check after step 2: the green court should sit on the painted lines in every picture."),
    ("hoops_check.jpg", "Check after step 3: the thin coloured line should lie on the rim in every small picture."),
    ("court_check.jpg", "Check after processing: the green court drawn on 8 moments of the game. "
                        "It should sit on the real lines in all of them."),
]

# The click page that was started last: for which game, which step, whether "Close" was just
# pressed (it is still shutting down), and whether it was closed from here at all.
_click_page = {"video": None, "title": "", "closing": False, "closed_here": False}


def court_marked(video):
    """True when the two steps the positions need (1 and 2) are both saved for this game."""
    return all((data_folder(video) / saved).exists() for _, saved, _, _ in COURT_PAGES[:2])


def step_state(video, saved_file):
    """"not done yet", "done", or done with stand-in marks, for one court step of one game."""
    saved = read_json(data_folder(video) / saved_file)
    if saved is None:
        return "not done yet"
    if isinstance(saved, dict) and saved.get("provisional"):
        return "done only with stand-in marks that are not yours"
    return "done"


def click_page_html():
    """The box about the click page: how to open it while it is there, and how it ended afterwards."""
    if not _click_page["video"]:
        return ""
    title = html.escape(f"{_click_page['title']} ({config.video_stem(_click_page['video'])})")
    printed = tail(dash_common.DASH_DIR / "logs" / f"{CLICK_JOB}.log", size=20_000)
    lines = [line.strip() for line in printed.splitlines() if line.strip()]
    if dash_common.job_running(CLICK_JOB):
        if _click_page["closing"]:
            return box(f"<b>Closing the click page: {title}...</b>")
        address = re.search(r"http://127\.0\.0\.1:\d+", printed)      # the click page prints its own address
        if not address:
            return box(f"<b>Getting the click page ready: {title}</b><br>{html.escape(lines[-1] if lines else '...')}")
        return box(f"<b>The click page is ready: {title}</b><br><br>"
                   f'<a href="{address[0]}" target="_blank" style="{LINK_STYLE}">Open the click page (in a new tab)</a>'
                   "<br><br>When you are done there, press \"Save and close\" on that page and come back to this one.")
    if dash_common.job_exit_code(CLICK_JOB) == 0:
        return box(f"<b>Saved: {title}.</b> Go on with the next step.")
    if _click_page["closed_here"]:
        return dash_common.warning(f"Not saved: {_click_page['title']} was closed without saving. Nothing was changed.")
    # It ended by itself: its own last words say why (without the lines about its address, which is gone).
    said = [line for line in lines if "http://" not in line and "Save and close" not in line]
    return dash_common.warning(f"Not saved: {_click_page['title']} was closed without saving, or could not start. "
                               "Nothing was changed.\n" + "\n".join(said[-3:]))


def refresh_court(video):
    """Everything the Court set-up tab shows: (which steps are done, the click-page box, the check pictures, the timer).

    Like refresh_games(): also called by the tab's timer, which runs while a click page is open.
    """
    watching = dash_common.job_running(CLICK_JOB)
    if not dash_common.sd_card_ok():
        return dash_common.warning(NO_CARD), "", "", gr.Timer(active=False)
    if not video:
        return dash_common.note(NO_GAME), click_page_html(), "", gr.Timer(active=watching)
    states = [step_state(video, saved) for _, saved, _, _ in COURT_PAGES]
    if "stand-in" in states[1]:
        states[1] += ": please do it again"         # step 3 is optional, so only step 2 asks for this
    done = "".join(f"<li><b>{html.escape(title)}</b>: {state}</li>"
                   for (_, _, title, _), state in zip(COURT_PAGES, states))
    if not court_marked(video):
        after = "Do steps 1 and 2 in order; step 3 can wait."
    elif "stand-in" in states[1]:
        after = "Step 2 was not done by you. Do it, then press Process this game in the Games tab."
    else:
        after = "Steps 1 and 2 are saved. Now go to the Games tab and press Process this game."
    status = box(f"<b>Court set-up for {html.escape(config.video_stem(video))}</b>"
                 f'<ul style="margin:6px 0 6px 22px; list-style:disc;">{done}</ul>{after}')
    pictures = "".join(picture_html(output_folder(video) / name, caption) for name, caption in CHECK_PICTURES
                       if (output_folder(video) / name).is_file())
    return status, click_page_html(), pictures, gr.Timer(active=watching)


def open_click_page(step, video):
    """Start the click page of one step (0, 1 or 2) for the chosen game, as a program of its own."""
    script, _, title, _ = COURT_PAGES[step]
    problem = ""
    if not dash_common.sd_card_ok():
        problem = NO_CARD
    elif not video or not config.video_path(video).is_file():
        problem = NO_GAME
    else:
        # each step works on what the steps before it saved
        missing = [earlier for _, saved, earlier, _ in COURT_PAGES[:min(step, 2)]
                   if not (data_folder(video) / saved).exists()]
        if missing:
            problem = f"Do this first: {missing[0]}."
    if problem:
        yield gr.skip(), dash_common.warning(problem), gr.skip(), gr.skip()
        return
    if dash_common.job_running(CLICK_JOB):
        # One click page at a time. An open one is never closed from here: what was clicked on it
        # is only saved by its own "Save and close" button, so closing it would throw the clicks away.
        if _click_page["video"] == video and _click_page["title"] == title:
            gr.Info("This click page is already open (or still getting ready). The link to it is in the box near the "
                    "top of this tab.", duration=15, title="Already open")
        else:
            gr.Warning(f"{_click_page['title']} ({config.video_stem(_click_page['video'])}) is still open. Press \"Save "
                       "and close\" on that page first, or press \"Close the click page without saving\" here.",
                       duration=20, title="One click page at a time")
        yield refresh_court(video)
        return
    dash_common.start_job(CLICK_JOB, [sys.executable, f"src/{script}", "--video", video])
    _click_page.update(video=video, title=title, closed_here=False)
    yield refresh_court(video)                      # switches the timer on


def close_click_page(video):
    """Close the click page that is open, without saving."""
    _click_page["closing"] = True
    yield gr.skip(), click_page_html(), gr.skip(), gr.skip()        # says "Closing..."
    try:
        if dash_common.job_running(CLICK_JOB):
            _click_page["closed_here"] = True
        dash_common.stop_job(CLICK_JOB, wait=60)        # a click page can take half a minute to shut down
    finally:
        _click_page["closing"] = False
    yield refresh_court(video)


# ---------------------------------------------------------------- Roster

ROSTER_EXAMPLE = config.PROJECT_ROOT / "roster.example.json"
ROSTER_BACKUP = config.ROSTER_PATH.with_name(config.ROSTER_PATH.name + ".bak")


def roster_usable(saved):
    """True when the contents of roster.json have the form the tab can show: two teams."""
    return isinstance(saved, dict) and len(saved) == 2 and all(isinstance(team, dict) for team in saved.values())


def load_roster():
    """The roster as two teams: [(key, team name, [[jersey number, player name], ...]), ...].

    From roster.json. If that is not there (or cannot be understood), two empty teams
    in the shape of roster.example.json: its keys and team names, but not its sample player.
    """
    saved = read_json(config.ROSTER_PATH)
    if not roster_usable(saved):
        example = read_json(ROSTER_EXAMPLE) or {"home": {"name": "Home"}, "away": {"name": "Away"}}
        saved = {key: {"name": team.get("name", key), "players": {}} for key, team in example.items()}
    teams = []
    for key, team in saved.items():
        players = team.get("players") if isinstance(team.get("players"), dict) else {}
        rows = sorted(([str(number), str(name)] for number, name in players.items()),
                      key=lambda row: (int(row[0]) if row[0].isdigit() else 100, row[0]))
        teams.append((key, str(team.get("name", key)), rows))
    return teams


def roster_boxes():
    """What the Roster tab shows: (team 1 name, team 1 players, team 2 name, team 2 players, a message).

    The players of a team are plain text, one player per line: the jersey number, a
    space, the name ("12 First Player"). (An editable table was tried first: the browser lost the
    first character typed into a cell.)
    """
    shown = []
    for _, name, rows in load_roster():
        shown += [name, "\n".join(f"{number} {player}".strip() for number, player in rows)]
    message = ""
    if config.ROSTER_PATH.exists() and not roster_usable(read_json(config.ROSTER_PATH)):
        message = dash_common.warning(
            "The roster file (roster.json) is there, but it is damaged or does not hold exactly two teams, so it is "
            f"shown empty here. If you save now, the old file is first kept as {ROSTER_BACKUP.name} next to it.")
    return (*shown, message)


def check_roster(names, texts):
    """Check what was typed. Returns (the roster as it goes into roster.json, a list of plain problems)."""
    roster, problems = {}, []
    for (key, _, _), name, text in zip(load_roster(), names, texts):
        name = (name or "").strip()
        if not name:
            problems.append("Every team needs a name (for example Home and Away).")
            name = key
        players = {}
        for line in (text or "").splitlines():
            if not line.strip():
                continue                        # an empty line
            # the number is the first word; "#12", "12," and "12." are read as 12
            number, _, player = line.strip().partition(" ")
            number, player = number.strip("#,.:;"), player.strip(" -,")
            if not number.isdigit():
                problems.append(f"{name}: the line \"{line.strip()}\" does not start with a jersey number. "
                                "Write the number first, then the name, like:  12 First Player")
            elif not re.fullmatch(r"0|00|[1-9]\d?", number):
                problems.append(f"{name}: the jersey number \"{number}\" cannot be right. It must be a number from 0 to "
                                "99, written as it is on the jersey (5, not 05).")
            elif number in players:
                problems.append(f"{name}: number {number} is there twice. Each number can be used once in a team.")
            else:
                players[number] = player
        roster[key] = {"name": name, "players": players}
    return roster, problems


def save_roster(name_1, text_1, name_2, text_2):
    """"Save the roster" was pressed: check it, then write roster.json."""
    roster, problems = check_roster([name_1, name_2], [text_1, text_2])
    if problems:
        return dash_common.warning("Not saved. Please put this right first:\n" + "\n".join(problems))
    old = read_json(config.ROSTER_PATH)
    if roster == old:
        return dash_common.note("Nothing has changed since the roster was last saved.")
    try:
        if config.ROSTER_PATH.exists() and not roster_usable(old):
            shutil.copyfile(config.ROSTER_PATH, ROSTER_BACKUP)      # a file the tab could not show is not thrown away
        config.save_json(config.ROSTER_PATH, roster, indent=2)      # writes, then reads back to check
    except OSError as problem:
        return dash_common.warning(f"The roster could not be saved: {problem}")
    # The numbers are shown back, so that a mistyped one is seen at once.
    saved = "<br>".join(f"{html.escape(team['name'])}: " + (", ".join(team["players"]) or "no players")
                        for team in roster.values())
    count = sum(len(team["players"]) for team in roster.values())
    return box(f"<b>Saved: {count} player{'' if count == 1 else 's'}.</b> The jersey numbers now on the roster:<br>{saved}<br>"
               "The names stay in the file roster.json on this laptop.<br>"
               "To put the names into a game's stats, press <b>Process this game</b> in the Games tab: for a game that "
               "was finished, only the last step (Stats) is done again.")


# ---------------------------------------------------------------- Results

STATS_HEADERS = ["Team", "No", "Name", "Seen", "On map", "Distance (ft)", "Speed (ft/s)", "Avg spot (x, y ft)",
                 "Left %", "Mid %", "Right %", "Paint %", "Tags"]
STATS_TYPES = ["str", "str", "str", "str", "str", "number", "number", "str", "number", "number", "number", "number", "number"]
STATS_GUIDE = """
<b>Reading the table.</b> Nothing here is "minutes played": the camera follows the play, so a player who is out of the
picture is not counted.
<ul style="margin:6px 0 0 22px; list-style:disc;">
<li><b>Seen</b>: time the player was seen on camera (minutes:seconds).</li>
<li><b>On map</b>: time the player also had a position on the court (feet inside the picture).</li>
<li><b>Distance</b>: feet covered while on the map. Gaps are not filled in.</li>
<li><b>Speed</b>: feet per second over the steps that were counted (10 ft/s is about 7 mph).</li>
<li><b>Avg spot</b>: average position in court feet: x from the left baseline as the camera sees it, y from the near sideline.</li>
<li><b>Left / Mid / Right</b>: share of the on-map time in each third of the court, as the camera sees it. The tool does
    not know which hoop a team attacks.</li>
<li><b>Paint</b>: share of the on-map time inside either lane.</li>
<li><b>Tags</b>: the tool gives a person a new tag ("id 7" in the videos) each time it loses and finds them again.
    This is how many tags were joined into this player, by team and jersey number.</li>
<li><b>No</b>: the jersey number was read from the video by the tool and can be wrong. The check sheets in the crops
    folder show what it read.</li>
<li><b>Distance and Speed</b> are estimates. They come out too high for players standing close together, and the
    settings behind them were tuned on one 20-second clip.</li>
</ul>
There are no ball numbers (possession, passes, shots): the tool cannot find the ball reliably yet.
"""


def stats_rows(result):
    """The table rows: one per player who has both a team and a jersey number (result = the contents of stats.json)."""
    rows = []
    for row in result["players"]:
        if row["identified"]:
            spot = "-" if row["avg_x_ft"] is None else f"{row['avg_x_ft']:.0f}, {row['avg_y_ft']:.0f}"
            rows.append([row["team"], row["number"], row["name"] or "-", stats_stage.clock(row["seen_s"]),
                         stats_stage.clock(row["on_map_s"]), round(row["distance_ft"]), row["avg_speed_ft_s"], spot,
                         row["left_third_pct"], row["middle_third_pct"], row["right_third_pct"], row["paint_pct"],
                         len(row["tracking_ids"])])
    return rows


def stats_notes(result):
    """What main.py prints under its table, as HTML: who is not in the table, team spacing, things to check."""
    clock = stats_stage.clock
    lines = [f"The players in the table account for <b>{result['identified_share_pct']}%</b> of all the time players "
             "were seen. The rest belongs to tags with no jersey number or no team, and nothing is guessed for those:"]
    for label, index in [(team["team"], team["team_index"]) for team in result["teams"]] + [("no team", None)]:
        others = [row for row in result["players"] if not row["identified"] and row["team_index"] == index]
        if others:
            longest = sorted(others, key=lambda row: -row["seen_s"])[:3]
            lines.append(f"&nbsp;&nbsp;{html.escape(label)}: {len(others)} tag(s) without a number, seen "
                         f"{clock(sum(row['seen_s'] for row in others))} in all. The longest: "
                         + ", ".join(f"tag {row['tracking_ids'][0]} ({clock(row['seen_s'])})" for row in longest)
                         + " (a tag is the \"id\" number in the videos)")
    lines.append("<b>Spacing</b>: the average distance between a team's players that are on the map together.")
    for team in result["teams"]:
        label = html.escape(team["team"])
        if team["roster_name"]:
            label += f" (taken to be \"{html.escape(team['roster_name'])}\" on the roster)"
        if team["spacing_ft"] is None:
            lines.append(f"&nbsp;&nbsp;{label}: never two players on the map at the same time")
        else:
            lines.append(f"&nbsp;&nbsp;{label}: {team['spacing_ft']:.1f} ft (over {clock(team['spacing_seconds'])} "
                         "with two or more on the map)")
    for row in result["players"]:
        if row["identified"] and row["two_places_frames"]:
            lines.append(f"<b>Check:</b> {html.escape(row['team'])} {html.escape(row['number'])} was in two places at "
                         f"once ({row['two_places_frames']} times). One of its tags probably has a wrong number.")
    return "<br>".join(lines)


def position_maps(video):
    """The position-map pictures of a game: [(team, picture file), ...].

    stats.py used to call these pictures heatmap_<team>.png. Such an older picture is
    still shown, as a position map, when the game has no newer one for that team.
    """
    maps = {}
    for prefix in ("position_map_", "heatmap_"):
        for path in sorted(output_folder(video).glob(prefix + "*.png")):
            maps.setdefault(path.stem[len(prefix):], path)
    return sorted(maps.items())


def refresh_results(video):
    """Everything the Results tab shows: (warnings and the videos, the stats table, the notes and the position maps)."""
    if not dash_common.sd_card_ok():
        return dash_common.warning(NO_CARD), [], ""
    if not video:
        return dash_common.note(NO_GAME), [], ""
    name, out = html.escape(config.video_stem(video)), output_folder(video)
    result = read_json(data_folder(video) / "stats.json")
    top, bottom, rows = "", "", []

    if dash_common.is_running(video):
        top += dash_common.note("This game is being processed now. What is shown here may be from an earlier run.")
    elif result and dash_common.stage_statuses(video)["stats"] != "done":
        top += dash_common.warning("Something has changed since these stats were made (the roster, the court marks or "
                                   "an earlier step). Press Process this game in the Games tab to bring them up to date.")
    if result and any("PROVISIONAL" in warning for warning in result.get("warnings", [])):
        top += dash_common.warning(
            "PROVISIONAL POSITIONS. The court of this game was marked with stand-in clicks, not by you. Every number "
            "that depends on where a player stood (On map, Distance, Speed, Avg spot, the court shares, the position "
            "maps and the top-down video) is provisional until you do step 2 in the Court set-up tab yourself and "
            "process the game again.")

    videos = [(out / "tactical.mp4", "Top-down view: the game above a court diagram, one dot per player"),
              (out / "tracked.mp4", "Tracked video: a box and a tag on every player and referee")]
    players = "".join(video_html(path, caption) for path, caption in videos if path.is_file())
    if players:
        top += f'<div style="display:flex; gap:18px; flex-wrap:wrap; margin-top:12px;">{players}</div>'

    if not result:
        top += dash_common.note(f"There are no stats for {config.video_stem(video)} yet. Process the game in the Games "
                                "tab; the stats are the last step." + ("" if players else " There are no videos yet either."))
        return top, [], ""
    court = result["court"]
    top += box(f"<b>Player numbers for {name}</b> &mdash; {stats_stage.clock(result['video_seconds'])} of video, court "
               f"{court['length_ft']} x {court['width_ft']} ft. <b>NOT minutes played</b>: \"Seen\" is time seen on camera.")
    rows = stats_rows(result)
    if not rows:
        bottom += dash_common.note("No player has both a team and a jersey number yet, so the table is empty.")
    elif all(row[2] == "-" for row in rows):
        bottom += dash_common.note("No names are shown (\"-\"). To get names: fill in the Roster tab, then press "
                                   "Process this game in the Games tab. A name only appears when the numbers that were "
                                   "read fit one roster team clearly better than the other.")
    bottom += box(stats_notes(result))
    # the stages' own warnings; the provisional one is said in our own words above
    for warning in result.get("warnings", []):
        if "PROVISIONAL" not in warning:
            bottom += dash_common.warning(warning)
    csv_file = data_folder(video) / "stats.csv"
    if csv_file.is_file():
        bottom += box("<b>The same table for Excel</b> (with a row for every tag that has no number): open this file "
                      f"in Windows:<br><code style=\"font-size:17px;\">{html.escape(pipeline.windows_path(csv_file) or str(csv_file))}</code>")
    bottom += box(STATS_GUIDE)

    maps = position_maps(video)
    if maps:
        bottom += box("<b>Position maps</b>: where each team's players <b>stood</b> while they were in the picture with "
                      "their feet in view; darker means more time. These are not scoring maps: where points were scored "
                      "from is in the Shots tab. The strip nearest the camera is usually empty, because feet there are "
                      "below the picture.")
        pictures = "".join('<div style="flex:1; min-width:420px;">'
                           + picture_html(path, f"Position map: {team.replace('_', ' ')} team") + "</div>"
                           for team, path in maps)
        bottom += f'<div style="display:flex; gap:18px; flex-wrap:wrap;">{pictures}</div>'
    return top, rows, bottom


# ---------------------------------------------------------------- the tabs

def build(game):
    """Add the tabs Games, Court set-up, Roster and Results to the "View the stats" page.

    dashboard.py calls this once, inside an open gr.Tabs(). game is the "Game" box at the
    top of the page: its value is the chosen video as main.py wants it ("clips/game1.mp4"),
    or None when there are no videos. Its "change" event fires when the page opens and
    whenever the coach picks another game, and each tab fills itself on it.
    """
    clips = pipeline.windows_path(config.CLIPS_DIR) or config.CLIPS_DIR

    with gr.Tab("Games") as games_tab:
        # The buttons, the "are you sure?" question and the progress come first, so that what a
        # button does shows without scrolling on a small laptop screen. The list is below them.
        chosen = gr.HTML(dash_common.note("No game is chosen yet."))
        with gr.Row():
            process = gr.Button("Process this game", variant="primary", elem_classes="hoops-big")
            stop = gr.Button("Stop", elem_classes="hoops-big")
            refresh = gr.Button("Refresh the list", elem_classes="hoops-big")
        with gr.Group(visible=False) as sure:
            sure_text = gr.HTML()
            with gr.Row():
                sure_yes = gr.Button("Yes, start now", variant="primary", elem_classes="hoops-big")
                sure_no = gr.Button("No, not now", elem_classes="hoops-big")
        progress = gr.HTML()
        table = gr.Dataframe(headers=["Game", "Length", "Status"] + [STAGE_NAMES[stage] for stage in pipeline.STAGES]
                             + ["Court marked"], value=[], interactive=False, wrap=True, buttons=[])
        gr.HTML(dash_common.warning(
            "A full game takes many hours on this laptop: plan for a day or two. The laptop must stay switched on, "
            "awake and plugged in, and the Hoops Tracker window must stay open. You can close this browser page and "
            "come back to it: the work goes on."))
        gr.HTML(dash_common.note(
            "A new game, from start to finish:\n"
            f"1. Copy the video file into {clips} with Windows Explorer. Press \"Refresh the list\" and choose it in "
            "the Game box at the top.\n"
            "2. Fill in the Roster tab (jersey numbers and names), if you have them. Best before step 3: the number "
            "reader uses the roster while it reads the game.\n"
            "3. Press \"Process this game\". It finds the players, the teams and the jersey numbers (many hours), and "
            "then waits for the court.\n"
            "4. Mark the court in the Court set-up tab. You can do this while step 3 is still running.\n"
            "5. Press \"Process this game\" again. It works out the positions, the top-down video and the stats.\n"
            "6. Look at the Results tab.\n"
            "7. Tag the shots in the Shots tab, for the points table and the scoring heat map."))
        games_timer = gr.Timer(3, active=False)

        games_parts = [chosen, table, progress, games_timer]
        start_parts = [progress, sure, sure_text, games_timer]
        game.change(refresh_games, inputs=game, outputs=games_parts)
        games_tab.select(refresh_games, inputs=game, outputs=games_parts)
        refresh.click(refresh_games, inputs=game, outputs=games_parts)
        games_timer.tick(refresh_games, inputs=game, outputs=games_parts)
        process.click(press_process, inputs=game, outputs=start_parts)
        sure_yes.click(start_processing, inputs=game, outputs=start_parts)      # switches the timer on
        sure_no.click(lambda: gr.Group(visible=False), outputs=sure)
        stop.click(press_stop, inputs=game, outputs=progress).then(refresh_games, inputs=game, outputs=games_parts)

    with gr.Tab("Court set-up") as court_tab:
        gr.HTML(dash_common.note(
            "The camera moves, so the tool has to be shown once per game where the court is. Each step opens a small "
            "click page in a new browser tab. You can do this before the game is processed or while it is being "
            "processed. If the camera was moved during the recording, or it is another game, do the steps again."))
        court_status = gr.HTML()
        click_page = gr.HTML()              # the link to the click page appears here, near the top (the buttons say so)
        step_buttons = []
        for _, _, title, guide in COURT_PAGES:
            gr.HTML(box(f"<b>{html.escape(title)}</b><br>{html.escape(guide)}"))
            step_buttons.append(gr.Button(f"Open {title[0].lower()}{title[1:]}", variant="primary", elem_classes="hoops-big"))
        with gr.Row():
            close_page = gr.Button("Close the click page without saving")
            check_again = gr.Button("Refresh this page")
        gr.HTML(dash_common.note(
            "After the game has been processed, look at the last check picture below. If the green court is missing "
            "or off in some of its moments, open step 1 again, add a reference picture at such a time, and process "
            "the game again.\n"
            "The click pages are small programs of their own, so they look plainer than this page."))
        court_pictures = gr.HTML()
        court_timer = gr.Timer(2, active=False)

        court_parts = [court_status, click_page, court_pictures, court_timer]
        game.change(refresh_court, inputs=game, outputs=court_parts)
        court_tab.select(refresh_court, inputs=game, outputs=court_parts)
        check_again.click(refresh_court, inputs=game, outputs=court_parts)
        court_timer.tick(refresh_court, inputs=game, outputs=court_parts)
        close_page.click(close_click_page, inputs=game, outputs=court_parts)
        for step, button in enumerate(step_buttons):
            button.click(functools.partial(open_click_page, step), inputs=game, outputs=court_parts)

    with gr.Tab("Roster"):
        gr.HTML(dash_common.note(
            "Type each team's players, one player on each line: the jersey number, a space, then the name. "
            "For example:   12 First Player\n"
            "The names are kept in the file roster.json on this laptop only: they never leave it, and they are never "
            "put on GitHub.\n"
            "The roster is the same for every game. A number without a name is fine (for the other team the numbers "
            "alone help). The number reader also uses the roster, but only at the moment it reads a game: fill the "
            "roster in before you process a game when you can."))
        roster_boxes_shown = []
        with gr.Row():
            for label in ("First team", "Second team"):
                with gr.Column():
                    roster_boxes_shown.append(gr.Textbox(label=f"{label}: name"))
                    roster_boxes_shown.append(gr.Textbox(
                        label=f"{label}: players, one on each line (number, space, name)", lines=15, max_lines=30,
                        placeholder="12 First Player\n5 Second Player\n23"))
        with gr.Row():
            save = gr.Button("Save the roster", variant="primary", elem_classes="hoops-big")
            reload_roster = gr.Button("Undo my changes (show the saved roster again)", elem_classes="hoops-big")
        roster_message = gr.HTML()

        # Filled once, when the page opens (gr.on without a trigger means "on page load"). Not on
        # every change of game or tab: that would wipe what the coach has typed but not saved yet.
        gr.on(fn=roster_boxes, outputs=roster_boxes_shown + [roster_message])
        reload_roster.click(roster_boxes, outputs=roster_boxes_shown + [roster_message])
        save.click(save_roster, inputs=roster_boxes_shown, outputs=roster_message)

    with gr.Tab("Results") as results_tab:
        refresh_shown = gr.Button("Refresh the results")
        results_top = gr.HTML()
        stats_table = gr.Dataframe(headers=STATS_HEADERS, datatype=STATS_TYPES, value=[], interactive=False)
        results_bottom = gr.HTML()

        results_parts = [results_top, stats_table, results_bottom]
        game.change(refresh_results, inputs=game, outputs=results_parts)
        results_tab.select(refresh_results, inputs=game, outputs=results_parts)
        refresh_shown.click(refresh_results, inputs=game, outputs=results_parts)
