/* Hoops Tracker: what every screen shares. Plain functions, no framework.
   The names used by the screens are listed in the contract in src/dash_api.py. */

/* ---- talking to the dashboard ---- */

// Fetch JSON from the dashboard. A problem becomes an Error with a plain message
// (the "detail" the API sent, or what went wrong with the connection).
async function api(path, options) {
  options = options || {};
  const settings = { method: options.method || 'GET', headers: {} };
  if (options.body !== undefined) {
    settings.headers['Content-Type'] = 'application/json';
    settings.body = JSON.stringify(options.body);
  }
  let reply;
  try {
    reply = await fetch(path, settings);
  } catch (problem) {
    throw new Error('Hoops Tracker is not answering. Is the black window still open?');
  }
  let data = null;
  try { data = await reply.json(); } catch (problem) { data = null; }
  if (!reply.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === 'string' ? detail : 'Something went wrong (' + reply.status + ').');
  }
  return data;
}
function getJSON(path) { return api(path); }
function putJSON(path, body) { return api(path, { method: 'PUT', body: body }); }
function postJSON(path, body) { return api(path, { method: 'POST', body: body || {} }); }

// A short message at the bottom of the screen. kind: '' (plain), 'bad' (a problem) or 'good'.
function toast(text, kind) {
  let holder = document.querySelector('.toasts');
  if (!holder) {
    holder = el('div', { class: 'toasts' });
    document.body.appendChild(holder);
  }
  const box = el('div', { class: 'toast' + (kind ? ' toast-' + kind : '') }, text);
  holder.appendChild(box);
  setTimeout(function () { box.remove(); }, kind === 'bad' ? 9000 : 5000);
}

// Run an action and show its error as a toast, so a button never fails silently.
async function tryToast(action) {
  try { return await action(); } catch (problem) { toast(problem.message, 'bad'); return undefined; }
}

/* ---- building the page ---- */

// HTML-escape anything that came from a file or from typing.
function esc(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Make an element: el('a', {href: 'games.html', class: 'btn'}, 'Games') or el('div', {}, [child, 'text']).
function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const name in (attrs || {})) {
    const value = attrs[name];
    if (name === 'html') node.innerHTML = value;
    else if (name.slice(0, 2) === 'on') node.addEventListener(name.slice(2), value);
    else if (value !== null && value !== undefined && value !== false) node.setAttribute(name, value === true ? '' : value);
  }
  const list = Array.isArray(children) ? children : (children === undefined ? [] : [children]);
  for (const child of list) {
    if (child === null || child === undefined) continue;
    node.appendChild(typeof child === 'string' || typeof child === 'number' ? document.createTextNode(child) : child);
  }
  return node;
}

// A file in one of the data folders: fileUrl('outputs', 'test/tracked.mp4') -> '/files/outputs/test/tracked.mp4'.
function fileUrl(folder, path) {
  return '/files/' + folder + '/' + String(path).split('/').map(encodeURIComponent).join('/');
}

/* ---- the current game ---- */

// The title of a game: "vs Riverside" once the opponent is typed, else the video's name.
function gameTitle(info, name) {
  return info && info.opponent ? 'vs ' + info.opponent : name;
}


// The game this screen is about: {video: 'clips/test.mp4', name: 'test'} or null.
// From the URL (?game=test) first; else the one remembered by the dashboard.
async function currentGame() {
  const fromUrl = new URLSearchParams(location.search).get('game');
  if (fromUrl) {
    const reply = await api('/api/games');
    const match = reply.games.find(function (game) { return game.name === fromUrl; });
    if (match) {
      if (reply.chosen !== match.video) putJSON('/api/game/chosen', { video: match.video }).catch(function () {});
      return { video: match.video, name: match.name, game: match };
    }
    toast('There is no game called "' + fromUrl + '" in the clips folder.', 'bad');
  }
  const chosen = await api('/api/game/chosen');
  if (!chosen.video) return null;
  return { video: chosen.video, name: gameStem(chosen.video), game: null };
}

// 'clips/test.mp4' -> 'test'
function gameStem(video) {
  return String(video).split('/').pop().replace(/\.[^.]+$/, '');
}

