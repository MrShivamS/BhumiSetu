(async function () {
  const ctx = await BS.boot({ need: 'any' });
  if (!ctx) return;
  const { cfg, me } = ctx, $ = BS.$;
  if (!me && cfg.app_flavor === 'registrar') return BS.go('login.html');   // the Registrar app has no landing page
  if (!me) { $('guestCta').hidden = false; $('guestCards').hidden = false; return; }
  const u = me.user;
  if (u.role === 'registrar') { $('regWelcome').textContent = 'Welcome, ' + u.name; $('regCard').hidden = false; return; }
  const btn = $('citBtn');
  if (me.can_search) { $('citText').textContent = "You're KYC verified. Search by Aadhaar, property ID, electricity bill number, property tax number, owner or survey number."; btn.textContent = 'Open search'; btn.href = 'search.html'; }
  else if (u.kyc_status === 'pending') { $('citText').textContent = 'Your KYC is under review by a Sub-Registrar. You can search once it is approved.'; btn.textContent = 'View KYC status'; btn.href = 'kyc.html'; }
  else { $('citText').textContent = 'Searching records needs a one-time Aadhaar KYC, verified by a Sub-Registrar.'; btn.textContent = u.kyc_status === 'failed' ? 'Submit KYC again' : 'Complete KYC'; btn.href = 'kyc.html'; }
  $('citCard').hidden = false;
})();
