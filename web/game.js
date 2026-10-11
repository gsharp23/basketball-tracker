/* The Game screen: one game (?game=<name>) the way a video platform shows it.
   The video with the possession timeline under it, the live court that follows the video, the score
   tiles, the players table, the scoring heat map; below them the processing card and the court set-up.
   Its API is src/api_game.py; the shared pieces (jobs, game info) are in src/dash_api.py. */

renderHeader('games');

let current = null;        // {video, name, game} from currentGame()
let summary = null;        // GET /api/game/<stem>/summary
let brand = {};            // GET /api/branding (the mascot goes in the title)
let table = null;          // GET /api/game/<stem>/positions: the live court's table (see api_game.py for the format)
let watch = { url: '', kind: 'game', scale: 1 };   // which video plays; game seconds = watched seconds * scale
let selected = null;       // the player the court follows: {team, number, tags: [...], words} or null
let filter = '';           // '' | 'made' | 'presses': the timeline filter that is on
let playlist = null;       // {items: [{start, end}], at, words} while clips play one after another
let lastRow = -1;          // the table row drawn last, so a frame is not redrawn for nothing
let stopWatching = null, stopPageWatch = null, wasRunning = false, courtOpened = false;

const OUR_DOT = '#141a24', SELECTED_DOT = '#d81e27', NO_TEAM_DOT = '#8a919c', THEIR_DOT = '#1d5fbf';
const TRAIL_SECONDS = 3;        // how much of the selected player's path is drawn behind him
const SPEED_OVER_SECONDS = 0.5; // "speed now" is measured over this much time
const SHOT_BEFORE_S = 4, SHOT_AFTER_S = 2;   // a shot clip starts this long before the tag and ends after it

function enc(text) { return encodeURIComponent(text); }
function video() { return byId('video'); }
function gameSeconds(watched) { return watched * watch.scale; }
function watchedSeconds(game) { return game / watch.scale; }
function slug(text) { return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); }
function ourTeam() { return summary && summary.teams.our_team; }
function theirTeam() { return summary && summary.teams.their_team; }

/* ================================ start ================================ */

async function setup() {
  current = await currentGame();
  if (!current) {
    byId('game-title').textContent = 'No game chosen';
    byId('game-line').replaceChildren('Choose a game on the ', el('a', { href: 'games.html' }, 'Games'), ' screen first.');
    document.querySelectorAll('main > .two-col, main > .grid-tiles, #court').forEach(function (part) { part.hidden = true; });
    return;
  }
  byId('link-ours').href = withParams('game.html', { game: current.name });
  byId('link-tag').href = withParams('tag.html', { game: current.name });
  byId('heat-tag').href = withParams('tag.html', { game: current.name });
  const [reply, branding] = await Promise.all([
    api('/api/game/' + enc(current.name) + '/summary'),
    api('/api/branding').catch(function () { return {}; })]);
  summary = reply;
  brand = branding || {};
  showAll();
  fillTeamNames();
  loadPositions();
  watchProcessing();
  loadCourt();
  startPlaylistFromUrl();
}

// Everything drawn from the summary (again after a change: the score, our team, a finished run).
function showAll() {
  showHead();
  showVideo();
  showTimeline();
  showTiles();
  showPlayers();
  showHeatPills();
  showHeat(selectedShooter());
}

async function reload() {
  summary = await api('/api/game/' + enc(current.name) + '/summary');
  showAll();
}

/* ================================ the head ================================ */

function showHead() {
  const info = summary.info, row = summary.row;
  const back = byId('back-link');
  if (summary.season) {
    back.textContent = '← ' + summary.season.name + ' season';
    back.href = withParams('season.html', { season: summary.season.id });
  } else {
    back.textContent = '← Games';
    back.href = 'games.html';
  }
  const us = brand.mascot || (summary.season && summary.season.roster.name) || 'Us';
  // The title as the mockup has it: "[MASCOT] 58 · 49 [OPPONENT]" once the score is typed, else "[MASCOT] vs [OPPONENT]";
  // with no opponent yet, the one thing to do is in its place.
  const opponent = summary.opponent
    ? el('a', { href: withParams('team.html', { team: summary.opponent.id }) }, summary.opponent.name)
    : el('a', { href: '#', class: 'name-opponent', onclick: function (event) { event.preventDefault(); openForm('opponent-form', 'opponent-name'); } }, 'Name the opponent');
  const typed = info.score_us !== null && info.score_them !== null;
  byId('game-title').replaceChildren(typed ? us + ' ' + info.score_us + ' · ' + info.score_them + ' ' : us + ' vs ', opponent);
  document.title = (summary.opponent ? 'vs ' + summary.opponent.name : current.name) + ' · Hoops Tracker';

  // One line under the title, with the small links that open the forms.
  const parts = [info.date ? fmtDate(info.date) + (info.date_is_guess ? ' (the file’s date)' : '') : 'Date not set'];
  if (info.home === true) parts.push('Home');
  if (info.home === false) parts.push('Away');
  parts.push(fmtLength(summary.seconds));
  if (info.processed_on) parts.push('processed on ' + (info.processed_on === 'colab' ? 'Colab' : 'the laptop'));
  if (row.points_from === 'tags') parts.push('score from the tagged shots');
  const line = [parts.join(' · '), ' · ',
    el('a', { href: '#', onclick: function (event) { event.preventDefault(); openForm('score-form', 'score-us'); } }, typed ? 'Change the final score' : 'Set final score')];
  const labels = summary.teams.labels;
  if (labels.length >= 2) {
    line.push(' · we wear ' + (ourTeam() || '?') + (summary.teams.guessed ? ' (a guess: check it)' : '') + ' · ');
    line.push(el('a', { href: '#', onclick: function (event) { event.preventDefault(); openForm('our-team-row', 'our-team'); } }, 'change'));
  }
  byId('game-line').replaceChildren.apply(byId('game-line'), line);
  byId('score-us').value = typed ? info.score_us : '';
  byId('score-them').value = typed ? info.score_them : '';
  const select = byId('our-team');
  select.replaceChildren.apply(select, labels.map(function (label) { return el('option', { value: label, selected: label === ourTeam() }, label); }));
  byId('opponent-name').value = info.opponent || '';

  byId('link-opponent').href = summary.opponent ? withParams('team.html', { team: summary.opponent.id }) : 'teams.html';
  byId('provisional').hidden = !summary.stand_in_court;
}

