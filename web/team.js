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
  showAbout();
  showHeadToHead();
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

/* ---- about them: what the coach typed or pasted, and the links ---- */

function aboutFact(label, value) {
  return el('div', {}, [el('span', { class: 'label' }, label), el('span', { class: 'value' }, value || '—')]);
}

function showAbout() {
  const team = summary.team, info = team.info || {};
  const facts = byId('about-facts');
  facts.replaceChildren(
    aboutFact('Where', team.place),
    aboutFact('Their record', info.record),
    aboutFact('Points for / against per game', info.points_for || info.points_against ? (info.points_for || '—') + ' / ' + (info.points_against || '—') : ''),
    aboutFact('Coach', info.coach),
    aboutFact('Level', info.level),
    aboutFact('Their roster', Object.keys(team.roster || {}).length ? plural(Object.keys(team.roster).length, 'player') + ' listed' : ''));
  const links = byId('about-links');
  links.replaceChildren.apply(links, (team.links || []).map(function (link) {
    return el('a', { href: link.url, target: '_blank', rel: 'noopener', class: 'btn btn-light btn-sm' }, link.label + ' ↗');
  }));
  byId('about-note').textContent = info.updated ? 'Typed or pasted ' + fmtDate(info.updated) + (info.source ? ' from ' + info.source : '') + '.'
    : 'Nothing typed about them yet. Edit adds where they are (for the map), their record, links to their pages and their roster.';
}

function showHeadToHead() {
  const versus = summary.head_to_head;
  const facts = byId('h2h-facts');
  facts.replaceChildren(
    aboutFact('Record against them', versus.played ? versus.record.text : 'Not played yet'),
    aboutFact('Our points per game', versus.points_per_game === null ? '' : String(Math.round(versus.points_per_game))),
    aboutFact('Their points per game', versus.allowed_per_game === null ? '' : String(Math.round(versus.allowed_per_game))),
    aboutFact('Coming up', versus.upcoming ? plural(versus.upcoming, 'game') : 'None scheduled'));
  byId('h2h-note').textContent = plural(versus.games_count, 'game') + ' over every season';
  const body = byId('h2h-rows');
  body.replaceChildren.apply(body, versus.rows.slice().reverse().map(function (row) {
    const result = row.result ? fmtResult(row) : (row.date >= new Date().toISOString().slice(0, 10) ? 'Upcoming' : '—');
    const cell = row.stem && row.status === 'finished'
      ? el('a', { href: withParams('game.html', { game: row.stem }) }, result) : result;
    return el('tr', {}, [el('td', {}, fmtDate(row.date) + ' ' + seasonName(row.season_name)), el('td', {}, row.home === true ? 'Home' : row.home === false ? 'Away' : ''),
                         el('td', { class: resultClass(row) }, cell)]);
  }));
}

/* ---- Edit: where they are, links, record, roster ---- */

let foundPlace = null;      // {lat, lon, place} from "Find on the map", until saved

