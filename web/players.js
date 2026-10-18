/* The Players page: our players this season in two tables, Black and Red (a player on both squads is in both),
   with season totals; a click on a name opens the player's own page. "Show unmatched numbers" lists the numbers
   the reader saw that no roster player explains; Edit changes a player's name, number or squad, or marks a number
   as not a real player and merges its minutes and points into someone. Data: GET /api/seasons/<id>/players. */

let season = null;
let page = null;          // the reply of /api/seasons/<id>/players
let editing = null;       // the player row being edited
let searchText = '';

renderHeader('players', { search: function (text) { searchText = text.toLowerCase(); showPlayers(); } });

async function fillSeasonPick() {
  const reply = await api('/api/seasons');
  const pick = byId('season-pick');
  pick.replaceChildren.apply(pick, reply.seasons.map(function (row) {
    return el('option', { value: row.id }, seasonName(row.name) + (row.current ? ' (current)' : ''));
  }));
  pick.value = season.id;
}

function shots(pair) {
  if (!pair.tried) return el('span', { class: 'muted' }, '—');
  return el('span', {}, [fmtMadeTried(pair), el('span', { class: 'small muted' }, ' ' + fmtPct(pair.pct))]);
}

function editLink(player) {
  return el('a', { href: '#', class: 'small', onclick: function (event) { event.preventDefault(); openEdit(player); } }, 'Edit');
}

function nameCell(player) {
  if (!player.name) {
    return el('td', {}, [el('span', { class: 'muted' }, 'No name in the roster'), ' ', el('a', { href: 'season.html#roster', class: 'small' }, 'Roster')]);
  }
  return el('td', {}, el('a', { class: 'name-link', href: withParams('player.html', { season: season.id, number: player.number }) }, player.name));
}

function playerRow(player) {
  return el('tr', {}, [
    el('td', { class: 'num' }, player.number),
    nameCell(player),
    el('td', {}, String(player.games)),
    el('td', { class: 'num' }, String(player.points)),
    el('td', {}, player.points_per_game === null ? '—' : player.points_per_game.toFixed(1)),
    el('td', {}, shots(player.field_goals)),
    el('td', {}, shots(player.threes)),
    el('td', {}, shots(player.free_throws)),
    el('td', {}, player.seen_s ? fmtClock(player.seen_s) : '—'),
    el('td', {}, player.distance_ft ? fmtMiles(player.distance_ft) : '—'),
    el('td', {}, editLink(player))]);
}

function unmatchedRow(player) {
  return el('tr', { class: 'unmatched' }, [
    el('td', { class: 'num' }, player.number),
    el('td', {}, (player.read_as || []).length ? 'also ' + player.read_as.join(', ') : ''),
    el('td', {}, String(player.games)),
    el('td', { class: 'num' }, String(player.points)),
    el('td', {}, player.seen_s ? fmtClock(player.seen_s) : '—'),
    el('td', {}, player.distance_ft ? fmtMiles(player.distance_ft) : '—'),
    el('td', {}, editLink(player))]);
}

function matches(player) {
  if (!searchText) return true;
  return (player.number + ' ' + (player.name || '')).toLowerCase().indexOf(searchText) >= 0;
}

function showPlayers() {
  const players = page.players.filter(matches);
  [['black', 'black-rows', 'black-note'], ['red', 'red-rows', 'red-note']].forEach(function (which) {
    const mine = players.filter(function (p) { return (p.squad || 'both') === which[0] || (p.squad || 'both') === 'both'; });
    const body = byId(which[1]);
    body.replaceChildren.apply(body, mine.map(playerRow));
    byId(which[2]).textContent = plural(mine.length, 'player');
    if (!mine.length) body.appendChild(el('tr', {}, el('td', { colspan: 11, class: 'muted' }, page.players.length ? 'No player on this squad yet: set the squad on the Roster.' : 'No players yet: fill in the Roster and process a game.')));
  });
  const show = byId('show-unmatched').checked;
  byId('unmatched-card').hidden = !show;
  if (show) {
    const body = byId('unmatched-rows');
    body.replaceChildren.apply(body, page.unmatched.map(unmatchedRow));
    if (!page.unmatched.length) body.appendChild(el('tr', {}, el('td', { colspan: 7, class: 'muted' }, 'Every number the reader saw matches a roster player.')));
  }
}

