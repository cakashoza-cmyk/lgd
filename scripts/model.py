"""LGD desk: monthly call, prediction ledger, scoring and learning. Numbers only from data/live.json and data/rates.json.
No AI is used anywhere; every number is plain arithmetic or least squares, written to JSON.

The data behind LGD prices is MONTHLY (GJEPC export review), so the call is monthly too.
Gauges for the latest GJEPC month m:
  price pressure (official) = this year's export price per carat (Apr..m) / last year's (Apr..m, same report) - 1
  demand (official)         = LGD exports, last 3 months / same 3 months last year (as stated in the same reports) - 1
  retail price (unofficial) = Caratlytics lab-grown median listed price per carat, 1- and 3-month change (shown, not used in the call)
Trader rules (editable):  price pressure <= -price_pct -> 'wait'; >= +price_pct -> 'stock up'
                          demand >= +demand_pct -> 'stock up'; <= -demand_pct -> 'wait'
  both agree -> that action; disagree or neither fires -> 'buy part' (buy only what confirmed orders need).
  A rule side drives the call only if, in the back-test, it was right more often than the opposite action with >= MIN_N signals.
  Outcome used to score: next month's GJEPC export price per carat (up = 'stock up' right, down = 'wait' right, within +-3% = 'buy part' right).
Forecast of next month's LGD export value: d% = w_mom*momentum + w_yoy*3-month YoY growth + w_cpi*US jewelry CPI 3-month % + w_fx*USD/INR 1-month %;
  ridge least squares re-fitted every run; a variable keeps weight only if it cuts walk-forward error by at least 1%, and the model must beat 'no change'.
"""
import json, csv, math, re, statistics as st
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
LIVE, PRED, MODEL, RATES = ROOT / "data/live.json", ROOT / "data/predictions.json", ROOT / "data/model.json", ROOT / "data/rates.json"
IST = timezone(timedelta(hours=5, minutes=30))
VERSION_BASE = "v1"
RULES = dict(price_pct=5.0, demand_pct=15.0)   # defaults; editable on the page
MIN_N = 12          # back-test signals a rule side needs before it may drive the call (monthly data: one signal a month at most)
BAND = 3.0          # % move of next month's export price per carat treated as 'flat'
VBAND = 5.0         # % move of next month's export value treated as 'flat' (forecast direction)
RIDGE = 0.5
WARM = 24           # months of history before the first walk-forward forecast
VARS = ["momentum", "yoy3", "us_cpi", "rupee"]
CAUTION = r"(price|prices|rates?)\W+(\w+\W+){0,3}(fall|falls|fell|drop|drops|crash|plunge|slump|declin|cut|slash)|\b(glut|oversupply|over-supply|price war|crash)\b"


def madd(m, k):
    y, mo = int(m[:4]), int(m[5:]) - 1 + k
    return f"{y + mo // 12:04d}-{mo % 12 + 1:02d}"


def rows_by_month(live):
    return {r["month"]: r for r in live["items"]["lgd_rows"]["rows"]}


def hmap(live, k):
    it = live["items"].get(k)
    return {a[:7]: b for a, b in it["history"]} if it and it.get("history") else {}


# ---------- gauges and rules ----------
def demand3(R, m):
    ms = [madd(m, -i) for i in range(3)]
    if all(x in R and R[x].get("usd_mn_prev") for x in ms):
        return 100 * (sum(R[x]["usd_mn"] for x in ms) / sum(R[x]["usd_mn_prev"] for x in ms) - 1)
    return None


def price_yoy(R, m):
    r = R.get(m)
    return 100 * r["ppc_yoy"] if r and r.get("ppc_yoy") is not None else None


def price_call(v, r=RULES):
    return None if v is None else "wait" if v <= -r["price_pct"] else "stock up" if v >= r["price_pct"] else None


def demand_call(v, r=RULES):
    return None if v is None else "stock up" if v >= r["demand_pct"] else "wait" if v <= -r["demand_pct"] else None


def combine(calls):
    s = sum({"stock up": 1, "wait": -1}.get(c, 0) for c in calls if c)
    return "stock up" if s > 0 else "wait" if s < 0 else "buy part"


