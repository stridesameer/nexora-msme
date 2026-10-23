/* NEXORA dashboard. Vanilla JS, no dependencies, no external requests.
   All user-supplied text is inserted with esc() or textContent (never raw HTML). */
(() => {
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const inrFmt = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 });
const money = v => (v === null || v === undefined) ? 'Insufficient data' : inrFmt.format(v);
const fmtDate = iso => iso ? new Date(iso + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
const shortDate = iso => iso ? new Date(iso + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }) : '';
const nextDay = iso => { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + 1); return d.toISOString().slice(0, 10); };
const compact = v => {
  const a = Math.abs(v), s = v < 0 ? '-' : '';
  if (a >= 1e7) return s + '₹' + (a / 1e7).toFixed(1).replace(/\.0$/, '') + 'Cr';
  if (a >= 1e5) return s + '₹' + (a / 1e5).toFixed(1).replace(/\.0$/, '') + 'L';
  if (a >= 1e3) return s + '₹' + (a / 1e3).toFixed(0) + 'K';
  return s + '₹' + Math.round(a);
};
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

const MAX_BYTES = 1000000;
const DEMO_NAME = 'Sharma Traders (demo)';
const LABEL = { healthy: 'Healthy', attention: 'Needs attention', high_risk: 'High risk', unknown: 'Insufficient data', high: 'High', medium: 'Medium', low: 'Low', ok: 'OK', below_reserve: 'Below reserve', shortfall: 'Shortfall', covered: 'Covered', overdue: 'Overdue', due_soon: 'Due soon', scheduled: 'Scheduled', unscheduled: 'No date', beyond_horizon: 'After 30 days' };
const KIND = { bill: 'Supplier bill', loan: 'Loan repayment', recurring: 'Recurring (estimated)' };
const CARD_KEYS = ['current_balance', 'total_income', 'total_expenses', 'outstanding_receivables', 'upcoming_loan_repayments', 'upcoming_expenses', 'avg_daily_outflow', 'min_projected_30'];

const state = { csv: null, name: '', validation: null, result: null, horizon: 30, severity: 'all', overrides: {}, seq: 0, openAlerts: new Set() };

/* ---------- small UI helpers ---------- */
function banner(kind, text, spin) {
  const b = $('#banner');
  if (!text) { b.hidden = true; return; }
  b.className = 'banner ' + kind;
  b.textContent = '';
  if (spin) { const s = document.createElement('span'); s.className = 'spinner'; b.append(s); }
  b.append(document.createTextNode(text));
  b.hidden = false;
}
let toastTimer;
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 2800);
}
function showDash(on) { $('#dash').hidden = !on; $('#empty').hidden = on; }

async function api(path, payload) {
  let res;
  try {
    res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  } catch (e) { throw new Error('Could not reach the NEXORA server. Check that it is running and try again.'); }
  let data = null;
  try { data = await res.json(); } catch (e) { /* handled below */ }
  if (!data) throw new Error('Unexpected response from the server (HTTP ' + res.status + ').');
  return { status: res.status, data };
}

/* ---------- settings <-> inputs ---------- */
const num = sel => { const v = $(sel).value; return v === '' ? null : Number(v); };
function buildSettings() {
  return {
    business_name: $('#bizName').value,
    opening_balance: num('#sOpening'),
    min_reserve: num('#sReserve') ?? 0,
    receipt_delay_days: num('#sDelay') ?? 0,
    expense_adjust_pct: num('#sAdj') ?? 0,
    daily_variable_spend: num('#sVar') ?? 0,
    collect_overdue: $('#sCollect').checked,
    overdue_lag_days: num('#sLag') ?? 7,
    as_of: $('#sAsOf').value || null,
    period_days: $('#period').value ? Number($('#period').value) : null,
    payment_overrides: state.overrides,
  };
}
function resetAssumptionInputs() {
  $('#sOpening').value = ''; $('#sReserve').value = 25000; $('#sDelay').value = 0; $('#sAdj').value = 0;
  $('#sVar').value = 0; $('#sCollect').checked = true; $('#sLag').value = 7; $('#sAsOf').value = ''; $('#period').value = '';
  state.overrides = {};
}

