/* The plain list of dataset versions and models, with their scores and "Score again". Data: GET /api/process/versions. */

renderSidebar('improve');

let timer = null;

function scoreWords(score) {
  if (!score || score.map50 === null || score.map50 === undefined) return el('span', { class: 'muted' }, 'Not scored yet');
  const per = score.per_class || {};
  return el('span', {}, [el('b', {}, score.map50.toFixed(2)), el('span', { class: 'small muted' }, ' · ' + Object.keys(per).map(function (k) { return k + ' ' + per[k].toFixed(2); }).join(' · '))]);
}

async function scoreAgain(number, button) {
  button.disabled = true;
  const reply = await tryToast(function () { return postJSON('/api/process/score', { version: number }); });
  if (reply) toast('Scoring v' + number + ' with ' + reply.model + '. A few minutes.', 'good');
  button.disabled = false;
  await load();
}

async function load() {
  const data = await api('/api/process/versions');
  const scoring = data.scoring && (data.scoring.running || data.scoring.starting);
  byId('scoring-note').textContent = scoring ? 'Scoring: ' + ((data.scoring.lines || ['']).slice(-1)[0] || 'starting') : '';
  const vbody = byId('version-rows');
  vbody.replaceChildren.apply(vbody, data.versions.slice().reverse().map(function (v) {
    const model = data.models.find(function (m) { return m.version === v.number; });
    return el('tr', {}, [
      el('td', { class: 'strong' }, 'v' + v.number),
      el('td', {}, fmtDate((v.built || '').slice(0, 10))),
      el('td', {}, String(v.pictures)),
      el('td', {}, v.test_game ? v.test_game + ' kept aside' : el('span', { class: 'muted' }, 'same games')),
      el('td', { class: 'small' }, v.variations || ''),
      el('td', {}, scoreWords(v.score)),
      el('td', {}, model && v.has_zip ? el('button', { class: 'btn btn-outline btn-sm', type: 'button', disabled: !!scoring, onclick: function () { scoreAgain(v.number, this); } }, v.score ? 'Score again' : 'Score this version')
        : el('span', { class: 'small muted' }, model ? 'no zip' : 'no model yet'))]);
  }));
  if (!data.versions.length) vbody.appendChild(el('tr', {}, el('td', { colspan: 7, class: 'muted' }, 'No version built yet.')));
  const mbody = byId('model-rows');
  mbody.replaceChildren.apply(mbody, data.models.map(function (m) {
    return el('tr', {}, [
      el('td', { class: 'strong' }, m.label + ' ' + el('span', { class: 'small muted' }, '').textContent + '(' + m.name + ')'),
      el('td', {}, m.version ? 'v' + m.version : el('span', { class: 'muted' }, 'downloaded')),
      el('td', {}, m.trained ? fmtDate(m.trained) : '—'),
      el('td', {}, m.map50 !== null && m.map50 !== undefined ? el('b', {}, m.map50.toFixed(2)) : el('span', { class: 'muted' }, 'Not scored yet')),
      el('td', {}, m.in_use ? el('span', { class: 'chip chip-green' }, 'In use') : ''),
      el('td', {}, m.in_use ? '' : el('button', { class: 'btn btn-outline btn-sm', type: 'button', onclick: async function () {
        const ok = await tryToast(function () { return postJSON('/api/models/use', { name: m.name }); }); if (ok) { toast(m.name + ' is the detector in use.', 'good'); load(); } } }, 'Use this one'))]);
  }));
  clearTimeout(timer);
  if (scoring) timer = setTimeout(function () { load().catch(function () {}); }, 5000);
}

load().catch(function (problem) { toast(problem.message, 'bad'); });