// Show one of the small forms under the title line (the others close) and put the cursor in its first field.
function openForm(id, field) {
  ['score-form', 'our-team-row', 'opponent-form'].forEach(function (other) { byId(other).hidden = other !== id; });
  byId(field).focus();
}

function closeForms() {
  ['score-form', 'our-team-row', 'opponent-form'].forEach(function (id) { byId(id).hidden = true; });
}

async function saveScore(event) {
  event.preventDefault();
  const saved = await tryToast(function () {
    return putJSON('/api/games/' + enc(current.name) + '/info', { score_us: byId('score-us').value, score_them: byId('score-them').value });
  });
  if (!saved) return;
  closeForms();
  toast('Final score saved.', 'good');
  reload().catch(function (problem) { toast(problem.message, 'bad'); });
}

async function chooseOurTeam() {
  const saved = await tryToast(function () {
    return putJSON('/api/games/' + enc(current.name) + '/info', { our_team: byId('our-team').value });
  });
  if (!saved) return;
  closeForms();
  toast('Our team is the ' + saved.our_team + ' team in this game.', 'good');
  selected = null;
  reload().catch(function (problem) { toast(problem.message, 'bad'); });
}

// "Name the opponent": a new name makes a team on the Teams screen, as the Games screen's Edit does.
async function saveOpponent(event) {
  event.preventDefault();
  const name = byId('opponent-name').value.trim();
  if (!name) { toast('Type the opponent\'s name first.', 'bad'); return; }
  const saved = await tryToast(function () { return putJSON('/api/games/' + enc(current.name) + '/info', { opponent: name }); });
  if (!saved) return;
  closeForms();
  toast('Saved: vs ' + saved.opponent + '.', 'good');
  reload().catch(function (problem) { toast(problem.message, 'bad'); });
}

// The team names the opponent box offers (the datalist), from the library.
async function fillTeamNames() {
  const all = await api('/api/games').catch(function () { return null; });
  if (!all) return;
  const names = byId('team-names');
  names.replaceChildren.apply(names, Object.keys(all.teams).map(function (id) { return el('option', { value: all.teams[id].name }); }));
}

/* ================================ the video ================================ */

function showVideo() {
  const player = video();
  watch = summary.watch;
  if (player.getAttribute('src') !== watch.url) {
    player.src = watch.url;
    byId('video-pane').title = watch.kind === 'tracked' ? 'The tool\'s check video (the browser cannot play the game file itself): a tracking number over every person, not a jersey number' : 'The game video';
  }
}

function wireVideo() {
  const player = video(), play = byId('play');
  function showButton() {
    play.innerHTML = player.paused
      ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4l13 8-13 8z"></path></svg>'
      : '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M6 5h4v14H6zM14 5h4v14h-4z"></path></svg>';
    play.setAttribute('aria-label', player.paused ? 'Play' : 'Pause');
  }
  function toggle() { if (player.paused) player.play(); else player.pause(); }
  play.addEventListener('click', toggle);
  player.addEventListener('click', toggle);
  player.addEventListener('play', function () { showButton(); tick(); });
  player.addEventListener('pause', showButton);
  player.addEventListener('timeupdate', follow);
  player.addEventListener('seeked', follow);
  player.addEventListener('loadedmetadata', function () { if (!summary.seconds) showTimeline(); follow(); });
  player.addEventListener('error', function () {
    byId('video-pane').replaceChildren('The browser cannot play this video file.');
  });
  byId('scrub').addEventListener('click', function (event) {
    if (!isFinite(player.duration)) return;
    const box = this.getBoundingClientRect();
    player.currentTime = player.duration * (event.clientX - box.left) / box.width;
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === ' ' && !/^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(document.activeElement.tagName)) { event.preventDefault(); toggle(); }
  });
}

// While the video plays, follow it between the browser's own (slow) time events, so the court moves 10 times a second.
function tick() {
  const player = video();
  if (player.paused || player.ended) return;
  follow();
  requestAnimationFrame(tick);
}

// Everything that moves with the video: the clock, the scrub bar, the current possession, the court, the playlist.
function follow() {
  const player = video(), seconds = gameSeconds(player.currentTime);
  byId('video-clock').textContent = fmtClock(seconds);
  if (isFinite(player.duration)) byId('scrub-played').style.width = (100 * player.currentTime / player.duration) + '%';
  markNow(seconds);
  if (table) {
    const row = rowAt(seconds);
    if (row !== lastRow) { lastRow = row; showRow(row); }
  }
  if (playlist) {
    const item = playlist.items[playlist.at];
    if (seconds >= item.end - 0.05 || player.ended) nextClip();
  }
}

let askedToPressPlay = false;
function seekTo(seconds, andPlay) {
  const player = video();
  player.currentTime = Math.max(0, watchedSeconds(seconds));
  if (!andPlay) return;
  const started = player.play();
  // A page opened with ?playlist= may not be allowed to start the video by itself: the coach presses play.
  if (started && started.catch) started.catch(function () {
    if (!askedToPressPlay) { askedToPressPlay = true; toast('Press play to watch the clips.', ''); }
  });
}

/* ================================ the possession timeline ================================ */

function totalSeconds() {
  const player = video();
  if (summary.seconds) return summary.seconds;
  if (isFinite(player.duration) && player.duration) return gameSeconds(player.duration);
  const last = summary.possessions[summary.possessions.length - 1];
  return last ? last.end_s : 1;
}

function possessionWords(record) {
  const who = record.team === ourTeam() ? 'Our possession' : 'Their possession';
  const what = [record.offense_label, record.defense_label ? 'vs ' + record.defense_label : ''].filter(Boolean).join(' ');
  const got = record.turnover ? 'turnover' : (record.points ? plural(record.points, 'point') : 'no points');
  return who + (what ? ' · ' + what : '') + ' · ' + got + ' · ' + fmtClock(record.start_s) + '–' + fmtClock(record.end_s);
}

function blockClass(record) {
  if (record.turnover) return 'turnover';
  if (record.team === ourTeam()) return 'ours';
  return record.points ? 'theirs-scored' : '';
}

