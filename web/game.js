/* The Game results screen: our team's numbers for the current game (?game=<name>), the video, the
   scoring heat map, the players table; the processing card and the court set-up panel below. */

renderHeader('games');

let current = null;         // {video, name, game} from currentGame()
let results = null;         // the answer of GET /api/games/results
let stopWatching = null;    // stops the polling of the processing job
let stopPageWatch = null;   // stops the polling of an open click page
let wasRunning = false;     // was the processing running at the last look?

function byId(id) { return document.getElementById(id); }

async function setup() {
  current = await currentGame();
  if (!current) {
    byId('game-title').textContent = 'No game chosen';
    byId('game-line').textContent = 'Choose a game on the Games screen first.';
    byId('court').hidden = true;
    return;
  }
  byId('link-ours').href = withGame('game.html', current.name);
  byId('link-opponent').href = withGame('opponent.html', current.name);
  byId('link-shots').href = withGame('shots.html', current.name);
  if (!current.game) current.game = await gameEntry();
  await loadResults();
  loadCourt();
  watchProcessing();
  if (location.hash === '#court') byId('court').scrollIntoView({ behavior: 'smooth' });
}

// The game's entry in /api/games (status, stages), when the page was opened without ?game=.
async function gameEntry() {
  const reply = await api('/api/games');
  return reply.games.find(function (game) { return game.video === current.video; }) || null;
}

/* ---- the head and the results ---- */

async function loadResults() {
  results = await api('/api/games/results?video=' + encodeURIComponent(current.video));
  byId('game-title').textContent = gameTitle(results.info, current.name);
  const line = [results.info.date_is_guess ? 'Added ' + results.info.date : results.info.date, fmtLength(results.seconds)];
  if (results.stand_in_court) line.push('Provisional: the court was marked with stand-in clicks');
  byId('game-line').textContent = line.join(' · ');
  byId('open-excel').disabled = !results.stats_csv;
  showOurTeamChoice();

  const ready = results.has_stats || Object.keys(results.videos).length > 0;
  byId('results').hidden = !ready;
  if (!ready) return;
  showTiles();
  showPlayers();
  showHeatPills();
  showHeat('');
  setVideo(results.videos.tactical ? 'tactical' : 'tracked');
}

function showOurTeamChoice() {
  const row = byId('our-team-row'), select = byId('our-team');
  row.hidden = results.teams.length < 2;
  select.replaceChildren();
  results.teams.forEach(function (label) {
    select.appendChild(el('option', { value: label, selected: label === results.our_team }, label));
  });
  byId('our-team-guess').hidden = results.our_team_chosen;
}

function fraction(part) { return part.made + '/' + part.tried; }

function showTiles() {
  const tiles = results.tiles;
  byId('tile-points').textContent = tiles.points;
  byId('tile-fg').textContent = fraction(tiles.field_goals);
  byId('tile-threes').textContent = fraction(tiles.threes);
  byId('tile-ft').textContent = fraction(tiles.free_throws);
}

function showPlayers() {
  const body = byId('players');
  body.replaceChildren();
  byId('players-provisional').hidden = !results.stand_in_court;
  if (!results.has_stats) {
    body.appendChild(el('tr', {}, el('td', { colspan: 7, class: 'muted' }, 'The stats are made at the last step of processing.')));
    return;
  }
  results.players.forEach(function (player) {
    body.appendChild(el('tr', {}, [
      el('td', { class: 'num' }, player.number),
      el('td', {}, player.name || '–'),
      el('td', { style: 'font-weight: 700' }, String(player.points)),
      el('td', {}, fraction(player.shots)),
      el('td', {}, fmtClock(player.seen_s)),
      el('td', {}, fmtFeet(player.distance_ft)),
      el('td', {}, fmtPercent(player.paint_pct))]));
  });
  const notes = [];
  if (!results.players.length) notes.push('No player of the ' + results.our_team + ' team got a jersey number.');
  const noNames = results.players.length && results.players.every(function (player) { return !player.name; });
  if (noNames && results.roster_numbers.length) {
    notes.push('The roster (numbers ' + results.roster_numbers.join(', ') + ') does not match the numbers read in this game, so no names are shown. Check Settings > Roster, and that "Our team wears" is right.');
  } else if (results.players.some(function (player) { return !player.name; })) notes.push('Names come from Settings > Roster.');
  if (results.players_without_number) {
    notes.push(results.players_without_number + ' player' + (results.players_without_number === 1 ? '' : 's') +
      ' of this team could not be given a number and ' + (results.players_without_number === 1 ? 'is' : 'are') + ' not listed.');
  }
  notes.push('Points and shots come from the shots you tagged. Distance is while the player had a position on the court.');
  byId('players-note').textContent = notes.join(' ');
}

