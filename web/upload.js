/* 1 · Upload: pictures from the chosen games. The games table, the picking job's progress (GET /api/jobs/pick_frames),
   the pictures so far with their condition tags, and "Keep this game for testing". */

renderSidebar('upload');

let info = null;              // GET /api/training/games
let stopPolling = null;       // the job poll, while one runs
const shown = {};             // game -> true when all of its pictures are shown (not just the first 24)
const FIRST_PICTURES = 24;

function num(n) { return Number(n || 0).toLocaleString('en-US'); }
function gameWords(words) { return (words || '').replace(/\d{4}-\d{2}-\d{2}/, fmtDate); }   // "vs Northside · 2026-10-03" -> "... · 3 Oct 2026"

/* ---- the games table ---- */

function conditionChips(conditions, pictures) {
  if (!pictures) return [el('span', { class: 'muted small' }, '—')];
  const chips = [];
  if (conditions.dim) chips.push(el('span', { class: 'chip chip-red' }, 'Dim ' + num(conditions.dim)));
  if (conditions.blur) chips.push(el('span', { class: 'chip chip-yellow' }, 'Blurred ' + num(conditions.blur)));
  if (conditions.wide) chips.push(el('span', { class: 'chip chip-blue' }, 'Zoomed out ' + num(conditions.wide)));
  chips.unshift(el('span', { class: 'chip chip-green' }, 'Good light ' + num(conditions.good)));
  return chips;
}

function showGames() {
  const body = byId('games-body');
  if (!info.games.length) {
    body.replaceChildren(el('tr', {}, el('td', { colspan: 7, class: 'muted' }, 'No videos in the clips folder yet. Copy a game video there and press Refresh.')));
    return;
  }
  body.replaceChildren(...info.games.map(function (game) {
    const pick = el('input', { type: 'checkbox', 'data-game': game.game, 'aria-label': 'Take pictures from ' + game.game });
    const keep = el('input', { type: 'radio', name: 'test-game', 'aria-label': 'Keep ' + game.game + ' for testing',
                               checked: game.test_game, disabled: !game.counts.pictures });
    keep.addEventListener('change', function () { tryToast(function () { return setTestGame(game.game); }); });
    const counts = game.counts;
    return el('tr', {}, [
      el('td', {}, pick),
      el('td', {}, game.words.indexOf('vs ') === 0
        ? [el('div', { class: 'game-name' }, gameWords(game.words)), el('div', { class: 'game-file' }, game.game)]
        : [el('div', { class: 'game-name' }, game.game), game.words !== game.game ? el('div', { class: 'game-file' }, fmtDate(game.words)) : null]),
      el('td', {}, fmtLength(game.seconds)),
      el('td', {}, el('span', { class: 'chip ' + (game.processed ? 'chip-green' : 'chip-grey') }, game.processed ? 'Yes' : 'Not yet')),
      el('td', {}, counts.pictures ? num(counts.pictures) + ' · ' + num(counts.checked) + ' checked' : el('span', { class: 'muted' }, 'none')),
      el('td', {}, el('div', { class: 'row', style: 'gap: 6px' }, conditionChips(game.conditions, counts.pictures))),
      el('td', {}, keep),
    ]);
  }));
  byId('clips-folder').textContent = info.clips_folder;
  byId('detector-words').textContent = 'The detector in use is ' + info.detector + ' (Settings).';
  byId('per-game').value = byId('per-game').value || info.default_per_game;
  byId('per-game').max = info.max_per_game;
}

async function setTestGame(game) {
  const reply = await putJSON('/api/training/test-game', { game: game });
  toast(reply.test_game ? reply.test_game + ' is kept for testing: none of its pictures are trained on.' : 'No game is kept for testing.', 'good');
  await load();
}

/* ---- the pictures so far, game by game ---- */

async function showPictures() {
  const holder = byId('pictures');
  const withPictures = info.games.filter(function (game) { return game.counts.pictures; });
  const total = withPictures.reduce(function (sum, game) { return sum + game.counts.pictures; }, 0);
  byId('pictures-note').textContent = total ? num(total) + ' pictures from ' + plural(withPictures.length, 'game') : '';
  if (!withPictures.length) {
    holder.replaceChildren(el('div', { class: 'callout callout-grey' }, 'No pictures yet. Tick a game above and press "Get pictures".'));
    return;
  }
  const blocks = [];
  for (const game of withPictures) {
    const pictures = await api('/api/training/pictures?game=' + encodeURIComponent(game.game));
    blocks.push(gameBlock(game, pictures));
  }
  holder.replaceChildren(...blocks);
}