function showTimeline() {
  const strip = byId('timeline'), total = totalSeconds();
  strip.replaceChildren();
  strip.classList.remove('marks');
  const records = summary.possessions;
  if (records.length) {
    let reached = 0;
    records.forEach(function (record) {
      if (record.start_s - reached > 0.5) strip.appendChild(el('span', { class: 'gap', style: 'flex-grow: ' + (record.start_s - reached) }));
      const block = el('button', { type: 'button', class: blockClass(record), style: 'flex-grow: ' + Math.max(0.2, record.end_s - record.start_s),
                                   title: possessionWords(record), 'aria-label': possessionWords(record),
                                   onclick: function () { stopPlaylist(false); seekTo(record.start_s, true); } });
      block.record = record;
      strip.appendChild(block);
      reached = Math.max(reached, record.end_s);
    });
    if (total - reached > 0.5) strip.appendChild(el('span', { class: 'gap', style: 'flex-grow: ' + (total - reached) }));
    byId('timeline-words').textContent = 'Click a block to jump the video and the court to that possession';
    byId('timeline-legend').hidden = false;
    byId('filter-presses').disabled = false;
    byId('filter-presses').title = '';
  } else {
    // No possessions labelled yet: the tagged shots stand in, as marks along the video.
    strip.classList.add('marks');
    const marks = (summary.marks.us || []).concat(summary.marks.them || []);
    marks.forEach(function (mark) {
      const theirs = !(summary.marks.us || []).includes(mark);
      const words = (theirs ? 'Their' : 'Our') + (mark.made ? ' made ' : ' missed ') + (mark.kind === 'free throw' ? 'free throw' : (mark.worth === 3 ? 'three' : 'shot')) +
                    (mark.number ? ' by No. ' + mark.number : '') + ' at ' + fmtClock(mark.time_s);
      const dot = el('button', { type: 'button', class: (theirs ? 'theirs' : '') + (mark.made ? '' : ' missed'), style: 'left: ' + (100 * mark.time_s / total) + '%',
                                 title: words, 'aria-label': words, onclick: function () { stopPlaylist(false); seekTo(mark.time_s - SHOT_BEFORE_S, true); } });
      dot.mark = mark;
      strip.appendChild(dot);
    });
    if (!marks.length) strip.appendChild(el('span', { class: 'empty-words' }, 'Nothing tagged yet'));
    byId('timeline-words').textContent = marks.length
      ? 'No possessions labelled yet, so the marks are the tagged shots (click one to watch it). Label possessions on Tag shots and plays.'
      : 'Tag shots and label possessions on Tag shots and plays; they appear here as a clickable timeline.';
    byId('timeline-legend').hidden = true;
    byId('filter-presses').disabled = true;
    byId('filter-presses').title = 'Presses are possession labels: label possessions first.';
  }
  applyFilter();
}

// Outline the possession (or shot mark) the video is in right now.
function markNow(seconds) {
  document.querySelectorAll('#timeline > button').forEach(function (block) {
    const on = block.record ? (block.record.start_s <= seconds && seconds < block.record.end_s)
                            : (block.mark && Math.abs(block.mark.time_s - seconds) <= SHOT_AFTER_S);
    block.classList.toggle('now', on);
  });
}

/* ================================ filters and playlists ================================ */

// Does a possession (or a shot mark) match a filter word?
function matches(thing, kind) {
  if (!kind) return true;
  if (thing.start_s !== undefined) {           // a possession
    if (kind === 'made') return thing.points > 0;
    if (kind === 'presses') return /press/i.test(thing.defense_label || '');
    if (kind === 'ours') return thing.team === ourTeam();
    if (kind === 'theirs') return thing.team === theirTeam();
    if (kind === 'turnovers') return !!thing.turnover;
    return slug(thing.offense_label) === kind || slug(thing.defense_label) === kind;
  }
  if (kind === 'made') return !!thing.made;     // a shot mark
  return false;
}

function applyFilter() {
  byId('filter-made').classList.toggle('on', filter === 'made');
  byId('filter-presses').classList.toggle('on', filter === 'presses');
  document.querySelectorAll('#timeline > button').forEach(function (block) {
    block.classList.toggle('dim', !!filter && !matches(block.record || block.mark, filter));
  });
}

// A filter button: on -> dim the rest and play the matching clips one after another; off -> stop.
function toggleFilter(kind) {
  if (filter === kind) { filter = ''; applyFilter(); stopPlaylist(true); return; }
  filter = kind;
  applyFilter();
  const words = kind === 'made' ? 'Made shots' : 'Presses';
  startPlaylist(clipsFor(kind), words);
}

// The clips a filter word stands for: possessions when there are any, else the tagged shots.
function clipsFor(kind) {
  if (summary.possessions.length) {
    return summary.possessions.filter(function (record) { return matches(record, kind); })
      .map(function (record) { return { start: record.start_s, end: record.end_s }; });
  }
  return shotClips((summary.marks.us || []).concat(summary.marks.them || []).filter(function (mark) { return matches(mark, kind); }));
}

function shotClips(marks) {
  return marks.slice().sort(function (a, b) { return a.time_s - b.time_s; })
    .map(function (mark) { return { start: Math.max(0, mark.time_s - SHOT_BEFORE_S), end: mark.time_s + SHOT_AFTER_S }; });
}

function startPlaylist(items, words) {
  if (!items.length) { toast('Nothing to play for "' + words + '" in this game.'); return; }
  playlist = { items: items, at: 0, words: words };
  playClip(0);
}

function playClip(index) {
  playlist.at = index;
  const line = byId('playing-line');
  line.hidden = false;
  byId('playing-words').textContent = playlist.words + ' · ' + (index + 1) + ' of ' + playlist.items.length;
  seekTo(playlist.items[index].start, true);
}

function nextClip() {
  if (!playlist) return;
  if (playlist.at + 1 < playlist.items.length) playClip(playlist.at + 1);
  else { video().pause(); stopPlaylist(false); toast('That was the last clip.', ''); }
}

function stopPlaylist(clearFilter) {
  playlist = null;
  byId('playing-line').hidden = true;
  if (clearFilter) { filter = ''; applyFilter(); }
}

