"""Court set-up, step 1 of 3: the court size and the reference pictures ("views").

    python src/select_court.py --video clips/game1.mp4

Do this once for every new video. It needs no player detection.

What it does
    1. Looks through the video (about 120 moments, or one per second for a short clip)
       and keeps a picture whenever the camera shows a part of the gym that no picture
       kept so far shows from nearly the same direction and zoom.
    2. Opens a page in your browser where you choose the court size, untick pictures
       that look wrong, and add pictures at times you choose.
    3. Works out how the pictures overlap ("links") so that all of them are tied to
       one "main" picture. A picture that overlaps none of the others is dropped.
    4. Writes data/<video>/court_setup.json, the pictures in data/<video>/court_views/
       and a sheet to look at, outputs/<video>/court_views.jpg.

Running it again keeps the pictures you already have (and so keeps your landmark
clicks valid). Use --rescan to choose the pictures from scratch; pictures that carry
landmark clicks or hoop marks are kept even then.

Other ways to run it
    --add-time 12:35     add one picture at that time, without opening the page
    --no-page            accept the automatic choice, without opening the page
    --rescan             look through the video again and choose new pictures
    --answers FILE       take the choices from a JSON file instead of the page:
                         {"court": {"length_ft": 84, "width_ft": 50},
                          "frames": [0, 300],   use exactly these frames (no scan)
                          "add": [450],         also use these frames
                          "drop": [120]}        do not use these frames
                         Every key is optional.

Next step:  python src/select_court_keypoints.py --video <the same video>
"""
import argparse
import sys
import threading

import cv2
import numpy as np

import click_tools
import config
import court_mapper
import court_model

MOMENTS = 120               # look at about this many moments of a long video
SEQUENTIAL_UP_TO = 3000     # videos up to this many frames are read from the start; longer ones by jumping
MIN_FEATURES = 500          # a picture with fewer fixed details than this is too blank to be a view
SAME_VIEW_MOVE_PX = 300     # a moment is "already covered" by a view if the middle moved no more than this ...
SAME_VIEW_ZOOM = 1.25       # ... and the zoom differs by no more than this factor
MAX_VIEWS = 30
JPEG_QUALITY = 95
THUMB_SIZE = (480, 270)     # small pictures for the page and the sheet

PRESET_LABELS = {"high_school": "High school", "college_nba": "College / NBA", "junior_high": "Junior high"}


