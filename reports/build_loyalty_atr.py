"""Build the Loyalty Card Member ATR report (HTML) from Snowflake.

v2 (21-Oct-2026) — adds 4 modules on top of the original ATR break-up:
  1. EMPLOYEE FACTOR  — employees inside the member base (HRMS tblEmployee via
     pyodbc, fallback emp_by_mobile.json), card purchases by staff, employee-
     discount (ACX_EMPID) double-dip detection, audit CSV.
  2. TIER SEGMENTATION — Silver / Gold / Platinum by highest card SKU held
     (…018 ₹200 / …003 ₹500 / …004 ₹1000; slab rates 2% / 5% / 11%).
  3. MEMBER GM — line-level GM via GOLD.LOYALTY_ART_COST (store x article
     actual unit cost), fallback LOYALTY_ART_COST_ANY then VW_PRODUCT_MASTER
     ARTICLE_COST; member vs non-member GM%, GM by tier, est. slab-discount
     cost, card revenue, program economics.
  4. CRM CAMPAIGN PLAN — data-driven target lists (ranked, contactable):
     dormant winback, single-bill onboarding, card upgrade, E/V trade-up +
     employee suppression list; CSVs under targets/.

Connection: OAuth env vars (SNOWFLAKE_ACCOUNT/HOST/TOKEN_FILE_PATH) as before;
if absent, falls back to the local JWT helper (E:\\ANAND PRAKASH\\AUTOMATION\\sf_helper.py).
"""
import os, re, sys, csv, html, datetime
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))

# ---------------- card config (validated in membership_report_job.py — do not change) ----------
CARDS = ('000000001730000018', '000000001730000003', '000000001730000004')
TIER_OF_SKU = {CARDS[0]: 'Silver', CARDS[1]: 'Gold', CARDS[2]: 'Platinum'}
RANK_OF_SKU = {CARDS[0]: 1, CARDS[1]: 2, CARDS[2]: 3}
TIER_NAME = {1: 'Silver', 2: 'Gold', 3: 'Platinum'}
TIER_RATE = {1: 0.02, 2: 0.05, 3: 0.11}          # Silver 2% / Gold 5% / Platinum 11% slab
TIER_PRICE = {1: 200, 2: 500, 3: 1000}           # card purchase price
JUNK_MOBILES = {'6666666666', '7777777777', '8888888888', '9999999999', '9876543210'}
VALID_MOB = re.compile(r'^[6-9][0-9]{9}$')

CARD_SQL = ("select distinct card_item itemid from V2RETAIL.MIS_REPORTS.LOYALTY_BILLS "
            "union select '000000001730000018' union select '000000001730000003' "
            "union select '000000001730000004'")   # union static SKUs: LOYALTY_BILLS may be a lagging delta

# ---------------- connection: OAuth env (data-platform) else local JWT (sf_helper) ----------
def connect():
    if all(os.environ.get(k) for k in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_HOST", "SNOWFLAKE_TOKEN_FILE_PATH")):
        import snowflake.connector
        return snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"], host=os.environ["SNOWFLAKE_HOST"],
            authenticator="oauth", token=open(os.environ["SNOWFLAKE_TOKEN_FILE_PATH"]).read().strip(),
            warehouse="COMPUTE_WH", role="DATA_PLATFORM_ADMIN", database="V2RETAIL", schema="MIS_REPORTS")
    sys.path.insert(0, r'E:\ANAND PRAKASH\AUTOMATION')
    import sf_helper
    return sf_helper.connect()

conn = connect()
cur = conn.cursor()

def _live_cur():
    """Return the shared cursor, reopening it (and the session) if closed."""
    global cur
    try:
        cur.execute("select 1")
        return cur
    except Exception:
        try: cur.close()
        except Exception: pass
        global conn
        try: conn.close()
        except Exception: pass
        conn = connect()
        cur = conn.cursor()
        return cur

def q(sql):
    c = _live_cur()
    c.execute(sql)
    df = pd.DataFrame(c.fetchall(), columns=[x[0].lower() for x in c.description])
    for col in df.columns:
        if df[col].dtype == object:
            try: df[col] = pd.to_numeric(df[col])
            except (ValueError, TypeError): pass
    return df

def run(sql):
    _live_cur().execute(sql)

# ---------- base: all sale lines since first card sale, flagged member / non-member ----------
# v2: + store, price, line disc (for slab estimate), unit cost (GM) via GOLD cost tables.
run(f"""
create or replace temporary table tmp_atr_lines as
with card as ({CARD_SQL}),
members as (
  select b.custmobileno, min(to_timestamp(s.businessdate/1e9))::date card_since
  from V2RETAIL.CRM_BRONZE.AXAPTA_SALESTRANS s
  join card c on s.itemid = c.itemid
  join V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA b on b.transactionid = s.transactionid
  where nullif(trim(b.custmobileno),'') is not null
  group by 1),
start_dt as (select min(card_since) d from members)
select to_timestamp(s.businessdate/1e9)::date dt,
       b.custmobileno mobile, s.transactionid txn, b.store store,
       iff(m.custmobileno is not null and to_timestamp(s.businessdate/1e9)::date >= m.card_since, 1, 0) is_member,
       iff(s.netamount = 0, 0, -s.qty) qty, -s.netamount sale_v,  -- zero-value lines (bags/freebies) carry junk qty
       s.price price, abs(s.discamount) disc,
       coalesce(c.unit_cost, ca.unit_cost, pm.ac) unit_cost,
       coalesce(a.div,'UNMAPPED') division, coalesce(a.sub_div,'UNMAPPED') sub_division,
       coalesce(a.maj_cat,'UNMAPPED') maj_cat, coalesce(a.rng_seg,'NA') grid,
       coalesce(a.size_1,'NA') size, coalesce(a.color,'NA') colour
from V2RETAIL.CRM_BRONZE.AXAPTA_SALESTRANS s
join V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA b on b.transactionid = s.transactionid
left join members m on m.custmobileno = b.custmobileno
left join V2RETAIL.SILVER.V_VAR_ARTICLE_MASTER a on a.var_article = ltrim(s.itemid,'0')
left join V2RETAIL.GOLD.LOYALTY_ART_COST c
       on c.store = b.store and c.art = lpad(ltrim(s.itemid,'0'), 13, '0')
left join V2RETAIL.GOLD.LOYALTY_ART_COST_ANY ca
       on ca.art = lpad(ltrim(s.itemid,'0'), 13, '0')
left join (select article_number an, max(try_to_number(article_cost)) ac
           from V2RETAIL.SILVER.VW_PRODUCT_MASTER group by 1) pm
       on pm.an = lpad(ltrim(s.itemid,'0'), 13, '0')
where to_timestamp(s.businessdate/1e9)::date >= (select d from start_dt)
  and s.itemid not in (select itemid from card)
""")
print("tmp_atr_lines done", flush=True)

