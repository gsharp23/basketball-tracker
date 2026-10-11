/* The "Tag shots and plays" screen: one video, two modes.
   Shots: pause at a shot, click the shooter in the picture, say made or missed. The boxes are drawn here in the
   browser from one small JSON answer per pause, so a step is quick (src/shots.py keeps data/<game>/shots.json).
   Plays: mark where each possession starts and ends, who had the ball, what they ran and what it came to
   (src/possessions.py keeps data/<game>/possessions.json). Talks to src/api_tag.py. */

renderHeader('games');


const state = {
  game: null,          // {video, name, game} from currentGame()
  setup: null,         // GET /api/tag/<game>/setup: teams, court, which video is watched, the play names
  mode: 'shots',       // 'shots' or 'plays'
  moment: null,        // the last /boxes answer (the paused moment)
  chosen: null,        // the shooter: a boxed player, or {typed: true, team, number}
  kind: null,          // '2', '3' or 'ft'
  kindByHand: false,   // true once the coach chose the kind himself (the position no longer decides)
  period: '1',
  shots: [],
  openShot: null,      // id of the shot whose editor is open in the list
  possessions: [],
  editing: null,       // the possession being changed, or null for a new one
  outcomeByHand: false,
  sureDelete: null,    // id of the possession whose Delete was pressed once
};

const video = byId('video');

function base() { return '/api/tag/' + encodeURIComponent(state.game.name); }
function watching() { return '?watching=' + encodeURIComponent(state.setup.watch.kind); }

/* ---- start ---- */

async function start() {
  state.mode = urlParam('mode') === 'plays' ? 'plays' : 'shots';
  state.game = await currentGame();
  if (!state.game) {
    showNotReady('Choose a game first, on the Games screen.');
    return;
  }
  byId('back-link').href = withGame('game.html', state.game.name);
  byId('heat-link').href = withGame('game.html', state.game.name);
  try {
    state.setup = await api(base() + '/setup');
  } catch (problem) {
    showNotReady(problem.message);
    return;
  }
  showTitle();
  showPeriods();
  showTeamChoice();
  fillPlayTypes();
  if (state.setup.stand_in_court) {
    byId('help').textContent = 'Pause at a shot, click the shooter in the picture, then choose made or missed. ' +
      'The court of this game was marked with stand-in clicks, so distances and 2-or-3 are provisional: check them.';
  }
  video.src = state.setup.watch.url;
  showMode();
  showShooter();
  await Promise.all([loadShots(), loadPossessions()]);
}

function showNotReady(text) {
  byId('not-ready-text').textContent = text;
  byId('not-ready-link').href = withGame('games.html', state.game && state.game.name);
  byId('not-ready').hidden = false;
  byId('work').hidden = true;
  byId('mode-tabs').hidden = true;
  byId('period-row').hidden = true;
  byId('subtitle').textContent = '';
}

// The game in words for the back link and the title row: "vs Opponent" when known, else the file name.
function gameTitle() {
  const info = state.setup.info;
  return info.opponent ? 'vs ' + info.opponent : state.game.name;
}

function showTitle() {
  const setup = state.setup;
  byId('back-link').textContent = '← ' + gameTitle();
  document.title = 'Tag shots and plays · ' + gameTitle() + ' · Hoops Tracker';
  const line = byId('subtitle');
  const bits = [];
  if (setup.info.date) bits.push((setup.info.date_is_guess ? 'Added ' : '') + fmtDate(setup.info.date));
  if (setup.watch.kind === 'tracked') bits.push('showing the tracked video (10 pictures a second): the browser cannot play the original file');
  line.replaceChildren(bits.join(' · ') + (bits.length ? ' · ' : ''));
  if (setup.our_team) {
    line.append('We are the ' + setup.our_team + ' team' + (setup.guessed ? ' (a guess: check it)' : '') + ' · ',
      el('a', { href: '#', onclick: function (event) { event.preventDefault(); swapTeams(); } }, 'Swap teams'));
  }
}

// The coach says the other colour group is us: saved in the game's info, then everything is read again.
async function swapTeams() {
  const other = state.setup.their_team;
  if (!other) return;
  const done = await tryToast(function () { return putJSON('/api/games/' + encodeURIComponent(state.game.name) + '/info', { our_team: other }); });
  if (!done) return;
  toast('Saved: we are the ' + other + ' team.', 'good');
  state.setup = await tryToast(function () { return api(base() + '/setup'); });
  if (!state.setup) return;
  showTitle();
  showTeamChoice();
  showShooter();
  drawBoxes();
  await Promise.all([loadShots(), loadPossessions()]);
}

