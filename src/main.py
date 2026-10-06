"""Run the whole of stage 1 on one video with one command.

    python src/main.py --video clips/test.mp4

The stages, in order
    detect     detect_track.py    find and follow the players (the long one: hours for a full game)
    court      court_mapper.py    work out where the court is in every frame
    teams      team_assigner.py   which team each player is on
    jerseys    jersey_id.py       read the jersey numbers (also hours for a full game)
    tactical   tactical_view.py   positions in feet and the top-down video
    stats      stats.py           the per-player table

Stopping and starting
    A stage is skipped when its results are already there and are newer than the files
    they were made from. So after any stop (Ctrl+C, a crash, the laptop going to sleep)
    run the same command again: finished stages are skipped and a half-done stage
    carries on where it stopped.

    --force          do every stage again
    --from teams     do this stage again, and then every later stage that uses its
                     results (after "teams": tactical and stats, but not the hours of
                     jersey reading, which do not depend on the teams)
    If a run with --force or --from is stopped, run it again WITHOUT that option to
    carry on; with the option it would start that work from the beginning once more.

Two runs at once
    A second run on the same video is refused while the first is still going.

The log
    Everything printed is also added to data/<video>/run_log.txt, so the summaries of
    a long run can be read afterwards even if the terminal was closed.

Marking the court
    court, tactical and stats need the court to be marked first. That is done once per
    video, by you, in the browser (select_court.py, then select_court_keypoints.py).
    If it has not been done, the stages that do not need it (detect, teams, jerseys)
    run first, and then you are told exactly which commands to run.

Memory
    The stages run one after another in this one program. Only one of the two big models
    (the player finder, the number reader) is loaded at a time: each is dropped when its
    stage ends, and its memory is handed back before the next stage starts.
    Measured on the 20-second test clip: about 1.3 GB at the most.
"""
import argparse
import ctypes
import gc
import os
import sys
import time
from pathlib import Path

import config
import court_mapper
import detect_track
import jersey_id
import stats
import tactical_view
import team_assigner

STAGES = ["detect", "court", "teams", "jerseys", "tactical", "stats"]
WHAT_IT_DOES = {"detect": "find and follow the players",
                "court": "work out where the court is in every frame",
                "teams": "which team each player is on",
                "jerseys": "read the jersey numbers",
                "tactical": "positions in feet and the top-down video",
                "stats": "the per-player table"}
# These stages cannot run until the coach has marked the court for this video.
NEEDS_COURT = ["court", "tactical", "stats"]


