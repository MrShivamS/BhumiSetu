(async function () {
  const ctx = await BS.boot({ need: 'registrar' });
  if (!ctx) return;
  const $ = BS.$, h = BS.h;
  const drop = $('drop'), file = $('file'), upMsg = $('upMsg'), form = $('recForm'), recMsg = $('recMsg');
  let current = { doc: '', name: '' };
  const MAP = { Buyer_name: 'owner', Seller_name: 'seller', Survey_number: 'survey', Village: 'village', District: 'district',
    Area: 'area', Property_address: 'address', Sale_amount: 'sale_amount', Date_of_agreement: 'doc_date' };

  function openForm(data, doc, name) {
    form.reset(); BS.setFieldErrors(form, {}); BS.hide(recMsg);
    Object.entries(MAP).forEach(([k, id]) => { const v = data && data[k]; if (v && v.trim().toLowerCase() !== 'not found') form.elements[id].value = v; });
    current = { doc: doc || '', name: name || '' };
    $('docLine').textContent = doc ? 'Document attached: ' + name + '. Check every field against the original.' : 'No document attached. Fill in the details below.';
    $('formPanel').hidden = false; $('formPanel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  $('browse').onclick = () => file.click();
  $('manualBtn').onclick = () => openForm(null, '', '');
  $('cancelBtn').onclick = () => { $('formPanel').hidden = true; current = { doc: '', name: '' }; };
  ['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove('drag'); }));
  drop.addEventListener('drop', e => e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]));
  file.onchange = () => file.files[0] && upload(file.files[0]);

  async function upload(f) {
    BS.show(upMsg, 'Uploading and reading the document... this can take a few seconds.', 'info');
    const fd = new FormData(); fd.append('file', f);
    const r = await BS.api('/api/registrar/upload', { form: fd });
    file.value = '';
    if (!r.ok) return BS.show(upMsg, r.data.error || 'Upload failed.', 'err');
    if (r.data.note) BS.show(upMsg, r.data.note, 'info'); else BS.hide(upMsg);
    openForm(r.data.data, r.data.doc, r.data.original_name);
  }

  form.addEventListener('submit', async e => {
    e.preventDefault(); BS.hide(recMsg); BS.setFieldErrors(form, {}); $('saveBtn').disabled = true;
    const body = Object.fromEntries(new FormData(form)); body.doc = current.doc; body.doc_name = current.name;
    const r = await BS.api('/api/registrar/records', { body });
    $('saveBtn').disabled = false;
    if (!r.ok) { BS.setFieldErrors(form, r.data.fields); return BS.show(recMsg, r.data.error || 'Could not save.', 'err'); }
    $('formPanel').hidden = true; current = { doc: '', name: '' };
    BS.show(upMsg, 'Added to the land records (record #' + r.data.id + ').', 'okay'); window.scrollTo({ top: 0, behavior: 'smooth' });
    loadRecent();
  });

  async function viewDocument(id, btn) {
    btn.disabled = true;
    const r = await BS.api('/api/registrar/doc/' + id + '/link', { body: {} });
    btn.disabled = false;
    if (r.ok) BS.openFile(r.data.url); else BS.show(upMsg, r.data.error || 'Could not open the document.', 'err');
  }
  async function loadRecent() {
    const r = await BS.api('/api/registrar/records', { method: 'GET' });
    const body = $('recentBody'); body.replaceChildren();
    if (!r.ok) { $('listSub').textContent = r.data.error || 'Could not load.'; return; }
    $('recent').hidden = !r.data.results.length;
    $('listSub').textContent = r.data.results.length ? 'Your latest 20 records.' : 'Nothing added yet.';
    r.data.results.forEach(x => {
      const cell = h('td', {});
      if (x.has_document) { const b = h('button', { class: 'linkbtn', type: 'button', text: 'View' }); b.addEventListener('click', () => viewDocument(x.id, b)); cell.append(b); } else cell.textContent = '-';
      body.append(h('tr', {}, h('td', { text: x.survey_no }), h('td', { text: x.owner_name }), h('td', { text: x.village }), h('td', { text: x.property_id || '-' }), cell));
    });
  }
  loadRecent();
})();
