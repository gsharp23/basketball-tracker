/* The Seasons library: one card per season (the current one first), "New season" with its roster,
   and the recent games with the right action for each. Numbers come from GET /api/seasons/library. */

let library = null;
let searchText = '';

renderHeader('seasons', { search: function (text) { searchText = text.toLowerCase(); showRecent(); } });


/* ---- the season cards ---- */

// The small line of points per game: a polyline across the card, a dot on the latest game.
function trendSvg(values, colour) {
  const SVG = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 340 60');
  svg.setAttribute('class', 'trend');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Points per game this season');
  function add(tag, attrs) {
    const node = document.createElementNS(SVG, tag);
    for (const name in attrs) node.setAttribute(name, attrs[name]);
    svg.appendChild(node);
    return node;
  }
  if (!values.length) {
    add('line', { x1: 10, y1: 40, x2: 330, y2: 40, stroke: '#c9cdd4', 'stroke-width': 2, 'stroke-dasharray': '6 6' });
    const text = add('text', { x: 170, y: 26, 'text-anchor': 'middle', 'font-size': 12, fill: '#8a919c', 'font-family': 'Barlow, sans-serif' });
    text.textContent = 'No points yet: type a score or tag the shots';
    return svg;
  }
  const low = Math.min.apply(null, values), high = Math.max.apply(null, values);
  const span = Math.max(high - low, 10);
  const points = values.map(function (value, i) {
    const x = values.length === 1 ? 170 : 10 + 320 * i / (values.length - 1);
    const y = 50 - 40 * (value - low + (span - (high - low)) / 2) / span;
    return [Math.round(x), Math.round(y)];
  });
  add('polyline', { points: points.map(function (p) { return p.join(','); }).join(' '), fill: 'none', stroke: colour, 'stroke-width': 3 });
  const last = points[points.length - 1];
  add('circle', { cx: last[0], cy: last[1], r: 5, fill: colour });
  return svg;
}

function fact(label, value) {
  return el('div', { class: 'fact' }, [el('div', { class: 'fact-label' }, label), el('div', { class: 'fact-value' }, value)]);
}

function seasonCard(season) {
  const current = season.current;
  let foot;
  if (season.last_game) {
    const last = season.last_game;
    const who = last.opponent ? 'vs ' + last.opponent : last.stem;
    const result = fmtResult(last);
    foot = 'Last game: ' + who + (result !== '—' ? ', ' + result : '');
  } else {
    foot = 'Roster: ' + plural(season.roster_players, 'player');
  }
  return el('a', { class: 'card card-link season-card', href: withParams('season.html', { season: season.id }) }, [
    el('div', { class: 'top' }, [
      el('div', {}, [el('div', { class: 'eyebrow' }, current ? 'Current' : 'Archived'), el('div', { class: 'season-name' }, season.name)]),
      el('span', { class: 'chip chip-big ' + (current ? 'chip-green' : 'chip-grey') }, current ? 'In progress' : 'Complete')]),
    el('div', { class: 'fact-grid' }, [
      fact('Record', season.record),
      fact('Games', String(season.games)),
      fact('Pts / game', season.points_per_game === null ? '—' : String(Math.round(season.points_per_game)))]),
    trendSvg(season.trend, current ? '#d81e27' : '#5a5f6a'),
    el('div', { class: 'card-foot' }, [el('span', {}, foot), el('span', { class: 'open' }, 'Open →')])]);
}

function unassignedCard(count) {
  return el('a', { class: 'card card-link season-card', href: withParams('season.html', { season: 'unassigned' }) }, [
    el('div', { class: 'top' }, [
      el('div', {}, [el('div', { class: 'eyebrow' }, 'No season'), el('div', { class: 'season-name' }, 'Unassigned')]),
      el('span', { class: 'chip chip-big chip-grey' }, plural(count, 'game'))]),
    el('div', { class: 'note muted' }, 'Videos in the clips folder that are not in a season yet. Open to put each one in its season and name the opponent.'),
    el('div', { class: 'card-foot' }, [el('span', {}, 'Nothing is counted until a game is in a season'), el('span', { class: 'open' }, 'Open →')])]);
}

function showCards() {
  const grid = byId('season-cards');
  const cards = library.seasons.map(seasonCard);
  if (library.unassigned) cards.push(unassignedCard(library.unassigned));
  cards.push(el('button', { class: 'card-dashed', type: 'button', style: 'min-height: 280px', onclick: openNewPanel }, [
    icon('plus', 36), el('span', { class: 'big' }, 'New season'),
    el('span', { class: 'small muted' }, 'Name it, add the roster, start adding games')]));
  grid.replaceChildren.apply(grid, cards);
}

/* ---- the recent games ---- */


function gameLink(row, words, anchor) {
  return el('a', { href: withParams('game.html', { game: row.stem }) + (anchor || '') }, words);
}

// The one link the mockup gives each state: Open, Progress, Mark court; Start / Carry on go to the game's own page.
function actionLink(row) {
  if (row.action === 'open') return gameLink(row, 'Open');
  if (row.action === 'progress') return gameLink(row, 'Progress');
  if (row.action === 'mark court') return gameLink(row, 'Mark court', '#court');
  if (row.action === 'carry on') return gameLink(row, 'Carry on');
  return gameLink(row, 'Start');
}

function statusChip(row) {
  if (row.processed_on === 'colab' && row.status !== 'finished' && row.status !== 'running') {
    return el('span', { class: 'chip chip-blue' }, 'On Colab');
  }
  return el('span', { class: 'chip chip-' + row.chip, title: row.progress_words || null }, row.status_words);
}

