/* LGD Desk front end. All numbers come from data/*.json (public sources, fetched by GitHub Actions) or from the user's own entries (localStorage).
   Same structure as STTIE and Gold Desk. No AI produces any number or call. */
(function () {
"use strict";
const $ = (id) => document.getElementById(id);
const LS = "lgddesk.v1";
const DEF = { lang: "gu", pricePct: 5.0, demandPct: 15.0, quotes: [] };
let S = Object.assign({}, DEF, JSON.parse(localStorage.getItem(LS) || "{}"));
const save = () => localStorage.setItem(LS, JSON.stringify(S));
let L = null, M = null, P = null, R = null;   // live, model, predictions, rates

const f2 = (x, d = 2) => (x == null || isNaN(x)) ? "—" : Number(x).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const usd = (x, d = 2) => (x == null || isNaN(x)) ? "—" : "US$" + f2(x, d);
const rs = (x) => (x == null || isNaN(x)) ? "—" : "₹" + Math.round(x).toLocaleString("en-IN");
const pct = (x, d = 1) => (x == null || isNaN(x)) ? "—" : (x > 0 ? "+" : "") + Number(x).toFixed(d) + "%";
const cls = (x) => x > 0 ? "up" : x < 0 ? "down" : "flat";
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pill = (k, t) => `<span class="pill p-${k}">${t || k}</span>`;
const fill = (t, o) => t.replace(/\{(\w+)\}/g, (_, k) => o[k] != null ? o[k] : "");
const T = () => window.STR[S.lang] || window.STR.gu;
function mname(m, lang) { if (!m) return "—"; const t = window.STR[lang || S.lang]; return t.months[Number(m.slice(5, 7)) - 1] + " " + m.slice(0, 4); }
function madd(m, k) { let y = +m.slice(0, 4), mo = +m.slice(5, 7) - 1 + k; y += Math.floor(mo / 12); mo = ((mo % 12) + 12) % 12; return y + "-" + String(mo + 1).padStart(2, "0"); }

/* ---------- audit trail: every number can show source, date and formula ---------- */
function audit(val, a) {
  const st = a.status || "est";
  const body = [a.source ? `<b>Source:</b> ${a.url ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.source)}</a>` : esc(a.source)}` : "",
    a.asof ? `<b>As of:</b> ${esc(a.asof)}` : "", a.fetched ? `<b>Fetched:</b> ${esc(a.fetched)}` : "",
    a.formula ? `<b>Formula:</b> <span class="formula" style="display:block">${esc(a.formula)}</span>` : "", a.note ? esc(a.note) : ""].filter(Boolean).join("<br>");
  const lab = { user: "your input", est: "estimate", assume: "assumption" }[st] || st;
  return `<details class="aud"><summary>${val} ${pill(st === "user" ? "input" : st, lab)}</summary><div class="audbox">${body}</div></details>`;
}
function item(k) { return L && L.items[k]; }
function itemAudit(k, valTxt, extra) {
  const it = item(k);
  if (!it) return `<span class="grey">no live source</span> ${pill("none", "no live source")}`;
  const src = it.report_url ? it.source + " (report " + (it.report_date || it.as_of) + ")" : it.source;
  return audit(valTxt || f2(it.value), Object.assign({ status: it.status, source: src, url: it.report_url || it.source_url, asof: it.as_of, fetched: L.generated_ist,
    note: (it.note || "") + (it.status === "stale" ? " STALE: last refresh failed (" + (it.stale_reason || "") + "); showing last good value." : "") }, extra || {}));
}
function rateAudit(k, txt) { const r = R[k]; return audit(txt, { status: "official", source: r.source, url: r.url, asof: "checked by hand " + R.verified_on + (r.from ? "; in force from " + r.from : ""), note: r.detail }); }

/* ---------- monthly data and rules (same logic as scripts/model.py) ---------- */
function rows() { const it = item("lgd_rows"); const o = {}; (it ? it.rows : []).forEach((r) => (o[r.month] = r)); return o; }
function lastMonth(Rw) { const ks = Object.keys(Rw).sort(); return ks[ks.length - 1]; }
function demand3(Rw, m) { const ms = [0, 1, 2].map((i) => madd(m, -i)); if (!ms.every((x) => Rw[x] && Rw[x].usd_mn_prev)) return null;
  return 100 * (ms.reduce((a, x) => a + Rw[x].usd_mn, 0) / ms.reduce((a, x) => a + Rw[x].usd_mn_prev, 0) - 1); }
function priceYoy(Rw, m) { const r = Rw[m]; return r && r.ppc_yoy != null ? 100 * r.ppc_yoy : null; }
function rulesNow() { return { price_pct: S.pricePct, demand_pct: S.demandPct }; }
function priceCall(v, r) { return v == null ? null : v <= -r.price_pct ? "wait" : v >= r.price_pct ? "stock up" : null; }
function demandCall(v, r) { return v == null ? null : v >= r.demand_pct ? "stock up" : v <= -r.demand_pct ? "wait" : null; }
function combine(cs) { const s = cs.reduce((a, c) => a + ({ "stock up": 1, wait: -1 }[c] || 0), 0); return s > 0 ? "stock up" : s < 0 ? "wait" : "buy part"; }
function callHit(c, mv) { return c === "stock up" ? mv > 0 : c === "wait" ? mv < 0 : Math.abs(mv) <= (M ? M.band : 3); }
function nextMove(Rw, m) { const a = Rw[m] && Rw[m].usd_per_ct, b = Rw[madd(m, 1)] && Rw[madd(m, 1)].usd_per_ct; return a && b ? 100 * (b / a - 1) : null; }
function backtest(Rw, r) {
  const out = {}, minN = M ? M.min_n : 12;
  [["price", priceYoy, priceCall], ["demand", demand3, demandCall]].forEach(([name, g, fn]) => {
    const by = {}, sig = [];
    Object.keys(Rw).sort().forEach((m) => { const c = fn(g(Rw, m), r); if (!c) return; const mv = nextMove(Rw, m); if (mv == null) return;
      const o = { "stock up": "wait", wait: "stock up" }[c];
      (by[c] = by[c] || { n: 0, hit: 0, opp: 0 }); by[c].n++; by[c].hit += callHit(c, mv) ? 1 : 0; by[c].opp += callHit(o, mv) ? 1 : 0; sig.push({ m, c, mv, right: callHit(c, mv) }); });
    out[name] = { by, sig };
  });
  const active = {};
  ["price", "demand"].forEach((n) => ["stock up", "wait"].forEach((s) => { const b = out[n].by[s]; active[n + ":" + s] = !!(b && b.n >= minN && b.hit / b.n > b.opp / b.n); }));
  out.active = active; return out;
}
function decide() {
  const Rw = rows(), m = lastMonth(Rw); if (!m) return null;
  const r = rulesNow(), bt = backtest(Rw, r);
  const pv = priceYoy(Rw, m), dv = demand3(Rw, m), c1 = priceCall(pv, r), c2 = demandCall(dv, r);
  const used = [["price", c1], ["demand", c2]].filter(([n, c]) => c && bt.active[n + ":" + c]).map(([, c]) => c);
  let call = combine(used); const raw = combine([c1, c2]);
  const thin = !Object.values(bt.active).some(Boolean);
  const news = M && M.decision ? M.decision.news_flag : null;
  let softened = null; if (news && call === "stock up") { call = "buy part"; softened = news; }
  return { m, pv, dv, c1, c2, call, raw, thin, bt, news, softened, row: Rw[m], Rw };
}

/* ---------- main screen ---------- */
function renderSimple() {
  const t = T();
  document.documentElement.lang = S.lang;
  document.querySelectorAll("#langs button").forEach((b) => b.classList.toggle("on", b.dataset.l === S.lang));
  $("s-title").textContent = t.title;
  ["p-title", "d-title", "r-title", "c-title", "rough-title", "rough-main", "rough-sub", "q-label"].forEach((id) => ($(id).textContent = t[id.replace(/-/g, "_").replace("rough_", "rough_")] || t[id.replace("-", "_")]));
  $("q-clear").textContent = t.q_clear; $("wa-btn").textContent = t.wa; $("speak-btn").textContent = t.speak;
  $("speak-btn").style.display = ("speechSynthesis" in window) ? "" : "none";
  $("s-src").textContent = t.src; $("s-disc").textContent = t.disc; $("details-link").textContent = t.details;
  if (!L || !M) return;
  const d = decide(); if (!d) return;
  $("a-today").textContent = fill(t.today, { m: mname(d.m) });
  $("a-main").textContent = { "stock up": t.call_stock, wait: t.call_wait, "buy part": t.call_part }[d.call];
  $("answer").className = "answer " + ({ "stock up": "buy", wait: "wait", "buy part": "small" }[d.call]);
  const why = [];
  if (d.pv != null) why.push(fill(d.pv <= -S.pricePct ? t.r_price_down : d.pv >= S.pricePct ? t.r_price_up : t.r_price_flat, { p: pct(d.pv) }));
  if (d.dv != null) why.push(fill(d.dv >= S.demandPct ? t.r_dem_up : d.dv <= -S.demandPct ? t.r_dem_down : t.r_dem_flat, { p: pct(d.dv) }));
  if (d.news) why.push(t.r_news);
  $("a-reason").textContent = why.join(" ");
  $("a-thin").textContent = [ (item("lgd_exports") || {}).status === "stale" ? t.stale : "", d.thin ? t.thin : ""].filter(Boolean).join(" "); $("a-thin").className = "a-thin";
  const pc = item("lgd_ppc"), py = item("lgd_ppc_ytd");
  $("p-big").textContent = pc ? usd(pc.value) + t.ct : "—";
  $("p-sub").textContent = pc ? fill(t.p_sub, { m: mname(pc.as_of) }) : "";
  $("p-sub2").textContent = py ? fill(t.p_sub2, { m: mname(py.as_of), ytd: usd(py.value), prev: usd(py.prev_year), p: pct(100 * py.yoy) }) : "";
  const ex = item("lgd_exports"), g = M.gauges || {};
  $("d-main").textContent = ex ? mname(ex.as_of) + ": " + f2(ex.value) + " " + t.mn : "—";
  $("d-sub").textContent = ex ? fill(t.d_sub, { p: pct(ex.prev_year ? 100 * (ex.value / ex.prev_year - 1) : null), p3: pct(d.dv) }) : "";
  const rt = item("retail_lgd");
  $("r-main").textContent = rt ? usd(rt.value, 0) + t.ct : "—";
  $("r-sub").textContent = rt ? fill(t.r_sub, { m: mname(rt.as_of), p1: pct(g.retail_1m_pct), ps: pct(g.retail_since_start_pct), start: mname(g.retail_start) }) : "";
  const fx = item("usdinr");
  $("c-fx").textContent = fx ? fill(t.c_fx, { v: f2(fx.value), d: fx.as_of }) : "";
  $("c-us").textContent = R ? fill(t.c_us, { v: R.us_301_india.pct + R.us_mfn_7104_91_10.pct }) : "";
  $("c-pw").textContent = R ? fill(t.c_pw, { a: f2(R.dgvcl_htp1.bands[0].rs_kwh), b: f2(R.dgvcl_htp1.bands[2].rs_kwh) }) : "";
  // own quote tracker
  const q = S.quotes || [];
  if (q.length) { const a = q[q.length - 1], b = q[q.length - 2];
    $("q-res").textContent = b ? fill(t.q_res, { d: a.d, v: rs(a.v), pd: b.d, pv: rs(b.v), p: pct(100 * (a.v / b.v - 1)) }) : fill(t.q_first, { d: a.d, v: rs(a.v) });
  } else $("q-res").textContent = "";
  const nm = madd(d.m, 1);
  $("s-next").textContent = fill(t.next, { nm: mname(nm), d: mname(madd(d.m, 2)).replace(/^(\S+)/, "7–15 $1") });
  $("s-updated").textContent = fill(t.updated, { t: L.generated_ist });
}
function summaryText() {
  const t = T();
  return [t.title, $("a-main").textContent, $("a-reason").textContent, $("a-thin").textContent, t.p_title + ": " + $("p-big").textContent, t.d_title + ": " + $("d-main").textContent,
    t.disc, location.origin + location.pathname].filter(Boolean).join("\n");
}

/* ---------- details (English) ---------- */
const callTxt = (c) => c ? { "stock up": "Stock up", wait: "Wait", "buy part": "Buy part (orders only)" }[c] : "no signal";
function tbl(head, body) { return `<div class="tablewrap"><table><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${body.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table></div>`; }
function renderToday() {
  const d = decide(); if (!d) return;
  const ex = item("lgd_exports"), pc = item("lgd_ppc"), py = item("lgd_ppc_ytd"), rt = item("retail_lgd"), g = M.gauges;
  const thin = d.thin ? `<div class="warn"><b>Data too thin for a confident call.</b> Carats (needed for a price per carat) appear in GJEPC's text only since Sep 2025, so there are ${M.ppc.pairs} month-to-month price changes to test against. No rule has ${M.min_n}+ scored signals with a better record than the opposite action, so the call defaults to <b>buy part</b>.</div>` : "";
  $("s-today").innerHTML = `<div class="card"><div class="ct">Call for ${mname(d.m, "en")} data (monthly; next GJEPC report ${M.next_data_expected})</div>
    <div class="call">${callTxt(d.call)}</div><p class="sub">What the rules say before the trust check: price rule <b>${callTxt(d.c1)}</b>, demand rule <b>${callTxt(d.c2)}</b> → ${callTxt(d.raw)}. ${d.softened ? "Softened by headline: " + esc(d.softened.title) : ""}</p>${thin}</div>
  <div class="grid2">
   <div class="card"><div class="ct">Price pressure (official)</div><div class="big ${cls(d.pv)}">${itemAudit("lgd_ppc_ytd", pct(d.pv), { status: "est", formula: "(Apr–" + py.as_of + " export value / carats) ÷ (same period last year, same report) − 1 = " + usd(py.value) + " ÷ " + usd(py.prev_year) + " − 1" })}</div>
     <p class="sub">FY-to-date export price per carat ${usd(py.value)} vs ${usd(py.prev_year)} last year (${f2(py.ytd_lakh_ct)} lakh carats so far). Latest month ${pc.as_of}: ${itemAudit("lgd_ppc", usd(pc.value) + "/ct")}</p></div>
   <div class="card"><div class="ct">Demand (official)</div><div class="big ${cls(d.dv)}">${audit(pct(d.dv), { status: "est", source: "GJEPC monthly reports", url: ex.source_url, asof: ex.as_of, formula: "sum of last 3 months' LGD exports ÷ the same 3 months last year (as stated in those reports) − 1" })}</div>
     <p class="sub">${ex.as_of}: ${itemAudit("lgd_exports", usd(ex.value) + " mn")} vs ${usd(ex.prev_year)} mn a year earlier (${pct(100 * (ex.value / ex.prev_year - 1))}).</p></div>
   <div class="card"><div class="ct">Retail price, US online (unofficial)</div><div class="big">${itemAudit("retail_lgd", usd(rt.value, 0) + "/ct")}</div>
     <p class="sub">${pct(g.retail_1m_pct)} on the month, ${pct(g.retail_3m_pct)} over 3 months, ${pct(g.retail_since_start_pct)} since ${g.retail_start}. Shown for context; not used in the call.</p></div>
   <div class="card"><div class="ct">Costs &amp; market context</div>
     <p>USD/INR ${itemAudit("usdinr", "₹" + f2(item("usdinr").value))}</p>
     <p>US duty on cut LGD from India: ${rateAudit("us_301_india", (R.us_301_india.pct + R.us_mfn_7104_91_10.pct) + "%")} (base ${rateAudit("us_mfn_7104_91_10", "0%")} + Section 301 10%); most rival origins ${rateAudit("us_301_others", "12.5%")}</p>
     <p>DGVCL HTP-I energy charge: ${rateAudit("dgvcl_htp1", "₹" + R.dgvcl_htp1.bands.map((b) => f2(b.rs_kwh)).join(" / ") + " per kWh")} (≤500 / 500–2,500 / >2,500 kVA)</p>
     <p>US jewelry CPI ${itemAudit("cpi_jewelry")} · US jewelry-store jobs ${itemAudit("us_jewelry_jobs", f2(item("us_jewelry_jobs").value, 1) + "k")} · US import prices, gems &amp; jewelry ${itemAudit("us_import_px", f2(item("us_import_px").value, 1))}</p>
     <p>Rough LGD price: <span class="grey">no free public source</span> ${pill("none", "no live source")}. Fix: enter your broker's quote on the main screen each month (kept in your browser).</p></div>
  </div>`;
}
function renderExports() {
  const Rw = rows(), ks = Object.keys(Rw).sort().reverse();
  $("s-exports").innerHTML = `<div class="card"><div class="ct">GJEPC monthly LGD polished exports (first-published figures from each month's own report)</div>
   <p class="sub">Price per carat = month value ÷ month carats, where month carats = that report's year-to-date carats minus the previous report's. Year-on-year per-carat = this year's YTD value/carats ÷ last year's YTD value/carats as stated in the same report. Provisional data; GJEPC later reconciles with DGCI&amp;S and restates last year's months (the "same month last year" column is the restated figure).</p>
   ${tbl(["Month", "Exports US$ mn", "Same month last year", "YoY", "Carats (lakh)", "US$/ct", "YTD US$/ct", "YTD last year", "YoY per ct", "How", "Report"],
     ks.map((m) => { const r = Rw[m]; return [m, f2(r.usd_mn), f2(r.usd_mn_prev), r.usd_mn_prev ? `<span class="${cls(r.usd_mn - r.usd_mn_prev)}">${pct(100 * (r.usd_mn / r.usd_mn_prev - 1))}</span>` : "—",
       r.lakh_ct != null ? f2(r.lakh_ct) : "—", r.usd_per_ct ? f2(r.usd_per_ct) : "—", r.ytd_usd_per_ct ? f2(r.ytd_usd_per_ct) : "—", r.ytd_usd_per_ct_prev ? f2(r.ytd_usd_per_ct_prev) : "—",
       r.ppc_yoy != null ? pct(100 * r.ppc_yoy) : "—", esc(r.how_v || ""), `<a href="${esc(r.report_url)}" target="_blank" rel="noopener">${esc(r.report_date || "pdf")}</a>`]; }))}</div>`;
}
function renderRules() {
  const d = decide(); if (!d) return; const bt = d.bt;
  const side = (n) => Object.entries(bt[n].by).map(([c, b]) => `${callTxt(c)}: right ${f2(100 * b.hit / b.n, 1)}% of ${b.n} (opposite action ${f2(100 * b.opp / b.n, 1)}%) ${bt.active[n + ":" + c] ? pill("live", "used") : pill("none", "not trusted yet")}`).join("<br>") || "no scored signals";
  $("s-rules").innerHTML = `<div class="card"><div class="ct">Your rules of thumb (editable; saved in this browser)</div>
   <div class="row"><label>Price rule: wait if export price per carat is down by at least <input type="number" step="0.5" min="0.5" max="50" data-k="pricePct" value="${S.pricePct}">% on last year; stock up if up that much</label></div>
   <div class="row"><label>Demand rule: stock up if last 3 months' exports are up by at least <input type="number" step="1" min="1" max="200" data-k="demandPct" value="${S.demandPct}">% on last year; wait if down that much</label></div>
   <p class="sub">Each rule is scored on what happened next: the following month's GJEPC export price per carat (up = 'stock up' was right, down = 'wait' was right; 'buy part' right if within ±${M.band}%). A rule side drives the call only after ${M.min_n}+ scored signals with a better record than doing the opposite. Today that is: ${Object.entries(bt.active).filter(([, v]) => v).map(([k]) => k).join(", ") || "none"}.</p></div>
  <div class="grid2"><div class="card"><div class="ct">Price rule back-test</div><p>${side("price")}</p>${tbl(["Month", "Signal", "Next month US$/ct", "Right?"], bt.price.sig.slice().reverse().map((s) => [s.m, callTxt(s.c), pct(s.mv), s.right ? "yes" : "no"]))}</div>
  <div class="card"><div class="ct">Demand rule back-test</div><p>${side("demand")}</p>${tbl(["Month", "Signal", "Next month US$/ct", "Right?"], bt.demand.sig.slice().reverse().map((s) => [s.m, callTxt(s.c), pct(s.mv), s.right ? "yes" : "no"]))}</div></div>
  <div class="card"><div class="ct">Cross-check against the unofficial retail index (defaults only)</div><p class="sub">${["price", "demand"].map((n) => n + " rule vs next month's Caratlytics median: " + (Object.entries(M.rules_backtest[n].retail_check.by_call).map(([c, b]) => `${callTxt(c)} right ${b.hit_rate}% of ${b.n} (opposite ${b.opposite_hit_rate}%)`).join("; ") || "no signals")).join(" · ")}. Too few months to mean much; shown for transparency.</p></div>`;
}
function renderModel() {
  const v = M.value, b = v.backtest || {}, n = v.naive || {};
  $("s-model").innerHTML = `<div class="card"><div class="ct">Forecast: next month's LGD export value (demand)</div>
   <p>Last: ${usd(v.last_usd_mn)} mn (${M.data_month}). Model forecast for ${madd(M.data_month, 1)}: <b>${usd(v.pred_usd_mn)} mn</b> (${pct(v.pred_pct)}) ${pill("est", "estimate")}. Not used for the stock-up call (it forecasts demand, not price).</p>
   <p class="formula">d% = ${v.vars.map((k, i) => `${v.weights[i]} × ${k}`).join(" + ")}</p>
   ${tbl(["Variable", "Now", "Weight", "Walk-forward error alone", "Kept?"], v.vars.map((k, i) => [k, v.features_now ? f2(v.features_now[k]) : "—", v.weights[i], v.single[k] ? f2(v.single[k].mae_pct) + "%" : "—", v.mask[i] ? "yes" : "no"]))}
   <p class="sub">momentum = last month's % change in exports; yoy3 = last 3 months vs last year; us_cpi = US jewelry CPI 3-month % change; rupee = USD/INR 1-month % change. Walk-forward back-test ${b.start || "—"} to ${b.end || "—"} (${b.n || 0} months, each forecast fitted only on earlier months): average error ${f2(b.mae_pct)}% vs ${f2(n.naive_mae_pct)}% for 'no change'; all four variables ${f2(v.ungated && v.ungated.mae_pct)}%. Gate: ${esc(v.gate_reason)}. A variable keeps weight only if it cuts error by ≥1% and the model beats 'no change'.</p></div>
  <div class="card"><div class="ct">Price per carat model</div><p>${esc(M.ppc.reason)} Weights: ${M.ppc.weights.join(", ")}.</p></div>
  <div class="card"><div class="ct">What the app has learned (fixed templates)</div><ul>${M.lessons.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></div>`;
}
function renderTrack() {
  const ps = (P && P.predictions) || [];
  $("s-track").innerHTML = `<div class="card"><div class="ct">Prediction ledger (one set per GJEPC month; scored when the next report arrives)</div>
   ${tbl(["ID", "Made", "For", "Version", "Prediction", "Scored", "Actual", "Error / move", "Right?"], ps.slice().reverse().map((p) => [p.id, p.made_on, p.target_month, p.model_version,
     p.kind === "call" ? callTxt(p.call) + (p.data_too_thin ? " (thin data)" : "") : p.kind === "value" ? usd(p.pred) + " mn" : usd(p.pred) + "/ct", p.scored ? "yes" : "pending",
     p.realised != null ? f2(p.realised) : "—", p.error_pct != null ? pct(p.error_pct) : p.realised_move_pct != null ? pct(p.realised_move_pct) : "—", p.hit == null ? "—" : p.hit ? "yes" : "no"]))}
   <p class="sub"><a href="data/track_record.csv">Download track_record.csv</a> · model archive in data/models/</p></div>
  <div class="card"><div class="ct">Model versions</div>${tbl(["Version", "Date", "Change"], M.changelog.slice().reverse().map((c) => [c.version, c.date, esc(c.change)]))}</div>`;
}
function renderSources() {
  const its = Object.entries(L.items);
  $("s-sources").innerHTML = `<div class="card"><div class="ct">Data items</div>${tbl(["Item", "Value", "As of", "Status", "Source"], its.map(([k, it]) => [esc(it.label), f2(it.value) + " " + esc(it.unit), esc(it.as_of), pill(it.status), `<a href="${esc(it.source_url)}" target="_blank" rel="noopener">${esc(it.source)}</a>`]))}
   ${Object.keys(L.errors || {}).length ? `<div class="warn">Last refresh errors: ${esc(JSON.stringify(L.errors))}</div>` : ""}</div>
  <div class="card"><div class="ct">Rates checked by hand (${R.verified_on})</div>${tbl(["Item", "Rate", "Source"], ["us_301_india", "us_mfn_7104_91_10", "us_301_others", "dgvcl_htp1"].map((k) => [esc(R[k].label), R[k].pct != null ? R[k].pct + "%" : R[k].bands.map((b) => "₹" + f2(b.rs_kwh)).join(" / ") + "/kWh", `<a href="${esc(R[k].url)}" target="_blank" rel="noopener">${esc(R[k].source)}</a><br><span class="sub">${esc(R[k].detail)}</span>`]))}<p class="sub">${esc(R.gjepc_release.detail)}</p></div>
  <div class="card"><div class="ct">Not available free (kept grey)</div><ul><li>Rough LGD prices and rough LGD import volumes: GJEPC shows them only as images in the report annex; TradeStat (DGCI&amp;S) data for Apr–Oct 2026 is not yet released per GJEPC. No free consistent rough price series found.</li><li>GJEPC's website refuses cloud servers (HTTP 403 from GitHub Actions). If a new month is not picked up, upload the report PDF to the repo's <a href="https://github.com/cakashoza-cmyk/lgd/tree/main/inbox">inbox/</a> folder; the workflow parses it the same way.</li><li>Surat polished LGD trade prices: no free public series. Use the quote tracker on the main screen.</li><li>US Census monthly imports of HS 7104 from India: the Census API needs a sign-up key, so it is not used.</li></ul></div>
  <div class="card"><div class="ct">Headlines (context only; a price-fall headline can only soften 'stock up')</div><ul>${(L.headlines || []).map((h) => `<li>${esc(h.date)} <a href="${esc(h.url)}" target="_blank" rel="noopener">${esc(h.title)}</a></li>`).join("")}</ul><p class="sub">${esc(L.headlines_source)}</p></div>`;
}
function renderAll() {
  renderSimple();
  if (!L || !M || !R) return;
  $("meta-data").textContent = "Data refreshed " + L.generated_ist + " · latest GJEPC month " + M.data_month;
  $("meta-model").textContent = "Model " + M.version + " · " + M.generated_ist;
  [renderToday, renderExports, renderRules, renderModel, renderTrack, renderSources].forEach((f) => { try { f(); } catch (e) { console.error(e); const id = "s-" + f.name.replace("render", "").toLowerCase(); if ($(id)) $(id).innerHTML = `<div class="warn">Could not render: ${esc(e.message)}</div>`; } });
}
function route() {
  const det = location.hash.startsWith("#details");
  $("simple").style.display = det ? "none" : ""; $("details").style.display = det ? "" : "none";
  if (det) renderAll(); else renderSimple();
}

document.addEventListener("click", (ev) => {
  const tb = ev.target.closest(".tab");
  if (tb) { document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === tb)); document.querySelectorAll(".screen").forEach((x) => x.classList.toggle("active", x.id === "s-" + tb.dataset.s)); }
  const l = ev.target.closest("#langs button"); if (l) { S.lang = l.dataset.l; save(); renderSimple(); }
});
document.addEventListener("change", (ev) => { const k = ev.target.dataset && ev.target.dataset.k; if (k) { const v = Number(ev.target.value); S[k] = ev.target.value === "" || !(v > 0) ? DEF[k] : v; save(); setTimeout(renderAll, 0); } });
$("back-link").onclick = (e) => { e.preventDefault(); history.pushState(null, "", location.pathname); route(); };
window.addEventListener("hashchange", route);
$("q-input").addEventListener("change", () => { const v = Number($("q-input").value); if (v >= 100 && v <= 2000000) { S.quotes = (S.quotes || []).concat([{ d: new Date().toISOString().slice(0, 10), v }]).slice(-36); save(); $("q-input").value = ""; renderSimple(); } });
$("q-clear").onclick = () => { S.quotes = []; save(); renderSimple(); };
$("wa-btn").onclick = () => window.open("https://wa.me/?text=" + encodeURIComponent(summaryText()), "_blank");
$("speak-btn").onclick = () => { const u = new SpeechSynthesisUtterance([$("a-main").textContent, $("a-reason").textContent, $("p-title").textContent, $("p-big").textContent].join(". ").replace(/[✅⏳🟡⚠️🔊]/gu, ""));
  u.lang = { gu: "gu-IN", hi: "hi-IN", en: "en-IN" }[S.lang]; speechSynthesis.cancel(); speechSynthesis.speak(u); };

const j = (u) => fetch(u + "?t=" + Date.now()).then((r) => { if (!r.ok) throw new Error(u + " " + r.status); return r.json(); });
Promise.all([j("data/live.json"), j("data/model.json"), j("data/predictions.json").catch(() => null), j("data/rates.json")])
  .then(([l, m, p, r]) => { L = l; M = m; P = p; R = r; route(); })
  .catch((e) => { console.error(e); renderSimple(); $("a-reason").textContent = String(e); });
renderSimple();
})();