/* ---- the two modes ---- */

function showMode() {
  const plays = state.mode === 'plays';
  for (const button of byId('mode-tabs').children) button.className = button.dataset.mode === state.mode ? 'on' : '';
  byId('shots-side').hidden = plays;
  byId('plays-side').hidden = !plays;
  byId('period-row').hidden = plays;
  byId('strip-wrap').hidden = !plays;
  byId('help').hidden = plays;
  history.replaceState(null, '', withParams('tag.html', { game: state.game.name, mode: plays ? 'plays' : null }));
  if (plays) clearBoxes(); else if (video.paused) fetchBoxes();
  if (plays) drawStrip();
}

byId('mode-tabs').addEventListener('click', function (event) {
  const button = event.target.closest('button');
  if (!button || !state.setup) return;
  state.mode = button.dataset.mode;
  showMode();
});

function showPeriods() {
  const holder = byId('periods');
  holder.replaceChildren();
  for (const period of state.setup.periods) {
    holder.appendChild(el('button', { type: 'button', class: period === state.period ? 'on' : null, onclick: function () {
      state.period = period;
      showPeriods();
    } }, period));
  }
}

/* ---- who is who ---- */

function teamOf(label) { return state.setup.teams.find(function (each) { return each.label === label; }); }
// "Us" or the opponent's name; the colour label in brackets, because that is what the tool calls the group.
function teamWords(label) {
  const team = teamOf(label);
  return team ? team.name + ' (' + label + ')' : label;
}
function teamDot(label) {
  const team = teamOf(label);
  return el('span', { class: 'team-dot', style: 'background:' + (team ? team.colour : '#888'), title: label });
}

function showTeamChoice() {
  const holder = byId('typed-team');
  holder.replaceChildren();
  for (const team of state.setup.teams) {
    const on = state.chosen && state.chosen.typed && state.chosen.team === team.label;
    holder.appendChild(el('button', { type: 'button', class: on ? 'on' : null, onclick: function () { pickTyped(team.label); } },
                          [teamDot(team.label), team.name]));
  }
}

/* ---- the video and its transport ---- */

function updateClock() {
  const where = video.duration ? 100 * video.currentTime / video.duration : 0;
  byId('scrub-fill').style.width = where + '%';
  byId('scrub').setAttribute('aria-valuenow', Math.round(where));
  byId('clock').textContent = fmtClock(video.currentTime);
  const now = byId('strip').querySelector('.now');
  if (now) now.style.left = where + '%';
}

function showPlaying(playing) {
  byId('play-icon').hidden = playing;
  byId('pause-icon').hidden = !playing;
  byId('play').setAttribute('aria-label', playing ? 'Pause' : 'Play');
}

function jump(seconds) {
  video.currentTime = Math.min(Math.max(0, video.currentTime + seconds), video.duration || 0);
}

function seekTo(seconds) {
  video.pause();
  video.currentTime = Math.max(0, seconds);
}

// play() gives a promise that fails when paused again at once; that is not a problem worth a message
function togglePlay() { if (video.paused) video.play().catch(function () {}); else video.pause(); }

video.addEventListener('loadedmetadata', function () {
  byId('stage-note').hidden = true;
  updateClock();
  drawStrip();
  fetchBoxes();
});
video.addEventListener('error', function () {
  // MediaError code 2 is a reading problem (the disk), 3 and 4 mean the browser cannot play the file
  const code = video.error ? video.error.code : 0;
  byId('stage-note').hidden = false;
  byId('stage-note').textContent = code === 2
    ? 'The video could not be read. Check that the file is still in the clips folder, then reload this page.'
    : 'The browser cannot play this video. Process the game on the Games screen: the tracked video it makes can always be played.';
});
video.addEventListener('timeupdate', updateClock);
video.addEventListener('play', function () { showPlaying(true); clearBoxes(); });
video.addEventListener('pause', function () { showPlaying(false); fetchBoxes(); });
video.addEventListener('seeked', function () { if (video.paused) fetchBoxes(); });