/* ---- the heat map ---- */

function showHeatPills() {
  const pills = byId('heat-pills');
  pills.replaceChildren(el('button', { class: 'pill on', type: 'button', 'data-player': '', onclick: pickHeat }, 'Team'));
  results.shooters.forEach(function (number) {
    pills.appendChild(el('button', { class: 'pill', type: 'button', 'data-player': number, onclick: pickHeat }, 'No. ' + number));
  });
}

function pickHeat() {
  const chosen = this;
  document.querySelectorAll('#heat-pills .pill').forEach(function (pill) { pill.classList.toggle('on', pill === chosen); });
  showHeat(chosen.getAttribute('data-player'));
}

// The half court is drawn here from the shots' positions (drawHalfCourt in app.js, shared with the Opponent report).
function showHeat(player) {
  const marks = results.marks.filter(function (mark) { return !player || String(mark.number) === String(player); });
  const empty = !results.shots_tagged || !results.our_team;
  byId('heat-empty').hidden = !empty;
  byId('heat-tag').href = withGame('shots.html', current.name);
  byId('heat-legend').hidden = empty;
  byId('heat-note').hidden = empty;
  drawHalfCourt(byId('heat-court'), results.court, marks, '#d81e27');
  byId('heat-areas').replaceChildren.apply(byId('heat-areas'), pointsByArea(marks).map(function (row) {
    return el('div', {}, [el('span', {}, row.area), el('span', { class: 'value' }, row.points + (row.points === 1 ? ' pt' : ' pts'))]);
  }));
  byId('heat-note').textContent = undrawnNote(marks);
}

/* ---- the video ---- */

function setVideo(kind) {
  const video = byId('video');
  document.querySelectorAll('#video-choice button').forEach(function (button) {
    button.classList.toggle('on', button.getAttribute('data-video') === kind);
    button.disabled = !results.videos[button.getAttribute('data-video')];
  });
  if (!results.videos[kind]) {
    byId('video-pane').replaceChildren('This video has not been made yet.');
    byId('video-row').hidden = true;
    return;
  }
  const wasPlaying = !video.paused;
  video.src = results.videos[kind];
  byId('video-row').hidden = false;
  if (wasPlaying) video.play();
}

function wireVideo() {
  const video = byId('video'), bar = byId('video-bar'), played = byId('video-played'), time = byId('video-time');
  const play = byId('play');
  function showTime() {
    const total = isFinite(video.duration) ? video.duration : 0;
    played.style.width = (total ? 100 * video.currentTime / total : 0) + '%';
    time.textContent = fmtClock(video.currentTime) + ' / ' + fmtClock(total);
  }
  function showButton() {
    play.innerHTML = video.paused
      ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4l13 8-13 8z"></path></svg>'
      : '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M6 5h4v14H6zM14 5h4v14h-4z"></path></svg>';
    play.setAttribute('aria-label', video.paused ? 'Play' : 'Pause');
  }
  play.addEventListener('click', function () { if (video.paused) video.play(); else video.pause(); });
  video.addEventListener('click', function () { if (video.paused) video.play(); else video.pause(); });
  video.addEventListener('timeupdate', showTime);
  video.addEventListener('loadedmetadata', showTime);
  video.addEventListener('play', showButton);
  video.addEventListener('pause', showButton);
  bar.addEventListener('click', function (event) {
    if (!isFinite(video.duration)) return;
    const box = bar.getBoundingClientRect();
    video.currentTime = video.duration * (event.clientX - box.left) / box.width;
  });
  document.querySelectorAll('#video-choice button').forEach(function (button) {
    button.addEventListener('click', function () { setVideo(button.getAttribute('data-video')); });
  });
}