def call_hit(call, mv):
    return mv > 0 if call == "stock up" else mv < 0 if call == "wait" else abs(mv) <= BAND


def next_ppc_move(R, m):
    a, b = R.get(m, {}).get("usd_per_ct"), R.get(madd(m, 1), {}).get("usd_per_ct")
    return 100 * (b / a - 1) if a and b else None


def summarise(res):
    by, opp = {}, {}
    for c, mv in res:
        by.setdefault(c, []).append(call_hit(c, mv))
        o = {"stock up": "wait", "wait": "stock up"}.get(c)
        opp.setdefault(c, []).append(call_hit(o, mv) if o else False)
    return dict(n=len(res), hit_rate=round(100 * sum(sum(v) for v in by.values()) / len(res), 1) if res else None,
                by_call={c: dict(n=len(v), hit_rate=round(100 * sum(v) / len(v), 1), opposite_hit_rate=round(100 * sum(opp[c]) / len(v), 1)) for c, v in by.items()})


def rules_backtest(R, retail, r=RULES):
    out, outcomes = {}, []
    for m in sorted(R):
        mv = next_ppc_move(R, m)
        if mv is not None:
            outcomes.append(dict(month=m, next_ppc_move_pct=round(mv, 2)))
    for name, gauge, fn in (("price", price_yoy, price_call), ("demand", demand3, demand_call)):
        res, rres, dated = [], [], []
        for m in sorted(R):
            c = fn(gauge(R, m), r)
            if not c:
                continue
            mv = next_ppc_move(R, m)
            if mv is not None:
                res.append((c, mv)); dated.append(dict(month=m, call=c, next_ppc_move_pct=round(mv, 2), right=call_hit(c, mv)))
            if retail.get(m) and retail.get(madd(m, 1)):
                rres.append((c, 100 * (retail[madd(m, 1)] / retail[m] - 1)))
        s = summarise(res); s["signals"] = dated; s["retail_check"] = summarise(rres)
        out[name] = s
    active = {}
    for name in ("price", "demand"):
        for side in ("stock up", "wait"):
            b = out[name]["by_call"].get(side)
            active[f"{name}:{side}"] = bool(b and b["n"] >= MIN_N and b["hit_rate"] > b["opposite_hit_rate"])
    mv = [o["next_ppc_move_pct"] for o in outcomes]
    out.update(active=active, min_n=MIN_N, outcomes=outcomes, n_outcomes=len(mv),
               base_up=round(100 * sum(x > 0 for x in mv) / len(mv), 1) if mv else None,
               base_down=round(100 * sum(x < 0 for x in mv) / len(mv), 1) if mv else None)
    return out


def caution_news(live, today, days=21):
    for h in live.get("headlines", []):
        d = datetime.fromisoformat(h["date"]).date()
        if (today - d).days <= days and re.search(CAUTION, h["title"], re.I):
            return dict(date=h["date"], title=h["title"], url=h["url"])
    return None


def decide(R, m, live, today, bt, r=RULES):
    pv, dv = price_yoy(R, m), demand3(R, m)
    c1, c2 = price_call(pv, r), demand_call(dv, r)
    act = bt["active"]
    used = [c for name, c in (("price", c1), ("demand", c2)) if c and act.get(f"{name}:{c}")]
    raw = combine([c1, c2])
    call = combine(used)
    thin = not any(act.values())
    news = caution_news(live, today)
    softened = None
    if news and call == "stock up":
        call = "buy part"; softened = f"'stock up' softened to 'buy part': recent headline ({news['date']}: {news['title'][:120]})"
    return dict(call=call, raw_call=raw, rule_price=c1, rule_demand=c2, price_yoy_pct=None if pv is None else round(pv, 2),
                demand3_pct=None if dv is None else round(dv, 2), rules_active=act, data_too_thin=thin, news_flag=news, softened=softened)