# ---------- members with tier (highest card SKU held), card revenue paid ----------
run(f"""
create or replace temporary table tmp_members as
with card as ({CARD_SQL}),
b as (select transactionid, max(custmobileno) mobile from V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA
      where nullif(trim(custmobileno),'') is not null group by 1),
cl as (
  select b.mobile, to_timestamp(s.businessdate/1e9)::date d, s.transactionid txn,
         iff(s.itemid = '000000001730000004', 3, iff(s.itemid = '000000001730000003', 2, 1)) rnk,
         -s.netamount v
  from V2RETAIL.CRM_BRONZE.AXAPTA_SALESTRANS s
  join card c on s.itemid = c.itemid
  join b on b.transactionid = s.transactionid)
select mobile, min(d) card_since, max(rnk) tier_rank, sum(v) card_paid,
       count(distinct txn) card_bills
from cl group by 1
""")
print("tmp_members done", flush=True)

period = q("select min(dt) mn, max(dt) mx from tmp_atr_lines").iloc[0]

kpi = q("""
select is_member, count(distinct mobile) customers, count(distinct txn) bills,
       sum(sale_v) sale_v, sum(qty) sale_q
from tmp_atr_lines group by 1""").set_index("is_member")

enrol = q(f"""
with card as ({CARD_SQL})
select date_trunc('week', d)::date wk, count(*) new_members from (
  select b.custmobileno, min(to_timestamp(s.businessdate/1e9))::date d
  from V2RETAIL.CRM_BRONZE.AXAPTA_SALESTRANS s join card c on s.itemid = c.itemid
  join V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA b on b.transactionid = s.transactionid
  where nullif(trim(b.custmobileno),'') is not null group by 1) group by 1 order by 1""")

enrol_tier = q("""
select date_trunc('week', card_since)::date wk, tier_rank rnk, count(*) n
from tmp_members group by 1, 2 order by 1""")

weekly = q("""
select date_trunc('week', dt)::date wk,
       sum(iff(is_member=1, sale_v, 0)) mem_v, sum(sale_v) tot_v
from tmp_atr_lines group by 1 order by 1""")

# ---------- per-member summary (post-enrolment behaviour) ----------
mem_sum = q("""
select l.mobile,
       count(distinct iff(l.is_member=1, l.txn, null)) mem_bills,
       sum(iff(l.is_member=1, l.sale_v, 0)) mem_sales,
       sum(iff(l.is_member=1, l.qty, 0)) mem_qty,
       sum(iff(l.is_member=1 and l.grid in ('E','V'), l.sale_v, 0)) mem_ev,
       max(l.dt) last_dt
from tmp_atr_lines l join tmp_members m on m.mobile = l.mobile
group by 1""")
mem_sum["mobile"] = mem_sum.mobile.astype(str)
print("mem_sum done", len(mem_sum), flush=True)

mem_store = q("""
select mobile, store, sum(sale_v) v from tmp_atr_lines
where is_member = 1 group by 1, 2""")
mem_store["mobile"] = mem_store.mobile.astype(str)

members = q("select * from tmp_members")
members["mobile"] = members.mobile.astype(str)
members["tier"] = members.tier_rank.map(TIER_NAME)
members = members.merge(mem_sum, on="mobile", how="left")
top = mem_store.loc[mem_store.groupby("mobile").v.idxmax()][["mobile", "store"]] if len(mem_store) else mem_store
members = members.merge(top, on="mobile", how="left")
members["mobile"] = members.mobile.astype(str)
members["last_dt"] = pd.to_datetime(members.last_dt)
for c in ("mem_bills", "mem_sales", "mem_qty", "mem_ev"):
    members[c] = members[c].fillna(0)          # card-only buyers have no goods lines
members = members[~members.mobile.isin(JUNK_MOBILES) & members.mobile.str.match(r'^[6-9][0-9]{9}$', na=False)]
print("members frame done", len(members), flush=True)

# ---------- GM ----------
gm_kpi = q("""
select is_member,
       count(distinct txn) bills, sum(sale_v) sale_v, sum(qty) sale_q,
       sum(iff(unit_cost is not null, sale_v - qty*unit_cost, 0)) gm,
       sum(iff(unit_cost is not null, sale_v, 0)) costed_sale
from tmp_atr_lines group by 1""").set_index("is_member")

gm_tier = q(f"""
select m.tier_rank rnk,
       count(distinct l.txn) bills, sum(l.sale_v) sale_v,
       sum(iff(l.unit_cost is not null, l.sale_v - l.qty*l.unit_cost, 0)) gm,
       sum(iff(l.unit_cost is not null, l.sale_v, 0)) costed_sale,
       sum(iff(abs(l.disc/nullif(l.price*abs(l.qty),0) -
             case m.tier_rank when 3 then 0.11 when 2 then 0.05 else 0.02 end) < 0.005,
             l.disc, 0)) slab_disc
from tmp_atr_lines l join tmp_members m on m.mobile = l.mobile
where l.is_member = 1 group by 1""")

card_rev = q(f"""
with card as ({CARD_SQL}),
b as (select transactionid, max(custmobileno) mobile from V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA
      where nullif(trim(custmobileno),'') is not null group by 1)
select iff(s.itemid = '000000001730000004', 'Platinum',
       iff(s.itemid = '000000001730000003', 'Gold', 'Silver')) tier,
       count(distinct s.transactionid) card_bills,
       sum(-s.netamount) card_rev, count(distinct b.mobile) buyers
from V2RETAIL.CRM_BRONZE.AXAPTA_SALESTRANS s
join card c on s.itemid = c.itemid
join b on b.transactionid = s.transactionid
group by 1 order by 1""")

gm_div = q("""
select division,
       sum(iff(is_member=1, sale_v, 0)) mem_v,
       sum(iff(is_member=1 and unit_cost is not null, sale_v - qty*unit_cost, 0)) mem_gm,
       sum(iff(is_member=1 and unit_cost is not null, sale_v, 0)) mem_costed,
       sum(iff(is_member=0, sale_v, 0)) non_v,
       sum(iff(is_member=0 and unit_cost is not null, sale_v - qty*unit_cost, 0)) non_gm,
       sum(iff(is_member=0 and unit_cost is not null, sale_v, 0)) non_costed
from tmp_atr_lines group by 1 having sum(is_member*sale_v) > 0 order by mem_v desc""")
print("GM queries done", flush=True)