/* ---------- data loading ---------- */

function renderValidation(v) {
  const el = $('#valStatus'); el.hidden = false;
  if (v.fatal_errors.length) {
    el.innerHTML = `<div class="counts"><span class="pill bad">Cannot analyze this file</span></div><ul>${v.fatal_errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul>`;
    return;
  }
  let h = `<div class="counts"><span class="pill good">${v.accepted_count} accepted</span><span class="pill ${v.rejected_count ? 'bad' : 'good'}">${v.rejected_count} rejected</span><span class="pill">${v.rows_read} rows read</span>${v.warnings.length ? `<span class="pill warn">${v.warnings.length} notice(s)</span>` : ''}</div>`;
  if (v.rejected.length) h += `<p class="hint">Rejected rows (not used in any calculation):</p><ul>${v.rejected.slice(0, 10).map(r => `<li>Line ${r.line}${r.transaction_id ? ' (' + esc(r.transaction_id) + ')' : ''}: ${esc(r.errors.join('; '))}</li>`).join('')}${v.rejected_count > 10 ? `<li>…and ${v.rejected_count - 10} more</li>` : ''}</ul>`;
  if (v.warnings.length) h += `<p class="hint" style="margin-top:8px">Notices:</p><ul>${v.warnings.slice(0, 8).map(w => `<li>${esc(w.message)}</li>`).join('')}${v.warnings.length > 8 ? `<li>…and ${v.warnings.length - 8} more</li>` : ''}</ul>`;
  if (v.preview.length) {
    h += `<p class="hint" style="margin-top:10px">Preview of the first ${v.preview.length} accepted records:</p><div class="table-scroll"><table class="tbl"><tr><th>Line</th><th>ID</th><th>Date</th><th>Description</th><th>Type</th><th class="n">Amount</th><th>Category</th><th>Due</th><th>Expected</th><th>Paid</th></tr>${v.preview.map(p => `<tr><td>${p.line}</td><td>${esc(p.transaction_id)}</td><td>${esc(p.transaction_date)}</td><td>${esc(p.description)}</td><td>${esc(p.transaction_type)}</td><td class="n">${money(p.amount)}</td><td>${esc(p.category)}</td><td>${esc(p.due_date)}</td><td>${esc(p.expected_payment_date)}</td><td>${esc(p.actual_payment_date)}</td></tr>`).join('')}</table></div>`;
  }
  el.innerHTML = h;
}

