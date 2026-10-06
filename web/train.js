/* The "Train the model" screen: click the ball in picked frames, make the zip for Colab, see the ball models.
   The dashboard keeps nothing about this page between requests: every answer is saved the moment it is given. */

renderHeader('train');

function byId(id) { return document.getElementById(id); }

const ZOOM = 3;             // the enlarged view shows the picture 3 times bigger
const INSET_PX = 240;       // the enlarged view's size on screen (see .inset in train.html); 160 on a phone
const SIZE_STEP = 2;        // pixels per press of Smaller / Larger

// What the page is looking at right now.
const page = {
  game: null,       // {video, name}
  status: null,     // the answer of /api/train/status
  index: 0,         // which of the game's frames is on show
  info: null,       // the answer of /api/train/frame for it
  box: null,        // {cx, cy, size} in pixels of the full picture, or null until the ball is clicked
  aim: null,        // top-left corner {x0, y0} of the area the enlarged view shows, or null
  history: [],      // frames answered on this page, newest last (for Undo)
  pickTimer: null,
  zipTimer: null,
};

/* ---- geometry: the picture is shown "contained" in the pane, so it may have bars at the sides ---- */

function drawnRect() {
  const pane = byId('pane').getBoundingClientRect(), img = byId('picture');
  const natW = img.naturalWidth || 1920, natH = img.naturalHeight || 1080;
  const scale = Math.min(pane.width / natW, pane.height / natH);
  return { scale: scale, left: (pane.width - natW * scale) / 2, top: (pane.height - natH * scale) / 2, natW: natW, natH: natH };
}

// A click in the pane -> the same spot in the full picture (null when the click is on a bar beside the picture).
function paneToFull(event) {
  const pane = byId('pane').getBoundingClientRect(), d = drawnRect();
  const x = (event.clientX - pane.left - d.left) / d.scale, y = (event.clientY - pane.top - d.top) / d.scale;
  if (x < 0 || y < 0 || x > d.natW || y > d.natH) return null;
  return { x: x, y: y };
}

function insetSize() { return byId('inset').getBoundingClientRect().width || INSET_PX; }

// Put the enlarged view's area so that (x, y) is in its middle, kept inside the picture.
function aimAt(x, y) {
  byId('inset').hidden = false;                 // shown first, so its size on this screen can be measured
  const d = drawnRect(), area = insetSize() / ZOOM;
  page.aim = { x0: Math.min(Math.max(x - area / 2, 0), d.natW - area), y0: Math.min(Math.max(y - area / 2, 0), d.natH - area) };
  // The view sits bottom right, or bottom left when it would cover the spot just clicked.
  byId('inset').classList.toggle('on-left', x > d.natW * 0.62 && y > d.natH * 0.5);
}

/* ---- drawing the boxes and the enlarged view (pure CSS: no picture is made on the server) ---- */

function placeBox(node, cx, cy, size, d) {
  node.style.left = (d.left + (cx - size / 2) * d.scale) + 'px';
  node.style.top = (d.top + (cy - size / 2) * d.scale) + 'px';
  node.style.width = node.style.height = (size * d.scale) + 'px';
  node.hidden = false;
}

function draw() {
  const d = drawnRect(), img = byId('picture');
  const ballBox = byId('ball-box'), guessBox = byId('guess-box'), guessWord = byId('guess-word');
  const inset = byId('inset'), insetBox = byId('inset-box');
  if (img.hidden) { ballBox.hidden = guessBox.hidden = guessWord.hidden = inset.hidden = true; return; }

  const guess = page.info && page.info.guess;
  if (guess && !page.box) {
    placeBox(guessBox, guess[0], guess[1], guess[2], d);
    guessWord.style.left = (d.left + (guess[0] + guess[2]) * d.scale + 6) + 'px';
    guessWord.style.top = (d.top + (guess[1] - guess[2]) * d.scale) + 'px';
    guessWord.hidden = false;
  } else {
    guessBox.hidden = guessWord.hidden = true;
  }

  if (page.box) placeBox(ballBox, page.box.cx, page.box.cy, page.box.size, d); else ballBox.hidden = true;

  if (page.aim) {
    inset.hidden = false;
    inset.style.backgroundImage = 'url("' + img.src + '")';
    inset.style.backgroundSize = (d.natW * ZOOM) + 'px ' + (d.natH * ZOOM) + 'px';
    inset.style.backgroundPosition = (-page.aim.x0 * ZOOM) + 'px ' + (-page.aim.y0 * ZOOM) + 'px';
    if (page.box) {
      const size = page.box.size * ZOOM;
      insetBox.style.left = ((page.box.cx - page.aim.x0) * ZOOM - size / 2) + 'px';
      insetBox.style.top = ((page.box.cy - page.aim.y0) * ZOOM - size / 2) + 'px';
      insetBox.style.width = insetBox.style.height = size + 'px';
      insetBox.hidden = false;
    } else {
      insetBox.hidden = true;
    }
  } else {
    inset.hidden = true;
  }
  const size = page.box ? page.box.size : page.info && page.info.usual_size;
  byId('size-words').textContent = size ? 'Box size: ' + size + ' px' : 'Box size: set by your click';
  byId('btn-guess').hidden = !guess;
}