byId('play').addEventListener('click', togglePlay);
byId('back5').addEventListener('click', function () { jump(-5); });
byId('fwd5').addEventListener('click', function () { jump(5); });
byId('scrub').addEventListener('click', function (event) {
  const bar = byId('scrub').getBoundingClientRect();
  if (video.duration) seekTo(video.duration * (event.clientX - bar.left) / bar.width);
});
byId('scrub').addEventListener('keydown', function (event) {
  if (event.key === 'ArrowLeft') jump(-5);
  if (event.key === 'ArrowRight') jump(5);
});
// Keys: Space plays and pauses (as in any player); in Plays mode S marks the start and E the end of a possession.
// Nothing happens while the coach is typing in a box, and nothing is saved by a key alone.
document.addEventListener('keydown', function (event) {
  const tag = event.target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  if (event.key === ' ' && tag !== 'BUTTON') { event.preventDefault(); togglePlay(); }
  if (state.mode !== 'plays' || !state.setup) return;
  if (event.key === 's' || event.key === 'S') markTime('start');
  if (event.key === 'e' || event.key === 'E') markTime('end');
});
window.addEventListener('resize', drawBoxes);

/* ---- the player boxes over the paused picture (Shots mode) ---- */

let boxesAsked = 0;     // so a late answer for an earlier moment is not drawn over a newer one

function clearBoxes() {
  state.moment = null;
  byId('boxes').replaceChildren();
}

async function fetchBoxes() {
  if (!state.setup || !video.duration || state.mode === 'plays') return;     // boxes are for picking a shooter
  const asked = ++boxesAsked;
  const query = watching() + '&t=' + video.currentTime.toFixed(3);
  try {
    const moment = await api(base() + '/boxes' + query);
    if (asked !== boxesAsked || !video.paused) return;
    state.moment = moment;
    drawBoxes();
    showShooter();
  } catch (problem) {
    toast(problem.message, 'bad');
  }
}

// Where the picture sits inside the stage (a 4:3 video would have bars at the sides), and how many
// screen pixels one pixel of the game video is. The boxes are measured in the game video's pictures
// (setup.picture); the tracked video can be a smaller copy of the same picture.
function pictureRect() {
  const stage = byId('stage').getBoundingClientRect();
  const picture = state.setup.picture;
  const scale = Math.min(stage.width / picture.width, stage.height / picture.height);
  const width = picture.width * scale, height = picture.height * scale;
  return { left: (stage.width - width) / 2, top: (stage.height - height) / 2, scale: scale, stageWidth: stage.width, stageHeight: stage.height };
}

function drawBoxes() {
  const holder = byId('boxes');
  holder.replaceChildren();
  if (!state.moment || !state.setup) return;
  const rect = pictureRect();
  for (const player of state.moment.players) {
    const chosen = state.chosen && state.chosen.tag === player.tag;
    const label = player.number !== null ? player.number : '?';
    holder.appendChild(el('button', {
      type: 'button', class: 'pbox' + (chosen ? ' chosen' : ''), title: shooterWords(player),
      style: 'left:' + (100 * (rect.left + player.box[0] * rect.scale) / rect.stageWidth) + '%;' +
             'top:' + (100 * (rect.top + player.box[1] * rect.scale) / rect.stageHeight) + '%;' +
             'width:' + (100 * (player.box[2] - player.box[0]) * rect.scale / rect.stageWidth) + '%;' +
             'height:' + (100 * (player.box[3] - player.box[1]) * rect.scale / rect.stageHeight) + '%',
      onclick: function () { pick(player); },
    }, el('span', { class: 'tagline' }, label)));
  }
}

/* ---- the shooter card ---- */

function shooterWords(player) {
  const team = teamOf(player.team);
  const who = team ? team.name : 'Team not sorted';          // the tool could not tell which colour this player wears
  if (player.number === null) return who + ', number not read';
  return 'No. ' + player.number + (player.name ? ' · ' + player.name : ' · ' + who);
}

function pick(player) {
  state.chosen = player;
  state.kindByHand = false;
  state.kind = player.worth ? String(player.worth) : null;
  byId('typed-number').value = '';
  showTeamChoice();
  showShooter();
  drawBoxes();
  if (player.number === null) {
    // The number was not read: the coach types it; the team is already his unless the tool could not sort him either.
    byId('shooter-where').textContent = player.team ? 'Type the jersey number below (the team is chosen).'
                                                    : 'Choose the team and type the jersey number below.';
    byId('typed-number').focus();
  }
}