async function loadCsv(text, name, auto) {
  state.seq++;
  state.csv = text; state.name = name; state.result = null; state.validation = null;
  resetAssumptionInputs();
  showDash(false);
  banner('info', 'Checking the file…', true);
  $('#btnAnalyze').disabled = true;
  try {
    const { data } = await api('/api/validate', { csv: text });
    state.validation = data;
    renderValidation(data);
    $('#btnAnalyze').disabled = !data.ok;
    if (!data.ok) {
      banner('error', data.fatal_errors[0] || 'No valid records were found in this file. Fix the rows listed below and upload again.');
    } else if (auto) {
      await runAnalysis(true);
    } else {
      banner('info', `${data.accepted_count} valid record(s), ${data.rejected_count} rejected. Check the preview, then press “Analyze records”.`);
      $('#valStatus').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  } catch (e) { banner('error', e.message); }
}

async function loadDemo() {
  banner('info', 'Loading the demo business…', true);
  try {
    const res = await fetch('/api/sample');
    if (!res.ok) throw new Error('Could not load the demo data (HTTP ' + res.status + ').');
    $('#bizName').value = DEMO_NAME;
    await loadCsv(await res.text(), 'sample_transactions.csv', true);
  } catch (e) { banner('error', e.message.startsWith('Could not') ? e.message : 'Could not load the demo data. Check that the server is running.'); }
}

async function handleFile(file) {
  if (!file) return;
  if (!/\.csv$/i.test(file.name) && !/csv|text\/plain/.test(file.type)) { banner('error', 'Please choose a .csv file.'); return; }
  if (file.size > MAX_BYTES) { banner('error', `That file is ${(file.size / 1e6).toFixed(1)} MB. The limit is 1 MB.`); return; }
  let text;
  try { text = await file.text(); } catch (e) { banner('error', 'Could not read that file.'); return; }
  if ($('#bizName').value === DEMO_NAME) $('#bizName').value = 'My Business';
  await loadCsv(text, file.name, false);
}

async function runAnalysis(first) {
  if (!state.csv) return;
  const seq = ++state.seq;
  $('#dash').classList.add('loading');
  if (first) banner('info', 'Analyzing…', true);
  try {
    const { status, data } = await api('/api/analyze', { csv: state.csv, settings: buildSettings() });
    if (seq !== state.seq) return;
    if (status === 400) { banner('error', data.error || 'Those assumptions are not valid.'); return; }
    if (!data.ok) { banner('error', data.error || 'The analysis failed.'); showDash(false); return; }
    state.result = data;
    showDash(true);
    render();
    if (first) banner('ok', `Analysis ready: ${data.validation.accepted_count} records used${data.validation.rejected_count ? `, ${data.validation.rejected_count} rejected` : ''}.`);
    else banner('', '');
  } catch (e) { if (seq === state.seq) banner('error', e.message); }
  finally { if (seq === state.seq) $('#dash').classList.remove('loading'); }
}
const recalc = debounce(() => { if (state.result) runAnalysis(false); }, 250);

function resetAll() {
  state.seq++; state.csv = null; state.result = null; state.validation = null; state.overrides = {}; state.openAlerts.clear();
  resetAssumptionInputs(); $('#file').value = ''; $('#valStatus').hidden = true; $('#valStatus').innerHTML = '';
  $('#btnAnalyze').disabled = true; $('#bizName').value = 'My Business'; $('#asOfChip').textContent = 'No data loaded';
  banner('', ''); showDash(false); window.scrollTo({ top: 0, behavior: 'smooth' }); toast('Reset: all data cleared from this page');
}

/* ---------- rendering ---------- */
function render() {
  const r = state.result;
  $('#asOfChip').textContent = `Reporting date ${fmtDate(r.meta.as_of)} · ${r.meta.period_days} day(s) in period · ${r.meta.record_count} records`;
  const src = r.meta.opening_balance_source;
  $('#sOpeningHint').textContent = src === 'file' ? `From file: ${money(r.meta.opening_balance)}` : src === 'assumed_zero' ? 'None supplied: assuming ₹0' : '';
  const sg = r.forecast.suggested_daily_variable_spend;
  $('#sVarHint').textContent = sg === null ? '(needs 7+ days of history)' : `(≈ ${money(sg)}/day of non-recurring spend so far)`;
  renderCards(r); renderForecast(r); renderHealth(r); renderAlerts(r); renderTables(r); renderRecs(r);
}

function renderCards(r) {
  const by = Object.fromEntries(r.metrics.map(m => [m.key, m]));
  const reserve = r.meta.min_reserve;
  $('#cards').innerHTML = CARD_KEYS.map(k => {
    const m = by[k]; let tone = '', note = m.note || '';
    if (k === 'min_projected_30') { tone = m.value < 0 ? 'bad' : m.value < reserve ? 'warn' : 'good'; note = 'lowest on ' + fmtDate(m.note); }
    if (k === 'current_balance') tone = m.value < 0 ? 'bad' : '';
    if (k === 'outstanding_receivables') { const o = by.overdue_receivables; note = `${money(o.value)} overdue` + (o.note ? ` (${o.note})` : ''); if (o.value > 0) tone = 'warn'; }
    const na = m.value === null;
    const val = na ? 'Insufficient data' : m.unit === 'days' ? m.value + ' days' : money(m.value);
    return `<div class="card ${tone}"><div class="lbl">${esc(m.label)}</div><div class="val2 ${na ? 'na' : ''}">${esc(val)}</div><div class="note">${esc(na ? m.note : note)}</div><div class="formula">ƒ ${esc(m.formula)}</div></div>`;
  }).join('');
}

function renderForecast(r) {
  const h = state.horizon, hz = r.forecast.horizons[h], rows = r.forecast.rows.slice(0, h), reserve = r.meta.min_reserve;
  $$('.seg-btn[data-h]').forEach(b => b.setAttribute('aria-pressed', String(Number(b.dataset.h) === h)));
  const tone = hz.funding_gap > 0 ? 'bad' : hz.reserve_gap > 0 ? 'warn' : 'good';
  $('#fcStats').innerHTML = [
    ['Lowest projected balance', money(hz.min_balance) + ' · ' + shortDate(hz.min_date), tone],
    ['Funding gap', hz.funding_gap > 0 ? money(hz.funding_gap) : 'None', hz.funding_gap > 0 ? 'bad' : 'good'],
    ['Days below reserve', `${hz.days_below_reserve} of ${h}`, hz.days_below_reserve ? 'warn' : 'good'],
    [`Balance in ${h} days`, money(hz.ending_balance), ''],
    ['Expected money in', money(hz.total_inflow), ''],
    ['Expected money out', money(hz.total_outflow), ''],
  ].map(([k, v, t]) => `<div class="stat ${t}"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');
  drawChart(r, h);
  let d = '';
  if (hz.funding_gap > 0 || hz.reserve_gap > 0) {
    d += `<b>What drives the low point on ${esc(fmtDate(hz.min_date))}</b><ul>${hz.contributors.map(c => `<li>${esc(c.description)}: ${money(c.amount)} on ${shortDate(c.date)}</li>`).join('') || '<li>No scheduled payments before this date: the balance was already low.</li>'}</ul>`;
    if (hz.late_receipts.length) d += `<b>Money that arrives after the low point</b><ul>${hz.late_receipts.map(c => `<li>${esc(c.description)}: ${money(c.amount)} on ${shortDate(c.date)}</li>`).join('')}</ul>`;
    if (r.forecast.scenario) d += `<p>Your assumptions change the lowest balance from ${money(r.forecast.scenario.baseline_min_balance)} (unchanged data) to ${money(r.forecast.scenario.scenario_min_balance)}.</p>`;
  } else d = '<p>No shortfall and no reserve breach in this window, on the data and assumptions above.</p>';
  $('#drivers').innerHTML = d;
  $('#fcTable').innerHTML = `<tr><th>Date</th><th class="n">Money in</th><th class="n">Money out</th><th class="n">Closing balance</th><th>Status</th><th>What happens</th></tr>` + rows.map(x =>
    `<tr class="${x.status}"><td>${esc(shortDate(x.date))}</td><td class="n">${x.inflow ? money(x.inflow) : '—'}</td><td class="n">${x.outflow ? money(x.outflow) : '—'}</td><td class="n"><b>${money(x.closing)}</b></td><td><span class="badge b-${x.status}">${LABEL[x.status]}</span></td><td class="ev">${x.events.map(e => `${esc(e.description)} ${e.direction === 'in' ? '+' : '−'}${money(e.amount)}`).join('; ')}</td></tr>`).join('');
  $('#assumeList').innerHTML = r.forecast.assumptions.map(a => `<li>${esc(a)}</li>`).join('') + r.forecast.excluded.map(x => `<li>Not in forecast: ${esc(x.description)} (${money(x.amount)}), ${esc(x.reason)}</li>`).join('');
  $('#recurList').innerHTML = r.forecast.recurring.length ? `<p style="margin-top:8px"><b>Recurring costs detected:</b></p><ul>${r.forecast.recurring.map(x => `<li>${esc(x.description)}: ${money(x.amount)} ${esc(x.period)}${x.projected_dates.length ? `, projected ${x.projected_dates.length}× in window` : ''}</li>`).join('')}</ul>` : '';
}

function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= raw) return m * p;
  return 10 * p;
}
function drawChart(r, h) {
  const rows = r.forecast.rows.slice(0, h), reserve = r.meta.min_reserve, hz = r.forecast.horizons[h];
  const pts = [{ date: r.meta.as_of, closing: hz.start_balance, inflow: 0, outflow: 0, status: 'ok', today: true }, ...rows];
  const W = 860, H = 330, L = 64, R = 30, T = 16, B = 32;
  const ys = pts.map(p => p.closing);
  let lo = Math.min(0, ...ys), hi = Math.max(reserve, ...ys); if (hi <= lo) hi = lo + 1;
  const pad = (hi - lo) * 0.08; if (lo < 0) lo -= pad; hi += pad;
  const x = i => L + (W - L - R) * i / (pts.length - 1);
  const y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const step = niceStep((hi - lo) / 4);
  let grid = '';
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) grid += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="#1F2C47" stroke-width="1"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="#8CA0BD">${compact(v)}</text>`;
  const every = Math.max(1, Math.ceil(pts.length / 7));
  let xl = '';
  pts.forEach((p, i) => { if (i % every === 0) xl += `<text x="${x(i)}" y="${H - 10}" text-anchor="middle" font-size="11" fill="#8CA0BD">${shortDate(p.date)}</text>`; });
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.closing).toFixed(1)}`).join(' ');
  const area = `${line} L${x(pts.length - 1).toFixed(1)},${y(lo).toFixed(1)} L${x(0).toFixed(1)},${y(lo).toFixed(1)} Z`;
  const minIdx = pts.findIndex(p => p.date === hz.min_date && !p.today);
  const dots = pts.map((p, i) => {
    const c = p.closing < 0 ? '#EF4444' : p.closing < reserve ? '#F59E0B' : '#22D3EE';
    return (p.inflow || p.outflow || i === minIdx || h === 7 || p.today) ? `<circle cx="${x(i).toFixed(1)}" cy="${y(p.closing).toFixed(1)}" r="${i === minIdx ? 5.5 : 3}" fill="${c}" ${i === minIdx ? 'stroke="#E6EDF7" stroke-width="1.5"' : ''}/>` : '';
  }).join('');
  const shortfall = lo < 0 ? `<rect x="${L}" y="${y(0)}" width="${W - L - R}" height="${y(lo) - y(0)}" fill="#EF4444" opacity=".10"/><line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="#EF4444" stroke-width="1.2"/>` : '';
  const lowLabel = minIdx > 0 ? `<text x="${Math.min(Math.max(x(minIdx), L + 40), W - R - 40)}" y="${y(pts[minIdx].closing) + (pts[minIdx].closing < 0 ? 20 : -10)}" text-anchor="middle" font-size="11.5" font-weight="700" fill="#E6EDF7">Low ${compact(pts[minIdx].closing)}</text>` : '';
  $('#chartWrap').innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Projected cash balance for the next ${h} days. Lowest ${money(hz.min_balance)} on ${fmtDate(hz.min_date)}. The table below lists every day.">
<defs><linearGradient id="g1" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#22D3EE" stop-opacity=".30"/><stop offset="1" stop-color="#22D3EE" stop-opacity="0"/></linearGradient></defs>
${grid}${shortfall}
<path d="${area}" fill="url(#g1)"/>
<line x1="${L}" x2="${W - R}" y1="${y(reserve)}" y2="${y(reserve)}" stroke="#F59E0B" stroke-width="1.3" stroke-dasharray="6 5"/>
<text x="${W - R - 4}" y="${y(reserve) - 5}" text-anchor="end" font-size="11" fill="#F59E0B">Reserve ${compact(reserve)}</text>
<path d="${line}" fill="none" stroke="#22D3EE" stroke-width="2.4" stroke-linejoin="round"/>
${dots}${lowLabel}${xl}
<line id="guide" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="#8CA0BD" stroke-dasharray="3 4" visibility="hidden"/>
<rect id="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/></svg>
<div class="legend"><span style="--c:#22D3EE">Projected balance (estimate)</span><span style="--c:#F59E0B">Minimum reserve</span><span style="--c:#EF4444">Below zero</span></div><div class="tip" id="tip" hidden></div>`;
  const svg = $('#chartWrap svg'), tip = $('#tip'), guide = $('#guide');
  const move = ev => {
    const rect = svg.getBoundingClientRect(), vx = (ev.clientX - rect.left) / rect.width * W;
    const i = Math.max(0, Math.min(pts.length - 1, Math.round((vx - L) / (W - L - R) * (pts.length - 1))));
    const p = pts[i];
    guide.setAttribute('x1', x(i)); guide.setAttribute('x2', x(i)); guide.setAttribute('visibility', 'visible');
    tip.hidden = false;
    tip.innerHTML = `<b>${esc(fmtDate(p.date))}</b><br>Balance ${money(p.closing)}${p.today ? ' (today)' : `<br>In ${money(p.inflow)} · Out ${money(p.outflow)}`}`;
    tip.style.left = (x(i) / W * rect.width + 8) + 'px'; tip.style.top = (y(p.closing) / H * rect.height + 8) + 'px';
  };
  const leave = () => { tip.hidden = true; guide.setAttribute('visibility', 'hidden'); };
  svg.addEventListener('pointermove', move); svg.addEventListener('pointerdown', move); svg.addEventListener('pointerleave', leave);
}

