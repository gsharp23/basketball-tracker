/* The printable scouting report: the Team page's numbers on one sheet, for the browser's print dialog
   ("Save as PDF"). Built from GET /api/teams/<id>/summary with the builders in team_parts.js. */


function section(title, children) {
  return el('section', {}, [el('h2', {}, title)].concat(children));
}

async function start() {
  const teamId = currentTeam();
  const sheet = byId('sheet');
  if (!teamId) {
    sheet.replaceChildren(el('div', { class: 'note' }, ['No team chosen. ', el('a', { href: 'teams.html' }, 'Open the Teams screen'), ' and press "Scouting report" on a team.']));
    return;
  }
  byId('back').href = withTeam('team.html', teamId);
  let summary;
  try {
    summary = await api('/api/teams/' + encodeURIComponent(teamId) + '/summary');
  } catch (problem) {
    sheet.replaceChildren(el('div', { class: 'callout callout-red' }, problem.message));
    return;
  }
  const team = summary.team;
  document.title = team.name + ' scouting report';

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'court');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Where this team scored against us');
  const courtNote = drawTheirShots(svg, summary, null);
  const areas = pointsByArea(summary.shots.marks);

  const today = new Date();
  const made = today.getDate() + ' ' + MONTHS[today.getMonth()] + ' ' + today.getFullYear();
  let school = '';
  try { const brand = await api('/api/branding'); school = [brand.school_name, brand.mascot].filter(Boolean).join(' · '); } catch (problem) { school = ''; }

  sheet.replaceChildren(
    el('div', { class: 'sheet-head' }, [
      el('span', { class: 'head-badge', style: 'background: ' + team.colour, 'aria-hidden': 'true' }, team.short || '?'),
      el('div', { class: 'head-text' }, [
        el('div', { class: 'eyebrow' }, 'Scouting report'),
        el('h1', { class: 'title sheet-title' }, team.name),
        el('div', { class: 'subtitle-small' }, [plural(summary.games_count, 'game') + ' against us · we are ' + summary.record.text +
          ' · ' + summary.possessions_labelled + ' of their possessions labelled' +
          (summary.points_against_per_game !== null ? ' · ' + fmtEach(summary.points_against_per_game) + ' points per game against us' : '')])])]),
    summary.provisional ? el('div', { class: 'callout' }, 'A game against them was marked with stand-in court clicks: positions are provisional.') : null,
    section('What they run on offense', [offenseTable(summary, false), offenseCallout(summary)]),
    section('How they defend us', [defenseTable(summary, false), pressCallout(summary)]),
    section('Where they score', [el('div', { class: 'sheet-two' }, [
      el('div', {}, [svg, el('div', { class: 'hint' }, courtNote)]),
      el('div', { class: 'area-list' }, areas.length
        ? [el('div', { class: 'eyebrow' }, 'Points by area')].concat(areas.map(function (area) {
            return el('div', {}, [el('span', {}, area.area), el('span', { class: 'value' }, plural(area.points, 'point'))]); }))
        : [el('div', { class: 'hint' }, 'No made shot of theirs tagged yet.')])])]),
    section('Their players', [playersTable(summary, null), el('div', { class: 'hint' }, 'By jersey number, over every game against us. Notes are the coach\'s own.')]),
    section('Games against them', [summary.games.length
      ? el('div', { class: 'game-list' }, summary.games.slice().reverse().map(function (row) {
          return el('div', {}, [el('span', { class: 'strong' }, gameWhen(row)), el('span', { class: resultClass(row) }, fmtResult(row))]); }))
      : el('div', { class: 'hint' }, 'No game against them yet.')]),
    el('div', { class: 'sheet-foot' }, [el('span', {}, 'Hoops Tracker' + (school ? ' · ' + school : '')), el('span', {}, 'Made ' + made)]),
  );
}

byId('print').addEventListener('click', function () { window.print(); });
start();
