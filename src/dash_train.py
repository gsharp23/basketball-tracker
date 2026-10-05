"""The "Train the model" tabs of the dashboard: Label the ball, Export, Models.

The models this project has cannot find the ball reliably. These tabs are the first
step toward one that can: the coach shows where the ball is in frames of his own games,
the answers are packed into a zip, and a small model is trained from it on Google
Colab (colab/train_ball_model.ipynb).

    Label the ball   pick frames from the chosen game, then one frame at a time: click the
                     ball (once in the whole picture, once exactly in the enlarged one),
                     or say that no ball can be seen
    Export           choose the game kept aside for testing, and make the zip
    Models           the model files on the SD card and which one is in use

What is saved, and the zip, are the work of ball_labels.py; this file is only the page.
The functions under "what the page does on each click" are plain functions on the
"session" dictionary, so they can be tested without a browser.
"""
import html
from datetime import datetime

import cv2

import ball_labels
import click_tools
import config
import dash_common
import main as pipeline         # only for windows_path()
from dash_common import gr

MY_BOX = (0, 255, 255)          # yellow: the box the coach placed
GUESS_BOX = (255, 255, 255)     # thin white: the detector's guess
ZOOM_FRAME = (255, 255, 0)      # the area that is shown enlarged
SIZE_STEP = 2                   # pixels per press of Smaller / Larger

# What the coach is looking at right now. One dictionary for the whole program: the
# dashboard has one user, and every answer is saved to the SD card the moment it is given.
# A second browser tab shares it, so each tab also remembers what it shows (on_show), and
# a click from a tab that shows something else is refused (see only_if_current).
session = {"stem": None, "frames": [], "position": 0, "zoom": None, "hints": {}, "history": [], "fps": 30.0,
           "picture": None, "picture_path": None}


def load_labels():
    """The saved answers. A damaged file, or the SD card gone, becomes a plain message on the page."""
    try:
        return ball_labels.load_labels()
    except ValueError as problem:
        raise gr.Error(str(problem), duration=None)


def save_labels(labels):
    """Save the answers. A write that fails (SD card full, gone or damaged) becomes a plain message on the page."""
    try:
        ball_labels.save_labels(labels)
    except OSError as problem:
        raise gr.Error(f"The answer could not be saved: {problem} Check the SD card and press the button again.",
                       duration=None)


def windows_name(path):
    """A file or folder as Windows names it (D:\\...), because that is where the coach will look for it."""
    return pipeline.windows_path(path) or str(path)


# ---------------------------------------------------------------- drawing

def draw_box(picture, cx, cy, size, colour, thickness):
    half = size / 2
    cv2.rectangle(picture, (int(round(cx - half)), int(round(cy - half))), (int(round(cx + half)), int(round(cy + half))),
                  colour, thickness, cv2.LINE_AA)