function renderHealth(r) {
  const hp = r.health, s = hp.score;
  const col = s === null ? '#8CA0BD' : s >= 75 ? '#10B981' : s >= 45 ? '#F59E0B' : '#EF4444', C = 2 * Math.PI * 60;
  const ring = `<svg viewBox="0 0 150 150" role="img" aria-label="Health index ${s === null ? 'not available' : s + ' out of 100'}"><circle cx="75" cy="75" r="60" fill="none" stroke="#1F2C47" stroke-width="12"/>${s === null ? '' : `<circle cx="75" cy="75" r="60" fill="none" stroke="${col}" stroke-width="12" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - s / 100)}" transform="rotate(-90 75 75)"/>`}<text x="75" y="84" text-anchor="middle" font-size="${s === null ? 16 : 36}" font-weight="700" fill="#E6EDF7">${s === null ? 'n/a' : s}</text></svg>`;
  $('#healthBody').innerHTML = `<div class="score">${ring}<div class="lab" style="color:${col}">${esc(hp.label)}</div><small>NEXORA Health Index (0–100)<br>heuristic summary, not a credit rating</small></div>
<div><div class="dims">${hp.dimensions.map(d => `<div class="dim"><span class="nm">${esc(d.label)}</span><span class="badge b-${d.status}">${LABEL[d.status]}</span><span class="vl">${esc(d.value)}</span><details><summary>How this is judged</summary><p>${esc(d.rule)}</p><p>${esc(d.explanation)}</p></details></div>`).join('')}</div>
<p class="hint" style="margin-top:12px"><b>Main factors:</b></p><ul class="factors">${hp.factors.map(f => `<li>${esc(f)}</li>`).join('')}</ul>
<details class="assume-list"><summary>Score formula and limits</summary><p>${esc(hp.formula)}</p><p>${esc(hp.disclaimer)}</p></details></div>`;
}

