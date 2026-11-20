import json, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gjepc as G  # noqa: E402
import model as m  # noqa: E402

OCT26 = """Ref: GJC/Sts/OCT 2026 /2026 - 2027/      Date: 09/11/2026
(4) Polished Lab Grown Diamonds OCT 2026 • Gross export of Polished Lab Grown Diamonds for the period Oct 2026 at US$ 170.46 million (in Rs. 1627.94 crores)
shows a growth of (+) 81.02% (+95.52% in Rs. term) over the comparative figure of US$ 94.17 million (Rs. 832.62 crores) for the previous year.
APR 2026 - OCT 2026 • Provisional gross export of Polished Lab Grown Diamonds for the period APR 2026 - OCT 2026 at US$ 855.56 million (in Rs. 8150.09 crores)
shows growth of (+)25.95% (+38.27% in Rs. term) over the comparative figure of US$ 679.31 million (Rs. 5894.26 crores) for previous year.
• In volume terms gross Exports of Worked Lab Grown diamonds at 129.13 lakhs carats during APR 2026 - OCT 2026 has shown a growth of (+)35.02%
compared with the exports at 95.64 lakhs carats during previous year. Per Carat Value April to Oct 2026 US$ 66.26
*Trade figures are Provisional and further shall be reconciled with DGCI&S data. (5) Gold Jewellery (Plain & Studded) OCT 2026 • The total gross export"""
SEP26 = OCT26.replace("09/11/2026", "13/10/2026").replace("129.13", "101.77").replace("855.56", "685.10").replace("OCT 2026", "SEP 2026").replace("Oct 2026", "Sep 2026").replace("170.46", "151.43")
APR26 = """Lab Grown Diamonds APR 2026 • Provisional gross export of Polished Lab Grown Diamonds for the period APR 2026 at US$ 93.28 million (in Rs. 872.16 crores) shows decline of (-)15.53%
over the comparative figure of US$ 110.43 million (Rs. 944.21 crores) for previous year. • In volume terms gross Exports of Worked Lab Grown diamonds at 13.59 lakhs carats during APR 2026
has shown a growth of (+)0.36% compared with the exports at 13.54 lakhs carats during previous year. Per Carat Value April 2026 US$ 68.59 (4) Gold Jewellery (Plain & Studded) APR 2026"""


class Parser(unittest.TestCase):
    def test_october_report(self):
        r = G.parse(OCT26)
        self.assertEqual(r["month"], "2026-10"); self.assertEqual(r["report_date"], "2026-11-09")
        self.assertEqual((r["month_usd_mn"], r["month_usd_mn_prev"], r["ytd_usd_mn"], r["ytd_usd_mn_prev"]), (170.46, 94.17, 855.56, 679.31))
        self.assertEqual((r["ytd_lakh_ct"], r["ytd_lakh_ct_prev"], r["ytd_usd_per_ct"]), (129.13, 95.64, 66.26))
        self.assertAlmostEqual(r["ytd_usd_per_ct_prev"], 71.03, places=2)

    def test_april_single_month(self):
        r = G.parse(APR26)
        self.assertEqual(r["month"], "2026-04"); self.assertEqual(r["ytd_usd_mn"], 93.28); self.assertEqual(r["ytd_lakh_ct"], 13.59)

    def test_series_derivation_same_vintage(self):
        rows = {x["month"]: x for x in G.build_series({"2026-09": G.parse(SEP26), "2026-10": G.parse(OCT26)})}
        self.assertEqual(rows["2026-10"]["lakh_ct"], 27.36)                 # 129.13 - 101.77
        self.assertAlmostEqual(rows["2026-10"]["usd_per_ct"], 62.30, places=2)   # 170.46 / 27.36 lakh
        self.assertNotIn("lakh_ct", rows["2026-09"])                       # no August report -> no carats derived, never guessed

    def test_no_text_no_row(self):
        self.assertIsNone(G.parse("Annex image only"))


class Rules(unittest.TestCase):
    def test_calls(self):
        self.assertEqual(m.price_call(-6.7), "wait"); self.assertEqual(m.price_call(5.0), "stock up"); self.assertIsNone(m.price_call(2)); self.assertIsNone(m.price_call(None))
        self.assertEqual(m.demand_call(49), "stock up"); self.assertEqual(m.demand_call(-20), "wait"); self.assertIsNone(m.demand_call(3))
        self.assertEqual(m.combine(["wait", "stock up"]), "buy part"); self.assertEqual(m.combine(["wait", None]), "wait"); self.assertEqual(m.combine([]), "buy part")

    def test_call_hit(self):
        self.assertTrue(m.call_hit("stock up", 0.5)); self.assertTrue(m.call_hit("wait", -0.5)); self.assertTrue(m.call_hit("buy part", 2.9)); self.assertFalse(m.call_hit("buy part", 3.1))

    def test_madd(self):
        self.assertEqual(m.madd("2026-12", 1), "2027-01"); self.assertEqual(m.madd("2026-01", -3), "2025-10")

    def test_caution_regex(self):
        import re
        for t in ["Lab-grown diamond prices fall another 20%", "LGD glut hits Surat units", "Lab-grown diamond prices crash"]:
            self.assertTrue(re.search(m.CAUTION, t, re.I), t)
        for t in ["Lab-Grown Diamond Wholesale Prices Rise in Q3 2026", "Half-Price Tennis Bracelets launched"]:
            self.assertFalse(re.search(m.CAUTION, t, re.I), t)


class Data(unittest.TestCase):
    def setUp(self):
        self.live = json.loads((ROOT / "data/live.json").read_text())
        self.model = json.loads((ROOT / "data/model.json").read_text())

    def test_every_item_has_source_and_date(self):
        for k, it in self.live["items"].items():
            self.assertTrue(it.get("source") and it.get("source_url") and it.get("as_of") and it.get("status"), k)

    def test_rules_inactive_below_min_n(self):
        bt = self.model["rules_backtest"]
        for k, v in bt["active"].items():
            name, side = k.split(":")
            b = bt[name]["by_call"].get(side)
            if v:
                self.assertGreaterEqual(b["n"], m.MIN_N)
        if not any(bt["active"].values()):
            self.assertEqual(self.model["decision"]["call"], "buy part"); self.assertTrue(self.model["decision"]["data_too_thin"])

    def test_model_weight_only_if_earned(self):
        v = self.model["value"]
        for w, k in zip(v["weights"], v["mask"]):
            if not k:
                self.assertEqual(w, 0)
        if any(v["mask"]):
            self.assertLess(v["backtest"]["mae_pct"], v["naive"]["naive_mae_pct"])
        self.assertEqual(self.model["ppc"]["weights"], [0, 0, 0, 0])


if __name__ == "__main__":
    unittest.main()
