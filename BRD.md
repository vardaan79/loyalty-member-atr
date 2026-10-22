# Business Requirements Document
## Loyalty Card Member — Action Taken Report (ATR) Web App

| | |
|---|---|
| **Document** | BRD — Loyalty Card Member ATR Interactive App |
| **Version** | 1.0 (app v3.2) |
| **Date** | 22-Oct-2026 |
| **Status** | Implemented & in production use (single-user internal deployment) |
| **Owner** | CRM / Marketing Analytics |
| **Related** | [README.md](README.md) · `loyalty_member_atr.sql` (standalone SQL) |

---

## 1. Executive Summary

The V2 membership-card programme (Silver ₹200/2%, Gold ₹500/5%, Platinum ₹1,000/11% slabs)
launched in Sep-2026. The business needed to answer, continuously and on any date window:
**who the card members are, what they buy, what they cost us, and what we should do next** —
replacing a manual, fixed-period static report with an interactive app that any analyst can
drive, plus board-ready Excel in the company's locked V2 house format.

The ATR app delivers: member vs non-member behaviour across the full merchandise hierarchy,
tier economics with margin impact, employee-card leakage (incl. staff-discount "double-dip"),
customer-level before/after card-day journeys, and five ranked, contactable CRM campaign lists —
with a date-window filter, drill-down interactivity and one-click V2 Excel export.

## 2. Business Context & Problem Statement

| Pain (before) | Outcome (after) |
|---|---|
| Static HTML for one fixed period; any new question = code change | Any date window on demand; presets + custom range; results cached per window |
| No tier or margin view in the break-up report | Tier segmentation, GM% by division **with Silver/Gold/Platinum split**, programme economics (card revenue vs slab discount) |
| Employee exposure unknown | 867 employee card-holders identified with Active/Inactive status + 48 double-dippers (staff discount + member slab on one wallet) |
| No customer-level view of value before/after buying the card | 33k-row BEFORE/AFTER journey sheet with fee-recovery and behavioural flags (matches the Marketing Deep-Dive definition, verified row-perfect) |
| Campaign lists built ad-hoc | Five ranked, junk-filtered, employee-suppressed target lists regenerated per window (CSV + in-app previews) |
| Excel assembled by hand | One-click 19-sheet workbook in the locked V2 house format |

## 3. Objectives & Success Criteria

1. **Self-serve analysis** — any window from first card sale to today without developer help. ✅
2. **Trustworthy definitions** — every metric matches the agreed definitions (member proxy, tier by highest card SKU, Deep-Dive flags verified 28,474/28,474 rows against the reference workbook). ✅
3. **Actionable output** — every run produces ready-to-call lists + V2 Excel. ✅
4. **Speed** — short windows (<90 days) in under a minute; all-time ~5–10 min with live progress; cached windows reload instantly. ✅
5. **Zero-IT deployment** — one folder, `python server.py`, local Chart.js (intranet-safe). ✅

## 4. Scope

**In scope:** Snowflake POS-based member analytics (Axapta CRM_BRONZE/SILVER/GOLD), HRMS
employee overlay, interactive dashboard, V2 Excel export, campaign CSVs, window caching,
campaign list regeneration.

**Out of scope:** POS behaviour change (LOYALTYCARDID capture — raised as data-quality action),
RCS/call-centre campaign execution, marketing-cost accounting beyond slab discount and card
revenue, multi-user auth (single-user internal tool), mobile app.

## 5. Users & Stakeholders

| User | Use |
|---|---|
| CRM / Marketing Analytics (primary) | Run windows, read insights, export Excel/CSVs, brief leadership |
| Marketing leadership | ATR actions table, tier economics, campaign plan |
| Retail / Merchandising | Mix & index tables (allocation, size-sets, pricing) |
| Data platform | Data-quality actions (LOYALTYCARDID, article-master gaps) |

## 6. Functional Requirements