// ?playlist=made | presses | ours | theirs | turnovers | <an offense or defense type, like fast-break> | shots-<number>
function startPlaylistFromUrl() {
  const raw = new URLSearchParams(location.search).get('playlist');
  if (!raw) return;
  const shots = /^shots-(\d+)$/.exec(raw);
  if (shots) { playPlayer(shots[1], 'all'); return; }
  const wanted = slug(raw);      // the Team page links with the play type's words ("Fast break"); a typed link may use the slug
  if (wanted === 'made' || wanted === 'presses') { filter = wanted; applyFilter(); }
  const labels = {}.hasOwnProperty.call({ made: 1, presses: 1, ours: 1, theirs: 1, turnovers: 1 }, wanted) ? null : wanted;
  const named = summary.possessions.find(function (record) { return slug(record.offense_label) === wanted || slug(record.defense_label) === wanted; });
  const words = labels ? (named ? (slug(named.offense_label) === wanted ? named.offense_label : named.defense_label) : wanted)
                       : { made: 'Made shots', presses: 'Presses', ours: 'Our possessions', theirs: 'Their possessions', turnovers: 'Turnovers' }[wanted];
  startPlaylist(clipsFor(wanted), words);
}

// Play one of our players' shots: which = 'all' | 'made' | 'field goals' | 'threes'
function playPlayer(number, which) {
  const marks = (summary.marks.us || []).filter(function (mark) {
    if (String(mark.number) !== String(number)) return false;
    if (which === 'made') return mark.made;
    if (which === 'field goals') return mark.kind === 'field goal';
    if (which === 'threes') return mark.kind === 'field goal' && mark.worth === 3;
    return true;
  });
  const words = 'No. ' + number + (which === 'all' ? ': every shot' : ': ' + which);
  startPlaylist(shotClips(marks), words);
}

/* ================================ the live court ================================ */

const SVG = 'http://www.w3.org/2000/svg';
function shape(parent, tag, attrs, text) {
  const node = document.createElementNS(SVG, tag);
  for (const name in attrs) node.setAttribute(name, attrs[name]);
  if (text !== undefined) node.textContent = text;
  parent.appendChild(node);
  return node;
}

// Court feet -> picture units. x runs along the court from the left baseline; y across from the near
// sideline, which is drawn at the bottom (the camera side), so the picture is the court as the camera sees it.
let courtScale = 480 / 84, courtWidth = 50;
function px(x) { return 10 + x * courtScale; }
function py(y) { return 10 + (courtWidth - y) * courtScale; }

// The whole court from court_model.py's numbers: lanes 12 ft wide and 19 ft long, 6 ft circles,
// the three-point line 19.75 ft round a hoop 5.25 ft from the baseline.
function drawFullCourt(court) {
  const svg = byId('live-court');
  const length = court.length_ft, width = court.width_ft;
  courtScale = 480 / length;
  courtWidth = width;
  svg.setAttribute('viewBox', '0 0 500 ' + Math.round(width * courtScale + 20));
  svg.replaceChildren();
  const lines = shape(svg, 'g', { fill: 'none', stroke: '#15171c', 'stroke-width': 2 });
  const line = function (tag, attrs) { return shape(lines, tag, attrs); };
  line('rect', { x: px(0), y: py(width), width: length * courtScale, height: width * courtScale });
  line('line', { x1: px(length / 2), y1: py(0), x2: px(length / 2), y2: py(width) });
  line('circle', { cx: px(length / 2), cy: py(width / 2), r: 6 * courtScale });
  [[0, 1], [length, -1]].forEach(function (end) {
    const baseline = end[0], dir = end[1];
    const freeThrow = baseline + dir * 19, hoop = baseline + dir * 5.25, middle = width / 2;
    line('rect', { x: px(Math.min(baseline, freeThrow)), y: py(middle + 6), width: 19 * courtScale, height: 12 * courtScale });
    line('circle', { cx: px(freeThrow), cy: py(middle), r: 6 * courtScale });
    const r = 19.75 * courtScale, sweep = dir > 0 ? 1 : 0;
    line('path', { d: 'M' + px(baseline) + ' ' + py(middle + 19.75) + ' L' + px(hoop) + ' ' + py(middle + 19.75) +
                      ' A ' + r + ' ' + r + ' 0 0 ' + sweep + ' ' + px(hoop) + ' ' + py(middle - 19.75) + ' L' + px(baseline) + ' ' + py(middle - 19.75) });
    line('circle', { cx: px(hoop), cy: py(middle), r: 0.75 * courtScale + 1.5, stroke: '#c2410c' });
  });
  shape(svg, 'polyline', { id: 'trail', points: '', fill: 'none', stroke: SELECTED_DOT, 'stroke-width': 3, opacity: 0.7 });
  shape(svg, 'g', { id: 'dots' });
}

async function loadPositions() {
  drawFullCourt(summary.court);
  const over = byId('live-over');
  over.hidden = false;
  over.textContent = 'Loading the positions...';
  let reply;
  try {
    reply = await api('/api/game/' + enc(current.name) + '/positions');
  } catch (problem) {
    over.textContent = problem.message;
    return;
  }
  if (!reply.ready) { over.textContent = reply.why; byId('live-note').textContent = 'Not yet'; return; }
  table = reply;
  over.hidden = true;
  drawFullCourt(table.court);
  lastRow = -1;
  follow();
}

// The table row for a moment of the game: frame = seconds * fps, then the last listed frame at or before it
// (a video shows a frame until the next one is due, so this is the picture on the screen).
function rowAt(seconds) {
  const frames = table.frames, frame = seconds * table.fps + 0.5;
  let low = 0, high = frames.length - 1;
  while (low < high) {
    const mid = (low + high + 1) >> 1;
    if (frames[mid] <= frame) low = mid; else high = mid - 1;
  }
  return low;
}

function secondsPerRow() { return table.step * table.stride / table.fps; }
function isSelected(tag) { return selected && selected.tags.indexOf(tag) >= 0; }

// The selected player's position in a row, or null.
function spotIn(row) {
  if (!selected || row < 0 || row >= table.rows.length) return null;
  const found = table.rows[row].find(function (entry) { return isSelected(entry[0]); });
  return found ? [found[1] / 10, found[2] / 10] : null;
}

function dotColour(who, tag) {
  if (isSelected(tag)) return SELECTED_DOT;
  if (!who || !who.team) return NO_TEAM_DOT;
  if (who.team === ourTeam()) return OUR_DOT;
  return (summary.opponent && summary.opponent.colour) || THEIR_DOT;
}