/* ---- the first idea of the ball's size at a clicked spot (the same rule as ball_labels.propose_size) ---- */

function sizeAt(x, y) {
  const info = page.info, d = drawnRect(), sizes = info.sizes;
  // 1. a share of the height of the nearest whole player box (when the game has been processed)
  const whole = (info.players || []).filter(function (b) { return b[1] > 2 && b[3] < d.natH - 2; });
  if (whole.length) {
    function distance(b) {
      return Math.pow(Math.max(b[0] - x, 0, x - b[2]), 2) + Math.pow(Math.max(b[1] - y, 0, y - b[3]), 2);
    }
    let nearest = whole[0];
    for (const b of whole) if (distance(b) < distance(nearest)) nearest = b;
    return Math.max(sizes.min, Math.round((nearest[3] - nearest[1]) * sizes.share_of_player));
  }
  // 2. the size the coach gave the ball most often in this game; 3. a share of the picture's width
  if (info.usual_size) return info.usual_size;
  return Math.max(sizes.min, Math.round(d.natW * sizes.share_of_width));
}

/* ---- showing a frame ---- */

function showWords(text) {
  const words = byId('pane-words');
  words.hidden = !text;
  if (typeof text === 'string') words.textContent = text; else if (text) words.replaceChildren(text);
}

function loadPicture(url) {
  return new Promise(function (done, fail) {
    const img = byId('picture');
    img.onload = function () { done(); };
    img.onerror = function () { fail(new Error('The picture could not be read from the data folder. Reload the page.')); };
    img.src = url;
  });
}

async function showFrame(index) {
  const frames = page.status.frames;
  if (!frames.length) { showNoFrames(); return; }
  index = Math.min(Math.max(index, 0), frames.length - 1);
  page.index = index;
  page.box = page.aim = null;
  const info = await api('/api/train/frame?video=' + encodeURIComponent(page.game.video) + '&index=' + index);
  page.info = info;
  const img = byId('picture'), pane = byId('pane');
  pane.dataset.ready = '';
  await loadPicture(info.url);
  img.hidden = false;
  showWords('');
  byId('answer-row').hidden = byId('size-row').hidden = false;
  enableAnswers();

  const chip = byId('answered-chip');
  if (info.answer) {
    const said = info.answer.answer;
    chip.textContent = 'Answered: ' + (said === 'ball' ? 'ball marked' : said === 'no ball' ? 'no ball visible' : "can't tell") + ' (answer again to change it)';
    chip.hidden = false;
    if (info.answer.box) {
      page.box = { cx: info.answer.box.cx, cy: info.answer.box.cy, size: info.answer.box.w };
      aimAt(page.box.cx, page.box.cy);
    }
  } else {
    chip.hidden = true;
  }
  byId('btn-undo').hidden = !page.history.length;
  draw();
  pane.dataset.frame = info.frame;
  pane.dataset.ready = '1';
  // The next picture is fetched now, so that "Save and next" shows it at once.
  if (index + 1 < frames.length) {
    const next = new Image();
    next.src = info.url.replace(/[^/]+$/, '') + nextFileName(frames[index + 1].frame);
  }
}

// The picture files are named <game>_<frame number, 6 digits>.jpg (see ball_labels.py).
function nextFileName(frame) {
  return page.game.name + '_' + String(frame).padStart(6, '0') + '.jpg';
}

