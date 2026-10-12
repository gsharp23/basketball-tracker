/* One season, the first screen of Game analysis: a dropdown to switch season, the five tiles, points by game,
   the season shot map, the schedule (every game of the season, with or without a video), the leaders,
   "Add a game" (a game on the schedule, a video linked or not) and the roster.
   Numbers come from GET /api/seasons/<id>/summary; writing goes through the shared routes
   (POST/PUT/DELETE /api/seasons/<id>/schedule...). Processing a video (Run on Colab) is the Games screen's job;
   the "A video's game" panel for a video on no scheduled game is shared with it: web/game_rows.js. */

let season = null;        // the row of /api/seasons (id, name, current, roster...)
let summary = null;       // GET /api/seasons/<id>/summary
let searchText = '';

renderHeader('seasons', { search: function (text) { searchText = text.toLowerCase(); showGames(); } });

/* ---- the title and the tiles ---- */

async function showTitle() {
  let words = seasonName(season.name);
  if (season.id !== 'unassigned') {
    try {
      const brand = await api('/api/branding');
      const team = brand.mascot || season.roster.name;
      if (team) words += ' · ' + team;
    } catch (problem) { /* the plain title is fine */ }
  }
  byId('season-title').textContent = words;
  document.title = seasonName(season.name) + ' · Hoops Tracker';
  byId('make-current').hidden = season.current || season.id === 'unassigned';
  byId('open-roster').hidden = season.id === 'unassigned';
  byId('add-fixture').hidden = season.id === 'unassigned';
}

// The dropdown at the top: every season, the current one marked, then "All seasons" (the library screen).
async function fillSeasonPicker() {
  const pick = byId('season-pick');
  const reply = await api('/api/seasons');
  const options = reply.seasons.map(function (row) {
    return el('option', { value: row.id }, seasonName(row.name) + (row.current ? ' · current' : ''));
  });
  if (reply.unassigned) options.push(el('option', { value: 'unassigned' }, 'Unassigned videos (' + reply.unassigned + ')'));
  options.push(el('option', { value: '__all' }, 'All seasons...'));
  pick.replaceChildren.apply(pick, options);
  pick.value = season.id;
  pick.addEventListener('change', function () {
    if (pick.value === '__all') { location.href = 'seasons.html'; return; }
    rememberSeason(pick.value);
    location.href = withParams('season.html', { season: pick.value });
  });
}

function showTiles() {
  const pct = function (pair) { return pair.tried ? fmtPct(pair.pct) : '—'; };
  byId('tile-record').textContent = summary.record.text;
  byId('tile-points').textContent = summary.points_per_game === null ? '—' : Math.round(summary.points_per_game);
  byId('tile-allowed').textContent = summary.allowed_per_game === null ? '—' : Math.round(summary.allowed_per_game);
  byId('tile-fg').textContent = pct(summary.field_goals);
  byId('tile-threes').textContent = pct(summary.threes);
  byId('tile-fg').title = summary.field_goals.tried ? fmtMadeTried(summary.field_goals) + ' tagged' : 'No shots tagged yet';
  byId('tile-threes').title = summary.threes.tried ? fmtMadeTried(summary.threes) + ' tagged' : 'No threes tagged yet';
  byId('provisional-note').hidden = !summary.provisional;
}

/* ---- points by game: an inline chart, us in red, the opponents in grey ---- */

