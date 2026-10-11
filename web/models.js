/* The Train and Models screen (4 · Train and 5 · Models): send a dataset version to Drive, open Colab, watch the
   Colab line, bring the model back; the score-by-version chart, the models table ("Use this one", "Try on a clip")
   and where the model still struggles. Everything comes from GET /api/models/ (src/api_models.py). */

renderSidebar('models');
// Both steps live on this page, so "4 · Train" lights up too.
document.querySelectorAll('#side-bar .step-link').forEach(function (link) {
  if (link.getAttribute('href') === 'models.html#train') link.classList.add('active');
});


let data = null;
let stopTry = null;
let stopSend = null;

/* ---- the training card ---- */

function showChoices() {
  const versions = byId('dataset-version');
  const chosenVersion = versions.value;
  versions.replaceChildren(...data.versions.filter(function (v) { return v.has_zip; }).map(function (v) {
    return el('option', { value: v.number, selected: String(v.number) === chosenVersion },
              'Version ' + v.number + ' (' + plural(v.pictures, 'picture') + (v.test_game ? ', test game kept aside' : '') + ')');
  }));
  if (!versions.options.length) versions.appendChild(el('option', { value: '' }, 'No version built yet'));
  const start = byId('start-from');
  const chosenStart = start.value;
  const options = [el('option', { value: 'fresh' }, 'Fresh small model')];
  data.models.forEach(function (model) {
    options.push(el('option', { value: model.name, selected: model.name === chosenStart },
                    (model.downloaded ? model.label + ' (the downloaded model)' : 'Model ' + model.label + ' (continue learning)')));
  });
  start.replaceChildren(...options);
  byId('colab-link').href = data.colab_url;
  byId('notebook-link').setAttribute('download', data.notebook_name);
  byId('send-button').disabled = !byId('dataset-version').value;
}

function showColab() {
  const line = byId('colab-line');
  line.replaceChildren(el('span', { class: 'dot dot-' + data.colab.dot }), data.colab.line);
  line.title = data.colab.updated ? 'Last news ' + data.colab.updated.replace('T', ' ') : '';
  const back = byId('bring-back');
  back.hidden = !data.waiting_in_drive.length;
  back.replaceChildren(...data.waiting_in_drive.map(function (model) {
    const button = el('button', { class: 'btn btn-green btn-big', type: 'button' }, '3 · Bring back ' + model.name);
    button.addEventListener('click', function () { bringBack(model.name, button); });
    return el('div', { class: 'stack' }, [button, model.has_card ? null : el('span', { class: 'hint' }, 'Its score file is not there yet: wait for the notebook to finish.')]);
  }));
}

async function bringBack(name, button) {
  button.disabled = true;
  button.textContent = 'Copying ' + name + '...';
  const reply = await tryToast(function () { return postJSON('/api/models/fetch', { name: name }); });
  if (reply) {
    data = reply;
    toast(name + ' is in the models folder now. Press "Use this one" to make it the detector.', 'good');
    showAll();
  } else {
    button.disabled = false;
    button.textContent = '3 · Bring back ' + name;
  }
}

function sendStatus(node, kind) {
  const box = byId('send-status');
  box.hidden = false;
  box.className = kind === 'bad' ? 'callout callout-red' : kind === 'plain' ? 'callout callout-grey' : 'callout';
  box.replaceChildren(node);
}

async function sendToDrive() {
  const version = byId('dataset-version').value;
  if (!version) { toast('Build a version on the Dataset screen first.', 'bad'); return; }
  const reply = await tryToast(function () {
    return postJSON('/api/models/send', { version: Number(version), base: byId('start-from').value, length: byId('length').value });
  });
  if (!reply) return;
  if (!reply.sent) {
    // Drive is not connected: say where the zip is, so the coach can copy it himself.
    sendStatus(el('div', { class: 'stack' }, [
      el('div', {}, reply.message),
      el('div', {}, ['The zip is here: ', el('b', {}, reply.zip_path)]),
      el('div', {}, 'Copy it into the "' + reply.drive_folder + '" folder of your Google Drive, then press "2 · Open Colab and train". ' +
                    'Remember: the pictures show players, and this is the step where they leave the laptop.'),
    ]), 'plain');
    return;
  }
  watchSend();
}

function watchSend() {
  if (stopSend) stopSend();
  byId('send-button').disabled = true;
  stopSend = pollUntil('/api/models/send', function (state) {
    if (state.running) {
      const share = state.total ? Math.round(100 * state.done / state.total) : 0;
      sendStatus(el('div', { class: 'stack' }, [
        el('div', {}, state.step || 'Copying to Drive...'),
        el('div', { class: 'progress progress-red' }, el('span', { style: 'width: ' + share + '%' })),
        el('div', { class: 'small muted' }, state.total ? fmtBytes(state.done) + ' of ' + fmtBytes(state.total) : ''),
      ]));
    } else if (state.error) {
      sendStatus(el('div', {}, state.error), 'bad');
      byId('send-button').disabled = false;
    } else if (state.result) {
      sendStatus(el('div', {}, ['In your Drive now: training/' + state.result.zip +
        (state.result.base_copied ? ' and the model to start from' : '') + ' (' + state.result.folder + '). ' +
        'Google Drive for desktop uploads it in the background; when it is there, press "2 · Open Colab and train". ' +
        'The pictures show players: this is the step where they leave the laptop.']));
      byId('send-button').disabled = false;
      refreshColab();
    }
  }, function (state) { return !state.running; }, 1000);
}

