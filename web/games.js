/* The Games screen: a card per video in the clips folder, from GET /api/games/list.
   While a game is being processed the list is asked again every 2 seconds. */

renderHeader('games');

let editing = null;         // the name of the game whose title is being edited (the list is not redrawn meanwhile)
let pollTimer = null;

function byId(id) { return document.getElementById(id); }

/* ---- the list ---- */

async function loadGames() {
  clearTimeout(pollTimer);
  let reply;
  try {
    reply = await api('/api/games/list');
  } catch (problem) {
    byId('summary').textContent = problem.message;
    return;
  }
  byId('clips-folder').textContent = reply.clips_folder;
  if (editing === null) drawGames(reply);
  if (reply.games.some(function (game) { return game.running; })) pollTimer = setTimeout(loadGames, 2000);
}

function drawGames(reply) {
  const games = reply.games;
  const counts = { finished: 0, running: 0, 'needs the court marked': 0 };
  games.forEach(function (game) { if (game.status in counts) counts[game.status] += 1; });
  const parts = [games.length + (games.length === 1 ? ' game' : ' games')];
  if (counts.finished) parts.push(counts.finished + ' finished');
  if (counts.running) parts.push(counts.running + ' running');
  if (counts['needs the court marked']) parts.push(counts['needs the court marked'] + (counts['needs the court marked'] === 1 ? ' needs you' : ' need you'));
  byId('summary').textContent = games.length ? parts.join(' · ') : 'No videos in the clips folder yet. Press "Add a game" to see how to add one.';

  const grid = byId('games');
  grid.replaceChildren();
  games.forEach(function (game) { grid.appendChild(gameCard(game, reply)); });
  grid.appendChild(el('button', { class: 'add-card', type: 'button', onclick: showAddHelp }, [
    plusIcon(40), el('span', { class: 'add-title' }, 'Add a game'),
    el('span', { class: 'add-text' }, 'Copy the video into the clips folder, then press Refresh')]));
}

function plusIcon(size) {
  return el('span', { html: '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14"></path><path d="M5 12h14"></path></svg>' });
}

/* ---- one card ---- */

function gameCard(game, reply) {
  const info = game.info;
  const picture = el('div', { class: 'game-picture' },
    game.thumbnail_url ? el('img', { src: game.thumbnail_url, alt: '' }) : 'No picture of this video yet');
  const head = el('div', { class: 'game-head' }, [
    el('div', { class: 'stack', style: 'gap: 2px' }, [
      el('h2', { class: 'game-title' }, gameTitle(info, game.name)),
      el('div', { class: 'game-when' }, [info.date_is_guess ? 'Added ' + info.date : info.date, ' · ', fmtLength(game.seconds)]),
      el('button', { class: 'edit-link', type: 'button', onclick: function () { editTitle(game, body); } },
         info.opponent ? 'Change the opponent or date' : 'Name the opponent')]),
    statusChip(game)]);
  const body = el('div', { class: 'game-body' }, [head]);
  middlePart(game, reply).forEach(function (part) { body.appendChild(part); });
  const card = el('article', { class: 'card card-shadow game-card' + (game.status === 'needs the court marked' ? ' needs-you' : '') }, [picture, body]);
  return card;
}

function statusChip(game) {
  const words = {
    'finished': ['chip-green', 'Finished'], 'running': ['chip-blue', 'Running here'],
    'needs the court marked': ['chip-red', 'Needs you'], 'not started': ['chip-grey', 'Not started'],
    'stopped part-way': ['chip-dark', 'Stopped part-way'] };
  const chip = words[game.status] || ['chip-grey', game.status];
  return el('span', { class: 'chip ' + chip[0] }, chip[1]);
}