function pickTyped(team) {
  const number = byId('typed-number').value.trim();
  const before = state.chosen;
  // A boxed player whose number was not read keeps his box: only the number is typed, the position still comes from the box.
  const boxed = before && before.tag !== null && before.number === null && (!team || !before.team || team === before.team) ? before : null;
  const typedBefore = before && before.typed ? before : null;
  state.chosen = { typed: true, team: team || (boxed ? boxed.team : typedBefore ? typedBefore.team : null), number: number,
                   tag: boxed ? boxed.tag : (typedBefore ? typedBefore.tag : null),
                   worth: boxed ? boxed.worth : (typedBefore ? typedBefore.worth : null),
                   where: boxed ? boxed.where : (typedBefore ? typedBefore.where : null),
                   near_line: boxed ? boxed.near_line : (typedBefore ? typedBefore.near_line : false) };
  if (!typedBefore) {
    state.kindByHand = false;
    state.kind = boxed && boxed.worth ? String(boxed.worth) : null;
  }
  showTeamChoice();
  showShooter();
  drawBoxes();
}

byId('typed-number').addEventListener('input', function () { pickTyped(state.chosen ? state.chosen.team : null); });

function showShooter() {
  const chosen = state.chosen, tile = byId('shooter-number');
  if (!chosen) {
    tile.className = 'shooter-number empty';
    tile.textContent = '?';
    byId('shooter-name').textContent = 'Click the shooter in the picture';
    byId('shooter-where').textContent = video.paused && state.moment ? 'Or type the team and number below' : 'Pause the video at the shot first';
  } else {
    tile.className = 'shooter-number';
    tile.textContent = chosen.number !== null && chosen.number !== '' ? chosen.number : '?';
    if (chosen.typed) {
      const team = chosen.team ? teamOf(chosen.team) : null;
      byId('shooter-name').textContent = (chosen.number ? 'No. ' + chosen.number + ' · ' : '') + (team ? team.name : 'Which team?');
      byId('shooter-where').textContent = chosen.where || 'Typed: the position is looked up when the shot is saved';
    } else {
      byId('shooter-name').textContent = shooterWords(chosen);
      byId('shooter-where').textContent = chosen.where || 'Position unknown just now';
    }
  }
  for (const button of byId('kinds').children) button.className = button.dataset.kind === state.kind ? 'on' : '';
  const hint = byId('kind-hint');
  hint.hidden = true;
  if (chosen && !chosen.typed && chosen.near_line && !state.kindByHand) {
    hint.hidden = false;
    hint.textContent = 'Close to the three-point line: check whether it was a 2 or a 3.';
  } else if (chosen && !chosen.worth && state.kind === null) {
    hint.hidden = false;
    hint.textContent = 'Choose 2 points, 3 points or free throw.';
  }
}

byId('kinds').addEventListener('click', function (event) {
  const button = event.target.closest('button');
  if (!button) return;
  state.kind = button.dataset.kind;
  state.kindByHand = true;
  showShooter();
});

/* ---- saving a shot ---- */

async function saveShot(made) {
  const chosen = state.chosen;
  if (!state.moment) { toast('Pause the video at the shot first.', 'bad'); return; }
  if (!chosen) { toast('Say who shot: click the player in the picture, or type the team and number.', 'bad'); return; }
  if (!state.kind) { toast('Choose 2 points, 3 points or free throw.', 'bad'); return; }
  const body = {
    index: state.moment.index, period: state.period, watching: state.setup.watch.kind,
    team: chosen.team, number: chosen.number, tag: chosen.tag, made: made,
    kind: state.kind === 'ft' ? 'free throw' : 'field goal',
    // The tool works 2-or-3 out from the position; only a choice by the coach overrules it.
    worth: state.kind !== 'ft' && (state.kindByHand || !chosen.worth) ? Number(state.kind) : null,
  };
  const buttons = [byId('made'), byId('missed')];
  buttons.forEach(function (button) { button.disabled = true; });
  const shot = await tryToast(function () { return postJSON(base() + '/shots', body); });
  buttons.forEach(function (button) { button.disabled = false; });
  if (!shot) return;
  toast('Saved: No. ' + (shot.number || '?') + ' ' + shot.line, 'good');
  state.chosen = null;
  state.kind = null;
  state.kindByHand = false;
  byId('typed-number').value = '';
  showTeamChoice();
  showShooter();
  await loadShots();
  video.play().catch(function () {});           // on to the next shot
}

byId('made').addEventListener('click', function () { saveShot(true); });
byId('missed').addEventListener('click', function () { saveShot(false); });

/* ---- the "Tagged so far" list ---- */

