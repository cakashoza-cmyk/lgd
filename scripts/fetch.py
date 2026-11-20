"""LGD desk data refresh. Free public sources only, no keys. Writes data/live.json.
Each source is fetched independently; on failure the last good value is kept and marked stale.
Same pattern as STTIE (github.com/cakashoza-cmyk/sttie) and Gold Desk (github.com/cakashoza-cmyk/gold)."""
import json, re, ssl, html, sys, subprocess, tempfile, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gjepc as G

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "live.json"
CACHE = ROOT / "data" / "gjepc_reports.json"
IST = timezone(timedelta(hours=5, minutes=30))
UA = {"User-Agent": "Mozilla/5.0 (LGD desk data refresh; github.com/cakashoza-cmyk/lgd)"}
NOW = datetime.now(IST)
GJ_PAGE = "https://gjepc.org/statistics.php"


def get(url, headers=None, timeout=45):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url.replace(" ", "%20"), headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def check(v, lo, hi, name):
    if v is None or not (lo <= v <= hi):
        raise ValueError(f"{name}={v} outside sanity range {lo}-{hi}")
    return v


def pdf_text(data):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data); f.flush()
        return subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, timeout=60).stdout.decode("utf-8", "ignore")


# ---------- sources ----------
def gjepc():
    """GJEPC (Gem & Jewellery Export Promotion Council) monthly export reports, public PDFs on gjepc.org/statistics.php.
    Only the text 'Polished Lab Grown Diamonds' section is read. Parsed reports are cached in data/gjepc_reports.json."""
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    page = get(GJ_PAGE, timeout=60).decode("utf-8", "ignore")
    links = sorted(set(html.unescape(l) for l in re.findall(r'href="(admin/StatisticsExport/[^"]+\.pdf)"', page, re.I)))
    new = 0
    for l in links:
        if l in cache:
            continue
        try:
            cache[l] = G.parse(pdf_text(get("https://gjepc.org/" + l, timeout=90)))
        except Exception as e:  # noqa: BLE001
            print("gjepc pdf failed", l, e); continue
        new += 1
    CACHE.write_text(json.dumps(cache, indent=0, sort_keys=True))
    reps, src = {}, {}
    for l, r in cache.items():
        if r and (r["month"] not in reps or (r.get("report_date") or "") > (reps[r["month"]].get("report_date") or "")):
            reps[r["month"]] = r; src[r["month"]] = l
    rows = G.build_series(reps)
    for r in rows:
        r["report_url"] = "https://gjepc.org/" + src[r["month"]].replace(" ", "%20")
        r["report_date"] = reps[r["month"]].get("report_date")
    last = rows[-1]
    check(last["usd_mn"], 5, 2000, "lgd usd_mn")
    common = dict(source="GJEPC monthly export review (provisional), Polished Lab Grown Diamonds section", source_url=GJ_PAGE, status="official")
    note = ("GJEPC is the Commerce Ministry's export promotion council; figures are provisional and are later reconciled with DGCI&S, "
            "so last year's months are often restated. Each monthly figure here is taken from that month's own report (first published).")
    out = {"lgd_rows": dict(label="LGD polished exports, monthly table", value=len(rows), unit="months", as_of=last["month"],
                            note=note + f" Reports parsed: {sum(1 for v in cache.values() if v)} of {len(cache)} PDFs ({new} new this run).", rows=rows,
                            report_url=last["report_url"], report_date=last.get("report_date"), **common)}
    out["lgd_exports"] = dict(label="LGD polished exports, latest month", value=last["usd_mn"], unit="US$ million", as_of=last["month"],
                              prev_year=last.get("usd_mn_prev"), note=note, history=[[r["month"], r["usd_mn"]] for r in rows],
                              report_url=last["report_url"], report_date=last.get("report_date"), **common)
    pc = [r for r in rows if r.get("usd_per_ct")]
    if pc:
        out["lgd_ppc"] = dict(label="LGD export price per carat, latest month", value=pc[-1]["usd_per_ct"], unit="US$ per carat", as_of=pc[-1]["month"],
                              note=note + " Month carats = this report's year-to-date carats minus last month's report; price = value / carats. Mix of sizes and qualities moves it, not only price.",
                              history=[[r["month"], r["usd_per_ct"]] for r in pc], report_url=pc[-1]["report_url"], report_date=pc[-1].get("report_date"), **common)
    yy = [r for r in rows if r.get("ppc_yoy") is not None]
    if yy:
        y = yy[-1]
        out["lgd_ppc_ytd"] = dict(label="LGD export price per carat, financial year to date", value=y["ytd_usd_per_ct"], unit="US$ per carat", as_of=y["month"],
                                  prev_year=y["ytd_usd_per_ct_prev"], yoy=y["ppc_yoy"], ytd_lakh_ct=y["ytd_lakh_ct"],
                                  note=note + " Last year's figure = last year's value / last year's carats as stated in the same report.",
                                  history=[[r["month"], r["ytd_usd_per_ct"]] for r in yy], report_url=y["report_url"], report_date=y.get("report_date"), **common)
    return out