def parse_args():
    parser = argparse.ArgumentParser(description="Choose the court size and the reference pictures.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="video file (default clips/test.mp4)")
    parser.add_argument("--answers", help="JSON file with the choices, instead of the page")
    parser.add_argument("--rescan", action="store_true", help="choose the reference pictures from scratch")
    parser.add_argument("--add-time", help="add one reference picture at this time (mm:ss), no page")
    parser.add_argument("--no-page", action="store_true", help="accept the automatic choice, no page")
    return parser.parse_args()


# ---------------------------------------------------------------- reading the video

def video_info(video):
    """Size, speed and length of the video."""
    path = config.video_path(video)
    config.require_video(video)
    capture = cv2.VideoCapture(str(path))
    info = {"fps": capture.get(cv2.CAP_PROP_FPS),
            "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "frame_count": int(capture.get(cv2.CAP_PROP_FRAME_COUNT))}
    capture.release()
    if info["frame_count"] <= 0 or info["fps"] <= 0:
        sys.exit(f"{path} could not be opened as a video.\n"
                 "The file may be damaged: copy it to the clips folder again.")
    return info


def read_pictures(video, frames):
    """Give (frame number, picture) for each wanted frame, in order, one at a time.

    A short video is read from the start (always exact). A long one is read by jumping
    to each frame. Stops at the first frame that cannot be read: a damaged file can be
    shorter than it claims.
    """
    capture = cv2.VideoCapture(str(config.video_path(video)))
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    index = -1
    for frame in sorted(set(frames)):
        if count <= SEQUENTIAL_UP_TO:
            while index < frame - 1 and capture.grab():
                index += 1
            if index < frame - 1:
                break
        else:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame)
        ok, picture = capture.read()
        if not ok:
            break
        index = frame
        yield frame, picture
    capture.release()


def time_label(frame, fps):
    """Frame number -> time in the video as "mm:ss"."""
    return court_mapper.minutes_seconds(frame / fps)


def time_to_frame(text, fps):
    """ "12:35" (or plain seconds, "755") -> frame number. Raises ValueError if it is not a time."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(text)
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return int(round(seconds * fps))


# ---------------------------------------------------------------- the reference pictures

def view_file(video, frame):
    return config.data_dir(video) / "court_views" / f"view_{frame:06d}.jpg"


def save_view(video, frame, picture):
    """Save a reference picture and read it back to be sure the disk kept it whole."""
    path = view_file(video, frame)
    path.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(path), picture, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    back = cv2.imread(str(path))
    if back is None or back.shape != picture.shape:
        raise IOError(f"{path} did not read back correctly. The disk may be failing; run the command again.")


def add_views(video, frames, wanted):
    """Read the wanted frames from the video and save them as views. Returns the frames that were added."""
    added = []
    for frame, picture in read_pictures(video, [f for f in wanted if f not in frames]):
        save_view(video, frame, picture)
        frames.append(frame)
        added.append(frame)
    frames.sort()
    return added


def scan_views(video, info):
    """Look through the video and keep a picture of every different view. Returns the frame numbers."""
    step = max(1.0, info["frame_count"] / info["fps"] / MOMENTS)        # seconds between moments
    moments = [int(round(i * step * info["fps"])) for i in range(int(info["frame_count"] / info["fps"] / step) + 1)]
    moments = [frame for frame in moments if frame < info["frame_count"]]
    print(f"Looking at {len(moments)} moments of {video} to choose the reference pictures ...")
    kept = {}           # frame -> (points, descriptors)
    middle = np.float32([[[info["width"] / 2, info["height"] / 2]]])
    outline = np.float32([[0, 0], [info["width"], 0], [info["width"], info["height"]],
                          [0, info["height"]]]).reshape(-1, 1, 2)
    looked = 0
    for frame, picture in read_pictures(video, moments):
        looked += 1
        if looked % 10 == 0:
            print(f"  {looked} of {len(moments)} moments, {len(kept)} pictures kept so far")
        points, descriptors = court_mapper.features(picture)
        if len(points) < MIN_FEATURES:
            continue                    # too blank (a close-up of the floor, a black frame)
        covered = False
        for view_points, view_descriptors in kept.values():
            m = court_mapper.match(points, descriptors, view_points, view_descriptors, info["width"], info["height"])
            if m is None or not court_mapper.good(m):
                continue
            moved = np.linalg.norm(cv2.perspectiveTransform(middle, m["H"]) - middle)
            area = cv2.contourArea(cv2.perspectiveTransform(outline, m["H"]))
            zoom = np.sqrt(area / (info["width"] * info["height"]))
            if moved <= SAME_VIEW_MOVE_PX and 1 / SAME_VIEW_ZOOM <= zoom <= SAME_VIEW_ZOOM:
                covered = True
                break
        if covered:
            continue
        # Features of the SAVED picture are what every later step uses, so keep those.
        save_view(video, frame, picture)
        kept[frame] = court_mapper.features(cv2.imread(str(view_file(video, frame))))
        if len(kept) >= MAX_VIEWS:
            print(f"  Reached the limit of {MAX_VIEWS} pictures; stopping the search.")
            break
    if looked < len(moments) and len(kept) < MAX_VIEWS:
        print(f"  The video could only be read up to moment {looked} of {len(moments)}.")
    print(f"  Kept {len(kept)} reference pictures.")
    return sorted(kept)


def marked_views(video):
    """The frames of the reference pictures that carry landmark clicks, and those that carry hoop marks."""
    folder = config.data_dir(video)
    landmark_views, hoop_views = [], []
    if (folder / "court_calibration.json").exists():
        landmark_views = [mark["view"] for mark in config.load_json(folder / "court_calibration.json")["landmarks"]]
    if (folder / "hoops.json").exists():
        hoop_views = [hoop["view"] for hoop in config.load_json(folder / "hoops.json")["hoops"].values() if hoop]
    return landmark_views, hoop_views


def thumbnail(video, frame, label):
    """A small labelled copy of a reference picture."""
    picture = cv2.imread(str(view_file(video, frame)))
    small = cv2.resize(picture, THUMB_SIZE, interpolation=cv2.INTER_AREA)
    click_tools.draw_label(small, label, 8, 26, (0, 255, 255), 0.7)
    return small


def save_sheet(video, frames, fps, main):
    """Write outputs/<video>/court_views.jpg: all reference pictures on one sheet."""
    tiles = [thumbnail(video, frame, f"{time_label(frame, fps)}  frame {frame}" + ("  MAIN" if frame == main else ""))
             for frame in frames]
    while len(tiles) % 4:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)])
    path = config.output_dir(video) / "court_views.jpg"
    cv2.imwrite(str(path), sheet)
    back = cv2.imread(str(path))
    if back is None or back.shape != sheet.shape:
        raise IOError(f"{path} did not read back correctly. The disk may be failing; run the command again.")
    return path


# ---------------------------------------------------------------- the court size

def court_choice(length, width):
    """{"length_ft", "width_ft", "preset"} for a court size; preset is "custom" if it is not a usual size."""
    # 84.0 -> 84, so the files and messages show whole numbers as whole numbers
    length = int(length) if float(length).is_integer() else float(length)
    width = int(width) if float(width).is_integer() else float(width)
    preset = "custom"
    for name, size in court_model.PRESETS.items():
        if (length, width) == size:
            preset = name
    return {"length_ft": length, "width_ft": width, "preset": preset}


def check_court_size(length, width):
    """Raises ValueError with a plain message if the numbers cannot be a basketball court."""
    if not (50 <= length <= 100 and 30 <= width <= 60 and length > width):
        raise ValueError(f"A court of {length} x {width} ft does not look right. Length is the long side "
                         "(usually 74 to 94 ft), width the short side (usually 42 to 50 ft).")


# ---------------------------------------------------------------- the page

def open_page(video, frames, court, info):
    """Show the page and wait for "Save and close". Returns the coach's choices in the answers format."""
    temp_dir = config.data_dir(video) / "page_tmp"
    gr = click_tools.load_gradio(temp_dir)
    done = threading.Event()
    answers = {}

    def label(frame):
        return f"{time_label(frame, info['fps'])} (frame {frame})"

    def gallery_items():
        return [(click_tools.to_browser(thumbnail(video, frame, label(frame))), label(frame)) for frame in frames]

    def choose_preset(name):
        length, width = court_model.PRESETS[name]
        return length, width

    def add_picture(text, ticked):
        try:
            frame = time_to_frame(text, info["fps"])
        except ValueError:
            return gr.update(), gr.update(), f"### \"{text}\" is not a time. Type it like 12:35 (minutes:seconds)."
        if frame in frames:
            return gr.update(), gr.update(), f"### There is already a picture at {label(frame)}."
        if not add_views(video, frames, [frame]):
            return gr.update(), gr.update(), f"### The video could not be read at {text}. Is it past the end?"
        choices = [label(f) for f in frames]
        return (gallery_items(), gr.update(choices=choices, value=ticked + [label(frame)]),
                f"### Added the picture at {label(frame)}.")

    def save_and_close(length, width, ticked):
        try:
            check_court_size(length, width)
        except (ValueError, TypeError) as problem:
            return f"### Not saved. {problem}"
        answers["court"] = {"length_ft": length, "width_ft": width}
        answers["drop"] = [frame for frame in frames if label(frame) not in ticked]
        if len(answers["drop"]) == len(frames):
            return "### Not saved. At least one picture must stay ticked."
        done.set()
        return "## Saved. You can close this browser tab and go back to the terminal."

    with gr.Blocks(title="Court set-up") as demo:
        gr.Markdown(
            f"# Court set-up for {video}\n"
            "## 1. Choose the size of your court.\n"
            "## 2. Look at the reference pictures. Untick any that look wrong "
            "(very blurred, or not showing the court).\n"
            "## 3. Press the big \"Save and close\" button at the bottom.\n"
            "Each end of the court that the video shows needs at least one picture where its lines are easy to "
            "see. If one is missing, add a picture at a time when the camera looks there.")
        with gr.Row():
            preset = gr.Radio([(f"{PRESET_LABELS[name]}: {size[0]} x {size[1]} ft", name)
                               for name, size in court_model.PRESETS.items()],
                              value=court["preset"] if court["preset"] in court_model.PRESETS else None,
                              label="Court size")
            length = gr.Number(court["length_ft"], label="Court length in feet (type your own if different)")
            width = gr.Number(court["width_ft"], label="Court width in feet")
        gallery = gr.Gallery(gallery_items(), columns=4, label="Reference pictures (time in the video)",
                             height="auto", format="jpeg")
        ticked = gr.CheckboxGroup([label(frame) for frame in frames], value=[label(frame) for frame in frames],
                                  label="Pictures to use. Untick any that look wrong.")
        with gr.Row():
            add_time = gr.Textbox(label="Add a picture at this time in the video (minutes:seconds, like 12:35)")
            add_button = gr.Button("Add this picture")
        message = gr.Markdown()
        save_button = gr.Button("Save and close", variant="primary", size="lg")

        preset.input(choose_preset, preset, [length, width])
        add_button.click(add_picture, [add_time, ticked], [gallery, ticked, message])
        save_button.click(save_and_close, [length, width, ticked], message)

    click_tools.run_page(demo, done, temp_dir)
    return answers


# ---------------------------------------------------------------- putting it together

def build_setup(video, frames, court, info, dropped):
    """Link the views and return the contents of court_setup.json."""
    views = [{"frame": frame, "picture": f"court_views/view_{frame:06d}.jpg"} for frame in frames]
    print(f"Working out how the {len(views)} reference pictures overlap ...")
    view_features = {view["frame"]: court_mapper.features(court_mapper.load_view_picture(video, view))
                     for view in views}
    main, to_main, links, unlinked = court_mapper.link_views(view_features, info["width"], info["height"])
    for frame in unlinked:
        dropped.append({"frame": frame, "reason": "it does not overlap the other pictures enough to be linked to them"})
    return {"video": video, "width": info["width"], "height": info["height"], "court": court, "main_view": main,
            "views": [{"frame": view["frame"], "time": time_label(view["frame"], info["fps"]),
                       "picture": view["picture"], "to_main": to_main[view["frame"]].tolist()}
                      for view in views if view["frame"] in to_main],
            "links": [link for link in links if link[0] in to_main and link[1] in to_main],
            "dropped": dropped}


def run(video=config.DEFAULT_VIDEO, answers=None, rescan=False, add_time=None, use_page=True):
    """Choose court size and reference views for a video and write court_setup.json."""
    info = video_info(video)
    setup_file = config.data_dir(video) / "court_setup.json"
    old = config.load_json(setup_file) if setup_file.exists() else None
    show_page = use_page and answers is None and not add_time
    answers = answers or {}
    court = old["court"] if old else court_choice(config.COURT_LENGTH_FT, config.COURT_WIDTH_FT)

    # Which frames are the views?
    frames = []
    wanted = []
    if "frames" in answers:
        wanted = list(answers["frames"])
    elif old and not rescan:
        frames = [view["frame"] for view in old["views"]]
        print(f"Keeping the {len(frames)} reference pictures chosen before (use --rescan to choose again).")
    else:
        frames = scan_views(video, info)
        # Pictures that carry your landmark clicks or hoop marks are kept as well, so those stay valid.
        landmark_views, hoop_views = marked_views(video)
        for frame in sorted(set(landmark_views + hoop_views)):
            if frame not in frames and view_file(video, frame).exists():
                frames.append(frame)
        frames.sort()
    wanted += answers.get("add", [])
    if add_time:
        try:
            wanted.append(time_to_frame(add_time, info["fps"]))
        except ValueError:
            sys.exit(f"\"{add_time}\" is not a time. Type it like --add-time 12:35 (minutes:seconds).")
    added = add_views(video, frames, wanted)
    for frame in wanted:
        if frame >= info["frame_count"]:
            print(f"NOTE: the video is only {time_label(info['frame_count'], info['fps'])} long, "
                  "so no picture could be added at the time asked for.")
        elif frame not in frames:
            print(f"NOTE: the video could not be read at {time_label(frame, info['fps'])} (frame {frame}); "
                  "no picture was added there.")
    if added:
        times = ", ".join(time_label(frame, info["fps"]) for frame in added)
        print(f"Added {len(added)} picture(s), at {times}.")
    if not frames:
        sys.exit(f"No usable picture was found in {video}.")

    if show_page:
        answers = open_page(video, frames, court, info)
        if not answers:
            sys.exit("Closed without saving. Nothing was changed.")
    if "court" in answers:
        try:
            check_court_size(answers["court"]["length_ft"], answers["court"]["width_ft"])
        except ValueError as problem:
            sys.exit(str(problem))
        court = court_choice(answers["court"]["length_ft"], answers["court"]["width_ft"])
    dropped = [{"frame": frame, "reason": "unticked by the coach"} for frame in answers.get("drop", [])
               if frame in frames]
    frames = [frame for frame in frames if frame not in answers.get("drop", [])]
    if not frames:
        sys.exit("Every reference picture was dropped. Nothing was saved.")

    setup = build_setup(video, frames, court, info, dropped)
    config.save_json(setup_file, setup, indent=1)
    court_mapper.forget(video)
    # Tidy up: pictures in court_views/ that are no longer views.
    used = {view_file(video, view["frame"]) for view in setup["views"]}
    for path in (config.data_dir(video) / "court_views").glob("view_*.jpg"):
        if path not in used:
            path.unlink()
    sheet = save_sheet(video, [view["frame"] for view in setup["views"]], info["fps"], setup["main_view"])

    print()
    print(f"Court set-up for {video}")
    print(f"  court size: {court['length_ft']} x {court['width_ft']} ft ({court['preset'].replace('_', ' ')})")
    print(f"  reference pictures: {len(setup['views'])}, at " + ", ".join(view["time"] for view in setup["views"]))
    print(f"  main picture: {time_label(setup['main_view'], info['fps'])} (frame {setup['main_view']})")
    if setup["links"]:
        weakest = min(link[2] for link in setup["links"])
        print(f"  overlaps found between pictures: {len(setup['links'])} (the weakest has {weakest} matching points; "
              f"{court_mapper.MIN_INLIERS} is the minimum)")
    for item in dropped:
        print(f"  NOT used: picture at {time_label(item['frame'], info['fps'])} (frame {item['frame']}): "
              f"{item['reason']}")
    if len(dropped) > len(answers.get("drop", [])):
        print("  To use a picture that could not be linked, add another one from a moment when the camera was")
        print("  between that part of the gym and the rest (--add-time mm:ss), then run this again.")
    print(f"  Wrote {setup_file}")
    print(f"  Look at the pictures: {sheet}")
    kept = {view["frame"] for view in setup["views"]}
    landmark_views, hoop_views = marked_views(video)
    lost_clicks = [frame for frame in landmark_views if frame not in kept]
    if any(frame not in kept for frame in hoop_views):
        print("  NOTE: a hoop was marked on a picture that is no longer used. Mark it again:")
        print(f"    python src/select_hoops.py --video {video}")
    if lost_clicks:
        print(f"  NOTE: {len(lost_clicks)} of your landmark clicks were on pictures that are no longer used.")
        print("  Open the landmark page, check the court and save again:")
        print(f"    python src/select_court_keypoints.py --video {video}")
    elif landmark_views:
        print(f"  Your landmark clicks are kept. Next:  python src/main.py --video {video}")
    else:
        print(f"  Next step:  python src/select_court_keypoints.py --video {video}")
    return setup


if __name__ == "__main__":
    args = parse_args()
    run(args.video, answers=click_tools.load_answers(args.answers),
        rescan=args.rescan, add_time=args.add_time, use_page=not args.no_page)
