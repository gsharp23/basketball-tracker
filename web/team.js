/* One team: what they run on offense, how they defend us, where they score, their players by number
   (with the coach's notes), the games against them. Numbers from GET /api/teams/<id>/summary. */

renderHeader('teams');


let teamId = currentTeam();
let summary = null;
let shownNumber = null;      // null = the whole team on the heat map

async function start() {
  if (!teamId) { showNotFound('Open a team from the Teams screen.'); return; }
  byId('report-link').href = withTeam('report.html', teamId);
  try {
    summary = await api('/api/teams/' + encodeURIComponent(teamId) + '/summary');
  } catch (problem) {
    showNotFound(problem.message);
    return;
  }
  showHead();
  showTables();
  showPills();
  showCourt();
  showPlayers();
  showGames();
  byId('report-body').hidden = false;
}

function showNotFound(text) {
  byId('subtitle').textContent = '';
  byId('report-link').hidden = true;
  byId('label-link').hidden = true;
  byId('not-found-text').textContent = text + ' ';
  byId('not-found-text').appendChild(el('a', { href: 'teams.html' }, 'Open the Teams screen.'));
  byId('not-found').hidden = false;
  byId('report-link').classList.add('is-waiting');
  byId('label-link').classList.add('is-waiting');
}

/* ---- the title row ---- */

function showHead() {
  const team = summary.team;
  document.title = team.name + ' · Hoops Tracker';
  byId('badge').textContent = team.short || '?';
  byId('badge').style.background = team.colour;
  byId('title').textContent = team.name;
  const bits = [plural(summary.games_count, 'game') + ' against us', 'we are ' + summary.record.text,
                summary.possessions_labelled + ' of their possessions labelled'];
  byId('subtitle').textContent = bits.join(' · ');
  const label = byId('label-link');
  if (summary.latest_game) {
    label.href = withParams('tag.html', { game: summary.latest_game, mode: 'plays' });
    label.title = 'Label the possessions of the newest game against them';
  } else {
    label.removeAttribute('href');
    label.classList.add('is-waiting');
    label.title = 'Add a game against them first';
  }
  byId('provisional').hidden = !summary.provisional;
}

/* ---- the two tables and their callouts ---- */

function showTables() {
  byId('offense').replaceChildren(offenseTable(summary, true));
  byId('offense-callout').replaceChildren(offenseCallout(summary) || '');
  byId('defense').replaceChildren(defenseTable(summary, true));
  byId('defense-callout').replaceChildren(pressCallout(summary));
}

/* ---- where they score ---- */

function showPills() {
  const pills = byId('who-pills');
  pills.replaceChildren();
  const numbers = shooterNumbers(summary);
  if (!numbers.length) return;
  const all = [null].concat(numbers);
  all.forEach(function (number) {
    pills.appendChild(el('button', { type: 'button', class: 'pill' + (number === shownNumber ? ' on' : ''),
      onclick: function () { shownNumber = number; showPills(); showCourt(); } }, number === null ? 'Team' : 'No. ' + number));
  });
}

function showCourt() {
  byId('court-note').textContent = drawTheirShots(byId('court'), summary, shownNumber);
}

/* ---- their players, with an editable note each ---- */

function noteCell(row) {
  const cell = el('td', {});
  showNote(cell, row.number, row.note);
  return cell;
}

function showNote(cell, number, text) {
  cell.replaceChildren(el('button', { type: 'button', class: 'note-button' + (text ? '' : ' empty'),
    'aria-label': 'Note for number ' + number, onclick: function () { editNote(cell, number, text); } }, text || 'Add a note'));
}

function editNote(cell, number, text) {
  const input = el('input', { type: 'text', class: 'input note-input', value: text || '', maxlength: 200, 'aria-label': 'Note for number ' + number });
  let done = false;
  async function finish(save) {
    if (done) return;
    done = true;
    const fresh = input.value.trim();
    if (!save || fresh === (text || '')) { showNote(cell, number, text); return; }
    const notes = {};
    notes[number] = fresh;
    const team = await tryToast(function () { return putJSON('/api/teams/' + encodeURIComponent(teamId), { notes: notes }); });
    if (team) {
      summary.team.notes = team.notes;
      summary.players.forEach(function (player) { if (player.number === number) player.note = fresh; });
      toast(fresh ? 'Note saved for number ' + number + '.' : 'Note removed for number ' + number + '.', 'good');
      showNote(cell, number, fresh);
    } else {
      showNote(cell, number, text);
    }
  }
  input.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') finish(true);
    if (event.key === 'Escape') finish(false);
  });
  input.addEventListener('blur', function () { finish(true); });
  cell.replaceChildren(input);
  input.focus();
}

function showPlayers() {
  byId('players').replaceChildren(playersTable(summary, noteCell));
}

/* ---- the games against them ---- */

function showGames() {
  const holder = byId('games');
  holder.replaceChildren();
  if (!summary.games.length) {
    holder.appendChild(el('div', { class: 'hint' }, 'No game against them yet. Name them as the opponent on a game (Games screen) and it appears here.'));
    return;
  }
  summary.games.slice().reverse().forEach(function (row) { holder.appendChild(gameCard(row)); });    // newest first
}

start();