def caratlytics():
    """Caratlytics Diamond Price Index: monthly median listed retail price per carat across 140+ online retailers (CC BY 4.0). Unofficial."""
    page = get("https://caratlytics.com/price-index/").decode("utf-8", "ignore")
    csvs = sorted(set(re.findall(r'/datasets/caratlytics-index-(\d{4}-\d{2})\.csv', page)))
    url = f"https://caratlytics.com/datasets/caratlytics-index-{csvs[-1]}.csv"
    lines = get(url).decode().strip().splitlines()
    hdr = lines[0].split(",")
    hist = []
    for l in lines[1:]:
        r = dict(zip(hdr, l.split(",")))
        if r["segment"] == "lab_grown":
            hist.append([r["month"], float(r["median_usd_per_carat"])])
    hist.sort()
    return dict(label="Lab-grown retail price index (median listed price)", value=check(hist[-1][1], 20, 20000, "caratlytics"), unit="US$ per carat",
                as_of=hist[-1][0], source="Caratlytics Diamond Price Index (CC BY 4.0)", source_url="https://caratlytics.com/price-index/",
                data_url=url, status="unofficial",
                note="Private index, not official. Median listed (asking) price per carat of lab-grown stones 0.25-10 ct at 140+ mostly US online retailers; "
                     "listing prices, not transaction prices, and the mix of stones listed moves it. Series starts Dec 2025.", history=hist)


BLS = {"cpi_jewelry": ("CUUR0000SEAG02", "US consumer price index: jewelry", "index 1982-84 = 100 (not seasonally adjusted)", 50, 1000),
       "us_jewelry_jobs": ("CEU4245830001", "US jobs at jewelry, luggage and leather goods stores", "thousand jobs (not seasonally adjusted)", 30, 500)}


def bls(key):
    sid, label, unit, lo, hi = BLS[key]
    body = json.dumps({"seriesid": [sid], "startyear": str(NOW.year - 9), "endyear": str(NOW.year)}).encode()
    req = urllib.request.Request("https://api.bls.gov/publicAPI/v1/timeseries/data/", data=body, headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        j = json.loads(r.read())
    if j.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError(str(j.get("message"))[:200])
    hist = sorted([f"{d['year']}-{d['period'][1:]}", float(d["value"])] for d in j["Results"]["series"][0]["data"] if d["period"].startswith("M") and d["period"] != "M13" and re.fullmatch(r"[\d.]+", d["value"]))
    d, v = hist[-1]
    return dict(label=label, value=check(v, lo, hi, key), unit=unit, as_of=d, source=f"US Bureau of Labor Statistics (series {sid})",
                source_url=f"https://data.bls.gov/timeseries/{sid}", status="official",
                note="Official US data, monthly; the latest month can be revised. Context for US demand, the largest market for Indian LGD.", history=hist)


def fred_ir413():
    rows = [l.split(",") for l in get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=IR413", timeout=60).decode().strip().splitlines()[1:]]
    hist = [[d[:7], float(v)] for d, v in rows if v not in (".", "")][-240:]
    return dict(label="US import price index: gems, jewelry and coins", value=check(hist[-1][1], 20, 2000, "ir413"), unit="index 2000 = 100", as_of=hist[-1][0],
                source="US Bureau of Labor Statistics via FRED (series IR413)", source_url="https://fred.stlouisfed.org/series/IR413", status="official",
                note="Prices of all imported gems, jewelry and coins into the US (natural diamonds and gold dominate), not LGD alone. Context only.", history=hist)


def rbi():
    t = get("https://www.rbi.org.in/", timeout=60).decode("utf-8", "ignore")
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))
    usd = float(re.search(r"INR\s*/\s*1 USD\s*:\s*([\d.]+)", t).group(1))
    m = re.search(r"\(As at [^)]*? of (\w+ \d{1,2}, \d{4})\)", t)
    fx_date = datetime.strptime(m.group(1), "%B %d, %Y").strftime("%Y-%m-%d") if m else NOW.strftime("%Y-%m-%d")
    return {"usdinr": dict(label="USD/INR reference rate", value=check(usd, 60, 140, "usdinr"), unit="₹ per US$", as_of=fx_date,
                           source="FBIL reference rate, as shown on RBI homepage", source_url="https://www.fbil.org.in/", status="official",
                           note="Set once a day around 1:30 pm IST. A weaker rupee raises the rupee value of each dollar of exports.")}


