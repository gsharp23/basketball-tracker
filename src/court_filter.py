"""Keep the tracking to the court: people on the bench, the scorer's table or in the stands are not tracked.

After the court stage knows where the floor is in each frame (court_frames.json), every Player and Referee
box whose foot point (the middle of the box's bottom edge) lands outside the court, with a margin of 3% of
the court's size so a player standing on the sideline stays, is moved out of the frame's "players" / "refs"
lists into its "off_court" list in tracks.json. Every later step (teams, jersey numbers, the court view,
the stats, shot tagging) reads "players" and "refs", so a bench player never gets a jersey number, a team
colour or a stat line. The ball is never touched: it goes out of bounds. A frame the court could not be
mapped in is left as it is, and a box whose feet are cut off by the bottom edge too (its foot is unknown).
Running it again first puts every box back and judges afresh, so a re-marked court changes nothing twice.

    apply(video) -> {"judged", "dropped", "kept", "frames_unjudged"}; prints one line; rewrites tracks.json
    inside(x, y, length, width, margin=MARGIN) -> bool          (the rule, for the tests)
    python src/court_filter.py --video clips/game1.mp4         (by hand; main.py's court stage calls apply())
"""
import argparse
import json
import os

import config
import court_mapper

MARGIN = 0.03           # of the court's length and width, outward


def inside(x, y, length, width, margin=MARGIN):
    """True when a court position (feet) is on the court or no further outside it than the margin."""
    return -margin * length <= x <= length * (1 + margin) and -margin * width <= y <= width * (1 + margin)


def foot_point(box, picture_height):
    """The middle of the bottom edge of a box, or None when the feet are cut off by the bottom of the picture."""
    x1, y1, x2, y2 = box["bbox"]
    if y2 >= picture_height - court_mapper.CUT_OFF_PX:
        return None
    return [(x1 + x2) / 2, y2]


def restore(record):
    """Put the boxes an earlier run moved out back where they came from (so a run can judge afresh)."""
    for box in record.get("off_court", []):
        box = dict(box)
        kind = box.pop("cls", "Player")
        record["refs" if kind == "Referee" else "players"].append(box)
    record["off_court"] = []


def judge_frame(record, H, court, picture_height):
    """Move the Player and Referee boxes outside the court into record["off_court"]. Returns (kept, dropped)."""
    kept = dropped = 0
    length, width = court[0], court[1]
    for key, kind in (("players", "Player"), ("refs", "Referee")):
        keep = []
        boxes = record.get(key) or []
        feet = [foot_point(box, picture_height) for box in boxes]
        judged = [index for index, foot in enumerate(feet) if foot is not None]
        spots = court_mapper.to_court(H, [feet[index] for index in judged]) if judged else []
        where = dict(zip(judged, spots))
        for index, box in enumerate(boxes):
            if index in where and not inside(float(where[index][0]), float(where[index][1]), length, width):
                record["off_court"].append(dict(box, cls=kind))
                dropped += 1
            else:
                keep.append(box)
                kept += 1
        record[key] = keep
    return kept, dropped


def apply(video):
    folder = config.data_dir(video)
    path = folder / "tracks.json"
    tracks = config.load_json(path)
    court = court_mapper.court_size(video)
    homographies = court_mapper.load_homographies(video)
    picture_height = tracks.get("height") or 1080
    counts = {"judged": 0, "dropped": 0, "kept": 0, "frames_unjudged": 0}
    for record in tracks["frames"]:
        restore(record)
        H = homographies.get(record["frame"])
        if H is None:
            counts["frames_unjudged"] += 1
            continue
        kept, dropped = judge_frame(record, H, court, picture_height)
        counts["judged"] += 1
        counts["kept"] += kept
        counts["dropped"] += dropped
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8") as out:
        json.dump(tracks, out)
    os.replace(temp, path)
    court_mapper.forget(video)
    print(f"  Ignored {counts['dropped']:,} detections outside the court (bench, table, stands); "
          f"{counts['kept']:,} kept in {counts['judged']:,} frames" + (f"; {counts['frames_unjudged']} frames had no court mapping" if counts["frames_unjudged"] else "") + ".")
    return counts


def parse_args():
    parser = argparse.ArgumentParser(description="Move the detections outside the court out of the way.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO)
    return parser.parse_args()


if __name__ == "__main__":
    config.require_video(parse_args().video)
    apply(parse_args().video)
