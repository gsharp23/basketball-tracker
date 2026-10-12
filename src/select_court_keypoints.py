"""Court set-up, step 2 of 3: click court landmarks so that pictures can be turned into feet.

    python src/select_court_keypoints.py --video clips/game1.mp4

Run select_court.py first. This step needs no player detection.

What you do on the page
    Pick a reference picture, choose a landmark from the list (the small court diagram
    shows which is which), click it in the picture, then click it again exactly in the
    enlarged picture. Do at least 4 landmarks at EACH end of the court the video shows;
    10 to 14 in total is ideal. A landmark hidden in one picture can be clicked in another.
    Use the basketball lines, not the lines of another sport such as volleyball.

What happens with the clicks
    Every click is carried into the "main" reference picture, and ONE court matrix is
    solved from all of them together (court_mapper.solve_court). The table shows how far
    each click is from where all the others say it should be; over 1 ft is flagged.

What it writes
    data/<video>/court_calibration.json            your clicks (the matrices are worked out
                                                   again from the clicks whenever needed)
    outputs/<video>/court_calibration_check.jpg    every reference picture with the court drawn on

Without the page
    --answers FILE    take the clicks from a JSON file:
                      {"landmarks": [{"name": "right_freethrow_far", "view": 0, "px": [660.5, 751.3]}, ...]}
                      "view" is the frame number of the reference picture, "px" the pixel in it.
                      A saved court_calibration.json has this form too and can be given as is.
                      Add "provisional": true if the clicks are a stand-in that the coach should redo.

Next step:  python src/select_hoops.py --video <the same video> (optional), then python src/main.py
"""
import argparse
import sys
import threading

import cv2
import numpy as np

import click_tools
import config
import court_fit
import court_mapper
import court_model
import gyms

DIAGRAM_PX_PER_FT = 10
DIAGRAM_MARGIN = 50
CHECK_TILE = (960, 540)