def ecb():
    start = (NOW - timedelta(days=2400)).strftime("%Y-%m-%d")
    j = json.loads(get(f"https://api.frankfurter.dev/v1/{start}..?base=USD&symbols=INR"))
    month = {}
    for d in sorted(j["rates"]):
        month.setdefault(d[:7], []).append(j["rates"][d]["INR"])
    hist = [[m, round(sum(v) / len(v), 4)] for m, v in sorted(month.items())]
    return {"usdinr_m": dict(label="USD/INR, monthly average (ECB cross rate)", value=check(hist[-1][1], 40, 140, "inr"), unit="₹ per US$", as_of=hist[-1][0],
                             source="European Central Bank reference rates via Frankfurter API", source_url="https://www.frankfurter.app/", status="live",
                             note="Monthly averages, used for history and the model; the current month is month-to-date.", history=hist)}


def headlines():
    q = '("lab grown diamond" OR "lab-grown diamond" OR LGD diamond) (price OR export OR tariff OR Surat) when:30d'
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=en-IN&gl=IN&ceid=IN:en"
    root = ET.fromstring(get(url))
    items = []
    for it in root.iter("item"):
        t = (it.findtext("title") or "").lstrip("| ").strip()
        if not re.search(r"lab[- ]?grown|\bLGD\b|synthetic diamond|CVD", t, re.I) or any(x["title"] == t for x in items):
            continue
        d = datetime.strptime(it.findtext("pubDate"), "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc).astimezone(IST)
        items.append({"title": t, "date": d.strftime("%Y-%m-%d"), "url": it.findtext("link")})
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:15]


def main():
    prev = json.loads(OUT.read_text()) if OUT.exists() else {"items": {}}
    items, errors, groups = {}, {}, {}

    def run(name, fn, keys=None):
        try:
            r = fn()
            r = r if keys is None else {keys: r}
            for k in r:
                groups[k] = name
            items.update(r)
        except Exception as e:  # noqa: BLE001
            errors[name] = f"{type(e).__name__}: {e}"[:300]
            for k, old in prev["items"].items():
                if old.get("group") == name and k not in items:
                    old = dict(old); old["status"] = "stale"; old["stale_reason"] = errors[name]
                    items[k] = old

    run("gjepc", gjepc)
    run("caratlytics", caratlytics, "retail_lgd")
    for k in BLS:
        run("bls_" + k, lambda k=k: bls(k), k)
    run("fred_ir413", fred_ir413, "us_import_px")
    run("rbi", rbi)
    run("ecb", ecb)
    for k, v in items.items():
        if k in groups:
            v["group"] = groups[k]
    try:
        news = headlines()
    except Exception as e:  # noqa: BLE001
        errors["news"] = str(e)[:300]; news = prev.get("headlines", [])
    out = {"generated_ist": NOW.strftime("%Y-%m-%d %H:%M IST"), "items": items, "errors": errors, "headlines": news,
           "headlines_source": "Google News RSS search (public). Headlines only; never used in a number. A price-cut or glut headline can only make the call more cautious."}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print("items:", len(items), "| errors:", json.dumps(errors))
    for k, v in items.items():
        print(f"  {k:16s} {v['value']:>10} {v['unit']:44s} as of {v['as_of']}  [{v['status']}]  hist={len(v.get('history', []))}")
    if "lgd_exports" not in items:
        sys.exit(2)


if __name__ == "__main__":
    main()