function showNoFrames() {
  byId('picture').hidden = true;
  byId('answer-row').hidden = byId('size-row').hidden = true;
  byId('answered-chip').hidden = true;
  page.info = null; page.box = page.aim = null;
  draw();
  showWords(el('div', { class: 'stack', style: 'gap: 12px; align-items: center' }, [
    el('div', { style: 'font-size: 20px' }, 'No pictures have been picked from this game yet.'),
    el('div', { class: 'small', style: 'color: #d6d1d3' }, 'Press "Pick frames" below. About 150 spread over a whole game take a minute or two.'),
  ]));
}

function showAllDone() {
  byId('picture').hidden = true;
  byId('answer-row').hidden = byId('size-row').hidden = true;
  byId('answered-chip').hidden = true;
  page.info = null; page.box = page.aim = null;
  draw();
  const n = page.status.counts;
  showWords(el('div', { class: 'stack', style: 'gap: 12px; align-items: center' }, [
    el('div', { style: 'font-size: 20px' }, 'All ' + n.frames + ' pictures of this game are done.'),
    el('div', { class: 'small', style: 'color: #d6d1d3' }, n.ball + ' with the ball, ' + n['no ball'] + ' with no ball, ' + n.skipped + " you couldn't tell. Pick more frames below, or use the Back button to look again."),
    el('button', { class: 'btn btn-light', type: 'button', onclick: function () { showFrame(page.status.frames.length - 1); } }, 'Look at the last picture'),
  ]));
  byId('answer-row').hidden = false;
  byId('btn-save').disabled = byId('btn-noball').disabled = byId('btn-skip').disabled = true;
}

function enableAnswers() {
  byId('btn-save').disabled = byId('btn-noball').disabled = byId('btn-skip').disabled = false;
}

/* ---- answering ---- */

async function giveAnswer(said) {
  if (!page.info) return;
  const img = byId('picture');
  const body = { video: page.game.video, frame: page.info.frame, answer: said, width: img.naturalWidth, height: img.naturalHeight };
  if (said === 'ball') {
    if (!page.box) { toast('Click the ball in the picture first (then click its middle in the enlarged view).', ''); return; }
    body.box = { cx: page.box.cx, cy: page.box.cy, size: page.box.size };
  }
  const reply = await tryToast(function () { return postJSON('/api/train/answer', body); });
  if (!reply) return;
  page.history.push(page.info.frame);
  page.status.frames[page.index].answer = said;
  countAnswer(reply.counts);
  // next: the following frame that has no answer yet, else the next one, else "all done"
  const frames = page.status.frames;
  let next = frames.findIndex(function (item, i) { return i > page.index && !item.answer; });
  if (next < 0) next = frames.findIndex(function (item) { return !item.answer; });
  if (next < 0) {
    refreshStatus();
    showAllDone();
  } else {
    await showFrame(next);
  }
}

async function undoLast() {
  const frame = page.history.pop();
  if (frame === undefined) return;
  const reply = await tryToast(function () { return postJSON('/api/train/clear', { video: page.game.video, frame: frame }); });
  if (!reply) return;
  const index = page.status.frames.findIndex(function (item) { return item.frame === frame; });
  if (index >= 0) page.status.frames[index].answer = null;
  countAnswer(reply.counts);
  enableAnswers();
  await showFrame(index >= 0 ? index : page.index);
  toast('That answer was taken back: answer this picture again.', '');
}

function nudge(step) {
  if (!page.box) return;
  page.box.size = Math.max(page.info.sizes.min, page.box.size + step);
  draw();
}

