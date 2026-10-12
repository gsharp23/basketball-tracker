/* The Teams library: a card per opponent (or, under "Our team", per season for our own side) and the
   compare table. Every number comes from GET /api/teams/library (src/api_teams.py). */

renderHeader('teams', { search: filterCards });


let view = ['opponents', 'ours', 'all'].indexOf(urlParam('view')) >= 0 ? urlParam('view') : 'opponents';
let adding = false;          // the "Add a team" form is open

const SUBTITLES = {
  opponents: 'Every team you have played this season, with what they run on offense and defense, built from this season\'s games against them.',
  all: 'Every team you have played, with what they run on offense and defense, built from every game against them.',
  ours: 'How our own team plays, season by season: what we run on offense and how we defend, built from every game of the season.',
};

/* ---- loading ---- */

async function load() {
  for (const button of byId('view-switch').querySelectorAll('button')) button.classList.toggle('on', button.dataset.view === view);
  byId('subtitle').textContent = SUBTITLES[view];
  byId('compare-note').textContent = (view === 'ours' ? 'Share of our possessions' : 'Share of their possessions') + ' · points per possession in brackets';
  let reply;
  try {
    reply = await api('/api/teams/library?view=' + encodeURIComponent(view));
  } catch (problem) {
    byId('cards').replaceChildren(el('div', { class: 'callout callout-red' }, problem.message));
    return;
  }
  drawMap(reply);
  drawCards(reply);
  drawCompare(reply);
  filterCards(document.getElementById('site-search') ? document.getElementById('site-search').value.trim() : '');
}

function switchView(wanted) {
  view = wanted;
  adding = false;
  history.replaceState(null, '', withParams('teams.html', { view: view === 'opponents' ? '' : view }));
  load();
}

/* ---- the map: home as a star, each opponent as a dot in its colour ---- */

let map = null, markers = null;

function drawMap(reply) {
  const card = byId('map-card'), note = byId('map-note');
  if (view === 'ours') { card.hidden = true; return; }
  card.hidden = false;
  if (typeof L === 'undefined') {
    note.textContent = 'The map needs the internet (it could not be loaded).';
    byId('map').replaceChildren(el('div', { class: 'hint', style: 'padding: 20px' }, 'No map without the internet. The teams are still listed below.'));
    return;
  }
  const placed = reply.cards.filter(function (c) { return c.lat !== null && c.lon !== null && c.lat !== undefined; });
  const home = reply.home && reply.home.lat !== null ? reply.home : null;
  if (!map) {
    map = L.map('map', { scrollWheelZoom: false });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 17, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
  }
  if (markers) markers.remove();
  markers = L.layerGroup().addTo(map);
  const points = [];
  if (home) {
    const star = L.marker([home.lat, home.lon], { icon: L.divIcon({ className: '', html: '<span class="home-pin">★</span>', iconSize: [26, 26], iconAnchor: [13, 13] }), title: 'Home' });
    star.bindPopup('<div class="map-popup"><div class="name">Home</div><div class="small">' + esc(home.place || '') + '</div></div>');
    star.addTo(markers);
    points.push([home.lat, home.lon]);
  }
  placed.forEach(function (c) {
    const pin = L.marker([c.lat, c.lon], { icon: L.divIcon({ className: '', html: '<span class="team-pin" style="display:block;background:' + c.colour + '"></span>', iconSize: [16, 16], iconAnchor: [8, 8] }), title: c.name });
    const versus = c.versus && c.versus.played ? 'We are ' + c.versus.record.text + ' against them' : 'Not played yet';
    const theirs = c.info && c.info.record ? ' · their record ' + esc(c.info.record) : '';
    pin.bindPopup('<div class="map-popup"><div class="name">' + esc(c.name) + '</div><div class="small">' + esc(c.place || '') + '</div>' +
                  '<div>' + versus + theirs + '</div><a href="' + withTeam('team.html', c.id) + '">Open the team →</a></div>');
    pin.addTo(markers);
    points.push([c.lat, c.lon]);
  });
  if (points.length) map.fitBounds(points, { padding: [30, 30], maxZoom: 11 });
  else map.setView([35.8, -78.8], 8);
  const missing = reply.cards.length - placed.length;
  note.textContent = placed.length + ' of ' + reply.cards.length + ' on the map' + (missing ? ' · ' + missing + ' without a place yet (open the team and type its town)' : '');
  setTimeout(function () { map.invalidateSize(); }, 50);
}