# ---------- employee factor ----------
HRMS_CS = (r"DRIVER={ODBC Driver 18 for SQL Server};SERVER=192.168.151.28\hrms;DATABASE=HRMS;"
           r"UID=sa_hrms;PWD=CIHTY5pBmRRwjAw;Encrypt=Optional;TrustServerCertificate=Yes")

def load_employees():
    """{mobile10: dict(ecode,name,desig,loc)} — HRMS live first, JSON snapshot fallback."""
    try:
        import pyodbc
        cn = pyodbc.connect(HRMS_CS, timeout=15)
        df = pd.read_sql(
            "select e.Ecode ecode, e.[FULL NAME] nm, e.MOBILE mob, "
            "isnull(d.DesignationName,'') desig, isnull(l.LocationName,'') loc "
            "from tblEmployee e "
            "left join tblDesignation d on d.DesignationId = e.DesignationId "
            "left join tblLocation l on l.LocationId = e.LocationId "
            "where e.IsActive = 1", cn)
        cn.close()
        df["mob"] = df.mob.astype(str).str.strip()
        df = df[df.mob.str.fullmatch(r'[6-9][0-9]{9}')]
        out = {r.mob: dict(ecode=r.ecode, name=(r.nm or '').strip(), desig=r.desig, loc=r.loc)
               for r in df.itertuples()}
        return out, f"HRMS live tblEmployee IsActive=1 ({len(out)} with valid mobile)"
    except Exception:
        pass
    try:
        import json
        raw = json.load(open(os.path.join(r'E:\ANAND PRAKASH\AUTOMATION\MEMBERSHIP CARD',
                                          'emp_by_mobile.json'), encoding='utf-8'))
        out = {}
        for k, v in raw.items():
            if isinstance(v, list) and len(v) >= 3 and VALID_MOB.match(str(k)):
                out[str(k)] = dict(ecode=v[0], name=v[1], desig=v[2] if len(v) > 2 else '',
                                   loc=v[3] if len(v) > 3 else '')
        return out, "HRMS snapshot emp_by_mobile.json (14-Sep-2026)"
    except Exception:
        return {}, "unavailable"

EMP, emp_src = load_employees()
print("employees:", len(EMP), emp_src, flush=True)

empd = q(f"""
select to_timestamp(businessdate/1e9)::date d, transactionid, max(store) store,
       max(custmobileno) mobile, max(acx_empid) empid, max(acx_empdiscamount) empdisc
from V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA
where to_timestamp(businessdate/1e9)::date >= DATE '{period.mn}'
  and nullif(trim(acx_empid),'') is not null
group by 1, 2""")
empd["mobile"] = empd.mobile.astype(str)
empd["is_emp"] = empd.mobile.isin(EMP)
print("emp-disc bills done", len(empd), flush=True)

members["is_emp"] = members.mobile.isin(EMP)
emp_mem = members[members.is_emp].copy()
for c in ("ecode", "name", "desig", "loc"):
    emp_mem[c] = emp_mem.mobile.map(lambda m: EMP.get(m, {}).get(c, ""))
emp_dd = empd[empd.is_emp]                      # employee-discount bills whose payer mobile = an employee
emp_dd_mob = set(emp_dd.mobile) & set(emp_mem.mobile)

# ---------- mix tables (original ATR) ----------
def mix(dims, where="1=1"):
    """Member vs non-member sales by dims, with grid split and share index."""
    d = ", ".join(dims)
    return q(f"""
    with x as (
      select {d},
        sum(iff(is_member=1, sale_v, 0)) mem_v, sum(iff(is_member=1, qty, 0)) mem_q,
        sum(iff(is_member=0, sale_v, 0)) non_v,
        sum(iff(is_member=1 and grid='E',  sale_v, 0)) e_v,
        sum(iff(is_member=1 and grid='V',  sale_v, 0)) v_v,
        sum(iff(is_member=1 and grid='P',  sale_v, 0)) p_v,
        sum(iff(is_member=1 and grid='SP', sale_v, 0)) sp_v,
        count(distinct iff(is_member=1, txn, null)) mem_bills
      from tmp_atr_lines where {where} group by {d})
    select *, mem_v / nullif(sum(mem_v) over (), 0) mem_share,
              non_v / nullif(sum(non_v) over (), 0) non_share
    from x order by mem_v desc""")

div = mix(["division"])
subdiv = mix(["division", "sub_division"])
majcat = mix(["division", "maj_cat"])
grid = mix(["grid"])
grid_div = mix(["division", "grid"], "division in ('MENS','LADIES','KIDS')")
size = mix(["division", "size"], "division in ('MENS','LADIES','KIDS')")
colour = mix(["colour"])
colour_div = mix(["division", "colour"], "division in ('MENS','LADIES','KIDS')")

# member grid mix per tier (needed by the tier section; must run before close)
tgrid = q("""
select m.tier_rank rnk, l.grid, sum(l.sale_v) v
from tmp_atr_lines l join tmp_members m on m.mobile = l.mobile
where l.is_member = 1 and l.grid in ('E','V','P','SP') group by 1, 2""")
conn.close()
print("mix tables done", flush=True)

# ---------- helpers ----------
def inr(v):
    v = float(v or 0)
    if abs(v) >= 1e7: return f"₹{v/1e7:,.2f} Cr"
    if abs(v) >= 1e5: return f"₹{v/1e5:,.1f} L"
    return f"₹{v:,.0f}"

def idx(r):
    return 100 * r.mem_share / r.non_share if r.non_share and r.non_share > 0 else None

def idx_cell(i):
    if i is None or pd.isna(i): return "<td>–</td>"
    cls = "hi" if i >= 110 else "lo" if i <= 90 else ""
    return f'<td class="{cls}">{i:,.0f}</td>'

def bar(v, mx, color="#2563eb"):
    w = 0 if not mx else max(0, 100 * v / mx)
    return f'<div class="bar"><span style="width:{w:.1f}%;background:{color}"></span></div>'

GRID_COL = {"e_v": "#94a3b8", "v_v": "#38bdf8", "p_v": "#2563eb", "sp_v": "#7c3aed"}
def gridbar(r):
    t = sum(max(r[k], 0) for k in GRID_COL) or 1
    segs = "".join(f'<span style="width:{100*max(r[k],0)/t:.1f}%;background:{c}" title="{k[:-2].upper()} {100*r[k]/t:.0f}%"></span>' for k, c in GRID_COL.items())
    return f'<div class="stack">{segs}</div>'