function pointsChart(games) {
  const SVG = 'http://www.w3.org/2000/svg';
  const played = games.filter(function (game) { return game.us !== null || game.them !== null; });
  if (!played.length) {
    return el('div', { class: 'callout callout-grey' },
      'No points yet. Type a game\'s final score (Edit, in the games table) or tag its shots, and the chart draws itself.');
  }
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 640 220');
  svg.setAttribute('class', 'chart');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Points scored and allowed per game');
  function add(tag, attrs) {
    const node = document.createElementNS(SVG, tag);
    for (const name in attrs) node.setAttribute(name, attrs[name]);
    svg.appendChild(node);
    return node;
  }
  const values = [];
  played.forEach(function (game) { if (game.us !== null) values.push(game.us); if (game.them !== null) values.push(game.them); });
  let low = Math.max(0, Math.floor((Math.min.apply(null, values) - 5) / 5) * 5);
  let high = Math.ceil((Math.max.apply(null, values) + 5) / 5) * 5;
  if (high - low < 20) high = low + 20;
  const y = function (value) { return Math.round(180 - 120 * (value - low) / (high - low)); };
  const x = function (i) { return Math.round(played.length === 1 ? 340 : 100 + 480 * i / (played.length - 1)); };
  [low, (low + high) / 2, high].forEach(function (level) {
    add('line', { x1: 40, y1: y(level), x2: 620, y2: y(level), stroke: '#e3e5e9' });
    const label = add('text', { x: 30, y: y(level) + 4, 'text-anchor': 'end', 'font-size': 11, fill: '#5a5f6a', 'font-family': 'Barlow, sans-serif' });
    label.textContent = Math.round(level);
  });
  function line(key, colour) {
    const points = [];
    played.forEach(function (game, i) { if (game[key] !== null) points.push(x(i) + ',' + y(game[key])); });
    if (points.length > 1) add('polyline', { points: points.join(' '), fill: 'none', stroke: colour, 'stroke-width': 3 });
  }
  line('them', '#8a919c');
  line('us', '#d81e27');
  // A small grey dot per game for the opponent's points, so one game alone still shows both sides.
  played.forEach(function (game, i) {
    if (game.them !== null) add('circle', { cx: x(i), cy: y(game.them), r: 4, fill: '#8a919c' });
  });
  played.forEach(function (game, i) {
    if (game.us === null) return;
    // A filled dot is a typed final score; a hollow one is points from the tagged shots, a stand-in.
    add('circle', { cx: x(i), cy: y(game.us), r: 5, fill: game.from === 'scores' ? '#d81e27' : '#ffffff', stroke: '#d81e27', 'stroke-width': 2.5 });
  });
  const every = Math.ceil(played.length / 8);     // with many games, only every n-th label fits
  played.forEach(function (game, i) {
    if (i % every) return;
    const label = add('text', { x: x(i), y: 205, 'text-anchor': 'middle', 'font-size': 12, fill: '#5a5f6a', 'font-family': 'Barlow, sans-serif' });
    label.textContent = game.label.length > 16 ? game.label.slice(0, 15) + '…' : game.label;
  });
  return svg;
}

function showChart() {
  byId('chart').replaceChildren(pointsChart(summary.points_by_game));
  const tagged = summary.points_by_game.some(function (game) { return game.from === 'tags'; });
  byId('chart-note').hidden = !tagged;
  byId('chart-note').textContent = 'A hollow dot is the points from the tagged shots, not a typed final score: type the score to replace it.';
}

/* ---- the season shot map ---- */

function showShotMap() {
  const shots = summary.shots;
  byId('shots-count').textContent = plural(shots.count, 'shot');
  drawHalfCourt(byId('shot-map'), shots.court, shots.marks, '#d81e27');
  const share = function (value) { return el('b', {}, value === null ? '—' : value + '%'); };
  byId('shares').replaceChildren(
    el('span', {}, ['Paint ', share(shots.shares.paint), ' of points']),
    el('span', {}, ['Threes ', share(shots.shares.threes)]),
    el('span', {}, ['Free throws ', share(shots.shares.free_throws)]));
  byId('shots-note').textContent = shots.marks.length ? undrawnNote(shots.marks) : 'Shots are tagged on each game\'s page ("Tag shots and plays").';
}

/* ---- the schedule: every game of the season, with or without a video ---- */

function matches(row) {
  if (!searchText) return true;
  return [row.opponent, row.stem, row.date, fmtDate(row.date), row.status_words].join(' ').toLowerCase().indexOf(searchText) >= 0;
}

function todayText() { return new Date().toISOString().slice(0, 10); }

function fmtTime(text) {
  const match = /^(\d{2}):(\d{2})$/.exec(text || '');
  if (!match) return '';
  const hours = Number(match[1]);
  return (hours % 12 || 12) + ':' + match[2] + (hours >= 12 ? ' pm' : ' am');
}

// Date · time, with "home" or "away" after it; a date that is only the video file's day is grey.
function whenCell(row) {
  const where = row.home === true ? ' · home' : row.home === false ? ' · away' : '';
  if (!row.date) return el('td', { class: 'muted' }, 'no date yet' + where);
  const time = row.time ? el('span', { class: 'small muted' }, ' ' + fmtTime(row.time)) : null;
  return el('td', { class: row.date_is_guess ? 'muted' : null, title: row.date_is_guess ? 'The day of the video file; set the date with Edit' : null },
            [fmtDate(row.date), time, where]);
}