function matches(row) {
  if (!searchText) return true;
  const words = [row.opponent, row.stem, row.date, fmtDate(row.date), row.season_name, row.status_words].join(' ').toLowerCase();
  return words.indexOf(searchText) >= 0;
}

function showRecent() {
  const body = byId('recent-rows'), note = byId('recent-note');
  const rows = library.recent.filter(matches);
  body.replaceChildren.apply(body, rows.map(function (row) {
    const opponent = row.opponent
      ? el('span', {}, [row.opponent, row.season_name ? el('span', { class: 'small muted' }, ' · ' + row.season_name) : null])
      : el('span', { class: 'muted' }, 'no opponent yet · ' + row.stem);
    return el('tr', {}, [
      dateCell(row),
      el('td', { class: 'strong' }, opponent),
      el('td', { class: resultClass(row) }, fmtResult(row)),
      el('td', {}, statusChip(row)),
      el('td', {}, String(row.shots)),
      el('td', {}, actionLink(row))]);
  }));
  note.hidden = rows.length > 0;
  if (!rows.length) {
    note.textContent = library.games_total
      ? 'No game matches "' + searchText + '".'
      : 'No games yet. Copy a video into the clips folder, then open a season and press "Add a game".';
  }
}

/* ---- "New season" ---- */

function rosterRow(number, name) {
  return el('tr', {}, [
    el('td', {}, el('input', { class: 'input', type: 'text', value: number, placeholder: 'No.', inputmode: 'numeric', 'aria-label': 'Jersey number' })),
    el('td', {}, el('input', { class: 'input', type: 'text', value: name, placeholder: 'Name', 'aria-label': 'Player name' }))]);
}

function readRoster() {
  const players = {};
  for (const row of byId('new-roster-rows').querySelectorAll('tr')) {
    const boxes = row.querySelectorAll('input');
    const number = boxes[0].value.trim(), name = boxes[1].value.trim();
    if (!number && !name) continue;
    if (!number) throw new Error('Every name needs a jersey number (the "No." box next to it).');
    if (number in players) throw new Error('Number ' + number + ' is listed twice.');
    players[number] = name;
  }
  return { name: byId('new-team-name').value.trim(), players: players };
}

// A season is named after the year it starts in, as coaches do: "2027-28".
function suggestedName() {
  const now = new Date();
  const first = now.getMonth() >= 7 ? now.getFullYear() : now.getFullYear() - 1;
  const taken = (library ? library.seasons : []).map(function (season) { return season.name; });
  let year = first;
  while (taken.indexOf(year + '-' + String(year + 1).slice(2)) >= 0) year += 1;
  return year + '-' + String(year + 1).slice(2);
}

function openNewPanel() {
  const panel = byId('new-panel');
  if (!panel.hidden) { byId('new-name').focus(); return; }
  panel.hidden = false;
  byId('new-name').value = suggestedName();
  byId('new-start').value = new Date().toISOString().slice(0, 10);
  const body = byId('new-roster-rows');
  body.replaceChildren();
  for (let i = 0; i < 5; i++) body.appendChild(rosterRow('', ''));
  // The current roster is a good start for next season's: copy its names in for editing.
  const current = library && library.seasons.find(function (season) { return season.current; });
  if (current && current.roster_players) {
    api('/api/seasons').then(function (reply) {
      const row = reply.seasons.find(function (season) { return season.id === current.id; });
      if (!row) return;
      byId('new-team-name').value = row.roster.name || '';
      const numbers = Object.keys(row.roster.players).sort(function (a, b) { return Number(a) - Number(b); });
      body.replaceChildren.apply(body, numbers.map(function (number) { return rosterRow(number, row.roster.players[number]); }));
      for (let i = 0; i < 3; i++) body.appendChild(rosterRow('', ''));
      toast('The current roster was copied in as a start: change it as you like.', '');
    }).catch(function () {});
  }
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  byId('new-name').focus();
}

function closeNewPanel() { byId('new-panel').hidden = true; }

async function createSeason(event) {
  event.preventDefault();
  const button = byId('new-save');
  button.disabled = true;
  const made = await tryToast(function () {
    const name = byId('new-name').value.trim();
    if (!name) throw new Error('Give the season a name, like 2027-28.');
    return postJSON('/api/seasons', { name: name, start: byId('new-start').value, roster: readRoster(), current: byId('new-current').checked });
  });
  button.disabled = false;
  if (!made) return;
  rememberSeason(made.id);
  toast('Season ' + made.name + ' made. Names stay on this laptop.', 'good');
  location.href = withParams('season.html', { season: made.id });
}

/* ---- loading, and following a run ---- */

let refreshTimer = null;

async function loadLibrary() {
  clearTimeout(refreshTimer);
  library = await api('/api/seasons/library');
  showCards();
  showRecent();
  // While a game is being processed the table refreshes by itself.
  if (library.recent.some(function (row) { return row.running; })) refreshTimer = setTimeout(function () { loadLibrary().catch(function () {}); }, 5000);
}

byId('new-season').addEventListener('click', openNewPanel);
byId('new-cancel').addEventListener('click', closeNewPanel);
byId('new-roster-add').addEventListener('click', function () { byId('new-roster-rows').appendChild(rosterRow('', '')); });
byId('new-form').addEventListener('submit', createSeason);
loadLibrary().catch(function (problem) {
  toast(problem.message, 'bad');
  byId('season-cards').replaceChildren(el('div', { class: 'callout callout-red' }, problem.message));
});
