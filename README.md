# Loyalty Card Member ATR — Interactive Analytics App

Web dashboard + Excel reporting for the V2 membership-card (loyalty) programme: member vs
non-member behaviour, Silver/Gold/Platinum tier economics, employee leakage, customer
BEFORE/AFTER card-day journeys, and ready-to-call CRM target lists — with date-window
filtering, drill-down interactivity and V2-house-format Excel export.

> **Documention:** [BRD.md](BRD.md) — complete Business Requirements Document
> (definitions, requirements register, data dictionary, architecture).

---

## What it does

| Module | What you get |
|---|---|
| **Overview** | KPI tiles (clickable → source data), weekly member-vs-total sales, enrolments by tier, price-grid mix, programme economics |
| **ATR Actions** | Data-driven observations + recommended actions, with editable Owner/Status (saved per window) |
| **Sales Mix** | Division / Sub-division / Major category / Price grid (E·V·P·SP) / Size / Colour — searchable, sortable, per-column value filters, member-vs-non-member index heat |
| **Weekly Trend** | Member sales, total sales, member share, new enrolments by tier |
| **Tiers** | Silver (₹200 · 2%) / Gold (₹500 · 5%) / Platinum (₹1,000 · 11%): members, sales, ABV, GM%, slab-discount leakage, grid mix |
| **Customers** | Per-member BEFORE card-day / card-day / AFTER journeys with FEE RECOVERED, HIGH DISC, RARE SHOPPER, NEW CUST flags |
| **Employee Factor** | Employees inside the member base with Active/Inactive status + staff-discount "double-dip" detection |
| **Margin & Economics** | Member GM% vs non-member by division **with the Silver/Gold/Platinum split** |
| **Campaigns** | Ranked target lists: winback, onboarding, card upgrade, trade-up, employee suppression |

Every completed window is cached — **Window history** reloads any past period instantly.
Excel export produces an **19-sheet V2-house-format workbook** (LAKHS totals, spacer
columns, freeze panes) plus per-list CSV downloads.

## Quick start

```bash
cd app
pip install -r ../requirements.txt
python server.py            # → http://127.0.0.1:5015
```

or double-click `app\run_app.bat` (Windows).

1. Pick a date window (All-time / 30d / 90d / MTD / FY26-27 / custom) → **Run analysis**
2. All-time builds scan ~34M POS rows and take ~5–10 min (short windows: <1 min);
   the progress bar shows a live per-step timer
3. **⤓ Excel** exports the V2 workbook for the loaded window

## Architecture

```
Browser (vanilla JS + local Chart.js, no CDN)
   │  /api/run → single-flight job queue → engine.build(frm, to) → Snowflake
   │  /api/job → live progress        /api/data → cached JSON payload
   │  /api/excel → V2 workbook        /api/targets → campaign CSVs
Flask (server.py, port 5015)
   ├── engine.py        date-parameterized build → results dict, pickled per window
   ├── excel_export.py  V2 house format via report_format_v2.py
   └── cache/           atr_<from>_<to>.pkl  (instant window reloads)
```

Data sources: Snowflake (`V2RETAIL` bronze/silver/gold) for POS + article + cost data;
HRMS (SQL Server) for the employee roster. Connections: Snowflake OAuth env vars or the
local JWT helper; HRMS via `HRMS_SERVER` / `HRMS_UID` / `HRMS_PWD` environment variables.

## Repository hygiene / security

- Customer mobiles, employee names and generated outputs are **not** part of the repo —
  see [.gitignore](.gitignore) (`app/cache/`, `app/output/`, `reports/targets/`, HTML build).
- No credentials in source: Snowflake uses env vars/JWT helper; the HRMS password is
  expected via environment variables.

## Legacy batch builder

`reports/build_loyalty_atr.py` is the original all-time single-file builder (static HTML +
target CSVs). It still works standalone; the web app supersedes it for interactive use.
