/* The Settings screen: school branding, storage, Google Drive folder, Colab, processing (court size, where to run,
   the detector model), this season's roster, the play types, and "Check everything". */

renderHeader('');


/* ---- School ---- */

async function showSchool() {
  const info = await api('/api/branding');
  byId('school-name').value = info.school_name || '';
  byId('team-name').value = info.mascot || '';
  if (info.logo_url) showLogo(info.logo_url);
}

function showLogo(url) {
  byId('logo-drop').replaceChildren(el('img', { src: url + '?t=' + Date.now(), alt: 'school logo' }),
                                    el('span', {}, 'Drop a picture to change'));
}

async function sendLogo(file) {
  if (!file) return;
  const word = byId('logo-word');
  if (word) word.textContent = 'Saving...';
  await tryToast(async function () {
    if (file.size > 5000000) throw new Error('That picture is too big: use one under 5 MB.');   // the server's rule, said here first
    let reply;
    try {
      reply = await fetch('/api/settings/logo?name=' + encodeURIComponent(file.name),
                          { method: 'PUT', headers: { 'X-Hoops': '1' }, body: file });   // the header api() adds
    } catch (problem) {
      throw new Error('Hoops Tracker is not answering. Is the black window still open?');
    }
    const data = await reply.json().catch(function () { return null; });
    if (!reply.ok) throw new Error((data && data.detail) || 'The logo could not be saved.');
    showLogo(data.logo_url);
    toast('Logo saved. It shows on every screen from now on.', 'good');
    renderHeader('');
  });
  if (word) word.textContent = 'LOGO';
}

function wireLogoDrop() {
  const zone = byId('logo-drop'), picker = byId('logo-file');
  zone.addEventListener('click', function () { picker.click(); });
  zone.addEventListener('keydown', function (event) { if (event.key === 'Enter' || event.key === ' ') picker.click(); });
  picker.addEventListener('change', function () { sendLogo(picker.files[0]); picker.value = ''; });
  zone.addEventListener('dragover', function (event) { event.preventDefault(); zone.classList.add('over'); });
  zone.addEventListener('dragleave', function () { zone.classList.remove('over'); });
  zone.addEventListener('drop', function (event) {
    event.preventDefault();
    zone.classList.remove('over');
    sendLogo(event.dataTransfer.files[0]);
  });
}

/* ---- Storage ---- */

async function showStorage() {
  const chip = byId('storage-chip');
  try {
    const storage = await api('/api/storage');
    if (!storage.attached) {
      chip.className = 'chip chip-red';
      chip.textContent = 'Not found';
      byId('storage-used').textContent = 'A data folder cannot be reached. See "If a data folder cannot be reached" in README.md.';
      return;
    }
    chip.className = 'chip chip-green';
    chip.textContent = 'In place';
    byId('storage-name').textContent = storage.folder || 'Data folder';
    if (storage.drive) byId('storage-hint').textContent = 'Videos, results and pictures are kept on this drive (' + storage.drive + '), not on the laptop\'s own drive.';
    if (storage.total) {
      byId('storage-used').textContent = fmtBytes(storage.used) + ' used of ' + fmtBytes(storage.total);
      byId('storage-bar').style.width = Math.max(1, Math.round(100 * storage.used / storage.total)) + '%';
    }
  } catch (problem) {
    chip.className = 'chip chip-red';
    chip.textContent = 'Unknown';
    byId('storage-used').textContent = problem.message;
  }
}

/* ---- Google Drive, Processing (saved together by "Save settings") ---- */

function showDriveCheck(check) {
  const chip = byId('drive-chip'), entries = byId('drive-entries');
  byId('drive-message').textContent = check.message || '';
  if (check.ok && check.path) {
    chip.className = 'chip chip-green';
    chip.textContent = 'Connected';
    entries.hidden = false;
    entries.textContent = 'In the folder: ' + (check.entries.length ? check.entries.join(', ') : 'nothing yet') +
      (check.windows_path ? ' · ' + check.windows_path : '');
  } else {
    chip.className = check.ok ? 'chip chip-grey' : 'chip chip-red';
    chip.textContent = check.ok ? 'Not connected' : 'Folder not found';
    entries.hidden = true;
  }
}

