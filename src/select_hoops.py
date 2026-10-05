"""Court set-up, step 3 of 3: mark the hoops (used later for shots; positions do not need it).

    python src/select_hoops.py --video clips/game1.mp4

Run select_court.py and select_court_keypoints.py first.

What you do on the page
    For each hoop the video shows: pick a reference picture that shows it, choose
    "left end" or "right end", and click the two ends of the orange rim (each click is
    repeated in the enlarged picture). A hoop the video never shows stays empty.

Why one marking is enough for the whole video
    The camera only turns and zooms, so a point marked in one picture can be carried
    into any other picture with the same matrices that map the court
    (court_mapper.carry_points). The rim is not on the floor, and that does not matter.

What it writes
    data/<video>/hoops.json            {"hoops": {"left": {"view": frame, "rim_px": [[x, y], [x, y]]} or null,
                                                  "right": ...}}
                                       "left" is the hoop at the left baseline as the camera sees it.
    outputs/<video>/hoops_check.jpg    each marked hoop, carried into other reference pictures

Without the page
    --answers FILE    a JSON file in the hoops.json form above. Add "provisional": true if the
                      marks are a stand-in that the coach should redo.
"""
import argparse
import sys
import threading

import cv2
import numpy as np

import click_tools
import config
import court_mapper

ENDS = {"left": "Left end hoop", "right": "Right end hoop"}
COLOURS = {"left": (255, 128, 0), "right": (255, 0, 255)}       # blue-ish and magenta: they stand out against a wooden floor and an orange rim
CHECK_CROP = (640, 360)         # the piece of each picture shown in hoops_check.jpg
CHECK_OTHER_VIEWS = 3
BOX_GROW_PX = 15                # when comparing with the detector's hoop boxes


