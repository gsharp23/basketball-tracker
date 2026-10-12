/* 2 · Annotate: the box editor. One picture at a time from the queue (GET /api/training/queue), its boxes drawn
   over the picture in class colours (solid when the model is sure, dashed when not), drag to move, corner handles
   to resize, Delete to remove, B then drag to draw. Every answer is one small PUT (no picture work on the server).
   Undo keeps what each picture looked like before each change, in this page. */

renderSidebar('annotate');

const CLASS_COLOURS = { Ball: '#ffd21f', Referee: '#4fa3ff', Player: '#15c2a0', Hoop: '#ff7a1a' };
const SURE = 0.6;                 // a model box under this confidence is drawn dashed
const MIN_DRAW = 4;               // a drawn box smaller than this (picture pixels) is a slip, not a box
const INSET_ZOOM = 3;             // the zoom inset shows the picture 3x bigger than on screen

const page = {
  mode: 'hard', game: urlParam('game') || '',
  queue: [], pos: 0, counts: { pictures: 0, checked: 0, unchecked: 0, skipped: 0 },
  classes: Object.keys(CLASS_COLOURS), cls: 'Ball', assist: true, detector: null,
  current: null,                  // the answer of GET /api/training/frame/<game>/<frame>
  boxes: [], status: 'unchecked', // what is on the screen now (saved after every change)
  selected: -1, drawing: false, drag: null, pointer: null,
  history: [],                    // [{game, frame, before: {status, boxes}}] for Undo
  busy: false,
};

function num(n) { return Number(n || 0).toLocaleString('en-US'); }
function copy(value) { return JSON.parse(JSON.stringify(value)); }
function W() { return page.current.record.size[0]; }
function H() { return page.current.record.size[1]; }

/* ---- the queue ---- */

async function loadQueue() {
  const reply = await api(withParams('/api/training/queue', { mode: page.mode, game: page.game }));
  page.queue = reply.queue;
  page.pos = 0;
  page.counts = reply.counts;
  page.classes = reply.classes;
  page.detector = reply.detector;
  byId('shots-words').textContent = 'Every shot tagged in game analysis is already a labelled 3-second clip. '
    + num(reply.shots_tagged) + ' clip' + (reply.shots_tagged === 1 ? '' : 's') + ' so far.';
  if (!page.classes.includes(page.cls)) page.cls = page.classes[0];
  if (reply.job_running && !page.warnedJob) {
    page.warnedJob = true;
    toast('The Upload screen is still taking pictures or running label assist. You can check pictures now; an answer you save is kept.', '');
  }
  showClasses();
  showAssistWords();
  showProgress();
}

function gameWords(words) { return (words || '').replace(/\d{4}-\d{2}-\d{2}/, fmtDate); }   // "vs Oakmont · 2026-10-03" -> "... · 3 Oct 2026"

function showProgress() {
  const counts = page.counts;
  byId('progress-words').textContent = num(counts.checked) + ' of ' + num(counts.pictures) + ' checked';
  byId('progress-bar').style.width = (counts.pictures ? Math.round(counts.checked / counts.pictures * 100) : 0) + '%';
  const where = byId('progress-where');
  if (page.current) {
    const light = page.current.record.conditions.light === 'dim' ? 'dim gym' : 'good light';
    where.textContent = gameWords(page.current.words) + ' · ' + light;
  } else {
    where.textContent = page.game ? page.game : '';
  }
}

/* ---- the side cards ---- */

function showClasses() {
  byId('classes').replaceChildren(...page.classes.map(function (name, index) {
    const colour = CLASS_COLOURS[name] || '#8a919c';
    return el('button', { type: 'button', class: 'class-row' + (name === page.cls ? ' on' : ''), 'data-cls': name,
                          onclick: function () { chooseClass(name); } },
      [el('span', { class: 'swatch', style: 'background:' + colour }), name, el('span', { class: 'key' }, String(index + 1))]);
  }));
}

function chooseClass(name) {
  page.cls = name;
  showClasses();
  // a selected box takes the class: click a wrong box, press the right number
  if (page.selected >= 0 && page.boxes[page.selected] && page.boxes[page.selected].cls !== name) {
    remember();
    page.boxes[page.selected].cls = name;
    page.boxes[page.selected].source = 'coach';
    page.boxes[page.selected].conf = null;
    render();
    save();
  }
}

function showAssistWords() {
  const model = page.detector ? page.detector.label : 'The model';
  byId('assist-words').textContent = page.assist
    ? model + ' draws its guesses. Solid boxes are sure; dashed ones need your call.'
    : 'Off: the model\'s guesses are hidden, and "Looks right" keeps only the boxes you drew yourself.';
}

