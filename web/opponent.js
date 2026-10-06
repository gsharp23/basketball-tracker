/* The Opponent report: their defense, their offense, their scoring heat map, their players,
   and the panel where the coach labels possessions by hand (the source of the percentages). */

renderHeader('games');

function byId(id) { return document.getElementById(id); }

let game = null;        // {video, name, game} from currentGame()
let report = null;      // the latest answer of GET /api/opponent/report
let labels = null;      // {offense: [[key, words]], defense: [[key, words]], press: key}
let editing = null;     // the possession being changed, or null for a new one

function q() { return '?video=' + encodeURIComponent(game.video); }

/* ---- start ---- */

async function start() {
  game = await tryToast(currentGame);
  if (!game) {
    showNotReady('Choose a game first, on the Games screen.', 'games.html');
    return;
  }
  byId('back-link').href = withGame('game.html', game.name);
  byId('back-link').textContent = '← ' + game.name;        // the opponent's name replaces it once the report is read
  byId('our-link').href = withGame('game.html', game.name);
  byId('their-link').href = withGame('opponent.html', game.name);
  await loadReport();
}

function showNotReady(text, link) {
  byId('not-ready-text').textContent = text;
  byId('not-ready-link').href = withGame(link, game && game.name);
  byId('not-ready').hidden = false;
  byId('report').hidden = true;
  byId('labelling').hidden = true;
  byId('subtitle').textContent = '';
}

async function loadReport() {
  try {
    report = await api('/api/opponent/report' + q());
  } catch (problem) {
    showNotReady(problem.message, 'games.html');
    return;
  }
  labels = report.labels;
  byId('not-ready').hidden = true;
  showTitle();
  if (!report.teams.our_team) {
    showTeamPicker();
    return;
  }
  byId('pick-team').hidden = true;
  byId('report').hidden = false;
  byId('labelling').hidden = false;
  showTiles();
  showBars();
  showHeatMap(null);
  showPlayers();
  await loadPossessions();
  if (!byId('video').src) loadVideo();
  // A link such as opponent.html#labelling: the section was hidden while loading, so scroll to it now.
  const wanted = location.hash && document.querySelector(location.hash);
  if (wanted) wanted.scrollIntoView();
}

/* ---- the title row ---- */

function showTitle() {
  const name = report.info.opponent || 'Opponent';
  byId('page-title').textContent = name + ' report';
  byId('back-link').textContent = '← ' + gameTitle(report.info, game.name);
  document.title = name + ' report · Hoops Tracker';
  const bits = [];
  if (report.info.date) bits.push(report.info.date_is_guess ? 'Added ' + report.info.date : report.info.date);
  bits.push(report.shots_tagged + ' of their shots tagged so far');
  if (game.game && game.game.stand_in_court) bits.push('court marked with stand-in clicks: positions are provisional');
  const line = byId('subtitle');
  line.replaceChildren(bits.join(' · '));
  if (report.teams.our_team) {
    line.append(' · We are the ' + report.teams.our_team + ' team' + (report.teams.guessed ? ' (a guess: check it)' : '') + ' · ',
      el('a', { href: '#', onclick: function (event) { event.preventDefault(); showTeamPicker(); } }, 'Change'));
  }
}

/* ---- which team is ours ---- */

function showTeamPicker() {
  const holder = byId('pick-team-buttons');
  holder.replaceChildren();
  report.teams.labels.forEach(function (label, index) {
    const swatch = el('span', { class: 'swatch', style: 'background:' + report.teams.colours[index] });
    const button = el('button', { type: 'button', class: 'btn btn-outline btn-big',
      onclick: function () { chooseTeam(label, button); } }, [swatch, 'We are the ' + label + ' team']);
    holder.appendChild(button);
  });
  byId('pick-team').hidden = false;
  byId('report').hidden = true;
  byId('labelling').hidden = true;
}

async function chooseTeam(label, button) {
  button.classList.add('is-waiting');
  const done = await tryToast(function () { return putJSON('/api/opponent/our_team' + q(), { our_team: label }); });
  button.classList.remove('is-waiting');
  if (done) {
    toast('Saved: we are the ' + label + ' team. The report is about the ' + done.their_team + ' team.', 'good');
    await loadReport();
  }
}

/* ---- tiles and bars ---- */

function showTiles() {
  const t = report.tiles;
  byId('tile-points').textContent = t.points;
  byId('tile-fg').textContent = t.field_goals[0] + '/' + t.field_goals[1];
  byId('tile-threes').textContent = t.threes[0] + '/' + t.threes[1];
  byId('tile-labelled').textContent = t.labelled;
}