// A link to another screen that keeps the current game: withGame('shots.html').
function withGame(page, name) {
  name = name || new URLSearchParams(location.search).get('game');
  return name ? page + '?game=' + encodeURIComponent(name) : page;
}

/* ---- the header of every screen but the start page ---- */

const NAV_LINKS = [['games', 'Games', 'games.html'], ['shots', 'Shots', 'shots.html'],
                   ['train', 'Train', 'train.html'], ['settings', 'Settings', 'settings.html']];

// Fill <header id="site-header"> with the logo, the name and the nav; `active` marks the screen.
function renderHeader(active) {
  const header = document.getElementById('site-header');
  if (!header) return;
  const logo = el('span', { class: 'brand-logo', id: 'brand-logo' }, 'LOGO');
  const brand = el('a', { class: 'brand', href: 'index.html' }, [logo, el('span', { class: 'brand-name' }, 'HOOPS TRACKER')]);
  const nav = el('nav', { class: 'nav', 'aria-label': 'Screens' }, NAV_LINKS.map(function (link) {
    return el('a', { href: withGame(link[2]), class: link[0] === active ? 'active' : null }, link[1]);
  }));
  header.replaceChildren(brand, nav);
  api('/api/branding').then(function (info) {
    if (info.logo_url) {
      logo.className = 'brand-logo has-logo';
      logo.replaceChildren(el('img', { src: info.logo_url, alt: 'school logo' }));
    }
  }).catch(function () {});
}

/* ---- the scoring heat map: one half court as the shooter sees it, hoop at the top ---- */

// Draw a half court into an <svg class="heat-court"> and put the shots on it.
// court = {length_ft, width_ft}; marks = [{across, from_baseline (feet, null when unknown), made, worth, kind}]
// (the shape possessions.shot_marks gives); colour = the made-shot colour (red for us, blue for them).
// A soft disc round every made shot builds up where they cluster: that is the "heat".
function drawHalfCourt(court, courtSize, marks, colour) {
  const SVG = 'http://www.w3.org/2000/svg';
  function shape(tag, attrs) {
    const node = document.createElementNS(SVG, tag);
    for (const name in attrs) node.setAttribute(name, attrs[name]);
    court.appendChild(node);
    return node;
  }
  const width = courtSize.width_ft, half = courtSize.length_ft / 2;
  const scale = 480 / width;                       // feet -> picture units; the court is 480 wide inside a 10 margin
  const x = function (across) { return 10 + across * scale; };
  const y = function (fromBaseline) { return 10 + fromBaseline * scale; };
  const bottom = y(half);
  court.setAttribute('viewBox', '0 0 500 ' + Math.round(bottom + 10));
  court.replaceChildren();
  const ink = { fill: 'none', stroke: '#141112', 'stroke-width': 3 };
  const line = function (tag, attrs) { shape(tag, Object.assign({}, ink, attrs)); };
  const hoopY = 5.25, lane = 12, freeThrow = 19, three = 19.75;
  line('rect', { x: 10, y: 10, width: 480, height: bottom - 10 });
  line('rect', { x: x(width / 2 - lane / 2), y: 10, width: lane * scale, height: freeThrow * scale });
  line('circle', { cx: x(width / 2), cy: y(freeThrow), r: 6 * scale });
  // the three-point line: straight beside the hoop, then an arc round it
  const left = x(width / 2 - three), right = x(width / 2 + three), r = three * scale;
  line('path', { d: 'M' + left + ' 10 L' + left + ' ' + y(hoopY) + ' A ' + r + ' ' + r + ' 0 0 0 ' + right + ' ' + y(hoopY) + ' L' + right + ' 10' });
  line('line', { x1: x(width / 2 - 3), y1: y(4), x2: x(width / 2 + 3), y2: y(4), 'stroke-width': 4 });
  line('circle', { cx: x(width / 2), cy: y(hoopY), r: 0.75 * scale + 2, stroke: '#c2410c' });
  const placed = marks.filter(function (mark) { return mark.kind === 'field goal' && mark.across !== null; });
  placed.forEach(function (mark) {
    if (mark.made) shape('circle', { cx: x(mark.across), cy: y(mark.from_baseline), r: 34, fill: colour, opacity: 0.14 });
  });
  // Misses first, then the made shots on top: at one spot the dot shows and the x peeks out behind it.
  placed.filter(function (mark) { return !mark.made; }).forEach(function (mark) {
    const cx = x(mark.across), cy = y(mark.from_baseline);
    shape('path', { d: 'M' + (cx - 9) + ' ' + (cy - 9) + 'l18 18m0-18l-18 18', stroke: '#141112', 'stroke-width': 3, 'stroke-linecap': 'round', fill: 'none' });
  });
  placed.filter(function (mark) { return mark.made; }).forEach(function (mark) {
    shape('circle', { cx: x(mark.across), cy: y(mark.from_baseline), r: mark.worth === 3 ? 10 : 8, fill: colour, stroke: '#ffffff', 'stroke-width': 1.5 });
  });
  if (!placed.length && marks.length) {
    const text = shape('text', { x: 250, y: Math.round(bottom * 0.7), 'text-anchor': 'middle', 'font-size': 18, fill: '#4a4446', 'font-weight': 600 });
    text.textContent = 'No shot with a known position yet';
  }
}

