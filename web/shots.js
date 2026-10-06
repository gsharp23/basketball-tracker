/* The Tag shots screen: watch the game, pause at a shot, click the shooter, say made or missed.
   The boxes are drawn here in the browser from one small JSON answer per pause, so a step is quick.
   Talks to src/api_shots.py; the records live in data/<game>/shots.json (src/shots.py). */

renderHeader('shots');

function byId(id) { return document.getElementById(id); }

const state = {
  game: null,        // {video, name} from currentGame()
  setup: null,       // GET /api/shots/setup: teams, court, which video is watched
  moment: null,      // the last GET /api/shots/boxes answer (the paused moment)
  chosen: null,      // the shooter: a boxed player, or {typed: true, team, number}
  kind: null,        // '2', '3' or 'ft'
  kindByHand: false, // true once the coach chose the kind himself (the position no longer decides)
  period: '1',
  shots: [],
  openShot: null,    // id of the shot whose editor is open in the list
};

const video = byId('video');

/* ---- start ---- */

async function start() {
  state.game = await currentGame();
  if (!state.game) {
    byId('stage-note').textContent = 'Choose a game first, on the Games screen.';
    byId('back-link').href = 'games.html';
    byId('back-link').textContent = '← Games';
    return;
  }
  byId('back-link').href = withGame('game.html', state.game.name);
  byId('back-link').textContent = '← ' + state.game.name;
  byId('heat-link').href = withGame('game.html', state.game.name);
  try {
    state.setup = await api('/api/shots/setup?video=' + encodeURIComponent(state.game.video));
  } catch (problem) {
    // Not processed far enough: the message sits in the picture, and the parts that need a video are hidden.
    byId('stage-note').textContent = problem.message;
    byId('periods').parentElement.hidden = true;
    byId('typed').hidden = true;
    byId('kinds').hidden = true;
    byId('made').parentElement.hidden = true;
    return;
  }
  byId('back-link').textContent = '← ' + gameTitle(state.setup.info, state.game.name);
  showPeriods();
  showTeamChoice();
  if (state.setup.stand_in_court) {
    byId('help').textContent = 'Pause at a shot, click the shooter in the picture, then choose made or missed. ' +
      'The court of this game was marked with stand-in clicks, so distances and 2-or-3 are provisional: check them.';
  }
  video.src = state.setup.watch.url;
  showShooter();
  await showList();
}

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

// The team buttons for a shooter who has no box: the roster's name when it is known, else the tool's label.
function teamWords(label) {
  const team = state.setup.teams.find(function (each) { return each.label === label; });
  return team && team.name ? team.name : label;
}

function showTeamChoice() {
  const holder = byId('typed-team');
  holder.replaceChildren();
  for (const team of state.setup.teams) {
    const on = state.chosen && state.chosen.typed && state.chosen.team === team.label;
    holder.appendChild(el('button', { type: 'button', class: on ? 'on' : null, onclick: function () { pickTyped(team.label); } },
                          teamWords(team.label)));
  }
}

/* ---- the video and its transport ---- */

