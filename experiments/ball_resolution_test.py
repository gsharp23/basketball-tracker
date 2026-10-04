# Tests ball detection at normal (640) and high (1280) resolution on 20 frames of clips/test.mp4.
# Saves a contact sheet of each top "ball" candidate to outputs/ so the hits can be checked by eye.
# usage (from the project root): python experiments/ball_resolution_test.py
import time, cv2, numpy as np
from ultralytics import YOLO
cfgs = [('player', 'models/player_detector.pt', 640), ('player', 'models/player_detector.pt', 1280)]
models = {}
cap = cv2.VideoCapture('clips/test.mp4'); frames = []; i = 0
while True:
    ok, f = cap.read()
    if not ok: break
    if i % 30 == 15: frames.append((i, f))
    i += 1
rows = []; times = {c: 0.0 for c in range(len(cfgs))}
for fi, f in frames:
    tiles = []; line = f'frame {fi:3d}:'
    for c, (name, w, sz) in enumerate(cfgs):
        m = models.setdefault(w, YOLO(w))
        t0 = time.time(); r = m(f, imgsz=sz, conf=0.15, verbose=False)[0]; times[c] += time.time() - t0
        ball_id = [k for k, v in m.names.items() if v == 'Ball'][0]
        b = [(float(cf), xy) for cl, cf, xy in zip(r.boxes.cls, r.boxes.conf, r.boxes.xyxy.tolist()) if int(cl) == ball_id]
        tile = np.zeros((240, 240, 3), np.uint8)
        if b:
            cf, (x1, y1, x2, y2) = max(b); cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
            x0 = min(max(cx - 60, 0), f.shape[1] - 120); y0 = min(max(cy - 60, 0), f.shape[0] - 120)
            tile = cv2.resize(f[y0:y0 + 120, x0:x0 + 120], (240, 240), interpolation=cv2.INTER_CUBIC)
            cv2.rectangle(tile, (int((x1 - x0) * 2), int((y1 - y0) * 2)), (int((x2 - x0) * 2), int((y2 - y0) * 2)), (0, 255, 0), 1)
            line += f' | {name}{sz}: ({cx},{cy}) {cf:.2f} n={len(b)}'
            cv2.putText(tile, f'{cf:.2f}', (4, 232), 0, 0.6, (0, 255, 255), 2)
        else:
            line += f' | {name}{sz}: none'
        cv2.putText(tile, f'f{fi} {name}{sz}', (4, 18), 0, 0.5, (0, 255, 255), 1)
        tiles.append(tile)
    rows.append(np.hstack(tiles)); print(line, flush=True)
for c, (name, w, sz) in enumerate(cfgs): print(f'{name}{sz}: {times[c] / len(frames):.1f} s per frame')
h = len(rows) // 2
cv2.imwrite('outputs/ball_exp_1.jpg', np.vstack(rows[:h])); cv2.imwrite('outputs/ball_exp_2.jpg', np.vstack(rows[h:]))