def parse_args():
    parser = argparse.ArgumentParser(description="Run every stage on one video, skipping what is already done.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="video file (default clips/test.mp4)")
    parser.add_argument("--force", action="store_true", help="do every stage again")
    parser.add_argument("--from", dest="from_stage", choices=STAGES, metavar="STAGE",
                        help="do this stage again, and the later stages that use its results: " + ", ".join(STAGES))
    return parser.parse_args()


# ---------------------------------------------------------------- is a stage already done?

def stage_files(video, stage):
    """The files a stage is made from ("inputs") and the files it makes ("outputs")."""
    data, out = config.data_dir(video), config.output_dir(video)
    tracks = data / "tracks.json"
    files = {
        "detect": ([config.video_path(video)], [tracks]),
        "court": ([tracks, data / "court_setup.json", data / "court_calibration.json"], [data / "court_frames.json"]),
        "teams": ([tracks], [data / "teams.json"]),
        "jerseys": ([tracks], [data / "jerseys.json"]),
        "tactical": ([tracks, data / "court_frames.json", data / "teams.json", data / "jerseys.json"],
                     [data / "positions.json", out / "tactical.mp4"]),
        "stats": ([tracks, data / "teams.json", data / "jerseys.json", data / "positions.json", config.ROSTER_PATH],
                  [data / "stats.json", data / "stats.csv"]),
    }
    return files[stage]


def up_to_date(inputs, outputs):
    """True when every output file exists and none of them is older than any input file."""
    if not all(path.exists() for path in outputs):
        return False
    oldest_output = min(path.stat().st_mtime for path in outputs)
    return all(path.stat().st_mtime <= oldest_output for path in inputs if path.exists())


def half_done(video, stage):
    """True when the stage was stopped part-way and left progress that can be continued."""
    data = config.data_dir(video)
    if stage == "detect":
        # detect_track.py keeps its unfinished work in parts and deletes them when it reaches the end
        return any(detect_track.parts_dir(video).glob("part_*.json"))
    if stage == "jerseys":
        # only usable if it was made from the tracks.json we have now (the IDs change with every detection)
        return up_to_date([data / "tracks.json"], [data / "jerseys_partial.json"])
    if stage == "court":
        frames = data / "court_frames.json"
        return up_to_date(stage_files(video, "court")[0], [frames]) and not config.load_json(frames)["complete"]
    return False


def stage_status(video, stage):
    """"done", "half" (stopped part-way, can be continued) or "todo"."""
    if half_done(video, stage):
        return "half"
    if not up_to_date(*stage_files(video, stage)):
        return "todo"
    if stage == "detect" and not config.load_json(config.data_dir(video) / "tracks.json")["frames"]:
        return "todo"       # a tracks.json with nothing in it is not a finished detection
    return "done"


# ---------------------------------------------------------------- running the stages

def run_stage(video, stage, resume):
    """Run one stage by calling its own run() function. resume=True continues saved progress."""
    if stage == "detect":
        if not resume and half_done(video, "detect"):
            sys.exit("The detection of this video was stopped part-way and its progress is saved.\n"
                     "Doing it again from the start would throw that away, so nothing was done.\n"
                     f"To carry on with it:    python src/main.py --video {video}\n"
                     f"To really start again:  delete the folder {detect_track.parts_dir(video)} and run this again.")
        # When we get here without resume, doing it (again) from the first frame is what is wanted.
        detect_track.run(video, resume=resume, start_again=not resume)
    elif stage == "court":
        court_mapper.run(video, resume=resume)
    elif stage == "teams":
        team_assigner.run(video)
    elif stage == "jerseys":
        jersey_id.run(video, resume=resume)
    elif stage == "tactical":
        tactical_view.run(video)
    elif stage == "stats":
        stats.run(video, show_table=False)      # the table is printed once, at the very end


def free_memory():
    """Give the memory of the stage that just ended back to the computer.

    A stage's model (the player finder or the number reader) only lives inside that
    stage's run() call, so Python has already let go of it. But on Linux the freed
    memory stays reserved by this program until it is "trimmed".
    Measured after detection: 1136 MB still held before the trim, 426 MB after it.
    """
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass        # not Linux: there is nothing to trim


def stage_order(video):
    """The order the stages run in.

    Normally the order of STAGES. But if the court has not been marked yet, the stages
    that do not need it go first: they take hours, and that leaves you the whole time
    to mark the court (in a second terminal) before the court stage is reached.
    """
    if court_mapper.setup_missing(video):
        return [stage for stage in STAGES if stage not in NEEDS_COURT] + NEEDS_COURT
    return STAGES


def court_instructions(video):
    """What to tell the coach when the court has not been marked."""
    lines = [f"STOPPED: the court has not been marked for {video} yet, so the players cannot be placed on it.",
             "Detection, teams and jersey numbers are done and saved. Now do this, in this order",
             "(each command prints an address like http://127.0.0.1:7860; open it in your browser):", ""]
    for number, step in enumerate(court_mapper.setup_missing(video), 1):
        lines.append(f"  {number}. {step}")
    lines += [f"  Optional, only needed for shots later:  python src/select_hoops.py --video {video}",
              "", "Then run the same command again; it carries on from here:",
              f"  python src/main.py --video {video}"]
    return "\n".join(lines)


def nice_time(seconds):
    if seconds < 90:
        return f"{seconds:.0f} sec"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} hours"


