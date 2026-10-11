/* What team.html and report.html both draw from GET /api/teams/<id>/summary (src/api_teams.py):
   the "what they run" and "how they defend us" tables, the two callouts, the heat map, their players,
   the games against them. Plain builders; nothing here runs on its own. */

function fmtEach(value) {
  if (value === null || value === undefined) return '—';
  return Number(value).toFixed(1);
}

// The row whose value is strictly the biggest (or smallest) of all, or null when there is a tie or one row.
function standout(rows, key, smallest) {
  if (rows.length < 2) return null;
  let best = null;
  for (const row of rows) {
    if (row[key] === null || row[key] === undefined) continue;
    if (best === null || (smallest ? row[key] < best[key] : row[key] > best[key])) best = row;
  }
  if (!best) return null;
  const tied = rows.some(function (row) { return row !== best && row[key] === best[key]; });
  return tied ? null : best;
}

// A link from a play-type row to the newest game that has possessions of that type (the Game page plays them).
function playlistLink(summary, side, label) {
  const stem = summary.playlists && summary.playlists[side] && summary.playlists[side][label];
  return stem ? withParams('game.html', { game: stem, playlist: label }) : null;
}

function barCell(label, share, href, red) {
  const name = href ? el('a', { href: href }, label) : el('span', { class: 'strong' }, label);
  return el('td', {}, [name, el('div', { class: 'bar-under' + (red ? ' bar-red' : '') }, el('div', { style: 'width: ' + share + '%' }))]);
}

function headRow(words) {
  return el('thead', {}, el('tr', {}, words.map(function (word) { return el('th', {}, word); })));
}

/* ---- what they run on offense ---- */

function offenseTable(summary, links) {
  const rows = summary.offense;
  if (!rows.length) return el('div', { class: 'callout callout-grey' }, 'No possession of theirs is labelled yet. "Label possessions" says what they ran on each one, and this table fills in.');
  const bestPoints = standout(rows, 'points_each'), mostTurnovers = standout(rows, 'turnover_pct');
  const body = rows.map(function (row) {
    return el('tr', {}, [
      barCell(row.label, row.share, links ? playlistLink(summary, 'offense', row.label) : null, false),
      el('td', { class: 'strong' }, row.share + '%'),
      el('td', { class: row === bestPoints ? 'loss' : null }, fmtEach(row.points_each)),
      el('td', {}, fmtPct(row.fg_pct)),
      el('td', { class: row === mostTurnovers && row.turnover_pct > 0 ? 'win' : null }, fmtPct(row.turnover_pct)),
    ]);
  });
  return el('div', { class: 'table-wrap' }, el('table', { class: 'table', style: 'min-width: 420px' }, [
    headRow(['Play type', 'Share', 'Pts / poss', 'FG%', 'Turnovers']), el('tbody', {}, body)]));
}

function offenseCallout(summary) {
  const rows = summary.offense;
  if (!rows.length) return null;
  const best = standout(rows, 'points_each') || rows.slice().sort(function (a, b) { return b.points_each - a.points_each; })[0];
  if (!best.points_each) return el('div', { class: 'callout callout-grey' }, 'They have not scored on a labelled possession yet.');
  let text = 'They score most on the ' + best.label.toLowerCase() + ': ' + fmtEach(best.points_each) + ' points per possession.';
  text += /fast break/i.test(best.label) ? ' Getting back on defense matters more than anything else against them.' : ' That is the play to prepare for.';
  if (best.count < 3) text += ' (Only ' + plural(best.count, 'possession') + ' labelled so far: a first look, not a pattern yet.)';
  return el('div', { class: 'callout callout-red' }, text);
}

/* ---- how they defend us ---- */

