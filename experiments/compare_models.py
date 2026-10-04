# usage (from the project root): python experiments/compare_models.py <weights> <label>
import sys, time, collections, cv2
from ultralytics import YOLO
w, label = sys.argv[1], sys.argv[2]
m = YOLO(w); names = m.names
cap = cv2.VideoCapture('clips/test.mp4')
counts = collections.defaultdict(list); confs = collections.defaultdict(list)
i = n = 0; t = 0.0
while True:
    ok, frame = cap.read()
    if not ok: break
    if i % 10 == 0:
        t0 = time.time(); r = m(frame, verbose=False, conf=0.25)[0]; t += time.time() - t0
        c = collections.Counter(names[int(k)] for k in r.boxes.cls)
        for k in set(names.values()): counts[k].append(c.get(k, 0))
        for k, cf in zip(r.boxes.cls, r.boxes.conf): confs[names[int(k)]].append(float(cf))
        if n == 30: cv2.imwrite(f'outputs/compare_{label}.jpg', r.plot())
        n += 1
    i += 1
print(f'{label}: {n} frames sampled, {t/n*1000:.0f} ms per frame ({n/t:.1f} fps)')
for k in sorted(counts, key=lambda k: -sum(counts[k])):
    v = counts[k]
    if sum(v) == 0: continue
    print(f'  {k:14s} avg {sum(v)/len(v):4.1f}/frame | seen in {100*sum(1 for x in v if x)/len(v):3.0f}% of frames | avg confidence {sum(confs[k])/len(confs[k]):.2f}')
