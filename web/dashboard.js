/* The Dashboard: the first screen of Processing & training. Four tiles, the score-by-version chart, and the two
   "continue" cards (the game to process next, the detector work to do next). Data: GET /api/process/overview. */

renderSidebar('dashboard');

function setText(id, text) { byId(id).textContent = text; }

function blockLine(node, block) {
  node.textContent = block.text;
  node.className = 'line ' + (block.ok ? 'text-green' : 'text-orange');
}

// One dot per version, a hollow one for a version with no score (or not built yet), on a 0 to 1 scale.
function drawChart(chart) {
  const SVG = 'http://www.w3.org/2000/svg';
  const svg = byId('score-chart');
  svg.setAttribute('viewBox', '0 0 640 220');
  svg.replaceChildren();
  function add(tag, attrs, text) {
    const node = document.createElementNS(SVG, tag);
    for (const name in attrs) node.setAttribute(name, attrs[name]);
    if (text !== undefined) node.textContent = text;
    svg.appendChild(node);
    return node;
  }
  const versions = chart.versions;
  const left = 50, right = 610, top = 20, bottom = 160;
  const y = function (score) { return bottom - (bottom - top) * score; };
  const x = function (i) { return versions.length === 1 ? (left + right) / 2 : left + (right - left) * i / (versions.length - 1); };
  [0, 0.5, 1].forEach(function (level) {
    add('line', { x1: left, y1: y(level), x2: right, y2: y(level), stroke: '#e3e5e9' });
    add('text', { x: left - 8, y: y(level) + 4, 'text-anchor': 'end', 'font-size': 11, fill: '#5a5f6a', 'font-family': 'Barlow, sans-serif' }, level.toFixed(1));
  });
  const scored = versions.filter(function (v) { return v.score !== null && v.score !== undefined; });
  if (scored.length > 1) {
    add('polyline', { points: scored.map(function (v) { return x(versions.indexOf(v)) + ',' + y(v.score); }).join(' '), fill: 'none', stroke: '#15171c', 'stroke-width': 2.5 });
  }
  versions.forEach(function (v, i) {
    const has = v.score !== null && v.score !== undefined;
    if (has) {
      add('circle', { cx: x(i), cy: y(v.score), r: 7, fill: '#ffd21f', stroke: '#15171c', 'stroke-width': 2 });
      add('text', { x: x(i), y: y(v.score) - 14, 'text-anchor': 'middle', 'font-size': 12, 'font-weight': 700, fill: '#15171c', 'font-family': 'Barlow, sans-serif' }, v.score.toFixed(2));
    } else {
      add('circle', { cx: x(i), cy: y(0.5), r: 7, fill: '#ffffff', stroke: '#8a8f99', 'stroke-width': 2, 'stroke-dasharray': '3 3' });
      add('text', { x: x(i), y: y(0.5) - 14, 'text-anchor': 'middle', 'font-size': 11, fill: '#5a5f6a', 'font-family': 'Barlow, sans-serif' }, v.not_built ? 'not built' : 'Not scored yet');
    }
    add('text', { x: x(i), y: bottom + 22, 'text-anchor': 'middle', 'font-size': 13, 'font-weight': 700, fill: '#15171c', 'font-family': 'Barlow, sans-serif' }, v.label);
    const under = v.not_built ? 'next' : [v.built, v.pictures !== null ? plural(v.pictures, 'picture') : ''].filter(Boolean).join(' · ');
    add('text', { x: x(i), y: bottom + 40, 'text-anchor': 'middle', 'font-size': 11, fill: '#5a5f6a', 'font-family': 'Barlow, sans-serif' }, under);
  });
}

function drawBars(chart) {
  const holder = byId('bars'), tip = byId('bars-tip');
  holder.replaceChildren();
  const per = chart.per_class;
  byId('bars-title').textContent = 'Score by class · ' + (chart.in_use.label || chart.in_use.name);
  if (!per) {
    chart.classes.forEach(function (name) {
      holder.appendChild(el('div', { class: 'bar-row' }, [el('span', {}, name), el('div', { class: 'bar' }, el('span', { style: 'width: 0' })), el('span', { class: 'muted' }, '—')]));
    });
    tip.textContent = 'Not scored yet. Scores arrive with "Score this version".';
    return;
  }
  let weakest = null;
  chart.classes.forEach(function (name) {
    const score = per[name];
    if (weakest === null || (score !== undefined && score < per[weakest])) weakest = name;
    holder.appendChild(el('div', { class: 'bar-row' }, [el('span', {}, name),
      el('div', { class: 'bar' }, el('span', { style: 'width: ' + Math.round(100 * (score || 0)) + '%' })),
      el('span', { class: 'strong' }, score === undefined ? '—' : score.toFixed(2))]));
  });
  tip.textContent = weakest ? 'Weakest: ' + weakest + '. More pictures with a clear ' + weakest.toLowerCase() + ' help most.' : '';
}

async function load() {
  const data = await api('/api/process/overview');
  setText('subtitle', 'Where the detector stands and what to do next. Season ' + seasonName(data.season) + '.');
  setText('tile-detector', data.tiles.detector.value); setText('tile-detector-line', data.tiles.detector.line);
  setText('tile-games', data.tiles.games.value); setText('tile-games-line', data.tiles.games.line);
  setText('tile-pictures', data.tiles.pictures.value); setText('tile-pictures-line', data.tiles.pictures.line);
  setText('tile-versions', data.tiles.versions.value); setText('tile-versions-line', data.tiles.versions.line);
  drawChart(data.chart);
  drawBars(data.chart);
  const p = data.process;
  setText('process-title', p.title); setText('process-status', p.status); blockLine(byId('process-block'), p.block);
  byId('process-button').textContent = p.button; byId('process-button').href = p.href; setText('process-small', p.small);
  const m = data.improve;
  setText('improve-status', m.status); setText('improve-step', m.step_line); blockLine(byId('improve-block'), m.block);
  byId('improve-button').textContent = m.button; byId('improve-button').href = m.href; setText('improve-small', m.small);
  setText('footer', data.footer);
}

load().catch(function (problem) {
  toast(problem.message, 'bad');
  byId('process-title').textContent = problem.message;
});
