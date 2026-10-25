/* BhumiSetu website core: talks to the backend API with a bearer token (no cookies, so it works
   from GitHub Pages and inside the Android WebView). Everything shown to the user is inserted with
   textContent, never as HTML. */
(function () {
  'use strict';
  const API = String(window.BHUMISETU_API || '').replace(/\/+$/, '');
  const KEY = 'bs_token';
  const inApp = /BhumiSetuApp\//.test(navigator.userAgent);
  const KYC_LABEL = { verified: 'KYC verified', pending: 'KYC under review', failed: 'KYC rejected', not_started: 'KYC needed' };

  const token = {
    get() { try { return localStorage.getItem(KEY); } catch (e) { return null; } },
    set(v) { try { localStorage.setItem(KEY, v); } catch (e) { /* private mode */ } },
    clear() { try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ } },
  };

  async function api(path, opts) {
    const { method = 'POST', body, form } = opts || {};
    const headers = {};
    const t = token.get();
    if (t) headers.Authorization = 'Bearer ' + t;
    const init = { method, headers };
    if (form) init.body = form;
    else if (body !== undefined) { headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    try {
      const res = await fetch(API + path, init);
      let data = {};
      try { data = await res.json(); } catch (_) { /* not json */ }
      if (res.status === 401 && data.code === 'login_required' && t) token.clear();
      return { ok: res.ok, status: res.status, data };
    } catch (_) {
      return { ok: false, status: 0, data: { error: "Can't reach the BhumiSetu server. Check your internet connection and try again." } };
    }
  }

  // ---- tiny DOM helpers
  const $ = id => document.getElementById(id);
  function h(tag, props, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : String(v));
    }
    kids.flat().forEach(c => { if (c !== null && c !== undefined && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c))); });
    return e;
  }
  const show = (el, msg, kind) => { el.className = 'alert ' + kind; el.textContent = msg; el.hidden = false; };
  const hide = el => { el.hidden = true; };
  function setFieldErrors(form, fields) {
    form.querySelectorAll('.field').forEach(f => { f.classList.remove('invalid'); const e = f.querySelector('.error'); if (e) e.textContent = ''; });
    Object.entries(fields || {}).forEach(([name, msg]) => {
      const f = form.querySelector('[data-field="' + name + '"]');
      if (f) { f.classList.add('invalid'); const e = f.querySelector('.error'); if (e) e.textContent = msg; }
    });
  }
  const fmtDate = t => (t ? new Date(t * 1000).toLocaleDateString([], { dateStyle: 'medium' }) : '-');
  const fmtTime = t => (t ? new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '-');
  const currentPage = () => location.pathname.split('/').pop() || 'index.html';
  function go(page, params) {
    const q = params ? '?' + new URLSearchParams(params).toString() : '';
    location.href = page + q;
  }
  const safeNext = n => (/^[a-z][a-z-]*\.html$/.test(n || '') && !/^(login|register)\.html$/.test(n) ? n : null);
  const fileUrl = path => API + path;             // signed /files/... links returned by the API
  function openFile(path) {                        // PDFs can't show inside a WebView; the app downloads them instead
    if (inApp) location.href = fileUrl(path); else window.open(fileUrl(path), '_blank', 'noopener');
  }

  // ---- header / navigation
  function renderHeader(cfg, me) {
    const hdr = $('hdr'); hdr.replaceChildren();
    hdr.append(h('a', { class: 'wordmark', href: 'index.html', 'aria-label': 'BhumiSetu home' },
      h('img', { src: 'assets/logo.png', alt: 'BhumiSetu', width: 1000, height: 233 })));
    const nav = h('nav', {}), page = currentPage();
    const link = (label, href, extra) => h('a', { href, class: page === href ? 'on' : '' }, label, extra);
    if (me) {
      const u = me.user, reg = u.role === 'registrar';
      if (reg) nav.append(link('Add documents', 'registrar.html'),
        link('KYC requests', 'registrar-kyc.html', me.pending_kyc ? h('span', { class: 'nbadge', text: String(me.pending_kyc) }) : null));
      nav.append(link('Search records', 'search.html'), link('Profile', 'profile.html'));
      nav.append(reg ? h('span', { class: 'chip ok', text: 'Sub-Registrar' })
                     : h('a', { href: 'kyc.html', class: 'chip kyc-' + u.kyc_status, text: KYC_LABEL[u.kyc_status] || '' }));
      nav.append(h('button', { class: 'linkbtn', type: 'button', text: 'Log out', onclick: logout }));
    } else if (cfg) {
      nav.append(link('Log in', 'login.html'));
      if (cfg.app_flavor !== 'registrar') nav.append(h('a', { class: 'btn small', href: 'register.html', text: 'Create account' }));
    }
    hdr.append(nav);
  }
  async function logout() { await api('/api/logout', { body: {} }); token.clear(); go('index.html'); }

  function banner(message, withLogin) {
    const main = $('main'); main.replaceChildren(h('div', { class: 'panel' }, h('p', { class: 'sub', text: message }),
      h('a', { class: 'btn', href: withLogin ? 'login.html' : 'index.html', text: withLogin ? 'Log in' : 'Go to home' })));
  }

  /* need: any | guest | user | citizen | registrar */
  async function boot(opts) {
    const need = (opts && opts.need) || 'any';
    const hasToken = !!token.get();
    const [cfgR, meR] = await Promise.all([api('/api/config', { method: 'GET' }), hasToken ? api('/api/me', { method: 'GET' }) : Promise.resolve(null)]);
    if (!cfgR.ok) { renderHeader(null, null); banner(cfgR.data.error || 'The server is not available right now.', false); return null; }
    if (meR && meR.status === 0) { renderHeader(cfgR.data, null); banner(meR.data.error, false); return null; }
    const cfg = cfgR.data, me = meR && meR.ok ? meR.data : null;
    renderHeader(cfg, me);
    if (need === 'guest' && me) { go(me.user.role === 'registrar' ? 'registrar.html' : 'profile.html'); return null; }
    if (need !== 'any' && need !== 'guest' && !me) { go('login.html', { next: currentPage() }); return null; }
    if (need === 'registrar' && me.user.role !== 'registrar') { banner('This page is only for Sub-Registrars.', false); return null; }
    if (need === 'citizen' && me.user.role === 'registrar') { go('profile.html'); return null; }
    return { cfg, me, user: me && me.user, flavor: cfg.app_flavor };
  }

  function finishLogin(data, next) {
    token.set(data.token);
    go(data.role === 'registrar' ? 'registrar.html' : (safeNext(next) || 'profile.html'));
  }

  // ---- "send my code by" picker
  function channelPicker(root, channels) {
    root.replaceChildren();
    [['email', 'Email'], ['sms', 'SMS'], ['whatsapp', 'WhatsApp']].forEach(([key, label]) => {
      const c = channels[key] || {};
      root.append(h('input', { type: 'radio', name: 'channel', id: 'ch-' + key, value: key, checked: key === 'email', disabled: !c.usable }),
        h('label', { for: 'ch-' + key }, label, h('small', { text: c.live ? 'live' : (c.usable ? 'demo' : 'unavailable') })));
    });
  }
  const chosenChannel = root => (root.querySelector('input[name=channel]:checked') || {}).value;

  // ---- 6-digit code step shared by login and sign-up
  function otpFlow(root, o) {
    root.replaceChildren();
    const sent = h('p', { class: 'sub' }), msg = h('div', { class: 'alert', hidden: true });
    const input = h('input', { id: 'otp-input', class: 'otp-box', type: 'text', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 6, placeholder: '------' });
    const verifyBtn = h('button', { class: 'btn block', type: 'button', text: 'Verify code' });
    const resendBtn = h('button', { class: 'linkbtn', type: 'button', text: 'Resend code', disabled: true });
    const backBtn = h('button', { class: 'linkbtn', type: 'button', text: 'Change details', onclick: () => o.onBack() });
    root.append(h('h3', { text: 'Enter your code' }), sent, h('div', { class: 'field' }, h('label', { for: 'otp-input', text: '6-digit code' }), input),
      msg, verifyBtn, h('div', { class: 'row-between' }, resendBtn, backBtn));
    let timer;
    function cooldown(sec) {
      clearInterval(timer); resendBtn.disabled = true;
      const tick = () => { resendBtn.textContent = sec > 0 ? 'Resend code in ' + sec + 's' : 'Resend code'; if (sec-- <= 0) { clearInterval(timer); resendBtn.disabled = false; } };
      tick(); timer = setInterval(tick, 1000);
    }
    input.addEventListener('input', () => { input.value = input.value.replace(/\D/g, '').slice(0, 6); if (input.value.length === 6 && !verifyBtn.disabled) verifyBtn.click(); });
    verifyBtn.addEventListener('click', async () => {
      if (input.value.length !== 6) return show(msg, 'Enter the 6-digit code.', 'err');
      verifyBtn.disabled = true; hide(msg);
      const r = await o.verify(input.value);
      if (r.ok) { show(msg, 'Verified. Redirecting...', 'okay'); o.onSuccess(r.data); }
      else { show(msg, r.data.error || 'Could not verify.', 'err'); input.select(); verifyBtn.disabled = false; }
    });
    resendBtn.addEventListener('click', async () => {
      resendBtn.disabled = true; hide(msg);
      const r = await o.resend();
      if (r.ok) { show(msg, 'A new code has been sent.', 'okay'); cooldown(30); }
      else { show(msg, r.data.error || 'Could not resend.', 'err'); cooldown(r.data.retry_after || 5); }
    });
    return { start(info) { sent.textContent = info.sentTo || ''; cooldown(30); input.value = ''; verifyBtn.disabled = false; input.focus(); } };
  }

  // ---- "Continue with Google"
  async function googleBlock(root, cfg, next) {
    if (!cfg.google_client_id || cfg.app_flavor === 'registrar') return;
    const msg = h('div', { class: 'alert err', hidden: true }), slot = h('div', { class: 'gbtn' });
    const native = h('button', { class: 'btn ghost block', type: 'button', text: 'Continue with Google', hidden: true });
    root.replaceChildren(h('div', { class: 'or' }, h('span', { text: 'or' })), msg, slot, native);
    const finish = async credential => {
      hide(msg);
      const r = await api('/api/auth/google', { body: { credential } });
      if (r.ok) finishLogin(r.data, next); else show(msg, r.data.error || 'Google sign-in failed.', 'err');
    };
    const n = await api('/api/auth/google/nonce', { method: 'GET' });
    root.hidden = false;
    if (!n.ok) { show(msg, n.data.error || 'Google sign-in is not available right now.', 'err'); return; }
    if (inApp) {          // Google refuses its sign-in page inside a WebView; the Android app provides a native picker
      slot.hidden = true; native.hidden = !window.BhumiSetuNative;
      native.onclick = () => window.BhumiSetuNative.googleSignIn(n.data.nonce);
      window.onNativeGoogleToken = finish;
      window.onNativeGoogleError = m => show(msg, m || 'Google sign-in was cancelled.', 'err');
      return;
    }
    const s = document.createElement('script'); s.src = 'https://accounts.google.com/gsi/client'; s.async = true;
    s.onload = () => {
      window.google.accounts.id.initialize({ client_id: cfg.google_client_id, nonce: n.data.nonce, callback: r => finish(r.credential), ux_mode: 'popup' });
      window.google.accounts.id.renderButton(slot, { theme: 'outline', size: 'large', text: 'continue_with', shape: 'rectangular', width: 320 });
    };
    s.onerror = () => show(msg, "Couldn't load Google sign-in. Check your connection.", 'err');
    document.head.appendChild(s);
  }

  // ---- phone photos are often 4-10 MB: shrink to 1600 px JPEG before uploading (the server re-checks everything)
  async function shrinkImage(file, maxDim, quality) {
    maxDim = maxDim || 1600; quality = quality || 0.85;
    try {
      if (!/^image\/(jpeg|png)$/.test(file.type) || !window.createImageBitmap) return file;
      const bmp = await createImageBitmap(file);
      const scale = Math.min(1, maxDim / Math.max(bmp.width, bmp.height));
      const c = document.createElement('canvas'); c.width = Math.round(bmp.width * scale); c.height = Math.round(bmp.height * scale);
      c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
      const blob = await new Promise(res => c.toBlob(res, 'image/jpeg', quality));
      return blob && blob.size < file.size ? new File([blob], 'aadhaar.jpg', { type: 'image/jpeg' }) : file;
    } catch (_) { return file; }
  }

  window.BS = { api, token, boot, h, $, show, hide, setFieldErrors, fmtDate, fmtTime, go, safeNext, fileUrl, openFile, inApp,
    finishLogin, channelPicker, chosenChannel, otpFlow, googleBlock, shrinkImage, KYC_LABEL, currentPage };
})();