def parse_args():
    parser = argparse.ArgumentParser(description="Mark the two ends of each hoop's rim.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="video file (default clips/test.mp4)")
    parser.add_argument("--answers", help="JSON file with the hoops, instead of the page")
    return parser.parse_args()


# ---------------------------------------------------------------- the marks

def add_rim_click(hoops, end, view, x, y):
    """Store one end of a rim. A hoop is two clicks on one picture; a third click starts it again."""
    hoop = hoops[end]
    if hoop is None or hoop["view"] != view or len(hoop["rim_px"]) >= 2:
        hoop = hoops[end] = {"view": view, "rim_px": []}
    hoop["rim_px"].append([round(float(x), 1), round(float(y), 1)])


def finished(hoops):
    """The hoops in the form saved to hoops.json: only hoops with both rim ends, left end of the rim first."""
    result = {}
    for end in ENDS:
        hoop = hoops.get(end)
        if hoop is None or len(hoop["rim_px"]) != 2:
            result[end] = None
        else:
            result[end] = {"view": hoop["view"], "rim_px": sorted(hoop["rim_px"])}
    return result


def carried_rim(hoop, view, view_to_court, width, height):
    """Where a marked rim is in another reference picture: [[x, y], [x, y]], or None if not in that picture."""
    rim = court_mapper.carry_points(view_to_court[hoop["view"]], view_to_court[view], hoop["rim_px"], width, height)
    if rim[0] is None or rim[1] is None:
        return None
    return rim


# ---------------------------------------------------------------- drawing

def draw_rim(picture, rim, colour, text):
    for x, y in rim:
        click_tools.draw_cross(picture, x, y, colour, size=10, thickness=1)
    if len(rim) == 2:
        (x1, y1), (x2, y2) = rim
        cv2.line(picture, (int(round(x1)), int(round(y1))), (int(round(x2)), int(round(y2))), colour, 1, cv2.LINE_AA)
    click_tools.draw_label(picture, text, rim[0][0] - 60, rim[0][1] - 30, colour, 0.7)


def draw_view(picture, view, hoops, view_to_court):
    """A copy of a reference picture with the hoops marked on it, and hoops marked elsewhere carried into it."""
    shown = picture.copy()
    height, width = shown.shape[:2]
    for end, hoop in hoops.items():
        if hoop is None:
            continue
        if hoop["view"] == view:
            draw_rim(shown, hoop["rim_px"], COLOURS[end], ENDS[end])
        elif len(hoop["rim_px"]) == 2:
            rim = carried_rim(hoop, view, view_to_court, width, height)
            if rim is not None:
                draw_rim(shown, rim, COLOURS[end], ENDS[end] + " (carried from another picture)")
    return shown


def save_check_picture(video, setup, hoops, view_to_court):
    """Write outputs/<video>/hoops_check.jpg: each marked hoop where it was marked and in other pictures."""
    width, height = setup["width"], setup["height"]
    rows = []
    for end, hoop in hoops.items():
        if hoop is None:
            continue
        # the picture it was marked on first, then up to 3 others that show it
        shown_on = [(hoop["view"], hoop["rim_px"])]
        for view in setup["views"]:
            rim = carried_rim(hoop, view["frame"], view_to_court, width, height)
            if view["frame"] != hoop["view"] and rim is not None and len(shown_on) <= CHECK_OTHER_VIEWS:
                shown_on.append((view["frame"], rim))
        tiles = []
        for frame, rim in shown_on:
            entry = next(v for v in setup["views"] if v["frame"] == frame)
            picture = court_mapper.load_view_picture(video, entry)
            draw_rim(picture, rim, COLOURS[end], "")
            # cut out the piece around the rim
            x0 = int(min(max((rim[0][0] + rim[1][0]) / 2 - CHECK_CROP[0] / 2, 0), width - CHECK_CROP[0]))
            y0 = int(min(max((rim[0][1] + rim[1][1]) / 2 - CHECK_CROP[1] / 2, 0), height - CHECK_CROP[1]))
            tile = picture[y0:y0 + CHECK_CROP[1], x0:x0 + CHECK_CROP[0]].copy()
            note = "marked here" if frame == hoop["view"] else "carried"
            click_tools.draw_label(tile, f"{ENDS[end]}, picture {entry['time']}: {note}", 10, 28, (255, 255, 255), 0.7)
            tiles.append(tile)
        while len(tiles) < CHECK_OTHER_VIEWS + 1:
            tiles.append(np.zeros_like(tiles[0]))
        rows.append(np.hstack(tiles))
    if not rows:
        return None
    sheet = np.vstack(rows)
    path = config.output_dir(video) / "hoops_check.jpg"
    cv2.imwrite(str(path), sheet)
    back = cv2.imread(str(path))
    if back is None or back.shape != sheet.shape:
        raise IOError(f"{path} did not read back correctly. The SD card may be failing; run the command again.")
    return path


# ---------------------------------------------------------------- comparing with the detector

def compare_with_detector(video):
    """How well the marked hoops agree with the hoop boxes the detector found. Returns None if it cannot be done yet."""
    folder = config.data_dir(video)
    if not (folder / "court_frames.json").exists() or not (folder / "tracks.json").exists():
        return None
    tracks = config.load_json(folder / "tracks.json")
    frames_with_box = frames_agreeing = boxes_unmatched = 0
    for record in tracks["frames"]:
        if not record["hoops"] or court_mapper.court_homography(video, record["frame"]) is None:
            continue
        frames_with_box += 1
        rims = [rim for rim in court_mapper.hoops_in_frame(video, record["frame"]).values() if rim is not None]
        middles = [((rim[0][0] + rim[1][0]) / 2, (rim[0][1] + rim[1][1]) / 2) for rim in rims]
        agrees = False
        for hoop in record["hoops"]:
            x1, y1, x2, y2 = hoop["bbox"]
            if any(x1 - BOX_GROW_PX <= x <= x2 + BOX_GROW_PX and y1 - BOX_GROW_PX <= y <= y2 + BOX_GROW_PX
                   for x, y in middles):
                agrees = True
            else:
                boxes_unmatched += 1
        frames_agreeing += agrees
    return {"frames_with_box": frames_with_box, "frames_agreeing": frames_agreeing, "boxes_unmatched": boxes_unmatched}


# ---------------------------------------------------------------- saving

def save(video, setup, hoops, view_to_court, provisional=False):
    """Write hoops.json and the check picture, and print what was saved."""
    result = {"hoops": finished(hoops)}
    if provisional:
        result["provisional"] = True
    path = config.data_dir(video) / "hoops.json"
    config.save_json(path, result, indent=1)
    court_mapper.forget(video)
    check = save_check_picture(video, setup, result["hoops"], view_to_court)

    print()
    print(f"Hoops for {video}")
    if provisional:
        print("  PROVISIONAL: these marks were not made by the coach. Redo them on the page.")
    times = {view["frame"]: view["time"] for view in setup["views"]}
    for end, hoop in result["hoops"].items():
        if hoop is None:
            print(f"  {ENDS[end]}: not marked")
        else:
            print(f"  {ENDS[end]}: marked on the picture at {times[hoop['view']]}, rim ends at {hoop['rim_px']}")
    print(f"  Wrote {path}")
    if check:
        print(f"  Check by eye: {check}")
        print("  The thin coloured line should lie on the orange rim in every small picture.")
    numbers = compare_with_detector(video)
    if numbers and numbers["frames_with_box"]:
        share = 100 * numbers["frames_agreeing"] / numbers["frames_with_box"]
        print(f"  Compared with the detector: it found a hoop box in {numbers['frames_with_box']} mapped frames; "
              f"in {numbers['frames_agreeing']} of them ({share:.0f}%) a marked rim lies inside a box.")
        print(f"  Detector boxes that match no marked hoop: {numbers['boxes_unmatched']}")
    return result


# ---------------------------------------------------------------- what the page does on each click
# Plain functions on a "session" dictionary, so they can be tested without a browser.
# Each returns what the page shows next: (picture, enlarged picture, message, list of hoops as text).

def new_session(video, setup, hoops, view_to_court):
    session = {"video": video, "setup": setup, "hoops": hoops, "view_to_court": view_to_court,
               "zoom": None, "saved": False}
    choose_view(session, setup["main_view"])
    return session


def refresh(session, message, enlarged=None):
    shown = draw_view(session["picture"], session["view"], session["hoops"], session["view_to_court"])
    if session["zoom"] is not None:
        x0, y0 = session["zoom"]
        height, width = shown.shape[:2]
        cv2.rectangle(shown, (x0, y0), (x0 + width // click_tools.ZOOM, y0 + height // click_tools.ZOOM),
                      (255, 255, 0), 3)
    times = {view["frame"]: view["time"] for view in session["setup"]["views"]}
    lines = []
    for end, hoop in session["hoops"].items():
        if hoop is None:
            lines.append(f"**{ENDS[end]}:** not marked")
        elif len(hoop["rim_px"]) < 2:
            lines.append(f"**{ENDS[end]}:** one end of the rim clicked on the picture at {times[hoop['view']]}; "
                         "click the other end")
        else:
            lines.append(f"**{ENDS[end]}:** marked on the picture at {times[hoop['view']]}")
    return (click_tools.to_browser(shown), None if enlarged is None else click_tools.to_browser(enlarged),
            f"## {message}", "\n\n".join(lines))


def choose_view(session, view):
    session["view"] = view
    session["zoom"] = None
    entry = next(v for v in session["setup"]["views"] if v["frame"] == view)
    session["picture"] = court_mapper.load_view_picture(session["video"], entry)
    return refresh(session, "Choose which hoop, then click one end of its rim in the LEFT picture.")


def click_picture(session, x, y):
    """First click, in the whole picture: show the area around it enlarged."""
    enlarged, session["zoom"] = click_tools.zoom_picture(session["picture"], x, y)
    return refresh(session, "Now click EXACTLY on the end of the rim in the RIGHT (enlarged) picture.", enlarged)


def click_enlarged(session, end, zoom_x, zoom_y):
    """Second click, in the enlarged picture: store one end of the rim."""
    if session["zoom"] is None:
        return refresh(session, "First click the rim in the LEFT picture.")
    x0, y0 = session["zoom"]
    x, y = click_tools.zoom_to_full(x0, y0, zoom_x, zoom_y)
    add_rim_click(session["hoops"], end, session["view"], x, y)
    # Draw the stored spot back onto the enlarged picture, so a wrong position would show at once.
    enlarged = click_tools.enlarge(session["picture"], x0, y0)
    click_tools.draw_cross(enlarged, *click_tools.full_to_zoom(x0, y0, x, y), COLOURS[end], size=40, thickness=3)
    if len(session["hoops"][end]["rim_px"]) == 1:
        return refresh(session, "Stored one end of the rim. Now click the OTHER end of the same rim.", enlarged)
    return refresh(session, f"{ENDS[end]} is marked. Mark the other hoop if the video shows it, "
                            "or press \"Save and close\".", enlarged)


def undo(session, end):
    """Take back the most recent click on the chosen hoop."""
    session["zoom"] = None
    hoop = session["hoops"][end]
    if hoop is None:
        return refresh(session, "There is nothing to undo for this hoop.")
    hoop["rim_px"].pop()
    if not hoop["rim_px"]:
        session["hoops"][end] = None
    return refresh(session, f"Removed the last click on: {ENDS[end]}.")


def clear(session, end):
    session["zoom"] = None
    session["hoops"][end] = None
    return refresh(session, f"Cleared: {ENDS[end]}.")


def save_session(session):
    session["zoom"] = None
    half_done = [ENDS[end] for end, hoop in session["hoops"].items() if hoop is not None and len(hoop["rim_px"]) == 1]
    if half_done:
        return refresh(session, f"NOT saved. {half_done[0]} has only one end of the rim. "
                                "Click the other end, or press \"Clear this hoop\".")
    save(session["video"], session["setup"], session["hoops"], session["view_to_court"])
    session["saved"] = True
    return refresh(session, "Saved. You can close this browser tab and go back to the terminal.")


# ---------------------------------------------------------------- the page

def open_page(video, setup, hoops, view_to_court):
    """Show the click page and wait for "Save and close". Returns True if the hoops were saved."""
    temp_dir = config.data_dir(video) / "page_tmp"
    gr = click_tools.load_gradio(temp_dir)
    done = threading.Event()
    session = new_session(video, setup, hoops, view_to_court)
    view_choices = [(f"Picture at {view['time']} (frame {view['frame']})", view["frame"]) for view in setup["views"]]
    first = refresh(session, "Choose which hoop, then click one end of its rim in the LEFT picture.")

    def on_view(view):
        return choose_view(session, view)

    def on_picture(event: gr.SelectData):
        return click_picture(session, event.index[0], event.index[1])

    def on_enlarged(end, event: gr.SelectData):
        return click_enlarged(session, end, event.index[0], event.index[1])

    def on_undo(end):
        return undo(session, end)

    def on_clear(end):
        return clear(session, end)

    def on_save():
        result = save_session(session)
        if session["saved"]:
            done.set()
        return result

    with gr.Blocks(title="Mark the hoops") as demo:
        gr.Markdown(
            f"# Mark the hoops for {video}\n"
            "## 1. Pick a picture that shows a hoop, and choose which hoop it is "
            "(left end or right end of the court).\n"
            "## 2. Click one end of the orange rim in the LEFT picture, then click it EXACTLY in the RIGHT "
            "(enlarged) picture.\n"
            "## 3. Do the same for the other end of the same rim. A thin line is drawn along the rim.\n"
            "## 4. Mark the other hoop if the video shows it. Then press \"Save and close\".\n"
            "The \"ends\" of the rim are its far left and far right points as you see it in the picture. "
            "A hoop the video never shows stays empty.")
        with gr.Row():
            view_box = gr.Dropdown(view_choices, value=setup["main_view"], label="Picture")
            end_box = gr.Radio([(label, end) for end, label in ENDS.items()], value="left", label="Which hoop")
        message = gr.Markdown(first[2])
        with gr.Row():
            picture = gr.Image(first[0], label="Whole picture: click near the rim", interactive=False,
                               format="jpeg", buttons=[])
            enlarged = gr.Image(None, label="Enlarged: click exactly on the end of the rim", interactive=False,
                                format="jpeg", buttons=[])
        with gr.Row():
            undo_button = gr.Button("Undo last click on this hoop")
            clear_button = gr.Button("Clear this hoop")
            save_button = gr.Button("Save and close", variant="primary")
        status = gr.Markdown(first[3])

        outputs = [picture, enlarged, message, status]
        view_box.input(on_view, view_box, outputs)
        picture.select(on_picture, None, outputs)
        enlarged.select(on_enlarged, end_box, outputs)
        undo_button.click(on_undo, end_box, outputs)
        clear_button.click(on_clear, end_box, outputs)
        save_button.click(on_save, None, outputs)

    click_tools.run_page(demo, done, temp_dir)
    return session["saved"]


def run(video=config.DEFAULT_VIDEO, answers=None):
    """Mark the hoops of a video, from the page or from an answers file."""
    config.require_video(video)     # a mistyped name is caught here, before anything else
    setup = court_mapper.load_setup(video)
    view_to_court = court_mapper.load_calibration(video)["view_to_court"]
    hoops = {end: None for end in ENDS}
    if answers is not None:
        for end, hoop in answers.get("hoops", {}).items():
            if end not in ENDS:
                sys.exit(f"Not saved. \"{end}\" is not a hoop; use \"left\" or \"right\".")
            if hoop is None:
                continue
            if hoop["view"] not in view_to_court or len(hoop["rim_px"]) != 2:
                sys.exit(f"Not saved. The {end} hoop needs \"view\" (the frame number of a reference picture in "
                         "court_setup.json) and \"rim_px\" with two points.")
            hoops[end] = {"view": hoop["view"], "rim_px": [list(point) for point in hoop["rim_px"]]}
        return save(video, setup, hoops, view_to_court, provisional=bool(answers.get("provisional")))

    # Start the page from the hoops saved before, so they can be corrected.
    path = config.data_dir(video) / "hoops.json"
    if path.exists():
        saved = config.load_json(path)
        if saved.get("provisional"):
            print("The saved hoops are provisional (not marked by you), so the page starts empty.")
        else:
            for end, hoop in saved["hoops"].items():
                if hoop is not None and hoop["view"] in view_to_court:
                    hoops[end] = hoop
    if not open_page(video, setup, hoops, view_to_court):
        sys.exit("Closed without saving. Nothing was changed.")


if __name__ == "__main__":
    args = parse_args()
    run(args.video, answers=click_tools.load_answers(args.answers))