function openAbout() {
  const team = summary.team, info = team.info || {};
  byId('about-panel').hidden = false;
  byId('about-place').value = team.place || '';
  byId('about-record').value = info.record || '';
  byId('about-for').value = info.points_for || '';
  byId('about-against').value = info.points_against || '';
  byId('about-coach').value = info.coach || '';
  byId('about-level').value = info.level || '';
  byId('about-source').value = info.source || '';
  byId('about-links-box').value = (team.links || []).map(function (link) { return link.label + ' | ' + link.url; }).join('\n');
  const numbers = Object.keys(team.roster || {}).sort(function (a, b) { return Number(a) - Number(b); });
  byId('about-roster-box').value = numbers.map(function (number) { return number + ' ' + team.roster[number]; }).join('\n');
  byId('about-found').textContent = team.lat !== null && team.lat !== undefined ? 'On the map at ' + team.lat.toFixed(3) + ', ' + team.lon.toFixed(3) + '.' : 'Not on the map yet.';
  foundPlace = null;
  byId('about-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  byId('about-place').focus();
}

function readLinks() {
  return byId('about-links-box').value.split('\n').map(function (line) { return line.trim(); }).filter(Boolean).map(function (line) {
    const parts = line.split('|');
    if (parts.length >= 2) return { label: parts[0].trim(), url: parts.slice(1).join('|').trim() };
    const url = line;
    let label = 'Link';
    if (/maxpreps/i.test(url)) label = 'MaxPreps';
    else { const host = /^https?:\/\/([^\/]+)/.exec(url); if (host) label = host[1].replace(/^www\./, ''); }
    return { label: label, label_guessed: true, url: url };
  });
}

// "5 Jordan Smith", "#5 Jordan Smith G 5'10\"", "Jordan Smith 5": the number and the words next to it.
function readRoster() {
  const players = {};
  byId('about-roster-box').value.split('\n').forEach(function (line) {
    line = line.replace(/\t/g, ' ').trim();
    if (!line) return;
    let match = /^#?(\d{1,3})\s+([A-Za-z][A-Za-z'.\- ]*?)(?:\s+(?:[A-Z]{1,2}|Fr\.?|So\.?|Jr\.?|Sr\.?|\d+(?:st|nd|rd|th)|\d+'\s*\d*\"?)\b.*)?$/.exec(line);
    if (!match) match = /^([A-Za-z][A-Za-z'.\- ]*?)\s+#?(\d{1,3})\b.*$/.exec(line) && { 1: RegExp.$2, 2: RegExp.$1 };
    if (!match) return;
    players[String(Number(match[1]))] = match[2].trim();
  });
  return players;
}

async function findOnMap() {
  const words = byId('about-place').value.trim();
  if (!words) { toast('Type the town or the school first.', 'bad'); return; }
  byId('about-found').textContent = 'Asking OpenStreetMap...';
  try {
    const reply = await fetch('https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(words), { headers: { Accept: 'application/json' } });
    const rows = await reply.json();
    if (!rows.length) { byId('about-found').textContent = 'Not found. Try the town and the state, like "Wilson, NC".'; return; }
    foundPlace = { lat: Number(rows[0].lat), lon: Number(rows[0].lon) };
    byId('about-found').textContent = 'Found: ' + rows[0].display_name.split(',').slice(0, 3).join(',') + '. Save to keep it.';
  } catch (problem) {
    byId('about-found').textContent = 'OpenStreetMap could not be reached (is the laptop online?).';
  }
}

async function saveAbout(event) {
  event.preventDefault();
  const button = byId('about-save');
  button.disabled = true;
  const body = {
    place: byId('about-place').value.trim(),
    links: readLinks().map(function (link) { return { label: link.label, url: link.url }; }),
    info: { record: byId('about-record').value.trim(), points_for: byId('about-for').value.trim(), points_against: byId('about-against').value.trim(),
            coach: byId('about-coach').value.trim(), level: byId('about-level').value.trim(), source: byId('about-source').value.trim() },
    roster: readRoster(),
  };
  if (foundPlace) { body.lat = foundPlace.lat; body.lon = foundPlace.lon; }
  if (!body.place) { body.lat = null; body.lon = null; }
  const saved = await tryToast(function () { return putJSON('/api/teams/' + encodeURIComponent(teamId), body); });
  button.disabled = false;
  if (!saved) return;
  summary.team = Object.assign(summary.team, saved);
  toast('Saved. ' + (Object.keys(saved.roster).length ? plural(Object.keys(saved.roster).length, 'player') + ' on their roster.' : ''), 'good');
  byId('about-panel').hidden = true;
  showAbout();
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

byId('edit-about').addEventListener('click', openAbout);
byId('about-close').addEventListener('click', function () { byId('about-panel').hidden = true; });
byId('about-find').addEventListener('click', findOnMap);
byId('about-form').addEventListener('submit', saveAbout);
start();