function showPicturePanel() {
  const current = page.current;
  byId('pic-game').textContent = current ? gameWords(current.words) : '—';
  byId('pic-time').textContent = current && current.record.time !== null && current.record.time !== undefined ? fmtClock(current.record.time) : '—';
  byId('pic-condition').textContent = current ? current.condition_words : '—';
  byId('pic-reason').textContent = current ? current.reason_words : '—';
}

function showFilmstrip() {
  const strip = byId('filmstrip');
  if (!page.current) { strip.replaceChildren(); return; }
  strip.replaceChildren(...page.current.neighbours.map(function (item) {
    const dot = item.status === 'checked' ? 'dot-green' : item.status === 'skipped' ? 'dot-grey' : 'dot-yellow';
    return el('a', { href: withParams('annotate.html', { game: item.game, frame: item.frame }), class: item.current ? 'on' : null,
                     title: 'Picture ' + item.frame, onclick: function (event) {
                       event.preventDefault(); tryToast(function () { return openFrame(item.game, item.frame); });
                     } },
      [el('img', { src: item.thumb, alt: '', loading: 'lazy' }), el('span', { class: 'dot ' + dot })]);
  }));
}

/* ---- showing a picture ---- */

function words(text, link) {
  const holder = byId('editor-words');
  holder.hidden = !text;
  holder.replaceChildren(text || '', link || '');
  byId('editor').classList.toggle('empty', !!text);     // no zoom inset over a message
}

function loadPicture(url) {
  return new Promise(function (done, fail) {
    const img = byId('picture');
    img.onload = function () { done(); };
    img.onerror = function () { fail(new Error('The picture could not be read from the data folder. Reload the page.')); };
    img.src = url;
  });
}

async function openFrame(game, frame) {
  page.busy = true;
  try {
    const reply = await api('/api/training/frame/' + encodeURIComponent(game) + '/' + frame);
    page.current = reply;
    page.boxes = copy(reply.record.boxes);
    page.status = reply.record.status;
    page.selected = -1;
    page.drawing = false;
    page.drag = null;
    byId('editor').classList.remove('drawing');
    await loadPicture(reply.url);
    const img = byId('picture');
    img.hidden = false;
    byId('editor').style.aspectRatio = W() + ' / ' + H();
    words('');
    byId('inset-words').hidden = false;
    render();
    showPicturePanel();
    showFilmstrip();
    showProgress();
    drawInset();
    preloadNext();
  } finally {
    page.busy = false;
  }
}

// The next picture of the queue is fetched now, so "Looks right · next" shows it at once.
function preloadNext() {
  const next = page.queue[page.pos + 1] || page.queue[page.pos];
  if (!next || !page.current || next.frame === page.current.record.frame) return;
  const img = new Image();
  img.src = page.current.url.replace(/[^/]+\/[^/]+$/, next.game + '/' + next.game + '_' + String(next.frame).padStart(6, '0') + '.jpg');
}

function showDone() {
  page.current = null;
  byId('picture').hidden = true;
  byId('boxes').replaceChildren();
  byId('inset-words').hidden = true;
  showPicturePanel();
  showFilmstrip();
  showProgress();
  if (!page.counts.pictures) {
    words('No pictures yet.', el('a', { href: 'upload.html' }, 'Get some from your games on the Upload screen →'));
  } else if (page.mode === 'unsure') {
    words('No picture the model is unsure about is left.', el('a', { href: '#', onclick: function (event) { event.preventDefault(); setMode('hard'); } }, 'Show the other pictures →'));
  } else {
    words('Every picture is checked: ' + num(page.counts.checked) + ' of ' + num(page.counts.pictures) + '.',
      el('a', { href: 'dataset.html' }, 'Build the dataset →'));
  }
}

async function openQueued() {
  if (!page.queue.length) { showDone(); return; }
  page.pos = Math.min(page.pos, page.queue.length - 1);
  const next = page.queue[page.pos];
  await openFrame(next.game, next.frame);
}

/* ---- drawing the boxes ---- */

function shownBoxes() {
  return page.boxes.map(function (box, index) { return { box: box, index: index }; })
    .filter(function (item) { return page.assist || item.box.source !== 'model'; });
}

function pct(value, whole) { return (value / whole * 100) + '%'; }