function updateClock() {
  const where = video.duration ? 100 * video.currentTime / video.duration : 0;
  byId('scrub-fill').style.width = where + '%';
  byId('scrub').setAttribute('aria-valuenow', Math.round(where));
  byId('clock').textContent = fmtClock(video.currentTime);
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

video.addEventListener('loadedmetadata', function () {
  byId('stage-note').hidden = true;
  updateClock();
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

// play() gives a promise that fails when paused again at once; that is not a problem worth a message
byId('play').addEventListener('click', function () { if (video.paused) video.play().catch(function () {}); else video.pause(); });
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
// Space plays and pauses, as in any player, unless the coach is typing a number.
document.addEventListener('keydown', function (event) {
  if (event.key === ' ' && event.target.tagName !== 'INPUT' && event.target.tagName !== 'BUTTON') {
    event.preventDefault();
    byId('play').click();
  }
});
window.addEventListener('resize', drawBoxes);

/* ---- the player boxes over the paused picture ---- */

let boxesAsked = 0;     // so a late answer for an earlier moment is not drawn over a newer one

function clearBoxes() {
  state.moment = null;
  byId('boxes').replaceChildren();
}

async function fetchBoxes() {
  if (!state.setup || !video.duration) return;
  const asked = ++boxesAsked;
  const query = '?video=' + encodeURIComponent(state.game.video) + '&t=' + video.currentTime.toFixed(3) +
                '&watching=' + state.setup.watch.kind;
  try {
    const moment = await api('/api/shots/boxes' + query);
    if (asked !== boxesAsked || !video.paused) return;
    state.moment = moment;
    drawBoxes();
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
    const chosen = state.chosen && !state.chosen.typed && state.chosen.tag === player.tag;
    const label = player.number !== null ? player.number : '?';
    const box = el('button', {
      type: 'button', class: 'pbox' + (chosen ? ' chosen' : ''), title: shooterWords(player),
      style: 'left:' + (100 * (rect.left + player.box[0] * rect.scale) / rect.stageWidth) + '%;' +
             'top:' + (100 * (rect.top + player.box[1] * rect.scale) / rect.stageHeight) + '%;' +
             'width:' + (100 * (player.box[2] - player.box[0]) * rect.scale / rect.stageWidth) + '%;' +
             'height:' + (100 * (player.box[3] - player.box[1]) * rect.scale / rect.stageHeight) + '%',
      onclick: function () { pick(player); },
    }, el('span', { class: 'tagline' }, label));
    holder.appendChild(box);
  }
}

/* ---- the shooter card ---- */

function shooterWords(player) {
  if (player.name) return player.name;
  if (player.number !== null) return teamWords(player.team) + ' #' + player.number;
  return teamWords(player.team) + ', number not read';
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
    // The number was not read: the coach types it, and the team is already his.
    byId('shooter-where').textContent = 'Type the jersey number below (the team is chosen).';
    byId('typed-number').focus();
  }
}

function pickTyped(team) {
  const number = byId('typed-number').value.trim();
  const before = state.chosen && state.chosen.typed ? state.chosen : null;
  state.chosen = { typed: true, team: team || (before && before.team) || null, number: number, tag: null, worth: null };
  if (!before) { state.kindByHand = false; state.kind = null; }
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
      byId('shooter-name').textContent = (chosen.team ? teamWords(chosen.team) : 'Which team?') + (chosen.number ? ' #' + chosen.number : '');
      byId('shooter-where').textContent = 'Typed: the position is looked up when the shot is saved';
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
    video: state.game.video, index: state.moment.index, period: state.period, watching: state.setup.watch.kind,
    team: chosen.team, number: chosen.number, tag: chosen.tag, made: made,
    kind: state.kind === 'ft' ? 'free throw' : 'field goal',
    // The tool works 2-or-3 out from the position; only a choice by the coach overrules it.
    worth: state.kind !== 'ft' && (state.kindByHand || !chosen.worth) ? Number(state.kind) : null,
  };
  const buttons = [byId('made'), byId('missed')];
  buttons.forEach(function (button) { button.disabled = true; });
  const shot = await tryToast(function () { return postJSON('/api/shots/add', body); });
  buttons.forEach(function (button) { button.disabled = false; });
  if (!shot) return;
  toast('Saved: #' + (shot.number || '?') + ' ' + shot.line, 'good');
  state.chosen = null;
  state.kind = null;
  state.kindByHand = false;
  byId('typed-number').value = '';
  showTeamChoice();
  showShooter();
  await showList();
  video.play().catch(function () {});           // on to the next shot
}

byId('made').addEventListener('click', function () { saveShot(true); });
byId('missed').addEventListener('click', function () { saveShot(false); });

/* ---- the "Tagged so far" list ---- */

async function showList() {
  const reply = await tryToast(function () {
    return api('/api/shots/list?video=' + encodeURIComponent(state.game.video) + '&watching=' + state.setup.watch.kind);
  });
  if (!reply) return;
  state.shots = reply.shots;
  byId('undo').disabled = !reply.can_undo;
  const holder = byId('tagged');
  holder.replaceChildren();
  if (!state.shots.length) {
    holder.appendChild(el('div', { class: 'hint' }, 'No shots tagged yet.'));
    byId('points-line').hidden = true;
    return;
  }
  const points = state.setup.teams.map(function (team) { return teamWords(team.label) + ' ' + (reply.points[team.label] || 0); });
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
  const row = el('div', { class: 'shot-row' + (state.openShot === shot.id ? ' open' : ''), title: 'Click to change or delete', onclick: function () {
    state.openShot = state.openShot === shot.id ? null : shot.id;
    showList();
  } }, [
    el('span', { class: shot.made ? 'dot-made' : 'dot-missed' }),
    el('span', { class: 'who', title: shot.name || (teamWords(shot.team) + (shot.number !== null ? ' #' + shot.number : ', number not read')) }, who),
    el('span', { class: 'what' }, shot.line + (shot.worth_from === 'coach' ? ' (your call)' : '')),
    el('button', { type: 'button', class: 'when', title: 'Go to this moment',
                   onclick: function (event) { event.stopPropagation(); seekTo(shot.watch_time); } }, shot.clock.replace(/\.\d$/, '')),
  ]);
  return row;
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
      await api('/api/shots/' + shot.id, { method: 'PUT', body: { video: state.game.video, changes: changes, watching: state.setup.watch.kind } });
      await showList();
    });
  }
  const kindNow = shot.kind === 'free throw' ? 'ft' : String(shot.worth);
  const teamAndName = teamWords(shot.team) + ' · ' + (shot.name || (shot.number !== null ? '#' + shot.number : 'number not read')) +
                      ' · period ' + shot.period + ' · ' + shot.where;
  return el('div', { class: 'shot-edit' }, [
    el('div', { class: 'small muted', style: 'width:100%' }, teamAndName),
    choice([[true, 'Made'], [false, 'Missed']], shot.made, function (made) { change({ made: made }); }),
    choice([['2', '2'], ['3', '3'], ['ft', 'Free throw']], kindNow, function (kind) {
      change(kind === 'ft' ? { kind: 'free throw' } : { kind: 'field goal', worth: Number(kind) });
    }),
    el('select', { class: 'input', style: 'width:auto;min-height:36px;padding:0 8px', 'aria-label': 'Period',
                   onchange: function (event) { change({ period: event.target.value }); },
                   onclick: function (event) { event.stopPropagation(); } },
       state.setup.periods.map(function (period) { return el('option', { value: period, selected: period === shot.period }, 'Period ' + period); })),
    el('button', { type: 'button', class: 'delete', onclick: function (event) {
      event.stopPropagation();
      tryToast(async function () {
        await api('/api/shots/' + shot.id + '?video=' + encodeURIComponent(state.game.video), { method: 'DELETE' });
        state.openShot = null;
        toast('Shot deleted. "Undo last" brings it back.', '');
        await showList();
      });
    } }, 'Delete'),
  ]);
}

byId('undo').addEventListener('click', function () {
  tryToast(async function () {
    await postJSON('/api/shots/undo', { video: state.game.video });
    state.openShot = null;
    toast('The last change was taken back.', '');
    await showList();
  });
});

// The heat map on the Game screen is drawn from the shots file; draw it fresh before going there.
byId('heat-link').addEventListener('click', function (event) {
  if (!state.game || !state.setup) return;
  event.preventDefault();
  const link = byId('heat-link');
  link.textContent = 'Drawing the heat map...';
  link.classList.add('is-waiting');
  tryToast(async function () {
    await postJSON('/api/shots/heat_maps', { video: state.game.video });
    location.href = link.href;
  }).then(function () {
    link.textContent = 'See the scoring heat map';
    link.classList.remove('is-waiting');
  });
});

start().catch(function (problem) { toast(problem.message, 'bad'); });
