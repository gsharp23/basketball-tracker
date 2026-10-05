"""Small helpers shared by the three click pages (select_court.py,
select_court_keypoints.py, select_hoops.py).

The pages are made with Gradio and open in your web browser, but they are served by
this computer to this computer only (address 127.0.0.1). Nothing is uploaded, and the
"share" option of Gradio, which would put the page on the internet, is never used:
the pictures show children.

How a click becomes a pixel
    Gradio reports a click as [x, y] in pixels of the picture that was SENT to the
    browser, as long as the browser shows that picture at its own size or smaller.
    (Checked in the code of the installed Gradio 6.29.1.) If the browser had to
    ENLARGE a picture the numbers would be wrong. So the pages always send
    full-size pictures, and the "enlarged" picture for exact clicking is enlarged
    here in Python (zoom_picture), never by the browser.
"""
import json
import os
import shutil
import sys
import time

import cv2

ZOOM = 4            # the enlarged picture shows a quarter of the width and height, 4x bigger


def zoom_picture(picture, x, y):
    """Enlarge the area around a click for exact clicking.

    Returns (the enlarged picture, same size as the original, and (x0, y0) = the
    top-left corner of the enlarged area in the original picture).
    """
    height, width = picture.shape[:2]
    area_width, area_height = width // ZOOM, height // ZOOM
    # Centre the area on the click, but keep it inside the picture.
    x0 = int(min(max(x - area_width / 2, 0), width - area_width))
    y0 = int(min(max(y - area_height / 2, 0), height - area_height))
    return enlarge(picture, x0, y0), (x0, y0)


def enlarge(picture, x0, y0):
    """The area of the picture whose top-left corner is (x0, y0), made ZOOM times bigger."""
    height, width = picture.shape[:2]
    area_width, area_height = width // ZOOM, height // ZOOM
    area = picture[y0:y0 + area_height, x0:x0 + area_width]
    return cv2.resize(area, (area_width * ZOOM, area_height * ZOOM), interpolation=cv2.INTER_CUBIC)


def zoom_to_full(x0, y0, zoom_x, zoom_y):
    """A click in the enlarged picture -> the same spot in the original picture."""
    return x0 + zoom_x / ZOOM, y0 + zoom_y / ZOOM


def full_to_zoom(x0, y0, x, y):
    """A spot in the original picture -> where it is in the enlarged picture."""
    return (x - x0) * ZOOM, (y - y0) * ZOOM


def draw_cross(picture, x, y, colour, size=14, thickness=2):
    """Mark an exact spot with a cross that leaves the spot itself visible."""
    x, y = int(round(x)), int(round(y))
    gap = max(2, size // 4)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        cv2.line(picture, (x + dx * gap, y + dy * gap), (x + dx * size, y + dy * size), colour, thickness, cv2.LINE_AA)


def draw_label(picture, text, x, y, colour, scale=0.8):
    """Write text with a dark outline so it can be read on any background.

    The text is moved left or down if it would otherwise run off the picture.
    """
    (text_width, text_height), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    x = int(max(5, min(x, picture.shape[1] - text_width - 5)))
    y = int(max(text_height + 5, y))
    cv2.putText(picture, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(picture, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, colour, 2, cv2.LINE_AA)


def to_browser(picture):
    """OpenCV keeps colours as blue-green-red; the browser wants red-green-blue."""
    return cv2.cvtColor(picture, cv2.COLOR_BGR2RGB)


def load_answers(path):
    """Read an --answers file (None if no file was given). Stops with a plain message if it is missing."""
    if not path:
        return None
    if not os.path.exists(path):
        sys.exit(f"Cannot find the answers file {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except ValueError as problem:
        sys.exit(f"The answers file {path} is not valid JSON: {problem}")


def load_gradio(temp_dir):
    """Import Gradio with its usage reporting off and its temporary pictures in temp_dir.

    Both settings must be in place BEFORE Gradio is imported, which is why the pages
    call this instead of writing "import gradio" at the top of the file.
    """
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    os.environ["GRADIO_TEMP_DIR"] = str(temp_dir)
    import gradio
    return gradio


def run_page(demo, done_event, temp_dir):
    """Show a page on this computer only, wait for "Save and close", then shut it down."""
    try:
        # Plain system fonts: the default theme would make the browser fetch fonts from Google.
        gradio = load_gradio(temp_dir)
        theme = gradio.themes.Default(font=["Arial", "sans-serif"], font_mono=["Consolas", "monospace"])
        demo.launch(server_name="127.0.0.1", share=False, inbrowser=False, prevent_thread_lock=True,
                    quiet=True, theme=theme, footer_links=[])
        print()
        print(f"Open this address in your web browser:   {demo.local_url}")
        print("When you are done, press \"Save and close\" on the page.  (Ctrl+C here cancels.)", flush=True)
        done_event.wait()
        time.sleep(1.0)         # let the "saved" message reach the browser before the page goes away
    except KeyboardInterrupt:
        print("\nCancelled.")
    finally:
        # Also reached on Ctrl+C, so no server is ever left running.
        demo.close(verbose=False)
        shutil.rmtree(temp_dir, ignore_errors=True)
