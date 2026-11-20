# LGD Desk · Surat

Public, zero-cost decision support for Surat lab-grown diamond manufacturers and polished LGD traders: this month, stock up, wait, or buy only part? Counterpart of [STTIE](https://github.com/cakashoza-cmyk/sttie) (yarn) and [Gold Desk](https://github.com/cakashoza-cmyk/gold), same structure and learning loop.

Live: https://cakashoza-cmyk.github.io/lgd/ · Methodology: methodology.html

- The data is **monthly** (GJEPC export reviews), so the call is monthly. The workflow runs daily at 10:00 IST to pick up a new report within a day.
- `scripts/fetch.py` + `scripts/gjepc.py`: GJEPC report PDFs (text layer only, cached in `data/gjepc_reports.json`), Caratlytics retail index [unofficial], BLS, FRED, RBI/FBIL, ECB, Google News RSS → `data/live.json`.
- `scripts/model.py`: gauges, rules back-test with trust gate, walk-forward model with weight gate, dated ledger (`data/predictions.json`), scoring, `data/model.json`, `data/models/`, `data/track_record.csv`.
- `data/rates.json`: US duty and DGVCL power tariff, checked by hand with links.
- Tests: `python3 -m unittest discover -s test -p "test_*.py"`; browser check and screenshots: `test/verify.py`.

No keys, no backend, no AI-generated numbers. User entries stay in the browser. Not investment advice.