# ---------- forecast model for next month's export value ----------
def table(R, cpi, fx):
    out = []
    for m in sorted(R):
        p, n = R.get(madd(m, -1)), R.get(madd(m, 1))
        dv = demand3(R, m)
        c0, c3, f0, f1 = cpi.get(m), cpi.get(madd(m, -3)), fx.get(m), fx.get(madd(m, -1))
        if not p or dv is None or None in (c0, c3, f0, f1):
            continue
        x = np.array([100 * math.log(R[m]["usd_mn"] / p["usd_mn"]), dv, 100 * (c0 / c3 - 1), 100 * (f0 / f1 - 1)])
        y = 100 * math.log(n["usd_mn"] / R[m]["usd_mn"]) if n else None
        out.append((m, x, y))
    return out


def fit(tab, mask, upto=None):
    sub = [t for t in tab if t[2] is not None and (upto is None or madd(t[0], 1) <= upto)]
    w = np.zeros(len(VARS))
    if len(sub) < WARM or not any(mask):
        return w, len(sub)
    m = np.array(mask, dtype=float)
    X = np.array([t[1] for t in sub]) * m; y = np.array([t[2] for t in sub])
    return np.linalg.solve(X.T @ X + RIDGE * len(y) * np.eye(len(VARS)), X.T @ y) * m, len(sub)


def direction(p):
    return "up" if p > VBAND else "down" if p < -VBAND else "flat"


def backtest(tab, mask):
    res = []
    for m, x, y in tab:
        if y is None:
            continue
        w, n = fit(tab, mask, upto=m)
        if n < WARM:
            continue
        res.append((m, float(w @ x), y))
    if not res:
        return None
    return dict(n=len(res), start=res[0][0], end=res[-1][0], mae_pct=round(st.mean(abs(p - a) for _, p, a in res), 2),
                naive_mae_pct=round(st.mean(abs(a) for _, _, a in res), 2),
                hit_rate=round(100 * sum(direction(p) == direction(a) for _, p, a in res) / len(res), 1),
                naive_hit_rate=round(100 * sum(direction(0) == direction(a) for _, _, a in res) / len(res), 1))


def gate(tab):
    base = backtest(tab, [0] * len(VARS))
    if not base:
        return dict(mask=[0] * len(VARS), single={}, base=None, gated=None, ungated=None, reason="too little history")
    single, mask = {}, []
    for i, v in enumerate(VARS):
        b = backtest(tab, [int(j == i) for j in range(len(VARS))])
        single[v] = b
        mask.append(int(b["mae_pct"] <= 0.99 * base["naive_mae_pct"]))
    gated = backtest(tab, mask) if any(mask) else None
    reason = "kept: " + ", ".join(v for v, k in zip(VARS, mask) if k) if any(mask) else "no variable cut walk-forward error by 1% or more"
    if gated and gated["mae_pct"] >= base["naive_mae_pct"]:
        mask = [0] * len(VARS); reason = "kept variables together did not beat 'no change'"
    return dict(mask=mask, single=single, base=base, gated=gated, ungated=backtest(tab, [1] * len(VARS)), reason=reason)