def gpct(r, k):
    t = r.e_v + r.v_v + r.p_v + r.sp_v
    return f"{100*r[k]/t:.0f}%" if t else "–"

def table(df, label_cols, n=None, grid_cols=True):
    df = df.head(n) if n else df
    mx = df.mem_v.max()
    head = "".join(f"<th>{h}</th>" for h in label_cols) + "<th>Member sales</th><th></th><th>Share</th><th>Index vs non-member</th>"
    if grid_cols: head += "<th>E</th><th>V</th><th>P</th><th>SP</th><th>Grid mix</th>"
    rows = []
    for _, r in df.iterrows():
        cells = "".join(f"<td>{html.escape(str(r[c]))}</td>" for c in label_cols)
        cells += f"<td class='num'>{inr(r.mem_v)}</td><td>{bar(r.mem_v, mx)}</td><td class='num'>{100*r.mem_share:.1f}%</td>{idx_cell(idx(r))}"
        if grid_cols:
            cells += "".join(f"<td class='num'>{gpct(r,k)}</td>" for k in GRID_COL) + f"<td>{gridbar(r)}</td>"
        rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"

# ---------- KPIs ----------
m, n = kpi.loc[1], kpi.loc[0]
tot_v = m.sale_v + n.sale_v
abv_m, abv_n = m.sale_v / m.bills, n.sale_v / n.bills
ipb_m, ipb_n = m.sale_q / m.bills, n.sale_q / n.bills
asp_m, asp_n = m.sale_v / m.sale_q, n.sale_v / n.sale_q
freq_m, freq_n = m.bills / m.customers, n.bills / max(n.customers, 1)

gmp = gm_kpi.loc[1] if 1 in gm_kpi.index else None
gmn = gm_kpi.loc[0] if 0 in gm_kpi.index else None
mem_gmp = 100 * gmp.gm / gmp.costed_sale if gmp is not None and gmp.costed_sale else None
non_gmp = 100 * gmn.gm / gmn.costed_sale if gmn is not None and gmn.costed_sale else None
mem_cov = 100 * gmp.costed_sale / gmp.sale_v if gmp is not None and gmp.sale_v else 0

# ---------- tier frame ----------
tier_tbl = (members.groupby("tier")
            .agg(members=("mobile", "count"), card_paid=("card_paid", "sum"),
                 mem_bills=("mem_bills", "sum"), mem_sales=("mem_sales", "sum"))
            .reindex(["Silver", "Gold", "Platinum"]).fillna(0))
tier_tbl["mem_abv"] = tier_tbl.mem_sales / tier_tbl.mem_bills.replace(0, pd.NA)
tier_tbl["freq"] = tier_tbl.mem_bills / tier_tbl.members
gt = gm_tier.set_index("rnk") if len(gm_tier) else gm_tier
tier_tbl["gmp"] = [100 * gt.loc[r, "gm"] / gt.loc[r, "costed_sale"] if r in gt.index and gt.loc[r, "costed_sale"] else None for r in (1, 2, 3)]
tier_tbl["slab_disc"] = [gt.loc[r, "slab_disc"] if r in gt.index else 0 for r in (1, 2, 3)]
cr = card_rev.set_index("tier") if len(card_rev) else card_rev
tier_tbl["card_rev"] = [cr.loc[t, "card_rev"] if t in cr.index else 0 for t in tier_tbl.index]
tier_tbl["buyers"] = [cr.loc[t, "buyers"] if t in cr.index else 0 for t in tier_tbl.index]
tg = tgrid.pivot_table(index="rnk", columns="grid", values="v", aggfunc="sum").reindex([1, 2, 3]).fillna(0) if len(tgrid) else tgrid
for g in ("E", "V", "P", "SP"):
    tier_tbl[f"{g.lower()}_pct"] = [100 * tg.loc[r, g] / tg.loc[r].sum() if r in tg.index and tg.loc[r].sum() else None for r in (1, 2, 3)]

# ---------- campaign frames ----------
asof = pd.to_datetime(period.mx)
def contactable(df):
    return df[~df.mobile.isin(JUNK_MOBILES) & df.mobile.str.match(r'^[6-9][0-9]{9}$', na=False)]

winback = contactable(members[(members.mem_bills >= 2) & (members.last_dt <= asof - datetime.timedelta(days=30))]).copy()
winback["rec_days"] = (asof - winback.last_dt).dt.days
winback = winback.sort_values("mem_sales", ascending=False)
onboard = contactable(members[members.mem_bills <= 1]).copy()
onboard = onboard.sort_values(["card_paid"], ascending=False)
upg = contactable(members[((members.tier == "Silver") & (members.mem_sales >= 25000)) |
                          ((members.tier == "Gold") & (members.mem_sales >= 50000))]).copy()
upg = upg.sort_values("mem_sales", ascending=False)
med = members.loc[members.mem_sales > 0, "mem_sales"].median()
trade = contactable(members[(members.mem_sales >= max(10000, 2 * (med or 0))) &
                            (members.mem_ev / members.mem_sales.replace(0, pd.NA) >= 0.8)]).copy()
trade = trade.sort_values("mem_sales", ascending=False)

TGT = os.path.join(BASE, "targets")
os.makedirs(TGT, exist_ok=True)
stamp = asof.date().isoformat()
def save_csv(df, name, cols):
    p = os.path.join(TGT, f"{stamp}_{name}.csv")
    out = df[cols].head(100000).copy()
    if "last_dt" in out: out["last_dt"] = pd.to_datetime(out.last_dt).dt.date
    if "card_since" in out: out["card_since"] = pd.to_datetime(out.card_since).dt.date
    out.to_csv(p, index=False)
    return p, len(out)

COLS = ["mobile", "tier", "card_since", "card_paid", "mem_bills", "mem_sales", "last_dt", "store"]
f_win, n_win = save_csv(winback, "winback_dormant_members", COLS + ["rec_days"])
f_onb, n_onb = save_csv(onboard, "onboarding_single_bill", COLS)
f_upg, n_upg = save_csv(upg, "card_upgrade_high_spend", COLS)
f_trd, n_trd = save_csv(trade, "premium_tradeup_ev_heavy", COLS)
emp_sup = emp_mem[["mobile", "tier", "card_since", "card_paid", "mem_bills", "mem_sales", "ecode", "name", "desig", "loc"]].sort_values("mem_sales", ascending=False)
f_sup, n_sup = save_csv(emp_sup, "employee_suppression_list", emp_sup.columns.tolist())
print("target CSVs done", flush=True)