function defenseTable(summary, links) {
  const rows = summary.defense;
  if (!rows.length) return el('div', { class: 'callout callout-grey' }, 'None of our possessions against them is labelled yet. "Label possessions" says what defense we faced on each one.');
  const bestPoints = standout(rows, 'points_each'), worstPoints = standout(rows, 'points_each', true), mostTurnovers = standout(rows, 'turnover_pct');
  const body = rows.map(function (row) {
    return el('tr', {}, [
      barCell(row.label, row.share, links ? playlistLink(summary, 'defense', row.label) : null, true),
      el('td', { class: 'strong' }, row.share + '%'),
      el('td', { class: row === bestPoints ? 'win' : row === worstPoints ? 'loss' : null }, fmtEach(row.points_each)),
      el('td', { class: row === mostTurnovers && row.turnover_pct > 0 ? 'loss' : null }, fmtPct(row.turnover_pct)),
    ]);
  });
  return el('div', { class: 'table-wrap' }, el('table', { class: 'table', style: 'min-width: 420px' }, [
    headRow(['Defense', 'Share', 'Our pts / poss', 'Our turnovers']), el('tbody', {}, body)]));
}

function pressCallout(summary) {
  const press = summary.press;
  let text;
  if (!press.baskets) {
    text = 'Nothing is known about when they press yet: no made basket of theirs is followed by a labelled possession of ours.';
  } else if (!press.pressed) {
    text = 'They did not press after any of the ' + plural(press.baskets, 'made basket') + ' we have labelled.';
  } else {
    text = 'They press after ' + press.share + '% of their made baskets' + (press.when ? ', mostly in the ' + press.when : '') + '.';
    if (press.turnover_pct !== null && press.turnover_pct !== undefined) text += ' We turned it over on ' + press.turnover_pct + '% of those possessions.';
  }
  return el('div', { class: 'callout callout-grey' }, text);
}

/* ---- where they score ---- */

// The jersey numbers with tagged shots, most points first: the buttons beside the heat map.
function shooterNumbers(summary) {
  const points = {};
  summary.shots.marks.forEach(function (mark) {
    if (mark.number === null || mark.number === undefined) return;
    points[mark.number] = (points[mark.number] || 0) + (mark.made ? mark.worth : 0);
  });
  return Object.keys(points).sort(function (a, b) { return points[b] - points[a] || Number(a) - Number(b); });
}

// Draw the team's shots (or one number's) on the svg and return the note under it.
function drawTheirShots(svg, summary, number) {
  const marks = number ? summary.shots.marks.filter(function (mark) { return String(mark.number) === String(number); }) : summary.shots.marks;
  drawHalfCourt(svg, summary.shots.court, marks, summary.team.colour);
  return marks.length ? undrawnNote(marks) : 'Tag their shots on a game against them and they appear here.';
}

/* ---- their players ---- */

function playersTable(summary, noteCell) {
  const rows = summary.players;
  if (!rows.length) return el('div', { class: 'callout callout-grey' }, 'No one of theirs is listed yet: their players appear once a game against them is processed or their shots are tagged.');
  const body = rows.map(function (row) {
    return el('tr', {}, [
      el('td', { class: 'num' }, row.number),
      el('td', { class: 'strong' }, fmtEach(row.points_per_game)),
      el('td', {}, fmtMadeTried(row.field_goals)),
      el('td', {}, fmtMadeTried(row.threes)),
      el('td', {}, row.scores_from || '—'),
      noteCell ? noteCell(row) : el('td', { class: 'muted' }, row.note || ''),
    ]);
  });
  return el('div', { class: 'table-wrap' }, el('table', { class: 'table', style: 'min-width: 560px' }, [
    headRow(['No.', 'Pts / game', 'FG', 'Threes', 'Scores most from', 'Note']), el('tbody', {}, body)]));
}

/* ---- the games against them ---- */

function gameWhen(row) {
  const where = row.home === true ? 'Home' : row.home === false ? 'Away' : '';
  const date = fmtDate(row.date) + (row.date_is_guess ? ' (file date)' : '');
  return where ? date + ' · ' + where : date;
}

function gameCard(row) {
  const thumb = el('div', { class: 'thumb' }, row.thumbnail_url ? el('img', { src: row.thumbnail_url, alt: '' }) : null);
  return el('a', { class: 'game-link', href: withParams('game.html', { game: row.stem }) }, [
    thumb,
    el('div', {}, [el('div', { class: 'strong' }, gameWhen(row)), el('div', { class: 'small ' + resultClass(row) }, fmtResult(row))]),
    el('span', { class: 'push strong' }, 'Open →'),
  ]);
}