function wireClicks() {
  byId('pane').addEventListener('click', function (event) {
    if (!page.info || byId('picture').hidden) return;
    if (event.target.closest('#inset')) return;         // handled below
    const spot = paneToFull(event);
    if (!spot) return;
    // First click: the box is placed here and the enlarged view opens around it. A later click moves it.
    const size = page.box ? page.box.size : sizeAt(spot.x, spot.y);
    page.box = { cx: spot.x, cy: spot.y, size: size };
    aimAt(spot.x, spot.y);
    byId('inset-words').textContent = 'Now click the middle of the ball here';
    draw();
  });
  byId('inset').addEventListener('click', function (event) {
    if (!page.aim || !page.box) return;
    const rect = byId('inset').getBoundingClientRect();
    // Second click, in the enlarged view: this fixes the centre exactly.
    page.box.cx = page.aim.x0 + (event.clientX - rect.left) / ZOOM;
    page.box.cy = page.aim.y0 + (event.clientY - rect.top) / ZOOM;
    byId('inset-words').textContent = 'Centre set. Press Save and next (S)';
    draw();
    event.stopPropagation();
  });
  byId('btn-save').addEventListener('click', function () { giveAnswer('ball'); });
  byId('btn-noball').addEventListener('click', function () { giveAnswer('no ball'); });
  byId('btn-skip').addEventListener('click', function () { giveAnswer('skipped'); });
  byId('btn-back').addEventListener('click', function () { showFrame(page.index - (byId('picture').hidden ? 0 : 1)); });
  byId('btn-undo').addEventListener('click', undoLast);
  byId('btn-smaller').addEventListener('click', function () { nudge(-SIZE_STEP); });
  byId('btn-larger').addEventListener('click', function () { nudge(SIZE_STEP); });
  byId('btn-guess').addEventListener('click', function () {
    const g = page.info && page.info.guess;
    if (!g) return;
    page.box = { cx: g[0], cy: g[1], size: g[2] };
    aimAt(g[0], g[1]);
    draw();
  });
  window.addEventListener('resize', draw);

  document.addEventListener('keydown', function (event) {
    if (event.target.matches('input, select, textarea') || event.ctrlKey || event.metaKey || event.altKey) return;
    const key = event.key.toLowerCase();
    if (key === 'enter' && event.target.matches('button, a')) return;   // Enter on a button presses that button
    if (key === 's' || key === 'enter') giveAnswer('ball');
    else if (key === 'n') giveAnswer('no ball');
    else if (key === 'c') giveAnswer('skipped');
    else if (key === 'b' || key === 'backspace' || key === 'arrowleft') byId('btn-back').click();
    else if (key === 'u') undoLast();
    else if (key === '+' || key === '=' || key === 'arrowup') nudge(SIZE_STEP);
    else if (key === '-' || key === 'arrowdown') nudge(-SIZE_STEP);
    else return;
    event.preventDefault();
  });
}

/* ---- the header count, the game card, the steps card ---- */

// New counts for this game (from an answer's reply): the header, the game card and the steps card follow.
function countAnswer(counts) {
  const s = page.status;
  s.counts = counts;
  const mine = s.games.find(function (game) { return game.name === page.game.name; });
  if (mine) mine.counts = counts; else s.games.push({ name: page.game.name, counts: counts });
  s.answered = s.games.reduce(function (sum, game) { return sum + game.counts.ball + game.counts['no ball']; }, 0);
  s.games_answered = s.games.filter(function (game) { return game.counts.ball + game.counts['no ball'] > 0; }).length;
  showProgress();
  showSteps();
}

function showProgress() {
  const n = page.status.counts, done = n.ball + n['no ball'] + n.skipped;
  byId('done-words').textContent = n.frames ? done + ' of ' + n.frames + ' done' : 'No pictures yet';
  byId('game-words').textContent = 'game: ' + page.game.name;
  byId('done-bar').style.width = (n.frames ? Math.round(100 * done / n.frames) : 0) + '%';
  const chip = byId('game-chip');
  chip.textContent = n.frames ? n.frames + ' frames picked · ' + n.ball + ' with the ball · ' + n['no ball'] + ' no ball · ' + n.skipped + " can't tell" : 'No frames yet';
  byId('pick-count').value = n.frames ? Math.max(n.frames + 50, page.status.default_frames) : page.status.default_frames;
  byId('btn-pick').textContent = n.frames ? 'Pick more' : 'Pick frames';
}

function showSteps() {
  const s = page.status;
  byId('step1-note').textContent = s.answered + ' done across ' + s.games_answered + ' game' + (s.games_answered === 1 ? '' : 's') +
    (s.answered < s.min_to_export ? ' (at least ' + s.min_to_export + ' before a zip; a few hundred for a useful model)' : '');
  const labelled = s.answered >= s.min_to_export;
  byId('step1').className = 'step-number ' + (labelled ? 'done' : 'now');
  byId('step2').className = 'step-number ' + (s.latest_zip ? 'done' : labelled ? 'now' : '');
  byId('step3').className = 'step-number ' + (s.latest_zip ? 'now' : '');
  byId('step2-note').textContent = s.latest_zip ? 'Last zip: ' + s.latest_zip.name + ' (' + Math.round(s.latest_zip.megabytes) + ' MB, ' + s.latest_zip.when + ')'
                                                : 'Packs the labelled pictures into one file';
  byId('keep-test').checked = s.test_game === page.game.name;
  byId('split-words').textContent = s.split;
}