byId('send-button').addEventListener('click', sendToDrive);

/* ---- the chart: one group of three bars per scored model ---- */

const SVG = 'http://www.w3.org/2000/svg';
const BAR_CLASSES = [['Ball', '#d81e27'], ['Referee', '#4fa3ff'], ['Player', '#15171c']];

function shape(parent, tag, attrs, text) {
  const node = document.createElementNS(SVG, tag);
  for (const name in attrs) node.setAttribute(name, attrs[name]);
  if (text !== undefined) node.textContent = text;
  parent.appendChild(node);
  return node;
}

function showChart() {
  const chart = byId('chart');
  chart.replaceChildren();
  const top = 20, bottom = 180, left = 60, right = 740;
  [[bottom, '0%'], [(top + bottom) / 2, '50%'], [top, '100%']].forEach(function (line) {
    shape(chart, 'line', { x1: left, y1: line[0], x2: right, y2: line[0], stroke: '#e3e5e9' });
    shape(chart, 'text', { x: left - 10, y: line[0] + 4, 'text-anchor': 'end', 'font-size': 11, fill: '#5a5f6a', 'font-family': 'Barlow, sans-serif' }, line[1]);
  });
  if (!data.chart.length) {
    shape(chart, 'text', { x: (left + right) / 2, y: 105, 'text-anchor': 'middle', 'font-size': 15, fill: '#5a5f6a', 'font-family': 'Barlow, sans-serif' },
          'No scored model yet. Train one on Colab and bring it back; its scores appear here.');
    return;
  }
  const groupWidth = (right - left) / data.chart.length;
  const barWidth = Math.min(40, Math.floor((groupWidth - 20) / 3) - 6);
  data.chart.forEach(function (group, index) {
    const centre = left + groupWidth * (index + 0.5);
    const startX = centre - (3 * barWidth + 2 * 6) / 2;
    BAR_CLASSES.forEach(function (cls, k) {
      const value = group.scores[cls[0]];
      if (value === null || value === undefined) return;
      const height = Math.max(2, Math.round((bottom - top) * value / 100));
      const bar = shape(chart, 'rect', { x: startX + k * (barWidth + 6), y: bottom - height, width: barWidth, height: height, fill: cls[1] });
      shape(bar, 'title', {}, group.label + ': ' + cls[0] + ' found ' + value + '%');
    });
    shape(chart, 'text', { x: centre, y: 205, 'text-anchor': 'middle', 'font-size': 12, 'font-weight': group.in_use ? 700 : 400,
                           fill: group.in_use ? '#15171c' : '#5a5f6a', 'font-family': 'Barlow, sans-serif' },
          group.label + (group.in_use ? ' (in use)' : ''));
  });
}

/* ---- the models table ---- */

function pct(value, warn) {
  if (value === null || value === undefined) return el('span', { class: 'muted' }, '—');
  const cls = value >= 75 ? 'good' : value < 65 && warn ? 'bad' : '';
  return el('span', { class: cls }, value + '%');
}

function trainedWords(model) {
  if (model.downloaded) return el('span', { class: 'muted' }, 'Downloaded model');
  return fmtDate(String(model.trained).slice(0, 10));     // the card's date, with or without a time of day
}

function speedWords(model) {
  if (model.speed_fps === null || model.speed_fps === undefined) return el('span', { class: 'muted' }, 'not measured');
  const tried = model.tried_on || {};
  const size = model.picture_size || tried.picture_size;
  const words = model.speed_fps.toFixed(1) + ' frames/s' + (size ? ' at ' + size + ' wide' : '');
  const found = tried.found_in_frames ? Object.keys(tried.found_in_frames).map(function (kind) {
    return kind + ' in ' + tried.found_in_frames[kind] + ' of ' + tried.frames + ' frames';
  }).join(', ') : '';
  return el('span', { title: found ? 'On ' + tried.clip + ': ' + found : '' }, words);
}