function whoCell(row) {
  const name = row.opponent
    ? (row.opponent_id ? el('a', { href: withParams('team.html', { team: row.opponent_id }) }, row.opponent) : el('span', {}, row.opponent))
    : el('span', { class: 'muted' }, row.stem ? 'no opponent yet · ' + row.stem : 'no opponent yet');
  const extra = [];
  if (row.kind === 'tournament') extra.push(el('span', { class: 'chip chip-grey chip-sm' }, 'Tournament'));
  if (row.unscheduled) extra.push(el('span', { class: 'small muted' }, ' video on no scheduled game'));
  return el('td', { class: 'strong' }, [name].concat(extra));
}

// What the video column says: the status chip for a linked video, "No video" otherwise.
function videoCell(row) {
  if (!row.stem) return el('td', { class: 'muted' }, 'No video');
  const cell = el('td', { class: 'status-cell' });
  cell.appendChild(el('span', { class: 'chip chip-' + row.chip, title: row.progress_words || row.stem }, row.status_words));
  if (row.processed_on === 'colab' && row.status !== 'finished') cell.appendChild(el('span', { class: 'small muted' }, 'On Colab'));
  return cell;
}

function scheduleActions(row) {
  const parts = [];
  if (row.stem && row.status === 'finished') parts.push(el('a', { href: withParams('game.html', { game: row.stem }) }, 'Open stats'));
  else if (row.stem) parts.push(el('a', { href: 'games.html', class: 'muted' }, 'Process'));
  parts.push(el('a', { href: '#', class: 'small', onclick: function (event) { event.preventDefault(); openFixturePanel(row); } }, 'Edit'));
  return el('div', { class: 'actions' }, parts);
}

function scheduleRow(row) {
  const today = todayText();
  return el('tr', { id: row.stem ? 'row-' + row.stem : null, class: row.date === today ? 'today' : null }, [
    whenCell(row),
    whoCell(row),
    el('td', { class: resultClass(row) }, fmtResult(row)),
    videoCell(row),
    el('td', {}, scheduleActions(row))]);
}

function showGames() {
  const body = byId('game-rows'), note = byId('games-note');
  const rows = summary.schedule.filter(matches);
  body.replaceChildren.apply(body, rows.map(scheduleRow));
  const withVideo = summary.schedule.filter(function (row) { return row.stem; }).length;
  byId('schedule-count').textContent = plural(summary.schedule.length, 'game') + ' · ' + withVideo + ' with video';
  note.hidden = rows.length > 0;
  if (!rows.length) {
    note.textContent = summary.schedule.length ? 'No game matches "' + searchText + '".'
      : 'No games in this season yet. Press "Add a game" above.';
  }
  // A row being processed refreshes itself (game_rows.js); the page reloads when the run ends.
  summary.schedule.forEach(function (row) { if (row.running) watchRun(row); });
}

/* ---- "Add a game" / Edit: one game on the schedule ---- */

let editing = null;       // the schedule row being edited, or null for a new one

function setFixtureHome(value) {
  byId('fixture-home').dataset.value = value === null ? '' : String(value);
  for (const button of byId('fixture-home').querySelectorAll('button')) {
    button.classList.toggle('on', value !== null && String(value) === button.dataset.home);
  }
}

function fixtureHome() {
  const value = byId('fixture-home').dataset.value;
  return value === 'true' ? true : value === 'false' ? false : null;
}

// The video list: every video in the clips folder, the ones on no scheduled game first.
async function fillVideoList(chosen) {
  const all = await api('/api/games');
  const linked = {};
  summary.schedule.forEach(function (row) { if (row.stem && row.fixture_id) linked[row.stem] = row.opponent || row.fixture_id; });
  const games = all.games.slice().sort(function (a, b) { return (linked[a.name] ? 1 : 0) - (linked[b.name] ? 1 : 0) || a.name.localeCompare(b.name); });
  const select = byId('fixture-stem');
  select.replaceChildren(el('option', { value: '' }, 'No video yet'));
  games.forEach(function (row) {
    const words = row.name + ' · ' + fmtLength(row.seconds) + (linked[row.name] && row.name !== chosen ? ' · linked to ' + linked[row.name] : '');
    select.appendChild(el('option', { value: row.name }, words));
  });
  select.value = chosen || '';
  const names = byId('team-names');
  names.replaceChildren.apply(names, Object.keys(all.teams).map(function (id) { return el('option', { value: all.teams[id].name }); }));
}

