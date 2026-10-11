/* What the Season page and the Games page share: one game as a table row (date, opponent, result, status,
   the action to offer), "Run here" (main.py on this laptop), "Run on Colab" (through the Google Drive folder),
   and the "Add a game" panel that puts a video in a season with its opponent, date and score.
   The rows come from the seasons module (GET /api/seasons/<id>/summary or /api/seasons/games): each row is
   library.game_row() plus status_words, chip, action, progress_words and estimate.
   The page sets GameRows.reload (how it re-reads its data) and GameRows.clipsFolder before using the panel. */

const GameRows = {
  reload: async function () {},     // the page re-reads its data (after a run ends, a game is saved...)
  afterSave: null,                  // function (where, stem, saved): what the page does after "Save the game" (null = hide the panel and reload)
  defaultSeason: 'unassigned',      // the season a game is put in unless the coach picks another
  clipsFolder: '',                  // the clips folder as Windows names it, shown in the panel
  allGames: null,                   // GET /api/games, for the panel's list
  homeChoice: null,                 // the Home / Away segment: true, false or null
  watchers: {},                     // stem -> stop function of a running game's poll
  importSeen: {},                   // stem -> the import time the page has already shown (see followColab)
};



// The row's API path, for a fresh copy while the game is processed.
function rowPath(row) {
  return '/api/seasons/' + encodeURIComponent(row.season_id || 'unassigned') + '/game/' + encodeURIComponent(row.stem);
}

/* ---- one row ---- */

// The game's date; the video file's day stands in (in grey) until the coach types the date.
function dateCell(row, extra) {
  if (!row.date) return el('td', { class: 'muted' }, 'no date yet' + (extra || ''));
  if (row.date_is_guess) return el('td', { class: 'muted', title: 'The day of the video file; type the game\'s date with Edit' }, fmtDate(row.date) + (extra || ''));
  return el('td', {}, fmtDate(row.date) + (extra || ''));
}

function gameLink(row, words, anchor) {
  return el('a', { href: withParams('game.html', { game: row.stem }) + (anchor || '') }, words);
}

function editLink(row) {
  return el('a', { href: '#', class: 'small', onclick: function (event) { event.preventDefault(); openAddPanel(row.stem); } }, 'Edit');
}

function actionsFor(row) {
  const parts = [];
  if (row.action === 'open') parts.push(gameLink(row, 'Open'));
  else if (row.action === 'progress') parts.push(gameLink(row, 'Progress'));
  else if (row.action === 'mark court') parts.push(gameLink(row, 'Mark court', '#court'));
  else if (row.action === 'carry on') {
    parts.push(el('button', { class: 'btn btn-red btn-sm', type: 'button', onclick: function () { runHere(row, this); } }, 'Carry on'));
    parts.push(gameLink(row, 'Open'));
    // A game that was on Colab and now has its court marked: the court files go to Colab, which finishes it.
    if (row.processed_on === 'colab') parts.push(colabButton(row));
  } else {
    // Not started here, or sent to Colab and not back yet: both ways stay open (a game Colab failed on, or whose
    // video left the inbox, is sent again with "Run on Colab").
    parts.push(el('button', { class: 'btn btn-dark btn-sm', type: 'button', onclick: function () { runHere(row, this); } }, 'Run here'));
    parts.push(colabButton(row));
  }
  parts.push(editLink(row));
  return el('div', { class: 'actions' }, parts);
}

function colabButton(row) {
  return el('button', { class: 'btn btn-outline btn-sm', type: 'button', onclick: function () { runOnColab(row, this); } }, 'Run on Colab');
}

// chip = true draws the words as a coloured chip (the Games page); the Season page shows plain words.
function statusCell(row, chip) {
  const cell = el('td', { class: 'status-cell' });
  // A game back from Colab whose roster changed since is not waiting on Colab: its words say "Roster changed".
  if (row.processed_on === 'colab' && row.status !== 'finished' && row.status !== 'running' && !row.roster_changed) {
    cell.replaceChildren(el('span', { class: 'chip chip-blue' }, 'On Colab'), el('span', { class: 'small muted' }, 'Asking Google Drive...'));
    followColab(row, cell);
    return cell;
  }
  cell.replaceChildren(chip ? el('span', { class: 'chip chip-' + row.chip }, row.status_words) : row.status_words);
  if (row.progress_words) cell.appendChild(el('span', { class: 'small muted' }, row.progress_words));
  return cell;
}