### 6.1 Window selection & execution
- **FR-1** Presets: All-time, 30d, 90d, MTD, FY26-27, custom from/to. Validation: from ≤ to, ISO dates.
- **FR-2** Runs execute server-side in a single-flight queue (one Snowflake build at a time), FIFO.
- **FR-3** Live progress: step text, %, and per-step elapsed timer; heads-up notice for all-time builds.
- **FR-4** Every completed window is cached (`atr_<from>_<to>.pkl`, atomic write) and reloadable
  instantly from **Window history**, across server restarts.
- **FR-5** Re-running an already-cached/running window reuses the existing result/job.

### 6.2 Analytics modules
- **FR-6 Overview** — KPI tiles (members, sales, bills, ABV, IPB, ASP, GM%, card revenue,
  employee members, campaign targets); each tile clickable → source table pre-filtered.
  Weekly stacked member/non-member sales + member-share line; enrolments by tier; grid doughnut;
  programme economics panel.
- **FR-7 ATR actions** — data-driven insight rows (basket, grid index, over/under-indexed
  categories, sizes, colours, tiers, employee factor, GM, campaigns, data quality) with
  recommended actions; Owner/Status editable inline, persisted per window (localStorage).
- **FR-8 Sales mix** — dimension switch (Division / Sub-division / Major category / Grid /
  Size / Colour) with member sales, qty, bills, member/non-member share, **index** (member share ÷
  non-member share × 100; >110 green, <90 red), grid mix per row and grid-within-division matrix.
- **FR-9 Weekly trend** — member sales, total sales, share %, new members, new by tier.
- **FR-10 Tiers** — tier cards + table: members, share, sales, sales share, card revenue, buyers,
  bills/member, ABV, GM%, est. slab discount, E/V/P/SP mix; tier charts (sales + GM%, 100% grid mix).
- **FR-11 Customers (BEFORE/AFTER)** — per-member pre-card (all-time visits, last visit, 12M
  visits/bills/value/ABV), card-day (bills/value/ABV/discount), post-card (visits/bills/value/ABV/
  discount), TTL discount, card fee, DISC % fee, **FEE RECOVERED** (NOT USED / <50% / 50–99% / FULLY),
  EMPLOYEE (Y/N + status), **HIGH DISC** (disc ≥ fee), **RARE SHOPPER** (shopped before, ≤1 visit in
  12M pre-card), **NEW CUST** (zero pre-card visits), combined FLAG. KPI tiles per flag; full list in
  Excel/CSV; searchable/sortable/filterable preview in-app.
- **FR-12 Employee factor** — employees in the member base (HRMS mobile-normalised) with
  **Active/Inactive status**; card purchases; staff-discount bills (ACX_EMPID/ACX_EMPDISCAMOUNT);
  double-dip flags (staff discount + membership card on same mobile); searchable table.
- **FR-13 Margin & economics** — member vs non-member GM% by division (costed lines only) **with
  Silver/Gold/Platinum GM% split per division**; below-benchmark cells highlighted; programme
  margin view (incremental GM − slab discount + card revenue).
- **FR-14 Campaigns** — dormant winback (2+ member bills, 30d+ dormant), onboarding (≤1 bill),
  card upgrade (Silver ≥ ₹25k / Gold ≥ ₹50k), premium trade-up (≥80% spend in E/V), employee
  suppression; counts + member-period value + top-25 previews + per-list CSV download.

### 6.3 Interactivity & table engine
- **FR-15** Every data table: click-to-sort on headers, free-text search, **per-column value
  filters** (distinct-value checklist with counts, in-filter search, active-funnel indicator),
  sticky headers, tabular numerals, count notes. Filters reset on window change.
- **FR-16 Charts** (Chart.js, served locally — no CDN dependency): stacked sales, enrolments,
  doughnut, share line, tier sales/GM combo, 100% stacked tier grid, GM comparison, mix top-12 bars.