async function openFixturePanel(row) {
  editing = row || null;
  const panel = byId('fixture-panel');
  panel.hidden = false;
  byId('fixture-title').textContent = row ? (row.fixture_id ? 'Edit this game' : 'Put this video on the schedule') : 'Add a game to the schedule';
  byId('fixture-date').value = row && !row.date_is_guess ? (row.date || '') : '';
  byId('fixture-time').value = row ? (row.time || '') : '';
  byId('fixture-opponent').value = row ? (row.opponent || '') : '';
  byId('fixture-kind').value = row ? (row.kind || '') : '';
  byId('fixture-us').value = row && row.score_us !== null ? row.score_us : '';
  byId('fixture-them').value = row && row.score_them !== null ? row.score_them : '';
  byId('fixture-note').value = row ? (row.note || '') : '';
  setFixtureHome(row ? row.home : null);
  byId('fixture-delete').hidden = !(row && row.fixture_id);
  await tryToast(function () { return fillVideoList(row ? row.stem : ''); });
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  byId(row && row.date ? 'fixture-opponent' : 'fixture-date').focus();
}

function closeFixturePanel() { byId('fixture-panel').hidden = true; editing = null; }

async function saveFixture(event) {
  event.preventDefault();
  const button = byId('fixture-save');
  button.disabled = true;
  const body = {
    date: byId('fixture-date').value, time: byId('fixture-time').value, opponent: byId('fixture-opponent').value.trim(),
    home: fixtureHome(), kind: byId('fixture-kind').value, score_us: byId('fixture-us').value.trim(),
    score_them: byId('fixture-them').value.trim(), note: byId('fixture-note').value.trim(), stem: byId('fixture-stem').value || null,
  };
  const saved = await tryToast(function () {
    if (!body.date) throw new Error('A scheduled game needs a date.');
    if (!body.opponent) throw new Error('Name the opponent.');
    const base = '/api/seasons/' + encodeURIComponent(season.id) + '/schedule';
    return editing && editing.fixture_id ? putJSON(base + '/' + encodeURIComponent(editing.fixture_id), body) : postJSON(base, body);
  });
  button.disabled = false;
  if (!saved) return;
  toast('Saved: ' + fmtDate(saved.date) + ' vs ' + body.opponent + (body.stem ? ' · video ' + body.stem : ''), 'good');
  closeFixturePanel();
  await loadSummary();
}

async function deleteFixture() {
  if (!editing || !editing.fixture_id) return;
  const words = fmtDate(editing.date) + (editing.opponent ? ' vs ' + editing.opponent : '');
  if (!window.confirm('Take ' + words + ' off the schedule?' + (editing.stem ? ' The video stays in the season.' : ''))) return;
  const done = await tryToast(function () {
    return deleteJSON('/api/seasons/' + encodeURIComponent(season.id) + '/schedule/' + encodeURIComponent(editing.fixture_id));
  });
  if (!done) return;
  closeFixturePanel();
  await loadSummary();
}

/* ---- the leaders ---- */

function showLeaders() {
  const list = byId('leaders');
  if (!summary.leaders.length) {
    list.replaceChildren(el('div', { class: 'hint' }, 'Leaders appear once games are processed and their shots are tagged.'));
    return;
  }
  list.replaceChildren.apply(list, summary.leaders.map(function (lead) {
    return el('div', { class: 'lead-row' }, [
      el('span', { class: 'lead-number' }, lead.number),
      el('div', { class: 'lead-text' }, [el('div', { class: 'name' }, lead.name || 'No. ' + lead.number), el('div', { class: 'what' }, lead.label)]),
      el('div', { class: 'lead-value' }, lead.text)]);
  }));
}

/* ---- the roster ---- */

function rosterRow(number, name) {
  return el('tr', {}, [
    el('td', {}, el('input', { class: 'input', type: 'text', value: number, placeholder: 'No.', inputmode: 'numeric', 'aria-label': 'Jersey number' })),
    el('td', {}, el('input', { class: 'input', type: 'text', value: name, placeholder: 'Name', 'aria-label': 'Player name' }))]);
}