// Points by area from the marks, most first: [{area, points}].
function pointsByArea(marks) {
  const totals = {};
  marks.forEach(function (mark) { if (mark.made) totals[mark.area] = (totals[mark.area] || 0) + mark.worth; });
  return Object.keys(totals).sort(function (a, b) { return totals[b] - totals[a]; })
    .map(function (area) { return { area: area, points: totals[area] }; });
}

// What the court does not show: free throws and shots with no known position.
function undrawnNote(marks) {
  const unknown = marks.filter(function (mark) { return mark.kind === 'field goal' && mark.across === null; }).length;
  const free = marks.filter(function (mark) { return mark.kind === 'free throw'; }).length;
  const notes = [];
  if (unknown) notes.push(unknown + ' shot' + (unknown === 1 ? '' : 's') + ' with position unknown not drawn');
  if (free) notes.push(free + ' free throw' + (free === 1 ? '' : 's') + ' not drawn');
  notes.push('Both ends of the court are shown as one, hoop at the top');
  return notes.join('. ') + '.';
}

/* ---- formatting ---- */

// 1062 -> '17:42'
function fmtClock(seconds) {
  seconds = Math.max(0, Math.round(seconds || 0));
  const minutes = Math.floor(seconds / 60), rest = seconds % 60;
  return minutes + ':' + (rest < 10 ? '0' : '') + rest;
}
// 3120 -> '52 min', 20 -> '20 sec'
function fmtLength(seconds) {
  if (seconds === null || seconds === undefined) return 'length unknown';
  if (seconds < 90) return Math.round(seconds) + ' sec';
  return Math.round(seconds / 60) + ' min';
}
function fmtFeet(feet) {
  if (feet === null || feet === undefined) return '';
  return (feet >= 100 ? Math.round(feet) : Math.round(feet * 10) / 10) + ' ft';
}
function fmtPercent(value) {
  if (value === null || value === undefined) return '';
  return Math.round(value) + '%';
}
// 9123456789 -> '9 GB'
function fmtBytes(bytes) {
  if (bytes === null || bytes === undefined) return '';
  if (bytes >= 1e9) return (bytes / 1e9 < 10 ? (bytes / 1e9).toFixed(1) : Math.round(bytes / 1e9)) + ' GB';
  if (bytes >= 1e6) return Math.round(bytes / 1e6) + ' MB';
  return Math.round(bytes / 1e3) + ' KB';
}

/* ---- watching a background job ---- */

// Ask about a job every `every` ms (default 2000) until it is no longer running.
// onUpdate(info) gets the answer of GET /api/jobs/<name> each time. Returns a function that stops the polling.
function pollJob(name, onUpdate, every) {
  let stopped = false, timer = null;
  async function ask() {
    if (stopped) return;
    try {
      const info = await api('/api/jobs/' + encodeURIComponent(name));
      onUpdate(info);
      if (!info.running && !info.starting) { stopped = true; return; }
    } catch (problem) {
      onUpdate({ name: name, running: false, lines: [], progress: {}, error: problem.message });
      stopped = true;
      return;
    }
    timer = setTimeout(ask, every || 2000);
  }
  ask();
  return function () { stopped = true; clearTimeout(timer); };
}
