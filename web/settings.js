/* The Settings screen: school branding, storage, Drive and Colab (coming next), processing, roster, about. */

renderHeader('settings');

function byId(id) { return document.getElementById(id); }

/* ---- School ---- */

async function showSchool() {
  const info = await api('/api/branding');
  byId('school-name').value = info.school_name || '';
  byId('team-name').value = info.mascot || '';
  if (info.logo_url) showLogo(info.logo_url);
}

function showLogo(url) {
  byId('logo-drop').replaceChildren(el('img', { src: url + '?t=' + Date.now(), alt: 'school logo' }),
                                    el('span', {}, 'Drop a file to change'));
}

async function sendLogo(file) {
  if (!file) return;
  const word = byId('logo-word');
  if (word) word.textContent = 'Saving...';
  await tryToast(async function () {
    let reply;
    try {
      reply = await fetch('/api/settings/logo?name=' + encodeURIComponent(file.name), { method: 'PUT', body: file });
    } catch (problem) {
      throw new Error('Hoops Tracker is not answering. Is the black window still open?');
    }
    const data = await reply.json().catch(function () { return null; });
    if (!reply.ok) throw new Error((data && data.detail) || 'The logo could not be saved.');
    showLogo(data.logo_url);
    toast('Logo saved. It shows on every screen from now on.', 'good');
  });
  if (word) word.textContent = '[LOGO]';
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

/* ---- Processing and Google Drive (saved together by "Save settings") ---- */

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
  byId('drive-delete').checked = settings.drive_delete_after;
}

async function saveAll() {
  const button = byId('save-all');
  button.disabled = true;
  await tryToast(async function () {
    await putJSON('/api/settings', {
      court_size: byId('court-size').value,
      run_where: byId('run-where').value,
      make_videos: byId('make-videos').checked,
      drive_folder: byId('drive-folder').value,
      drive_delete_after: byId('drive-delete').checked,
    });
    await putJSON('/api/settings/school', { school_name: byId('school-name').value, mascot: byId('team-name').value });
    toast('Settings saved.', 'good');
    renderHeader('settings');
  });
  button.disabled = false;
}

/* ---- Roster ---- */

const SIDES = [['home', 'Our team'], ['away', 'Opponent']];

function rosterRow(number, name) {
  return el('tr', {}, [
    el('td', { style: 'width: 90px' }, el('input', { class: 'input', type: 'text', value: number, placeholder: 'No.', 'aria-label': 'Jersey number', inputmode: 'numeric' })),
    el('td', {}, el('input', { class: 'input', type: 'text', value: name, placeholder: 'Name', 'aria-label': 'Player name' })),
  ]);
}

function rosterTeam(side, label, team) {
  const body = el('tbody', {});
  const numbers = Object.keys(team.players).sort(function (a, b) { return Number(a) - Number(b); });
  for (const number of numbers) body.appendChild(rosterRow(number, team.players[number]));
  for (let i = 0; i < 3; i++) body.appendChild(rosterRow('', ''));       // room for a few more
  const nameBox = el('input', { type: 'text', value: team.name, id: 'team-' + side, 'aria-label': 'Team name' });
  return el('div', { class: 'stack', 'data-side': side, style: 'gap: 10px' }, [
    el('div', { class: 'field' }, [el('label', { for: 'team-' + side }, label + ' (team name)'), nameBox]),
    el('div', { class: 'table-wrap' }, el('table', { class: 'table' }, [
      el('thead', {}, el('tr', {}, [el('th', {}, 'No.'), el('th', {}, 'Player')])), body])),
    el('button', { class: 'btn btn-light', type: 'button', onclick: function () { body.appendChild(rosterRow('', '')); } }, 'Add a row'),
  ]);
}

async function showRoster() {
  const roster = await api('/api/roster');
  const teams = SIDES.map(function (side) { return rosterTeam(side[0], side[1], roster[side[0]]); });
  byId('roster-teams').replaceChildren(teams[0], teams[1]);
}

function readRoster() {
  const roster = {};
  for (const block of document.querySelectorAll('#roster-teams [data-side]')) {
    const side = block.getAttribute('data-side');
    const players = {};
    for (const row of block.querySelectorAll('tbody tr')) {
      const boxes = row.querySelectorAll('input');
      const number = boxes[0].value.trim(), name = boxes[1].value.trim();
      if (!number && !name) continue;
      if (number in players) {
        throw new Error('Number ' + number + ' is listed twice for the ' + byId('team-' + side).value + ' team.');
      }
      players[number] = name;
    }
    roster[side] = { name: byId('team-' + side).value, players: players };
  }
  return roster;
}

async function saveRoster() {
  const button = byId('roster-save');
  button.disabled = true;
  await tryToast(async function () {
    const roster = readRoster();
    for (const side in roster) {
      for (const number in roster[side].players) {
        if (!number) throw new Error('Every name needs a jersey number (the "No." box next to it).');
      }
    }
    await putJSON('/api/roster', roster);
    toast('Roster saved on this laptop.', 'good');
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
        el('td', { style: 'font-weight: 700; white-space: nowrap' }, item.name),
        el('td', {}, item.text),
      ]);
    });
    box.replaceChildren(
      el('div', { class: report.ok ? 'chip chip-green' : 'chip chip-red' }, report.ok ? 'Everything is in place' : 'Something needs attention'),
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

byId('drive-connect').addEventListener('click', function () {
  toast('Connecting Google Drive is coming next. Until then, games are processed on this laptop.', '');
});
byId('save-all').addEventListener('click', saveAll);
byId('roster-save').addEventListener('click', saveRoster);
byId('check-all').addEventListener('click', checkEverything);
wireLogoDrop();
wireSideNav();
showSchool().catch(function (problem) { toast(problem.message, 'bad'); });
showStorage();
showSettings().catch(function (problem) { toast(problem.message, 'bad'); });
showRoster().catch(function (problem) { toast(problem.message, 'bad'); });