// Draw one moment: a dot per player (team colour, jersey number), the selected player's trail, the panel.
function showRow(row) {
  const dots = byId('dots'), trail = byId('trail');
  if (!dots) return;
  dots.replaceChildren();
  (table.rows[row] || []).forEach(function (entry) {
    const tag = entry[0], x = entry[1] / 10, y = entry[2] / 10, who = table.players[String(tag)] || {};
    const mine = isSelected(tag), colour = dotColour(who, tag);
    const group = shape(dots, 'g', { class: 'dot', 'data-tag': tag });
    group.addEventListener('click', function () { selectTag(tag); });
    shape(group, 'title', {}, who.number ? 'No. ' + who.number + (who.team ? ' (' + who.team + ')' : '') : 'Tag ' + tag + ': no number read');
    shape(group, 'circle', { cx: px(x), cy: py(y), r: mine ? 13 : (who.number ? 11 : 8), fill: colour,
                             stroke: mine ? '#ffffff' : 'none', 'stroke-width': 2 });
    if (who.number) {
      shape(group, 'text', { x: px(x), y: py(y) + 4, 'text-anchor': 'middle', 'font-size': mine ? 12 : 11, 'font-weight': 700,
                             fill: '#ffffff', 'font-family': 'Barlow, sans-serif' }, who.number);
    }
  });
  // the trail: where the selected player was over the last few seconds
  const points = [];
  if (selected) {
    const back = Math.round(TRAIL_SECONDS / secondsPerRow());
    for (let r = Math.max(0, row - back); r <= row; r++) {
      const spot = spotIn(r);
      if (spot) points.push(px(spot[0]) + ',' + py(spot[1]));
    }
  }
  trail.setAttribute('points', points.join(' '));
  showPanel(row);
}

// Click-to-isolate: one dot -> that player (every tag with his team and number), his row, his heat map.
function selectTag(tag) {
  const who = table.players[String(tag)] || {};
  if (who.number && who.team) {
    const tags = Object.keys(table.players).filter(function (key) {
      return table.players[key].team === who.team && table.players[key].number === who.number;
    }).map(Number);
    selected = { team: who.team, number: who.number, tags: tags, words: 'No. ' + who.number };
  } else {
    selected = { team: who.team || null, number: null, tags: [tag], words: 'Tag ' + tag };
  }
  afterSelection();
}

function selectPlayer(team, number) {
  if (!table) { toast('The live court is not ready for this game yet.'); return; }
  const tags = Object.keys(table.players).filter(function (key) {
    return table.players[key].team === team && table.players[key].number === String(number);
  }).map(Number);
  if (!tags.length) { toast('No. ' + number + ' has no position in this game.'); return; }
  selected = { team: team, number: String(number), tags: tags, words: 'No. ' + number };
  afterSelection();
}

function showAllPlayers(event) {
  if (event) event.preventDefault();
  selected = null;
  afterSelection();
}

function afterSelection() {
  if (table) { lastRow = -1; follow(); if (lastRow < 0) showRow(rowAt(gameSeconds(video().currentTime))); }
  highlightRow();
  showHeatPills();
  showHeat(selectedShooter());
}

// The jersey number the heat map should show: the selected player when he is ours and has shots.
function selectedShooter() {
  if (!selected || selected.team !== ourTeam() || !selected.number) return '';
  return (summary.marks.us || []).some(function (mark) { return String(mark.number) === selected.number; }) ? selected.number : '';
}

function ourRow(number) {
  return (summary.players.us || []).concat(summary.players.them || []).find(function (row) { return String(row.number) === String(number); });
}

// The panel under the court: who is followed and his speed now, nearest defender, sprints and distance.
function showPanel(row) {
  const who = byId('live-who'), facts = byId('live-facts');
  byId('live-all').hidden = !selected;
  if (!selected) {
    const here = (table.rows[row] || []).length;
    who.textContent = 'Everyone on the map';
    // A moment with no position at all is a gap in the data (feet below the picture, or no court mapping there), not the page.
    facts.replaceChildren(el('span', {}, here
      ? [plural(here, 'player'), ' with a position at this moment. Click a dot to follow one player.']
      : 'No position for anyone at this moment: a gap in the data (feet out of the picture, or the court not mapped here). The dots come back when the video moves on.'));
    return;
  }
  const name = selected.number && selected.team === ourTeam() ? (ourRow(selected.number) || {}).name : '';
  who.textContent = selected.words + (name ? ' · ' + name : (selected.team && selected.team !== ourTeam() ? ' · opponent' : ''));
  const now = spotIn(row);
  let speed = '—', nearest = '—';
  if (now) {
    const back = Math.max(1, Math.round(SPEED_OVER_SECONDS / secondsPerRow()));
    for (let r = row - 1; r >= Math.max(0, row - back); r--) {
      const before = spotIn(r);
      if (before) {
        const dt = (table.frames[row] - table.frames[r]) / table.fps;
        if (dt > 0) speed = Math.round(Math.hypot(now[0] - before[0], now[1] - before[1]) / dt) + ' ft/s';
        break;
      }
    }
    const others = selected.team === ourTeam() ? theirTeam() : ourTeam();
    let best = null;
    (table.rows[row] || []).forEach(function (entry) {
      const player = table.players[String(entry[0])] || {};
      if (player.team !== others || !others) return;
      const distance = Math.hypot(entry[1] / 10 - now[0], entry[2] / 10 - now[1]);
      if (best === null || distance < best) best = distance;
    });
    if (best !== null) nearest = fmtFeet(best);
  }
  const sprints = selected.number && summary.sprints[selected.team] ? summary.sprints[selected.team][selected.number] : undefined;
  const stats = selected.number ? ourRow(selected.number) : null;
  const fact = function (label, value) { return el('span', {}, [label + ' ', el('b', {}, value)]); };
  facts.replaceChildren(
    now ? null : el('span', {}, 'Not on the court map at this moment.'),
    fact('Speed now', now ? speed : '—'),
    fact(selected.team === ourTeam() ? 'Nearest defender' : 'Nearest of ours', nearest),
    fact('Sprints', sprints === undefined ? '—' : String(sprints)),
    fact('Distance', stats && stats.distance_ft !== null && stats.distance_ft !== undefined ? fmtMiles(stats.distance_ft) : '—'));
}

/* ================================ the score tiles ================================ */

function showTiles() {
  const tiles = summary.tiles.us;
  byId('tile-points').textContent = tiles.points;
  byId('tile-fg-value').textContent = fmtMadeTried(tiles.field_goals);
  byId('tile-threes-value').textContent = fmtMadeTried(tiles.threes);
  byId('tile-ft-value').textContent = fmtMadeTried(tiles.free_throws);
  const turnovers = summary.possessions.filter(function (record) { return record.team === ourTeam() && record.turnover; });
  byId('tile-to-value').textContent = summary.possessions.length ? String(turnovers.length) : '—';
  byId('tile-to').title = summary.possessions.length ? 'Watch our turnovers' : 'Turnovers come from the labelled possessions: label them on Tag shots and plays';
}