function opponentCell(row) {
  const opponent = row.opponent
    ? (row.opponent_id ? el('a', { href: withParams('team.html', { team: row.opponent_id }) }, row.opponent) : el('span', {}, row.opponent))
    : el('span', { class: 'muted' }, 'no opponent yet · ' + row.stem);
  return el('td', { class: 'strong' }, opponent);
}

// A row of the games table: Date · Opponent · Result · Status · actions. While the game is processed the row
// refreshes itself. chip = true draws the status as a chip.
function gameRow(row, chip) {
  const tr = el('tr', { id: 'row-' + row.stem }, [
    dateCell(row, row.home === true ? ' · home' : row.home === false ? ' · away' : ''),
    opponentCell(row),
    el('td', { class: resultClass(row) }, fmtResult(row)),
    statusCell(row, chip),
    el('td', {}, actionsFor(row))]);
  if (row.running) watchRun(row);
  return tr;
}

// While a game is processed, its row refreshes every few seconds: the job (GET /api/jobs/run_<stem>) says
// whether it is still going, the seasons route gives the row's words. When the run ends the page reloads its data.
function watchRun(row) {
  if (GameRows.watchers[row.stem]) return;
  GameRows.watchers[row.stem] = pollJob('run_' + row.stem, function (info) {
    const old = byId('row-' + row.stem);
    if (!info.running && !info.starting) {
      delete GameRows.watchers[row.stem];
      GameRows.reload().catch(function (problem) { toast(problem.message, 'bad'); });
      return;
    }
    if (info.starting) {
      if (old) old.querySelector('.status-cell').replaceChildren('Starting...');
      return;
    }
    const chip = !!(old && old.querySelector('.status-cell .chip'));
    api(rowPath(row)).then(function (fresh) { if (old && fresh.running) old.replaceWith(gameRow(fresh, chip)); }).catch(function () {});
  }, 3000);
}

/* ---- "Run here": main.py on this laptop, one game at a time ---- */

function gameTitle(row) { return row.opponent ? 'vs ' + row.opponent + ' (' + row.stem + ')' : row.stem; }

async function runHere(row, button) {
  const carryOn = row.action === 'carry on';
  const question = row.roster_changed
    ? 'Redo the stats of ' + gameTitle(row) + ' with the new roster? Every other step is done; this takes seconds.'
    : (carryOn ? 'Carry on processing ' : 'Process ') + gameTitle(row) + ' on this laptop?\n\n' +
    'It takes ' + row.estimate + ' for this video' + (carryOn ? ' from the start; what is finished is kept' : '') +
    '. Keep the laptop plugged in and awake, and the black Hoops Tracker window open. ' +
    'You can stop at any time and carry on later; nothing finished is lost.';
  if (!window.confirm(question)) return;
  button.disabled = true;
  const started = await tryToast(function () { return postJSON('/api/jobs/start', { kind: 'run', video: row.video }); });
  button.disabled = false;
  if (!started) return;
  putJSON('/api/games/' + encodeURIComponent(row.stem) + '/info', { processed_on: 'laptop' }).catch(function () {});
  toast('Started. The row shows how far it is.', 'good');
  // The program takes a moment to start; the row says so, then follows the run.
  const tr = byId('row-' + row.stem);
  if (tr) {
    tr.querySelector('.status-cell').replaceChildren('Starting...');
    tr.querySelectorAll('button').forEach(function (other) { other.disabled = true; });
  }
  watchRun(row);
}

/* ---- "Run on Colab": through the Google Drive folder (the Drive module's routes) ---- */

// What the Drive module answers about a game (GET /api/drive/status/<stem>), with "not connected" picked out.
async function colabStatus(stem) {
  try {
    const reply = await api('/api/drive/status/' + encodeURIComponent(stem));
    if (reply && reply.connected === false) return { not_connected: true, message: reply.message || 'Google Drive is not connected.' };
    return reply || {};
  } catch (problem) {
    if (/not connected/i.test(problem.message)) return { not_connected: true, message: problem.message };
    return { error: true, message: problem.message };
  }
}

