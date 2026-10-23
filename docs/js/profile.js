(async function () {
  const ctx = await BS.boot({ need: 'user' });
  if (!ctx) return;
  const { me, user: u } = ctx, $ = BS.$, h = BS.h, reg = u.role === 'registrar';
  const chip = (text, cls) => h('span', { class: 'chip ' + cls, text });
  const row = (label, ...vals) => h('div', {}, h('dt', { text: label }), h('dd', {}, ...vals));
  const list = $('detailList');
  list.append(row('Full name', u.name), row('Account type', reg ? chip('Sub-Registrar', 'ok') : 'Citizen'));
  if (reg && u.office) list.append(row('Office', u.office));
  if (reg) list.append(row('Service area (PIN codes)', u.pin_codes ? u.pin_codes : chip('None assigned', 'warn')));
  list.append(row('Mobile', ...(u.mobile ? ['+91 ' + u.mobile, u.mobile_verified ? chip('Verified', 'ok') : chip('Not verified', 'warn')] : [h('span', { class: 'muted', text: 'Not added' })])));
  list.append(row('Email', u.email, u.email_verified ? chip('Verified', 'ok') : chip('Not verified', 'warn')));
  list.append(row('Google account', u.google_linked ? chip('Linked', 'ok') : h('span', { class: 'muted', text: 'Not linked' })));
  list.append(row('Member since', BS.fmtDate(u.created_at)), row('Last login', BS.fmtDate(u.last_login_at)));
  $('verifyHint').hidden = !(u.mobile && !(u.mobile_verified && u.email_verified));

  const actions = $('kycActions'), kl = $('kycList'), s = u.kyc_status, k = me.kyc;
  if (reg) {
    $('kycTitle').textContent = 'Official account';
    $('kycSub').textContent = "Aadhaar KYC isn't needed for Sub-Registrar accounts. You review KYC requests from your area, add documents to the land records, and search all records.";
    actions.append(h('a', { class: 'btn', href: 'registrar-kyc.html', text: 'KYC requests' }), ' ', h('a', { class: 'btn ghost', href: 'registrar.html', text: 'Add a document' }));
    $('kycNote').hidden = true;
  } else {
    $('kycSub').textContent = s === 'verified' ? 'Your identity is verified. You can search land records.' : s === 'pending' ? 'A Sub-Registrar is reviewing your documents.' : 'Required before you can search land records.';
    kl.append(row('Status', chip({ verified: 'Verified', pending: 'Under review', failed: 'Rejected', not_started: 'Not started' }[s], 'kyc-' + s)));
    if (s === 'pending' && k) kl.append(row('Submitted on', BS.fmtDate(k.submitted_at)));
    if (s === 'failed' && k && k.review_note) kl.append(row('Reason', k.review_note));
    if (s === 'verified') kl.append(row('Name on record', u.kyc_name), row('Aadhaar', 'XXXX XXXX ' + u.aadhaar_last4), row('Verified on', BS.fmtDate(u.kyc_verified_at)));
    if (s === 'verified') actions.append(h('a', { class: 'btn', href: 'search.html', text: 'Search land records' }));
    else actions.append(h('a', { class: 'btn', href: 'kyc.html', text: { pending: 'View KYC status', failed: 'Submit KYC again' }[s] || 'Start Aadhaar KYC' }));
    $('kycNote').textContent = 'A Sub-Registrar in your area reviews your Aadhaar photos. We keep only the last 4 digits of your number, never the full number.';
  }
  const r = await BS.api('/api/me/searches', { method: 'GET' });
  if (r.ok && r.data.results.length) {
    r.data.results.forEach(x => $('recentList').append(h('li', {}, h('span', { text: x.query }), h('span', { text: x.results_count + ' result(s) · ' + BS.fmtDate(x.created_at) }))));
    $('recentPanel').hidden = false;
  }
})();