async function refreshStatus() {
  page.status = await api('/api/train/status?video=' + encodeURIComponent(page.game.video));
  showProgress();
  showSteps();
  if (page.status.picking.running && page.status.picking.game === page.game.name) watchPicking();
  if (page.status.exporting.running) watchZip();
  return page.status;
}

/* ---- picking frames (a job inside the dashboard; the page asks every second) ---- */

async function startPicking() {
  const count = parseInt(byId('pick-count').value, 10);
  const reply = await tryToast(function () { return postJSON('/api/train/pick', { video: page.game.video, count: count }); });
  if (reply) watchPicking();
}

function watchPicking() {
  clearTimeout(page.pickTimer);
  byId('pick-progress').hidden = false;
  byId('btn-pick').disabled = true;
  async function ask() {
    let p;
    try { p = await api('/api/train/pick'); } catch (problem) { toast(problem.message, 'bad'); return; }
    const total = p.total || 0;
    byId('pick-words').textContent = p.running ? 'Picking frame ' + p.done + ' of ' + total + '...' : (p.error ? 'Stopped' : 'Done: ' + p.done + ' new frames');
    byId('pick-left').textContent = p.running ? (p.seconds_left == null ? 'working out how long' : p.seconds_left < 60 ? 'under a minute left' : 'about ' + Math.round(p.seconds_left / 60) + ' min left') : '';
    byId('pick-bar').style.width = (total ? Math.round(100 * p.done / total) : 0) + '%';
    if (p.running) { page.pickTimer = setTimeout(ask, 1000); return; }
    byId('btn-pick').disabled = false;
    if (p.error) toast(p.error, 'bad');
    else {
      toast(p.done + ' frames picked' + (p.unreadable ? ' (' + p.unreadable + ' could not be read from the video)' : '') + '. Click the ball in each.', 'good');
      setTimeout(function () { byId('pick-progress').hidden = true; }, 4000);
    }
    const hadFrames = page.status.frames.length;
    await refreshStatus();
    if (!hadFrames || byId('picture').hidden) { showFrame(page.status.first_to_do == null ? 0 : page.status.first_to_do); }
  }
  ask();
}

/* ---- keep this game for testing ---- */

async function keepForTesting(keep) {
  const reply = await tryToast(function () { return putJSON('/api/train/test_game', { video: page.game.video, keep: keep }); });
  if (!reply) { byId('keep-test').checked = !keep; return; }
  page.status.test_game = reply.test_game;
  byId('split-words').textContent = reply.split;
  toast(!keep ? 'This game is no longer kept for testing.'
    : reply.by_game ? 'This game is kept for testing: none of its pictures go into training.'
    : 'Noted. It is kept apart for testing once a second game has labelled pictures; until then the pictures are split by time.', 'good');
}

/* ---- the zip ---- */

async function startZip() {
  const reply = await tryToast(function () { return postJSON('/api/train/export', {}); });
  if (reply) watchZip();
}

function watchZip() {
  clearTimeout(page.zipTimer);
  byId('zip-progress').hidden = false;
  byId('btn-zip').disabled = true;
  async function ask() {
    let e;
    try { e = await api('/api/train/export'); } catch (problem) { toast(problem.message, 'bad'); return; }
    byId('zip-words').textContent = e.checking ? 'Checking the zip reads back correctly...' : 'Packing ' + e.done + ' of ' + (e.total || '?') + ' pictures...';
    byId('zip-bar').style.width = (e.total ? Math.round(100 * e.done / e.total) : 0) + '%';
    if (e.running) { page.zipTimer = setTimeout(ask, 1000); return; }
    byId('btn-zip').disabled = false;
    byId('zip-progress').hidden = true;
    if (e.error) { toast(e.error, 'bad'); return; }
    if (e.result) showZipResult(e.result);
    refreshStatus();
  }
  ask();
}