function ourShots(which) {
  return (summary.marks.us || []).filter(function (mark) {
    if (which === 'field goals') return mark.kind === 'field goal';
    if (which === 'threes') return mark.kind === 'field goal' && mark.worth === 3;
    if (which === 'free throws') return mark.kind === 'free throw';
    return true;
  });
}

function wireTiles() {
  byId('tile-fg').addEventListener('click', function (event) { event.preventDefault(); startPlaylist(shotClips(ourShots('field goals')), 'Our field goals'); });
  byId('tile-threes').addEventListener('click', function (event) { event.preventDefault(); startPlaylist(shotClips(ourShots('threes')), 'Our threes'); });
  byId('tile-ft').addEventListener('click', function (event) { event.preventDefault(); startPlaylist(shotClips(ourShots('free throws')), 'Our free throws'); });
  byId('tile-to').addEventListener('click', function (event) {
    event.preventDefault();
    const items = summary.possessions.filter(function (record) { return record.team === ourTeam() && record.turnover; })
      .map(function (record) { return { start: record.start_s, end: record.end_s }; });
    startPlaylist(items, 'Our turnovers');
  });
}

/* ================================ the players table ================================ */

function showPlayers() {
  const body = byId('players'), empty = byId('players-empty'), notes = [];
  body.replaceChildren();
  const rows = summary.players.us || [];
  byId('players-wrap').hidden = !rows.length;
  empty.hidden = !!rows.length;
  if (!rows.length) {
    empty.replaceChildren(el('div', {}, summary.has_stats
      ? (ourTeam() ? 'No player of the ' + ourTeam() + ' team got a jersey number in this game.' : 'The teams of this game are not known yet.')
      : 'The players table appears when the game has been processed.'));
    byId('players-note').textContent = '';
    return;
  }
  rows.forEach(function (player) {
    const sprints = summary.sprints[ourTeam()] ? summary.sprints[ourTeam()][player.number] : undefined;
    const link = function (words, which, title) {
      return el('a', { href: '#', title: title, onclick: function (event) { event.preventDefault(); event.stopPropagation(); playPlayer(player.number, which); } }, words);
    };
    const tr = el('tr', { class: 'pick', 'data-number': player.number, title: 'Click the row to follow this player on the court',
                          onclick: function () { selectPlayer(ourTeam(), player.number); } }, [
      el('td', { class: 'num' }, link(player.number, 'all', 'Watch every shot of No. ' + player.number)),
      el('td', { class: 'strong' }, player.name || '—'),
      el('td', {}, link(String(player.points), 'made', 'Watch the made shots')),
      el('td', {}, link(fmtMadeTried(player.field_goals), 'field goals', 'Watch the field goals')),
      el('td', {}, link(fmtMadeTried(player.threes), 'threes', 'Watch the threes')),
      el('td', {}, player.seen_s === null || player.seen_s === undefined ? '—' : fmtClock(player.seen_s)),
      el('td', {}, sprints === undefined ? '—' : String(sprints))]);
    body.appendChild(tr);
  });
  highlightRow();
  if (rows.every(function (player) { return !player.name; })) notes.push('Names come from the season’s roster (Settings).');
  if (summary.players_without_number.us) {
    notes.push(plural(summary.players_without_number.us, 'player') + ' of this team could not be given a number and ' +
               (summary.players_without_number.us === 1 ? 'is' : 'are') + ' not listed.');
  }
  if (!summary.has_stats) notes.push('On camera and sprints come at the last step of processing.');
  notes.push('Points and shots come from the shots you tagged. A sprint is a run faster than 12 ft/s for at least a second while the player had a position.');
  if (summary.stand_in_court) notes.push('Sprints are provisional: the court was marked with stand-in clicks.');
  byId('players-note').textContent = notes.join(' ');
}

function highlightRow() {
  document.querySelectorAll('#players tr').forEach(function (tr) {
    tr.classList.toggle('hilite', !!selected && selected.team === ourTeam() && tr.getAttribute('data-number') === selected.number);
  });
}

/* ================================ the scoring heat map ================================ */

function shooters() {
  const numbers = {};
  (summary.marks.us || []).forEach(function (mark) { if (mark.number !== null && mark.number !== undefined) numbers[mark.number] = true; });
  return Object.keys(numbers).sort(function (a, b) { return Number(a) - Number(b); });
}

function showHeatPills() {
  const pills = byId('heat-pills'), chosen = selectedShooter();
  pills.replaceChildren(el('button', { class: 'pill' + (chosen ? '' : ' on'), type: 'button', 'data-player': '', onclick: pickHeat }, 'Team'));
  shooters().forEach(function (number) {
    pills.appendChild(el('button', { class: 'pill' + (chosen === number ? ' on' : ''), type: 'button', 'data-player': number, onclick: pickHeat }, 'No. ' + number));
  });
}

function pickHeat() {
  const chosen = this;
  document.querySelectorAll('#heat-pills .pill').forEach(function (pill) { pill.classList.toggle('on', pill === chosen); });
  showHeat(chosen.getAttribute('data-player'));
}

function showHeat(player) {
  const marks = (summary.marks.us || []).filter(function (mark) { return !player || String(mark.number) === String(player); });
  const empty = !(summary.marks.us || []).length;
  byId('heat-empty').hidden = !empty;
  byId('heat-pills').hidden = empty;
  byId('heat-legend').hidden = empty;
  byId('heat-note').hidden = empty;
  byId('heat-areas').hidden = empty;
  drawHalfCourt(byId('heat-court'), summary.court, marks, '#d81e27');
  const areas = byId('heat-areas');
  areas.replaceChildren.apply(areas, pointsByArea(marks).map(function (row) {
    return el('div', {}, [el('span', {}, row.area), el('span', { class: 'value' }, plural(row.points, 'pt'))]);
  }));
  byId('heat-note').textContent = empty ? '' : undrawnNote(marks) + (summary.stand_in_court ? ' Positions are provisional (stand-in court clicks).' : '');
}

/* ================================ processing (the run of main.py) ================================ */

const STEP_WORDS = { detect: 'Finding the players', court: 'Mapping the court', teams: 'Sorting the players into teams',
                     jerseys: 'Reading the jersey numbers', tactical: 'Drawing the court view', stats: 'Working out the stats' };