function renderAlerts(r) {
  const ws = r.warnings, cnt = s => s === 'all' ? ws.length : ws.filter(w => w.severity === s).length;
  $$('#alertFilter .seg-btn').forEach(b => { const s = b.dataset.sev; b.textContent = `${s === 'all' ? 'All' : LABEL[s]} (${cnt(s)})`; b.setAttribute('aria-pressed', String(s === state.severity)); });
  const list = ws.filter(w => state.severity === 'all' || w.severity === state.severity);
  const key = w => [w.kind, w.title, w.affected.join(',')].join('|');
  $('#alertList').innerHTML = list.length ? list.map(w => {
    const open = state.openAlerts.has(key(w));
    return `<article class="alert ${w.severity}"><div class="top"><span class="badge b-${w.severity}">${LABEL[w.severity]}</span><span class="t">${esc(w.title)}</span></div><p class="obs">${esc(w.observed)}</p>${w.affected.length ? `<div class="chips">${w.affected.filter(Boolean).map(a => `<span class="chip">${esc(a)}</span>`).join('')}</div>` : ''}<button type="button" class="btn small" data-why="${esc(key(w))}" aria-expanded="${open}">${open ? 'Hide details' : 'Why was this triggered?'}</button><div class="why" ${open ? '' : 'hidden'}><p><b>Rule:</b> ${esc(w.rule)}</p><p><b>Why it matters:</b> ${esc(w.why)}</p><p><b>Suggested next step:</b> ${esc(w.next_step)}</p></div></article>`;
  }).join('') : `<p class="hint">${ws.length ? 'No warnings at this severity.' : 'No warnings: none of the checks fired on this data.'}</p>`;
  $('#skipped').innerHTML = r.checks_skipped.map(s => `<li>${esc(s)}</li>`).join('');
}

