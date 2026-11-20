"""Parse GJEPC monthly 'Gem & Jewellery Exports' reports (text layer of the public PDF) for the
'Polished Lab Grown Diamonds' section. Only text the PDF states is read; nothing is OCR'd or guessed.
Returns per report: month value, previous-year same month, YTD value, previous-year YTD, YTD carats (lakh),
previous-year YTD carats, YTD per-carat value."""
import re

MON = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
NUM = r"([\d,]+(?:\.\d+)?)"


def _ym(s):
    """'Oct 2026' / 'October 2026' / 'JUNE 2026' -> '2026-10'"""
    m = re.match(r"([A-Za-z]{3})[A-Za-z]*\.?\s*'?(\d{2,4})", s.strip())
    if not m or m.group(1).upper() not in MON:
        return None
    y = int(m.group(2)); y = y + 2000 if y < 100 else y
    return f"{y:04d}-{MON[m.group(1).upper()]:02d}"


def _f(s):
    return float(s.replace(",", ""))


def report_date(text):
    m = re.search(r"Date\s*:\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text[:3000])
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else None


def parse(text):
    t = re.sub(r"\*Trade figures are Provisional[^.]*\.", " ", text)
    t = re.sub(r"\s+", " ", t)
    i = re.search(r"Lab[- ]Grown Diamonds\s*(?:[A-Za-z]{3,9}\s*\d{4}\s*)?•", t, re.I)
    if not i:
        return None
    sec = t[i.start(): i.start() + 2500]
    j = re.search(r"Gold Jewellery \(Plain", sec)
    sec = sec[: j.start()] if j else sec
    out = {}
    for m in re.finditer(r"export of (?:Polished |Worked )?Lab[- ]Grown Diamonds for the (?:period|month of)?\s*([^@]{3,40}?)\s+at US\$\s*" + NUM +
                         r" million.{0,200}?(?:figure of|compared to|compared with|as against)\s*US\$\s*" + NUM + r" million", sec, re.I):
        per, cur, prv = m.group(1), _f(m.group(2)), _f(m.group(3))
        rng = re.match(r"([A-Za-z]+\s*\d{4})\s*-\s*([A-Za-z]+\s*\d{4})", per)
        if rng:
            a, b = _ym(rng.group(1)), _ym(rng.group(2))
            if a and b and a[5:] == "04":
                out.update(ytd_from=a, month=b, ytd_usd_mn=cur, ytd_usd_mn_prev=prv)
        else:
            ym = _ym(per)
            if ym:
                out.setdefault("month", ym)
                out.update(month_usd_mn=cur, month_usd_mn_prev=prv)
    c = re.search(r"Lab[- ]Grown diamonds at " + NUM + r" lakhs? carats during ([A-Za-z]+\s*\d{4})(?:\s*-\s*([A-Za-z]+\s*\d{4}))?.{0,200}?(?:exports|imports) at " + NUM + r" lakhs? carats", sec, re.I)
    if c:
        out.update(ytd_lakh_ct=_f(c.group(1)), ytd_lakh_ct_prev=_f(c.group(4)))
        out.setdefault("month", _ym(c.group(3) or c.group(2)))
    p = re.search(r"Per Carat Value[^$]{0,40}US\$\s*" + NUM, sec, re.I)
    if p and c:
        out["ytd_usd_per_ct"] = _f(p.group(1))
    if out.get("month", "")[5:] == "04" and "month_usd_mn" in out and "ytd_usd_mn" not in out:
        out.update(ytd_usd_mn=out["month_usd_mn"], ytd_usd_mn_prev=out["month_usd_mn_prev"], ytd_from=out["month"])
    if "ytd_lakh_ct" in out and "ytd_usd_mn" in out:
        out["ytd_usd_per_ct_prev"] = round(out["ytd_usd_mn_prev"] * 10 / out["ytd_lakh_ct_prev"], 2)
        out.setdefault("ytd_usd_per_ct", round(out["ytd_usd_mn"] * 10 / out["ytd_lakh_ct"], 2))
    out["report_date"] = report_date(text)
    return out if out.get("month") and ("ytd_usd_mn" in out or "month_usd_mn" in out) else None


def fy_start(ym):
    y, m = int(ym[:4]), int(ym[5:])
    return y if m >= 4 else y - 1


def prev_month(ym):
    y, m = int(ym[:4]), int(ym[5:])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def shift_year(ym, k=-1):
    return f"{int(ym[:4]) + k}{ym[4:]}"


def build_series(reports):
    """reports: {month: parsed report for that month}. Uses each report's OWN (first-published) figures only, so a
    monthly figure is never the difference of two different vintages. Returns rows sorted by month with:
    usd_mn (month export value), lakh_ct (month carats), usd_per_ct (month export unit value),
    ytd_usd_per_ct / ytd_usd_per_ct_prev / ppc_yoy (same-report year-on-year per-carat comparison),
    usd_mn_prev (same month last year as restated in this report), how_v / how_c (stated or derived)."""
    rows = []
    for mo in sorted(reports):
        r, pm = reports[mo], reports.get(prev_month(mo), {})
        row = {"month": mo}
        if "month_usd_mn" in r:
            row.update(usd_mn=r["month_usd_mn"], usd_mn_prev=r["month_usd_mn_prev"], how_v="stated in report")
        elif "ytd_usd_mn" in r and "ytd_usd_mn" in pm and mo[5:] != "04" and fy_start(mo) == fy_start(prev_month(mo)):
            row.update(usd_mn=round(r["ytd_usd_mn"] - pm["ytd_usd_mn"], 2), how_v="derived: this report's YTD minus last report's YTD")
            if "ytd_usd_mn_prev" in pm:
                row["usd_mn_prev"] = round(r["ytd_usd_mn_prev"] - pm["ytd_usd_mn_prev"], 2)
        if "ytd_lakh_ct" in r:
            row.update(ytd_lakh_ct=r["ytd_lakh_ct"], ytd_usd_per_ct=r["ytd_usd_per_ct"], ytd_usd_per_ct_prev=r["ytd_usd_per_ct_prev"],
                       ppc_yoy=round(r["ytd_usd_per_ct"] / r["ytd_usd_per_ct_prev"] - 1, 4))
            if mo[5:] == "04":
                row.update(lakh_ct=r["ytd_lakh_ct"], how_c="stated (April = year to date)")
            elif "ytd_lakh_ct" in pm and fy_start(mo) == fy_start(prev_month(mo)):
                row.update(lakh_ct=round(r["ytd_lakh_ct"] - pm["ytd_lakh_ct"], 2), how_c="derived: this report's YTD carats minus last report's")
            if row.get("lakh_ct", 0) > 0 and row.get("usd_mn"):
                row["usd_per_ct"] = round(row["usd_mn"] * 10 / row["lakh_ct"], 2)
        if "usd_mn" in row:
            rows.append(row)
    return rows