async function checkDrive() {
  const button = byId('drive-check');
  button.disabled = true;
  await tryToast(async function () {
    const check = await api('/api/settings/drive-folder?path=' + encodeURIComponent(byId('drive-path').value.trim()));
    showDriveCheck(check);
    if (check.ok && check.path) byId('drive-path').value = check.path;
  });
  button.disabled = false;
}

// What the Drive link sees (GET /api/drive): the inbox and outbox, the model in Drive, Colab's last words.
async function showDriveStatus() {
  const status = byId('drive-status'), colab = byId('colab-line');
  let drive;
  try { drive = await api('/api/drive'); } catch (problem) { return; }
  status.hidden = !drive.connected;
  if (drive.connected) {
    const parts = ['Inbox: ' + plural(drive.inbox.length, 'video'), 'Outbox: ' + plural(drive.outbox.length, 'game'),
                   'Model in Drive: ' + (drive.model ? drive.model.name : 'not yet (sent with the first game)')];
    if (drive.last_import) parts.push('Results last brought back ' + drive.last_import.when);
    status.textContent = parts.join(' · ');
  }
  if (drive.worker_log) {
    colab.replaceChildren(el('span', { class: 'dot dot-green' }), 'Last word from Colab: ' + drive.worker_log.last_line);
  } else {
    colab.replaceChildren(el('span', { class: 'dot dot-grey' }), drive.connected ? 'Not running (no word from Colab yet)' : 'Not running');
  }
}

function showModels(models, chosen) {
  const box = byId('detector-model');
  box.replaceChildren();
  if (!models.length) {
    box.appendChild(el('option', { value: chosen }, chosen + ' (the models folder cannot be read)'));
  }
  for (const model of models) {
    const words = [model.name, model.megabytes + ' MB'];
    if (model.trained) words.push('trained ' + fmtDate(String(model.trained).slice(0, 10)));
    box.appendChild(el('option', { value: model.name }, words.join(' · ')));
  }
  box.value = chosen;
}

async function showSettings() {
  const settings = await api('/api/settings');
  const sizes = byId('court-size');
  sizes.replaceChildren();
  for (const key in settings.court_sizes) {
    const size = settings.court_sizes[key];
    sizes.appendChild(el('option', { value: key }, size.label + ' · ' + size.length_ft + ' × ' + size.width_ft + ' ft'));
  }
  sizes.value = settings.court_size;
  byId('run-where').value = settings.run_where;
  byId('make-videos').checked = settings.make_videos;
  byId('drive-folder').value = settings.drive_folder;
  byId('drive-path').value = settings.drive_folder_path || '';
  byId('drive-delete').checked = settings.drive_delete_after;
  showModels(settings.models || [], settings.detector_model);
  showDriveCheck(settings.drive || { ok: true, path: '', message: '', entries: [] });
}

async function saveAll() {
  const button = byId('save-all');
  button.disabled = true;
  await tryToast(async function () {
    const reply = await putJSON('/api/settings', {
      court_size: byId('court-size').value,
      run_where: byId('run-where').value,
      make_videos: byId('make-videos').checked,
      drive_folder: byId('drive-folder').value,
      drive_folder_path: byId('drive-path').value.trim(),
      drive_delete_after: byId('drive-delete').checked,
      detector_model: byId('detector-model').value,
    });
    showDriveCheck(reply.drive);
    byId('drive-path').value = reply.drive_folder_path || '';
    showDriveStatus();
    await putJSON('/api/settings/school', { school_name: byId('school-name').value, mascot: byId('team-name').value });
    toast('Settings saved.', 'good');
    renderHeader('');
  });
  button.disabled = false;
}

/* ---- Roster: the current season's ---- */

let rosterSeason = null;

function rosterRow(number, name) {
  return el('tr', {}, [
    el('td', {}, el('input', { class: 'input', type: 'text', value: number, placeholder: 'No.', 'aria-label': 'Jersey number', inputmode: 'numeric' })),
    el('td', {}, el('input', { class: 'input', type: 'text', value: name, placeholder: 'Name', 'aria-label': 'Player name' })),
  ]);
}

async function showRoster() {
  const reply = await api('/api/seasons');
  rosterSeason = reply.seasons.find(function (season) { return season.id === reply.current; });
  byId('roster-season').textContent = '· ' + rosterSeason.name;
  byId('roster-team-name').value = rosterSeason.roster.name || '';
  const body = byId('roster-rows');
  body.replaceChildren();
  const numbers = Object.keys(rosterSeason.roster.players).sort(function (a, b) { return Number(a) - Number(b); });
  for (const number of numbers) body.appendChild(rosterRow(number, rosterSeason.roster.players[number]));
  for (let i = 0; i < 3; i++) body.appendChild(rosterRow('', ''));       // room for a few more
}