### 6.4 Outputs
- **FR-17 Excel export (V2 house format)** — 19 sheets via the locked `report_format_v2.py`
  module: 01-ATR-SUMMARY, 02-KPI, 03-WEEKLY, 04-DIVISION, 05-SUB-DIVISION, 06-MAJ-CATEGORY,
  07-PRICE-GRID, 08-SIZE, 09-COLOUR, 10-TIERS, 11-EMPLOYEES, 12-GM-DIVISION (with tier split),
  13-CAMPAIGNS, 14-WINBACK, 15-ONBOARD, 16-UPGRADE, 17-TRADEUP, 18-SUPPRESS, 19-CUSTOMERS.
  House anatomy: merged title, header band R4, GRD-TOT R6 / SUB-TOT R7 (sales in LAKHS, counts
  plain SUM), spacer R8, data R9+, yellow spacer columns, freeze C9, zoom 85. Campaign list sheets
  capped at 5,000 rows (full lists in CSVs). Workbooks cached on disk and reused.
- **FR-18 Campaign CSVs** — `reports/targets/<window-end>_<list>.csv`, ranked, junk-mobile
  filtered (`^[6-9]\d{9}$`, no repeat-digit test mobiles), employee suppression applied via list.

## 7. Non-Functional Requirements

- **NFR-1 Performance** — ≤60s for ≤90-day windows; ~5–10 min all-time (34M-row scan); dashboard
  payload <400 KB, served from per-window cache in <100 ms on repeat loads.
- **NFR-2 Reliability** — Snowflake session loss auto-reconnects; temp-table rebuild per run;
  atomic cache writes; one failing dashboard section never blanks the page (isolated render).
- **NFR-3 Security** — no credentials in source (Snowflake OAuth env/JWT helper; HRMS via env
  vars); customer PII (mobiles) and employee names never committed (gitignored outputs);
  internal-only deployment (localhost binding).
- **NFR-4 Usability** — responsive (hamburger drawer <940px); no external CDN (works on the
  corporate intranet); all interactivity keyboard/mouse native.
- **NFR-5 Maintainability** — engine separated from presentation; one date-parameterized data
  layer shared by UI and Excel; house-format module reused, not duplicated.

## 8. Data Sources & Definitions (authoritative)

| Object | Source |
|---|---|
| POS lines / bills | `V2RETAIL.CRM_BRONZE.AXAPTA_SALESTRANS` / `AXAPTA_BILL_WISE_DATA` (sign-flipped; returns netted; card SKUs excluded from sales; zero-value lines excluded from qty) |
| Article attributes | `V2RETAIL.SILVER.V_VAR_ARTICLE_MASTER` (join `a.var_article::varchar = ltrim(itemid,'0')` — NUMBER column, cast-safe) |
| Unit cost chain | `GOLD.LOYALTY_ART_COST` (store×article) → `GOLD.LOYALTY_ART_COST_ANY` → `VW_PRODUCT_MASTER.ARTICLE_COST`; GM = sale − qty × cost on costed lines only |
| Card SKUs (do not change) | `…018` Silver ₹200/2% · `…003` Gold ₹500/5% · `…004` Platinum ₹1,000/11% (`MIS_REPORTS.LOYALTY_BILLS` is a lagging delta — static union applied) |
| Employee roster | HRMS `tblEmployee` (all statuses) via SQL Server, mobile-normalised; fallback JSON snapshot (status = Unknown) |

**Key definitions**
- **Member** = customer mobile on any bill containing a loyalty card SKU; member sales counted
  from first card purchase (POS `LOYALTYCARDID` is empty — best available proxy).
- **Tier** = highest card SKU ever held (lifetime identity).
- **Windowing** — sale-line analytics filtered to [from, to]; member identity lifetime; enrolment
  trend + card revenue windowed; campaign dormancy anchored on window end.
- **Visit** = a day with a bill. **Index** = member share ÷ non-member share × 100.
- **Customer flags** (verified against the Marketing Deep-Dive reference, 28,474/28,474 rows):
  FEE RECOVERED = disc buckets (0 → NOT USED; ≥100% → FULLY; 50–99%; <50%);
  HIGH DISC = total discount ≥ card fee; RARE SHOPPER = pre-card visits ≥1 and 12M pre-card ≤1;
  NEW CUST = zero pre-card visits.
