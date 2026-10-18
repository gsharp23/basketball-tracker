/* Improve the detector: six steps, top to bottom, one live button at a time. Data: GET /api/process/improve.
   Step 1 opens the Upload options, step 2 the Annotate screen, step 3 the Dataset options, step 4 the Train
   options (Models screen); steps 5 and 6 post straight from here. The page refreshes itself while something runs. */

renderSidebar('improve');

let timer = null;

function progressBar(progress) {
  const bar = el('div', { class: 'progress progress-green' }, el('span', { style: 'width: ' + Math.round(100 * (progress.how_far || 0)) + '%' }));
  return el('div', {}, [bar, progress.words ? el('div', { class: 'small muted' }, progress.words) : null]);
}

async function post(action, button) {
  button.disabled = true;
  const words = button.textContent;
  button.textContent = 'Working...';
  const reply = await tryToast(function () { return postJSON(action.path, action.body || {}); });
  if (reply && reply.message) toast(reply.message, 'good');
  else if (reply && reply.job) toast('Started.', 'good');
  else if (reply) toast('Done.', 'good');
  button.disabled = false;
  button.textContent = words;
  await load();
}

function actionNode(step, words, action, primary) {
  const cls = 'btn ' + (primary ? 'btn-yellow' : 'btn-outline');
  if (!action) return el('button', { class: cls + ' greyed', type: 'button', disabled: true }, words);
  if (action.kind === 'link') return el('a', { class: cls, href: action.href }, words);
  if (action.kind === 'colab') return el('a', { class: cls, href: action.href, target: '_blank', rel: 'noopener' }, words);
  return el('button', { class: cls, type: 'button', onclick: function () { post(action, this); } }, words);
}

function stepRow(step, current) {
  const classes = ['step-row', step.state === 'done' ? 'done' : '', step.state === 'locked' ? 'locked' : '', step.number === current ? 'current' : ''].filter(Boolean).join(' ');
  const title = el('div', { class: 'step-title' }, ['Step ' + step.number + ' · ' + step.title, step.number === current ? el('span', { class: 'here' }, 'You are here') : null]);
  const text = el('div', {}, [title, el('div', { class: 'step-text' }, step.explanation),
    step.progress ? progressBar(step.progress) : null,
    step.note ? el('div', { class: 'text-orange small', style: 'margin-top: 4px' }, step.note) : null]);
  const side = el('div', { class: 'step-side' });
  if (step.state === 'done') {
    side.appendChild(el('span', { class: 'text-green' }, 'Done'));
    if (step.done_link) {
      if (step.done_link.href.indexOf('#score-') === 0) {
        const number = Number(step.done_link.href.slice(7));
        side.appendChild(el('a', { class: 'small', href: '#', onclick: function (event) { event.preventDefault(); post({ path: '/api/process/score', body: { version: number } }, this); } }, step.done_link.words));
      } else {
        side.appendChild(el('a', { class: 'small', href: step.done_link.href }, step.done_link.words));
      }
    }
  } else if (step.state === 'busy') {
    side.appendChild(el('button', { class: 'btn btn-yellow greyed', type: 'button', disabled: true }, step.button));
  } else if (step.state === 'waiting') {
    side.appendChild(el('span', { class: 'muted strong' }, 'Waiting for Colab'));
  } else if (step.state === 'live') {
    if (step.second) side.appendChild(actionNode(step, step.second.words, step.second.action, false));
    side.appendChild(actionNode(step, step.button, step.action, true));
  } else {
    side.appendChild(actionNode(step, step.button, null, false));
    side.appendChild(el('div', { class: 'locked-reason' }, step.locked_reason || 'Locked.'));
  }
  return el('div', { class: classes }, [el('div', { class: 'circle' }, step.state === 'done' ? '✓' : String(step.number)), text, side]);
}

async function load() {
  const data = await api('/api/process/improve');
  byId('status-step').textContent = data.status_line;
  const block = byId('status-block');
  block.textContent = data.block.text;
  block.className = 'line ' + (data.block.ok ? 'text-green' : 'text-orange');
  byId('head-counts').replaceChildren(
    el('span', {}, el('b', {}, data.head.confirmed)), el('span', {}, data.head.versions), el('span', {}, data.head.models));
  const holder = byId('steps');
  holder.replaceChildren.apply(holder, data.steps.map(function (step) { return stepRow(step, data.current); }));
  clearTimeout(timer);
  if (data.steps.some(function (step) { return step.state === 'busy' || step.state === 'waiting'; })) timer = setTimeout(function () { load().catch(function () {}); }, 5000);
}

load().catch(function (problem) { toast(problem.message, 'bad'); byId('status-step').textContent = problem.message; });
