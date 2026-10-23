(async function () {
  const ctx = await BS.boot({ need: 'guest' });
  if (!ctx) return;
  const { cfg } = ctx, $ = BS.$;
  const next = new URLSearchParams(location.search).get('next');
  if (cfg.app_flavor === 'registrar') { $('title').textContent = 'Sub-Registrar login'; $('switchRow').hidden = true; $('regNote').hidden = false; }
  BS.channelPicker($('channels'), cfg.channels);
  const form = $('loginForm'), msg = $('loginMsg'), btn = $('loginBtn'), otpRoot = $('otpStep'), alt = $('altAuth');
  let challenge = null;
  const flow = BS.otpFlow(otpRoot, {
    verify: code => BS.api('/api/otp/verify', { body: { challenge, otp: code } }),
    resend: () => BS.api('/api/otp/resend', { body: { challenge } }),
    onSuccess: d => BS.finishLogin(d, next),
    onBack: () => { otpRoot.hidden = true; form.hidden = false; alt.hidden = !alt.childElementCount; },
  });
  form.addEventListener('submit', async e => {
    e.preventDefault(); BS.hide(msg); btn.disabled = true;
    const r = await BS.api('/api/login/start', { body: { identifier: $('ident').value, channel: BS.chosenChannel($('channels')) } });
    btn.disabled = false;
    if (!r.ok) return BS.show(msg, r.data.error || 'Something went wrong.', 'err');
    challenge = r.data.challenge;
    form.hidden = true; alt.hidden = true; otpRoot.hidden = false;
    flow.start({ sentTo: (r.data.message || '') + ' It is valid for 5 minutes. Not registered yet? Go back and check your details.', devOtp: r.data.dev_otp });
  });
  BS.googleBlock(alt, cfg, next);
})();