async function loadShots() {
  const reply = await tryToast(function () { return api(base() + '/shots' + watching()); });
  if (!reply) return;
  state.shots = reply.shots;
  byId('undo').disabled = !reply.can_undo;
  const holder = byId('tagged');
  holder.replaceChildren();
  if (!byId('poss-form').hidden) showPointsHint();      // the possession form counts points from these shots
  if (!state.shots.length) {
    holder.appendChild(el('div', { class: 'hint' }, 'No shots tagged yet.'));
    byId('points-line').hidden = true;
    return;
  }
  const points = state.setup.teams.map(function (team) { return team.name + ' ' + (reply.points[team.label] || 0); });
  byId('points-line').hidden = false;
  byId('points-line').textContent = 'Points so far: ' + points.join(' · ');
  // newest first, as the mockup lists them
  for (const shot of state.shots.slice().reverse()) {
    holder.appendChild(shotRow(shot));
    if (state.openShot === shot.id) holder.appendChild(shotEditor(shot));
  }
}

function shotRow(shot) {
  const who = shot.number !== null ? shot.number : '?';
  return el('div', { class: 'shot-row' + (state.openShot === shot.id ? ' open' : ''), title: 'Click to change or delete', onclick: function () {
    state.openShot = state.openShot === shot.id ? null : shot.id;
    loadShots();
  } }, [
    el('span', { class: shot.made ? 'dot-made' : 'dot-missed' }),
    teamDot(shot.team),
    el('span', { class: 'who', title: shooterWords(shot) }, who),
    el('span', { class: 'what' }, shot.line + (shot.worth_from === 'coach' ? ' (your call)' : '')),
    el('button', { type: 'button', class: 'when', title: 'Go to this moment',
                   onclick: function (event) { event.stopPropagation(); seekTo(shot.watch_time); } }, fmtClock(shot.time_s)),
  ]);
}

function choice(options, current, onPick) {
  return el('div', { class: 'segment' }, options.map(function (option) {
    return el('button', { type: 'button', class: option[0] === current ? 'on' : null,
                          onclick: function (event) { event.stopPropagation(); onPick(option[0]); } }, option[1]);
  }));
}

function shotEditor(shot) {
  function change(changes) {
    tryToast(async function () {
      await putJSON(base() + '/shots/' + shot.id, { changes: changes, watching: state.setup.watch.kind });
      await loadShots();
    });
  }
  const kindNow = shot.kind === 'free throw' ? 'ft' : String(shot.worth);
  const words = teamWords(shot.team) + ' · ' + (shot.name || (shot.number !== null ? 'No. ' + shot.number : 'number not read')) +
                ' · period ' + shot.period + ' · ' + shot.where;
  return el('div', { class: 'shot-edit' }, [
    el('div', { class: 'small muted', style: 'width:100%' }, words),
    choice([[true, 'Made'], [false, 'Missed']], shot.made, function (made) { change({ made: made }); }),
    choice([['2', '2'], ['3', '3'], ['ft', 'Free throw']], kindNow, function (kind) {
      change(kind === 'ft' ? { kind: 'free throw' } : { kind: 'field goal', worth: Number(kind) });
    }),
    el('select', { class: 'input', 'aria-label': 'Period',
                   onchange: function (event) { change({ period: event.target.value }); },
                   onclick: function (event) { event.stopPropagation(); } },
       state.setup.periods.map(function (period) { return el('option', { value: period, selected: period === shot.period }, 'Period ' + period); })),
    el('button', { type: 'button', class: 'btn btn-link push', style: 'color: var(--red-dark)', onclick: function (event) {
      event.stopPropagation();
      tryToast(async function () {
        await deleteJSON(base() + '/shots/' + shot.id);
        state.openShot = null;
        toast('Shot deleted. "Undo last" brings it back.', '');
        await loadShots();
      });
    } }, 'Delete'),
  ]);
}

byId('undo').addEventListener('click', function () {
  tryToast(async function () {
    await postJSON(base() + '/shots/undo', {});
    state.openShot = null;
    toast('The last change was taken back.', '');
    await loadShots();
  });
});

/* ---- Plays mode: the possessions ---- */

