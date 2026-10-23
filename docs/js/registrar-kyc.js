(async function () {
  const ctx = await BS.boot({ need: 'registrar' });
  if (!ctx) return;
  const $ = BS.$, h = BS.h;
  let state = { s: 'pending', pin: '', q: '' }, current = null;
  const LABEL = { pending: 'Pending', approved: 'Approved', rejected: 'Rejected' }, CLS = { pending: 'warn', approved: 'ok', rejected: 'bad' };
  Object.entries(ctx.cfg.kyc_reasons).forEach(([k, v]) => $('reason').append(h('option', { value: k, text: v })));

  async function loadList() {
    const p = new URLSearchParams({ status: state.s, pin: state.pin, q: state.q });
    const r = await BS.api('/api/registrar/kyc?' + p, { method: 'GET' });
    if (!r.ok) { $('list').hidden = true; return BS.show($('listMsg'), r.data.error || 'Could not load.', 'err'); }
    BS.hide($('listMsg'));
    $('area').textContent = r.data.area ? 'Your service area: ' + r.data.area : '';
    $('noArea').hidden = !!r.data.area;
    ['pending', 'approved', 'rejected'].forEach(k => { $('n-' + k).textContent = r.data.counts[k]; });
    const body = $('rows'); body.replaceChildren();
    r.data.results.forEach(x => {
      const name = h('td', { text: x.full_name });
      if (x.duplicate_of_verified) name.append(h('span', { class: 'chip bad', text: 'Duplicate Aadhaar', style: 'margin-left:8px' }));
      const btn = h('button', { class: 'btn small ghost', type: 'button', text: x.status === 'pending' ? 'Review' : 'View' });
      const tr = h('tr', { class: 'clickable' }, h('td', { text: BS.fmtTime(x.submitted_at) }), name, h('td', { text: x.pincode }), h('td', { text: x.district }),
        h('td', {}, h('span', { class: 'chip ' + CLS[x.status], text: LABEL[x.status] })), h('td', {}, btn));
      tr.addEventListener('click', () => openDetail(x.id));
      body.append(tr);
    });
    $('list').hidden = !r.data.results.length; $('empty').hidden = !!r.data.results.length || !r.data.area;
  }

  async function openDetail(id) {
    BS.hide($('dMsg'));
    const r = await BS.api('/api/registrar/kyc/' + id, { method: 'GET' });
    if (!r.ok) return BS.show($('listMsg'), r.data.error || 'Could not open.', 'err');
    const k = r.data.request; current = k;
    $('dName').textContent = k.full_name; $('dStatus').textContent = LABEL[k.status]; $('dStatus').className = 'chip ' + CLS[k.status];
    const warns = [];
    if (k.duplicate_of_verified) warns.push('Another account is already verified with this Aadhaar number. Do not approve; reject this request.');
    if (!k.name_matches_account) warns.push('The name on this request differs from the account name (' + k.account.name + '). Check the card carefully.');
    $('dWarn').replaceChildren(...warns.map(w => h('div', { text: w }))); $('dWarn').hidden = !warns.length;
    const rows = [['Name on Aadhaar', k.full_name], ['Date of birth', k.dob], ['Gender', k.gender], ['Aadhaar number', k.aadhaar + ' (check the last 4 on the card)'],
      ['Address', k.address], ['Village / city', k.city], ['District', k.district], ['State', k.state], ['PIN code', k.pincode],
      ['Account', k.account.name + ' · ' + k.account.email + (k.account.mobile ? ' · +91 ' + k.account.mobile : '')], ['Submitted', BS.fmtTime(k.submitted_at)]];
    if (k.status !== 'pending') rows.push(['Decision', LABEL[k.status] + ' by ' + (k.reviewer || '-') + ' on ' + BS.fmtTime(k.reviewed_at)], ['Note', k.review_note || '-']);
    $('dList').replaceChildren(...rows.map(([a, b]) => h('div', {}, h('dt', { text: a }), h('dd', { text: b }))));
    [['imgFront', 'front'], ['imgBack', 'back']].forEach(([el, side]) => {      // signed, short-lived links; no login header is needed for <img>
      const im = $(el); im.classList.remove('zoom');
      if (k.images) { im.src = BS.fileUrl(k.images[side]); im.alt = 'Aadhaar ' + side; } else { im.removeAttribute('src'); im.alt = 'Photo no longer stored'; }
    });
    $('decide').hidden = k.status !== 'pending'; $('confirm').checked = false; $('reason').value = ''; $('note').value = '';
    $('detail').hidden = false; $('detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  document.querySelectorAll('.zoomable').forEach(im => im.addEventListener('click', () => im.classList.toggle('zoom')));

  async function decide(kind) {
    BS.hide($('dMsg')); const id = current.id;
    const body = kind === 'approve' ? { confirm: $('confirm').checked } : { reason: $('reason').value, note: $('note').value };
    $('approve').disabled = $('reject').disabled = true;
    const r = await BS.api('/api/registrar/kyc/' + id + '/' + kind, { body });
    $('approve').disabled = $('reject').disabled = false;
    if (!r.ok) return BS.show($('dMsg'), r.data.error || 'Could not save.', 'err');
    await loadList(); await openDetail(id);
    BS.show($('dMsg'), kind === 'approve' ? 'KYC approved. The user can now search land records.' : 'KYC rejected. The user will see your reason and can resubmit.', 'okay');
  }
  $('approve').onclick = () => decide('approve'); $('reject').onclick = () => decide('reject');
  $('tabs').addEventListener('click', e => { const b = e.target.closest('.tab'); if (!b) return; document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t === b)); state.s = b.dataset.s; loadList(); });
  $('filters').addEventListener('submit', e => { e.preventDefault(); state.pin = $('pin').value.replace(/\D/g, ''); state.q = $('q').value.trim(); loadList(); });
  $('clear').onclick = () => { $('pin').value = ''; $('q').value = ''; state.pin = state.q = ''; loadList(); };
  loadList();
})();
