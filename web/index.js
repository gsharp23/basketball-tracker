/* The start page: the school's logo, name and artwork from /api/branding, and the data folder line.
   It is the only door between the two areas (Game analysis, Model training). */

async function showBranding() {
  const info = await api('/api/branding');
  if (info.logo_url) {
    const plate = document.getElementById('plate-logo');
    plate.className = 'plate-logo has-logo';
    plate.replaceChildren(el('img', { src: info.logo_url, alt: 'school logo' }));
  }
  const line = document.getElementById('school-line');
  const words = [info.school_name, info.mascot].filter(Boolean);
  line.textContent = words.length ? words.join(' · ') : 'Game video in, coaching numbers out';
  if (info.hero_url) {
    const art = document.getElementById('door-art');
    art.className = 'door-art has-art';
    art.replaceChildren(el('img', { src: info.hero_url, alt: 'team artwork' }));
  }
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