/* ---- the stats file for Excel: its Windows path, shown and copied for the coach ---- */

async function showStatsPath() {
  if (!results || !results.stats_csv) { toast('The stats file is made at the last step of processing.'); return; }
  let copied = false;
  try { await navigator.clipboard.writeText(results.stats_csv); copied = true; } catch (problem) { copied = false; }
  const box = byId('stats-path');
  box.hidden = false;
  box.replaceChildren('The stats are in this file' + (copied ? ' (the path is copied)' : '') + ': ',
    el('b', {}, results.stats_csv), '. In Excel choose File > Open and paste the path.');
}

/* ---- our team ---- */

async function chooseOurTeam() {
  const saved = await tryToast(function () {
    return putJSON('/api/games/info?video=' + encodeURIComponent(current.video), { our_team: byId('our-team').value });
  });
  if (saved) { toast('Our team is the ' + saved.our_team + ' team in this game.', 'good'); loadResults().catch(function (p) { toast(p.message, 'bad'); }); }
}

/* ---- processing (the run of main.py) ---- */

function watchProcessing() {
  if (stopWatching) stopWatching();
  const status = current.game ? current.game.status : null;
  // The card stays hidden until the first answer: a finished game must not flash "Not started".
  stopWatching = pollJob('run_' + current.name, function (info) { showProcessing(info, status); }, 2000);
}

function showProcessing(info, status) {
  const card = byId('processing'), progress = info.progress || {};
  const running = info.running || info.starting;
  // A finished game that is not running hides the card: the results speak for themselves.
  card.hidden = !running && status === 'finished' && !(info.exit_code !== null && info.exit_code !== undefined && info.exit_code !== 0);
  const chip = byId('processing-chip');
  const words = { 'finished': ['chip-green', 'Finished'], 'running': ['chip-blue', 'Running here'],
                  'needs the court marked': ['chip-red', 'Needs the court marked'], 'not started': ['chip-grey', 'Not started'],
                  'stopped part-way': ['chip-dark', 'Stopped part-way'] };
  const which = running ? words.running : (words[status] || ['chip-grey', '']);
  chip.className = 'chip ' + which[0];
  chip.textContent = which[1];

  byId('processing-progress').hidden = !running;
  byId('process-stop').hidden = !running;
  byId('process-start').hidden = running;
  byId('process-start').textContent = status === 'stopped part-way' ? 'Carry on' : 'Process this game';
  if (running) {
    const step = progress.step ? (STEP_WORDS[progress.step] || progress.step) : 'Starting up';
    const percent = progress.how_far === null || progress.how_far === undefined ? null : Math.round(100 * progress.how_far);
    byId('processing-step').textContent = step;
    byId('processing-left').textContent = progress.left || '';
    byId('processing-bar').style.width = (percent === null ? 3 : Math.max(3, percent)) + '%';
    byId('processing-detail').textContent = (progress.step_count ? 'Step ' + progress.step_number + ' of ' + progress.step_count : 'Getting ready: loading the programs (about half a minute)') +
      (percent === null ? '' : ' · ' + percent + '%') + (progress.detail ? ' · ' + progress.detail : '');
    byId('processing-text').textContent = 'The results appear here when every step is done. You can close this page; the processing goes on while the black Hoops Tracker window is open.';
  } else if (progress.ended === 'court') {
    byId('processing-text').textContent = 'Stopped at the court step: the players, teams and jersey numbers are done. Mark the court below, then press "Process this game" to carry on.';
  } else if (progress.ended === 'stopped') {
    byId('processing-text').textContent = 'Stopped part-way. What was finished is kept: press "Carry on" to continue from there.';
  } else if (progress.ended === 'finished') {
    const why = current.game && current.game.roster_newer
      ? ' (The roster was changed after the stats were made: "Carry on" puts the new names into the stats, a few seconds.)'
      : ' (The files have changed since, so some steps would be done again.)';
    byId('processing-text').textContent = 'Every step is done.' + (status === 'finished' ? '' : why);
    if (status === 'finished') { card.hidden = true; }
  } else if (info.exit_code !== null && info.exit_code !== undefined && info.exit_code !== 0 && info.lines.length) {
    byId('processing-text').textContent = 'The last run broke off: ' + info.lines[info.lines.length - 1];
    byId('processing-log').hidden = false;
  } else {
    byId('processing-text').textContent = status === 'not started'
      ? 'Finds the players, sorts them into teams, reads the jersey numbers, maps the court and works out the stats. ' +
        'On this laptop a full game takes a day or two; a short clip a few minutes.'
      : 'Press the button to run the steps that are not done yet.';
  }
  const flags = byId('processing-flags');
  flags.hidden = !(info.flagged && info.flagged.length);
  flags.textContent = (info.flagged || []).join(' ');
  byId('processing-log').textContent = (info.lines || []).join('\n') || '(nothing printed yet)';
  if (info.error) byId('processing-text').textContent = info.error;
  if (!running && wasRunning) {
    // The run just ended: show the new results and the game's new status.
    wasRunning = false;
    gameEntry().then(function (entry) { current.game = entry; watchProcessing(); return loadResults(); })
      .then(loadCourt).catch(function (p) { toast(p.message, 'bad'); });
  }
  if (running) wasRunning = true;
}

