/* Kleine SVG-Diagrammbibliothek ohne Abhängigkeiten (läuft auch ohne Internetzugang). */
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const DAY = 86400000;
  const tipEl = () => document.getElementById('tip');

  const svgEl = (tag, attrs = {}, parent, style = {}) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    for (const k in style) e.style.setProperty(k, style[k]);
    if (parent) parent.appendChild(e);
    return e;
  };
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const toDay = (s) => Date.parse(s.slice(0, 10) + 'T00:00:00Z') / DAY;
  const fmtDay = (d, year) => new Date(d * DAY).toLocaleDateString('de-DE',
    { day: '2-digit', month: '2-digit', timeZone: 'UTC', ...(year ? { year: '2-digit' } : {}) });

  function niceTicks(min, max, n = 5) {
    const span = max - min || 1;
    const raw = span / n;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const norm = raw / mag;
    const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
    const out = [];
    for (let v = Math.floor(min / step) * step; v <= max + step * 0.5; v += step) out.push(+v.toFixed(10));
    return out;
  }

  function showTip(html, x, y) {
    const t = tipEl();
    t.innerHTML = html;
    t.style.display = 'block';
    const w = t.offsetWidth;
    t.style.left = Math.min(window.innerWidth - w - 8, Math.max(8, x + 14)) + 'px';
    t.style.top = (y + 14 + window.scrollY) + 'px';
  }
  const hideTip = () => { tipEl().style.display = 'none'; };

  function legend(host, items) {
    const d = document.createElement('div');
    d.className = 'legend';
    d.innerHTML = items.map((s) => `<span><i style="background:${s.color};${s.dash ? 'opacity:.6' : ''}"></i>${esc(s.name)}</span>`).join('');
    host.appendChild(d);
  }

  function empty(host, text) {
    host.innerHTML = `<div class="empty">${esc(text || 'Keine Daten im Zeitraum')}</div>`;
  }

  /* cfg: {height, series:[{name,color,kind:'line'|'dash'|'dots'|'area', data:[{x:'YYYY-MM-DD',y,note}], fmt}],
          y:{fmt,reverse,min,max}, hlines:[{y,label}], vlines:[{x,label}], legend, emptyText} */
  function lineChart(host, cfg) {
    host.innerHTML = '';
    const pts = cfg.series.flatMap((s) => s.data.map((p) => ({ ...p, X: toDay(p.x) })));
    if (!pts.length) return empty(host, cfg.emptyText);
    const W = Math.max(280, host.clientWidth), H = cfg.height || 250;
    const m = { l: 54, r: 14, t: 10, b: 26 };
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: 'chart', role: 'img' }, host);
    let x0 = Math.min(...pts.map((p) => p.X)), x1 = Math.max(...pts.map((p) => p.X));
    (cfg.vlines || []).forEach((v) => { x1 = Math.max(x1, toDay(v.x)); });
    if (x0 === x1) { x0 -= 1; x1 += 1; }
    const ys = pts.map((p) => p.y).concat((cfg.hlines || []).map((h) => h.y));
    let lo = cfg.y?.min ?? Math.min(...ys), hi = cfg.y?.max ?? Math.max(...ys);
    if (lo === hi) { lo -= 1; hi += 1; }
    const pad = (hi - lo) * 0.08;
    if (cfg.y?.min == null) lo -= pad;
    if (cfg.y?.max == null) hi += pad;
    const ticks = niceTicks(lo, hi, 5).filter((t) => t >= lo - 1e-9 && t <= hi + 1e-9);
    const X = (x) => m.l + ((x - x0) / (x1 - x0)) * (W - m.l - m.r);
    const Y = (y) => { const f = (y - lo) / (hi - lo); return m.t + (cfg.y?.reverse ? f : 1 - f) * (H - m.t - m.b); };
    const yf = cfg.y?.fmt || ((v) => String(+v.toFixed(2)));

    ticks.forEach((t) => {
      svgEl('line', { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), class: 'grid' }, svg);
      const tx = svgEl('text', { x: m.l - 8, y: Y(t) + 4, 'text-anchor': 'end', class: 'tick' }, svg);
      tx.textContent = yf(t);
    });
    const xt = niceTicks(x0, x1, Math.max(3, Math.floor((W - m.l) / 90))).filter((t) => t >= x0 && t <= x1);
    const withYear = x1 - x0 > 300;
    xt.forEach((t) => {
      const tx = svgEl('text', { x: X(t), y: H - 6, 'text-anchor': 'middle', class: 'tick' }, svg);
      tx.textContent = fmtDay(Math.round(t), withYear);
    });
    (cfg.hlines || []).forEach((h) => {
      svgEl('line', { x1: m.l, x2: W - m.r, y1: Y(h.y), y2: Y(h.y), class: 'refline' }, svg);
      if (h.label) { const t = svgEl('text', { x: W - m.r - 4, y: Y(h.y) - 4, 'text-anchor': 'end', class: 'reflabel' }, svg); t.textContent = h.label; }
    });
    (cfg.vlines || []).forEach((v) => {
      const vx = X(toDay(v.x));
      svgEl('line', { x1: vx, x2: vx, y1: m.t, y2: H - m.b, class: 'refline' }, svg);
      if (v.label) { const t = svgEl('text', { x: vx - 4, y: m.t + 10, 'text-anchor': 'end', class: 'reflabel' }, svg); t.textContent = v.label; }
    });

    cfg.series.forEach((s) => {
      const data = s.data.map((p) => ({ ...p, X: toDay(p.x) })).sort((a, b) => a.X - b.X);
      if (!data.length) return;
      const kind = s.kind || 'line';
      if (kind === 'area') {
        const base = Y(cfg.y?.reverse ? hi : lo);
        const d = `M${X(data[0].X)},${base} ` + data.map((p) => `L${X(p.X)},${Y(p.y)}`).join(' ') + ` L${X(data.at(-1).X)},${base}Z`;
        svgEl('path', { d }, svg, { fill: s.color, opacity: 0.12 });
      }
      if (kind === 'line' || kind === 'dash' || kind === 'area') {
        const d = data.map((p, i) => `${i ? 'L' : 'M'}${X(p.X)},${Y(p.y)}`).join(' ');
        svgEl('path', { d, fill: 'none', 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round',
          ...(kind === 'dash' ? { 'stroke-dasharray': '5 4' } : {}) }, svg, { stroke: s.color });
        if (data.length <= 40 && kind !== 'dash') data.forEach((p) => svgEl('circle', { cx: X(p.X), cy: Y(p.y), r: 2.6 }, svg, { fill: s.color }));
      } else {
        data.forEach((p) => svgEl('circle', { cx: X(p.X), cy: Y(p.y), r: 3.4 }, svg, { fill: s.color, opacity: 0.6 }));
      }
    });

    const dot = svgEl('circle', { r: 5, class: 'hoverdot', display: 'none' }, svg);
    const overlay = svgEl('rect', { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: 'transparent' }, svg);
    overlay.addEventListener('mousemove', (ev) => {
      const r = svg.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W, py = ((ev.clientY - r.top) / r.height) * H;
      let best = null, bd = 1e9;
      cfg.series.forEach((s) => {
        if (s.tip === false) return;
        s.data.forEach((p) => {
          const d = Math.hypot(X(toDay(p.x)) - px, (Y(p.y) - py) * 0.5);
          if (d < bd) { bd = d; best = { s, p }; }
        });
      });
      if (!best || bd > 60) { dot.setAttribute('display', 'none'); return hideTip(); }
      dot.setAttribute('cx', X(toDay(best.p.x)));
      dot.setAttribute('cy', Y(best.p.y));
      dot.setAttribute('display', 'block');
      const f = best.s.fmt || yf;
      showTip(`<b>${esc(best.s.name)}</b><br>${fmtDay(toDay(best.p.x), true)} · ${esc(f(best.p.y))}` +
        (best.p.note ? `<br><span class="muted">${esc(best.p.note)}</span>` : ''), ev.clientX, ev.clientY);
    });
    overlay.addEventListener('mouseleave', () => { dot.setAttribute('display', 'none'); hideTip(); });
    if (cfg.legend !== false && cfg.series.length > 1) legend(host, cfg.series.filter((s) => s.name));
  }

  /* cfg: {height, labels:[], series:[{name,color,values:[],opacity}], y:{fmt}, tickEvery} */
  function barChart(host, cfg) {
    host.innerHTML = '';
    const n = cfg.labels.length;
    if (!n) return empty(host, cfg.emptyText);
    const W = Math.max(280, host.clientWidth), H = cfg.height || 220;
    const m = { l: 46, r: 10, t: 10, b: 26 };
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: 'chart', role: 'img' }, host);
    const hi = Math.max(1e-9, ...cfg.series.flatMap((s) => s.values.map((v) => v || 0))) * 1.08;
    const ticks = niceTicks(0, hi, 4).filter((t) => t <= hi);
    const Y = (v) => m.t + (1 - v / hi) * (H - m.t - m.b);
    const yf = cfg.y?.fmt || ((v) => String(+v.toFixed(1)));
    ticks.forEach((t) => {
      svgEl('line', { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), class: 'grid' }, svg);
      const tx = svgEl('text', { x: m.l - 8, y: Y(t) + 4, 'text-anchor': 'end', class: 'tick' }, svg);
      tx.textContent = yf(t);
    });
    const slot = (W - m.l - m.r) / n, k = cfg.series.length;
    const bw = Math.max(2, Math.min(26, (slot * 0.78) / k));
    const every = cfg.tickEvery || Math.max(1, Math.ceil(n / Math.floor((W - m.l) / 52)));
    cfg.labels.forEach((lab, i) => {
      const cx = m.l + slot * (i + 0.5);
      if (i % every === 0) { const t = svgEl('text', { x: cx, y: H - 6, 'text-anchor': 'middle', class: 'tick' }, svg); t.textContent = lab; }
      cfg.series.forEach((s, j) => {
        const v = s.values[i] || 0;
        const x = cx - (bw * k) / 2 + j * bw;
        const r = svgEl('rect', { x, y: Y(v), width: Math.max(1, bw - 1), height: Math.max(0, Y(0) - Y(v)), rx: 2 }, svg,
          { fill: s.color, opacity: s.opacity ?? 1 });
        r.addEventListener('mousemove', (ev) => showTip(`<b>${esc(s.name)}</b><br>${esc(lab)} · ${esc(yf(v))}`, ev.clientX, ev.clientY));
        r.addEventListener('mouseleave', hideTip);
      });
    });
    if (cfg.series.length > 1 || cfg.legend) legend(host, cfg.series);
  }

  /* Neu zeichnen, wenn sich die Breite ändert (Fenstergröße, Reiterwechsel). */
  function mount(host, draw) {
    let last = 0;
    const run = () => { const w = host.clientWidth; if (w && Math.abs(w - last) > 6) { last = w; draw(); } };
    draw();
    last = host.clientWidth;
    new ResizeObserver(run).observe(host);
  }

  window.Charts = { lineChart, barChart, mount, toDay, fmtDay };
})();