/* ---- Edit a player ---- */

function openEdit(player) {
  editing = player;
  const panel = byId('edit-panel');
  panel.hidden = false;
  byId('edit-title').textContent = 'Edit No. ' + player.number + (player.name ? ' · ' + player.name : '');
  byId('edit-name').value = player.name || '';
  byId('edit-number').value = player.number;
  byId('edit-squad').value = player.squad || 'both';
  byId('edit-not-real').checked = !!player.unmatched && page.roster.merges[player.number] !== undefined;
  const merge = byId('edit-merge');
  merge.replaceChildren(el('option', { value: '' }, 'nobody (just hide it)'));
  Object.keys(page.roster.players).sort(function (a, b) { return Number(a) - Number(b); }).forEach(function (n) {
    if (n !== player.number) merge.appendChild(el('option', { value: n }, 'No. ' + n + (page.roster.players[n] ? ' ' + page.roster.players[n] : '')));
  });
  merge.value = page.roster.merges[player.number] || '';
  byId('edit-merge-field').hidden = !byId('edit-not-real').checked;
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  byId('edit-name').focus();
}

async function saveEdit(event) {
  event.preventDefault();
  const roster = JSON.parse(JSON.stringify(page.roster));
  const oldNumber = editing.number, newNumber = byId('edit-number').value.trim();
  const notReal = byId('edit-not-real').checked;
  if (notReal) {
    delete roster.players[oldNumber];
    delete roster.squads[oldNumber];
    roster.merges[oldNumber] = byId('edit-merge').value;
  } else {
    if (!newNumber) { toast('A player needs a number.', 'bad'); return; }
    if (newNumber !== oldNumber && roster.players[newNumber] !== undefined) { toast('Number ' + newNumber + ' is already on the roster.', 'bad'); return; }
    if (newNumber !== oldNumber) {
      delete roster.players[oldNumber];
      delete roster.squads[oldNumber];
      if (editing.games) roster.merges[oldNumber] = newNumber;          // the stats read under the old number follow the player
    }
    delete roster.merges[newNumber];
    roster.players[newNumber] = byId('edit-name').value.trim();
    roster.squads[newNumber] = byId('edit-squad').value;
  }
  const saved = await tryToast(function () { return putJSON('/api/seasons/' + encodeURIComponent(season.id), { roster: roster }); });
  if (!saved) return;
  toast('Saved.', 'good');
  byId('edit-panel').hidden = true;
  await load();
}

/* ---- loading ---- */

async function load() {
  page = await api('/api/seasons/' + encodeURIComponent(season.id) + '/players');
  document.title = 'Players · ' + seasonName(page.season.name) + ' · Hoops Tracker';
  byId('provisional-note').hidden = !page.provisional;
  byId('roster-link').href = withParams('season.html', { season: season.id }) + '#roster';
  showPlayers();
}

async function start() {
  season = await currentSeason();
  await fillSeasonPick();
  await load();
}

byId('season-pick').addEventListener('change', function () {
  rememberSeason(byId('season-pick').value);
  location.href = withParams('players.html', { season: byId('season-pick').value });
});
byId('show-unmatched').addEventListener('change', showPlayers);
byId('edit-close').addEventListener('click', function () { byId('edit-panel').hidden = true; });
byId('edit-not-real').addEventListener('change', function () { byId('edit-merge-field').hidden = !byId('edit-not-real').checked; });
byId('edit-form').addEventListener('submit', saveEdit);
start().catch(function (problem) {
  toast(problem.message, 'bad');
  byId('black-rows').replaceChildren(el('tr', {}, el('td', { colspan: 11, class: 'muted' }, problem.message)));
});