# ---------- main ----------
def main():
    live = json.loads(LIVE.read_text())
    R = rows_by_month(live)
    retail, cpi, fx = hmap(live, "retail_lgd"), hmap(live, "cpi_jewelry"), hmap(live, "usdinr_m")
    today = datetime.now(IST).date()
    m = max(R)
    old = json.loads(MODEL.read_text()) if MODEL.exists() else {}
    ledger = json.loads(PRED.read_text()) if PRED.exists() else {"predictions": []}

    # 1. value model
    tab = table(R, cpi, fx)
    g = gate(tab)
    w, n_fit = fit(tab, g["mask"])
    last = next((t for t in tab if t[0] == m), None)
    pred_pct = float(w @ last[1]) if last is not None else 0.0
    value = dict(target="next month's LGD polished export value (GJEPC)", vars=VARS, weights=[round(float(x), 4) for x in w], mask=g["mask"], gate_reason=g["reason"],
                 backtest=g["gated"] or g["base"], naive=g["base"], single=g["single"], ungated=g["ungated"], n_fit=n_fit,
                 features_now=dict(zip(VARS, [round(float(x), 2) for x in last[1]])) if last is not None else None,
                 pred_pct=round(pred_pct, 2), pred_usd_mn=round(R[m]["usd_mn"] * math.exp(pred_pct / 100), 2), last_usd_mn=R[m]["usd_mn"],
                 model_has_edge=any(g["mask"]))
    ppc_pairs = sum(1 for k in R if next_ppc_move(R, k) is not None)
    ppc = dict(target="next month's LGD export price per carat (GJEPC)", pairs=ppc_pairs, weights=[0, 0, 0, 0],
               reason=f"only {ppc_pairs} month-to-month pairs exist (carats are published only since Sep 2025); a model needs at least {WARM}. Forecast = no change.",
               last=R[m].get("usd_per_ct"), pred=R[m].get("usd_per_ct"))

    # 2. rules and call
    bt = rules_backtest(R, retail)
    dec = decide(R, m, live, today, bt)

    # 3. ledger: one set of predictions per GJEPC data month; score when the next month's report arrives
    preds = ledger["predictions"]
    scored_now = []
    for p in preds:
        if p.get("scored"):
            continue
        t = R.get(p["target_month"])
        if not t:
            continue
        if p["kind"] == "value":
            p.update(realised=t["usd_mn"], error_pct=round(100 * (p["pred"] / t["usd_mn"] - 1), 2), naive_error_pct=round(100 * (p["naive"] / t["usd_mn"] - 1), 2))
        elif p["kind"] == "ppc" and t.get("usd_per_ct"):
            p.update(realised=t["usd_per_ct"], error_pct=round(100 * (p["pred"] / t["usd_per_ct"] - 1), 2))
        elif p["kind"] == "call" and t.get("usd_per_ct") and p["inputs"].get("usd_per_ct"):
            mv = 100 * (t["usd_per_ct"] / p["inputs"]["usd_per_ct"] - 1)
            p.update(realised=t["usd_per_ct"], realised_move_pct=round(mv, 2), hit=call_hit(p["call"], mv))
        else:
            continue
        p.update(scored=True, scored_on=today.isoformat()); scored_now.append(p["id"])

    # 4. version: bump when weights, gate or active rules change
    sig = dict(mask=g["mask"], weights=value["weights"], active=bt["active"])
    version, changelog = old.get("version"), old.get("changelog", [])
    if not version:
        version = f"{VERSION_BASE}.0"; changelog = [dict(version=version, date=today.isoformat(), change="First version: monthly LGD call from GJEPC data; all model weights start at 0.")]
    elif old.get("signature") != sig:
        version = f"{VERSION_BASE}.{int(version.split('.')[1]) + 1}"
        changelog.append(dict(version=version, date=today.isoformat(), change=f"Re-fit on data to {m}: weights {value['weights']}, gate {g['reason']}, active rules {[k for k, v in bt['active'].items() if v] or 'none'}."))
    base = dict(made_on=today.isoformat(), data_month=m, target_month=madd(m, 1), model_version=version, scored=False)
    if not any(p["data_month"] == m for p in preds):
        preds.append(dict(id=f"{m}-value", kind="value", pred=value["pred_usd_mn"], naive=R[m]["usd_mn"], pred_pct=value["pred_pct"], weights=value["weights"], **base))
        if R[m].get("usd_per_ct"):
            preds.append(dict(id=f"{m}-ppc", kind="ppc", pred=R[m]["usd_per_ct"], basis="no change (model has no weight)", **base))
        preds.append(dict(id=f"{m}-call", kind="call", call=dec["call"], raw_call=dec["raw_call"], rule_price=dec["rule_price"], rule_demand=dec["rule_demand"],
                          rules_active=dec["rules_active"], data_too_thin=dec["data_too_thin"], softened=dec["softened"],
                          inputs=dict(usd_per_ct=R[m].get("usd_per_ct"), price_yoy_pct=dec["price_yoy_pct"], demand3_pct=dec["demand3_pct"], rules=RULES), **base))

    # 5. lessons (fixed templates)
    sc = [p for p in preds if p.get("scored")]
    names = {"price:stock up": "stock up when export price per carat is rising", "price:wait": "wait when export price per carat is falling",
             "demand:stock up": "stock up when exports are growing", "demand:wait": "wait when exports are shrinking"}
    L = []
    b = value["naive"]
    if b:
        L.append(f"Export-value forecast back-test ({b['start']} to {b['end']}, {b['n']} months): average error {(value['backtest'] or b)['mae_pct']}% vs {b['naive_mae_pct']}% for 'no change'. "
                 f"All four variables together: error {g['ungated']['mae_pct']}%. Gate: {g['reason']}.")
    if not value["model_has_edge"]:
        L.append("No variable has proven predictive value for next month's export value, so the forecast is 'no change' and is not used for the call.")
    L.append(f"Price per carat: {ppc['reason']}")
    for name, lab in (("price", f"price rule ({RULES['price_pct']}%)"), ("demand", f"demand rule ({RULES['demand_pct']}%)")):
        s = bt[name]
        L.append(f"Back-test of the {lab}: {s['n']} scored signals" + (f", right {s['hit_rate']}% (" + ", ".join(f"{k}: {v['hit_rate']}% of {v['n']}, opposite {v['opposite_hit_rate']}%" for k, v in s["by_call"].items()) + ")" if s["n"] else "") +
                 f". A side needs at least {MIN_N} signals and to beat the opposite action before it can drive the call.")
    L.append("Rule sides used for this month's call: " + (", ".join(names[k] for k, v in bt["active"].items() if v) or "none") + ".")
    if dec["data_too_thin"]:
        L.append("DATA TOO THIN: no rule has earned trust yet, so the call stays 'buy part' (buy only for confirmed orders) whatever the rules say. It will change only after enough months are scored.")
    L.append(f"Live track record: {len(sc)} predictions scored so far." + (" Learning: too few to judge." if len(sc) < 10 else ""))

    model = dict(version=version, generated_ist=datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"), data_month=m, next_data_expected=f"{madd(m, 2)}-07 to {madd(m, 2)}-15 (GJEPC report for {madd(m, 1)})",
                 signature=sig, changelog=changelog, rules=RULES, min_n=MIN_N, band=BAND, value=value, ppc=ppc, rules_backtest=bt, decision=dec, lessons=L,
                 gauges=dict(price_yoy_pct=dec["price_yoy_pct"], demand3_pct=dec["demand3_pct"], months=[madd(m, -2), m],
                             retail_1m_pct=round(100 * (retail[m] / retail[madd(m, -1)] - 1), 2) if retail.get(m) and retail.get(madd(m, -1)) else None,
                             retail_3m_pct=round(100 * (retail[m] / retail[madd(m, -3)] - 1), 2) if retail.get(m) and retail.get(madd(m, -3)) else None,
                             retail_since_start_pct=round(100 * (retail[max(retail)] / retail[min(retail)] - 1), 2) if retail else None,
                             retail_start=min(retail) if retail else None))
    ledger["predictions"] = preds[-1000:]
    PRED.write_text(json.dumps(ledger, indent=0))
    cols = ["id", "kind", "made_on", "data_month", "target_month", "model_version", "call", "pred", "naive", "scored", "realised", "error_pct", "naive_error_pct", "realised_move_pct", "hit"]
    with open(ROOT / "data/track_record.csv", "w", newline="") as fh:
        wr = csv.writer(fh); wr.writerow(cols)
        for p in preds:
            wr.writerow([p.get(c, "") for c in cols])
    arch = ROOT / "data/models"; arch.mkdir(exist_ok=True)
    if not (arch / f"model_{version}.json").exists():
        (arch / f"model_{version}.json").write_text(json.dumps(model, indent=1))
    MODEL.write_text(json.dumps(model, indent=1))
    print("model", version, "| data month", m, "| call", dec["call"], "(raw", dec["raw_call"] + ")", "| scored now", len(scored_now))
    for l in L:
        print(" -", l)


if __name__ == "__main__":
    main()