/* ---- the cards ---- */

function fmtPoints(value) {
  if (value === null || value === undefined) return '—';
  return Number.isInteger(value) ? String(value) : Number(value).toFixed(1);
}

function fact(label, value) {
  return el('div', {}, [el('span', { class: 'label' }, label), el('span', { class: 'value' }, value)]);
}

function shareWords(top) {
  return top ? top.label + ' ' + top.share + '%' : 'Not labelled yet';
}

function teamCard(card) {
  const ours = view === 'ours';
  const href = card.link === 'season' ? withSeason('season.html', card.id) : withTeam('team.html', card.id);
  const games = card.games ? plural(card.games, 'game') + ' on video' + (card.versus ? ' · ' + plural(card.versus.games_count, 'game') + ' scheduled' : '')
    : (card.versus && card.versus.games_count ? plural(card.versus.games_count, 'game') + ' on the schedule, none on video' : 'No games yet');
  const mix = card.defense_mix, total = mix.zone + mix.man + mix.press;
  const bar = el('div', { class: 'split-bar', 'aria-hidden': 'true' });
  [[mix.zone, 'var(--blue)'], [mix.man, 'var(--blue-soft)'], [mix.press, 'var(--blue-pale)']].forEach(function (part) {
    if (part[0]) bar.appendChild(el('div', { style: 'flex: ' + part[0] + '; background: ' + part[1] }));
  });
  return el('a', { class: 'card card-link', href: href, 'data-name': card.name.toLowerCase() }, [
    el('div', { class: 'team-head' }, [
      el('span', { class: 'team-badge', style: 'background: ' + card.colour }, card.short || '?'),
      el('div', {}, [el('div', { class: 'team-name' }, card.name), el('div', { class: 'team-sub' }, games)])]),
    el('div', { class: 'fact-list' }, [
      ours ? null : fact('Record against them', card.versus && card.versus.played ? card.versus.record.text + ' in ' + plural(card.versus.played, 'game') : 'Not played yet'),
      ours ? null : fact('Their record', card.info && card.info.record ? card.info.record : '—'),
      ours ? null : fact('Where', card.place || '—'),
      fact('Main defense', shareWords(card.main_defense)),
      fact('Main offense', shareWords(card.main_offense)),
      fact(ours ? 'We press after a made basket' : 'Presses after a made basket', card.press_share === null ? '—' : card.press_share + '%'),
      fact(ours ? 'Points per game' : 'Points per game against us', fmtPoints(card.points_per_game))]),
    total ? bar : null,
    el('div', { class: 'small muted' }, total ? 'Defense mix: zone · man · press' : 'Label possessions on a game to see the defense mix'),
  ]);
}

function addCard() {
  return el('button', { class: 'card-dashed', type: 'button', onclick: function () { adding = true; redrawAdd(); } }, [
    icon('plus', 34), el('span', { class: 'big' }, 'Add a team'),
    el('span', { class: 'small muted' }, 'Usually created when you name the opponent on a game')]);
}

function addForm() {
  const name = el('input', { type: 'text', id: 'new-name', placeholder: 'Team name', autocomplete: 'off' });
  const short = el('input', { type: 'text', id: 'new-short', placeholder: 'Up to 3 letters', maxlength: 3 });
  const colour = el('input', { type: 'color', id: 'new-colour', value: '#1d5fbf', style: 'min-height: 44px; width: 100%; padding: 4px; border: 1px solid var(--line-strong); border-radius: 8px; background: var(--card)' });
  const save = el('button', { class: 'btn btn-red', type: 'submit' }, 'Save the team');
  const cancel = el('button', { class: 'btn btn-light', type: 'button', onclick: function () { adding = false; redrawAdd(); } }, 'Cancel');
  const form = el('form', { class: 'card', onsubmit: function (event) { event.preventDefault(); saveTeam(name, short, colour, save); } }, [
    el('h2', { class: 'card-title' }, 'Add a team'),
    el('div', { class: 'field' }, [el('label', { for: 'new-name' }, 'Team name'), name]),
    el('div', { class: 'form-grid' }, [
      el('div', { class: 'field' }, [el('label', { for: 'new-short' }, 'Short code'), short]),
      el('div', { class: 'field' }, [el('label', { for: 'new-colour' }, 'Colour'), colour])]),
    el('div', { class: 'row' }, [save, cancel]),
    el('div', { class: 'hint' }, 'The team also appears here by itself when you name the opponent on a game.'),
  ]);
  setTimeout(function () { name.focus(); }, 0);
  return form;
}