function render() {
  const holder = byId('boxes');
  if (!page.current) { holder.replaceChildren(); return; }
  holder.replaceChildren(...shownBoxes().map(function (item) {
    const box = item.box, colour = CLASS_COLOURS[box.cls] || '#8a919c';
    const unsure = box.source === 'model' && (box.conf || 0) < SURE;
    const label = box.cls + (unsure ? '?' : '') + (box.source === 'model' && box.conf !== null ? ' ' + box.conf.toFixed(2) : '');
    const node = el('div', {
      class: 'box cls-' + box.cls + (unsure ? ' unsure' : '') + (item.index === page.selected ? ' selected' : ''),
      'data-index': item.index,
      style: '--box:' + colour + ';left:' + pct(box.cx - box.w / 2, W()) + ';top:' + pct(box.cy - box.h / 2, H())
        + ';width:' + pct(box.w, W()) + ';height:' + pct(box.h, H()),
    }, [el('span', { class: 'label' }, label)]);
    for (const corner of ['nw', 'ne', 'sw', 'se']) node.appendChild(el('span', { class: 'handle ' + corner, 'data-corner': corner }));
    return node;
  }));
}

function toPicture(event) {
  const rect = byId('boxes').getBoundingClientRect();
  return { x: Math.min(Math.max((event.clientX - rect.left) / rect.width * W(), 0), W()),
           y: Math.min(Math.max((event.clientY - rect.top) / rect.height * H(), 0), H()) };
}

/* ---- the zoom inset: the picture around the pointer, 3x bigger than on screen ---- */

function drawInset() {
  const canvas = byId('inset'), img = byId('picture');
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#2b2b30';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!page.current || img.hidden || !img.naturalWidth) return;
  const rect = byId('boxes').getBoundingClientRect();
  const screenScale = rect.width / W();                         // screen px per picture px
  const side = canvas.width / (INSET_ZOOM * screenScale);         // picture px shown across the inset
  const at = page.pointer || { x: W() / 2, y: H() / 2 };
  const sx = at.x - side / 2, sy = at.y - side / 2;
  ctx.drawImage(img, sx, sy, side, side, 0, 0, canvas.width, canvas.height);
  const k = canvas.width / side;
  ctx.lineWidth = 2;
  shownBoxes().forEach(function (item) {
    const box = item.box;
    ctx.strokeStyle = CLASS_COLOURS[box.cls] || '#8a919c';
    ctx.setLineDash(box.source === 'model' && (box.conf || 0) < SURE ? [6, 4] : []);
    ctx.strokeRect((box.cx - box.w / 2 - sx) * k, (box.cy - box.h / 2 - sy) * k, box.w * k, box.h * k);
  });
  ctx.setLineDash([]);
  ctx.strokeStyle = 'rgba(255,255,255,0.7)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(canvas.width / 2, 0); ctx.lineTo(canvas.width / 2, canvas.height);
  ctx.moveTo(0, canvas.height / 2); ctx.lineTo(canvas.width, canvas.height / 2);
  ctx.stroke();
}

/* ---- moving, resizing, drawing with the pointer ---- */

function edges(box) { return { x1: box.cx - box.w / 2, y1: box.cy - box.h / 2, x2: box.cx + box.w / 2, y2: box.cy + box.h / 2 }; }
function fromEdges(box, e) {
  const x1 = Math.min(e.x1, e.x2), x2 = Math.max(e.x1, e.x2), y1 = Math.min(e.y1, e.y2), y2 = Math.max(e.y1, e.y2);
  box.cx = (x1 + x2) / 2; box.cy = (y1 + y2) / 2; box.w = x2 - x1; box.h = y2 - y1;
}

function onPointerDown(event) {
  if (!page.current || page.busy || event.button !== 0) return;
  const editor = byId('editor');
  editor.setPointerCapture(event.pointerId);
  const at = toPicture(event);
  const handle = event.target.closest('.handle');
  const boxNode = event.target.closest('.box');
  const snapshot = copy(page.boxes);
  if (handle && boxNode) {
    const index = Number(boxNode.dataset.index);
    page.selected = index;
    page.drag = { kind: 'resize', index: index, corner: handle.dataset.corner, start: at, orig: copy(page.boxes[index]), snapshot: snapshot, moved: false };
  } else if (boxNode && !page.drawing) {
    const index = Number(boxNode.dataset.index);
    page.selected = index;
    page.drag = { kind: 'move', index: index, start: at, orig: copy(page.boxes[index]), snapshot: snapshot, moved: false };
  } else if (page.drawing) {
    page.drag = { kind: 'draw', start: at, moved: false };
  } else {
    page.selected = -1;
  }
  render();
  event.preventDefault();
}