function watchProcessing() {
  if (stopWatching) stopWatching();
  stopWatching = pollJob('run_' + current.name, function (info) { showProcessing(info, summary.game.status); }, 2000);
}

function showProcessing(info, status) {
  const card = byId('processing'), progress = info.progress || {};
  const running = info.running || info.starting;
  const broke = info.exit_code !== null && info.exit_code !== undefined && info.exit_code !== 0;
  // A finished game that is not running hides the card: the results speak for themselves.
  card.hidden = !running && status === 'finished' && !broke;
  syncCourtButtons();
  const words = { 'finished': ['chip-green', 'Finished'], 'running': ['chip-blue', 'Running here'],
                  'needs the court marked': ['chip-red', 'Needs the court marked'], 'not started': ['chip-grey', 'Not started'],
                  'stopped part-way': ['chip-dark', 'Stopped part-way'] };
  const which = running ? words.running : (words[status] || ['chip-grey', '']);
  byId('processing-chip').className = 'chip ' + which[0];
  byId('processing-chip').textContent = which[1];
  byId('processing-progress').hidden = !running;
  byId('process-stop').hidden = !running;
  byId('process-start').hidden = running;
  byId('process-start').textContent = status === 'stopped part-way' ? 'Carry on' : 'Process this game';
  const text = byId('processing-text');
  if (running) {
    const step = progress.step ? (STEP_WORDS[progress.step] || progress.step) : 'Starting up';
    const percent = progress.how_far === null || progress.how_far === undefined ? null : Math.round(100 * progress.how_far);
    byId('processing-step').textContent = step;
    byId('processing-left').textContent = progress.left || '';
    byId('processing-bar').style.width = (percent === null ? 3 : Math.max(3, percent)) + '%';
    byId('processing-detail').textContent = (progress.step_count ? 'Step ' + progress.step_number + ' of ' + progress.step_count : 'Getting ready: loading the programs (about half a minute)') +
      (percent === null ? '' : ' · ' + percent + '%') + (progress.detail ? ' · ' + progress.detail : '');
    text.textContent = 'The results appear here when every step is done. You can close this page; the processing goes on while the black Hoops Tracker window is open.';
  } else if (progress.ended === 'court') {
    text.textContent = 'Stopped at the court step: the players, teams and jersey numbers are done. Mark the court below, then press "Process this game" to carry on.';
  } else if (progress.ended === 'stopped') {
    text.textContent = 'Stopped part-way. What was finished is kept: press "Carry on" to continue from there.';
  } else if (summary.game.roster_changed) {
    text.textContent = 'The roster changed after the stats were made. "Carry on" redoes only the stats, with the new names, in seconds.';
  } else if (progress.ended === 'finished') {
    text.textContent = 'Every step is done.' + (status === 'finished' ? '' : ' (The files have changed since, so some steps would be done again.)');
    if (status === 'finished') card.hidden = true;
  } else if (broke && info.lines.length) {
    text.textContent = 'The last run broke off: ' + info.lines[info.lines.length - 1];
    byId('processing-log').hidden = false;
  } else {
    text.textContent = status === 'not started'
      ? 'Finds the players, sorts them into teams, reads the jersey numbers, maps the court and works out the stats. On this laptop this game takes ' + summary.estimate + '.'
      : 'Press the button to run the steps that are not done yet.';
  }
  const flags = byId('processing-flags');
  flags.hidden = !(info.flagged && info.flagged.length);
  flags.textContent = (info.flagged || []).join(' ');
  byId('processing-log').textContent = (info.lines || []).join('\n') || '(nothing printed yet)';
  if (info.error) text.textContent = info.error;
  if (!running && wasRunning) {
    // The run just ended: show the new results and the game's new status.
    wasRunning = false;
    reload().then(function () { watchProcessing(); loadPositions(); loadCourt(); }).catch(function (problem) { toast(problem.message, 'bad'); });
  }
  if (running) wasRunning = true;
}

let courtHint = '';

// The court panel's buttons: "Process this game" only when the processing card above does not offer it already;
// "Run on Colab" for a game that is on Colab (its court files go there and Colab finishes the positions and stats).
function syncCourtButtons() {
  const above = !byId('processing').hidden;
  const onColab = !!(summary && summary.info.processed_on === 'colab');
  byId('court-process').hidden = above;
  byId('court-colab').hidden = !onColab;
  byId('court-hint').textContent = (above ? 'Then press the button in the card above. ' : courtHint + ' ')
    + (onColab ? 'Run on Colab sends the court files to Colab, which finishes the positions and stats.' : '');
}

async function sendToColab(button) {
  if (!window.confirm('Send this game\'s court files to Google Drive for Colab? Colab finishes the positions and stats the next time its notebook looks.')) return;
  button.disabled = true;
  const reply = await tryToast(function () { return postJSON('/api/drive/send/' + enc(current.name)); });
  button.disabled = false;
  if (reply) toast(reply.message || 'Sent.', 'good');
}

async function startProcessing(button) {
  if (!window.confirm('Process this game on this laptop? It takes ' + summary.estimate + '. Keep the laptop plugged in and awake, and the black Hoops Tracker window open. You can stop at any time; nothing finished is lost.')) return;
  button.disabled = true;
  const started = await tryToast(function () { return postJSON('/api/jobs/start', { kind: 'run', video: current.video }); });
  button.disabled = false;
  if (started) { toast('Started.', 'good'); await reload(); watchProcessing(); }
}

async function stopProcessing(button) {
  if (!window.confirm('Stop the processing? What is finished is kept, and "Carry on" continues later.')) return;
  button.disabled = true;
  button.textContent = 'Stopping...';
  await tryToast(function () { return postJSON('/api/jobs/run_' + enc(current.name) + '/stop'); });
  button.disabled = false;
  button.textContent = 'Stop';
  await reload();
  watchProcessing();
}

/* ================================ court set-up (the three click pages) ================================ */