async function saveTeam(name, short, colour, button) {
  if (!name.value.trim()) { toast('Give the team a name.', 'bad'); name.focus(); return; }
  button.disabled = true;
  const made = await tryToast(function () { return postJSON('/api/teams', { name: name.value.trim(), short: short.value.trim(), colour: colour.value }); });
  button.disabled = false;
  if (made) {
    toast(made.name + ' added. Name it as the opponent on a game to fill the card.', 'good');
    adding = false;
    load();
  }
}

function redrawAdd() {
  const grid = byId('cards');
  const old = grid.querySelector('.card-dashed, form.card');
  const fresh = adding ? addForm() : addCard();
  if (old) grid.replaceChild(fresh, old); else grid.appendChild(fresh);
}

function drawCards(reply) {
  const grid = byId('cards');
  grid.replaceChildren();
  reply.cards.forEach(function (card) { grid.appendChild(teamCard(card)); });
  if (view === 'ours') {
    if (!reply.cards.length) grid.appendChild(el('div', { class: 'card' }, [el('h2', { class: 'card-title' }, 'No games in a season yet'), el('div', { class: 'note muted' }, 'Put a game in a season on the Games screen and our own tendencies appear here, one card per season.')]));
    return;
  }
  if (!reply.cards.length && view === 'opponents') {
    grid.appendChild(el('div', { class: 'card' }, [el('h2', { class: 'card-title' }, 'No opponent this season yet'), el('div', { class: 'note muted' }, 'Name the opponent on a game of this season (Games screen) and the team appears here. "All seasons" shows every team you have ever played.')]));
  }
  grid.appendChild(adding ? addForm() : addCard());
}

/* ---- the compare table ---- */

function drawCompare(reply) {
  const wrap = byId('compare-wrap');
  const columns = reply.compare.columns, rows = reply.compare.rows;
  if (!rows.length || !columns.length) {
    wrap.replaceChildren(el('div', { class: 'hint' }, rows.length
      ? 'Nothing to compare yet: label possessions on a game and the play types appear here.'
      : 'Nothing to compare yet.'));
    return;
  }
  const head = el('tr', {}, [el('th', {}, 'Team')].concat(columns.map(function (label) { return el('th', {}, label); }), [el('th', {}, 'Main defense')]));
  const body = rows.map(function (row) {
    const card = reply.cards.find(function (each) { return each.id === row.id; });
    const href = card && card.link === 'season' ? withSeason('season.html', row.id) : withTeam('team.html', row.id);
    return el('tr', { 'data-name': row.name.toLowerCase() }, [el('td', { class: 'strong' }, el('a', { href: href }, row.name))].concat(
      columns.map(function (label) {
        const cell = row.cells[label];
        return el('td', {}, cell ? cell.share + '% (' + Number(cell.points_each).toFixed(1) + ')' : '—');
      }), [el('td', {}, row.main_defense || 'Not labelled yet')]));
  });
  wrap.replaceChildren(el('table', { class: 'table', style: 'min-width: 760px' }, [el('thead', {}, head), el('tbody', {}, body)]));
}

/* ---- the search box in the header ---- */

function filterCards(text) {
  const wanted = (text || '').toLowerCase();
  for (const node of document.querySelectorAll('[data-name]')) node.hidden = Boolean(wanted) && node.dataset.name.indexOf(wanted) < 0;
}

byId('view-switch').addEventListener('click', function (event) {
  const button = event.target.closest('button[data-view]');
  if (button && button.dataset.view !== view) switchView(button.dataset.view);
});
load();
