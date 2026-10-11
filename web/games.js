/* The Games screen: every video in the clips folder, grouped by season (the current season first), with
   the state of each game and what to do next: Open, Progress, Mark court, Carry on, Run here, Run on Colab.
   The rows, the runs and the "Add a game" panel are shared with the Season page: web/game_rows.js.
   Data: GET /api/seasons/games. ?q=words filters the rows (the header search box sends it); ?game=<name>
   points at one game. */

let reply = null;         // GET /api/seasons/games
let searchText = (urlParam('q') || '').toLowerCase();

renderHeader('games', { search: function (text) { searchText = text.toLowerCase(); showGroups(); } });

function matches(row) {
  if (!searchText) return true;
  return [row.opponent, row.stem, row.date, fmtDate(row.date), row.status_words].join(' ').toLowerCase().indexOf(searchText) >= 0;
}

// One card: the season's name, a link to its page, and its games as rows.
function groupCard(group, rows) {
  const title = group.id === 'unassigned'
    ? el('h2', { class: 'card-title' }, 'Not in a season yet')
    : el('h2', { class: 'card-title' }, [seasonName(group.name), group.current ? el('span', { class: 'chip chip-green', style: 'margin-left: 10px' }, 'Current') : null]);
  const side = group.id === 'unassigned'
    ? el('span', { class: 'card-note' }, 'Press Edit on a game to put it in a season')
    : el('a', { href: withParams('season.html', { season: group.id }), class: 'card-link-right' }, 'Open the season →');
  const table = el('table', { class: 'table', style: 'min-width: 640px' }, [
    el('thead', {}, el('tr', {}, [el('th', {}, 'Date'), el('th', {}, 'Opponent'), el('th', {}, 'Result'), el('th', {}, 'Status'), el('th', {}, '')])),
    el('tbody', {}, rows.map(function (row) { return gameRow(row, true); }))]);
  return el('section', { class: 'card', id: 'group-' + group.id }, [
    el('div', { class: 'card-head' }, [title, side]),
    rows.length ? el('div', { class: 'table-wrap' }, table)
      : el('div', { class: 'hint' }, group.id === 'unassigned' ? 'Every game is in a season.' : 'No games in this season yet. Press "Add a game".')]);
}

function showGroups() {
  const holder = byId('season-groups');
  holder.replaceChildren();
  let shown = 0;
  reply.seasons.forEach(function (group) {
    const rows = group.games.filter(matches);
    shown += rows.length;
    // While searching, a season with no matching game is left out.
    if (!searchText || rows.length) holder.appendChild(groupCard(group, rows));
  });
  const loose = reply.unassigned.filter(matches);
  shown += loose.length;
  if (loose.length || (!searchText && !reply.games_total)) holder.appendChild(groupCard({ id: 'unassigned' }, loose));
  if (!reply.games_total) {
    holder.appendChild(el('div', { class: 'callout callout-grey' },
      'No videos in the clips folder yet. Press "Add a game" to see where to copy one.'));
  } else if (searchText && !shown) {
    holder.appendChild(el('div', { class: 'hint' }, 'No game matches "' + searchText + '".'));
  }
  byId('games-line').textContent = reply.games_total
    ? plural(reply.games_total, 'game') + ' in the clips folder, grouped by season.' + (reply.busy ? ' ' + reply.busy : '')
    : 'Every video in the clips folder, grouped by season.';
  // ?game=<name>: point at that game's row.
  const wanted = urlParam('game');
  const row = wanted && byId('row-' + wanted);
  if (row) { row.classList.add('hilite'); row.scrollIntoView({ block: 'center' }); }
}

async function load() {
  reply = await api('/api/seasons/games');
  GameRows.clipsFolder = reply.clips_folder;
  GameRows.defaultSeason = (reply.seasons.find(function (group) { return group.current; }) || { id: 'unassigned' }).id;
  showGroups();
}

GameRows.reload = load;
wireAddPanel();
byId('add-game').addEventListener('click', function () { openAddPanel(urlParam('game')); });
byId('refresh').addEventListener('click', function () { tryToast(load); });
const box = document.getElementById('site-search');
if (box && searchText) box.value = urlParam('q');
load().catch(function (problem) {
  toast(problem.message, 'bad');
  byId('season-groups').replaceChildren(el('div', { class: 'callout callout-red' }, problem.message));
});
if (location.hash === '#add') openAddPanel(urlParam('game'));