// "1:23.4" -> seconds; null when it cannot be read
function parseClock(text) {
  const parts = String(text || '').trim().split(':');
  if (!parts.length || parts.length > 3 || parts.some(function (part) { return part === '' || isNaN(Number(part)); })) return null;
  return parts.reduce(function (total, part) { return total * 60 + Number(part); }, 0);
}
// 83.44 -> "1:23.4" (tenths, so two possessions can meet exactly)
function fmtTenths(seconds) {
  const tenths = Math.max(0, Math.round((seconds || 0) * 10));
  const rest = tenths % 600;
  return Math.floor(tenths / 600) + ':' + (rest < 100 ? '0' : '') + (rest / 10).toFixed(1);
}

// The made points and the number of the team's tagged shots between two times (the end belongs to the next possession).
function shotsInside(team, start, end) {
  let points = 0, count = 0;
  for (const shot of state.shots) {
    if (shot.team === team && start <= shot.time_s && shot.time_s < end) {
      count += 1;
      if (shot.made) points += shot.worth;
    }
  }
  return { points: points, count: count };
}

async function loadPossessions() {
  const reply = await tryToast(function () { return api(base() + '/possessions'); });
  if (!reply) return;
  state.possessions = reply.possessions;
  byId('poss-count').textContent = reply.labelled + ' of ' + plural(reply.count, 'possession') + ' labelled';
  byId('poss-count').className = 'chip ' + (reply.count && reply.labelled === reply.count ? 'chip-green' : 'chip-grey');
  const holder = byId('poss-list');
  holder.replaceChildren();
  if (!state.possessions.length) holder.appendChild(el('div', { class: 'hint' }, 'No possessions labelled yet.'));
  for (const record of state.possessions) holder.appendChild(possessionRow(record));
  drawStrip();
}

function outcomeWords(record) {
  if (record.outcome === 'scored') return 'Scored';
  if (record.outcome === 'missed') return 'Missed';
  if (record.outcome === 'turnover') return 'Turnover';
  return '';
}

function possessionRow(record) {
  const ours = record.team === state.setup.our_team;
  const unlabelled = !record.offense || !record.defense;
  const words = (record.offense ? record.offense_label : 'offense?') + ' · vs ' + (record.defense ? record.defense_label : 'defense?');
  const editing = state.editing && state.editing.id === record.id;
  const sure = state.sureDelete === record.id;
  const points = el('span', { class: 'pts', title: record.points_from === 'coach' ? 'typed by you' : 'counted from the tagged shots' },
                    [record.points + ' pt' + (record.points === 1 ? '' : 's'),
                     record.points_from === 'coach' ? el('span', { class: 'typed' }, ' (typed)') : null]);
  return el('div', { class: 'poss-row' + (editing ? ' on' : ''), id: 'poss-' + record.id }, [
    el('div', { class: 'poss-line' }, [
      el('button', { type: 'button', class: 'when time', title: 'Go to this moment', onclick: function () { seekTo(record.start_s); } },
         fmtClock(record.start_s) + '–' + fmtClock(record.end_s)),
      el('span', { class: 'chip ' + (ours ? 'chip-dark' : 'chip-grey') }, ours ? 'Us' : 'Them'),
      el('span', { class: 'what' + (unlabelled ? ' unlabelled' : '') }, words),
    ]),
    el('div', { class: 'poss-line' }, [
      points,
      el('span', { class: 'small' + (record.outcome === 'turnover' ? ' bad' : record.outcome === 'scored' ? ' good' : ' muted') }, outcomeWords(record) || 'Outcome not said'),
      el('span', { class: 'buttons' }, [
        el('button', { type: 'button', class: 'btn btn-sm btn-light', onclick: function () { openForm(record); } }, 'Change'),
        el('button', { type: 'button', class: 'btn btn-sm btn-light' + (sure ? ' sure' : ''), onclick: function () { deletePossession(record); } },
           sure ? 'Sure? Delete' : 'Delete'),
      ]),
    ]),
  ]);
}

