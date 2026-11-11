/* Vinted Drop 2.0 — interactions (aucune dépendance) */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const eur = v => (v || 0).toLocaleString('fr-FR', { style: 'currency', currency: 'EUR' });
  const fmt = (v, f) => f === 'eur' ? eur(v) : f === 'pct' ? `${(+v || 0).toLocaleString('fr-FR')} %` : Math.round(v || 0).toLocaleString('fr-FR');
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } },
                  set(k, v) { try { localStorage.setItem(k, v); } catch {} } };

  /* ---- thème ---- */
  $$('[data-theme-toggle]').forEach(b => b.addEventListener('click', () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.dataset.theme = dark ? 'light' : 'dark';
    store.set('theme', dark ? 'light' : 'dark');
    window.__charts?.forEach(f => f());
  }));

  /* ---- menu mobile ---- */
  $$('[data-menu]').forEach(b => b.addEventListener('click', e => { e.stopPropagation(); document.body.classList.toggle('menu-open'); }));
  document.addEventListener('click', e => { if (document.body.classList.contains('menu-open') && !e.target.closest('.sidebar')) document.body.classList.remove('menu-open'); });

  /* ---- apparition + compteurs ---- */
  const groups = new Map();
  $$('.reveal').forEach(el => { const i = groups.get(el.parentElement) || 0; el.style.setProperty('--i', Math.min(i, 10)); groups.set(el.parentElement, i + 1); });
  function countUp(el, to) {
    const target = to ?? parseFloat(el.dataset.count), from = parseFloat(el.dataset.current || 0), f = el.dataset.format;
    if (reduced || isNaN(target)) { el.textContent = fmt(target, f); el.dataset.current = target; return; }
    const t0 = performance.now(), d = 900;
    const step = t => { const k = Math.min(1, (t - t0) / d), e = 1 - Math.pow(1 - k, 4);
      el.textContent = fmt(from + (target - from) * e, f); if (k < 1) requestAnimationFrame(step); else el.dataset.current = target; };
    requestAnimationFrame(step);
  }
  const animate = root => {
    $$('[data-count]', root).forEach(el => countUp(el));
    $$('[data-p]', root).forEach(r => requestAnimationFrame(() => r.style.setProperty('--p', r.dataset.p)));
    $$('[data-w]', root).forEach(b => requestAnimationFrame(() => b.style.width = Math.max(0, Math.min(100, +b.dataset.w)) + '%'));
  };
  const io = new IntersectionObserver(es => es.forEach(e => { if (!e.isIntersecting) return; e.target.classList.add('in'); animate(e.target); io.unobserve(e.target); }), { threshold: .05 });
  $$('.reveal').forEach(el => io.observe(el));
  [...$$('[data-count]'), ...$$('[data-p]'), ...$$('[data-w]')].filter(el => !el.closest('.reveal')).forEach(el => animate(el.parentElement || el));

  /* ---- toasts ---- */
  const box = $('.toasts');
  window.toast = (msg, kind = 'ok') => {
    if (!box) return;
    const t = document.createElement('div'); t.className = `toast ${kind}`; t.textContent = msg; box.appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 350); }, 4500);
  };
  $$('[data-toast]').forEach(el => window.toast(el.dataset.toast, el.dataset.kind || 'ok'));
  const params = new URLSearchParams(location.search);
  ['ok', 'err'].forEach(k => { if (params.get(k)) { if (k === 'ok') window.toast(params.get(k)); params.delete(k); } });
  if (location.search.includes('ok=') || location.search.includes('err=')) history.replaceState(null, '', location.pathname + (params.toString() ? '?' + params : '') + location.hash);

  /* ---- formulaires : chargement, confirmation ---- */
  $$('form').forEach(f => f.addEventListener('submit', e => {
    const b = e.submitter || $('button[type=submit],button:not([type])', f);
    if (b && !b.hasAttribute('data-noload')) b.classList.add('loading');
  }));
  document.addEventListener('click', e => { const el = e.target.closest('[data-confirm]'); if (el && !confirm(el.dataset.confirm)) { e.preventDefault(); e.stopPropagation(); } }, true);

  /* ---- sélection groupée ---- */
  $$('[data-checkall]').forEach(cb => cb.addEventListener('change', () => {
    $$(`input[name="${cb.dataset.checkall}"]`).forEach(x => { x.checked = cb.checked; });
    updateBulk();
  }));
  const bulkBar = $('[data-bulkbar]');
  const updateBulk = () => { if (!bulkBar) return; const n = $$('input[name="ids"]:checked').length;
    bulkBar.style.display = n ? 'flex' : 'none'; const c = $('[data-bulkcount]', bulkBar); if (c) c.textContent = n; };
  $$('input[name="ids"]').forEach(x => x.addEventListener('change', updateBulk)); updateBulk();

  /* ---- tri des tableaux ---- */
  $$('table[data-sortable]').forEach(table => {
    $$('th[data-sort]', table).forEach((th, idx) => th.addEventListener('click', () => {
      const col = [...th.parentElement.children].indexOf(th), body = table.tBodies[0], asc = !th.classList.contains('sorted-asc');
      $$('th', table).forEach(x => x.classList.remove('sorted-asc', 'sorted-desc')); th.classList.add(asc ? 'sorted-asc' : 'sorted-desc');
      const val = tr => { const td = tr.children[col]; const raw = td?.dataset.v ?? td?.textContent.trim() ?? '';
        const n = parseFloat(raw.replace(/\s/g, '').replace('€', '').replace(',', '.')); return isNaN(n) || th.dataset.sort === 'text' ? raw.toLowerCase() : n; };
      [...body.rows].sort((a, b) => { const x = val(a), y = val(b); return (x > y ? 1 : x < y ? -1 : 0) * (asc ? 1 : -1); }).forEach(r => body.appendChild(r));
    }));
  });

  /* ---- recherche globale (Ctrl+K ou /) ---- */
  const pal = $('#palette'), pin = pal && $('input', pal), pres = pal && $('.res', pal);
  let sel = -1, timer;
  const openPal = () => { if (!pal) return; pal.classList.add('open'); pin.value = ''; pres.innerHTML = '<div class="grp">Tape au moins 2 caractères</div>'; pin.focus(); };
  const closePal = () => pal?.classList.remove('open');
  $$('[data-search]').forEach(b => b.addEventListener('click', openPal));
  pal?.addEventListener('click', e => { if (e.target === pal) closePal(); });
  pin?.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(async () => {
    const q = pin.value.trim(); if (q.length < 2) return;
    try { const r = await (await fetch('/api/search?q=' + encodeURIComponent(q))).json();
      const rows = [];
      if (r.products.length) { rows.push('<div class="grp">Produits</div>'); r.products.forEach(p => rows.push(`<a href="/produit/${p.id}"><b>#${p.id}</b> ${esc(p.title || p.supplier_title || 'Sans titre')} <span class="faint" style="margin-left:auto">${p.price ? eur(p.price) : ''}</span></a>`)); }
      if (r.shops.length) { rows.push('<div class="grp">Boutiques</div>'); r.shops.forEach(s => rows.push(`<a href="/boutique/${s.id}">${esc(s.name)} <span class="faint" style="margin-left:auto">${esc(s.niche || '')}</span></a>`)); }
      if (r.sales.length) { rows.push('<div class="grp">Ventes</div>'); r.sales.forEach(s => rows.push(`<a href="/ventes">${esc(s.title || 'Vente')} · ${esc(s.buyer || '')} <span class="faint" style="margin-left:auto">${eur(s.sale_price)}</span></a>`)); }
      pres.innerHTML = rows.join('') || '<div class="grp">Aucun résultat</div>'; sel = -1;
    } catch { pres.innerHTML = '<div class="grp">Recherche indisponible</div>'; }
  }, 160); });
  pin?.addEventListener('keydown', e => { const items = $$('a', pres);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length; items.forEach((a, i) => a.classList.toggle('sel', i === sel)); }
    if (e.key === 'Enter' && items[sel >= 0 ? sel : 0]) location.href = items[sel >= 0 ? sel : 0].href; });

  /* ---- raccourcis clavier ---- */
  const go = { a: '/', p: '/produits', s: '/studio', v: '/ventes', c: '/commandes', f: '/finances', b: '/boutiques', m: '/niches', o: '/opportunites', t: '/activite', r: '/pilote' };
  let gPressed = false;
  document.addEventListener('keydown', e => {
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPal(); return; }
    if (e.key === 'Escape') { closePal(); document.body.classList.remove('menu-open'); return; }
    if (typing) return;
    if (e.key === '/') { e.preventDefault(); openPal(); return; }
    if (gPressed && go[e.key]) { location.href = go[e.key]; gPressed = false; return; }
    gPressed = e.key === 'g'; if (gPressed) setTimeout(() => gPressed = false, 900);
  });

  /* ---- graphiques SVG (période + période précédente en pointillés) ---- */
  window.__charts = [];
  $$('[data-chart]').forEach(el => {
    const data = JSON.parse(el.dataset.chart || '[]'), prev = JSON.parse(el.dataset.prev || '[]'), key = el.dataset.key || 'profit';
    const barKey = el.dataset.bars || 'revenue', tip = el.parentElement.querySelector('.tip');
    const draw = () => {
      if (!data.length) { el.innerHTML = '<div class="empty small">Pas encore de données sur cette période</div>'; return; }
      const W = el.clientWidth || 600, H = el.clientHeight || 250, pad = { l: 46, r: 8, t: 12, b: 24 }, iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
      const max = Math.max(10, ...data.map(d => Math.max(d[barKey] || 0, d[key] || 0)), ...prev.map(d => d[key] || 0)) * 1.1;
      const x = i => pad.l + (i + .5) * (iw / data.length), y = v => pad.t + ih - (Math.max(0, v) / max) * ih;
      const bw = Math.max(2, iw / data.length * .55), ticks = [0, .5, 1].map(k => max * k);
      const line = arr => arr.slice(0, data.length).map((d, i) => `${i ? 'L' : 'M'}${x(i)},${y(d[key] || 0)}`).join('');
      const l1 = line(data), area = `${l1}L${x(data.length - 1)},${y(0)}L${x(0)},${y(0)}Z`;
      const every = Math.ceil(data.length / 7);
      el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        ${ticks.map(t => `<line class="gridl" x1="${pad.l}" x2="${W - pad.r}" y1="${y(t)}" y2="${y(t)}"/><text x="${pad.l - 8}" y="${y(t) + 4}" text-anchor="end">${Math.round(t)} €</text>`).join('')}
        ${barKey !== 'none' ? data.map((d, i) => `<rect class="barr" x="${x(i) - bw / 2}" width="${bw}" rx="2" y="${y(0)}" height="0" data-y="${y(d[barKey] || 0)}" data-h="${y(0) - y(d[barKey] || 0)}"/>`).join('') : ''}
        <path class="area" d="${area}" opacity="0"/>
        ${prev.length ? `<path class="line prev" d="${line(prev)}"/>` : ''}
        <path class="line main" d="${l1}"/>
        ${data.map((d, i) => i % every === 0 ? `<text x="${x(i)}" y="${H - 5}" text-anchor="middle">${d.date.slice(8, 10)}/${d.date.slice(5, 7)}</text>` : '').join('')}
      </svg>`;
      const svg = $('svg', el), path = $('.line.main', svg), len = path.getTotalLength();
      if (!reduced) { path.style.strokeDasharray = len; path.style.strokeDashoffset = len; path.getBoundingClientRect();
        path.style.transition = 'stroke-dashoffset 1.3s cubic-bezier(.2,.8,.2,1)'; path.style.strokeDashoffset = 0; }
      $$('.barr', svg).forEach((r, i) => setTimeout(() => { r.style.transition = 'y .6s, height .6s'; r.setAttribute('y', r.dataset.y); r.setAttribute('height', r.dataset.h); }, reduced ? 0 : 120 + i * 12));
      const ar = $('.area', svg); ar.style.transition = 'opacity .8s .6s'; requestAnimationFrame(() => ar.style.opacity = 1);
      svg.addEventListener('mousemove', e => { const b = svg.getBoundingClientRect(); const i = Math.max(0, Math.min(data.length - 1, Math.floor(((e.clientX - b.left) / b.width * W - pad.l) / iw * data.length)));
        const d = data[i], p = prev[i]; if (!d || !tip) return; tip.style.left = (x(i) / W * 100) + '%'; tip.style.top = (y(Math.max(d[barKey] || 0, d[key] || 0)) / H * 100) + '%'; tip.style.opacity = 1;
        tip.innerHTML = `${d.date.slice(8, 10)}/${d.date.slice(5, 7)} · ${d.sales} vente${d.sales > 1 ? 's' : ''}<br>CA ${eur(d.revenue)} · bénéfice ${eur(d.profit)}${p ? `<br><span style="opacity:.6">période préc. : ${eur(p[key])}</span>` : ''}`; });
      svg.addEventListener('mouseleave', () => tip && (tip.style.opacity = 0));
    };
    window.__charts.push(draw); draw();
  });
  let rt; addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => window.__charts.forEach(f => f()), 150); });

  /* ---- chiffres en direct ---- */
  const live = $$('[data-live]');
  if (live.length) setInterval(async () => {
    try { const r = await fetch('/api/stats' + location.search); if (!r.ok) return; const s = await r.json();
      live.forEach(el => { const v = el.dataset.live.split('.').reduce((a, k) => a?.[k], s);
        if (v != null && +v !== +el.dataset.current) { if (+v > +(el.dataset.current || 0) && el.dataset.live.endsWith('sales')) window.toast('Nouvelle vente détectée'); countUp(el, +v); } });
    } catch {}
  }, 30000);

  /* ---- suivi des tâches IA en cours ---- */
  const watch = $('[data-watch-tasks]');
  if (watch) { const url = '/api/tasks' + (watch.dataset.product ? '?product_id=' + watch.dataset.product : '');
    const poll = async () => { try { const r = await (await fetch(url)).json(); if (!r.active.length) { location.reload(); return; } } catch {} setTimeout(poll, 4000); };
    setTimeout(poll, 4000); }
})();

/* Copier un texte (annonce Depop…) : <button data-copy="#id"> */
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-copy]');
  if (!b) return;
  const el = document.querySelector(b.dataset.copy);
  if (!el) return;
  const done = () => { const t = b.textContent; b.textContent = 'Copié ✓'; setTimeout(() => { b.textContent = t; }, 1500); };
  const text = el.value ?? el.textContent;
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, () => { el.select(); document.execCommand('copy'); done(); });
  else { el.select(); document.execCommand('copy'); done(); }
});
