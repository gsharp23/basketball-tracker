/* The Dataset screen (3 · Dataset): the split, the variations checklist, "Build version N" and the versions table.
   Everything comes from GET /api/models/dataset (src/api_models.py); the build runs inside the dashboard and the
   page asks every second how far it is. */

renderSidebar('dataset');


let data = null;            // the last answer of /api/models/dataset
let stopPolling = null;

/* ---- the split card ---- */

function showSplit() {
  const counts = data.plan.split.counts;
  const total = counts.train + counts.val + counts.test;
  const bar = byId('split-bar');
  const parts = [['part-train', 'Train', counts.train], ['part-val', 'Check', counts.val], ['part-test', 'Test', counts.test]];
  bar.replaceChildren(...parts.map(function (part) {
    const share = total ? Math.round(100 * part[2] / total) : (part[1] === 'Train' ? 70 : 15);
    return el('div', { class: part[0], style: 'flex: ' + Math.max(share, 1), title: plural(part[2], 'picture') }, part[1] + ' ' + share + '%');
  }));
  const split = data.plan.split;
  const words = byId('split-words');
  if (split.by_game) {
    const game = data.games.find(function (row) { return row.game === split.test_game; });
    words.replaceChildren('The test pictures come from one game kept aside: ', el('b', {}, game ? game.words : split.test_game),
                          '. The model never sees it in training, so its score is honest.');
  } else {
    words.textContent = split.explanation;
  }
  // the checkbox and the game list
  const keep = byId('keep-game');
  keep.checked = Boolean(data.project.test_game);
  const field = byId('test-game-field');
  field.hidden = !keep.checked;
  const select = byId('test-game');
  select.replaceChildren(...data.games.filter(function (row) { return row.checked > 0 || row.game === data.project.test_game; })
    .map(function (row) {
      return el('option', { value: row.game, selected: row.game === data.project.test_game },
                row.words + (row.date ? ', ' + fmtDate(row.date) : '') + ' (' + plural(row.checked, 'checked picture') + ')');
    }));
}

// Without a choice, keep the game with the fewest checked pictures: that leaves the most for training.
function smallestGame() {
  const games = data.games.filter(function (row) { return row.checked > 0; });
  games.sort(function (a, b) { return a.checked - b.checked; });
  return games.length ? games[0].game : null;
}

async function saveTestGame(game) {
  await tryToast(async function () {
    data = await putJSON('/api/models/test-game', { game: game });
    showAll();
  });
}

byId('keep-game').addEventListener('change', function () {
  if (byId('keep-game').checked) {
    const game = smallestGame();
    if (!game) {
      toast('No game has checked pictures yet. Check some on 2 · Annotate first.', 'bad');
      byId('keep-game').checked = false;
      return;
    }
    saveTestGame(game);
  } else {
    saveTestGame(null);
  }
});
byId('test-game').addEventListener('change', function () { saveTestGame(byId('test-game').value); });

/* ---- the variations ---- */

function tickedVariations() {
  return Array.from(document.querySelectorAll('#variations input:checked')).map(function (box) { return box.value; });
}

function showVariations() {
  // start from what the last version used, else the usual four
  const last = data.versions.length ? data.versions[0].augmentations : null;
  const box = byId('variations');
  box.replaceChildren(...data.variations.map(function (item) {
    const on = last ? last.indexOf(item.key) >= 0 : item.on;
    const input = el('input', { type: 'checkbox', value: item.key, checked: on });
    input.addEventListener('change', showPicturesLine);
    return el('label', { class: 'check check-box' }, [input, item.label]);
  }));
  showPicturesLine();
}

function showPicturesLine() {
  const ticked = tickedVariations().length;
  const train = data.plan.split.counts.train;
  const about = train * (1 + ticked);
  // The zip holds the checked pictures only; the variations are made on Colab while it trains, from the list in the version.
  byId('pictures-line').textContent = 'Version ' + data.plan.number + ' would hold ' + plural(data.plan.checked, 'checked picture') +
    (ticked ? '. The ' + plural(ticked, 'variation') + ' ticked are made on Colab while it trains (the zip stays ' + plural(data.plan.checked, 'picture') +
              '), so the model sees about ' + about.toLocaleString() + ' training pictures.' : ', with no variations.');
}