// The possessions along the video: red = ours, grey = theirs (darker when they scored), pale = a turnover.
function drawStrip() {
  const strip = byId('strip');
  strip.replaceChildren();
  if (!state.setup) return;
  const length = video.duration || (state.game.game && state.game.game.seconds);
  if (!length) return;
  // longest first, so a short possession inside a longer (probably mistaken) one still shows on top
  const ordered = state.possessions.slice().sort(function (a, b) { return (b.end_s - b.start_s) - (a.end_s - a.start_s); });
  for (const record of ordered) {
    const ours = record.team === state.setup.our_team;
    const cls = record.outcome === 'turnover' ? 'turnover' : ours ? 'ours' : record.points > 0 ? 'theirs-scored' : 'theirs';
    const editing = state.editing && state.editing.id === record.id;
    strip.appendChild(el('button', { type: 'button', class: cls + (editing ? ' on' : ''),
      title: (ours ? 'Us' : 'Them') + ' ' + fmtClock(record.start_s) + '–' + fmtClock(record.end_s) + (record.points ? ', ' + record.points + ' pts' : ''),
      style: 'left:' + (100 * record.start_s / length) + '%; width:' + Math.max(0.3, 100 * (record.end_s - record.start_s) / length) + '%',
      onclick: function () { seekTo(record.start_s); } }));
  }
  strip.appendChild(el('span', { class: 'now', style: 'left:' + (video.duration ? 100 * video.currentTime / video.duration : 0) + '%' }));
}

/* ---- the possession form ---- */

function fillSelect(select, names, keep) {
  select.replaceChildren(el('option', { value: '' }, 'Not labelled yet'));
  names.forEach(function (name) { select.appendChild(el('option', { value: name }, name)); });
  if (keep && !names.includes(keep)) select.appendChild(el('option', { value: keep }, keep));   // a word from an older list
  select.value = keep || '';
}

function fillPlayTypes(keepOffense, keepDefense) {
  fillSelect(byId('f-offense'), state.setup.play_types.offense, keepOffense);
  fillSelect(byId('f-defense'), state.setup.play_types.defense, keepDefense);
}

function showTeamSegment(chosen) {
  const holder = byId('f-team');
  holder.replaceChildren();
  for (const team of state.setup.teams) {
    holder.appendChild(el('button', { type: 'button', class: team.label === chosen ? 'on' : null, 'data-team': team.label,
      onclick: function () { showTeamSegment(team.label); showPointsHint(); } }, [teamDot(team.label), team.name]));
  }
}
function chosenTeam() {
  const on = byId('f-team').querySelector('.on');
  return on ? on.dataset.team : null;
}

const OUTCOME_WORDS = [['scored', 'Scored'], ['missed', 'Missed'], ['turnover', 'Turnover']];
function showOutcome(chosen) {
  const holder = byId('f-outcome');
  holder.replaceChildren();
  OUTCOME_WORDS.forEach(function (pair) {
    holder.appendChild(el('button', { type: 'button', class: pair[0] === chosen ? 'on' : null, 'data-outcome': pair[0],
      onclick: function () { state.outcomeByHand = true; showOutcome(pair[0]); } }, pair[1]));
  });
}
function chosenOutcome() {
  const on = byId('f-outcome').querySelector('.on');
  return on ? on.dataset.outcome : '';
}

// The points counted from the shots inside the span, shown under the Points box; and, until the coach
// says otherwise, the outcome that follows from them.
function showPointsHint() {
  const team = chosenTeam(), start = parseClock(byId('f-start').value), end = parseClock(byId('f-end').value);
  const hint = byId('points-hint');
  if (!team || start === null || end === null || end <= start) {
    byId('f-points').placeholder = '0';
    hint.textContent = 'Counted from the shots tagged in this span once the start and end are set. Type a number to overrule it.';
    return;
  }
  const inside = shotsInside(team, start, end);
  byId('f-points').placeholder = String(inside.points);
  hint.textContent = inside.count
    ? 'Counted from the ' + plural(inside.count, 'shot') + ' tagged in this span: ' + inside.points + ' points. Type a number to overrule it.'
    : 'No shot of ' + (team === state.setup.our_team ? 'ours' : 'theirs') + ' is tagged in this span, so this counts 0. Tag the shot in Shots mode, or type the points.';
  if (!state.outcomeByHand && !chosenOutcome()) showOutcome(inside.points > 0 ? 'scored' : 'missed');
}

function openForm(record) {
  state.editing = record || null;
  state.outcomeByHand = !!record;
  state.sureDelete = null;
  video.pause();
  const setup = state.setup;
  // a new possession starts now; the ball is with the other side than the last possession, else with them
  const last = state.possessions[state.possessions.length - 1];
  const nextTeam = last ? (last.team === setup.our_team ? setup.their_team : setup.our_team) : setup.their_team;
  byId('form-title').textContent = record ? 'Possession ' + fmtClock(record.start_s) + '–' + fmtClock(record.end_s) : 'New possession';
  byId('f-start').value = record ? fmtTenths(record.start_s) : fmtTenths(video.currentTime);
  byId('f-end').value = record ? fmtTenths(record.end_s) : '';
  showTeamSegment(record ? record.team : nextTeam);
  fillPlayTypes(record ? record.offense : '', record ? record.defense : '');
  showOutcome(record ? record.outcome : '');
  byId('f-points').value = record && record.points_from === 'coach' ? record.points : '';
  byId('f-note').value = record ? record.note : '';
  byId('poss-form').hidden = false;
  byId('plays-editor').hidden = true;
  showPointsHint();
  loadPossessionsRowsOnly();
  (record ? byId('f-offense') : byId('f-end')).focus();
}

