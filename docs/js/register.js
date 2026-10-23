(async function () {
  const ctx = await BS.boot({ need: 'guest' });
  if (!ctx) return;
  const { cfg } = ctx, $ = BS.$;
  if (cfg.app_flavor === 'registrar') return BS.go('login.html');         // Sub-Registrar accounts are issued by the department
  BS.channelPicker($('channels'), cfg.channels);
  const form = $('regForm'), msg = $('regMsg'), btn = $('regBtn'), otpRoot = $('otpStep'), alt = $('altAuth');
  let challenge = null;
  $('mobile').addEventListener('input', e => { e.target.value = e.target.value.replace(/\D/g, ''); });
  const flow = BS.otpFlow(otpRoot, {
    verify: code => BS.api('/api/otp/verify', { body: { challenge, otp: code } }),
    resend: () => BS.api('/api/otp/resend', { body: { challenge } }),
    onSuccess: d => BS.finishLogin(d, null),
    onBack: () => { otpRoot.hidden = true; form.hidden = false; alt.hidden = !alt.childElementCount; },
  });
  form.addEventListener('submit', async e => {
    e.preventDefault(); BS.hide(msg); BS.setFieldErrors(form, {}); btn.disabled = true;
    const r = await BS.api('/api/register', { body: { name: $('name').value, mobile: $('mobile').value, email: $('email').value,
      channel: BS.chosenChannel($('channels')), consent: $('consent').checked } });
    btn.disabled = false;
    if (!r.ok) { BS.setFieldErrors(form, r.data.fields); return BS.show(msg, r.data.error || 'Something went wrong.', 'err'); }
    challenge = r.data.challenge;
    form.hidden = true; alt.hidden = true; otpRoot.hidden = false;
    flow.start({ sentTo: 'We sent a code to ' + r.data.sent_to + '.', devOtp: r.data.dev_otp });
  });
  BS.googleBlock(alt, cfg, null);
})();