function showNotConnected(message) {
  const note = byId('colab-note');
  if (!note) return;
  note.hidden = false;
  note.replaceChildren(message + ' ', el('a', { href: 'settings.html#drive' }, 'Connect Google Drive on the Settings page'), '.');
}

// The chip for what Colab says: "On Colab · 57%", "Done on Colab", "Back from Colab", "Needs the court", "Failed on Colab".
function colabChip(status, state) {
  const far = status.progress && typeof status.progress === 'object' ? status.progress.how_far : null;
  const percent = typeof far === 'number' ? ' · ' + Math.round(far * 100) + '%' : '';
  if (state === 'done') return el('span', { class: 'chip chip-green' }, status.imported ? 'Back from Colab' : 'Done on Colab');
  if (state === 'needs_court') return el('span', { class: 'chip chip-red' }, 'Needs the court');
  if (state === 'failed') return el('span', { class: 'chip chip-red' }, 'Failed on Colab');
  return el('span', { class: 'chip chip-blue' }, 'On Colab' + percent);
}

// The status cell of a game that was sent to Colab: the chip and the Drive module's words, asked again every
// 15 seconds until the results are here or Colab has failed. A finished game's results come back by themselves
// within a minute (the dashboard looks in the Drive folder), or now with "Import now"; the page reads its rows
// again ONCE when they have arrived (the import time changes), never in a loop.
async function followColab(row, cell) {
  const status = await colabStatus(row.stem);
  if (!document.body.contains(cell)) return;
  if (status.not_connected) showNotConnected(status.message);
  const state = String(status.state || '').toLowerCase();
  cell.replaceChildren(colabChip(status, state), el('span', { class: 'small muted' }, status.message || status.words || 'Sent to Colab'));
  if (status.can_import) {
    cell.appendChild(el('button', { class: 'btn btn-outline btn-sm', type: 'button', onclick: function () { importNow(row, this); } }, 'Import now'));
  }
  if (status.imported && GameRows.importSeen[row.stem] !== status.imported) {
    GameRows.importSeen[row.stem] = status.imported;
    GameRows.reload().catch(function () {});
    return;
  }
  const keepAsking = !status.error && !status.not_connected && state !== 'failed' && !status.imported;
  if (keepAsking) setTimeout(function () { if (document.body.contains(cell)) followColab(row, cell); }, 15000);
}

// "Import now": bring a finished game's results back from the Drive folder without waiting for the next look.
async function importNow(row, button) {
  button.disabled = true;
  const reply = await tryToast(function () { return postJSON('/api/drive/import/' + encodeURIComponent(row.stem)); });
  if (!reply) { button.disabled = false; return; }
  toast(reply.message, reply.missing && reply.missing.length ? '' : 'good');
  await GameRows.reload();
}

async function runOnColab(row, button) {
  const question = 'Send ' + gameTitle(row) + ' to Google Drive for Colab?\n\n' +
    'The video is copied into the Drive folder on this computer; Google Drive for desktop then uploads it to your Google account, ' +
    'and Colab processes it (about an hour) once its notebook is running. The results come back the same way.';
  if (!window.confirm(question)) return;
  button.disabled = true;
  try {
    const reply = await postJSON('/api/drive/send/' + encodeURIComponent(row.stem));
    toast((reply && reply.message) || 'Sent. The row shows what Drive and Colab say.', 'good');
    await GameRows.reload();
  } catch (problem) {
    if (/not connected/i.test(problem.message)) showNotConnected(problem.message);
    toast(problem.message, 'bad');
  }
  button.disabled = false;
}

/* ---- "Add a game": put a video in a season, name the opponent, the date, the score ---- */

function setHome(value) {
  GameRows.homeChoice = value;
  for (const button of byId('assign-home').querySelectorAll('button')) {
    button.classList.toggle('on', value !== null && String(value) === button.dataset.home);
  }
}