def parse_args():
    parser = argparse.ArgumentParser(description="Click court landmarks on the reference pictures.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="video file (default clips/test.mp4)")
    parser.add_argument("--answers", help="JSON file with the clicks, instead of the page")
    return parser.parse_args()


# ---------------------------------------------------------------- the list of clicks

def add_landmark(landmarks, name, view, x, y):
    """Store a click. A landmark can be clicked once per picture: clicking it again replaces it."""
    remove_landmark(landmarks, name, view)
    landmarks.append({"name": name, "view": view, "px": [round(float(x), 1), round(float(y), 1)]})


def remove_landmark(landmarks, name, view):
    """Take a landmark off one picture. Returns True if it was there."""
    for mark in landmarks:
        if mark["name"] == name and mark["view"] == view:
            landmarks.remove(mark)
            return True
    return False


def try_solve(setup, landmarks):
    """(fit, None) when the clicks give a court, else (None, the plain reason why not)."""
    court = setup["court"]
    try:
        return court_mapper.solve_court(setup, landmarks, court["length_ft"], court["width_ft"], **court_model.extra(court)), None
    except ValueError as problem:
        return None, str(problem)


def model_of(setup):
    """The 20 landmarks at this court's size and markings."""
    court = setup["court"]
    return court_model.landmarks(court["length_ft"], court["width_ft"], **court_model.extra(court))


def numbers(setup):
    """{landmark name: its number in the list and on the diagram}."""
    return {name: i + 1 for i, name in enumerate(model_of(setup))}


# ---------------------------------------------------------------- drawing

def draw_view(picture, view, landmarks, fit, setup):
    """A copy of a reference picture with the court (once it can be solved) and this picture's clicks."""
    shown = picture.copy()
    court = setup["court"]
    if fit is not None:
        to_main = next(np.array(v["to_main"]) for v in setup["views"] if v["frame"] == view)
        court_model.draw_court(shown, fit["main_to_court"] @ to_main, court["length_ft"], court["width_ft"],
                               **court_model.extra(court))
    number = numbers(setup)
    for i, mark in enumerate(landmarks):
        if mark["view"] != view:
            continue
        x, y = mark["px"]
        text = f"{number[mark['name']]} {mark['name'].replace('_', ' ')}"
        if fit is not None:
            text += f"  {fit['errors_ft'][i]:.1f} ft"
        flagged = fit is not None and fit["errors_ft"][i] > 1.0
        colour = (0, 0, 255) if flagged else (0, 255, 255)
        click_tools.draw_cross(shown, x, y, colour)
        click_tools.draw_label(shown, text, x + 16, y - 12, colour)
    return shown


def draw_diagram(setup, landmarks):
    """Top-down drawing of the court with the 20 landmarks numbered. Clicked ones are green."""
    court = setup["court"]
    length, width = court["length_ft"], court["width_ft"]
    scale, margin = DIAGRAM_PX_PER_FT, DIAGRAM_MARGIN

    def spot(x, y):
        # far sideline at the top, as the camera sees the court
        return int(round(margin + x * scale)), int(round(margin + (width - y) * scale))

    diagram = np.full((int(width * scale) + 2 * margin, int(length * scale) + 2 * margin, 3), 255, np.uint8)
    for line in court_model.court_lines(length, width, **court_model.extra(court)):
        points = np.array([spot(x, y) for x, y in line], np.int32)
        cv2.polylines(diagram, [points], False, (0, 0, 0), 2, cv2.LINE_AA)
    clicked = {mark["name"] for mark in landmarks}
    model = model_of(setup)
    for name, i in numbers(setup).items():
        x, y = model[name]["court_ft"]
        centre = spot(x, y)
        cv2.circle(diagram, centre, 16, (0, 170, 0) if name in clicked else (0, 0, 255), -1, cv2.LINE_AA)
        text_size = cv2.getTextSize(str(i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0]
        cv2.putText(diagram, str(i), (centre[0] - text_size[0] // 2, centre[1] + text_size[1] // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(diagram, "FAR side (away from the camera)", (margin, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (90, 90, 90), 2, cv2.LINE_AA)
    cv2.putText(diagram, "NEAR side (camera)", (margin, diagram.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                (90, 90, 90), 2, cv2.LINE_AA)
    return diagram


def table_rows(setup, landmarks, fit):
    """One row per click: (landmark label, time of the picture, error in feet or None)."""
    model = model_of(setup)
    number = numbers(setup)
    times = {view["frame"]: view["time"] for view in setup["views"]}
    rows = []
    for i, mark in enumerate(landmarks):
        label = f"{number[mark['name']]}. {model[mark['name']]['label']}"
        rows.append((label, times.get(mark["view"], "?"), None if fit is None else fit["errors_ft"][i]))
    return rows


# ---------------------------------------------------------------- saving

def save(video, setup, landmarks, provisional=False):
    """Solve, then write court_calibration.json and the check picture. Raises ValueError if it cannot be solved."""
    court = setup["court"]
    fit = court_mapper.solve_court(setup, landmarks, court["length_ft"], court["width_ft"], **court_model.extra(court))
    model = model_of(setup)
    calibration = {
        "court": court,
        "landmarks": [{"name": mark["name"], "view": mark["view"], "px": mark["px"],
                       "court_ft": model[mark["name"]]["court_ft"], "error_ft": error}
                      for mark, error in zip(landmarks, fit["errors_ft"])],
        "fit": {"count": len(landmarks), "rms_ft": fit["rms_ft"], "max_ft": fit["max_ft"]},
        "warnings": fit["warnings"],
    }
    if provisional:
        calibration["provisional"] = True
    path = config.data_dir(video) / "court_calibration.json"
    config.save_json(path, calibration, indent=1)
    court_mapper.forget(video)
    check = save_check_picture(video, setup, landmarks, fit)

    print()
    print(f"Court landmarks for {video}  ({gyms.court_words(court)} court)")
    if provisional:
        print("  PROVISIONAL: these clicks were not made by the coach. Redo them on the page.")
    for label, when, error in table_rows(setup, landmarks, fit):
        print(f"  {label:<58} picture {when}   {error:.2f} ft")
    print(f"  {len(landmarks)} clicks; typical error {fit['rms_ft']:.2f} ft, largest {fit['max_ft']:.2f} ft")
    for warning in fit["warnings"]:
        print(f"  WARNING: {warning}")
    print(f"  Wrote {path}")
    print(f"  Check by eye: {check}")
    print("  The green court should sit on the painted lines in every picture.")
    print(f"  Next:  python src/select_hoops.py --video {video}   (optional, for shots later)")
    print(f"         python src/main.py --video {video}")
    print("         (if main.py is already running in another terminal there is nothing more to do:")
    print("          it goes on to the court stage by itself)")
    return fit


def save_check_picture(video, setup, landmarks, fit):
    """Write outputs/<video>/court_calibration_check.jpg: every view with the court and the clicks drawn."""
    tiles = []
    for view in setup["views"]:
        shown = draw_view(court_mapper.load_view_picture(video, view), view["frame"], landmarks, fit, setup)
        click_tools.draw_label(shown, f"{view['time']}  frame {view['frame']}", 20, 50, (255, 255, 255), 1.4)
        tiles.append(cv2.resize(shown, CHECK_TILE, interpolation=cv2.INTER_AREA))
    if len(tiles) % 2:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)])
    path = config.output_dir(video) / "court_calibration_check.jpg"
    cv2.imwrite(str(path), sheet)
    back = cv2.imread(str(path))
    if back is None or back.shape != sheet.shape:
        raise IOError(f"{path} did not read back correctly. The disk may be failing; run the command again.")
    return path


# ---------------------------------------------------------------- what the page does on each click
# These are plain functions on a "session" dictionary, so they can be tested without a browser.
# Each returns what the page shows next: (main picture, enlarged picture, message, diagram, table).

def new_session(video, setup, landmarks):
    session = {"video": video, "setup": setup, "landmarks": landmarks, "zoom": None, "saved": False}
    choose_view(session, setup["main_view"])
    return session


def refresh(session, message, enlarged=None):
    setup, landmarks = session["setup"], session["landmarks"]
    fit, problem = try_solve(setup, landmarks)
    shown = draw_view(session["picture"], session["view"], landmarks, fit, setup)
    if session["zoom"] is not None:
        # show which part of the picture is enlarged
        x0, y0 = session["zoom"]
        height, width = shown.shape[:2]
        cv2.rectangle(shown, (x0, y0), (x0 + width // click_tools.ZOOM, y0 + height // click_tools.ZOOM),
                      (255, 255, 0), 3)
    lines = ["| Landmark | Picture | Error |", "|---|---|---|"]
    for label, when, error in table_rows(setup, landmarks, fit):
        if error is None:
            shown_error = ""
        elif error > 1.0:
            shown_error = f"**{error:.1f} ft  CHECK THIS CLICK**"
        else:
            shown_error = f"{error:.1f} ft"
        lines.append(f"| {label} | {when} | {shown_error} |")
    table = "\n".join(lines)
    if fit is not None:
        table += (f"\n\n**{len(landmarks)} clicks. Typical error {fit['rms_ft']:.2f} ft, "
                  f"largest {fit['max_ft']:.2f} ft.**")
        table += "".join(f"\n\n**WARNING: {warning}**" for warning in fit["warnings"])
    elif landmarks:
        table += f"\n\n**No court yet: {problem}**"
    return (click_tools.to_browser(shown), None if enlarged is None else click_tools.to_browser(enlarged),
            f"## {message}", click_tools.to_browser(draw_diagram(setup, landmarks)), table)


def choose_view(session, view):
    """The coach picked another reference picture."""
    session["view"] = view
    session["zoom"] = None
    entry = next(v for v in session["setup"]["views"] if v["frame"] == view)
    session["picture"] = court_mapper.load_view_picture(session["video"], entry)
    return refresh(session, "Choose a landmark in the list, then click it in the LEFT picture.")


def click_picture(session, x, y):
    """First click, in the whole picture: show the area around it enlarged."""
    enlarged, session["zoom"] = click_tools.zoom_picture(session["picture"], x, y)
    return refresh(session, "Now click EXACTLY on the landmark in the RIGHT (enlarged) picture.", enlarged)


def click_enlarged(session, name, zoom_x, zoom_y):
    """Second click, in the enlarged picture: store the landmark."""
    if session["zoom"] is None:
        return refresh(session, "First click the landmark in the LEFT picture.")
    if not name:
        return refresh(session, "First choose a landmark in the list.")
    x0, y0 = session["zoom"]
    x, y = click_tools.zoom_to_full(x0, y0, zoom_x, zoom_y)
    add_landmark(session["landmarks"], name, session["view"], x, y)
    # Draw the stored spot back onto the enlarged picture, so a wrong position would show at once.
    enlarged = click_tools.enlarge(session["picture"], x0, y0)
    click_tools.draw_cross(enlarged, *click_tools.full_to_zoom(x0, y0, x, y), (0, 255, 255), size=40, thickness=3)
    return refresh(session, f"Stored landmark {numbers(session['setup'])[name]}. Choose the next landmark "
                            "(or click again to move this one).", enlarged)


def undo(session):
    """Take back the most recent click."""
    session["zoom"] = None
    if not session["landmarks"]:
        return refresh(session, "There is nothing to undo.")
    mark = session["landmarks"].pop()
    return refresh(session, f"Removed the last click (landmark {numbers(session['setup'])[mark['name']]}).")


def remove(session, name):
    """Take the chosen landmark off the picture being shown."""
    session["zoom"] = None
    if name and remove_landmark(session["landmarks"], name, session["view"]):
        return refresh(session, f"Removed landmark {numbers(session['setup'])[name]} from this picture.")
    return refresh(session, "That landmark is not marked on this picture.")


def measure_session(session):
    """ "Measure the court from my clicks": try the sizes and markings, say what fits best; nothing is changed yet."""
    session["zoom"] = None
    try:
        found = court_fit.measure(session["setup"], session["landmarks"])
    except ValueError as problem:
        session["measured"] = None
        return refresh(session, f"Cannot measure yet. {problem}")
    session["measured"] = found["court"]
    words = found["words"] + " Press \"Use the measured size\" to keep it."
    words += "".join(f" WARNING: {warning}" for warning in found["warnings"])
    return refresh(session, words)


def use_measured(session):
    """ "Use the measured size": the measured court goes into court_setup.json and the clicks are refitted to it."""
    session["zoom"] = None
    measured = session.get("measured")
    if not measured:
        return refresh(session, "Press \"Measure the court from my clicks\" first.")
    setup = session["setup"]
    setup["court"] = dict(measured)
    config.save_json(config.data_dir(session["video"]) / "court_setup.json", setup, indent=1)
    court_mapper.forget(session["video"])
    session["measured"] = None
    return refresh(session, f"The court is now {gyms.court_words(setup['court'])}. Press \"Save and close\" to keep the clicks with it.")


def save_gym_session(session, name):
    """ "Save this court as a gym": the court size and markings go into the gym library under that name."""
    session["zoom"] = None
    try:
        key, gym = gyms.save_gym(name, session["setup"]["court"])
    except ValueError as problem:
        return refresh(session, str(problem))
    session["setup"]["gym"] = gym["name"]
    config.save_json(config.data_dir(session["video"]) / "court_setup.json", session["setup"], indent=1)
    return refresh(session, f"Saved the gym \"{gym['name']}\" ({gyms.court_words(gym['court'])}). The first click page "
                            "offers it for the next video from there.")


def save_session(session):
    """ "Save and close" was pressed. Saving is refused, with the reason, if there is no court yet."""
    session["zoom"] = None
    try:
        save(session["video"], session["setup"], session["landmarks"])
    except ValueError as problem:
        return refresh(session, f"NOT saved. {problem}")
    session["saved"] = True
    return refresh(session, "Saved. You can close this browser tab and go back to the terminal.")


# ---------------------------------------------------------------- the page

def open_page(video, setup, landmarks):
    """Show the click page and wait for "Save and close". Returns True if the clicks were saved."""
    temp_dir = config.data_dir(video) / "page_tmp"
    gr = click_tools.load_gradio(temp_dir)
    done = threading.Event()
    session = new_session(video, setup, landmarks)
    model = model_of(setup)
    view_choices = [(f"Picture at {view['time']} (frame {view['frame']})", view["frame"]) for view in setup["views"]]
    landmark_choices = [(f"{i}. {model[name]['label']}", name) for name, i in numbers(setup).items()]
    first = refresh(session, "Choose a landmark in the list, then click it in the LEFT picture.")

    def on_view(view):
        return choose_view(session, view)

    def on_picture(event: gr.SelectData):
        return click_picture(session, event.index[0], event.index[1])

    def on_enlarged(name, event: gr.SelectData):
        return click_enlarged(session, name, event.index[0], event.index[1])

    def on_undo():
        return undo(session)

    def on_remove(name):
        return remove(session, name)

    def on_save():
        result = save_session(session)
        if session["saved"]:
            done.set()
        return result

    def on_measure():
        return measure_session(session)

    def on_use_measured():
        return use_measured(session)

    def on_save_gym(name):
        return save_gym_session(session, name)

    with gr.Blocks(title="Mark the court") as demo:
        gr.Markdown(
            f"# Mark the court for {video}\n"
            "## 1. Pick a picture where the lines at one end of the court are easy to see.\n"
            "## 2. Choose a landmark in the list. The court diagram at the bottom shows which is which.\n"
            "## 3. Click the landmark in the LEFT picture. Then click it EXACTLY in the RIGHT (enlarged) picture.\n"
            "## 4. Do at least 4 landmarks at EACH end of the court. 10 to 14 in total is ideal. "
            "Then press \"Save and close\".\n"
            "Use the BASKETBALL lines, not the lines of another sport such as volleyball. \"Far\" means the side of the court away "
            "from the camera, \"near\" means the camera side. After 4 landmarks a green court is drawn: it should sit on the "
            "real lines in every picture. A landmark hidden by a player in one picture can be clicked in another.")
        with gr.Row():
            view_box = gr.Dropdown(view_choices, value=setup["main_view"], label="Picture")
            landmark_box = gr.Dropdown(landmark_choices, value=landmark_choices[0][1], label="Landmark to click")
        message = gr.Markdown(first[2])
        with gr.Row():
            picture = gr.Image(first[0], label="Whole picture: click near the landmark", interactive=False,
                               format="jpeg", buttons=[])
            enlarged = gr.Image(None, label="Enlarged: click exactly on the landmark", interactive=False,
                                format="jpeg", buttons=[])
        with gr.Row():
            undo_button = gr.Button("Undo last click")
            remove_button = gr.Button("Remove the chosen landmark from this picture")
            save_button = gr.Button("Save and close", variant="primary")
        gr.Markdown("### Is this gym a different size? With 4+ landmarks at each end and a centre point, the tool can "
                    "measure the court from your clicks (length, width, the lane, the lines).")
        with gr.Row():
            measure_button = gr.Button("Measure the court from my clicks")
            use_button = gr.Button("Use the measured size")
            gym_name = gr.Textbox(label="Gym name (to save this court for other videos)", placeholder="Home gym",
                                  value=setup.get("gym") or "")
            gym_button = gr.Button("Save this court as a gym")
        with gr.Row():
            diagram = gr.Image(first[3], label="Court diagram (green = clicked, red = not yet)", interactive=False,
                               format="jpeg", buttons=[])
            table = gr.Markdown(first[4])

        outputs = [picture, enlarged, message, diagram, table]
        view_box.input(on_view, view_box, outputs)
        picture.select(on_picture, None, outputs)
        enlarged.select(on_enlarged, landmark_box, outputs)
        undo_button.click(on_undo, None, outputs)
        remove_button.click(on_remove, landmark_box, outputs)
        save_button.click(on_save, None, outputs)
        measure_button.click(on_measure, None, outputs)
        use_button.click(on_use_measured, None, outputs)
        gym_button.click(on_save_gym, gym_name, outputs)

    click_tools.run_page(demo, done, temp_dir)
    return session["saved"]


def run(video=config.DEFAULT_VIDEO, answers=None):
    """Mark the court of a video, from the page or from an answers file."""
    config.require_video(video)     # a mistyped name is caught here, before anything else
    setup = court_mapper.load_setup(video)
    if answers is not None:
        landmarks = []
        for mark in answers.get("landmarks", []):
            add_landmark(landmarks, mark["name"], mark["view"], mark["px"][0], mark["px"][1])
        try:
            return save(video, setup, landmarks, provisional=bool(answers.get("provisional")))
        except ValueError as problem:
            sys.exit(f"Not saved. {problem}")

    # Start the page from the clicks saved before, so they can be added to or corrected.
    landmarks = []
    path = config.data_dir(video) / "court_calibration.json"
    if path.exists():
        saved = config.load_json(path)
        views = {view["frame"] for view in setup["views"]}
        if saved.get("provisional"):
            print("The saved landmarks are provisional (not clicked by you), so the page starts empty.")
        else:
            landmarks = [{"name": mark["name"], "view": mark["view"], "px": mark["px"]}
                         for mark in saved["landmarks"] if mark["view"] in views]
            print(f"Starting from the {len(landmarks)} landmarks saved before.")
    if not open_page(video, setup, landmarks):
        sys.exit("Closed without saving. Nothing was changed.")


if __name__ == "__main__":
    args = parse_args()
    run(args.video, answers=click_tools.load_answers(args.answers))