function showZipResult(r) {
  const parts = ['train', 'val', 'test'].map(function (name) {
    return name + ' ' + r.counts[name].frames + ' (' + r.counts[name].ball + ' with the ball)';
  }).join(' · ');
  byId('zip-box').replaceChildren(
    el('div', { style: 'font-weight: 700; margin-bottom: 4px' }, 'The zip is ready: ' + r.name + ' (' + Math.round(r.megabytes) + ' MB)'),
    el('div', {}, ['In Windows: ', el('b', {}, r.path)]),
    r.notebook ? el('div', {}, 'The Colab notebook is next to it in the same folder.') : null,
    el('div', { class: 'muted', style: 'margin-top: 6px' }, parts),
    el('div', { class: 'muted', style: 'margin-top: 6px' }, r.explanation),
    el('div', { class: 'warn', style: 'margin-top: 8px' }, 'Uploading this zip to Google Drive is your decision: the pictures in it show the players, and they leave this laptop when you upload it. The tool uploads nothing by itself.')
  );
  byId('zip-box').hidden = false;
}

/* ---- the ball models ---- */

async function showModels() {
  const list = byId('models-list');
  let reply;
  try { reply = await api('/api/train/models'); } catch (problem) { list.replaceChildren(el('span', { class: 'hint' }, problem.message)); return; }
  if (!reply.models.length) {
    list.replaceChildren(el('span', { class: 'hint' }, 'No ball model yet. The first one comes back from Colab (step 4).'));
    return;
  }
  list.replaceChildren.apply(list, reply.models.map(function (m) {
    const scores = m.scores || {};
    const recall = scores.recall == null ? null : Math.round(scores.recall * 100);
    const right = m.in_use ? el('span', { class: 'chip chip-dark' }, 'In use')
                           : el('button', { class: 'btn btn-outline', type: 'button', onclick: function () { useModel(m.name); } }, 'Use this one');
    const learned = (m.labels ? 'Learned from ' + m.labels + ' pictures · ' : '') + (scores.trained || m.date) + ' · ' + Math.round(m.megabytes) + ' MB';
    return el('div', { class: 'model' + (m.in_use ? ' in-use' : '') }, [
      el('div', { class: 'model-head' }, [el('div', { class: 'model-name', title: m.name }, m.label), right]),
      el('div', { class: 'small muted' }, m.note || learned),
      m.note ? el('div', { class: 'small muted' }, m.name + ' · ' + m.date + ' · ' + Math.round(m.megabytes) + ' MB') : null,
      recall == null ? el('div', { class: 'small muted' }, m.trained ? 'No scores file next to it (ball_model_v<n>_scores.txt).' : 'Not scored on your games.')
                     : el('div', { class: 'progress ' + (m.in_use ? 'progress-green' : 'progress-grey') }, el('span', { style: 'width:' + recall + '%' })),
      recall == null ? null : el('div', { class: 'score' }, 'Found the ball in ' + recall + '% of test pictures' +
        (scores.precision == null ? '' : ' · ' + Math.round(scores.precision * 100) + '% of its boxes were the ball')),
    ]);
  }));
}

async function useModel(name) {
  const reply = await tryToast(function () { return putJSON('/api/train/models/in_use', { name: name }); });
  if (reply) { toast(name + ' is marked as the one in use.', 'good'); showModels(); }
}

/* ---- start ---- */

async function start() {
  wireClicks();
  byId('btn-pick').addEventListener('click', startPicking);
  byId('btn-zip').addEventListener('click', startZip);
  byId('keep-test').addEventListener('change', function (event) { keepForTesting(event.target.checked); });
  showModels();
  page.game = await tryToast(currentGame);
  if (!page.game) {
    byId('done-words').textContent = 'No game chosen';
    showWords(el('div', { class: 'stack', style: 'gap: 12px; align-items: center' }, [
      el('div', { style: 'font-size: 20px' }, 'Choose a game first.'),
      el('a', { class: 'btn btn-light', href: 'games.html' }, 'Go to Games'),
    ]));
    byId('game-card').hidden = true;
    return;
  }
  const status = await tryToast(refreshStatus);
  if (!status) return;
  if (!status.frames.length) showNoFrames();
  else if (status.first_to_do == null) showAllDone();
  else await tryToast(function () { return showFrame(status.first_to_do); });
}

start();