function loadPossessionsRowsOnly() {
  const holder = byId('poss-list');
  holder.replaceChildren();
  if (!state.possessions.length) holder.appendChild(el('div', { class: 'hint' }, 'No possessions labelled yet.'));
  for (const record of state.possessions) holder.appendChild(possessionRow(record));
  drawStrip();
}

function closeForm() {
  state.editing = null;
  state.outcomeByHand = false;
  byId('poss-form').hidden = true;
  loadPossessionsRowsOnly();
}

// The Start / End buttons (and the S / E keys): take the time the video is at.
function markTime(which) {
  if (byId('poss-form').hidden) openForm(null);
  byId('f-' + which).value = fmtTenths(video.currentTime);
  if (which === 'end' && !state.editing) state.outcomeByHand = false;
  showPointsHint();
}

async function savePossession(event) {
  event.preventDefault();
  const body = { start: byId('f-start').value, end: byId('f-end').value, team: chosenTeam(),
                 offense: byId('f-offense').value, defense: byId('f-defense').value, outcome: chosenOutcome(),
                 points: byId('f-points').value, note: byId('f-note').value };
  if (!body.end.trim()) { toast('Say when the possession ended: press End while the video is there, or type the time.', 'bad'); return; }
  if (!body.team) { toast('Say who had the ball.', 'bad'); return; }
  const saved = await tryToast(function () {
    return state.editing ? putJSON(base() + '/possessions/' + state.editing.id, body) : postJSON(base() + '/possessions', body);
  });
  if (!saved) return;
  toast((state.editing ? 'Possession changed: ' : 'Possession saved: ') + fmtClock(saved.start_s) + '–' + fmtClock(saved.end_s) + '.', 'good');
  closeForm();
  await loadPossessions();
}

// Delete asks once, in the button itself (no pop-up): the first press turns it into "Sure? Delete".
async function deletePossession(record) {
  if (state.sureDelete !== record.id) {
    state.sureDelete = record.id;
    loadPossessionsRowsOnly();
    return;
  }
  const done = await tryToast(function () { return deleteJSON(base() + '/possessions/' + record.id); });
  state.sureDelete = null;
  if (!done) return;
  toast('Possession ' + fmtClock(record.start_s) + '–' + fmtClock(record.end_s) + ' deleted.', '');
  if (state.editing && state.editing.id === record.id) closeForm();
  await loadPossessions();
}

byId('add-possession').addEventListener('click', function () { openForm(null); });
byId('poss-form').addEventListener('submit', savePossession);
byId('cancel-possession').addEventListener('click', closeForm);
byId('use-start').addEventListener('click', function () { markTime('start'); });
byId('use-end').addEventListener('click', function () { markTime('end'); });
['f-start', 'f-end'].forEach(function (id) { byId(id).addEventListener('input', showPointsHint); });

/* ---- the coach's own play names ---- */

byId('edit-plays').addEventListener('click', function () {
  const editor = byId('plays-editor');
  editor.hidden = !editor.hidden;
  if (editor.hidden) return;
  byId('names-offense').value = state.setup.play_types.offense.join('\n');
  byId('names-defense').value = state.setup.play_types.defense.join('\n');
  byId('names-offense').focus();
});
byId('cancel-names').addEventListener('click', function () { byId('plays-editor').hidden = true; });
byId('save-names').addEventListener('click', function () {
  const lines = function (id) { return byId(id).value.split('\n').map(function (line) { return line.trim(); }).filter(Boolean); };
  tryToast(async function () {
    state.setup.play_types = await putJSON('/api/play-types', { offense: lines('names-offense'), defense: lines('names-defense') });
    fillPlayTypes(byId('f-offense').value, byId('f-defense').value);
    byId('plays-editor').hidden = true;
    toast('Play names saved.', 'good');
  });
});

start().catch(function (problem) { toast(problem.message, 'bad'); });
