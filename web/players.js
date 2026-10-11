/* The Players page: the current season's players with their season totals; a click on a player shows
   that player's season shot map and one line per game. Numbers come from GET /api/seasons/<id>/players. */

let season = null;
let page = null;          // the reply of /api/seasons/<id>/players
let chosen = null;        // the jersey number shown in the panel
let searchText = '';

renderHeader('players', { search: function (text) { searchText = text.toLowerCase(); showPlayers(); } });


/* ---- the season chooser in the title row ---- */

async function fillSeasonPick() {
  const reply = await api('/api/seasons');
  const pick = byId('season-pick');
  pick.replaceChildren.apply(pick, reply.seasons.map(function (row) {
    return el('option', { value: row.id }, seasonName(row.name) + (row.current ? ' (current)' : ''));
  }));
  if (reply.unassigned) pick.appendChild(el('option', { value: 'unassigned' }, 'Unassigned games'));
  pick.value = season.id;
}

/* ---- the table ---- */

function shots(pair) {
  if (!pair.tried) return el('span', { class: 'muted' }, '—');
  return el('span', {}, [fmtMadeTried(pair), el('span', { class: 'small muted' }, ' ' + fmtPct(pair.pct))]);
}

function playerRow(player) {
  const row = el('tr', { class: player.number === chosen ? 'hilite' : null, onclick: function () { choose(player.number); } }, [
    el('td', { class: 'num' }, player.number),
    el('td', { class: 'strong' }, player.name || el('span', { class: 'muted' }, 'No name in the roster')),
    el('td', {}, String(player.games)),
    el('td', { class: 'num' }, String(player.points)),
    el('td', {}, player.points_per_game === null ? '—' : player.points_per_game.toFixed(1)),
    el('td', {}, shots(player.field_goals)),
    el('td', {}, shots(player.threes)),
    el('td', {}, shots(player.free_throws)),
    el('td', {}, player.seen_s ? fmtClock(player.seen_s) : '—'),
    el('td', {}, player.distance_ft ? fmtMiles(player.distance_ft) : '—')]);
  return row;
}

function matches(player) {
  if (!searchText) return true;
  return (player.number + ' ' + (player.name || '')).toLowerCase().indexOf(searchText) >= 0;
}

function showPlayers() {
  const body = byId('player-rows'), note = byId('players-note');
  const players = page.players.filter(matches);
  body.replaceChildren.apply(body, players.map(playerRow));
  note.hidden = players.length > 0;
  if (!players.length) {
    note.textContent = page.players.length ? 'No player matches "' + searchText + '".'
      : 'No players yet. Fill in the roster (the Roster button on the season page) and process a game: every player whose number was read on our team appears here.';
  }
}

/* ---- one player: the season shot map and the game lines ---- */

function choose(number) {
  chosen = number;
  showPlayers();
  const player = page.players.find(function (row) { return row.number === number; });
  if (!player) return;
  const marks = page.marks.filter(function (mark) { return String(mark.number) === String(number); });
  const lines = page.lines[number] || [];
  const court = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  court.setAttribute('class', 'court');
  court.setAttribute('role', 'img');
  court.setAttribute('aria-label', 'Where this player scored from this season');
  drawHalfCourt(court, page.court, marks, '#d81e27');
  const areas = pointsByArea(marks);
  const lineRows = lines.map(function (line) {
    return el('tr', {}, [
      el('td', {}, fmtDate(line.date)),
      el('td', { class: 'strong' }, el('a', { href: withParams('game.html', { game: line.stem }) }, line.opponent || line.stem)),
      el('td', { class: 'num' }, String(line.points)),
      el('td', {}, shots(line.field_goals)),
      el('td', {}, shots(line.threes)),
      el('td', {}, line.seen_s ? fmtClock(line.seen_s) : '—'),
      el('td', {}, line.distance_ft ? fmtFeet(line.distance_ft) : '—')]);
  });
  byId('player-panel').replaceChildren(
    el('div', { class: 'player-head' }, [
      el('span', { class: 'lead-number' }, number),
      el('div', {}, [el('div', { class: 'name' }, player.name || 'No. ' + number),
                     el('div', { class: 'small muted' }, plural(player.games, 'game') + ' · ' + plural(marks.length, 'shot') + ' tagged')])]),
    el('div', { class: 'card-head' }, [el('h3', { class: 'card-title-small' }, 'Season shot map'), el('span', { class: 'card-note' }, String(player.points) + ' points')]),
    court,
    el('div', { class: 'area-list' }, areas.length
      ? areas.map(function (area) { return el('div', {}, [el('span', {}, area.area), el('span', { class: 'value' }, plural(area.points, 'point'))]); })
      : [el('div', { class: 'hint' }, 'No made shot tagged for this player yet.')]),
    el('div', { class: 'hint' }, marks.length ? undrawnNote(marks) : ''),
    el('h3', { class: 'card-title-small' }, 'Game by game'),
    lines.length
      ? el('div', { class: 'table-wrap' }, el('table', { class: 'table lines-table' }, [
          el('thead', {}, el('tr', {}, [el('th', {}, 'Date'), el('th', {}, 'Opponent'), el('th', {}, 'Pts'), el('th', {}, 'FG'), el('th', {}, '3PT'), el('th', {}, 'Seen'), el('th', {}, 'Distance')])),
          el('tbody', {}, lineRows)]))
      : el('div', { class: 'hint' }, 'This player has not been seen in a processed game of this season yet.'));
}

/* ---- loading ---- */

async function load() {
  page = await api('/api/seasons/' + encodeURIComponent(season.id) + '/players');
  byId('players-title').textContent = 'Players · ' + seasonName(page.season.name);
  document.title = 'Players · ' + seasonName(page.season.name) + ' · Hoops Tracker';
  byId('provisional-note').hidden = !page.provisional;
  showPlayers();
  if (page.players.length) choose(page.players[0].number);
  else byId('player-panel').replaceChildren(el('div', { class: 'hint' }, 'Choose a player in the table once there are some.'));
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
start().catch(function (problem) {
  toast(problem.message, 'bad');
  byId('player-rows').replaceChildren(el('tr', {}, el('td', { colspan: 10, class: 'muted' }, problem.message)));
});
