/* One squad (Middle School Black or Red): its schedule for the chosen season and its roster.
   Data: GET /api/seasons (the roster and the dropdown) and GET /api/seasons/<id>/summary (the schedule rows, each with
   its squad). A game with no squad shows for both squads. */

renderHeader('teams');

const squad = urlParam('squad') === 'red' ? 'red' : 'black';
const NAMES = { black: 'Middle School Black', red: 'Middle School Red' };
let season = null;

function resultCell(row) {
  if (row.result) return el('td', { class: resultClass(row) }, fmtResult(row));
  if (row.points_from === 'tags') return el('td', { class: 'muted' }, fmtResult(row));
  return el('td', { class: 'muted' }, row.date >= new Date().toISOString().slice(0, 10) ? 'Upcoming' : '—');
}

function fmtTime(text) {
  const match = /^(\d{2}):(\d{2})$/.exec(text || '');
  if (!match) return 'TBA';
  const hours = Number(match[1]);
  return (hours % 12 || 12) + ':' + match[2] + (hours >= 12 ? ' pm' : ' am');
}

function showSchedule(summary) {
  const rows = summary.schedule.filter(function (row) { return !row.squad || row.squad === squad; });
  const body = byId('schedule-rows');
  body.replaceChildren.apply(body, rows.map(function (row) {
    const opponent = row.opponent_id ? el('a', { href: withParams('team.html', { team: row.opponent_id }) }, row.opponent) : el('span', {}, row.opponent || 'no opponent yet');
    const extra = [];
    if (row.kind === 'tournament') extra.push(el('span', { class: 'chip chip-grey chip-sm' }, 'Tournament'));
    if (!row.squad) extra.push(el('span', { class: 'small muted' }, ' (squad not set)'));
    const link = row.stem && row.status === 'finished' ? el('a', { href: withParams('game.html', { game: row.stem }) }, 'Open stats') : el('span', { class: 'muted small' }, row.stem ? 'video not processed' : '');
    return el('tr', { title: row.note || null }, [
      el('td', {}, [fmtDate(row.date), el('span', { class: 'small muted' }, ' ' + fmtTime(row.time))]),
      el('td', { class: 'strong' }, [opponent].concat(extra)),
      el('td', {}, row.home === true ? 'Home' : row.home === false ? 'Away' : ''),
      resultCell(row),
      el('td', {}, link)]);
  }));
  const wins = rows.filter(function (r) { return r.result === 'W'; }).length, losses = rows.filter(function (r) { return r.result === 'L'; }).length;
  byId('schedule-note').textContent = plural(rows.length, 'game') + ' · ' + wins + '–' + losses;
  if (!rows.length) body.appendChild(el('tr', {}, el('td', { colspan: 5, class: 'muted' }, 'No games for this squad in ' + seasonName(season.name) + ' yet.')));
}

function showRoster() {
  const players = season.roster.players || {}, squads = season.roster.squads || {};
  const numbers = Object.keys(players).filter(function (n) { return (squads[n] || 'both') === squad || (squads[n] || 'both') === 'both'; })
    .sort(function (a, b) { return Number(a) - Number(b); });
  const body = byId('roster-rows');
  body.replaceChildren.apply(body, numbers.map(function (n) {
    const team = squads[n] || 'both';
    return el('tr', {}, [el('td', { class: 'strong' }, n), el('td', {}, players[n] || el('span', { class: 'muted' }, 'no name')),
                         el('td', {}, team === 'both' ? 'Both' : team === 'red' ? 'Red' : 'Black')]);
  }));
  byId('roster-note').textContent = plural(numbers.length, 'player');
  if (!numbers.length) body.appendChild(el('tr', {}, el('td', { colspan: 3, class: 'muted' }, 'No roster for ' + seasonName(season.name) + ' yet: fill it on the Season page.')));
}

async function start() {
  byId('badge').textContent = squad === 'red' ? 'R' : 'B';
  byId('badge').className = 'squad-badge ' + squad;
  byId('title').textContent = NAMES[squad];
  document.title = NAMES[squad] + ' · Hoops Tracker';
  for (const link of byId('squad-switch').children) link.classList.toggle('on', link.dataset.squad === squad);
  const reply = await api('/api/seasons');
  const wanted = urlParam('season') || remembered('season') || reply.current;
  season = reply.seasons.find(function (s) { return s.id === wanted; }) || reply.seasons.find(function (s) { return s.id === reply.current; });
  const pick = byId('season-pick');
  pick.replaceChildren.apply(pick, reply.seasons.map(function (s) { return el('option', { value: s.id }, seasonName(s.name) + (s.current ? ' · current' : '')); }));
  pick.value = season.id;
  pick.addEventListener('change', function () { rememberSeason(pick.value); location.href = withParams('squad.html', { squad: squad, season: pick.value }); });
  byId('subtitle').textContent = seasonName(season.name) + ' · ' + (season.roster.name || 'our team');
  const summary = await api('/api/seasons/' + encodeURIComponent(season.id) + '/summary');
  showSchedule(summary);
  showRoster();
}

start().catch(function (problem) { toast(problem.message, 'bad'); byId('subtitle').textContent = problem.message; });
