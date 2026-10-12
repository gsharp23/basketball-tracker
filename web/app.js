/* Hoops Tracker v3: what every screen shares. Plain functions, no framework.
   The names used by the screens are listed in the contract in src/dash_api.py. */

/* ---- talking to the dashboard ---- */

// Fetch JSON from the dashboard. A problem becomes an Error with a plain message
// (the "detail" the API sent, or what went wrong with the connection).
async function api(path, options) {
  options = options || {};
  // X-Hoops says "this comes from the dashboard's own pages": the server changes nothing without it, and no
  // other website's page can add it (see own_pages_only in src/dashboard.py).
  const settings = { method: options.method || 'GET', headers: { 'X-Hoops': '1' } };
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
function deleteJSON(path) { return api(path, { method: 'DELETE' }); }

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

function byId(id) { return document.getElementById(id); }

// "2026-27" is shown as "2026–27", as the design writes it.
function seasonName(name) { return String(name || '').replace(/(\d)-(\d)/, '$1–$2'); }

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

// An inline stroke icon (no image files in the repo). name: 'arrow', 'plus', 'back', 'play', 'gear'.
function icon(name, size, colour) {
  const paths = {
    arrow: '<path d="M5 12h14"></path><path d="M13 6l6 6-6 6"></path>',
    plus: '<path d="M12 5v14"></path><path d="M5 12h14"></path>',
    back: '<path d="M19 12H5"></path><path d="M11 18l-6-6 6-6"></path>',
    play: '<path d="M7 4l13 8-13 8z" fill="currentColor" stroke="none"></path>',
    gear: '<circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"></path>',
  };
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', size || 20);
  svg.setAttribute('height', size || 20);
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', colour || 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = paths[name] || '';
  return svg;
}

// A file in one of the data folders: fileUrl('outputs', 'test/tracked.mp4') -> '/files/outputs/test/tracked.mp4'.
function fileUrl(folder, path) {
  return '/files/' + folder + '/' + String(path).split('/').map(encodeURIComponent).join('/');
}

/* ---- the current game, season and team ---- */

function urlParam(name) { return new URLSearchParams(location.search).get(name); }

function remembered(key) {
  try { return localStorage.getItem('hoops.' + key); } catch (problem) { return null; }
}
function remember(key, value) {
  try { if (value) localStorage.setItem('hoops.' + key, value); else localStorage.removeItem('hoops.' + key); } catch (problem) { /* a private window */ }
}
function rememberSeason(id) { remember('season', id); }
function rememberTeam(id) { remember('team', id); }

// 'clips/test.mp4' -> 'test'
function gameStem(video) {
  return String(video).split('/').pop().replace(/\.[^.]+$/, '');
}

// The game this screen is about: {video: 'clips/test.mp4', name: 'test', game: <row of /api/games or null>} or null.
// From the URL (?game=test) first; else the one the dashboard remembers.
async function currentGame() {
  const fromUrl = urlParam('game');
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

// The season this screen is about: a row of /api/seasons (with "id"), from ?season=, else the one
// remembered in this browser, else the current season. "unassigned" is allowed and gives {id: 'unassigned', name: 'Unassigned'}.
async function currentSeason() {
  const reply = await api('/api/seasons');
  const wanted = urlParam('season') || remembered('season') || reply.current;
  if (wanted === 'unassigned') { rememberSeason(wanted); return { id: 'unassigned', name: 'Unassigned', current: false, roster: { name: '', players: {} }, games: reply.unassigned }; }
  let match = reply.seasons.find(function (season) { return season.id === wanted; });
  if (!match) {
    if (urlParam('season')) toast('There is no season "' + wanted + '". Showing the current one.', 'bad');
    match = reply.seasons.find(function (season) { return season.id === reply.current; });
  }
  rememberSeason(match.id);
  return match;
}

// The team id this screen is about, from ?team= (remembered), else the remembered one, else null.
function currentTeam() {
  const fromUrl = urlParam('team');
  if (fromUrl) rememberTeam(fromUrl);
  return fromUrl || remembered('team');
}

// A link to another screen carrying ids: withParams('game.html', {game: 'test', season: '2026-27'}).
function withParams(page, params) {
  const query = new URLSearchParams();
  for (const name in (params || {})) if (params[name]) query.set(name, params[name]);
  const text = query.toString();
  return text ? page + '?' + text : page;
}
function withGame(page, name) { return withParams(page, { game: name || urlParam('game') }); }
function withSeason(page, id) { return withParams(page, { season: id || urlParam('season') || remembered('season') }); }
function withTeam(page, id) { return withParams(page, { team: id || urlParam('team') }); }

/* ---- the brand block, shared by the header and the sidebar ---- */

function brandBlock(areaWords) {
  const logo = el('span', { class: 'brand-logo' }, 'LOGO');
  const words = el('span', { class: 'brand-words' }, [el('span', { class: 'brand-name' }, 'HOOPS TRACKER'), el('span', { class: 'brand-area' }, areaWords)]);
  api('/api/branding').then(function (info) {
    if (info.logo_url) {
      logo.className = 'brand-logo has-logo';
      logo.replaceChildren(el('img', { src: info.logo_url, alt: 'school logo' }));
    }
  }).catch(function () {});
  return el('a', { class: 'brand', href: 'index.html' }, [logo, words]);
}

/* ---- Game analysis: the header ---- */

const NAV_LINKS = [['seasons', 'Season', 'season.html'], ['teams', 'Teams', 'teams.html'], ['players', 'Players', 'players.html']];

// Fill <header id="site-header"> with the logo, the nav, the search box and "← Start page".
// `active` marks the screen ('seasons' | 'teams' | 'games' | 'players' | ''). options.search(text) answers typing.
function renderHeader(active, options) {
  const header = document.getElementById('site-header');
  if (!header) return;
  options = options || {};
  const nav = el('nav', { class: 'nav', 'aria-label': 'Screens' }, NAV_LINKS.map(function (link) {
    return el('a', { href: link[2], class: link[0] === active ? 'active' : null }, link[1]);
  }));
  const search = el('input', { id: 'site-search', class: 'search', type: 'search', placeholder: 'Search games, teams, players' });
  search.addEventListener('input', function () { if (options.search) options.search(search.value.trim()); });
  search.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !options.search) location.href = withParams('season.html', {});
  });
  const side = el('div', { class: 'header-side' }, [
    el('label', { for: 'site-search', class: 'visually-hidden' }, 'Search'), search,
    el('a', { href: 'index.html', class: 'start-link' }, '← Start page')]);
  header.replaceChildren(brandBlock('GAME ANALYSIS'), nav, side);
}