function onPointerMove(event) {
  if (!page.current) return;
  const at = toPicture(event);
  page.pointer = at;
  const drag = page.drag;
  if (drag) {
    const dx = at.x - drag.start.x, dy = at.y - drag.start.y;
    if (Math.abs(dx) > 1 || Math.abs(dy) > 1) drag.moved = true;
    if (drag.kind === 'move') {
      const box = page.boxes[drag.index], o = drag.orig;
      // the box keeps its size and stays inside the picture
      box.cx = Math.min(Math.max(o.cx + dx, o.w / 2), W() - o.w / 2);
      box.cy = Math.min(Math.max(o.cy + dy, o.h / 2), H() - o.h / 2);
      render();
    } else if (drag.kind === 'resize') {
      const e = edges(drag.orig);
      if (drag.corner.includes('w')) e.x1 = at.x; else e.x2 = at.x;
      if (drag.corner.includes('n')) e.y1 = at.y; else e.y2 = at.y;
      fromEdges(page.boxes[drag.index], e);
      render();
    } else if (drag.kind === 'draw') {
      const draft = byId('draft');
      draft.hidden = false;
      const x1 = Math.min(drag.start.x, at.x), y1 = Math.min(drag.start.y, at.y);
      draft.style.left = pct(x1, W()); draft.style.top = pct(y1, H());
      draft.style.width = pct(Math.abs(at.x - drag.start.x), W()); draft.style.height = pct(Math.abs(at.y - drag.start.y), H());
    }
  }
  drawInset();
}

function onPointerUp(event) {
  const drag = page.drag;
  if (!drag) return;
  page.drag = null;
  const at = toPicture(event);
  if (drag.kind === 'draw') {
    byId('draft').hidden = true;
    page.drawing = false;
    byId('editor').classList.remove('drawing');
    const w = Math.abs(at.x - drag.start.x), h = Math.abs(at.y - drag.start.y);
    if (w >= MIN_DRAW && h >= MIN_DRAW) {
      remember();
      page.boxes.push({ cls: page.cls, cx: (at.x + drag.start.x) / 2, cy: (at.y + drag.start.y) / 2, w: w, h: h, source: 'coach', conf: null });
      page.selected = page.boxes.length - 1;
      render();
      save();
    }
    return;
  }
  if (drag.moved) {
    const box = page.boxes[drag.index];
    if (box.w < MIN_DRAW || box.h < MIN_DRAW) {        // squeezed to nothing: put it back
      page.boxes[drag.index] = drag.orig;
      render();
      return;
    }
    // a box the coach touched is the coach's box now
    remember(drag.snapshot);
    box.source = 'coach';
    box.conf = null;
    render();
    save();
  }
}

function deleteSelected() {
  if (page.selected < 0 || !page.boxes[page.selected]) return;
  remember();
  page.boxes.splice(page.selected, 1);
  page.selected = -1;
  render();
  save();
}

function startDrawing() {
  if (!page.current) return;
  page.drawing = !page.drawing;
  page.selected = -1;
  byId('editor').classList.toggle('drawing', page.drawing);
  byId('btn-draw').classList.toggle('on', page.drawing);
  render();
  if (page.drawing) toast('Drag on the picture to draw a ' + page.cls + ' box.', '');
}

/* ---- saving, answering, undo ---- */

// Keep what this picture looked like before a change, for Undo. `boxes` is the state before the change
// (the boxes as they are now, unless a drag already moved them: then the copy taken when the drag began).
function remember(boxes) {
  page.history.push({ game: page.current.record.game, frame: page.current.record.frame,
                      before: { status: page.status, boxes: copy(boxes || page.boxes) } });
  if (page.history.length > 200) page.history.shift();
}

function boxesToSave() {
  return page.assist ? page.boxes : page.boxes.filter(function (box) { return box.source !== 'model'; });
}

async function save(status) {
  if (!page.current) return null;
  const record = page.current.record;
  if (status) page.status = status;
  const body = { status: page.status, boxes: boxesToSave() };
  const reply = await tryToast(function () {
    return putJSON('/api/training/frame/' + encodeURIComponent(record.game) + '/' + record.frame, body);
  });
  if (reply) {
    page.counts = reply.counts;
    page.boxes = copy(reply.record.boxes);
    page.current.record = reply.record;
    render();
    showProgress();
  }
  return reply;
}

function dropFromQueue(game, frame) {
  const index = page.queue.findIndex(function (item) { return item.game === game && item.frame === frame; });
  if (index >= 0) {
    page.queue.splice(index, 1);
    if (index < page.pos) page.pos -= 1;
  }
}