def run(video=config.DEFAULT_VIDEO, redo_from=None):
    """Run every stage that is not already done.

    redo_from: do this stage again. The later stages that use its results then see
    that their own results are older, and are done again too.
    """
    config.require_video(video)
    config.lock_video(video)
    started = time.time()
    order = stage_order(video)
    print(f"Running the pipeline on {video}")
    if order != STAGES:
        print("NOTE: the court has not been marked for this video yet. Detection, teams and jersey numbers run")
        print("      first. You can mark the court meanwhile, in a second terminal. In that terminal first run")
        print(f"        cd {config.PROJECT_ROOT}")
        print("        source ~/venvs/hoops-tracker/bin/activate")
        print("      and then:")
        for step in court_mapper.setup_missing(video):
            print(f"        {step}")

    for count, stage in enumerate(order, 1):
        title = f"=== Stage {count} of {len(order)}: {stage} ({WHAT_IT_DOES[stage]})"
        if stage == "court" and court_mapper.setup_missing(video):
            sys.exit("\n" + court_instructions(video))
        status = "todo" if stage == redo_from else stage_status(video, stage)
        if status == "done":
            print(f"\n{title}\n    already done - skipped")
            continue
        print(f"\n{title}" + ("\n    carrying on where it stopped" if status == "half" else ""), flush=True)
        stage_started = time.time()
        try:
            run_stage(video, stage, resume=(status == "half"))
        except KeyboardInterrupt:
            sys.exit(f"\nStopped by Ctrl+C in the '{stage}' stage. To carry on, run this:\n"
                     f"  python src/main.py --video {video}")
        except SystemExit as stop:
            # The stage stopped itself with a plain message (stop.code): Ctrl+C, or a problem
            # it explains. Show its message, and add how to go on with the whole pipeline.
            if stop.code in (None, 0):
                raise
            message = stop.code if isinstance(stop.code, str) else ""
            sys.exit(f"{message}\n\nThe '{stage}' stage stopped (see above). To carry on with all the stages, run this\n"
                     f"(not a single-stage command shown above); finished stages are skipped and a half-done one continues:\n"
                     f"  python src/main.py --video {video}")
        free_memory()       # before the next stage loads its own model
        print(f"    '{stage}' finished in {nice_time(time.time() - stage_started)}")

    print_summary(video, time.time() - started)


# ---------------------------------------------------------------- the summary at the end

def windows_path(path):
    r"""The same file as Windows names it, so the coach can paste it into Explorer or Excel.

    A file on a Windows drive: /mnt/d/hoops-tracker/x.mp4 -> D:\hoops-tracker\x.mp4.
    A file on Ubuntu's own disk: /home/me/hoops-data/x.mp4 -> \\wsl.localhost\Ubuntu\home\me\hoops-data\x.mp4.
    """
    parts = Path(path).resolve().parts      # resolve() follows the links to the data folder
    if len(parts) > 2 and parts[1] == "mnt" and len(parts[2]) == 1:
        return parts[2].upper() + ":\\" + "\\".join(parts[3:])
    distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu")
    return f"\\\\wsl.localhost\\{distro}\\" + "\\".join(parts[1:])


class Tee:
    """Stands in for the screen: everything printed goes to the screen and to the log file."""

    def __init__(self, screen, log):
        self.screen, self.log = screen, log

    def write(self, text):
        self.screen.write(text)
        try:
            self.log.write(text)
        except OSError:
            pass            # the log file cannot be written: keep printing to the screen

    def flush(self):
        self.screen.flush()

    def __getattr__(self, name):
        return getattr(self.screen, name)       # anything else is answered by the real screen


def start_log(video):
    """From here on, also add everything that is printed to data/<video>/run_log.txt."""
    log = open(config.data_dir(video) / "run_log.txt", "a", encoding="utf-8", buffering=1)     # 1 = save line by line
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M')}  main.py --video {video}\n")
    sys.stdout = Tee(sys.stdout, log)
    sys.stderr = Tee(sys.stderr, log)


def print_summary(video, seconds):
    data, out = config.data_dir(video), config.output_dir(video)
    results = [("Players boxed, with tracking IDs (video)", out / "tracked.mp4"),
               ("Top-down court view (video)", out / "tactical.mp4"),
               ("Stats table (opens in Excel)", data / "stats.csv"),
               ("Stats with every detail", data / "stats.json")]
    results += [("Position map", path) for path in sorted(out.glob("position_map_*.png"))]
    results += [("Check by eye: the two teams", out / "teams_sheet.jpg"),
                ("Check by eye: the court lines", out / "court_check.jpg"),
                ("Check by eye: the jersey readings (folder)", config.CROPS_DIR / config.video_stem(video))]
    print()
    print(f"=== All stages are done for {video}  (this run took {nice_time(seconds)})")
    print()
    print("Where the results are (the second line is the same file in Windows):")
    for label, path in results:
        if path.exists():
            print(f"  {label}")
            print(f"      {path}")
            print(f"      {windows_path(path)}")
    print(f"  Everything printed during the runs: {data / 'run_log.txt'}")
    # The court summary was printed hours ago on a full game; repeat what may need doing.
    court = config.load_json(data / "court_frames.json")["summary"]
    print()
    print(f"Court mapping: {court['mapped']} of {court['frames']} frames looked at were mapped to the court.")
    court_mapper.print_unmapped(video, court)
    stats.print_table(config.load_json(data / "stats.json"))


if __name__ == "__main__":
    args = parse_args()
    config.require_video(args.video)        # before the log, so a mistyped name does not make a folder
    start_log(args.video)
    run(args.video, redo_from=STAGES[0] if args.force else args.from_stage)