function renderTables(r) {
  const min = nextDay(r.meta.as_of);
  $('#recvTable').innerHTML = `<tr><th>Invoice</th><th class="n">Amount</th><th>Due</th><th>Expected</th><th>Forecast pays on</th><th>Status</th></tr>` + (r.receivables.length ? r.receivables.map(x =>
    `<tr><td>${esc(x.description)}<div class="ev">${esc(x.id)}${x.note ? ' · ' + esc(x.note) : ''}</div></td><td class="n">${money(x.amount)}</td><td>${esc(shortDate(x.due_date)) || '—'}</td><td>${esc(shortDate(x.expected_date)) || '—'}</td><td><input type="date" data-id="${esc(x.id)}" min="${min}" value="${esc(x.forecast_date || '')}" aria-label="Expected payment date for ${esc(x.id)}">${x.overridden ? ` <button type="button" class="link" data-reset="${esc(x.id)}" title="Undo my change">↺</button>` : ''}</td><td><span class="badge b-${x.status}">${LABEL[x.status] || x.status}</span>${x.days_overdue ? `<div class="ev">${x.days_overdue}d late</div>` : ''}</td></tr>`).join('') : '<tr><td colspan="6" class="ev">No unpaid invoices.</td></tr>');
  $('#payTable').innerHTML = `<tr><th>Date</th><th>Payment</th><th>Type</th><th class="n">Amount</th><th class="n">Balance after that day</th><th>Covered?</th></tr>` + (r.obligations.length ? r.obligations.map(o =>
    `<tr class="${o.status === 'shortfall' ? 'shortfall' : o.status === 'below_reserve' ? 'below_reserve' : ''}"><td>${o.editable ? `<input type="date" data-id="${esc(o.id)}" min="${min}" value="${esc(o.date)}" aria-label="Payment date for ${esc(o.id)}">${o.overridden ? ` <button type="button" class="link" data-reset="${esc(o.id)}" title="Undo my change">↺</button>` : ''}` : esc(shortDate(o.date))}</td><td>${esc(o.description)}${o.late ? '<div class="ev">Past due: assumed paid next day</div>' : ''}</td><td>${KIND[o.kind] || o.kind}</td><td class="n">${money(o.amount)}</td><td class="n">${money(o.balance_after_day)}</td><td><span class="badge b-${o.status}">${LABEL[o.status]}</span></td></tr>`).join('') : '<tr><td colspan="6" class="ev">No payments due in the next 30 days.</td></tr>');
}