const STEP_WORDS = { detect: 'Finding the players', court: 'Mapping the court', teams: 'Sorting the players into teams',
                     jerseys: 'Reading the jersey numbers', tactical: 'Drawing the court view', stats: 'Working out the stats' };

async function startProcessing(button) {
  const estimate = current.game && current.game.seconds < 300 ? 'a few minutes' : 'many hours (a day or two for a full game)';
  if (!window.confirm('Process this game on this laptop? It takes ' + estimate + '. Keep the laptop plugged in and awake, and the black Hoops Tracker window open. You can stop at any time; nothing finished is lost.')) return;
  button.disabled = true;
  const started = await tryToast(function () { return postJSON('/api/jobs/start', { kind: 'run', video: current.video, where: 'laptop' }); });
  button.disabled = false;
  if (started) { toast('Started.', 'good'); current.game = await gameEntry(); watchProcessing(); }
}

async function stopProcessing(button) {
  if (!window.confirm('Stop the processing? What is finished is kept, and "Carry on" continues later.')) return;
  button.disabled = true;
  button.textContent = 'Stopping...';
  await tryToast(function () { return postJSON('/api/jobs/run_' + encodeURIComponent(current.name) + '/stop'); });
  button.disabled = false;
  button.textContent = 'Stop';
  current.game = await gameEntry();
  watchProcessing();
}

/* ---- court set-up ---- */

async function loadCourt() {
  let court;
  try {
    court = await api('/api/games/court?video=' + encodeURIComponent(current.video));
  } catch (problem) {
    byId('court-chip').textContent = problem.message;
    return;
  }
  const chip = byId('court-chip');
  chip.className = 'chip ' + (court.ready ? (court.stand_in_court ? 'chip-red' : 'chip-green') : 'chip-grey');
  chip.textContent = court.ready ? (court.stand_in_court ? 'Stand-in clicks' : 'Marked') : 'Not marked yet';
  // A marked court folds the steps away behind one line (unless the coach opened them, or a click page is open).
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
  byId('court-hint').textContent = court.ready
    ? 'Runs the steps that use the court (mapping, positions, court view, stats).'
    : 'Do steps 1 and 2 first. Processing started now does the long steps that need no court, then waits for it.';
  byId('court-check').hidden = !court.court_check_url;
  byId('court-check-picture').dataset.src = court.court_check_url || '';
  if (court.open_page) watchClickPage(court.open_page.kind);
}