// What sits between the title and the buttons, plus the buttons, by status.
function middlePart(game, reply) {
  const link = withGame('game.html', game.name);
  if (game.status === 'finished') {
    return [
      el('div', { class: 'counts' }, [
        countTile(game.points, 'Points tagged'), countTile(game.players, 'Players found'), countTile(game.shots, 'Shots tagged')]),
      el('div', { class: 'game-actions' }, el('a', { class: 'btn btn-dark', href: link }, 'View results'))];
  }
  if (game.status === 'running') {
    const progress = game.progress || {};
    const step = progress.step ? (game.step_words[progress.step] || progress.step) : 'Starting up';
    const percent = progress.how_far === null || progress.how_far === undefined ? null : Math.round(100 * progress.how_far);
    const stepLine = progress.step_count ? 'Step ' + progress.step_number + ' of ' + progress.step_count : 'Getting ready: loading the programs (about half a minute)';
    return [
      el('div', { class: 'stack', style: 'gap: 8px' }, [
        el('div', { class: 'progress-row' }, [el('span', { style: 'font-weight: 700' }, step), el('span', { class: 'muted' }, progress.left || '')]),
        el('div', { class: 'progress progress-blue' }, el('span', { style: 'width: ' + (percent === null ? 3 : Math.max(3, percent)) + '%' })),
        el('div', { class: 'small muted' }, stepLine + (percent === null ? '' : ' · ' + percent + '%'))]),
      el('div', { class: 'game-actions' }, [
        el('a', { class: 'btn btn-outline', href: link }, 'See progress'),
        el('button', { class: 'btn btn-light', type: 'button', onclick: function () { stopRun(game, this); } }, 'Stop')])];
  }
  if (game.status === 'needs the court marked') {
    return [
      el('div', { class: 'game-text' }, 'Mark the court so player positions can be mapped. About 5 minutes of clicking.'),
      el('div', { class: 'game-actions' }, el('a', { class: 'btn btn-red', href: link + '#court' }, 'Mark the court'))];
  }
  if (game.status === 'stopped part-way') {
    const progress = game.progress || {};
    const where = progress.step ? ' during "' + (game.step_words[progress.step] || progress.step).toLowerCase() + '"' : '';
    // main.py wants a step again either because a run stopped, or because a file the step uses (the roster, say) changed since.
    const text = game.roster_newer
      ? 'The roster was changed after the stats were made. Carry on puts the new names into the stats (a few seconds).'
      : progress.ended === 'finished'
      ? 'The last run finished, but a file it used has changed since. Carry on redoes only the steps that need it.'
      : 'Stopped' + where + '. What was finished is kept: carry on from there.';
    return [
      el('div', { class: 'game-text' }, text),
      el('div', { class: 'game-actions' }, [
        el('button', { class: 'btn btn-red', type: 'button', onclick: function () { runHere(game, this); } }, 'Carry on'),
        el('a', { class: 'btn btn-outline', href: link }, 'Details')])];
  }
  // not started
  return [
    el('div', { class: 'game-text muted' }, 'On this laptop: ' + game.estimate + '. On Colab: about an hour, coming next.'),
    el('div', { class: 'game-actions' }, [
      el('button', { class: 'btn btn-dark', type: 'button', onclick: function () { runHere(game, this); } }, 'Run here'),
      el('span', { class: 'chip chip-blue' }, 'Colab: coming next')])];
}

function countTile(value, label) {
  return el('div', {}, [el('div', { class: 'count-value' }, value === null || value === undefined ? '–' : String(value)),
                        el('div', { class: 'count-label' }, label)]);
}

/* ---- editing the opponent and the date ---- */

function editTitle(game, body) {
  editing = game.name;
  const opponent = el('input', { type: 'text', value: game.info.opponent, maxlength: 80, placeholder: 'Who the game was against' });
  const date = el('input', { type: 'date', value: game.info.date });
  const form = el('form', { class: 'stack', style: 'gap: 10px' }, [
    el('div', { class: 'field' }, [el('label', {}, 'Against'), opponent]),
    el('div', { class: 'field' }, [el('label', {}, 'Date'), date]),
    el('div', { class: 'row' }, [
      el('button', { class: 'btn btn-green', type: 'submit' }, 'Save'),
      el('button', { class: 'btn btn-light', type: 'button', onclick: function () { editing = null; loadGames(); } }, 'Cancel')])]);
  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    const saved = await tryToast(function () {
      return putJSON('/api/games/info?video=' + encodeURIComponent(game.video), { opponent: opponent.value, date: date.value });
    });
    if (saved) { editing = null; loadGames(); }
  });
  body.replaceChildren(form);
  opponent.focus();
}

/* ---- starting and stopping the processing ---- */

async function runHere(game, button) {
  const question = 'Process "' + gameTitle(game.info, game.name) + '" on this laptop?\n\n' +
    'It takes ' + game.estimate + ' for this video. Keep the laptop plugged in and awake, and the black Hoops Tracker window open. ' +
    'You can stop at any time and carry on later; nothing finished is lost.';
  if (!window.confirm(question)) return;
  button.disabled = true;
  const started = await tryToast(function () { return postJSON('/api/jobs/start', { kind: 'run', video: game.video, where: 'laptop' }); });
  button.disabled = false;
  if (started) { toast('Started. The card shows how far it is.', 'good'); loadGames(); }
}

async function stopRun(game, button) {
  if (!window.confirm('Stop processing "' + gameTitle(game.info, game.name) + '"? What is finished is kept, and "Carry on" continues later.')) return;
  button.disabled = true;
  button.textContent = 'Stopping...';
  const reply = await tryToast(function () { return postJSON('/api/jobs/run_' + encodeURIComponent(game.name) + '/stop'); });
  if (reply) toast(reply.running ? 'It has not stopped yet; give it a moment.' : 'Stopped.', reply.running ? '' : 'good');
  loadGames();
}

/* ---- "Add a game" ---- */

function showAddHelp() {
  byId('add-help').hidden = false;
  byId('add-help').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

byId('add-game').addEventListener('click', showAddHelp);
byId('add-close').addEventListener('click', function () { byId('add-help').hidden = true; });
byId('refresh').addEventListener('click', function () { editing = null; loadGames(); });

loadGames();