/* ---- Processing and training: the sidebar (the Games screen first, then the five training steps) ---- */

const SIDEBAR_LINKS = [['games', 'Games · process', 'games.html'], ['overview', 'Training overview', 'train.html'], ['upload', '1 · Upload', 'upload.html'],
                       ['annotate', '2 · Annotate', 'annotate.html'], ['dataset', '3 · Dataset', 'dataset.html'],
                       ['train', '4 · Train', 'models.html#train'], ['models', '5 · Models', 'models.html']];

// Fill <aside id="side-bar"> with the logo, the five steps and "← Start page". `active` marks the step.
function renderSidebar(active) {
  const aside = document.getElementById('side-bar');
  if (!aside) return;
  const links = SIDEBAR_LINKS.map(function (link) {
    return el('a', { href: link[2], class: 'step-link' + (link[0] === active ? ' active' : '') }, link[1]);
  });
  aside.replaceChildren(brandBlock('PROCESSING & TRAINING'), ...links, el('div', { class: 'grow' }),
                        el('a', { href: 'index.html', class: 'start-link' }, '← Start page'));
}

/* ---- the court drawings: one half court as the shooter sees it, hoop at the top ---- */

// Draw a half court into an <svg class="court"> and put the shots on it.
// court = the svg; courtSize = {length_ft, width_ft}; marks = [{across, from_baseline (feet, null when unknown),
// made, worth, kind}] as library.marks_of gives them; colour = the made-shot colour (red for us, blue for them).
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
  const ink = { fill: 'none', stroke: '#15171c', 'stroke-width': 2 };
  const line = function (tag, attrs) { shape(tag, Object.assign({}, ink, attrs)); };
  // This court's markings (court_model.Court on the Python side); the usual ones when the game does not say.
  const hoopY = 5.25, lane = courtSize.lane_ft || 12, freeThrow = courtSize.free_throw_ft || 19, three = courtSize.three_point_ft || 19.75;
  line('rect', { x: 10, y: 10, width: 480, height: bottom - 10 });
  line('rect', { x: x(width / 2 - lane / 2), y: 10, width: lane * scale, height: freeThrow * scale });
  line('circle', { cx: x(width / 2), cy: y(freeThrow), r: 6 * scale });
  // the three-point line: straight beside the hoop, then an arc round it
  const left = x(width / 2 - three), right = x(width / 2 + three), r = three * scale;
  line('path', { d: 'M' + left + ' 10 L' + left + ' ' + y(hoopY) + ' A ' + r + ' ' + r + ' 0 0 0 ' + right + ' ' + y(hoopY) + ' L' + right + ' 10' });
  line('line', { x1: x(width / 2 - 3), y1: y(4), x2: x(width / 2 + 3), y2: y(4), 'stroke-width': 3 });
  line('circle', { cx: x(width / 2), cy: y(hoopY), r: 0.75 * scale + 2, stroke: '#c2410c' });
  const placed = (marks || []).filter(function (mark) { return mark.kind === 'field goal' && mark.across !== null && mark.across !== undefined; });
  placed.forEach(function (mark) {
    if (mark.made) shape('circle', { cx: x(mark.across), cy: y(mark.from_baseline), r: 34, fill: colour, opacity: 0.14 });
  });
  // Misses first, then the made shots on top: at one spot the dot shows and the x peeks out behind it.
  placed.filter(function (mark) { return !mark.made; }).forEach(function (mark) {
    const cx = x(mark.across), cy = y(mark.from_baseline);
    shape('path', { d: 'M' + (cx - 6) + ' ' + (cy - 6) + 'l12 12m0-12l-12 12', stroke: '#15171c', 'stroke-width': 2.5, 'stroke-linecap': 'round', fill: 'none' });
  });
  placed.filter(function (mark) { return mark.made; }).forEach(function (mark) {
    shape('circle', { cx: x(mark.across), cy: y(mark.from_baseline), r: mark.worth === 3 ? 8 : 7, fill: colour, stroke: '#ffffff', 'stroke-width': 1.5 });
  });
  if (!placed.length) {
    const text = shape('text', { x: 250, y: Math.round(bottom * 0.7), 'text-anchor': 'middle', 'font-size': 18, fill: '#5a5f6a', 'font-weight': 600, 'font-family': 'Barlow, sans-serif' });
    text.textContent = (marks || []).length ? 'No shot with a known position yet' : 'No shots tagged yet';
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
  if (unknown) notes.push(plural(unknown, 'shot') + ' with position unknown not drawn');
  if (free) notes.push(plural(free, 'free throw') + ' not drawn');
  notes.push('Both ends of the court are shown as one, hoop at the top');
  return notes.join('. ') + '.';
}

