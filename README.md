# basketball-tracker

Turns a full-court basketball game video into coaching information: who was on
the floor, where they were, which team had the ball, and per-player stats.
It is built to run on an ordinary laptop with no GPU (AMD Ryzen 5 4500U, 8 GB
RAM, WSL Ubuntu), so every design choice trades accuracy against CPU time.

**Status: setup and model selection done; tracking code not written yet.**
The files in `src/` are empty placeholders for the planned pipeline.

---

## Planned pipeline

| Stage | File | What it will do |
|---|---|---|
| 1 | `src/detect_track.py` | Detect players, referees, hoop and ball; give each player a stable ID |
| 2 | `src/select_court.py`, `src/select_court_keypoints.py` | Mark the court once by hand so positions can be mapped |
| 3 | `src/team_assigner.py` | Split players into two teams by jersey colour |
| 4 | `src/tactical_view.py` | Draw a top-down view of the court with player positions |
| 5 | `src/select_hoops.py` | Mark the hoops, for shot detection |
| 6 | `src/jersey_id.py` | Read jersey numbers and match them to `roster.json` |
| 7 | `src/stats.py`, `src/main.py` | Produce per-player stats and run the whole pipeline |

---

## What has been built so far

### 1. Environment (CPU only)

PyTorch CPU build, Ultralytics YOLO, Supervision, OpenCV, scikit-learn, EasyOCR
and Gradio, pinned in `requirements.txt`. ffmpeg comes from the
`imageio-ffmpeg` package, so nothing needs a system install.

### 2. Model comparison on real game footage

`experiments/compare_models.py` runs a model over a 20-second test clip
(1920x1080, 30 fps, fixed wide-angle gym camera) and reports what it finds.
Sixty frames were sampled per model.

| | Standard `yolov8n` | Basketball-trained `player_detector` | Basketball-trained `ball_detector_model` |
|---|---|---|---|
| Speed on this laptop | 4.5 frames/sec | 0.9 frames/sec | 0.9 frames/sec |
| Players: average confidence | 0.56 | 0.79 | 0.49 |
| Referee found | never (no such class) | 65% of frames | 47% of frames |
| Hoop found | never | 98% of frames | 77% of frames |
| Ball reported | never | 68% of frames | 82% of frames |

Findings:

- The basketball-trained player model is clearly better at players, referees
  and the hoop, but about five times slower. A full 52-minute game at every
  frame would take roughly 29 hours; sampling 10 frames per second brings that
  to about 10 hours.
- Ball detection is not reliable yet. The "ball reported" figures include false
  detections (for example a box on a player's legs while the referee held the
  ball), so they overstate the real hit rate.

The basketball-trained weights come from the MIT-licensed
[abdullahtarek/basketball_analysis](https://github.com/abdullahtarek/basketball_analysis)
project. They were trained on NBA broadcast footage, not gym footage. Both files
were checked with `picklescan` before being loaded.

### 3. Ball detection experiment

`experiments/ball_resolution_test.py` tests whether looking at the frame at
higher resolution helps find the ball, which is only a few pixels wide in
wide-angle footage. It takes 20 frames from the test clip, runs
`player_detector` at 640 and 1280 pixels, and saves a close-up of each top
"ball" candidate so every one can be checked by eye.

| `player_detector` | Normal (640) | High (1280) |
|---|---|---|
| Top candidate really was the ball | 3 of 20 frames | 3 of 20 frames |
| Top candidate was something else | 10 of 20 | 12 of 20 |
| Nothing reported | 7 of 20 | 5 of 20 |

Findings:

- Higher resolution did not help. The ball was found only when it was large
  and in the open (a dribble, or the referee holding it).
- Most false detections were players' elbows and forearms, some at 0.70-0.78
  confidence, so a confidence threshold cannot separate them from the real ball.
- Conclusion: this model cannot be relied on for the ball in this footage.
  The likely fix is fine-tuning on labelled frames from the team's own games.

---

## What is in the repo

```
src/            pipeline code (placeholders for now)
experiments/    one-off test scripts behind the results above
roster.json     jersey number -> player name (empty for now)
requirements.txt
clips/ models/ outputs/ crops/ data/   links to the SD card, not in git
```

The five data folders are links to `/mnt/d/hoops-tracker` on an SD card (`D:`),
so game videos, model weights and outputs never take space on `C:` and are never
committed. The SD card must be plugged in and mounted at `/mnt/d`.
No game footage or images of players are stored in this repository.

---

## Start working (every new terminal)

```bash
cd ~/aicloudops/basketball-tracker
source ~/venvs/hoops-tracker/bin/activate
```

To leave the environment: `deactivate`

## Reinstall from scratch

```bash
python3 -m venv ~/venvs/hoops-tracker
source ~/venvs/hoops-tracker/bin/activate
pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install --no-cache-dir -r requirements.txt
```

`--no-cache-dir` stops pip keeping a download cache on `C:`.