function readRoster() {
  const players = {};
  for (const row of byId('roster-rows').querySelectorAll('tr')) {
    const boxes = row.querySelectorAll('input');
    const number = boxes[0].value.trim(), name = boxes[1].value.trim();
    if (!number && !name) continue;
    if (!number) throw new Error('Every name needs a jersey number (the "No." box next to it).');
    if (number in players) throw new Error('Number ' + number + ' is listed twice.');
    players[number] = name;
  }
  return { name: byId('roster-team-name').value.trim(), players: players };
}

async function saveRoster() {
  const button = byId('roster-save');
  button.disabled = true;
  await tryToast(async function () {
    if (!rosterSeason) throw new Error('The seasons could not be read. Reload the page.');
    await putJSON('/api/seasons/' + encodeURIComponent(rosterSeason.id), { roster: readRoster() });
    toast('Roster saved for ' + rosterSeason.name + '. Names stay on this laptop.', 'good');
  });
  button.disabled = false;
}

/* ---- Play types ---- */

async function showPlayTypes() {
  const types = await api('/api/play-types');
  byId('plays-offense').value = types.offense.join('\n');
  byId('plays-defense').value = types.defense.join('\n');
}

async function savePlayTypes() {
  const button = byId('plays-save');
  button.disabled = true;
  await tryToast(async function () {
    const lines = function (id) { return byId(id).value.split('\n').map(function (line) { return line.trim(); }).filter(Boolean); };
    const types = await putJSON('/api/play-types', { offense: lines('plays-offense'), defense: lines('plays-defense') });
    byId('plays-offense').value = types.offense.join('\n');
    byId('plays-defense').value = types.defense.join('\n');
    toast('Play types saved.', 'good');
  });
  button.disabled = false;
}

/* ---- About: check everything ---- */

async function checkEverything() {
  const button = byId('check-all'), box = byId('check-results');
  button.disabled = true;
  box.hidden = false;
  box.replaceChildren(el('div', { class: 'hint' }, 'Looking at everything the tool needs...'));
  await tryToast(async function () {
    const report = await api('/api/settings/check');
    const rows = report.checks.map(function (item) {
      return el('tr', {}, [
        el('td', { style: 'width: 40px' }, el('span', { class: 'dot ' + (item.ok ? 'dot-green' : 'dot-red') })),
        el('td', { class: 'strong', style: 'white-space: nowrap' }, item.name),
        el('td', {}, item.text),
      ]);
    });
    box.replaceChildren(
      el('div', {}, el('span', { class: report.ok ? 'chip chip-green' : 'chip chip-red' }, report.ok ? 'Everything is in place' : 'Something needs attention')),
      el('div', { class: 'table-wrap' }, el('table', { class: 'table' }, el('tbody', {}, rows))));
  });
  button.disabled = false;
}

/* ---- the side nav follows the scrolling ---- */

function wireSideNav() {
  const links = Array.from(document.querySelectorAll('#side-nav a'));
  function mark() {
    let current = links[0];
    for (const link of links) {
      const section = document.querySelector(link.getAttribute('href'));
      if (section && section.getBoundingClientRect().top <= 140) current = link;
    }
    links.forEach(function (link) { link.classList.toggle('on', link === current); });
  }
  window.addEventListener('scroll', mark, { passive: true });
  mark();
}

byId('save-all').addEventListener('click', saveAll);
byId('drive-check').addEventListener('click', checkDrive);
byId('roster-save').addEventListener('click', saveRoster);
byId('roster-add').addEventListener('click', function () { byId('roster-rows').appendChild(rosterRow('', '')); });
byId('plays-save').addEventListener('click', savePlayTypes);
byId('check-all').addEventListener('click', checkEverything);
wireLogoDrop();
wireSideNav();
showSchool().catch(function (problem) { toast(problem.message, 'bad'); });
showStorage();
showSettings().catch(function (problem) { toast(problem.message, 'bad'); });
showDriveStatus();
showRoster().catch(function (problem) { toast(problem.message, 'bad'); });
showPlayTypes().catch(function (problem) { toast(problem.message, 'bad'); });
