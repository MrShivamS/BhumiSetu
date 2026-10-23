(async function () {
  const ctx = await BS.boot({ need: 'citizen' });
  if (!ctx) return;
  const { me, user: u } = ctx, $ = BS.$, h = BS.h, k = me.kyc;
  const rows = (dl, pairs) => pairs.forEach(([a, b]) => dl.append(h('div', {}, h('dt', { text: a }), h('dd', { text: b }))));

  if (u.kyc_status === 'verified') {
    rows($('verifiedList'), [['Name on record', u.kyc_name], ['Aadhaar', 'XXXX XXXX ' + u.aadhaar_last4], ['Verified on', BS.fmtDate(u.kyc_verified_at)]]);
    $('verifiedPanel').hidden = false; return;
  }
  if (k && k.status === 'pending') {
    rows($('pendingList'), [['Submitted on', BS.fmtDate(k.submitted_at)], ['Name', k.full_name], ['Aadhaar', 'XXXX XXXX ' + k.aadhaar_last4], ['PIN code', k.pincode],
      ['Reviewing office', k.offices.length ? k.offices.join(', ') : 'None assigned to this PIN code yet. Your request will wait until one is.']]);
    $('pendingPanel').hidden = false; return;
  }

  // the form (first time, or after a rejection)
  const form = $('kycForm'), msg = $('kycMsg'), btn = $('kycBtn');
  if (k && k.status === 'rejected') {
    $('rejectedNote').replaceChildren(h('strong', { text: 'Your last request was rejected. ' }), k.review_note || '', ' Please correct it and submit again.');
    $('rejectedNote').hidden = false;
    ['dob', 'gender', 'address', 'city', 'pincode', 'district', 'state'].forEach(f => { form.elements[f].value = k[f] || ''; });
  }
  form.elements.full_name.value = (k && k.full_name) || u.name;
  const picks = { front: null, back: null };
  ['front', 'back'].forEach(side => {
    const input = $(side), prev = $(side + 'Prev');
    input.addEventListener('change', async () => {
      const f = input.files[0]; if (!f) return;
      picks[side] = await BS.shrinkImage(f);
      prev.src = URL.createObjectURL(picks[side]); prev.hidden = false; input.parentElement.classList.add('has-img');
    });
  });
  $('aadhaar').addEventListener('input', e => { const d = e.target.value.replace(/\D/g, '').slice(0, 12); e.target.value = d.replace(/(\d{4})(?=\d)/g, '$1 '); });
  $('pincode').addEventListener('input', e => { e.target.value = e.target.value.replace(/\D/g, ''); });
  form.addEventListener('submit', async e => {
    e.preventDefault(); BS.hide(msg); BS.setFieldErrors(form, {}); btn.disabled = true; btn.textContent = 'Uploading...';
    const fd = new FormData();
    ['full_name', 'dob', 'aadhaar', 'gender', 'address', 'city', 'pincode', 'district', 'state'].forEach(f => fd.append(f, form.elements[f].value));
    fd.append('consent', $('consent').checked ? 'true' : '');
    if (picks.front) fd.append('front', picks.front, 'front.jpg');
    if (picks.back) fd.append('back', picks.back, 'back.jpg');
    const r = await BS.api('/api/kyc/submit', { form: fd });
    btn.disabled = false; btn.textContent = 'Submit for verification';
    if (!r.ok) { BS.setFieldErrors(form, r.data.fields); BS.show(msg, r.data.error || 'Could not submit.', 'err'); return msg.scrollIntoView({ block: 'center' }); }
    location.reload();
  });
  $('formPanel').hidden = false;
})();
