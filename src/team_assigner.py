"""Stage 1, step 2: decide which team each tracked player belongs to, from jersey colour.

    python src/team_assigner.py --video clips/test.mp4

Run the detection first (main.py does). This step does not load any detection model; it only
reads tracks.json and the video frames, so it is quick.

What it does
    1. For each player ID, picks up to 30 of its boxes, spread over the time it was tracked.
    2. From each box takes the chest patch (the middle of the upper body) and works out
       its average colour.
    3. Splits all those colours into two groups with KMeans. The two groups are the two teams.
    4. Each box votes for the team colour it is nearer to, or for "neither" when it is far
       from both (a spectator or coach). The player ID goes to whatever gets the most votes.
    5. Names each team from its jersey colour ("red", "green", ...).
    6. Writes data/<video>/teams.json and a picture to check by eye,
       outputs/<video>/teams_sheet.jpg.

Known limits
    A player ID that is only ever seen cut off by the edge of the picture gets no team
    (null). So does one whose colour matches neither team. Someone on the sideline who
    wears a team's colour will be put in that team; colour alone cannot tell.
    The colours are always split into two groups, also when only one team is in the
    video (a short clip of one end of the court, say). The "second team" is then made
    up of a few players of the first, or of bystanders. Three simple checks catch the
    cases that were tried and print a warning, but they cannot catch every case:
    look at teams_sheet.jpg.
"""
import argparse
import sys
from collections import Counter

import cv2
import numpy as np
from sklearn.cluster import KMeans

import config

# Look at no more than this many boxes per player ID. More would only be slower.
SAMPLES_PER_TRACK = 30

# The chest patch, as fractions of the player's box: the middle 40% of the width,
# and from 15% to 45% of the way down. That skips the head, the arms and the legs.
PATCH_LEFT, PATCH_RIGHT = 0.30, 0.70
PATCH_TOP, PATCH_BOTTOM = 0.15, 0.45

# Every chest patch is shrunk to this many pixels a side. It is all the colour we need.
PATCH_SIZE = 8

# A box this close (in pixels) to the edge of the picture counts as cut off by it.
EDGE_MARGIN = 5

# Colours are compared in "Lab", where the distance between two colours matches how
# different they look to the eye (about 2 is barely visible, 100 is black against white).
# A chest patch further than this from BOTH team colours votes "neither".
# Measured on the test clip: real jersey patches were at most 20 away, and people in
# clearly different colours were 40 or more away. Someone in a dark colour, in shadow,
# was only 22 to 27 away, so this test does not catch every bystander.
UNKNOWN_DISTANCE = 30

# Signs that there are not really two teams in the video (see check_two_teams).
# Measured on cut-down copies of the test clip that show one team only: the two colour
# groups were 9 to 10 apart, or else the "second team" had 1 or 2 player IDs and 2% to
# 17% of the patches. With both teams in view: 17 apart, 8 IDs each, 45% of the patches.
MIN_COLOUR_GAP = 13
MIN_TEAM_IDS = 3
MIN_TEAM_SHARE = 0.10

# The check-by-eye picture shows at most this many player IDs (the longest-tracked ones).
SHEET_MAX_TRACKS = 150
SHEET_CROPS_PER_TRACK = 6
SHEET_CROP_SIZE = (48, 72)      # width, height of each small picture


