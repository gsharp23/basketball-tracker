/* The start page: the school's logo, artwork and name from /api/branding, and the data folder line. */

async function showBranding() {
  const info = await api('/api/branding');
  const name = info.school_name, mascot = info.mascot;
  if (info.logo_url) {
    const plate = document.getElementById('plate-logo');
    plate.className = 'plate-logo has-logo';
    plate.replaceChildren(el('img', { src: info.logo_url, alt: 'school logo' }));
  }
  document.getElementById('plate-name').textContent = name || info.title;
  if (info.hero_url) {
    const hero = document.getElementById('hero');
    hero.className = 'hero';
    hero.replaceChildren(el('img', { src: info.hero_url, alt: 'team artwork' }));
  }
  document.getElementById('mascot').textContent = mascot || name || info.title;
  const line = document.getElementById('school-line');
  if (mascot && name) line.textContent = name + ' · ' + info.title;
  else if (name) line.textContent = info.title;
  else line.textContent = 'Game video in, coaching numbers out';
}

async function showStorage() {
  const line = document.getElementById('storage-line');
  try {
    const storage = await api('/api/storage');
    if (storage.attached) {
      const free = storage.free !== null ? ' · ' + fmtBytes(storage.free) + ' free' : '';
      line.replaceChildren(el('span', { class: 'dot dot-green' }), 'Data folder in place' + free);
    } else {
      line.replaceChildren(el('span', { class: 'dot dot-red' }), 'A data folder cannot be reached: see README.md');
    }
  } catch (problem) {
    line.replaceChildren(el('span', { class: 'dot dot-red' }), problem.message);
  }
}

function showPage() { document.getElementById('start-page').classList.remove('waiting'); }
showBranding().catch(function (problem) { toast(problem.message, 'bad'); }).then(showPage);
setTimeout(showPage, 1500);     // the branding is hidden until it answers, but never for longer than this
showStorage();
