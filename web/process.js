/* Process a game: one game's five steps, top to bottom, with one live button at a time.
   Data: GET /api/process/games (the dropdown) and GET /api/process/game/<stem> (the steps). "Send to Colab" posts
   to /api/drive/send/<stem> and the page polls while the copy runs. */

renderSidebar('process');

let games = [];
let stem = urlParam('game') || remembered('process_game') || '';
let timer = null;

function stepRow(step, current) {
  const classes = ['step-row', step.state === 'done' ? 'done' : '', step.state === 'locked' ? 'locked' : '', step.number === current ? 'current' : ''].filter(Boolean).join(' ');
  const title = el('div', { class: 'step-title' }, ['Step ' + step.number + ' · ' + step.title, step.number === current ? el('span', { class: 'here' }, 'You are here') : null]);
  const text = el('div', {}, [title, el('div', { class: 'step-text' }, step.explanation), step.note ? el('div', { class: 'text-orange small', style: 'margin-top: 4px' }, step.note) : null]);
  const side = el('div', { class: 'step-side' });
  if (step.state === 'done') {
    side.appendChild(el('span', { class: 'text-green' }, 'Done'));
    if (step.done_link) side.appendChild(el('a', { class: 'small', href: step.done_link.href, target: step.done_link.href.indexOf('http') === 0 ? '_blank' : null, rel: 'noopener' }, step.done_link.words));
  } else if (step.state === 'busy') {
    side.appendChild(el('button', { class: 'btn btn-yellow greyed', type: 'button', disabled: true }, step.button));
  } else if (step.state === 'waiting') {
    side.appendChild(el('span', { class: 'muted strong' }, 'Waiting'));
  } else if (step.state === 'live') {
    side.appendChild(actionButton(step));
  } else {
    side.appendChild(el('button', { class: 'btn greyed', type: 'button', disabled: true }, step.button || buttonWords(step)));
    side.appendChild(el('div', { class: 'locked-reason' }, step.locked_reason || 'Locked.'));
  }
  return el('div', { class: classes }, [el('div', { class: 'circle' }, step.state === 'done' ? '✓' : String(step.number)), text, side]);
}

function buttonWords(step) {
  return { 2: 'Mark the court', 3: 'Send to Colab', 4: 'Open Colab', 5: 'Waiting' }[step.number] || '';
}

function actionButton(step) {
  const action = step.action || {};
  if (action.kind === 'link') return el('a', { class: 'btn btn-yellow', href: action.href }, step.button);
  if (action.kind === 'colab') return el('a', { class: 'btn btn-yellow', href: action.href, target: '_blank', rel: 'noopener' }, step.button);
  return el('button', { class: 'btn btn-yellow', type: 'button', onclick: function () { post(action.path, this); } }, step.button);
}

async function post(path, button) {
  button.disabled = true;
  button.textContent = 'Sending...';
  const reply = await tryToast(function () { return postJSON(path); });
  if (reply && reply.message) toast(reply.message, 'good');
  await load();
}

function showGame(data) {
  byId('status-step').textContent = data.status_line;
  const block = byId('status-block');
  block.textContent = data.block.text;
  block.className = 'line ' + (data.block.ok ? 'text-green' : 'text-orange');
  const holder = byId('steps');
  holder.replaceChildren.apply(holder, data.steps.map(function (step) { return stepRow(step, data.current); }));
  document.title = data.game.title + ' · Process a game · Hoops Tracker';
  // While a copy or Colab is going, the page refreshes itself.
  clearTimeout(timer);
  const busy = data.steps.some(function (step) { return step.state === 'busy' || step.state === 'waiting'; });
  if (busy) timer = setTimeout(function () { load().catch(function () {}); }, busy && data.steps[2].state === 'busy' ? 3000 : 30000);
}

async function fillPicker() {
  const reply = await api('/api/process/games');
  games = [];
  reply.groups.forEach(function (group) { group.games.forEach(function (row) { games.push(row); }); });
  const pick = byId('game-pick');
  pick.replaceChildren.apply(pick, games.map(function (row) {
    return el('option', { value: row.stem }, row.title + (row.done ? ' · done' : ' · step ' + row.step + ' of 5'));
  }));
  if (!games.length) { pick.appendChild(el('option', { value: '' }, 'No game video yet')); return; }
  if (!games.some(function (row) { return row.stem === stem; })) {
    const todo = games.filter(function (row) { return !row.done; });
    stem = (todo[0] || games[0]).stem;
  }
  pick.value = stem;
}

async function load() {
  if (!stem) { byId('status-step').textContent = 'Add a game video first (Games tab).'; return; }
  remember('process_game', stem);
  const data = await api('/api/process/game/' + encodeURIComponent(stem));
  showGame(data);
}

byId('game-pick').addEventListener('change', function () {
  stem = byId('game-pick').value;
  history.replaceState(null, '', withParams('process.html', { game: stem }));
  load().catch(function (problem) { toast(problem.message, 'bad'); });
});

fillPicker().then(load).catch(function (problem) {
  toast(problem.message, 'bad');
  byId('status-step').textContent = problem.message;
});
