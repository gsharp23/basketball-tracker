/* The Games library of Processing & training: every game video, grouped by season, with where each one is in
   the five steps and what to do next. Data: GET /api/process/games. "Continue" opens Process a game with the
   game picked; "+ Add a game video" and Edit use the shared panel in game_rows.js. */

let reply = null;
let filter = 'all';
let searchText = (urlParam('q') || '').toLowerCase();

renderSidebar('games');

function matches(row) {
  if (filter === 'mine' && (row.done || row.step === 5)) return false;
  if (filter === 'done' && !row.done) return false;
  if (!searchText) return true;
  return [row.opponent, row.stem, row.title, row.date, row.where].join(' ').toLowerCase().indexOf(searchText) >= 0;
}

function actions(row) {
  const parts = [];
  if (row.done) parts.push(el('a', { class: 'btn btn-outline btn-sm', href: withParams('game.html', { game: row.stem }) }, 'Open stats →'));
  else parts.push(el('a', { class: 'btn btn-yellow btn-sm', href: withParams('process.html', { game: row.stem }) }, 'Continue →'));
  if (row.running) parts.push(el('span', { class: 'small muted' }, 'processing'));
  else parts.push(el('a', { href: '#', class: 'small', onclick: function (event) { event.preventDefault(); openAddPanel(row.stem); } }, 'Edit'));
  return el('div', { class: 'actions' }, parts);
}

function gameRowLine(row) {
  const where = el('td', {}, [row.where, row.drive && row.drive.stale ? el('div', { class: 'small text-orange' }, 'No word from Colab for a while') : null]);
  return el('tr', { id: 'row-' + row.stem }, [
    el('td', {}, row.title),
    el('td', { class: row.date_is_guess ? 'muted' : null }, fmtDate(row.date)),
    el('td', { class: 'muted' }, row.length_words + ' · ' + row.size_words),
    where,
    el('td', {}, actions(row))]);
}

function groupCard(group, rows) {
  const title = el('h2', { class: 'card-title' }, [seasonName(group.name), group.current ? el('span', { class: 'chip chip-green', style: 'margin-left: 10px' }, 'Current') : null]);
  const table = el('table', { class: 'table', style: 'width: 100%' }, [
    el('thead', {}, el('tr', {}, [el('th', {}, 'Game'), el('th', {}, 'Date'), el('th', {}, 'Video'), el('th', {}, 'Where it is'), el('th', {}, '')])),
    el('tbody', {}, rows.map(gameRowLine))]);
  return el('section', { class: 'card', id: 'group-' + group.id }, [
    el('div', { class: 'card-head' }, [title, el('span', { class: 'card-note' }, plural(rows.length, 'game'))]),
    rows.length ? el('div', { class: 'table-wrap' }, table) : el('div', { class: 'hint' }, 'No games here yet.')]);
}

function showGroups() {
  const holder = byId('season-groups');
  holder.replaceChildren();
  let shown = 0;
  reply.groups.forEach(function (group) {
    const rows = group.games.filter(matches);
    shown += rows.length;
    if (rows.length || (filter === 'all' && !searchText)) holder.appendChild(groupCard(group, rows));
  });
  if (!reply.games_total) {
    holder.appendChild(el('div', { class: 'callout callout-grey' }, 'No game video yet. Press "+ Add a game video" to see where to copy one.'));
  } else if (!shown) {
    holder.appendChild(el('div', { class: 'hint' }, filter === 'mine' ? 'Nothing needs you right now.' : filter === 'done' ? 'No game has results yet.' : 'No game matches "' + searchText + '".'));
  }
  byId('games-line').textContent = 'Every game video you have added. ' + plural(reply.games_total, 'game') + ' · ' + plural(reply.seasons_total, 'season') + '.' + (reply.busy ? ' ' + reply.busy : '');
  const wanted = urlParam('game');
  const row = wanted && byId('row-' + wanted);
  if (row) { row.classList.add('hilite'); row.scrollIntoView({ block: 'center' }); }
}

async function load() {
  reply = await api('/api/process/games');
  GameRows.clipsFolder = reply.clips_folder;
  const current = reply.groups.find(function (group) { return group.current; });
  GameRows.defaultSeason = current ? current.id : 'unassigned';
  showGroups();
  if (reply.groups.some(function (group) { return group.games.some(function (row) { return row.running || row.step === 5; }); })) {
    setTimeout(function () { load().catch(function () {}); }, 30000);
  }
}

GameRows.reload = load;
wireAddPanel();
byId('add-game').addEventListener('click', function () { openAddPanel(urlParam('game')); });
byId('refresh').addEventListener('click', function () { tryToast(load); });
byId('chips').addEventListener('click', function (event) {
  const button = event.target.closest('button[data-filter]');
  if (!button) return;
  filter = button.dataset.filter;
  for (const each of byId('chips').children) each.classList.toggle('on', each === button);
  showGroups();
});
load().catch(function (problem) {
  toast(problem.message, 'bad');
  byId('season-groups').replaceChildren(el('div', { class: 'callout callout-red' }, problem.message));
});
if (location.hash === '#add' || location.hash === '#edit') openAddPanel(urlParam('game'));