async function loadCourt() {
  let court;
  try {
    court = await api('/api/game/' + enc(current.name) + '/court');
  } catch (problem) {
    byId('court-chip').textContent = problem.message;
    return;
  }
  const chip = byId('court-chip');
  chip.className = 'chip ' + (court.ready ? (court.stand_in_court ? 'chip-red' : 'chip-green') : 'chip-grey');
  chip.textContent = court.ready ? (court.stand_in_court ? 'Stand-in clicks' : 'Marked') : 'Not marked yet';
  if (court.open_page) courtOpened = true;
  byId('court-folded').hidden = !court.ready || courtOpened;
  byId('court-body').hidden = court.ready && !courtOpened;
  byId('court-folded-words').textContent = court.stand_in_court
    ? 'Court marked with stand-in clicks: every position is provisional until you do step 2 yourself.'
    : 'Court marked. Every position-based number uses it.';
  byId('court-provisional').hidden = !court.stand_in_court;
  const holder = byId('court-steps');
  holder.replaceChildren();
  court.steps.forEach(function (step) { holder.appendChild(courtStep(step, court)); });
  courtHint = court.ready
    ? 'Runs the steps that use the court (mapping, positions, court view, stats).'
    : 'Do steps 1 and 2 first. Processing started now does the long steps that need no court, then waits for it.';
  syncCourtButtons();
  byId('court-check').hidden = !court.court_check_url;
  byId('court-check-picture').dataset.src = court.court_check_url || '';
  if (court.open_page) watchClickPage(court.open_page.kind);
}

function courtStep(step, court) {
  const side = el('div', { class: 'step-side' });
  const open = court.open_page && court.open_page.kind === step.kind;
  const button = el('button', { class: 'btn ' + (step.done ? 'btn-outline' : 'btn-red'), type: 'button', id: 'step-' + step.kind,
                                onclick: function () { startClickPage(step, this); } }, step.done ? 'Do it again' : 'Open step ' + step.number);
  button.disabled = !!court.open_page;
  side.appendChild(button);
  side.appendChild(el('div', { class: 'small muted', id: 'step-word-' + step.kind },
    open ? 'This page is open now.' : (step.done ? 'Done' : (step.number === 3 ? 'Optional' : 'To do'))));
  if (open) side.appendChild(el('button', { class: 'btn btn-light close-page', type: 'button', onclick: function () { closeClickPage(this); } }, 'Close without saving'));
  const text = el('div', { class: 'step-text' }, el('div', { class: 'step-title' }, step.title));
  if (!step.done) text.appendChild(el('div', { class: 'hint' }, step.guidance));
  if (step.picture_url) text.appendChild(el('a', { class: 'small', href: step.picture_url, target: '_blank', rel: 'noopener' }, 'See the check picture'));
  return el('div', { class: 'court-step' }, [
    el('div', { class: 'step-number' + (step.done ? ' done' : '') }, step.done ? '✓' : String(step.number)), text, side]);
}

async function startClickPage(step, button) {
  button.disabled = true;
  const started = await tryToast(function () { return postJSON('/api/jobs/start', { kind: step.kind, video: current.video }); });
  if (!started) { button.disabled = false; return; }
  byId('step-word-' + step.kind).textContent = 'Starting the page (about 10 seconds)...';
  // The browser allows a new tab only straight after a click: open it now, and send it to the page's address once known.
  const tab = window.open('', '_blank');
  if (tab) tab.document.write('<title>Hoops Tracker</title><p style="font-family: sans-serif; font-size: 18px; padding: 24px">Starting the page, about 10 seconds...</p>');
  watchClickPage(step.kind, tab);
}

async function closeClickPage(button) {
  if (!window.confirm('Close the page without saving? Clicks made on it are lost.')) return;
  button.disabled = true;
  button.textContent = 'Closing...';
  await tryToast(function () { return postJSON('/api/jobs/click_page/stop'); });
}

function watchClickPage(kind, tab) {
  if (stopPageWatch) stopPageWatch();
  let sent = false;
  document.querySelectorAll('#court-steps button:not(.close-page)').forEach(function (button) { button.disabled = true; });
  stopPageWatch = pollJob('click_page', function (info) {
    const word = byId('step-word-' + kind);
    if (info.address && !sent) {
      sent = true;
      if (tab) tab.location = info.address;
    }
    if (info.address && word && !word.querySelector('a')) word.replaceChildren('Open: ', el('a', { href: info.address, target: '_blank', rel: 'noopener' }, info.address));
    if (!info.running && !info.starting) {
      if (tab && !sent) tab.close();
      const last = (info.lines || []).slice(-1)[0] || '';
      if (info.error) toast(info.error, 'bad');
      else if (info.exit_code === 0) toast('The page was closed. Saved.', 'good');
      else if (/without saving|cancelled/i.test(last)) toast('The page was closed without saving. Nothing was changed.', '');
      else toast('The page closed: ' + (last || 'see the step'), 'bad');
      loadCourt();
      reload().then(watchProcessing).catch(function () {});
    }
  }, 1500);
}

/* ================================ wiring ================================ */

wireVideo();
wireTiles();
byId('filter-made').addEventListener('click', function () { toggleFilter('made'); });
byId('filter-presses').addEventListener('click', function () { toggleFilter('presses'); });
byId('playing-stop').addEventListener('click', function () { video().pause(); stopPlaylist(true); });
byId('live-all').addEventListener('click', showAllPlayers);
byId('score-cancel').addEventListener('click', closeForms);
byId('score-form').addEventListener('submit', saveScore);
byId('our-team').addEventListener('change', chooseOurTeam);
byId('our-team-cancel').addEventListener('click', closeForms);
byId('opponent-form').addEventListener('submit', saveOpponent);
byId('opponent-cancel').addEventListener('click', closeForms);
byId('process-start').addEventListener('click', function () { startProcessing(this); });
byId('court-process').addEventListener('click', function () { startProcessing(this); });
byId('court-colab').addEventListener('click', function () { sendToColab(this); });
byId('process-stop').addEventListener('click', function () { stopProcessing(this); });
byId('log-toggle').addEventListener('click', function () {
  const log = byId('processing-log');
  log.hidden = !log.hidden;
  this.textContent = log.hidden ? 'Details' : 'Hide the details';
});
byId('court-unfold').addEventListener('click', function () { courtOpened = true; loadCourt(); });
byId('court-check-toggle').addEventListener('click', function () {
  const holder = byId('court-check-holder'), picture = byId('court-check-picture');
  holder.hidden = !holder.hidden;
  if (!holder.hidden && picture.dataset.src && !picture.src) picture.src = picture.dataset.src;
  this.textContent = holder.hidden ? 'Show the court check picture' : 'Hide the court check picture';
});

setup().catch(function (problem) { toast(problem.message, 'bad'); byId('game-line').textContent = problem.message; });