# ---------- ATR insights (data-driven) ----------
def ranked(df, label, minshare=0.005):
    d = df[(df.mem_share >= minshare) & (df[label] != "UNMAPPED") & (df[label] != "NA")].copy()
    d["ix"] = d.apply(idx, axis=1)
    return d.dropna(subset=["ix"]).sort_values("ix")

mc = ranked(majcat, "maj_cat", 0.005)
sz = ranked(size, "size", 0.003)
cl = ranked(colour, "colour", 0.005)
gr = grid[grid.grid.isin(["E", "V", "P", "SP"])].copy(); gr["ix"] = gr.apply(idx, axis=1)
gd = grid_div.copy(); gd["ix"] = gd.apply(idx, axis=1)

def lst(d, cols, k=5, top=True):
    d = d.iloc[::-1] if top else d
    return ", ".join(f"<b>{html.escape(' / '.join(str(r[c]) for c in cols))}</b> ({r.ix:.0f})" for _, r in d.head(k).iterrows())

prem_ix = gr[gr.grid.isin(["P", "SP"])].mem_v.sum() / gr.mem_v.sum() / (gr[gr.grid.isin(["P", "SP"])].non_v.sum() / gr.non_v.sum()) * 100
dd_amt = emp_dd.empdisc.sum()
def _pct(v, tot):
    return f"{100*v/tot:.1f}%" if tot else "–"
_tshare = tier_tbl.members.sum() or 1
_tsales = tier_tbl.mem_sales.sum() or 1
top_tier = tier_tbl.mem_sales.idxmax() if tier_tbl.mem_sales.sum() else "Silver"
_gm_txt = ", ".join(f"{t} {r.gmp:.1f}%" if r.gmp is not None and pd.notna(r.gmp) else f"{t} n/a" for t, r in tier_tbl.iterrows())
actions = [
    ("Basket", f"Members spend <b>{inr(abv_m)}</b> per bill vs <b>{inr(abv_n)}</b> for non-members ({100*(abv_m/abv_n-1):+.0f}%), with {ipb_m:.1f} vs {ipb_n:.1f} items per bill and ASP {inr(asp_m)} vs {inr(asp_n)}.",
     "Use card benefit thresholds just above current member ABV to stretch baskets; push add-on categories at billing."),
    ("Price grid", "Member grid index: " + ", ".join(f"<b>{r.grid}</b> {r.ix:.0f}" for _, r in gr.sort_values('grid', key=lambda s: s.map({'E': 0, 'V': 1, 'P': 2, 'SP': 3})).iterrows()) + f". P+SP combined index {prem_ix:.0f}.",
     "Keep P/SP depth and freshness in member-heavy stores; target E-heavy members with trade-up offers." if prem_ix >= 100 else "Members skew to E/V — lead member communication with value pricing; test trade-up offers into P."),
    ("Major categories – members over-index", lst(mc, ["division", "maj_cat"], 6, True),
     "Protect availability (size-sets, depth) in these categories; feature them in member communication and new-arrival alerts."),
    ("Major categories – members under-index", lst(mc, ["division", "maj_cat"], 6, False),
     "Investigate range/price fit for members; use targeted member offers to cross-sell these categories."),
    ("Sizes", "Over-index: " + lst(sz, ["division", "size"], 5, True) + ". Under-index: " + lst(sz, ["division", "size"], 5, False) + ".",
     "Re-weight size ratios in allocation for member-heavy stores towards over-indexed sizes; check broken size-sets."),
    ("Colours", "Over-index: " + lst(cl, ["colour"], 6, True) + ". Under-index: " + lst(cl, ["colour"], 6, False) + ".",
     "Feed member colour preferences into next-season buying and visual merchandising."),
    ("Tiers", f"<b>{top_tier}</b> holds the largest member sales share ({_pct(tier_tbl.loc[top_tier,'mem_sales'], _tsales)}); Platinum is {_pct(tier_tbl.loc['Platinum','mem_sales'], _tsales)}. "
              f"Member GM% by tier: {_gm_txt}.",
     "Pitch card upgrades to high-spend Silver/Gold members (₹25k/₹50k lists ready); protect Platinum entitlements that drive the 11% slab."),
    ("Employee factor", f"<b>{len(emp_mem):,}</b> employees hold membership cards ({100*len(emp_mem)/max(len(members),1):.1f}% of members), contributing {inr(emp_mem.mem_sales.sum())} "
              f"({100*emp_mem.mem_sales.sum()/max(members.mem_sales.sum(),1):.1f}% of member sales). "
              f"<b>{emp_dd.mobile.nunique():,}</b> employees appear on Axapta staff-discount bills ({int(empd.transactionid.nunique()):,} bills, {inr(dd_amt)}); "
              f"{len(emp_dd_mob):,} of them also hold a membership card (double-dip: staff discount + member slab).",
     "Audit the double-dip list (targets CSV); policy call on staff card purchases; suppress employee mobiles from member campaigns."),
    ("Member GM", f"Member GM <b>{f'{mem_gmp:.1f}%' if mem_gmp is not None else 'n/a'}</b> vs non-member <b>{f'{non_gmp:.1f}%' if non_gmp is not None else 'n/a'}</b> (costed lines only, {mem_cov:.0f}% value coverage); est. slab discount given {inr(tier_tbl.slab_disc.sum())} vs card revenue {inr(tier_tbl.card_rev.sum())}.",
     "If member GM% trails non-member, re-price the Platinum slab or steer members to higher-margin categories; track discount leakage monthly."),
    ("CRM campaigns", f"Target lists built: dormant winback <b>{n_win:,}</b>, single-bill onboarding <b>{n_onb:,}</b>, card upgrade <b>{n_upg:,}</b>, E/V trade-up <b>{n_trd:,}</b>, employee suppression <b>{n_sup:,}</b>.",
     "Run winback + onboarding via RCS/call-centre this week; upgrade pitch with next-festival offer; exclude the suppression list from all member comms."),
    ("Data", f"{100*div[div.division=='UNMAPPED'].mem_v.sum()/m.sale_v:.1f}% of member sales have no article-master match; bills carry no loyalty card ID.",
     "Populate LOYALTYCARDID at POS and close article-master gaps so member tracking is exact."),
]