function renderRecs(r) {
  $('#recList2').innerHTML = r.recommendations.length ? r.recommendations.map(x =>
    `<article class="rec"><span class="badge b-${x.priority}">${LABEL[x.priority]} priority</span><h3>${esc(x.title)}</h3><p>${esc(x.action)}</p><ul class="evid">${x.evidence.map(e => `<li>${esc(e)}</li>`).join('')}</ul><div class="src">Rule-based${x.related_warnings.length ? ' · linked to ' + x.related_warnings.map(esc).join(', ') : ''}</div></article>`).join('') : '<p class="hint">No recommendations for this data.</p>';
}

/* ---------- events ---------- */
$('#btnDemo').addEventListener('click', loadDemo);
$('#btnDemo2').addEventListener('click', loadDemo);
$('#btnPick').addEventListener('click', () => $('#file').click());
$('#btnReset').addEventListener('click', resetAll);
$('#btnAnalyze').addEventListener('click', () => runAnalysis(true));
$('#file').addEventListener('change', e => { handleFile(e.target.files[0]); });
const drop = $('#drop');
['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => handleFile(e.dataTransfer.files[0]));

$$('.seg-btn[data-h]').forEach(b => b.addEventListener('click', () => { state.horizon = Number(b.dataset.h); if (state.result) renderForecast(state.result); }));
$$('#alertFilter .seg-btn').forEach(b => b.addEventListener('click', () => { state.severity = b.dataset.sev; if (state.result) renderAlerts(state.result); }));
$('#showFormulas').addEventListener('change', e => document.body.classList.toggle('show-formulas', e.target.checked));

['#sOpening', '#sReserve', '#sDelay', '#sAdj', '#sVar', '#sLag'].forEach(s => $(s).addEventListener('input', recalc));
['#sCollect', '#sAsOf', '#period'].forEach(s => $(s).addEventListener('change', recalc));
$('#bizName').addEventListener('input', () => { /* name is display-only */ });
$('#useSuggested').addEventListener('click', () => {
  const sg = state.result && state.result.forecast.suggested_daily_variable_spend;
  if (sg === null || sg === undefined) { toast('Not enough history to suggest a value yet'); return; }
  $('#sVar').value = Math.round(sg); recalc();
});
$('#btnResetAssume').addEventListener('click', () => {
  const period = $('#period').value; resetAssumptionInputs(); $('#period').value = period;
  if (state.result) { runAnalysis(false); toast('Assumptions reset'); }
});

document.addEventListener('change', e => {
  const t = e.target;
  if (t.matches('input[type=date][data-id]')) {
    if (t.value) state.overrides[t.dataset.id] = t.value; else delete state.overrides[t.dataset.id];
    runAnalysis(false); toast('Forecast recalculated with your date');
  }
});
document.addEventListener('click', e => {
  const reset = e.target.closest('[data-reset]');
  if (reset) { delete state.overrides[reset.dataset.reset]; runAnalysis(false); return; }
  const why = e.target.closest('[data-why]');
  if (why) {
    const k = why.dataset.why;
    if (state.openAlerts.has(k)) state.openAlerts.delete(k); else state.openAlerts.add(k);
    const open = state.openAlerts.has(k);
    why.setAttribute('aria-expanded', String(open)); why.textContent = open ? 'Hide details' : 'Why was this triggered?';
    why.nextElementSibling.hidden = !open;
  }
});

/* navigation: smooth scroll, guard sections that need data, highlight current section */
$$('.nav').forEach(a => a.addEventListener('click', e => {
  const id = a.dataset.target;
  if (id !== 'data' && !state.result) { e.preventDefault(); toast('Load the demo or upload a CSV first'); $('#data').scrollIntoView({ behavior: 'smooth' }); }
}));
let ticking = false;
function spy() {
  ticking = false;
  let cur = 'data';
  for (const a of $$('.nav')) { const s = document.getElementById(a.dataset.target); if (s && s.offsetParent !== null && s.getBoundingClientRect().top <= 140) cur = a.dataset.target; }
  $$('.nav').forEach(a => a.classList.toggle('active', a.dataset.target === cur));
}
window.addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(spy); } }, { passive: true });

resetAssumptionInputs();
spy();
})();