def parse_args():
    parser = argparse.ArgumentParser(description="Decide which team each tracked player belongs to.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="video file, e.g. clips/test.mp4")
    return parser.parse_args()


def box_is_usable(bbox, width, height):
    """False when the player is cut off by the picture edge so the chest is not where we expect."""
    x1, y1, x2, y2 = bbox
    # Cut by the left, right or top edge: the middle of the box is no longer the chest.
    if x1 <= EDGE_MARGIN or x2 >= width - EDGE_MARGIN or y1 <= EDGE_MARGIN:
        return False
    # Cut by the bottom edge is fine if only the legs are missing. A short, wide box
    # there means only head and shoulders are in view.
    if y2 >= height - EDGE_MARGIN and (y2 - y1) < 2 * (x2 - x1):
        return False
    return True


def pick_samples(tracks):
    """Choose which boxes to look at.

    Returns  {frame number: [(player ID, bbox), ...]}  and  {player ID: number of boxes it has}.
    """
    usable = {}         # player ID -> [(frame, bbox), ...] for boxes not cut off
    box_counts = {}
    for record in tracks["frames"]:
        for player in record["players"]:
            box_counts[player["id"]] = box_counts.get(player["id"], 0) + 1
            if box_is_usable(player["bbox"], tracks["width"], tracks["height"]):
                usable.setdefault(player["id"], []).append((record["frame"], player["bbox"]))

    by_frame = {}
    for track_id, boxes in usable.items():
        # Spread the samples evenly from the first box to the last.
        count = min(SAMPLES_PER_TRACK, len(boxes))
        for i in sorted(set(np.linspace(0, len(boxes) - 1, count).astype(int))):
            frame, bbox = boxes[i]
            by_frame.setdefault(frame, []).append((track_id, bbox))
    return by_frame, box_counts


def read_patches(video, by_frame, sheet_ids):
    """Go through the video once and cut out the chest patch of every chosen box.

    Returns a list of (player ID, patch) and, for the check-by-eye picture,
    {player ID: [small upper-body pictures]} for the IDs in sheet_ids.
    """
    cap = cv2.VideoCapture(str(config.video_path(video)))
    patches = []
    sheet_crops = {}
    last_frame = max(by_frame)
    for index in range(last_frame + 1):
        if index % 3000 == 0 and index > 0:
            print(f"  read {index} of {last_frame} frames")
        if index not in by_frame:
            if not cap.grab():      # skip this frame without unpacking the picture
                break
            continue
        ok, frame = cap.read()
        if not ok:
            break
        for track_id, bbox in by_frame[index]:
            x1, y1, x2, y2 = bbox
            w, h = x2 - x1, y2 - y1
            patch = frame[int(y1 + PATCH_TOP * h):int(y1 + PATCH_BOTTOM * h),
                          int(x1 + PATCH_LEFT * w):int(x1 + PATCH_RIGHT * w)]
            if patch.size == 0:
                continue
            # Shrinking with INTER_AREA averages the pixels, which also smooths video noise.
            patches.append((track_id, cv2.resize(patch, (PATCH_SIZE, PATCH_SIZE), interpolation=cv2.INTER_AREA)))

            if track_id in sheet_ids:
                # Top 60% of the box (head to waist), enough to recognise the jersey by eye.
                upper = frame[int(y1):int(y1 + 0.6 * h), int(x1):int(x2)]
                sheet_crops.setdefault(track_id, []).append(cv2.resize(upper, SHEET_CROP_SIZE))
    cap.release()
    return patches, sheet_crops


def to_lab(patch):
    """The pixels of a small BGR picture as Lab colours, one row per pixel."""
    return cv2.cvtColor(patch.astype(np.float32) / 255, cv2.COLOR_BGR2LAB).reshape(-1, 3)


def lab_to_bgr(lab):
    bgr = cv2.cvtColor(np.float32([[lab]]), cv2.COLOR_LAB2BGR)[0, 0] * 255
    return [int(round(float(value))) for value in np.clip(bgr, 0, 255)]


def vote(patches):
    """Split the chest colours into two teams and let every patch vote.

    Returns one vote per patch: 0, 1 or None (matches neither team), and how far apart
    the two team colours are.
    Team 0 is always the lighter of the two colours, so the numbering does not change between runs.
    """
    colours = np.array([to_lab(patch).mean(axis=0) for _, patch in patches])
    kmeans = KMeans(n_clusters=2, n_init=10, random_state=0).fit(colours)
    centres = kmeans.cluster_centers_
    if centres[0][0] < centres[1][0]:       # [0] is lightness
        centres = centres[::-1]

    votes = []
    for colour in colours:
        distances = np.linalg.norm(centres - colour, axis=1)
        nearest = int(np.argmin(distances))
        votes.append(nearest if distances[nearest] <= UNKNOWN_DISTANCE else None)
    return votes, float(np.linalg.norm(centres[0] - centres[1]))


def check_two_teams(votes, teams, colour_gap):
    """A warning (text) when the split into two teams does not look like two real teams, else None."""
    reasons = []
    if colour_gap < MIN_COLOUR_GAP:
        reasons.append("the two colours found are very close to each other")
    smaller_ids = min(sum(1 for team in teams.values() if team == index) for index in (0, 1))
    smaller_votes = min(votes.count(0), votes.count(1))
    if smaller_ids < MIN_TEAM_IDS or smaller_votes < MIN_TEAM_SHARE * (votes.count(0) + votes.count(1)):
        reasons.append("one of the two has very few players")
    if not reasons:
        return None
    return ("The split into two teams may not be reliable (" + " and ".join(reasons) + "). "
            "This happens when only one team is in the video. Check teams_sheet.jpg before using the teams.")


def decide_tracks(patches, votes, track_ids):
    """Majority vote per player ID. Returns {ID: 0, 1 or None} and {ID: [votes for 0, for 1, for neither]}."""
    counts = {track_id: [0, 0, 0] for track_id in track_ids}
    for (track_id, _), choice in zip(patches, votes):
        counts[track_id][2 if choice is None else choice] += 1

    teams = {}
    for track_id, (team0, team1, neither) in counts.items():
        if team0 > team1 and team0 > neither:
            teams[track_id] = 0
        elif team1 > team0 and team1 > neither:
            teams[track_id] = 1
        else:
            teams[track_id] = None      # no usable boxes, mostly "neither", or a tie
    return teams, counts


def jersey_colours(patches, votes):
    """The jersey colour of each team, as [team 0 Lab colour, team 1 Lab colour].

    A chest patch also holds floor, skin and hair, which both teams share, so a plain
    average comes out a muddy grey for both. Instead: sort all the pixels into 6 common
    colours, then for each team take the colour it has much more of than the other team.
    """
    pixels = [[], []]
    for (_, patch), choice in zip(patches, votes):
        if choice is not None:
            pixels[choice].append(to_lab(patch))
    # 2000 patches a team is plenty to find the colours, and keeps a full game quick.
    pixels = [np.vstack(group[::max(1, len(group) // 2000)]) for group in pixels]

    palette = KMeans(n_clusters=6, n_init=3, random_state=0).fit(np.vstack(pixels))
    share0 = np.bincount(palette.predict(pixels[0]), minlength=6) / len(pixels[0])
    share1 = np.bincount(palette.predict(pixels[1]), minlength=6) / len(pixels[1])
    return [palette.cluster_centers_[np.argmax(share0 - share1)],
            palette.cluster_centers_[np.argmax(share1 - share0)]]


def colour_name(bgr):
    """A plain name for a colour, for example "red" or "green"."""
    hue, saturation, value = (int(v) for v in cv2.cvtColor(np.uint8([[bgr]]), cv2.COLOR_BGR2HSV)[0, 0])
    # hue is 0-179 around the colour wheel; saturation and value (brightness) are 0-255.
    if value < 45:
        return "black"
    if saturation < 60:
        # Hardly any colour. On video a white jersey is never pure white, it looks
        # light grey, so the bar for "white" is set low.
        return "white" if value > 110 else "grey"
    for limit, name in [(10, "red"), (22, "orange"), (35, "yellow"), (85, "green"), (135, "blue"), (165, "purple")]:
        if hue < limit:
            return name
    return "red"        # the colour wheel wraps round to red again


def make_sheet(sheet_crops, teams, counts, team_info):
    """One picture with the jersey crops grouped by team, to check the result by eye."""
    crop_w, crop_h = SHEET_CROP_SIZE
    label_w = 150
    sheet_w = label_w + SHEET_CROPS_PER_TRACK * crop_w
    font = cv2.FONT_HERSHEY_SIMPLEX
    rows = []

    groups = [(0, f"Team 0: {team_info['0']['label']}", team_info["0"]["color_bgr"]),
              (1, f"Team 1: {team_info['1']['label']}", team_info["1"]["color_bgr"]),
              (None, "No team (unknown)", [0, 0, 0])]
    for team, title, colour in groups:
        total = sum(1 for value in teams.values() if value == team)
        ids = [track_id for track_id in sheet_crops if teams[track_id] == team]
        ids.sort(key=lambda track_id: -sum(counts[track_id]))
        header = np.full((30, sheet_w, 3), 60, np.uint8)
        cv2.rectangle(header, (4, 4), (26, 26), colour, -1)         # swatch of the team colour
        cv2.rectangle(header, (4, 4), (26, 26), (255, 255, 255), 1)
        text = f"{title} - {total} IDs"
        if len(ids) < total:
            # IDs only ever seen cut off have no picture; on a full game only the longest are kept.
            text += f", {len(ids)} pictured"
        cv2.putText(header, text, (34, 21), font, 0.45, (255, 255, 255), 1)
        rows.append(header)
        for track_id in ids:
            row = np.zeros((crop_h, sheet_w, 3), np.uint8)
            team0, team1, neither = counts[track_id]
            cv2.putText(row, f"ID {track_id}", (6, 28), font, 0.6, (255, 255, 255), 1)
            cv2.putText(row, f"votes {team0}/{team1}/{neither}", (6, 52), font, 0.4, (200, 200, 200), 1)
            for i, crop in enumerate(sheet_crops[track_id]):
                crop = crop.copy()
                # Green rectangle = the chest patch whose colour was used.
                cv2.rectangle(crop,
                              (int(PATCH_LEFT * crop_w), int(PATCH_TOP / 0.6 * crop_h)),
                              (int(PATCH_RIGHT * crop_w), int(PATCH_BOTTOM / 0.6 * crop_h)), (0, 255, 0), 1)
                row[:, label_w + i * crop_w:label_w + (i + 1) * crop_w] = crop
            rows.append(row)
    return np.vstack(rows)


def run(video):
    """Do the whole step for one video. Returns what was written to teams.json."""
    config.require_video(video)
    tracks_file = config.data_dir(video) / "tracks.json"
    config.require_file(tracks_file, f"python src/main.py --video {video}")
    tracks = config.load_json(tracks_file)

    by_frame, box_counts = pick_samples(tracks)
    track_ids = sorted(box_counts)
    sample_count = sum(len(boxes) for boxes in by_frame.values())
    print(f"Assigning teams for {video}: {len(track_ids)} player IDs.")
    if sample_count < 2:
        sys.exit("There are not enough usable player boxes in tracks.json to tell two teams apart.\n"
                 "If that cannot be right, do the detection again:  "
                 f"python src/main.py --video {video} --from detect")
    print(f"Looking at {sample_count} chest patches in {len(by_frame)} frames...")

    # Keep small pictures only for the longest-tracked IDs, and only a few each,
    # so a full game does not fill the memory.
    sheet_ids = set(sorted(track_ids, key=lambda track_id: -box_counts[track_id])[:SHEET_MAX_TRACKS])
    patches, sheet_crops = read_patches(video, by_frame, sheet_ids)
    if len(patches) < 2:
        sys.exit(f"Could not read the chosen frames from {config.video_path(video)}.")
    for track_id, crops in sheet_crops.items():
        step = max(1, len(crops) // SHEET_CROPS_PER_TRACK)
        sheet_crops[track_id] = crops[::step][:SHEET_CROPS_PER_TRACK]

    votes, colour_gap = vote(patches)
    if 0 not in votes or 1 not in votes:
        sys.exit("Could not find two team colours in this video.")
    teams, counts = decide_tracks(patches, votes, track_ids)
    warning = check_two_teams(votes, teams, colour_gap)

    team_info = {}
    for team, lab in enumerate(jersey_colours(patches, votes)):
        bgr = lab_to_bgr(lab)
        team_info[str(team)] = {"label": colour_name(bgr), "color_bgr": bgr}
    if team_info["0"]["label"] == team_info["1"]["label"]:
        # Same colour name for both (say light blue against navy): tell them apart by lightness.
        team_info["0"]["label"] = "light " + team_info["0"]["label"]
        team_info["1"]["label"] = "dark " + team_info["1"]["label"]

    result = {
        "teams": team_info,
        "tracks": {str(track_id): teams[track_id] for track_id in track_ids},
        # The evidence behind each decision: [votes for team 0, for team 1, for neither].
        "votes": {str(track_id): counts[track_id] for track_id in track_ids},
    }
    if warning:
        result["warning"] = warning     # stats.py repeats it under the stats table
    teams_file = config.data_dir(video) / "teams.json"
    config.save_json(teams_file, result, indent=2)

    sheet_file = config.output_dir(video) / "teams_sheet.jpg"
    cv2.imwrite(str(sheet_file), make_sheet(sheet_crops, teams, counts, team_info))
    if cv2.imread(str(sheet_file)) is None:     # the SD card has corrupted files before
        print(f"WARNING: {sheet_file} could not be read back. Run the command again.")

    print_summary(result, box_counts)
    print(f"\nWrote {teams_file}")
    print(f"Wrote {sheet_file}  <- open this to check the teams by eye")
    return result


def print_summary(result, box_counts):
    teams, votes = result["tracks"], result["votes"]
    print()
    print("Summary")
    for team in (0, 1):
        ids = [track_id for track_id, value in teams.items() if value == team]
        print(f"  Team {team} ({result['teams'][str(team)]['label']}): {len(ids)} player IDs")
    unknown = [track_id for track_id, value in teams.items() if value is None]
    never_seen = [track_id for track_id in unknown if sum(votes[track_id]) == 0]
    print(f"  No team: {len(unknown)} player IDs "
          f"({len(never_seen)} only ever seen cut off by the picture edge, "
          f"{len(unknown) - len(never_seen)} matching neither team colour)")
    if "warning" in result:
        print(f"  WARNING: {result['warning']}")
    if len(teams) <= 40:
        print()
        print("  ID   boxes  team      votes (team 0 / team 1 / neither)")
        for track_id, team in teams.items():
            name = "-" if team is None else result["teams"][str(team)]["label"]
            team0, team1, neither = votes[track_id]
            print(f"  {track_id:>3}  {box_counts[int(track_id)]:>5}  {name:<8}  {team0} / {team1} / {neither}")


if __name__ == "__main__":
    run(parse_args().video)
