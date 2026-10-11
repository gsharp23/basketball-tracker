/* The Model training overview: the project, the five steps with live counts, dataset health and the next steps.
   Everything comes from GET /api/training/overview. */

renderSidebar('overview');

function num(n) { return Number(n || 0).toLocaleString('en-US'); }

function showSteps(info) {
  const steps = info.steps, strip = byId('steps').children;
  // 1 Upload
  byId('step-upload').textContent = num(steps.upload.pictures);
  byId('step-upload-what').textContent = steps.upload.pictures
    ? 'pictures from ' + plural(steps.upload.games, 'game') : 'no pictures yet';
  strip[0].classList.add(steps.upload.pictures ? 'step-done' : 'step-now');
  // 2 Annotate
  byId('step-annotate').replaceChildren(el('span', {}, num(steps.annotate.checked)),
    el('span', { class: 'sub' }, 'of ' + num(steps.annotate.pictures)));
  if (steps.annotate.pictures) strip[1].classList.add(steps.annotate.checked === steps.annotate.pictures ? 'step-done' : 'step-now');
  // 3 Dataset
  if (steps.dataset.version) {
    byId('step-dataset').textContent = 'v' + steps.dataset.version;
    byId('step-dataset-what').textContent = steps.dataset.built ? 'last built ' + fmtDate(String(steps.dataset.built).slice(0, 10)) : 'built';
    strip[2].classList.add('step-done');
  } else {
    byId('step-dataset').textContent = '—';
  }
  // 5 Models
  const model = steps.models;
  byId('step-models').replaceChildren(el('span', { class: 'name', title: model.name }, model.label), el('span', { class: 'ok' }, 'in use'));
  byId('step-models-what').textContent = model.ball_recall !== null && model.ball_recall !== undefined
    ? 'ball found ' + fmtPct(model.ball_recall <= 1 ? model.ball_recall * 100 : model.ball_recall)
    : 'not scored yet';
  strip[4].classList.add(model.scored ? 'step-done' : 'step-todo');
}

function showHealth(info) {
  const rows = byId('classes');
  rows.replaceChildren(...info.classes.map(function (item) {
    const rare = item.name !== 'Player' && item.share < 0.2;
    return el('div', {}, [
      el('div', { class: 'bar-row' }, [el('span', { class: 'bar-label' }, item.name), el('span', { class: 'bar-value' }, num(item.count))]),
      el('div', { class: 'progress' + (rare ? ' progress-rare' : '') }, el('span', { style: 'width:' + Math.round(item.share * 100) + '%' })),
    ]);
  }));
  byId('conditions').replaceChildren(...info.conditions.map(function (item) {
    const value = item.need_more ? num(item.count) + ' · need more' : num(item.count);
    return el('div', {}, [el('span', {}, item.name), el('span', { class: 'value' + (item.need_more ? ' need-more' : '') }, value)]);
  }));
  byId('health-note').textContent = info.note;
  const missing = info.counts.pictures - info.assisted;
  const note = byId('assist-note');
  if (missing > 0 && info.counts.pictures) {
    note.hidden = false;
    note.replaceChildren(plural(missing, 'picture') + ' have no first boxes from the model yet. ',
      el('a', { href: 'upload.html' }, 'Run label assist on the Upload screen'), '.');
  } else {
    note.hidden = true;
  }
}

function showNextSteps(info) {
  byId('next-steps').replaceChildren(...info.next_steps.map(function (step, index) {
    return el('a', { href: step.href, class: 'lead-row boxed' }, [
      el('span', { class: 'lead-number ' + (index === 0 ? 'yellow' : 'grey') }, String(index + 1)),
      el('div', { class: 'lead-text' }, [el('div', { class: 'name' }, step.title), el('div', { class: 'what' }, step.what)]),
    ]);
  }));
}

function showSubtitle(info) {
  const project = info.project;
  const words = ['Project', project.classes.length + ' classes: ' + project.classes.join(', ')];
  if (project.games) words.push(plural(project.games, 'game') + (project.gyms ? ' from ' + plural(project.gyms, 'gym') : ''));
  byId('subtitle').textContent = words.join(' · ');
}

async function load() {
  const info = await api('/api/training/overview');
  showSubtitle(info);
  showSteps(info);
  showHealth(info);
  showNextSteps(info);
  if (info.job.running) {
    toast('Pictures are being taken from a game right now: see the Upload screen.', '');
  }
}

tryToast(load);
