(async function () {
  const ctx = await BS.boot({ need: 'user' });
  if (!ctx) return;
  const { me, user: u } = ctx, $ = BS.$, h = BS.h, isReg = u.role === 'registrar';
  if (!me.can_search) {
    $('lockedBtn').textContent = { pending: 'View KYC status', failed: 'Submit KYC again' }[u.kyc_status] || 'Start Aadhaar KYC';
    $('lockedPanel').hidden = false; return;
  }
  $('searchPanel').hidden = false;
  const sMsg = $('sMsg'), typeSel = $('t'), qIn = $('q');
  const HINTS = { auto: 'Owner, survey no., ID, bill or tax number', aadhaar: '12-digit Aadhaar number', property_id: 'e.g. PID-PUN-0001',
    electricity: 'Electricity consumer / bill number', tax: 'e.g. PMC-PT-10001', general: 'Owner name or survey number' };
  typeSel.addEventListener('change', () => { qIn.placeholder = HINTS[typeSel.value]; qIn.value = ''; qIn.focus(); });
  qIn.addEventListener('input', () => { if (typeSel.value === 'aadhaar') { const d = qIn.value.replace(/\D/g, '').slice(0, 12); qIn.value = d.replace(/(\d{4})(?=\d)/g, '$1 '); } });

  async function viewDocument(id, btn) {
    btn.disabled = true;
    const r = await BS.api('/api/registrar/doc/' + id + '/link', { body: {} });
    btn.disabled = false;
    if (r.ok) BS.openFile(r.data.url); else BS.show(sMsg, r.data.error || 'Could not open the document.', 'err');
  }
  $('sForm').addEventListener('submit', async e => {
    e.preventDefault(); BS.hide(sMsg);
    const p = new URLSearchParams({ type: typeSel.value, q: qIn.value, village: $('v').value, district: $('d').value });
    const r = await BS.api('/api/search?' + p, { method: 'GET' });
    if (!r.ok) {
      $('results').hidden = true;
      if (r.data.code === 'kyc_required') return location.reload();
      return BS.show(sMsg, r.data.error || 'Search failed.', 'err');
    }
    const body = $('rBody'); body.replaceChildren();
    r.data.results.forEach(x => {
      const ids = ['Property ID: ' + (x.property_id || '-'), 'Electricity: ' + (x.electricity_bill_no || '-'), 'Tax: ' + (x.property_tax_no || '-'),
        'Aadhaar: ' + (x.aadhaar || '-'), 'Doc: ' + (x.doc_no || '-')].join('\n');
      const status = h('td', {}, h('span', { class: 'chip ' + (x.status === 'registered' ? 'ok' : 'warn'), text: x.status === 'registered' ? 'Registered' : 'Pending verification' }));
      if (isReg && x.has_document) { const b = h('button', { class: 'linkbtn', type: 'button', text: 'View document' }); b.style.display = 'block'; b.style.marginTop = '6px'; b.style.fontSize = '12.5px'; b.addEventListener('click', () => viewDocument(x.id, b)); status.append(b); }
      const idCell = h('td', { text: ids }); idCell.style.whiteSpace = 'pre-line'; idCell.style.fontSize = '12.5px';
      body.append(h('tr', {}, h('td', { text: x.survey_no }), h('td', { text: x.owner_name }), h('td', { text: x.village }), h('td', { text: x.district }), h('td', { text: x.area }), idCell, status));
    });
    $('rCount').textContent = r.data.count ? r.data.count + ' record(s) found' : 'No records found';
    $('results').hidden = false;
  });
})();