/* ---- the build ---- */

function showBuild(state) {
  const card = byId('build-card');
  const running = state && state.running;
  card.hidden = !running && !(state && state.error);
  byId('build-button').disabled = Boolean(running);
  byId('build-button').textContent = running ? 'Building...' : 'Build version ' + data.plan.number;
  if (!state) return;
  if (running) {
    const share = state.total ? state.done / state.total : 0;
    byId('build-bar').style.width = Math.round(100 * (state.checking ? 1 : share)) + '%';
    byId('build-title').textContent = state.checking ? 'Checking the zip reads back...' : 'Building version ' + data.plan.number + '...';
    byId('build-detail').textContent = state.total ? state.done + ' of ' + state.total + ' pictures packed' : 'Starting...';
    byId('build-left').textContent = state.checking ? '' : state.seconds_left === null || state.seconds_left === undefined ? ''
      : (state.seconds_left < 60 ? 'under a minute left' : 'about ' + Math.round(state.seconds_left / 60) + ' min left');
  } else if (state.error) {
    byId('build-title').textContent = 'The version was not built';
    byId('build-detail').textContent = state.error;
    byId('build-left').textContent = '';
    byId('build-bar').style.width = '0%';
  }
}

async function startBuild() {
  if (data.checked < data.min_pictures) {
    toast('Only ' + plural(data.checked, 'picture') + ' checked so far. Check at least ' + data.min_pictures + ' on 2 · Annotate first.', 'bad');
    return;
  }
  const ok = await tryToast(function () { return postJSON('/api/models/build', { augmentations: tickedVariations() }); });
  if (!ok) return;
  watchBuild();
}

function watchBuild() {
  if (stopPolling) stopPolling();
  let announced = false;
  stopPolling = pollUntil('/api/models/build', function (state) {
    showBuild(state);
    if (!state.running && state.result && !announced) {
      announced = true;             // pollUntil calls this once more with the final state: one toast, not two
      toast('Version ' + state.result.number + ' built: ' + plural(state.result.pictures, 'picture') + ', ' + state.result.megabytes + ' MB.', 'good');
      load();
    }
  }, function (state) { return !state.running; }, 1000);
}

byId('build-button').addEventListener('click', startBuild);

/* ---- the versions table ---- */

function showVersions() {
  const rows = byId('version-rows');
  byId('versions-empty').hidden = data.versions.length > 0;
  rows.replaceChildren(...data.versions.map(function (version) {
    const model = version.model
      ? el('a', { href: 'models.html' }, 'Model ' + version.model.label + (version.model.ball === null ? '' : ' · ' + version.model.ball + '%'))
      : el('span', { class: 'muted' }, 'Not trained');
    const built = version.built ? fmtDate(version.built.slice(0, 10)) : '';
    const test = version.test_game ? el('div', { class: 'small muted' }, 'test game kept aside') : null;
    return el('tr', {}, [
      el('td', { class: 'num' }, 'v' + version.number),
      el('td', {}, built),
      el('td', {}, [String(version.pictures), test]),
      el('td', { title: (version.games || []).join(', ') }, (version.games || []).length + ' · ' + (version.gyms === null || version.gyms === undefined ? '?' : version.gyms)),
      el('td', {}, version.variations),
      el('td', {}, model),
      el('td', {}, version.zip_url
        ? el('a', { class: 'btn btn-outline btn-sm', href: version.zip_url, download: 'dataset_v' + version.number + '.zip', title: version.zip_path || '' }, 'Download zip')
        : el('span', { class: 'muted small' }, 'zip missing')),
    ]);
  }));
}

/* ---- the page ---- */

function showAll() {
  showSplit();
  showVariations();
  showVersions();
  showBuild(data.build);
  if (data.build.running) watchBuild();
}

async function load() {
  try {
    data = await getJSON('/api/models/dataset');
  } catch (problem) {
    toast(problem.message, 'bad');
    byId('split-words').textContent = problem.message;
    return;
  }
  showAll();
}

load();