function bar(row, withPoints) {
  const value = el('span', { class: 'value' }, fmtPercent(100 * row.share));
  const right = withPoints
    ? el('span', {}, [value, el('span', { class: 'muted' }, ' · ' + row.points_each.toFixed(1) + ' pts')])
    : value;
  const fill = row.key === labels.press || row.key === 'fast_break' ? 'progress progress-red' : 'progress progress-dark';
  return el('div', { class: 'bar' }, [
    el('div', { class: 'bar-row' }, [el('span', { class: 'name' }, row.label), right]),
    el('div', { class: fill }, el('span', { style: 'width:' + Math.round(100 * row.share) + '%' }))
  ]);
}

function showBars() {
  const defense = byId('defense-bars'), offense = byId('offense-bars');
  defense.replaceChildren(...report.defense.length ? report.defense.map(function (row) { return bar(row, false); })
    : [el('div', { class: 'hint' }, 'No possession of ours has a defense label yet. Label some below and the shares appear here.')]);
  offense.replaceChildren(...report.offense.length ? report.offense.map(function (row) { return bar(row, true); })
    : [el('div', { class: 'hint' }, 'No possession of theirs has an offense label yet.')]);
  const press = report.press;
  const callout = byId('press-callout');
  if (press.baskets) {
    callout.replaceChildren('Pressed after ', el('span', { style: 'font-weight: 700' }, press.pressed + ' of ' + press.baskets), ' made baskets.');
  } else {
    callout.textContent = 'Pressed after ? of ? made baskets: this needs their made shots tagged and our next possession labelled.';
  }
  const missing = byId('not-labelled');
  missing.replaceChildren();
  if (report.not_labelled) {
    missing.appendChild(document.createTextNode(report.not_labelled + ' possession' + (report.not_labelled === 1 ? '' : 's') + ' not labelled yet. '));
    missing.appendChild(el('a', { href: '#labelling', style: 'font-weight: 700' }, 'Label them'));
  } else if (!report.tiles.labelled) {
    missing.appendChild(document.createTextNode('No possessions labelled yet. '));
    missing.appendChild(el('a', { href: '#labelling', style: 'font-weight: 700' }, 'Label them'));
  }
}

/* ---- the scoring heat map: one half court drawn as the shooter sees it, hoop at the top ---- */

function showHeatMap(playerKey) {
  const marks = report.marks.filter(function (mark) { return playerKey === null || mark.key === playerKey; });
  // the player buttons: Team, then every opponent who took a shot
  const keys = [];
  report.marks.forEach(function (mark) { if (keys.indexOf(mark.key) < 0) keys.push(mark.key); });
  const pills = [el('button', { type: 'button', class: 'pill' + (playerKey === null ? ' on' : ''),
    onclick: function () { showHeatMap(null); } }, 'Team')];
  keys.forEach(function (key) {
    pills.push(el('button', { type: 'button', class: 'pill' + (key === playerKey ? ' on' : ''),
      onclick: function () { showHeatMap(key); } }, key.indexOf('tag') === 0 ? 'No number (' + key + ')' : 'No. ' + key));
  });
  byId('heat-players').replaceChildren(...pills);

  const list = byId('heat-areas');
  list.replaceChildren.apply(list, pointsByArea(marks).map(function (row) {
    return el('div', {}, [el('span', {}, row.area), el('span', { class: 'value' }, row.points + (row.points === 1 ? ' pt' : ' pts'))]);
  }));
  if (!list.children.length) list.appendChild(el('div', { class: 'hint' }, 'No points tagged yet.'));
  // the half court itself: drawHalfCourt in app.js, shared with the Game results screen (blue for them, red for us)
  drawHalfCourt(byId('heat-court'), report.court, marks, '#1d5fbf');
  byId('heat-note').textContent = undrawnNote(marks);
}

/* ---- their players ---- */

function showPlayers() {
  const body = byId('players-body');
  const rows = report.players.map(function (p) {
    return el('tr', {}, [
      el('td', { class: 'num' }, p.number === null ? 'no number (' + p.key + ')' : p.number),
      el('td', { style: 'font-weight: 700' }, String(p.points)),
      el('td', {}, p.made + '/' + p.tried),
      el('td', {}, p.threes_made + '/' + p.threes_tried),
      el('td', {}, p.seen_s === null || p.seen_s === undefined ? '-' : fmtClock(p.seen_s)),
      el('td', {}, p.scores_from || '-')
    ]);
  });
  body.replaceChildren(...rows.length ? rows
    : [el('tr', {}, el('td', { colspan: 6, class: 'hint' }, 'No opponent player with a jersey number yet. Players appear here once the game is processed and their shots are tagged.'))]);
}

/* ---- labelling possessions ---- */

async function loadVideo() {
  const info = await tryToast(function () { return api('/api/opponent/video' + q()); });
  if (!info) return;
  byId('video').src = info.url;
  byId('video-note').textContent = info.watching === 'tracked'
    ? 'Showing the tracked video (10 pictures a second): the browser cannot play the original file.'
    : 'The game video. Pause it where a possession starts or ends and press "Now".';
}

