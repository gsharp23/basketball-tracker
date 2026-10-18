/* One player's page: name and number, the season shot map, where the points come from (paint, mid-range, three),
   game by game lines and the season totals. Data: GET /api/seasons/<id>/players, the same reply the Players page
   reads, filtered to this number (?season=&number=). */

renderHeader('players');

const number = urlParam('number') || '';
let season = null;

function shots(pair) {
  if (!pair || !pair.tried) return '—';
  return fmtMadeTried(pair) + ' · ' + fmtPct(pair.pct);
}

function zone(mark) {
  if (mark.worth === 3) return 'three';
  if (mark.area === 'at the rim' || mark.area === 'paint') return 'paint';
  return 'mid';
}

async function start() {
  season = await currentSeason();
  byId('back').href = withParams('players.html', { season: season.id });
  const page = await api('/api/seasons/' + encodeURIComponent(season.id) + '/players');
  const player = page.players.find(function (p) { return p.number === number; }) || page.unmatched.find(function (p) { return p.number === number; });
  if (!player) { byId('title').textContent = 'No player with number ' + number; byId('subtitle').textContent = 'Pick one on the Players page.'; return; }
  byId('badge').textContent = player.number;
  byId('title').textContent = player.name || 'No. ' + player.number;
  document.title = (player.name || 'No. ' + player.number) + ' · Hoops Tracker';
  const squad = player.squad === 'black' ? 'Middle School Black' : player.squad === 'red' ? 'Middle School Red' : 'Black and Red';
  byId('subtitle').textContent = 'No. ' + player.number + ' · ' + squad + ' · ' + seasonName(page.season.name) + ' · ' + plural(player.games, 'game')
    + ((player.read_as || []).length ? ' · also read as ' + player.read_as.join(', ') : '');
  byId('t-points').textContent = String(player.points);
  byId('t-ppg').textContent = player.points_per_game === null ? '—' : player.points_per_game.toFixed(1);
  byId('t-fg').textContent = shots(player.field_goals);
  byId('t-3').textContent = shots(player.threes);
  byId('t-ft').textContent = shots(player.free_throws);
  byId('t-seen').textContent = player.seen_s ? fmtClock(player.seen_s) : '—';
  byId('t-dist').textContent = player.distance_ft ? fmtMiles(player.distance_ft) : '—';
  const marks = page.marks.filter(function (mark) { return String(mark.number) === String(number); });
  drawHalfCourt(byId('shot-map'), page.court, marks, '#d81e27');
  byId('shots-count').textContent = plural(marks.length, 'shot') + ' tagged';
  byId('shots-note').textContent = marks.length ? undrawnNote(marks) : 'No shot tagged for this player yet: tag shots on a game page.';
  const made = marks.filter(function (m) { return m.made && m.kind === 'field goal'; });
  const totals = { paint: 0, mid: 0, three: 0 }, tries = { paint: 0, mid: 0, three: 0 };
  marks.filter(function (m) { return m.kind === 'field goal'; }).forEach(function (m) { tries[zone(m)] += 1; });
  made.forEach(function (m) { totals[zone(m)] += m.worth; });
  const points = totals.paint + totals.mid + totals.three;
  [['paint', 'z-paint'], ['mid', 'z-mid'], ['three', 'z-three']].forEach(function (z) {
    byId(z[1]).textContent = points ? Math.round(100 * totals[z[0]] / points) + '%' : '—';
    byId(z[1] + '-sub').textContent = plural(totals[z[0]], 'point') + ' · ' + plural(tries[z[0]], 'shot');
  });
  const areas = pointsByArea(marks);
  byId('areas').replaceChildren.apply(byId('areas'), areas.length
    ? areas.map(function (a) { return el('div', {}, [el('span', {}, a.area), el('span', { class: 'value' }, plural(a.points, 'point'))]); })
    : [el('div', { class: 'hint' }, 'No made shot tagged yet.')]);
  const lines = page.lines[number] || [];
  const body = byId('lines');
  body.replaceChildren.apply(body, lines.map(function (line) {
    return el('tr', {}, [
      el('td', {}, fmtDate(line.date)),
      el('td', { class: 'strong' }, el('a', { href: withParams('game.html', { game: line.stem }) }, line.opponent || line.stem)),
      el('td', { class: resultClass(line) }, fmtResult(line)),
      el('td', { class: 'num' }, String(line.points)),
      el('td', {}, shots(line.field_goals)),
      el('td', {}, shots(line.threes)),
      el('td', {}, shots(line.free_throws)),
      el('td', {}, line.seen_s ? fmtClock(line.seen_s) : '—'),
      el('td', {}, line.distance_ft ? fmtFeet(line.distance_ft) : '—')]);
  }));
  if (!lines.length) body.appendChild(el('tr', {}, el('td', { colspan: 9, class: 'muted' }, 'Not seen in a processed game of this season yet.')));
}

start().catch(function (problem) { toast(problem.message, 'bad'); byId('subtitle').textContent = problem.message; });