function gameBlock(game, pictures) {
  const counts = game.counts;
  const missing = counts.pictures - game.assisted;
  const head = el('div', { class: 'row' }, [
    el('span', { class: 'game-title' }, gameWords(game.words)),
    game.test_game ? el('span', { class: 'chip chip-dark' }, 'Kept for testing') : null,
    el('span', { class: 'muted small' }, num(counts.pictures) + ' pictures · ' + num(counts.checked) + ' checked · ' + num(counts.skipped) + ' skipped'),
    el('span', { class: 'push row', style: 'gap: 8px' }, [
      missing > 0 ? el('button', { class: 'btn btn-outline btn-sm', type: 'button', onclick: function () { tryToast(function () { return runAssist(game.game); }); } },
                       'Run label assist on ' + plural(missing, 'picture')) : null,
      counts.unchecked && !jobBusy() ? el('a', { class: 'btn btn-yellow btn-sm', href: withParams('annotate.html', { game: game.game }) }, 'Check these pictures →') : null,
      counts.unchecked && jobBusy() ? el('span', { class: 'muted small' }, 'Check them once the pictures job above is done') : null,
    ]),
  ]);
  const all = pictures.pictures;
  const list = shown[game.game] ? all : all.slice(0, FIRST_PICTURES);
  const grid = el('div', { class: 'picture-grid' }, list.map(function (picture) {
    const tag = picture.status === 'checked' ? ['done', 'Checked'] : picture.status === 'skipped' ? ['', "Can't tell"]
      : picture.conditions.light === 'dim' ? ['dim', 'Dim'] : picture.conditions.blur ? ['', 'Blurred']
      : picture.conditions.zoom === 'wide' ? ['', 'Zoomed out'] : ['', 'Good light'];
    const thumb = pictures.thumbs[picture.frame];
    return el('a', { class: 'picture', href: withParams('annotate.html', { game: game.game, frame: picture.frame }), title: tag[1] },
      [thumb ? el('img', { src: thumb, alt: '', loading: 'lazy' }) : null,
       el('span', { class: 'time' }, fmtClock(picture.time || 0)),
       el('span', { class: 'tag ' + tag[0] }, tag[1])]);
  }));
  const more = all.length > FIRST_PICTURES ? el('button', { class: 'btn btn-light btn-sm', type: 'button', onclick: function () {
    shown[game.game] = !shown[game.game]; tryToast(showPictures);
  } }, shown[game.game] ? 'Show fewer' : 'Show all ' + num(all.length)) : null;
  return el('div', { class: 'game-block' }, [head, el('div', { class: 'row', style: 'gap: 6px' }, conditionChips(game.conditions, counts.pictures)), grid, more]);
}

/* ---- the picking job ---- */

function chosenGames() {
  return [...document.querySelectorAll('#games-body input[type="checkbox"]:checked')].map(function (box) { return box.dataset.game; });
}

async function getPictures() {
  const games = chosenGames();
  if (!games.length) { toast('Tick at least one game first.', 'bad'); return; }
  const perGame = Number(byId('per-game').value);
  const reply = await postJSON('/api/training/upload', { games: games, per_game: perGame, assist: byId('assist').checked });
  watchJob(reply.job);
}

async function runAssist(game) {
  const reply = await postJSON('/api/training/assist', { game: game });
  watchJob(reply.job);
}

function stepWords(progress) {
  if (!progress || !progress.step_number) return 'Starting...';
  const what = progress.step === 'assist' ? 'label assist' : 'taking pictures';
  return 'Step ' + progress.step_number + ' of ' + progress.step_count + ': ' + what
    + (progress.how_far !== null && progress.how_far !== undefined ? ' · ' + Math.round(progress.how_far * 100) + '%' : '');
}

function jobBusy() { return !!(info && info.job && (info.job.running || info.job.starting)); }

// The job's NOTE lines in plain words ("no new pictures: test already has 10..."), without the "NOTE:" prefix.
function jobNotes(job) {
  return (job.lines || []).filter(function (line) { return /^\s*NOTE:/.test(line); })
    .map(function (line) { return line.replace(/^\s*NOTE:\s*/, ''); });
}

function showJob(job) {
  const card = byId('job-card');
  card.hidden = false;
  const progress = job.progress || {};
  const notes = jobNotes(job);
  byId('job-step').textContent = job.running || job.starting ? stepWords(progress)
    : progress.ended === 'finished' ? (notes.length ? 'Done · ' + notes[0] : 'Done') : progress.ended === 'stopped' ? 'Stopped' : job.exit_code ? 'It did not finish' : 'Done';
  byId('job-left').textContent = job.running ? (progress.left || '') : '';
  byId('job-bar').style.width = (job.running || job.starting ? Math.round((progress.how_far || 0) * 100) : 100) + '%';
  byId('job-detail').textContent = (progress.detail || '') + (job.flagged && job.flagged.length ? ' · ' + job.flagged.join(' ') : '')
    + (notes.length > 1 ? ' · ' + notes.slice(1).join(' ') : '');
  byId('job-log').textContent = (job.lines || []).join('\n');
  byId('job-stop').hidden = !job.running;
  byId('get-pictures').disabled = !!(job.running || job.starting);
  if (!job.running && !job.starting && job.exit_code) {
    const last = (job.lines || []).filter(function (line) { return !line.startsWith('  '); }).pop();
    byId('job-detail').textContent = last || 'Open Details to see what went wrong.';
  }
}

function watchJob(job) {
  if (job) showJob(job);
  if (stopPolling) stopPolling();
  stopPolling = pollJob('pick_frames', function (latest) {
    if (latest.error) { toast(latest.error, 'bad'); return; }
    showJob(latest);
    if (!latest.running && !latest.starting) {
      stopPolling = null;
      const notes = jobNotes(latest);
      if (latest.exit_code === 0 && notes.length) toast(notes.join(' '), '');
      else if (latest.exit_code === 0) toast('The pictures are in. Check them on the Annotate screen.', 'good');
      tryToast(load);
    }
  }, 1500);
}

async function stopJob() {
  await postJSON('/api/jobs/pick_frames/stop');
  toast('Stopping. The pictures and boxes saved so far are kept.', '');
}

/* ---- the page ---- */

async function load() {
  info = await api('/api/training/games');
  showGames();
  await showPictures();
  if (info.job.running || info.job.starting) watchJob(info.job);
  else if (info.job.lines && info.job.lines.length && !byId('job-card').hidden) showJob(info.job);
}

byId('get-pictures').addEventListener('click', function () { tryToast(getPictures); });
byId('job-stop').addEventListener('click', function () { tryToast(stopJob); });
byId('refresh').addEventListener('click', function () { tryToast(load); });
tryToast(load);
