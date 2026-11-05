(() => {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const num = (v, d = 0) => (v == null || Number.isNaN(v) ? '–' : Number(v).toLocaleString('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d }));
  const pad = (n) => String(n).padStart(2, '0');
  const pace = (s) => { if (!s) return '–'; let m = Math.floor(s / 60), r = Math.round(s % 60); if (r === 60) { m++; r = 0; } return `${m}:${pad(r)}`; };
  const signed = (s) => `${s < 0 ? '−' : '+'}${Math.floor(Math.abs(s) / 60)}:${pad(Math.round(Math.abs(s) % 60))}`;
  const hm = (min) => `${Math.floor(min / 60)}:${pad(Math.round(min % 60))} h`;
  const sleepFmt = (s) => `${Math.floor(s / 3600)}:${pad(Math.round((s % 3600) / 60))}`;
  const parseISO = (iso) => new Date(iso + 'T00:00:00Z');
  const toISO = (d) => d.toISOString().slice(0, 10);
  const addDays = (iso, n) => toISO(new Date(parseISO(iso).getTime() + n * 86400000));
  const dateDE = (iso, o = { day: '2-digit', month: '2-digit', year: 'numeric' }) => parseISO(iso).toLocaleDateString('de-DE', { timeZone: 'UTC', ...o });
  const mondayOf = (iso) => addDays(iso, -((parseISO(iso).getUTCDay() + 6) % 7));
  const isoWeek = (iso) => { const d = parseISO(iso); d.setUTCDate(d.getUTCDate() + 3 - ((d.getUTCDay() + 6) % 7)); const y = new Date(Date.UTC(d.getUTCFullYear(), 0, 4)); return 1 + Math.round(((d - y) / 86400000 - 3 + ((y.getUTCDay() + 6) % 7)) / 7); };
  const SPORT = { run: 'Lauf', ride: 'Rad', strength: 'Kraft', rest: 'Pause', other: 'Sonstiges' };
  const STATUS = { done: 'erledigt', partial: 'teilweise', missed: 'verpasst', today: 'heute', planned: 'geplant', skipped: 'übersprungen', moved: 'verschoben', rest: 'Pause' };
  const SCEN = { potential: 'Potenzial', realistic: 'Realistisch', conservative: 'Konservativ' };
  const SCEN_HINT = { potential: 'schnellster Bezugswert', realistic: 'Median der Bezugswerte', conservative: 'Median, vorsichtig' };
  const css = (v) => `var(${v})`;

  const S = { status: null, health: null, plan: null, forecast: null, z2: {}, weekly: null, weekOffset: 0, healthDays: 60, z2Weeks: 26, loaded: {} };

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }
  const failBox = (host, e) => { host.innerHTML = `<div class="banner warn">Daten konnten nicht geladen werden (${esc(e.message)}).</div>`; };

  /* ------------------------------------------------------------ Kopfbereich */
  function renderHeader() {
    const st = S.status, s = st.settings;
    const weeks = Math.max(0, (parseISO(s.race_date) - parseISO(st.today)) / (7 * 86400000));
    $('#racepill').innerHTML = `<b>${esc(s.race_name)}</b> · ${dateDE(s.race_date)} · noch ${num(weeks, 1)} Wochen`;
    const rel = (ts) => (ts ? new Date(ts).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : 'nie');
    $('#syncinfo').textContent = `Strava: ${rel(st.strava.last_ok)} · Garmin: ${rel(st.garmin.last_ok)}`;
    const b = [];
    if (st.demo) b.push(['warn', '<b>Demodaten:</b> Alle Werte sind synthetisch und dienen nur zum Ausprobieren der Oberfläche.']);
    if (!st.strava.connected) {
      b.push(['', st.strava.configured ? 'Strava ist noch nicht verbunden. <a href="/auth/strava">Jetzt mit Strava verbinden</a>.'
        : 'Strava ist noch nicht eingerichtet: <code>STRAVA_CLIENT_ID</code> und <code>STRAVA_CLIENT_SECRET</code> setzen (siehe README).']);
    } else if (st.strava.last && !st.strava.last.ok) b.push(['warn', `Letzter Strava-Sync fehlgeschlagen: ${esc(st.strava.last.message)}`]);
    if (!st.garmin.configured) b.push(['', 'Garmin ist noch nicht eingerichtet: <code>GARMIN_EMAIL</code> und <code>GARMIN_PASSWORD</code> setzen oder <code>python -m app.garmin_login</code> ausführen.']);
    else if (st.garmin.last && !st.garmin.last.ok) b.push(['warn', `Letzter Garmin-Sync fehlgeschlagen: ${esc(st.garmin.last.message)}`]);
    if (st.counts.streams_pending > 0 && st.strava.connected) b.push(['', `Z2-Auswertung: ${st.counts.streams_pending} Aktivitäten werden noch nachgeladen (Strava-Rate-Limit, läuft mit jedem Sync weiter).`]);
    $('#banners').innerHTML = b.map(([c, t]) => `<div class="banner ${c}">${t}</div>`).join('');
    $('#conn').innerHTML = `<summary>Claude-Konnektor einrichten</summary>
      <p>In Claude unter <i>Einstellungen → Konnektoren → Benutzerdefinierten Konnektor hinzufügen</i> diese URL eintragen. Sie enthält ein Geheimnis und gehört nur dir:</p>
      <p><code id="mcpurl">${esc(st.mcp_url)}</code> <button type="button" id="copyurl">Kopieren</button></p>
      <p>Danach kann Claude Plan, Einstellungen und Referenzleistungen lesen und ändern (z. B. „Plane die nächsten zwei Wochen anhand meiner Erholungswerte neu“).</p>`;
    $('#copyurl').onclick = async (e) => { try { await navigator.clipboard.writeText(st.mcp_url); e.target.textContent = 'Kopiert'; } catch { e.target.textContent = 'Bitte manuell kopieren'; } };
  }

  /* ------------------------------------------------------------ Gesundheit */
  const kpi = (label, value, note, cls = '') => `<div class="card kpi"><div class="label">${label}</div><div class="value ${cls}">${value}</div><div class="note">${note || '&nbsp;'}</div></div>`;
  function rolling(rows, key, n) {
    const out = [];
    rows.forEach((r, i) => {
      const win = rows.slice(Math.max(0, i - n + 1), i + 1).map((x) => x[key]).filter((v) => v != null);
      if (r[key] != null && win.length) out.push({ x: r.date, y: win.reduce((a, b) => a + b, 0) / win.length });
    });
    return out;
  }
  const delta = (a, b, lowerBetter) => {
    if (a == null || b == null) return { txt: '', cls: 'flat' };
    const d = a - b; const good = lowerBetter ? d < 0 : d > 0;
    return { txt: `${d > 0 ? '▲' : d < 0 ? '▼' : '■'} ${num(Math.abs(d), 1)}`, cls: Math.abs(d) < 0.5 ? 'flat' : good ? 'up' : 'down' };
  };

  function renderHealth() {
    const host = $('#tab-health'), h = S.health, L = h.latest, B = h.baseline;
    if (!Object.keys(L).length && !h.load.some((d) => d.load > 0)) {
      host.innerHTML = '<div class="card empty">Noch keine Gesundheitsdaten. Sobald Garmin eingerichtet ist und ein Sync lief, erscheinen hier Schlaf, HRV, Ruhepuls, Body Battery und mehr.</div>';
      return;
    }
    const v = (k) => L[k]?.value;
    const hrvD = delta(B.hrv_7d, B.hrv_28d, false), rhrD = delta(B.rhr_7d, B.rhr_28d, true);
    const wk = h.weekly[h.weekly.length - 1] || {};
    const f = h.form;
    const asOf = L.sleep_s?.date || L.hrv?.date;
    const cards = [
      kpi('Schlaf', v('sleep_s') != null ? `${sleepFmt(v('sleep_s'))}<small> h</small>` : '–', v('sleep_score') != null ? `Score ${num(v('sleep_score'))} · 7-Tage-Ø ${B.sleep_7d ? sleepFmt(B.sleep_7d) + ' h' : '–'}` : ''),
      kpi('HRV (Nacht)', v('hrv') != null ? `${num(v('hrv'))}<small> ms</small>` : '–', `7 T Ø ${num(B.hrv_7d)} <span class="${hrvD.cls}">${hrvD.txt}</span> vs. 28 T${v('hrv_status') ? ' · ' + esc(String(v('hrv_status')).toLowerCase()) : ''}`),
      kpi('Ruhepuls', v('rhr') != null ? `${num(v('rhr'))}<small> bpm</small>` : '–', `7 T Ø ${num(B.rhr_7d, 1)} <span class="${rhrD.cls}">${rhrD.txt}</span> vs. 28 T`),
      kpi('Body Battery', v('bb_max') != null ? `${num(v('bb_max'))}<small> /100</small>` : '–', v('bb_min') != null ? `Tiefstwert ${num(v('bb_min'))}` : ''),
      kpi('Training Readiness', v('readiness') != null ? num(v('readiness')) : '–', v('readiness_level') ? esc(String(v('readiness_level')).toLowerCase()) : ''),
      kpi('Stress (Ø)', v('stress_avg') != null ? num(v('stress_avg')) : '–', ''),
      kpi('VO₂max', v('vo2max') != null ? num(v('vo2max'), 1) : '–', 'Garmin-Schätzung'),
      kpi('Gewicht', v('weight_kg') != null ? `${num(v('weight_kg'), 1)}<small> kg</small>` : '–', L.weight_kg ? `Stand ${dateDE(L.weight_kg.date, { day: '2-digit', month: '2-digit' })}` : ''),
      kpi('Form (TSB)', f ? num(f.tsb) : '–', f ? `${esc(f.label)} · Fitness ${num(f.ctl)} · Ermüdung ${num(f.atl)}` : '', f ? (f.tsb < -30 ? 'down' : '') : ''),
      kpi('Laufen diese Woche', `${num(wk.run_km, 1)}<small> km</small>`, `Rad ${num(wk.ride_h, 1)} h · Belastung ${num(wk.load)}`),
    ].join('');
    host.innerHTML = `
      <div class="toolbar"><span class="muted">${asOf ? 'Garmin-Stand: ' + dateDE(asOf, { weekday: 'short', day: '2-digit', month: '2-digit' }) : ''}</span><span class="spacer"></span>
        <span class="chips" role="group" aria-label="Zeitraum">${[30, 60, 120].map((d) => `<button type="button" data-days="${d}" aria-pressed="${S.healthDays === d}">${d} Tage</button>`).join('')}</span></div>
      <div class="grid kpis">${cards}</div>
      <div class="grid cols-2">
        <div class="card"><h3>Herzfrequenzvariabilität</h3><p class="sub">Nächtlicher Wert und 7-Tage-Durchschnitt (ms)</p><div id="c-hrv"></div></div>
        <div class="card"><h3>Ruhepuls</h3><p class="sub">Täglich und 7-Tage-Durchschnitt (bpm)</p><div id="c-rhr"></div></div>
        <div class="card"><h3>Schlafdauer</h3><p class="sub">Stunden pro Nacht</p><div id="c-sleep"></div></div>
        <div class="card"><h3>Fitness, Ermüdung und Form</h3><p class="sub">Aus Herzfrequenz und Dauer geschätzt (Fitness 42 Tage, Ermüdung 7 Tage, Form = Fitness − Ermüdung)</p><div id="c-form"></div></div>
      </div>`;
    host.querySelectorAll('[data-days]').forEach((b) => b.onclick = () => { S.healthDays = +b.dataset.days; renderHealth(); });
    const cutoff = addDays(S.status.today, -S.healthDays);
    const rows = h.series.filter((r) => r.date >= cutoff);
    const all = h.series;
    const keep = (arr) => arr.filter((p) => p.x >= cutoff);
    Charts.mount($('#c-hrv'), () => Charts.lineChart($('#c-hrv'), { series: [
      { name: 'HRV (Nacht)', color: css('--neutral'), kind: 'dots', data: rows.filter((r) => r.hrv != null).map((r) => ({ x: r.date, y: r.hrv })), fmt: (y) => `${num(y)} ms` },
      { name: '7-Tage-Ø', color: css('--accent'), kind: 'line', data: keep(rolling(all, 'hrv', 7)), fmt: (y) => `${num(y, 1)} ms` }], y: { fmt: (y) => num(y) }, emptyText: 'Keine HRV-Daten im Zeitraum' }));
    Charts.mount($('#c-rhr'), () => Charts.lineChart($('#c-rhr'), { series: [
      { name: 'Ruhepuls', color: css('--neutral'), kind: 'dots', data: rows.filter((r) => r.rhr != null).map((r) => ({ x: r.date, y: r.rhr })), fmt: (y) => `${num(y)} bpm` },
      { name: '7-Tage-Ø', color: css('--ride'), kind: 'line', data: keep(rolling(all, 'rhr', 7)), fmt: (y) => `${num(y, 1)} bpm` }], y: { fmt: (y) => num(y) }, emptyText: 'Keine Ruhepuls-Daten im Zeitraum' }));
    const sl = rows.filter((r) => r.sleep_s != null).slice(-45);
    Charts.mount($('#c-sleep'), () => Charts.barChart($('#c-sleep'), { labels: sl.map((r) => dateDE(r.date, { day: '2-digit', month: '2-digit' })),
      series: [{ name: 'Schlaf (h)', color: css('--accent'), values: sl.map((r) => r.sleep_s / 3600) }], y: { fmt: (v) => num(v, 1) }, emptyText: 'Keine Schlafdaten im Zeitraum' }));
    const lc = h.load.filter((d) => d.date >= cutoff);
    Charts.mount($('#c-form'), () => Charts.lineChart($('#c-form'), { series: [
      { name: 'Fitness', color: css('--run'), kind: 'area', data: lc.map((d) => ({ x: d.date, y: d.ctl })), fmt: (y) => num(y, 1) },
      { name: 'Ermüdung', color: css('--ride'), kind: 'line', data: lc.map((d) => ({ x: d.date, y: d.atl })), fmt: (y) => num(y, 1) },
      { name: 'Form', color: css('--neutral'), kind: 'dash', data: lc.map((d) => ({ x: d.date, y: d.tsb })), fmt: (y) => num(y, 1) }],
      y: { fmt: (y) => num(y) }, hlines: [{ y: 0 }] }));
  }

  /* ------------------------------------------------------------ Trainingsplan */
  function sessionHtml(s) {
    const meta = [];
    if (s.distance_km) meta.push(`${num(s.distance_km, 1)} km`);
    if (s.duration_min) meta.push(hm(s.duration_min));
    if (s.target) meta.push(esc(s.target));
    const eff = s.effective_status;
    const actual = s.actual_km != null || s.actual_min != null ? `<div class="actual">Ist: ${[s.actual_km ? num(s.actual_km, 1) + ' km' : '', s.actual_min ? s.actual_min + ' min' : ''].filter(Boolean).join(' · ')}</div>` : '';
    const can = !['rest'].includes(s.sport);
    const acts = can ? [
      !['done'].includes(s.status) ? `<button class="ghost" type="button" data-act="done" data-id="${s.id}">Erledigt</button>` : '',
      !['skipped'].includes(s.status) ? `<button class="ghost" type="button" data-act="skipped" data-id="${s.id}">Übersprungen</button>` : '',
      s.status !== 'planned' ? `<button class="ghost" type="button" data-act="planned" data-id="${s.id}">Zurücksetzen</button>` : ''].join('') : '';
    return `<div class="sess"><span class="sport ${s.sport}">${SPORT[s.sport] || s.sport}</span>
      <div><div class="title">${esc(s.title)}</div><div class="meta">${meta.join(' · ')}</div>
        ${s.description ? `<div class="meta">${esc(s.description)}</div>` : ''}${s.notes ? `<div class="meta">Notiz: ${esc(s.notes)}</div>` : ''}${actual}</div>
      <div class="side"><span class="status ${eff}">${STATUS[eff] || eff}</span><div class="actions">${acts}</div></div></div>`;
  }

  function renderPlan() {
    const host = $('#tab-plan'), P = S.plan, today = P.today;
    const wk = addDays(mondayOf(today), S.weekOffset * 7);
    const days = [...Array(7)].map((_, i) => addDays(wk, i));
    const sess = P.sessions.filter((s) => s.date >= wk && s.date <= days[6]);
    const W = P.weeks.find((w) => w.week === wk);
    const nonRest = sess.filter((s) => s.sport !== 'rest');
    const done = nonRest.filter((s) => s.effective_status === 'done').length;
    const pRun = W ? W.planned_run_km : 0, aRun = W ? W.actual_run_km : 0;
    const pMin = W ? W.planned_min : 0, aMin = W ? W.actual_min : 0;
    const pct = (a, b) => (b ? Math.min(100, (a / b) * 100) : 0);
    const dayRows = days.map((d) => {
      const list = sess.filter((s) => s.date === d);
      return `<div class="day ${d === today ? 'today' : ''}"><div class="dname">${dateDE(d, { weekday: 'short' })}<small>${dateDE(d, { day: '2-digit', month: '2-digit' })}</small></div>
        <div>${list.length ? list.map(sessionHtml).join('') : '<span class="muted">Nichts geplant</span>'}</div></div>`;
    }).join('');
    const weeks = P.weeks;
    host.innerHTML = `
      <div class="toolbar"><button type="button" id="wprev" aria-label="Vorherige Woche">←</button><b>KW ${isoWeek(wk)} · ${dateDE(wk, { day: '2-digit', month: '2-digit' })}–${dateDE(days[6])}</b>
        <button type="button" id="wnext" aria-label="Nächste Woche">→</button><button type="button" id="wnow">Diese Woche</button><span class="spacer"></span>
        <span class="muted">Der Plan wird über den Claude-Konnektor gepflegt</span></div>
      <div class="summary">
        <div class="card"><div class="muted">Laufen (Ist / Soll)</div><div class="v">${num(aRun, 1)} / ${num(pRun, 1)} km</div><div class="bar"><i style="width:${pct(aRun, pRun)}%"></i></div></div>
        <div class="card"><div class="muted">Trainingszeit (Ist / Soll)</div><div class="v">${hm(aMin)} / ${hm(pMin)}</div><div class="bar"><i style="width:${pct(aMin, pMin)}%"></i></div></div>
        <div class="card"><div class="muted">Einheiten erledigt</div><div class="v">${done} / ${nonRest.length}</div><div class="bar"><i style="width:${pct(done, nonRest.length)}%"></i></div></div>
      </div>
      <div class="card">${sess.length ? dayRows : '<div class="empty">Für diese Woche ist nichts geplant. Bitte Claude, die Woche zu planen.</div>'}</div>
      <div class="card" style="margin-top:12px"><h3>Laufumfang pro Woche: Plan und Realität</h3><p class="sub">Kilometer</p><div id="c-weeks"></div></div>`;
    $('#wprev').onclick = () => { S.weekOffset--; renderPlan(); };
    $('#wnext').onclick = () => { S.weekOffset++; renderPlan(); };
    $('#wnow').onclick = () => { S.weekOffset = 0; renderPlan(); };
    Charts.mount($('#c-weeks'), () => Charts.barChart($('#c-weeks'), { labels: weeks.map((w) => 'KW ' + isoWeek(w.week)),
      series: [{ name: 'Soll', color: css('--run'), opacity: 0.35, values: weeks.map((w) => w.planned_run_km) }, { name: 'Ist', color: css('--run'), values: weeks.map((w) => w.actual_run_km) }],
      y: { fmt: (v) => num(v) }, emptyText: 'Noch kein Plan vorhanden' }));
  }

  async function setStatus(id, status) {
    await api(`/api/plan/${id}/status`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ status }) });
    S.plan = await api('/api/plan');
    renderPlan();
  }

  /* ------------------------------------------------------------ Prognose & Z2 */
  const trendTxt = (t, unit) => (t ? `Trend der letzten ${t.weeks_used} Wochen: <b class="${t.pct_per_week > 0.02 ? 'up' : t.pct_per_week < -0.02 ? 'down' : 'flat'}">${t.pct_per_week > 0 ? '+' : ''}${num(t.pct_per_week, 2)} % pro Woche</b> (${t.change_pct_over_window > 0 ? '+' : ''}${num(t.change_pct_over_window, 1)} % im Zeitraum, + = besser bei gleichem Puls)`
    : `Für einen Trend braucht es mindestens 4 Wochen mit Z2-Daten (${unit}).`);

  function renderForecast() {
    const host = $('#tab-forecast'), F = S.forecast, set = S.status.settings;
    let top;
    if (!F.available) {
      top = `<div class="card"><h2>Prognose</h2><p class="muted">Noch keine Referenzleistungen (5 km, 10 km oder Halbmarathon) im Zeitfenster der letzten ${set.perf_window_days} Tage.
        Sie kommen automatisch aus den Strava-Bestwerten oder lassen sich von Claude mit <i>add_performance</i> eintragen.</p></div>`;
    } else {
      const rows = F.scenarios.map((s) => `<tr><td><b>${SCEN[s.name] || s.name}</b><div class="muted" style="font-size:12px">${SCEN_HINT[s.name] || ''} · Faktor ${num(s.factor, 2)}</div></td>
        <td class="num">${s.time}</td><td class="num"><b>${s.time_trend}</b></td><td class="num">${s.pace_trend} /km</td>
        <td class="num ${s.vs_goal_s <= 0 ? 'up' : 'down'}">${signed(s.vs_goal_s)}</td></tr>`).join('');
      const refs = F.references.map((r) => `<tr><td>${esc(r.label)}</td><td class="num">${r.time}</td><td>${dateDE(r.date)}</td><td class="muted">${r.source === 'manual' ? 'manuell' : 'Strava-Bestwert'}</td><td class="num">${r.predicted} (${r.predicted_pace} /km)</td></tr>`).join('');
      const tr = F.trend_adjustment_pct;
      top = `<div class="card"><div class="hero"><div><div class="muted">Ziel</div><div class="big">${F.goal}</div><div class="muted">${F.goal_pace} /km</div></div>
          <div><div class="muted">${esc(F.race.name)}</div><div class="big">${dateDE(F.race.date)}</div><div class="muted">noch ${num(F.race.weeks_left, 1)} Wochen</div></div></div></div>
        <div class="grid cols-2" style="margin-top:12px">
          <div class="card"><h3>Marathon-Prognose</h3><p class="sub">Abstand zum Ziel: − schneller, + langsamer</p>
            <div class="scroll-x"><table><thead><tr><th>Szenario</th><th class="num">Prognose</th><th class="num">mit Z2-Trend</th><th class="num">Pace</th><th class="num">zum Ziel</th></tr></thead><tbody>${rows}</tbody></table></div>
            <p class="sub" style="margin-top:10px">Rechnung: Riegel-Formel (Exponent ${num(set.riegel_exponent, 2)}) aus den besten Zeiten der letzten ${set.perf_window_days} Tage, mal Szenariofaktor.
              Der Z2-Trend wirkt auf 50 % gedämpft und höchstens ±5 %: aktuell <b>${tr >= 0 ? '−' : '+'}${num(Math.abs(tr), 1)} %</b> Zeit. Das ist eine Schätzung, keine Garantie.</p></div>
          <div class="card"><h3>Bezugsleistungen</h3><p class="sub">Beste Zeit je Distanz im Zeitfenster und Hochrechnung auf die Rennstrecke</p>
            <div class="scroll-x"><table><thead><tr><th>Distanz</th><th class="num">Zeit</th><th>Datum</th><th>Quelle</th><th class="num">Marathon</th></tr></thead><tbody>${refs}</tbody></table></div>
            <p class="sub" style="margin-top:10px">Bestwerte aus Trainingsläufen unterschätzen das Wettkampfniveau meist. Echte Wettkampf- oder Testzeiten kann Claude eintragen.
              ${F.marathons.length ? '<br>Bisherige Marathons: ' + F.marathons.map((m) => `${dateDE(m.date, { day: '2-digit', month: '2-digit', year: '2-digit' })} ${m.time}`).join(' · ') : ''}</p></div>
        </div>`;
    }
    const run = S.z2.run, ride = S.z2.ride;
    host.innerHTML = `${top}
      <div class="toolbar" style="margin-top:16px"><h2 style="margin:0;font-size:16px">Z2-Verlauf</h2><span class="muted">Herzfrequenzband ${run.hr_band[0]}–${run.hr_band[1]} bpm, Einlaufphase ausgenommen</span><span class="spacer"></span>
        <span class="chips" role="group" aria-label="Zeitraum">${[12, 26, 52].map((w) => `<button type="button" data-weeks="${w}" aria-pressed="${S.z2Weeks === w}">${w} Wochen</button>`).join('')}</span></div>
      <div class="grid cols-2">
        <div class="card"><h3>Z2-Pace beim Laufen</h3><p class="sub">Schneller bei gleichem Puls = bessere aerobe Form (min/km)</p><div id="c-z2run"></div><p class="sub">${trendTxt(run.trend, 'Läufe')}</p></div>
        <div class="card"><h3>Z2-Leistung auf dem Rad</h3><p class="sub">Mehr Watt bei gleichem Puls = bessere aerobe Form (W)</p><div id="c-z2ride"></div><p class="sub">${trendTxt(ride.trend, 'Radfahrten')}</p></div>
        <div class="card"><h3>Laufumfang pro Woche</h3><p class="sub">Kilometer</p><div id="c-vrun"></div></div>
        <div class="card"><h3>Radumfang pro Woche</h3><p class="sub">Stunden (inkl. Rolle)</p><div id="c-vride"></div></div>
      </div>
      <p class="sub" style="margin-top:10px">Z2-Werte schwanken mit Hitze, Müdigkeit, Wind und Gelände. Aussagekräftig ist der Verlauf der Wochenmittel, nicht der einzelne Punkt.</p>`;
    host.querySelectorAll('[data-weeks]').forEach((b) => b.onclick = async () => { S.z2Weeks = +b.dataset.weeks; await loadForecast(); });
    const race = S.status.settings.race_date;
    const z2cfg = (z, col, fmt, reverse, unitName) => ({
      series: [
        { name: 'Einzelne Einheit', color: css('--neutral'), kind: 'dots', data: z.points.map((p) => ({ x: p.date, y: p.value, note: `${p.name} · Ø ${num(p.hr)} bpm · ${num(p.seconds / 60)} min in Z2` })), fmt },
        { name: 'Wochenmittel', color: css(col), kind: 'line', data: z.weekly.map((p) => ({ x: p.date, y: p.value })), fmt },
        ...(z.projection ? [{ name: 'Trend bis zum Rennen (ungedämpft)', color: css(col), kind: 'dash', tip: false, data: z.projection.map((p) => ({ x: p.date, y: p.value })), fmt }] : [])],
      y: { fmt, reverse }, vlines: z.projection ? [{ x: race, label: 'Rennen' }] : [], emptyText: `Noch keine Z2-Daten (${unitName}). Sie entstehen aus den Strava-Streams beim Sync.` });
    Charts.mount($('#c-z2run'), () => Charts.lineChart($('#c-z2run'), z2cfg(run, '--run', (y) => `${pace(y)} /km`, true, 'Laufen')));
    Charts.mount($('#c-z2ride'), () => Charts.lineChart($('#c-z2ride'), z2cfg(ride, '--ride', (y) => `${num(y)} W`, false, 'Rad')));
    const wk = S.weekly;
    const lab = wk.map((w) => dateDE(w.week, { day: '2-digit', month: '2-digit' }));
    Charts.mount($('#c-vrun'), () => Charts.barChart($('#c-vrun'), { labels: lab, series: [{ name: 'Laufen (km)', color: css('--run'), values: wk.map((w) => w.run_km) }], y: { fmt: (v) => num(v) } }));
    Charts.mount($('#c-vride'), () => Charts.barChart($('#c-vride'), { labels: lab, series: [{ name: 'Rad (h)', color: css('--ride'), values: wk.map((w) => w.ride_h) }], y: { fmt: (v) => num(v, 1) } }));
  }

  async function loadForecast() {
    const host = $('#tab-forecast');
    try {
      const w = S.z2Weeks;
      [S.forecast, S.z2.run, S.z2.ride, S.weekly] = await Promise.all([api('/api/forecast'), api(`/api/z2?sport=run&weeks=${w}`), api(`/api/z2?sport=ride&weeks=${w}`), api(`/api/weekly?weeks=${Math.max(w, 12)}`)]);
      renderForecast();
    } catch (e) { failBox(host, e); }
  }

  /* ------------------------------------------------------------ Steuerung */
  const LOADERS = {
    health: async () => { try { S.health = await api('/api/health?days=120'); renderHealth(); } catch (e) { failBox($('#tab-health'), e); } },
    plan: async () => { try { S.plan = await api('/api/plan'); renderPlan(); } catch (e) { failBox($('#tab-plan'), e); } },
    forecast: loadForecast,
  };

  async function show(tab, force) {
    document.querySelectorAll('.tab').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
    ['health', 'plan', 'forecast'].forEach((t) => { $('#tab-' + t).hidden = t !== tab; });
    if (location.hash !== '#' + tab) history.replaceState(null, '', '#' + tab);
    if (force || !S.loaded[tab]) { S.loaded[tab] = true; await LOADERS[tab](); }
  }
  const current = () => (location.hash || '#health').slice(1).replace(/[^a-z]/g, '') || 'health';

  async function refreshAll() {
    S.status = await api('/api/status');
    renderHeader();
    S.loaded = {};
    await show(LOADERS[current()] ? current() : 'health', true);
  }

  document.querySelectorAll('.tab').forEach((b) => b.addEventListener('click', () => show(b.dataset.tab)));
  document.querySelectorAll('.tab').forEach((b) => b.addEventListener('keydown', (e) => {
    const tabs = [...document.querySelectorAll('.tab')]; const i = tabs.indexOf(b);
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { const n = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length]; n.focus(); show(n.dataset.tab); }
  }));
  $('#tab-plan').addEventListener('click', (e) => { const b = e.target.closest('[data-act]'); if (b) setStatus(+b.dataset.id, b.dataset.act).catch((err) => alert(err.message)); });
  $('#syncbtn').addEventListener('click', async (e) => {
    const btn = e.currentTarget; btn.disabled = true; const old = btn.textContent; btn.textContent = 'Synchronisiere …';
    try { const r = await api('/api/sync', { method: 'POST' }); if (r.status === 'busy') alert(r.message); await refreshAll(); }
    catch (err) { alert('Sync fehlgeschlagen: ' + err.message); }
    finally { btn.disabled = false; btn.textContent = old; }
  });
  window.addEventListener('hashchange', () => { const t = current(); if (LOADERS[t]) show(t); });

  refreshAll().catch((e) => { $('#banners').innerHTML = `<div class="banner warn">Dashboard konnte nicht geladen werden: ${esc(e.message)}</div>`; });
})();