function openRosterPanel() {
  const panel = byId('roster-panel');
  panel.hidden = false;
  byId('roster-team-name').value = season.roster.name || '';
  const body = byId('roster-rows');
  const numbers = Object.keys(season.roster.players).sort(function (a, b) { return Number(a) - Number(b); });
  body.replaceChildren.apply(body, numbers.map(function (number) { return rosterRow(number, season.roster.players[number]); }));
  for (let i = 0; i < 3; i++) body.appendChild(rosterRow('', ''));
  byId('season-delete').hidden = season.current;
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function readRoster() {
  const players = {};
  for (const row of byId('roster-rows').querySelectorAll('tr')) {
    const boxes = row.querySelectorAll('input');
    const number = boxes[0].value.trim(), name = boxes[1].value.trim();
    if (!number && !name) continue;
    if (!number) throw new Error('Every name needs a jersey number (the "No." box next to it).');
    if (number in players) throw new Error('Number ' + number + ' is listed twice.');
    players[number] = name;
  }
  return { name: byId('roster-team-name').value.trim(), players: players };
}

async function saveRoster() {
  const button = byId('roster-save');
  button.disabled = true;
  const saved = await tryToast(function () { return putJSON('/api/seasons/' + encodeURIComponent(season.id), { roster: readRoster() }); });
  button.disabled = false;
  if (!saved) return;
  season.roster = saved.roster;
  toast('Roster saved for ' + seasonName(season.name) + '. Names stay on this laptop.', 'good');
  await loadSummary();
}

async function makeCurrent() {
  const done = await tryToast(function () { return putJSON('/api/seasons/' + encodeURIComponent(season.id), { current: true }); });
  if (!done) return;
  season.current = true;
  toast(seasonName(season.name) + ' is now the current season.', 'good');
  byId('make-current').hidden = true;
}

async function deleteSeason() {
  if (!window.confirm('Delete the season ' + seasonName(season.name) + '? Its games stay in the clips folder and become Unassigned; its roster is lost.')) return;
  const done = await tryToast(function () { return deleteJSON('/api/seasons/' + encodeURIComponent(season.id)); });
  if (!done) return;
  rememberSeason(null);
  location.href = 'seasons.html';
}

/* ---- loading ---- */

async function loadSummary() {
  summary = await api('/api/seasons/' + encodeURIComponent(season.id) + '/summary');
  season = Object.assign(season, summary.season);
  GameRows.clipsFolder = summary.clips_folder;
  GameRows.defaultSeason = season.id;
  showTiles();
  showChart();
  showShotMap();
  showGames();
  showLeaders();
}

async function start() {
  season = await currentSeason();
  await showTitle();
  await fillSeasonPicker();
  await loadSummary();
  if (location.hash === '#add') openFixturePanel(null);
}

// The shared rows and panel (game_rows.js): how this page reloads, and what happens after "Save the game".
GameRows.reload = loadSummary;
GameRows.afterSave = function (where) {
  if (where !== season.id) {
    // The game went to another season: follow it there.
    rememberSeason(where);
    location.href = withParams('season.html', { season: where });
    return;
  }
  byId('add-panel').hidden = true;
  loadSummary().catch(function (problem) { toast(problem.message, 'bad'); });
};
wireAddPanel();
byId('add-fixture').addEventListener('click', function () { openFixturePanel(null); });
byId('fixture-close').addEventListener('click', closeFixturePanel);
byId('fixture-form').addEventListener('submit', saveFixture);
byId('fixture-delete').addEventListener('click', deleteFixture);
byId('fixture-home').addEventListener('click', function (event) {
  const button = event.target.closest('button');
  if (button) setFixtureHome(button.dataset.home === 'true');
});
byId('open-roster').addEventListener('click', openRosterPanel);
byId('roster-close').addEventListener('click', function () { byId('roster-panel').hidden = true; });
byId('roster-add').addEventListener('click', function () { byId('roster-rows').appendChild(rosterRow('', '')); });
byId('roster-save').addEventListener('click', saveRoster);
byId('make-current').addEventListener('click', makeCurrent);
byId('season-delete').addEventListener('click', deleteSeason);
start().catch(function (problem) {
  toast(problem.message, 'bad');
  byId('game-rows').replaceChildren(el('tr', {}, el('td', { colspan: 5, class: 'muted' }, problem.message)));
});