function courtStep(step, court) {
  const side = el('div', { class: 'step-side' });
  const open = court.open_page && court.open_page.kind === step.kind;
  const button = el('button', { class: 'btn ' + (step.done ? 'btn-outline' : 'btn-red'), type: 'button', id: 'step-' + step.kind,
                                onclick: function () { startClickPage(step, this); } },
                    step.done ? 'Do it again' : 'Open step ' + step.number);
  button.disabled = !!court.open_page;
  side.appendChild(button);
  side.appendChild(el('div', { class: 'small muted', id: 'step-word-' + step.kind },
    open ? 'This page is open now.' : (step.done ? 'Done' : (step.number === 3 ? 'Optional' : 'To do'))));
  // An open page is normally ended by its own "Save and close"; this closes it without saving.
  if (open) side.appendChild(el('button', { class: 'btn btn-light close-page', type: 'button', onclick: function () { closeClickPage(this); } }, 'Close without saving'));
  // A done step shows its title and, on request, its check picture; the how-to only while it is still to do.
  const text = el('div', { class: 'step-text' }, el('div', { class: 'step-title' }, step.title));
  if (!step.done) text.appendChild(el('div', { class: 'hint' }, step.guidance));
  if (step.picture_url) {
    text.appendChild(el('a', { class: 'small', href: step.picture_url, target: '_blank', rel: 'noopener' }, 'See the check picture'));
  }
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
      if (word) word.replaceChildren('Open: ', el('a', { href: info.address, target: '_blank', rel: 'noopener' }, info.address));
    } else if (info.address && word && !word.querySelector('a')) {
      word.replaceChildren('Open: ', el('a', { href: info.address, target: '_blank', rel: 'noopener' }, info.address));
    }
    if (!info.running && !info.starting) {
      if (tab && !sent) tab.close();
      const last = (info.lines || []).slice(-1)[0] || '';
      if (info.error) toast(info.error, 'bad');
      else if (info.exit_code === 0) toast('The page was closed. Saved.', 'good');
      else if (/without saving|cancelled/i.test(last)) toast('The page was closed without saving. Nothing was changed.', '');
      else toast('The page closed: ' + (last || 'see the step'), 'bad');
      loadCourt();
      gameEntry().then(function (entry) { current.game = entry; watchProcessing(); }).catch(function () {});
    }
  }, 1500);
}

/* ---- wiring ---- */

let courtOpened = false;
byId('court-unfold').addEventListener('click', function () { courtOpened = true; loadCourt(); });
byId('court-check-toggle').addEventListener('click', function () {
  const holder = byId('court-check-holder'), picture = byId('court-check-picture');
  holder.hidden = !holder.hidden;
  if (!holder.hidden && picture.dataset.src && !picture.src) picture.src = picture.dataset.src;
  this.textContent = holder.hidden ? 'Show the court check picture' : 'Hide the court check picture';
});

wireVideo();
byId('open-excel').addEventListener('click', showStatsPath);
byId('our-team').addEventListener('change', chooseOurTeam);
byId('process-start').addEventListener('click', function () { startProcessing(this); });
byId('court-process').addEventListener('click', function () { startProcessing(this); });
byId('process-stop').addEventListener('click', function () { stopProcessing(this); });
byId('log-toggle').addEventListener('click', function () {
  const log = byId('processing-log');
  log.hidden = !log.hidden;
  this.textContent = log.hidden ? 'Details' : 'Hide the details';
});

setup().catch(function (problem) { toast(problem.message, 'bad'); byId('game-line').textContent = problem.message; });