# ---------- sections ----------
mxw = weekly.tot_v.max()
et = enrol_tier.pivot_table(index="wk", columns="rnk", values="n", aggfunc="sum").reindex(columns=[1, 2, 3]).fillna(0) if len(enrol_tier) else enrol_tier
week_rows = "".join(
    f"<tr><td>{r.wk}</td><td class='num'>{inr(r.mem_v)}</td><td>{bar(r.mem_v, weekly.mem_v.max())}</td>"
    f"<td class='num'>{inr(r.tot_v)}</td><td class='num'>{100*r.mem_v/r.tot_v:.1f}%</td>"
    f"<td class='num'>{int(enrol.set_index('wk').new_members.get(r.wk, 0)):,}</td>"
    f"<td class='num'>{int(et.loc[r.wk, 1]) if r.wk in et.index else 0:,}</td>"
    f"<td class='num'>{int(et.loc[r.wk, 2]) if r.wk in et.index else 0:,}</td>"
    f"<td class='num'>{int(et.loc[r.wk, 3]) if r.wk in et.index else 0:,}</td></tr>" for _, r in weekly.iterrows())

def per_div(df, col, n=12):
    out = []
    for d_ in ["MENS", "LADIES", "KIDS"]:
        sub = df[(df.division == d_) & (df[col] != "NA")]
        out.append(f"<h3>{d_}</h3>" + table(sub, [col], n, grid_cols=(col != "grid")))
    return "".join(out)

grid_tbl = "".join(
    f"<tr><td><b>{r.grid}</b></td><td>{ {'E':'Economy','V':'Value','P':'Premium','SP':'Super Premium'}.get(r.grid,'Not tagged')}</td>"
    f"<td class='num'>{inr(r.mem_v)}</td><td>{bar(r.mem_v, grid.mem_v.max(), '#7c3aed')}</td><td class='num'>{100*r.mem_share:.1f}%</td>"
    f"<td class='num'>{100*r.non_share:.1f}%</td>{idx_cell(idx(r))}</tr>"
    for _, r in grid.sort_values("grid", key=lambda s: s.map({"E": 0, "V": 1, "P": 2, "SP": 3}).fillna(9)).iterrows())

gd_rows = ""
for d_ in ["MENS", "LADIES", "KIDS"]:
    sub = gd[gd.division == d_]
    tm, tn = sub.mem_v.sum(), sub.non_v.sum()
    cells = ""
    for g in ["E", "V", "P", "SP"]:
        r = sub[sub.grid == g]
        if len(r):
            ms, ns = r.mem_v.iloc[0] / tm, r.non_v.iloc[0] / tn
            cells += f"<td class='num'>{100*ms:.0f}% <small>vs {100*ns:.0f}%</small></td>" + idx_cell(100*ms/ns if ns else None)
        else:
            cells += "<td>–</td><td>–</td>"
    gd_rows += f"<tr><td><b>{d_}</b></td>{cells}</tr>"

act_html = "".join(f"<tr><td><b>{a}</b></td><td>{o}</td><td>{x}</td><td></td><td></td></tr>" for a, o, x in actions)

# ---- new sections html ----
def _cell(v, fmt="{:.1f}%"):
    return f"<td class='num'>{fmt.format(v)}</td>" if v is not None and pd.notna(v) else "<td>–</td>"
RANK_OF_TIER = {"Silver": 1, "Gold": 2, "Platinum": 3}
tier_rows = ""
for t, r in tier_tbl.iterrows():
    rk = RANK_OF_TIER[t]
    row = (f"<td><b>{t}</b> <small>₹{TIER_PRICE[rk]:,} card · {TIER_RATE[rk]*100:.0f}% slab</small></td>"
           f"<td class='num'>{int(r.members):,}</td><td class='num'>{_pct(r.members, _tshare)}</td>"
           f"<td class='num'>{inr(r.mem_sales)}</td><td class='num'>{_pct(r.mem_sales, _tsales)}</td>"
           f"<td class='num'>{inr(r.card_rev)}</td><td class='num'>{r.freq:.2f}</td>"
           + _cell(r.mem_abv, "{:,.0f}")
           + _cell(r.gmp)
           + f"<td class='num'>{inr(r.slab_disc)}</td>")
    for g in ("E", "V", "P", "SP"):
        v = getattr(r, f"{g.lower()}_pct")
        row += _cell(v, "{:.0f}%")
    tier_rows += f"<tr>{row}</tr>"

def gm_div_row(r):
    return (f"<tr><td>{html.escape(str(r.division))}</td><td class='num'>{inr(r.mem_v)}</td>"
            + _cell(100 * r.mem_gm / r.mem_costed if r.mem_costed else None)
            + f"<td class='num'>{inr(r.non_v)}</td>"
            + _cell(100 * r.non_gm / r.non_costed if r.non_costed else None)
            + "</tr>")
gm_div_rows = "".join(gm_div_row(r) for _, r in gm_div.iterrows())

camp_rows = "".join(
    f"<tr><td><b>{nm}</b></td><td>{rule}</td><td class='num'>{sz:,}</td><td class='num'>{inr(val)}</td>"
    f"<td>{chan}</td><td><code>{html.escape(os.path.basename(p))}</code></td></tr>"
    for nm, rule, sz, val, chan, p in [
        ("Dormant winback", "2+ member bills, no purchase in 30+ days", n_win, winback.mem_sales.sum(),
         "RCS + call-centre, tier-tagged offer", f_win),
        ("Onboarding nudge", "Bought a card, never returned (1 bill)", n_onb, onboard.mem_sales.sum(),
         "Welcome journey SMS/RC with ₹-off next-visit coupon", f_onb),
        ("Card upgrade", "Silver ≥ ₹25k / Gold ≥ ₹50k member-period spend", n_upg, upg.mem_sales.sum(),
         "Upgrade pitch: next-tier slab benefit (5%/11%) vs ₹500/₹1,000 card fee", f_upg),
        ("Premium trade-up", "≥80% of member spend in E/V grids", n_trd, trade.mem_sales.sum(),
         "P/SP new-arrival alerts + curated premium edits", f_trd),
        ("Employee suppression", "Employee mobiles holding cards — exclude from campaigns", n_sup, emp_mem.mem_sales.sum(),
         "Suppression list for all member comms; audit double-dip", f_sup)])