function showModels() {
  const rows = byId('model-rows');
  if (!data.models.length) {
    rows.replaceChildren(el('tr', {}, el('td', { colspan: 10, class: 'muted' }, 'No model file in the models folder.')));
    return;
  }
  rows.replaceChildren(...data.models.map(function (model) {
    // A downloaded model: its plain name ("Player finder"), the file name as the tooltip, "Before training" under it.
    const name = el('td', { class: 'num model', title: model.name }, [model.label,
      model.in_use ? el('span', { class: 'chip chip-dark', style: 'margin-left: 6px' }, 'In use') : null,
      model.downloaded ? el('div', { class: 'small muted', style: 'font-weight: 400' }, 'Before training') : null]);
    const actions = [];
    if (model.in_use) {
      actions.push(button('Try on a clip', function () { tryOnClip(model.name); }));
    } else {
      actions.push(button('Use this one', function () { useModel(model.name); }));
      const link = el('a', { href: '#', class: 'small-link' }, 'Try on a clip');
      link.addEventListener('click', function (event) { event.preventDefault(); tryOnClip(model.name); });
      actions.push(link);
    }
    const tr = el('tr', { class: model.in_use ? 'shaded' : null }, [
      name,
      el('td', { class: 'trained' }, trainedWords(model)),
      el('td', { class: model.dataset_version ? '' : 'muted' }, model.dataset_version ? 'v' + model.dataset_version
         : model.scored_on_dataset ? 'scored on v' + model.scored_on_dataset : '—'),
      el('td', { class: 'score' }, pct(model.scores.Ball, true)),
      el('td', { class: 'score' }, pct(model.ball_dim, true)),
      el('td', { class: 'score' }, pct(model.scores.Referee)),
      el('td', { class: 'score' }, pct(model.scores.Player)),
      el('td', { class: 'score' }, pct(model.scores.Hoop)),
      el('td', { class: 'speed' }, speedWords(model)),
      el('td', {}, el('div', { class: 'stack' }, actions)),      // the button over the link, so the table fits a 1280 screen
    ]);
    if (model.notes && model.notes.length) tr.title = model.notes.join(' ');
    return tr;
  }));
}

function button(words, onClick) {
  const node = el('button', { class: 'btn btn-outline btn-sm', type: 'button' }, words);
  node.addEventListener('click', onClick);
  return node;
}

async function useModel(name) {
  const reply = await tryToast(function () { return postJSON('/api/models/use', { name: name }); });
  if (!reply) return;
  toast(name + ' is the detector in use now. The next game processed finds players with it.', 'good');
  await load();
}

async function tryOnClip(name) {
  if (!data.test_clip) { toast('The test clip is not in the clips folder, so there is nothing to try the model on.', 'bad'); return; }
  const reply = await tryToast(function () { return postJSON('/api/models/try', { name: name }); });
  if (!reply) return;
  watchTry(name);
}

function watchTry(name) {
  if (stopTry) stopTry();
  const status = byId('try-status');
  status.hidden = false;
  status.textContent = 'Trying ' + (name || 'the model') + ' on the test clip...';
  stopTry = pollJob('try_model', function (info) {
    const progress = info.progress || {};
    if (info.running || info.starting) {
      status.textContent = 'Trying ' + (name || 'the model') + ' on the test clip: ' +
        (progress.detail || (info.lines && info.lines.length ? info.lines[info.lines.length - 1].trim() : 'loading the model...'));
    } else {
      const last = (info.lines || []).filter(function (line) { return line.indexOf('Speed on this laptop') === 0 || line.indexOf('Found in frames') === 0; });
      status.textContent = info.exit_code === 0 || last.length ? last.join(' ') : ((info.lines || []).slice(-2).join(' ') || 'The speed test stopped.');
      if (info.exit_code === 0 || last.length) toast('Speed measured and written to the model\'s score file.', 'good');
      load();
    }
  }, 2000);
}

/* ---- where it struggles ---- */

function showStruggles() {
  const box = byId('struggles');
  const items = data.struggles.items;
  box.hidden = !items.length;
  if (!items.length) return;
  byId('struggles-words').textContent = items.map(function (item) { return item.words + ' ' + item.percent + '%'; }).join(' · ') +
    (data.struggles.model ? ' (model ' + data.struggles.model + ')' : '');
  const worst = items.find(function (item) { return item.condition; });
  const words = { dim: 'dim-gym', blur: 'blurred', wide: 'zoomed-out' };
  byId('struggles-link').textContent = worst ? 'Label more ' + words[worst.condition] + ' pictures →' : 'Label more pictures →';
}

/* ---- the page ---- */

function showAll() {
  showChoices();
  showColab();
  showChart();
  showModels();
  showStruggles();
  if (data.try_job && (data.try_job.running || data.try_job.starting)) watchTry(null);
  if (data.send && data.send.running) watchSend();
}

async function load() {
  try {
    data = await getJSON('/api/models/');
  } catch (problem) {
    toast(problem.message, 'bad');
    return;
  }
  showAll();
}

// The Colab line changes while the notebook runs in the browser's other tab: look again every 20 seconds.
async function refreshColab() {
  try {
    const fresh = await getJSON('/api/models/');
    data.colab = fresh.colab;
    data.waiting_in_drive = fresh.waiting_in_drive;
    showColab();
  } catch (problem) { /* shown on the next full load */ }
}
setInterval(refreshColab, 20000);

load();