function fillFormFrom(stem) {
  const game = GameRows.allGames.games.find(function (row) { return row.name === stem; });
  if (!game) return;
  const info = game.info;
  byId('assign-season').value = info.season_id || GameRows.defaultSeason;
  byId('assign-opponent').value = info.opponent || '';
  byId('assign-date').value = info.date_is_guess ? '' : (info.date || '');
  byId('assign-date').placeholder = info.date_is_guess ? info.date : '';
  byId('assign-us').value = info.score_us === null ? '' : info.score_us;
  byId('assign-them').value = info.score_them === null ? '' : info.score_them;
  setHome(info.home);
}

// The panel's lists: every video in the clips folder (the unassigned ones first), the seasons, the team names.
async function fillGameList(chosen) {
  GameRows.allGames = await api('/api/games');
  const seasons = await api('/api/seasons');
  const all = GameRows.allGames;
  const select = byId('assign-game');
  const order = function (row) { return row.season_id === 'unassigned' ? 0 : row.season_id === GameRows.defaultSeason ? 1 : 2; };
  const games = all.games.slice().sort(function (a, b) { return order(a) - order(b) || a.name.localeCompare(b.name); });
  select.replaceChildren.apply(select, games.map(function (row) {
    const where = row.season_id === 'unassigned' ? 'not in a season' : 'in ' + seasonName(all.seasons[row.season_id] || row.season_id);
    return el('option', { value: row.name }, row.name + ' · ' + fmtLength(row.seconds) + ' · ' + where + (row.info.opponent ? ' · vs ' + row.info.opponent : ''));
  }));
  const seasonBox = byId('assign-season');
  seasonBox.replaceChildren.apply(seasonBox, seasons.seasons.map(function (row) { return el('option', { value: row.id }, seasonName(row.name)); })
    .concat([el('option', { value: 'unassigned' }, 'No season')]));
  const names = byId('team-names');
  names.replaceChildren.apply(names, Object.keys(all.teams).map(function (id) { return el('option', { value: all.teams[id].name }); }));
  if (!games.length) select.appendChild(el('option', { value: '' }, 'No video in the clips folder yet'));
  select.value = chosen && games.some(function (row) { return row.name === chosen; }) ? chosen : (games[0] ? games[0].name : '');
  fillFormFrom(select.value);
}

async function openAddPanel(stem) {
  const panel = byId('add-panel');
  panel.hidden = false;
  byId('clips-folder').textContent = GameRows.clipsFolder;
  await tryToast(function () { return fillGameList(stem); });
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  byId('assign-opponent').focus();
}

async function saveAssignment(event) {
  event.preventDefault();
  const stem = byId('assign-game').value;
  if (!stem) { toast('There is no video to add yet: copy one into the clips folder and press Refresh.', 'bad'); return; }
  const button = byId('assign-save');
  button.disabled = true;
  const saved = await tryToast(function () {
    return putJSON('/api/games/' + encodeURIComponent(stem) + '/info', {
      season_id: byId('assign-season').value,
      opponent: byId('assign-opponent').value.trim(),
      date: byId('assign-date').value,
      home: GameRows.homeChoice,
      score_us: byId('assign-us').value.trim(),
      score_them: byId('assign-them').value.trim(),
    });
  });
  button.disabled = false;
  if (!saved) return;
  const where = byId('assign-season').value;
  toast('Saved: ' + stem + (saved.opponent ? ' vs ' + saved.opponent : '') + (where === 'unassigned' ? ' (in no season)' : ''), 'good');
  if (GameRows.afterSave) { GameRows.afterSave(where, stem, saved); return; }
  byId('add-panel').hidden = true;
  await GameRows.reload();
}

// The panel's own buttons; the page wires its "Add a game" button to openAddPanel() itself.
function wireAddPanel() {
  byId('add-close').addEventListener('click', function () { byId('add-panel').hidden = true; });
  byId('add-refresh').addEventListener('click', function () {
    tryToast(function () { return fillGameList(byId('assign-game').value); }).then(function () { toast('The clips folder was read again.', ''); });
  });
  byId('assign-game').addEventListener('change', function () { fillFormFrom(byId('assign-game').value); });
  byId('assign-home').addEventListener('click', function (event) {
    const button = event.target.closest('button');
    if (button) setHome(button.dataset.home === 'true');
  });
  byId('assign-form').addEventListener('submit', saveAssignment);
}
