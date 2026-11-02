/* Vinted Drop — interactions et animations */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const eur = v => (v || 0).toLocaleString('fr-FR', { style: 'currency', currency: 'EUR' });
  const fmt = (v, f) => f === 'eur' ? eur(v) : f === 'pct' ? `${(v || 0).toLocaleString('fr-FR')} %` : Math.round(v || 0).toLocaleString('fr-FR');

  /* ---- thème ---- */
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } },
                  set(k, v) { try { localStorage.setItem(k, v); } catch {} } };
  const saved = store.get('theme');
  if (saved) document.documentElement.dataset.theme = saved;
  $$('[data-theme-toggle]').forEach(b => b.addEventListener('click', () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === 'dark'
      : matchMedia('(prefers-color-scheme: dark)').matches;
    const next = dark ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    store.set('theme', next);
    if (window.__chart) window.__chart();
  }));

  /* ---- apparition progressive ---- */
  const groups = new Map();
  $$('.reveal').forEach(el => {
    const parent = el.parentElement;
    const i = groups.get(parent) || 0;
    el.style.setProperty('--i', Math.min(i, 12));
    groups.set(parent, i + 1);
  });
  const io = new IntersectionObserver(entries => entries.forEach(e => {
    if (!e.isIntersecting) return;
    e.target.classList.add('in');
    $$('[data-count]', e.target).concat(e.target.matches('[data-count]') ? [e.target] : []).forEach(el => countUp(el));
    $$('[data-p]', e.target).forEach(r => requestAnimationFrame(() => r.style.setProperty('--p', r.dataset.p)));
    $$('[data-w]', e.target).forEach(b => requestAnimationFrame(() => b.style.width = b.dataset.w + '%'));
    io.unobserve(e.target);
  }), { threshold: .08 });
  $$('.reveal').forEach(el => io.observe(el));
  // éléments animés hors .reveal
  $$('[data-count]').filter(el => !el.closest('.reveal')).forEach(el => countUp(el));
  $$('[data-p]').filter(el => !el.closest('.reveal')).forEach(r => requestAnimationFrame(() => r.style.setProperty('--p', r.dataset.p)));
  $$('[data-w]').filter(el => !el.closest('.reveal')).forEach(b => requestAnimationFrame(() => b.style.width = b.dataset.w + '%'));

  /* ---- compteurs ---- */
  function countUp(el, to) {
    const target = to ?? parseFloat(el.dataset.count);
    const from = parseFloat(el.dataset.current || 0);
    const f = el.dataset.format;
    if (reduced || isNaN(target)) { el.textContent = fmt(target, f); el.dataset.current = target; return; }
    const t0 = performance.now(), d = 1100;
    const step = t => {
      const k = Math.min(1, (t - t0) / d), e = 1 - Math.pow(1 - k, 4);
      el.textContent = fmt(from + (target - from) * e, f);
      if (k < 1) requestAnimationFrame(step); else el.dataset.current = target;
    };
    requestAnimationFrame(step);
  }

  /* ---- toasts ---- */
  const box = $('.toasts');
  window.toast = (msg, kind = 'ok') => {
    if (!box) return;
    const t = document.createElement('div');
    t.className = `toast ${kind}`;
    t.innerHTML = `<span>${{ ok: '✅', err: '⚠️', info: '💡' }[kind] || ''}</span><span></span>`;
    t.lastChild.textContent = msg;
    box.appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 400); }, 4800);
  };
  $$('[data-toast]').forEach(el => window.toast(el.dataset.toast, el.dataset.kind || 'ok'));
  const params = new URLSearchParams(location.search);
  if (params.get('ok')) {
    window.toast(params.get('ok'));
    params.delete('ok');
    history.replaceState(null, '', location.pathname + (params.toString() ? '?' + params : ''));
  }

  /* ---- boutons en chargement ---- */
  $$('form').forEach(f => f.addEventListener('submit', e => {
    const b = e.submitter || $('button[type=submit],button:not([type])', f);
    if (b && !b.dataset.noload) { b.classList.add('loading'); }
  }));
  $$('[data-confirm]').forEach(el => el.addEventListener('click', e => { if (!confirm(el.dataset.confirm)) e.preventDefault(); }));

  /* ---- sparklines ---- */
  $$('[data-spark]').forEach(el => {
    const vals = JSON.parse(el.dataset.spark || '[]');
    if (!vals.length) return;
    const w = 90, h = 30, max = Math.max(...vals, 1);
    const pts = vals.map((v, i) => `${(i / (vals.length - 1 || 1)) * w},${h - (v / max) * h}`).join(' ');
    el.innerHTML = `<svg width="${w}" height="${h}" viewBox="0 -2 ${w} ${h + 4}"><polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity=".85"/></svg>`;
  });

  /* ---- graphique principal (SVG maison, animé) ---- */
  const chartEl = $('#chart');
  if (chartEl && window.SERIES) {
    const tip = $('.tip', chartEl.parentElement);
    const draw = () => {
      const data = window.SERIES, W = chartEl.clientWidth || 600, H = chartEl.clientHeight || 260;
      const pad = { l: 44, r: 10, t: 14, b: 26 }, iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
      const max = Math.max(10, ...data.map(d => d.revenue)) * 1.12;
      const x = i => pad.l + (i + .5) * (iw / data.length), y = v => pad.t + ih - (v / max) * ih;
      const bw = Math.max(3, iw / data.length * .55);
      const ticks = [0, .25, .5, .75, 1].map(k => max * k);
      const line = data.map((d, i) => `${i ? 'L' : 'M'}${x(i)},${y(d.profit)}`).join('');
      const area = `${line}L${x(data.length - 1)},${y(0)}L${x(0)},${y(0)}Z`;
      chartEl.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <defs><linearGradient id="lg" x1="0" x2="1"><stop offset="0" stop-color="var(--accent)"/><stop offset="1" stop-color="var(--accent-2)"/></linearGradient>
        <linearGradient id="ag" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="var(--accent)" stop-opacity=".28"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
        ${ticks.map(t => `<line class="gridl" x1="${pad.l}" x2="${W - pad.r}" y1="${y(t)}" y2="${y(t)}"/><text x="${pad.l - 8}" y="${y(t) + 4}" text-anchor="end">${Math.round(t)}€</text>`).join('')}
        ${data.map((d, i) => `<rect class="barr" data-i="${i}" x="${x(i) - bw / 2}" width="${bw}" rx="3" y="${y(0)}" height="0" data-y="${y(d.revenue)}" data-h="${y(0) - y(d.revenue)}"/>`).join('')}
        <path class="area" d="${area}" opacity="0"/>
        <path class="line" d="${line}"/>
        ${data.map((d, i) => i % Math.ceil(data.length / 8) === 0 ? `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${d.date.slice(8, 10)}/${d.date.slice(5, 7)}</text>` : '').join('')}
      </svg>`;
      const svg = $('svg', chartEl), path = $('.line', svg), len = path.getTotalLength();
      if (!reduced) {
        path.style.strokeDasharray = len; path.style.strokeDashoffset = len;
        path.getBoundingClientRect();
        path.style.transition = 'stroke-dashoffset 1.6s cubic-bezier(.2,.8,.2,1)'; path.style.strokeDashoffset = 0;
      }
      $$('.barr', svg).forEach((r, i) => setTimeout(() => {
        r.style.transition = 'y .7s cubic-bezier(.2,.8,.2,1), height .7s cubic-bezier(.2,.8,.2,1)';
        r.setAttribute('y', r.dataset.y); r.setAttribute('height', r.dataset.h);
      }, reduced ? 0 : 200 + i * 18));
      const ar = $('.area', svg); ar.style.transition = 'opacity 1s .8s'; requestAnimationFrame(() => ar.style.opacity = 1);
      svg.addEventListener('mousemove', e => {
        const b = svg.getBoundingClientRect(), i = Math.max(0, Math.min(data.length - 1, Math.floor((e.clientX - b.left - pad.l * b.width / W) / (iw * b.width / W) * data.length)));
        const d = data[i]; if (!d) return;
        tip.style.left = (x(i) / W * 100) + '%'; tip.style.top = (y(d.revenue) / H * 100) + '%'; tip.style.opacity = 1;
        tip.innerHTML = `${d.date.slice(8, 10)}/${d.date.slice(5, 7)} · ${d.sales} ventes<br>CA ${eur(d.revenue)} · bénéfice ${eur(d.profit)}`;
      });
      svg.addEventListener('mouseleave', () => tip.style.opacity = 0);
    };
    window.__chart = draw;
    draw();
    let rt; addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(draw, 150); });
  }

  /* ---- chiffres en direct ---- */
  const live = $$('[data-live]');
  if (live.length) {
    const get = (o, p) => p.split('.').reduce((a, k) => a?.[k], o);
    setInterval(async () => {
      try {
        const r = await fetch('/api/stats', { headers: { Accept: 'application/json' } });
        if (!r.ok) return;
        const s = await r.json();
        live.forEach(el => {
          const v = get(s, el.dataset.live);
          if (v != null && +v !== +el.dataset.current) {
            if (+v > +(el.dataset.current || 0) && el.dataset.live.includes('profit')) window.toast('Nouvelle vente ! 💸');
            countUp(el, +v);
          }
        });
      } catch {}
    }, 30000);
  }

  /* ---- auto-refresh pendant une génération IA ---- */
  if (document.body.dataset.running === '1') setTimeout(() => location.reload(), 8000);
})();