def draw_pictures(picture, entry, guess, zoom):
    """The two pictures the page shows: the whole frame, and the enlarged area (None if there is none).

    entry is the saved answer for this frame (or None), guess the detector's ball box
    (or None). A guess is only drawn while the coach has not answered.
    """
    box = entry.get("box") if entry else None
    whole = picture.copy()
    if box:
        # The whole picture is shown small, so the box alone would be hard to see: a ring is drawn round it.
        draw_box(whole, box["cx"], box["cy"], box["w"], MY_BOX, 3)
        cv2.circle(whole, (int(box["cx"]), int(box["cy"])), int(box["w"] * 1.5) + 12, MY_BOX, 4, cv2.LINE_AA)
    elif guess and not entry:
        draw_box(whole, *guess, GUESS_BOX, 2)
        cv2.circle(whole, (int(guess[0]), int(guess[1])), int(guess[2] * 1.5) + 12, GUESS_BOX, 2, cv2.LINE_AA)
        click_tools.draw_label(whole, "a guess", guess[0] + guess[2] * 1.5 + 18, guess[1], GUESS_BOX, 1.0)
    if zoom is None:
        return click_tools.to_browser(whole), None

    x0, y0 = zoom
    height, width = picture.shape[:2]
    cv2.rectangle(whole, (x0, y0), (x0 + width // click_tools.ZOOM, y0 + height // click_tools.ZOOM), ZOOM_FRAME, 3)
    enlarged = click_tools.enlarge(picture, x0, y0)
    if box:
        draw_box(enlarged, *click_tools.full_to_zoom(x0, y0, box["cx"], box["cy"]), box["w"] * click_tools.ZOOM, MY_BOX, 2)
    elif guess and not entry:
        x, y = click_tools.full_to_zoom(x0, y0, guess[0], guess[1])
        draw_box(enlarged, x, y, guess[2] * click_tools.ZOOM, GUESS_BOX, 2)
        click_tools.draw_label(enlarged, "the detector's guess (often wrong)", x - 280, y - guess[2] * 2 - 16, GUESS_BOX, 1.1)
    return click_tools.to_browser(whole), click_tools.to_browser(enlarged)


# ---------------------------------------------------------------- what the page does on each click
# Each returns what the page shows next: (whole picture, enlarged picture, the text box, the "guess" button,
# and what is on show, for the browser tab to remember: see on_show).

OUT_OF_DATE = ("This page was out of date: labelling was opened or used in another browser tab. Nothing was saved "
               "for that click. The page now shows the current frame: answer again.")


def on_show():
    """A short text for what the session shows now: the game, the frame and the enlarged area (None if no frame).

    Every browser tab keeps the text of what it was last sent. When a tab's text is not
    this one, the tab shows an older picture than the one an answer would be saved for.
    """
    if nothing_to_label():
        return None
    frame = session["frames"][session["position"]][0]
    return f"{ball_labels.frame_key(session['stem'], frame)} enlarged at {session['zoom']}"


def only_if_current(action):
    """Wrap a button's function so that it only runs when the tab that was clicked shows the current frame.

    The wrapped function takes what the tab shows (its on_show text) as its first input.
    """
    def checked(tab_shows, *inputs):
        if tab_shows != on_show():
            return show(OUT_OF_DATE)
        return action(*inputs)
    return checked


def current(labels):
    """(frame number, picture file, saved answer or None, the detector's guess or None) for the frame on show."""
    frame, path = session["frames"][session["position"]]
    entry = labels["frames"].get(ball_labels.frame_key(session["stem"], frame))
    return frame, path, entry, session["hints"].get(frame, {}).get("guess")


def frame_picture():
    """The picture of the frame on show. It is kept in memory until another frame is shown."""
    path = session["frames"][session["position"]][1]
    if session["picture_path"] != path:
        picture = cv2.imread(str(path))
        if picture is None:
            raise gr.Error(f"The picture {path} cannot be read (the SD card has damaged files before). "
                           "Delete that file and press \"Pick frames from this game\" again.", duration=None)
        session["picture"], session["picture_path"] = picture, path
    return session["picture"]


def progress_line(labels):
    """ "37 of 150 labelled ..." for the chosen game."""
    n = ball_labels.count_answers(labels, session["stem"], session["frames"])
    return (f"{n['ball'] + n['no ball']} of {n['frames']} labelled: {n['ball']} with the ball, {n['no ball']} with no ball. "
            f"{n['skipped']} skipped. {n['to do']} still to do.")


def text_box(big, small=""):
    """The box above the pictures: what to do now in large letters, and where we are below it."""
    small = html.escape(small).replace("\n", "<br>")
    return f'<div class="hoops-note"><b style="font-size: 21px">{html.escape(big)}</b><br>{small}</div>'


def show(message=None):
    """Everything the page shows for the frame we are on. message replaces the usual "what to do now"."""
    if not session["stem"]:
        return None, None, text_box("Choose a game in the Game box above.",
                                    "If the box is empty, put a game video in the clips folder first."), \
            gr.update(interactive=False), None
    if not session["frames"]:
        return None, None, text_box(f"No frames have been picked from {session['stem']} yet.",
                                    "Press \"Pick frames from this game\" above. The game does not have to be processed first."), \
            gr.update(interactive=False), None
    labels = load_labels()
    frame, _, entry, guess = current(labels)

    if entry is None:
        answer = "no answer yet"
        to_do = "Click the ball in the large picture. If you cannot see a ball, press \"No ball visible\"."
        if guess:
            to_do = ("Is the white box on the ball? Then press \"Use the detector's guess\". "
                     "If not, click the ball in the large picture, or press \"No ball visible\".")
    elif entry["answer"] == "ball":
        answer = f"the ball is marked (yellow box, {entry['box']['w']} pixels wide)"
        to_do = "Does the yellow box fit the ball? Use Smaller / Larger, or click again to move it. Then press \"Next frame\"."
    else:
        answer = "no ball visible" if entry["answer"] == "no ball" else "skipped"
        to_do = "This frame has an answer. Click the ball to change it, or press \"Next frame\"."
    seconds = int(frame / session["fps"])
    where = (f"Frame {session['position'] + 1} of {len(session['frames'])} from {session['stem']}, "
             f"{seconds // 60}:{seconds % 60:02d} into the video. Your answer: {answer}.")
    whole, enlarged = draw_pictures(frame_picture(), entry, guess, session["zoom"])
    return whole, enlarged, text_box(message or to_do, where + "\n" + progress_line(labels)), \
        gr.update(interactive=bool(guess) and entry is None), on_show()


def go_to(position, labels):
    """Move to a frame. The enlarged picture opens on the ball if it is marked, or on the detector's guess."""
    session["position"] = position
    _, _, entry, guess = current(labels)
    spot = None
    if entry and entry["answer"] == "ball":
        spot = (entry["box"]["cx"], entry["box"]["cy"])
    elif entry is None and guess:
        spot = guess[:2]
    session["zoom"] = None
    if spot:
        _, session["zoom"] = click_tools.zoom_picture(frame_picture(), *spot)


def first_unanswered(labels):
    """Position of the first frame without an answer, or None when every frame has one."""
    for position, (frame, _) in enumerate(session["frames"]):
        if ball_labels.frame_key(session["stem"], frame) not in labels["frames"]:
            return position
    return None


def video_fps(video):
    """Frames per second of a video, to show how far into the game a frame is."""
    capture = cv2.VideoCapture(str(config.video_path(video)))
    fps = capture.get(cv2.CAP_PROP_FPS)
    capture.release()
    return fps if fps and fps > 1 else 30.0


def open_game(video):
    """Start on a game: its first frame without an answer. Runs when the page opens and when another game is chosen.

    Returns what show() returns, plus whether the "Pick frames" part of the page should be
    open: it is when the game has no frames yet, and folded away once there are frames to label.
    """
    shown = start_game(video)
    return *shown, gr.update(open=not session["frames"])


def start_game(video):
    """Fill the session for a game, go to its first frame without an answer, and return what show() returns."""
    before = (session["stem"], session["frames"], session["history"])
    session.update(stem=None, frames=[], position=0, zoom=None, hints={}, history=[], picture=None, picture_path=None)
    if not video:
        return show()
    labels = load_labels()
    session["stem"] = config.video_stem(video)
    session["frames"] = ball_labels.frame_files(session["stem"])
    if not session["frames"]:
        return show()
    if (session["stem"], session["frames"]) == before[:2]:
        session["history"] = before[2]      # the same game was only opened again (a reload, another tab): Undo still works
    session["hints"] = ball_labels.load_hints(session["stem"])
    session["fps"] = video_fps(video)
    position = first_unanswered(labels)
    go_to(position or 0, labels)
    if position is None:
        return show("Every frame picked from this game has an answer. Pick more frames, choose another game, "
                    "or go to the Export tab.")
    return show()


def nothing_to_label():
    """True when there is no frame on show, so a click or a button has nothing to act on."""
    return not session["stem"] or not session["frames"]


def give_answer(labels, answer, box=None):
    """Save an answer for the frame on show, remembering what was there before so that Undo can put it back."""
    frame, _, entry, _ = current(labels)
    height, width = frame_picture().shape[:2]
    ball_labels.set_answer(labels, session["stem"], frame, width, height, answer, box)
    save_labels(labels)
    session["history"].append((session["position"], entry))


def click_whole(x, y):
    """First click, in the whole picture: show the area around it enlarged."""
    if nothing_to_label():
        return show()
    _, session["zoom"] = click_tools.zoom_picture(frame_picture(), x, y)
    return show("Now click EXACTLY on the middle of the ball in the small enlarged picture on the right.")


def click_enlarged(zoom_x, zoom_y):
    """Second click, in the enlarged picture: the middle of the ball. Saved at once, with a first idea of its size."""
    if nothing_to_label() or session["zoom"] is None:
        return show("First click the ball in the large picture.")
    x, y = click_tools.zoom_to_full(*session["zoom"], zoom_x, zoom_y)
    labels = load_labels()
    frame, _, entry, _ = current(labels)
    if entry and entry["answer"] == "ball":
        size = entry["box"]["w"]                    # moving a box that is already there keeps its size
    else:
        height, width = frame_picture().shape[:2]
        players = session["hints"].get(frame, {}).get("players", ())
        size = ball_labels.propose_size(labels, session["stem"], width, height, x, y, players)
    give_answer(labels, "ball", (x, y, size))
    return show()


def change_size(step):
    """Smaller / Larger: change the size of the box on the frame on show."""
    if nothing_to_label():
        return show()
    labels = load_labels()
    _, _, entry, _ = current(labels)
    if not entry or entry["answer"] != "ball":
        return show("There is no box to resize yet. Click the ball first.")
    entry["box"]["w"] = entry["box"]["h"] = max(ball_labels.MIN_BALL_PX, entry["box"]["w"] + step)
    save_labels(labels)
    return show()


def use_guess():
    """Accept the detector's guess as the ball's box. It can still be moved or resized afterwards."""
    if nothing_to_label():
        return show()
    labels = load_labels()
    _, _, entry, guess = current(labels)
    if not guess or entry:
        return show()
    give_answer(labels, "ball", guess)
    return show()


def step_forward(labels):
    """Go on to the next frame; after the last one, to the first frame that still has no answer."""
    if session["position"] + 1 < len(session["frames"]):
        go_to(session["position"] + 1, labels)
        return show()
    position = first_unanswered(labels)
    if position is None:
        return show("That was the last frame, and every frame of this game has an answer. Pick more frames, "
                    "choose another game, or go to the Export tab.")
    go_to(position, labels)
    return show("That was the last frame. This one still has no answer.")


def answer_and_go_on(answer):
    """ "No ball visible" and "Skip (can't tell)": save the answer and show the next frame."""
    if nothing_to_label():
        return show()
    labels = load_labels()
    give_answer(labels, answer)
    return step_forward(labels)


def next_frame():
    if nothing_to_label():
        return show()
    return step_forward(load_labels())


def back():
    """One frame back, to look at it again or change its answer."""
    if nothing_to_label():
        return show()
    if session["position"] == 0:
        return show("This is the first frame.")
    go_to(session["position"] - 1, load_labels())
    return show()


def undo():
    """Take back the answer given last (in this sitting) and show that frame again."""
    if nothing_to_label():
        return show()
    if not session["history"]:
        return show("There is nothing to undo.")
    position, entry_before = session["history"][-1]
    labels = load_labels()
    key = ball_labels.frame_key(session["stem"], session["frames"][position][0])
    if entry_before is None:
        labels["frames"].pop(key, None)
    else:
        labels["frames"][key] = entry_before
    save_labels(labels)
    session["history"].pop()
    go_to(position, labels)
    return show("Taken back. This frame is as it was before your last answer.")


def pick_frames(video, how_many):
    """The "Pick frames" button: save frames from the chosen game, showing progress."""
    if not video:
        yield dash_common.warning("Choose a game in the Game box first.")
        return
    if not how_many or how_many < 1:
        yield dash_common.warning("Type how many frames you want from this game, for example 150.")
        return
    stem = config.video_stem(video)
    try:
        for progress in ball_labels.pick_frames(video, how_many):
            yield dash_common.note(f"Picking frames from {stem}: {progress['done']} of {progress['total']} saved, "
                                   f"{ball_labels.nice_time(progress['seconds_left'])}...")
    except (ValueError, OSError) as problem:
        yield dash_common.warning(str(problem))
        return
    have = len(ball_labels.frame_files(stem))
    if progress["total"] == 0 and have >= how_many:
        text = f"{stem} already has {have} frames. To add more, type a larger number and press the button again."
    elif have < how_many:
        text = (f"{stem} now has {have} frames. That is all there is room for: two frames are never taken "
                f"less than {ball_labels.MIN_SECONDS_APART:.0f} second apart.")
    else:
        text = f"Done: {progress['done']} new frames saved. {stem} now has {have} frames. Label them below."
    if progress["unreadable"]:
        one = progress["unreadable"] == 1
        text += (f" ({progress['unreadable']} frame{'' if one else 's'} could not be read from the video and "
                 f"{'was' if one else 'were'} left out.)")
    yield dash_common.note(text)


# ---------------------------------------------------------------- the Export tab

def export_view():
    """What the Export tab shows: (the table of games, the "kept for testing" box, the explanation)."""
    labels = load_labels()
    games = ball_labels.games_with_frames()
    _, by_game, explanation = ball_labels.split_frames(labels)
    rows = []
    with_ball = 0
    for stem in games:
        n = ball_labels.count_answers(labels, stem)
        with_ball += n["ball"]
        if n["ball"] + n["no ball"] == 0:
            used_for = "nothing yet (no answers)"
        elif by_game:
            used_for = "the final test only" if stem == labels["test_game"] else "training"
        else:
            used_for = "training and testing (weaker)"
        rows.append([stem, n["frames"], n["ball"], n["no ball"], n["skipped"], n["to do"], used_for])
    if not games:
        explanation = "No frames have been picked yet. Start on the \"Label the ball\" tab."
    else:
        explanation += (f"\nFrames with the ball marked so far: {with_ball}. A useful model needs a few hundred, "
                        "from more than one game and gym if you can.")
    kept = labels["test_game"] if labels["test_game"] in games else ""
    choices = [("No game kept for testing", "")] + [(stem, stem) for stem in games]
    return rows, gr.update(choices=choices, value=kept), dash_common.note(explanation)


def set_test_game(stem):
    """The coach chose which game is kept aside for testing ("" for none)."""
    labels = load_labels()
    labels["test_game"] = stem or None
    save_labels(labels)
    return export_view()


def make_zip():
    """The "Make the zip" button: pack the answered frames, showing progress, then say where the zip is."""
    try:
        for step in ball_labels.export_dataset():
            if "done" in step:
                yield dash_common.note(f"Packing the frames: {step['done']} of {step['total']}...")
            elif "checking" in step:
                yield dash_common.note("Checking the zip by reading it back from the SD card...")
    except (ValueError, OSError) as problem:
        yield dash_common.warning(str(problem))
        return
    result = step["result"]
    counts = result["counts"]
    lines = [f"Made {result['path'].name} ({result['megabytes']:.0f} MB).",
             f"Where it is:  {windows_name(result['path'])}",
             f"Training: {counts['train']['frames']} frames ({counts['train']['ball']} with the ball).  "
             f"Checking during training: {counts['val']['frames']} ({counts['val']['ball']}).  "
             f"Final test: {counts['test']['frames']} ({counts['test']['ball']}).",
             result["explanation"]]
    if result["notebook"]:
        lines.append(f"The Colab notebook is in the same folder:  {windows_name(result['notebook'])}")
    yield dash_common.note("\n".join(lines))


# ---------------------------------------------------------------- the Models tab

def models_table():
    """One row per model file in models/: name, size, date, and what it is used for."""
    rows = []
    for path in sorted(config.MODELS_DIR.glob("*.pt")):
        if path == config.PLAYER_MODEL:
            used_for = "IN USE: finds the players, referees and hoop when a game is processed"
        elif path.name.startswith("ball_model_v"):
            used_for = "A ball model you trained. Not used yet: that is the next stage"
        elif path.name == "ball_detector_model.pt":
            used_for = "An earlier ball model. It was tried and found unreliable, so it is not used"
        else:
            used_for = "Not used when a game is processed"
        info = path.stat()
        rows.append([path.name, f"{info.st_size / 1e6:.0f} MB", datetime.fromtimestamp(info.st_mtime).strftime("%d %b %Y"),
                     used_for])
    return rows


# ---------------------------------------------------------------- the page

def build(game):
    """Add this module's tabs to the "Train the model" page.

    dashboard.py calls this once, while the page is being built, inside an open
    gr.Tabs(). game is the "Game" box at the top of the page: its value is the chosen
    video ("clips/game1.mp4") or None, and its "change" event fires when the page opens
    and whenever the coach picks another game.
    """
    with gr.Tab("Label the ball"):
        with gr.Accordion("Step 1: pick frames from this game", open=True) as pick_area:
            pick_status = gr.HTML(dash_common.note(
                "This saves frames spread over the whole game, for you to answer below. A full game takes a "
                "minute or two, and it does not have to be processed first. To add more frames later, type a "
                "larger number and press the button again."))
            with gr.Row():
                how_many = gr.Number(ball_labels.DEFAULT_FRAMES, label="Frames to have from this game", precision=0,
                                     minimum=1, maximum=2000, scale=1)
                pick_button = gr.Button("Pick frames from this game", variant="primary", elem_classes="hoops-big", scale=2)
        status = gr.HTML(text_box("Choose a game in the Game box above.",
                                  "If the box is empty, put a game video in the clips folder first."))
        on_show_here = gr.State()       # what THIS browser tab shows (the text from on_show)
        with gr.Row():
            # Both pictures are sent full size, so a click is reported in the picture's own pixels (see click_tools).
            # The whole picture gets two thirds of the width, because the ball is small in it. The answer
            # buttons are beside it, so that nothing has to be scrolled between one frame and the next.
            picture = gr.Image(None, label="Whole picture: click the ball (the corner button shows it full screen, to look closer)",
                               interactive=False, format="jpeg", buttons=["fullscreen"], scale=2)
            with gr.Column(scale=1):
                enlarged = gr.Image(None, label="Enlarged: click exactly on the middle of the ball", interactive=False,
                                    format="jpeg", buttons=[])
                no_ball_button = gr.Button("No ball visible", variant="primary", elem_classes="hoops-big")
                skip_button = gr.Button("Skip (can't tell)", elem_classes="hoops-big")
                next_button = gr.Button("Next frame", variant="primary", elem_classes="hoops-big")
        with gr.Row():
            smaller_button = gr.Button("Smaller box")
            larger_button = gr.Button("Larger box")
            guess_button = gr.Button("Use the detector's guess", interactive=False)
            back_button = gr.Button("Back one frame")
            undo_button = gr.Button("Undo my last answer")
        gr.HTML(dash_common.note(
            "The ball counts as visible when you can see any part of it, also in a player's hands. Press \"No ball "
            "visible\" when it is out of the picture or completely hidden: those frames teach the model what is NOT "
            "a ball. Press \"Skip\" only when you cannot tell. Every answer is saved at once."))

        def on_picture(tab_shows, event: gr.SelectData):       # event.index is [x, y] in pixels of the picture
            return click_whole(*event.index) if tab_shows == on_show() else show(OUT_OF_DATE)

        def on_enlarged(tab_shows, event: gr.SelectData):
            return click_enlarged(*event.index) if tab_shows == on_show() else show(OUT_OF_DATE)

        shown = [picture, enlarged, status, guess_button, on_show_here]
        # One click is dealt with at a time ("hoops_label"), so two quick clicks cannot mix up the frame on show.
        one_at_a_time = dict(outputs=shown, show_progress="minimal", concurrency_limit=1, concurrency_id="hoops_label")
        opened = dict(one_at_a_time, outputs=shown + [pick_area])
        game.change(open_game, game, **opened)
        # Every click that changes or moves something says what its tab shows, and is refused if that is out of date.
        picture.select(on_picture, on_show_here, **one_at_a_time)
        enlarged.select(on_enlarged, on_show_here, **one_at_a_time)
        no_ball_button.click(only_if_current(lambda: answer_and_go_on("no ball")), on_show_here, **one_at_a_time)
        skip_button.click(only_if_current(lambda: answer_and_go_on("skipped")), on_show_here, **one_at_a_time)
        next_button.click(only_if_current(next_frame), on_show_here, **one_at_a_time)
        smaller_button.click(only_if_current(lambda: change_size(-SIZE_STEP)), on_show_here, **one_at_a_time)
        larger_button.click(only_if_current(lambda: change_size(SIZE_STEP)), on_show_here, **one_at_a_time)
        guess_button.click(only_if_current(use_guess), on_show_here, **one_at_a_time)
        back_button.click(only_if_current(back), on_show_here, **one_at_a_time)
        undo_button.click(only_if_current(undo), on_show_here, **one_at_a_time)
        pick_button.click(pick_frames, [game, how_many], pick_status, concurrency_limit=1, concurrency_id="hoops_label") \
            .then(open_game, game, **opened)

    with gr.Tab("Export") as export_tab:
        gr.HTML(dash_common.note(
            "This packs every frame you have answered into one zip file on the SD card, in the form the training "
            "program expects. Skipped frames are left out. You can make a new zip whenever you have labelled more."))
        table = gr.Dataframe(headers=["Game", "Frames picked", "Ball marked", "No ball", "Skipped", "Still to do", "Used for"],
                             interactive=False, buttons=[])
        test_game = gr.Dropdown(label="Game kept for testing", choices=[("No game kept for testing", "")], value="",
                                info="None of this game's frames are used for training, so it gives an honest score.",
                                interactive=True, elem_classes="hoops-game", elem_id="hoops-test-game")
        explanation = gr.HTML()
        gr.HTML(dash_common.warning(
            "Privacy: the zip holds frames of the game, and they show the players. Nothing is uploaded by this "
            "tool. If you upload the zip to Google Drive for training, those frames leave this laptop and are "
            "kept by Google. That decision is yours: check what the school and the parents have agreed to first."))
        zip_button = gr.Button("Make the zip for Google Colab", variant="primary", elem_classes="hoops-big")
        zip_status = gr.HTML()
        gr.HTML(dash_common.note(
            "What to do with the zip:\n"
            "1. In Google Drive, make a folder called hoops-tracker and upload the zip into it.\n"
            "2. Open colab.research.google.com, choose File, Upload notebook, and pick train_ball_model.ipynb "
            "(it is put next to the zip).\n"
            "3. In Colab choose Runtime, Change runtime type, T4 GPU. Then Runtime, Run all, and allow it to use "
            "your Drive.\n"
            "4. Leave the Colab tab open until the last step has finished. The folder in Drive then holds "
            "ball_model_v<number>.pt and a text file with its scores. Download the .pt file into the models folder "
            "(see the Models tab)."))

        view = [table, test_game, explanation]
        export_tab.select(export_view, None, view)
        game.change(export_view, None, view)        # fills the tab when the page opens
        test_game.input(set_test_game, test_game, view)
        zip_button.click(make_zip, None, zip_status, concurrency_limit=1, concurrency_id="hoops_label")

    with gr.Tab("Models") as models_tab:
        models = gr.Dataframe(headers=["File", "Size", "Date", "What it is used for"], value=models_table, interactive=False, buttons=[])
        gr.HTML(dash_common.note(
            f"The model files are in  {windows_name(config.MODELS_DIR)}\n"
            f"When a game is processed, {config.PLAYER_MODEL.name} finds the players, referees and hoop. Its \"ball\" "
            "boxes are wrong more often than right, so nothing is worked out from them.\n"
            "A ball model you trained on Colab goes into the same folder, under the name it was given "
            "(ball_model_v1.pt, ball_model_v2.pt, ...). It then shows up in this list.\n"
            "Using a ball model when a game is processed is the NEXT stage of the project and is not built yet. "
            "There is no switch for it here on purpose: until that stage exists, a trained model changes nothing "
            "in the stats."))
        models_tab.select(models_table, None, models)