- **GM caveat** — ex-tax costs vs MRP-inclusive sales: levels read rich vs books; tier/division
  gaps are the reliable signal.

## 9. System Overview

```
┌────────────────────────── Browser (http://127.0.0.1:5015) ──────────────────────────┐
│  index.html + app.css + app.js (vanilla) + chart.umd.js (local)                      │
│  tabs: Overview · ATR · Mix · Trend · Tiers · Customers · Employees · Margin · Campaigns │
└──────────────┬───────────────────────────────────────────────────────────────────────┘
               │ fetch (JSON)
┌──────────────▼──────────────── Flask server.py ─────────────────────────────────────┐
│ POST /api/run   → single-flight queue → worker thread                                │
│ GET  /api/job   → {status, step, pct, elapsed}                                        │
│ GET  /api/data  → cleaned JSON payload (NaN→null; cached per window)                 │
│ GET  /api/excel → V2 workbook (built once, reused)                                    │
│ GET  /api/targets/<job>/<list> → campaign CSV                                         │
│ engine.build(frm, to, progress) → results dict → app/cache/atr_<frm>_<to>.pkl        │
└──────────────┬───────────────────────────────────┬───────────────────────────────────┘
               │ SQL (OAuth env / sf_helper JWT)   │ pyodbc
      ┌────────▼─────────┐                 ┌────────▼────────┐
      │ Snowflake V2RETAIL│                 │ HRMS SQL Server │
      └───────────────────┘                 └─────────────────┘
```

## 10. Data Dictionary — Customer BEFORE/AFTER sheet (19-CUSTOMERS / CSV)

| # | Column | Definition |
|---|---|---|
| 1 | CUST MOB | Member mobile (junk-filtered) |
| 2 | CARD TYPE | Silver / Gold / Platinum (highest SKU held) |
| 3 | ST-CD | Store of first card purchase |
| 4 | CARD PUR DT | First card purchase date |
| 5 | DAYS SINCE | Window-end − card date |
| 6–7 | BEF VISITS / BEF LAST VISIT DT | Distinct bill-days & last bill-day before card date (all-time) |
| 8–11 | BEF VIS 12M / BILLS / VAL / ABV | 12 months before card date |
| 12–15 | CD BILLS / VAL / ABV / DISC | On the card-purchase day |
| 16–20 | AFT VISITS / BILLS / VAL / ABV / DISC | Card day +1 → window end |
| 21–23 | TTL DISC / CARD FEE / DISC % FEE | CD+AFT discount; tier list price; disc ÷ fee |
| 24 | FEE RECOVERED | NOT USED / <50% / 50–99% / FULLY |
| 25–26 | EMP / STATUS | HRMS match Y/N; Active / Inactive / Unknown |
| 27–29 | HIGH DISC / RARE SHOPPER / NEW CUST | See §8; FLAG = joined string |

## 11. Dependencies

Python 3.12+ (runs on 3.14): `flask`, `pandas`, `openpyxl`, `snowflake-connector-python`,
`pyodbc` (HRMS; optional), `report_format_v2.py` (company module, path-configured in
`excel_export.py`), Chart.js vendored at `app/static/vendor/` (MIT).

## 12. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| POS `LOYALTYCARDID` empty → proxy member definition | Documented; data-quality action raised |
| Article-master gaps (UNMAPPED sales) | % surfaced on Overview + ATR actions |
| Junk POS rows breaking numeric joins ('T001' incident) | Cast-safe `::varchar` article join |
| Late-arriving ETL changes history between runs | Windows are re-runnable; cache is per-window |
| COSTAMOUNT empty in Axapta | Store×article cost chain with coverage % shown |
| HRMS downtime | JSON snapshot fallback (status = Unknown) |

## 13. Future Enhancements

Store-level member analytics · cohort retention curves · RCS/WhatsApp execution hooks ·
Slack/mail delivery of the workbook on schedule · multi-user auth if shared beyond analytics ·
LOYALTYCARDID-based exact member tracking once POS populates it.

---

*End of BRD — Loyalty Card Member ATR Web App v1.0 (app v3.2, 22-Oct-2026)*