/* ---- formatting ---- */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

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
// '2026-01-24' -> '24 Jan 2026'; anything else is given back as it is.
function fmtDate(text) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text || '');
  if (!match) return text || '';
  return Number(match[3]) + ' ' + MONTHS[Number(match[2]) - 1] + ' ' + match[1];
}
// 58, 49 -> '58–49'; '' when either is missing
function fmtScore(us, them) {
  if (us === null || us === undefined || them === null || them === undefined) return '';
  return us + '–' + them;
}
// A game row (library.game_row): 'W 58–49', 'L 51–57', or '58–49 (tagged)' or '—' when nothing is known.
function fmtResult(row) {
  if (row.result) return row.result + ' ' + fmtScore(row.score_us, row.score_them);
  if (row.points_from === 'tags') return fmtScore(row.points_us, row.points_them) + ' (tagged)';
  return '—';
}
function resultClass(row) { return row.result === 'W' ? 'win' : row.result === 'L' ? 'loss' : 'muted'; }
// {made: 5, tried: 11} -> '5/11'
function fmtMadeTried(pair) {
  if (!pair) return '—';
  return pair.made + '/' + pair.tried;
}
function fmtPct(value) {
  if (value === null || value === undefined) return '—';
  return Math.round(value) + '%';
}
function fmtFeet(feet) {
  if (feet === null || feet === undefined) return '';
  return (feet >= 100 ? Math.round(feet) : Math.round(feet * 10) / 10) + ' ft';
}
// 7920 -> '1.5 mi', 300 -> '300 ft'
function fmtMiles(feet) {
  if (feet === null || feet === undefined) return '';
  return feet >= 528 ? (feet / 5280).toFixed(1) + ' mi' : Math.round(feet) + ' ft';
}
// 9123456789 -> '9 GB'
function fmtBytes(bytes) {
  if (bytes === null || bytes === undefined) return '';
  if (bytes >= 1e9) return (bytes / 1e9 < 10 ? (bytes / 1e9).toFixed(1) : Math.round(bytes / 1e9)) + ' GB';
  if (bytes >= 1e6) return Math.round(bytes / 1e6) + ' MB';
  return Math.round(bytes / 1e3) + ' KB';
}
// plural(3, 'game') -> '3 games', plural(1, 'shot') -> '1 shot'
function plural(count, word, words) {
  return count + ' ' + (count === 1 ? word : (words || word + 's'));
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

// Ask a URL every `every` ms until `until(reply)` is true (a job that runs inside the dashboard). Returns a stop function.
function pollUntil(path, onUpdate, until, every) {
  let stopped = false, timer = null;
  async function ask() {
    if (stopped) return;
    try {
      const reply = await api(path);
      onUpdate(reply);
      if (until(reply)) { stopped = true; return; }
    } catch (problem) {
      toast(problem.message, 'bad');
      stopped = true;
      return;
    }
    timer = setTimeout(ask, every || 1000);
  }
  ask();
  return function () { stopped = true; clearTimeout(timer); };
}