function fillSelect(select, choices, blankWords) {
  select.replaceChildren();
  if (blankWords) select.appendChild(el('option', { value: '' }, blankWords));
  choices.forEach(function (choice) { select.appendChild(el('option', { value: choice[0] }, choice[1])); });
}

function openForm(record) {
  editing = record || null;
  const teams = report.teams;
  fillSelect(byId('f-team'), teams.labels.map(function (label) {
    return [label, label + (label === teams.our_team ? ' (us)' : ' (them)')];
  }));
  fillSelect(byId('f-offense'), labels.offense, 'Not labelled yet');
  fillSelect(byId('f-defense'), labels.defense, 'Not labelled yet');
  const now = fmtClock(byId('video').currentTime || 0);
  byId('form-title').textContent = record ? 'Possession ' + record.id : 'New possession';
  byId('f-start').value = record ? fmtClock(record.start_s) : now;
  byId('f-end').value = record ? fmtClock(record.end_s) : '';
  byId('f-team').value = record ? record.team : teams.their_team;
  byId('f-offense').value = record ? record.offense : '';
  byId('f-defense').value = record ? record.defense : '';
  byId('f-points').value = record && record.points_from === 'coach' ? record.points : '';
  byId('f-note').value = record ? record.note : '';
  byId('poss-form').hidden = false;
  byId('form-hint').hidden = true;
  (record ? byId('f-offense') : byId('f-end')).focus();
}

function closeForm() {
  editing = null;
  byId('poss-form').hidden = true;
  byId('form-hint').hidden = false;
}

async function savePossession(event) {
  event.preventDefault();
  const body = { start: byId('f-start').value, end: byId('f-end').value, team: byId('f-team').value,
                 offense: byId('f-offense').value, defense: byId('f-defense').value,
                 points: byId('f-points').value, note: byId('f-note').value };
  if (!body.end.trim()) { toast('Say when the possession ended: pause the video there and press "Now".', 'bad'); return; }
  const saved = await tryToast(function () {
    return editing ? putJSON('/api/opponent/possessions/' + editing.id + q(), body)
                   : postJSON('/api/opponent/possessions' + q(), body);
  });
  if (!saved) return;
  toast(editing ? 'Possession ' + saved.id + ' changed.' : 'Possession ' + saved.id + ' saved.', 'good');
  closeForm();
  await loadReport();        // the percentages come from this list, so the whole report is read again
}

async function deletePossession(record) {
  if (!confirm('Delete possession ' + record.id + ' (' + fmtClock(record.start_s) + ' to ' + fmtClock(record.end_s) + ')?')) return;
  const done = await tryToast(function () { return api('/api/opponent/possessions/' + record.id + q(), { method: 'DELETE' }); });
  if (!done) return;
  toast('Possession ' + record.id + ' deleted.', '');
  if (editing && editing.id === record.id) closeForm();
  await loadReport();
}

async function loadPossessions() {
  const reply = await tryToast(function () { return api('/api/opponent/possessions' + q()); });
  if (!reply) return;
  const body = byId('poss-body');
  const ours = report.teams.our_team;
  const rows = reply.possessions.map(function (record) {
    const unlabelled = !record.offense || !record.defense;
    return el('tr', {}, [
      el('td', { class: 'num' }, String(record.id)),
      el('td', {}, el('a', { href: '#', onclick: function (event) { event.preventDefault(); seekTo(record.start_s); } }, fmtClock(record.start_s))),
      el('td', {}, fmtClock(record.end_s)),
      el('td', {}, el('span', { class: 'chip ' + (record.team === ours ? 'chip-dark' : 'chip-grey') }, record.team === ours ? 'Us' : 'Them')),
      el('td', { class: unlabelled && !record.offense ? 'muted' : null }, record.offense_label),
      el('td', { class: unlabelled && !record.defense ? 'muted' : null }, record.defense_label),
      el('td', {}, record.points + (record.points_from === 'coach' ? ' (typed)' : '')),
      el('td', {}, el('div', { class: 'row', style: 'gap: 6px; flex-wrap: nowrap' }, [
        el('button', { type: 'button', class: 'btn btn-light', onclick: function () { openForm(record); } }, 'Change'),
        el('button', { type: 'button', class: 'btn btn-light', onclick: function () { deletePossession(record); } }, 'Delete')
      ]))
    ]);
  });
  body.replaceChildren(...rows);
  byId('poss-empty').hidden = rows.length > 0;
}

function seekTo(seconds) {
  const video = byId('video');
  if (video.src) { video.currentTime = seconds; video.pause(); }
}

function useVideoTime(inputId) {
  byId(inputId).value = fmtClock(byId('video').currentTime || 0);
}

byId('add-possession').addEventListener('click', function () { openForm(null); byId('video').pause(); });
byId('poss-form').addEventListener('submit', savePossession);
byId('cancel-possession').addEventListener('click', closeForm);
byId('use-start').addEventListener('click', function () { useVideoTime('f-start'); });
byId('use-end').addEventListener('click', function () { useVideoTime('f-end'); });

start();