async function answer(kind) {
  if (!page.current || page.busy) return;
  const record = page.current.record;
  remember();
  if (kind === 'noball') page.boxes = page.boxes.filter(function (box) { return box.cls !== 'Ball'; });
  const reply = await save(kind === 'skip' ? 'skipped' : 'checked');
  if (!reply) { page.history.pop(); return; }
  dropFromQueue(record.game, record.frame);
  await openQueued();
}

async function undo() {
  const last = page.history.pop();
  if (!last) { toast('Nothing to undo.', ''); return; }
  if (!page.current || page.current.record.game !== last.game || page.current.record.frame !== last.frame) {
    await openFrame(last.game, last.frame);
  }
  page.boxes = copy(last.before.boxes);
  page.status = last.before.status;
  page.selected = -1;
  const saved = page.assist;
  page.assist = true;                 // an undo puts every box back, model boxes included
  const reply = await save(page.status);
  page.assist = saved;
  render();
  if (reply && page.status === 'unchecked') {
    // back in the queue, at the front, so "next" carries on from here
    dropFromQueue(last.game, last.frame);
    page.queue.splice(page.pos, 0, { game: last.game, frame: last.frame, reason: reply.record.queue_reason });
  }
}

/* ---- the queue modes, the assist switch, the keys ---- */

async function setMode(mode) {
  page.mode = mode;
  for (const button of byId('modes').children) button.classList.toggle('on', button.dataset.mode === mode);
  try { localStorage.setItem('hoops.annotate.mode', mode); } catch (problem) { /* a private window */ }
  await loadQueue();
  await openQueued();
}

function typing(event) {
  const target = event.target;
  return target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable);
}

function onKey(event) {
  if (typing(event) || event.ctrlKey || event.metaKey || event.altKey) return;
  const key = event.key;
  if (key === ' ') { event.preventDefault(); tryToast(function () { return answer('right'); }); }
  else if (key === 'n' || key === 'N') tryToast(function () { return answer('noball'); });
  else if (key === 's' || key === 'S') tryToast(function () { return answer('skip'); });
  else if (key === 'z' || key === 'Z') tryToast(undo);
  else if (key === 'b' || key === 'B') startDrawing();
  else if (key === 'Delete' || key === 'Backspace') { event.preventDefault(); deleteSelected(); }
  else if (key === 'Escape') { page.drawing = false; byId('editor').classList.remove('drawing'); byId('btn-draw').classList.remove('on'); byId('draft').hidden = true; page.drag = null; page.selected = -1; render(); }
  else if (key >= '1' && key <= '9' && page.classes[Number(key) - 1]) chooseClass(page.classes[Number(key) - 1]);
}

function wire() {
  const editor = byId('editor');
  editor.addEventListener('pointerdown', onPointerDown);
  editor.addEventListener('pointermove', onPointerMove);
  editor.addEventListener('pointerup', onPointerUp);
  editor.addEventListener('pointercancel', function () { page.drag = null; byId('draft').hidden = true; render(); });
  document.addEventListener('keydown', onKey);
  window.addEventListener('resize', drawInset);
  byId('btn-right').addEventListener('click', function () { tryToast(function () { return answer('right'); }); });
  byId('btn-noball').addEventListener('click', function () { tryToast(function () { return answer('noball'); }); });
  byId('btn-skip').addEventListener('click', function () { tryToast(function () { return answer('skip'); }); });
  byId('btn-undo').addEventListener('click', function () { tryToast(undo); });
  byId('btn-draw').addEventListener('click', startDrawing);
  byId('assist').addEventListener('change', function () { page.assist = byId('assist').checked; page.selected = -1; showAssistWords(); render(); drawInset(); });
  for (const button of byId('modes').children) {
    button.addEventListener('click', function () { tryToast(function () { return setMode(button.dataset.mode); }); });
  }
}

async function start() {
  wire();
  let mode = 'hard';
  try { mode = localStorage.getItem('hoops.annotate.mode') || 'hard'; } catch (problem) { /* a private window */ }
  page.mode = ['hard', 'order', 'unsure'].includes(mode) ? mode : 'hard';
  for (const button of byId('modes').children) button.classList.toggle('on', button.dataset.mode === page.mode);
  await loadQueue();
  const wanted = urlParam('frame');
  if (page.game && wanted !== null && wanted !== '') {
    await openFrame(page.game, Number(wanted));
    history.replaceState(null, '', withParams('annotate.html', { game: page.game }));
  } else {
    await openQueued();
  }
}

tryToast(start);