emp_top = emp_mem.sort_values("mem_sales", ascending=False).head(20)
dd_bills = emp_dd.groupby("mobile").transactionid.nunique().to_dict()
dd_amt_m = emp_dd.groupby("mobile").empdisc.sum().to_dict()
emp_rows = "".join(
    f"<tr><td>{html.escape(str(r.ecode))}</td><td>{html.escape(str(r.name))}</td>"
    f"<td>{html.escape(str(r.desig))}</td><td>{html.escape(str(r.loc))}</td>"
    f"<td>{r.mobile}</td><td><b>{r.tier}</b></td><td class='num'>{int(r.card_bills)}</td>"
    f"<td class='num'>{inr(r.card_paid)}</td><td class='num'>{int(r.mem_bills)}</td>"
    f"<td class='num'>{inr(r.mem_sales)}</td>"
    f"<td class='num'>{int(dd_bills.get(r.mobile, 0))}</td>"
    f"<td class='num'>{inr(dd_amt_m.get(r.mobile, 0))}</td></tr>"
    for _, r in emp_top.iterrows())

now = datetime.datetime.now().strftime("%d %b %Y %H:%M")
doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>Loyalty Card Member ATR</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#f8fafc;color:#0f172a}}
.wrap{{max-width:1200px;margin:0 auto;padding:28px}}
h1{{margin:0 0 4px}} h2{{margin-top:36px;border-bottom:2px solid #e2e8f0;padding-bottom:6px}} h3{{margin:18px 0 6px;color:#334155}}
.sub{{color:#64748b}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:18px}}
.kpi{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px}} .kpi .l{{color:#64748b;font-size:12px;text-transform:uppercase}}
.kpi .v{{font-size:22px;font-weight:700;margin-top:4px}} .kpi .c{{font-size:12px;color:#64748b}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px;margin-top:8px}}
th,td{{padding:6px 8px;border-bottom:1px solid #eef2f7;text-align:left;vertical-align:top}} th{{background:#f1f5f9;font-weight:600}}
td.num{{text-align:right;white-space:nowrap}} td.hi{{color:#15803d;font-weight:700;text-align:right}} td.lo{{color:#b91c1c;font-weight:700;text-align:right}}
.bar{{background:#eef2f7;height:10px;border-radius:5px;min-width:120px}} .bar span{{display:block;height:10px;border-radius:5px}}
.stack{{display:flex;height:12px;min-width:140px;border-radius:6px;overflow:hidden}} .stack span{{display:block;height:12px}}
.legend span{{display:inline-block;width:10px;height:10px;margin:0 4px 0 12px;border-radius:2px}}
.note{{background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:12px;font-size:13px}}
code{{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:12px}}
</style></head><body><div class="wrap">
<h1>Loyalty Card Member – Action Taken Report (ATR)</h1>
<div class="sub">Member sales break-up: Division › Sub Division › Major Category › Price Grid (E/V/P/SP) › Size › Colour<br>
Period: {period.mn} to {period.mx} &nbsp;|&nbsp; Generated {now}</div>

<div class="grid">
<div class="kpi"><div class="l">Card members</div><div class="v">{int(m.customers):,}</div><div class="c">buying since card purchase</div></div>
<div class="kpi"><div class="l">Member sales</div><div class="v">{inr(m.sale_v)}</div><div class="c">{100*m.sale_v/tot_v:.1f}% of total sales</div></div>
<div class="kpi"><div class="l">Member bills</div><div class="v">{int(m.bills):,}</div><div class="c">{freq_m:.2f} bills / member</div></div>
<div class="kpi"><div class="l">Avg bill value</div><div class="v">{inr(abv_m)}</div><div class="c">non-member {inr(abv_n)} ({100*(abv_m/abv_n-1):+.0f}%)</div></div>
<div class="kpi"><div class="l">Items per bill</div><div class="v">{ipb_m:.2f}</div><div class="c">non-member {ipb_n:.2f}</div></div>
<div class="kpi"><div class="l">Avg selling price</div><div class="v">{inr(asp_m)}</div><div class="c">non-member {inr(asp_n)}</div></div>
</div>
<div class="grid">
<div class="kpi"><div class="l">Member GM%</div><div class="v">{f"{mem_gmp:.1f}%" if mem_gmp is not None else "–"}</div><div class="c">non-member {f"{non_gmp:.1f}%" if non_gmp is not None else "–"} · costed lines</div></div>
<div class="kpi"><div class="l">GM cost coverage</div><div class="v">{mem_cov:.0f}%</div><div class="c">of member sales value costed</div></div>
<div class="kpi"><div class="l">Employee members</div><div class="v">{len(emp_mem):,}</div><div class="c">{100*len(emp_mem)/max(len(members),1):.1f}% of members · {len(emp_dd_mob)} double-dip</div></div>
<div class="kpi"><div class="l">Campaign targets</div><div class="v">{n_win+n_onb+n_upg+n_trd:,}</div><div class="c">winback+onboard+upgrade+trade-up</div></div>
</div>

<h2>1. Action Taken Report</h2>
<p class="sub">Index = member share ÷ non-member share × 100 (same period). &gt;110 members over-index (green), &lt;90 under-index (red).</p>
<table><thead><tr><th>Area</th><th>Observation</th><th>Recommended action</th><th>Owner</th><th>Status</th></tr></thead><tbody>{act_html}</tbody></table>

<h2>2. Weekly trend</h2>
<table><thead><tr><th>Week starting</th><th>Member sales</th><th></th><th>Total sales</th><th>Member share</th><th>New members</th><th>Silver</th><th>Gold</th><th>Platinum</th></tr></thead><tbody>{week_rows}</tbody></table>

<h2>3. Price grid (E / V / P / SP)</h2>
<p class="sub">E = Economy, V = Value, P = Premium, SP = Super Premium (inferred from MRP ladder: median MRP ₹250 / ₹400 / ₹550 / ₹700 — to be confirmed).</p>
<table><thead><tr><th>Grid</th><th>Meaning</th><th>Member sales</th><th></th><th>Member mix</th><th>Non-member mix</th><th>Index</th></tr></thead><tbody>{grid_tbl}</tbody></table>
<h3>Grid mix within division (member vs non-member)</h3>
<table><thead><tr><th>Division</th><th>E</th><th>Idx</th><th>V</th><th>Idx</th><th>P</th><th>Idx</th><th>SP</th><th>Idx</th></tr></thead><tbody>{gd_rows}</tbody></table>

<h2>4. Division</h2>
<div class="legend sub">Grid mix: <span style="background:#94a3b8"></span>E<span style="background:#38bdf8"></span>V<span style="background:#2563eb"></span>P<span style="background:#7c3aed"></span>SP</div>
{table(div, ["division"])}

<h2>5. Sub Division</h2>
{table(subdiv, ["division", "sub_division"], 30)}

<h2>6. Major Category (Top 30)</h2>
{table(majcat, ["division", "maj_cat"], 30)}

<h2>7. Size (Top 12 per division)</h2>
{per_div(size, "size")}

<h2>8. Colour</h2>
<h3>Overall – Top 25</h3>
{table(colour[colour.colour != 'NA'], ["colour"], 25)}
{per_div(colour_div, "colour", 10)}

<h2>9. Tier segmentation (Silver / Gold / Platinum)</h2>
<p class="sub">Tier = highest card SKU held: Silver …018 (₹200, 2% slab) · Gold …003 (₹500, 5%) · Platinum …004 (₹1,000, 11%). GM% on costed lines; slab discount is the estimated member-slab leakage detected at line level.</p>
<table><thead><tr><th>Tier</th><th>Members</th><th>Share</th><th>Member sales</th><th>Sales share</th><th>Card revenue</th><th>Bills/member</th><th>ABV</th><th>GM%</th><th>Est. slab disc.</th><th>E</th><th>V</th><th>P</th><th>SP</th></tr></thead><tbody>{tier_rows}</tbody></table>
<div class="note">Card upgrade runway: {len(upg):,} high-spend Silver/Gold members qualify for the next-tier pitch — see section 12.</div>

<h2>10. Employee factor</h2>
<p class="sub">Source: {html.escape(emp_src)}. "Double-dip" = a bill with an Axapta employee discount (ACX_EMPID) whose payer mobile also holds a membership card — staff benefit + member slab on the same wallet.</p>
<div class="grid">
<div class="kpi"><div class="l">Employees holding cards</div><div class="v">{len(emp_mem):,}</div><div class="c">of {len(members):,} members ({100*len(emp_mem)/max(len(members),1):.1f}%)</div></div>
<div class="kpi"><div class="l">Employee member sales</div><div class="v">{inr(emp_mem.mem_sales.sum())}</div><div class="c">{100*emp_mem.mem_sales.sum()/max(members.mem_sales.sum(),1):.1f}% of member sales</div></div>
<div class="kpi"><div class="l">Employee card spend</div><div class="v">{inr(emp_mem.card_paid.sum())}</div><div class="c">{int(emp_mem.card_bills.sum()):,} card purchases</div></div>
<div class="kpi"><div class="l">Employee-disc. bills (period)</div><div class="v">{int(empd.transactionid.nunique()):,}</div><div class="c">{inr(dd_amt)} staff discount · {len(emp_dd_mob)} mobiles double-dip</div></div>
</div>
<h3>Top 20 employee members by member-period spend</h3>
<table><thead><tr><th>E-code</th><th>Name</th><th>Designation</th><th>Location</th><th>Mobile</th><th>Tier</th><th>Card bills</th><th>Card paid</th><th>Member bills</th><th>Member sales</th><th>DD bills</th><th>DD amount</th></tr></thead><tbody>{emp_rows}</tbody></table>

<h2>11. Member GM &amp; programme economics</h2>
<p class="sub">GM = member-line sales − qty × unit cost (GOLD.LOYALTY_ART_COST store×article actuals, fallback LOYALTY_ART_COST_ANY / VW_PRODUCT_MASTER). Costed lines only; ex-tax cost vs MRP-inclusive sale reads rich vs books.</p>
<table><thead><tr><th>Division</th><th>Member sales</th><th>Member GM%</th><th>Non-member sales</th><th>Non-member GM%</th></tr></thead><tbody>{gm_div_rows}</tbody></table>
<div class="note"><b>Programme view (period):</b> card revenue {inr(tier_tbl.card_rev.sum())} · est. member slab discount given {inr(tier_tbl.slab_disc.sum())} ·
member sales {inr(m.sale_v)} at {mem_gmp:.1f}% GM ({inr((gmp.gm if gmp is not None else 0))} gross margin) vs non-member {non_gmp:.1f}%. Net programme margin effect = incremental GM of member purchases − slab discount + card revenue.</div>

<h2>12. CRM campaign plan (target lists)</h2>
<p class="sub">Ranked, contactable lists exported as CSV under <code>targets/</code>. All lists exclude junk/repeat-digit POS mobiles; employees are suppressed via their own list.</p>
<table><thead><tr><th>Campaign</th><th>Rule</th><th>Targets</th><th>Member-period sales</th><th>Channel / offer</th><th>File</th></tr></thead><tbody>{camp_rows}</tbody></table>

<h2>13. Method &amp; caveats</h2>
<div class="note">
<b>Member definition:</b> a customer mobile on any bill containing a loyalty card article ({', '.join(CARDS)}); member sales counted from first card purchase date. POS bills carry no loyalty card ID (LOYALTYCARDID empty), so this is the best available proxy.<br>
<b>Tier:</b> highest card SKU held; Silver …018 ₹200/2% · Gold …003 ₹500/5% · Platinum …004 ₹1,000/11%. Enrolment week = first card purchase.<br>
<b>Non-member:</b> all other bills in the same period (incl. walk-ins without mobile, and members' purchases before enrolment).<br>
<b>Sales:</b> Axapta POS lines (CRM_BRONZE.AXAPTA_SALESTRANS), sign-flipped, net of returns, card articles excluded; zero-value lines (carry bags, freebies) excluded from quantity.<br>
<b>GM:</b> unit cost from GOLD.LOYALTY_ART_COST (store × article actuals), fallback LOYALTY_ART_COST_ANY then VW_PRODUCT_MASTER.ARTICLE_COST; uncosted articles excluded from GM maths (coverage shown). Costs are ex-tax landed, sales MRP-inclusive → GM% reads rich vs books. Slab-discount estimate = |line disc| where |disc ÷ (price×qty) − tier rate| &lt; 0.5pp.<br>
<b>Employee factor:</b> active employees from HRMS tblEmployee (mobile-normalised); Axapta employee-discount mechanism = ACX_EMPID / ACX_EMPDISCAMOUNT on the bill.<br>
<b>Campaign lists:</b> CSVs in targets/ ranked by member-period spend; 30-day dormancy; thresholds ₹25k/₹50k for upgrades. Suppression list = employee mobiles.<br>
<b>Owner / Status</b> columns in the ATR table are left blank for the business to fill in.
</div>
</div></body></html>"""

out = os.path.join(BASE, "loyalty_member_atr.html")
open(out, "w").write(doc)
print("written", out, len(doc))
print(f"campaign CSVs: winback={n_win:,} onboard={n_onb:,} upgrade={n_upg:,} tradeup={n_trd:,} employee_supp={n_sup:,}")
