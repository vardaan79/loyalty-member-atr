"""Loyalty ATR data engine — date-window parameterized rebuild of reports/build_loyalty_atr.py.

build(frm, to, progress) -> results dict (DataFrames + plain-number KPIs), pickled per
window in app/cache/ so the dashboard reloads any past window instantly.

Windowing rules (v3):
  - tmp_atr_lines (all sale-line analytics) is filtered to [frm, to].
  - Member identity (card_since, tier = highest card SKU held) stays LIFETIME.
  - Enrolment trend + card revenue are windowed; per-member behaviour (bills, sales,
    GM) is in-window; campaign dormancy anchors on the window end date.
  - frm=None means all-time (bounded by first card sale, as the original report).

Connection: OAuth env vars (SNOWFLAKE_ACCOUNT/HOST/TOKEN_FILE_PATH) else the local
JWT helper E:\\ANAND PRAKASH\\AUTOMATION\\sf_helper.py — identical to the batch builder.
"""
import os, re, sys, datetime
import pandas as pd

APP = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(APP, "cache")
TGT = os.path.join(os.path.dirname(APP), "reports", "targets")
os.makedirs(CACHE, exist_ok=True)
os.makedirs(TGT, exist_ok=True)

# ---------------- card config (validated in membership_report_job.py — do not change) ----------
CARDS = ('000000001730000018', '000000001730000003', '000000001730000004')
TIER_OF_SKU = {CARDS[0]: 'Silver', CARDS[1]: 'Gold', CARDS[2]: 'Platinum'}
TIER_NAME = {1: 'Silver', 2: 'Gold', 3: 'Platinum'}
TIER_RATE = {1: 0.02, 2: 0.05, 3: 0.11}
TIER_PRICE = {1: 200, 2: 500, 3: 1000}
JUNK_MOBILES = {'6666666666', '7777777777', '8888888888', '9999999999', '9876543210'}
VALID_MOB = re.compile(r'^[6-9][0-9]{9}$')

CARD_SQL = ("select distinct card_item itemid from V2RETAIL.MIS_REPORTS.LOYALTY_BILLS "
            "union select '000000001730000018' union select '000000001730000003' "
            "union select '000000001730000004'")   # union static SKUs: LOYALTY_BILLS may lag

# HRMS credentials come from environment variables (HRMS_SERVER / HRMS_UID / HRMS_PWD)
HRMS_CS = ("DRIVER={ODBC Driver 18 for SQL Server};SERVER=" + os.environ.get("HRMS_SERVER", "") +
           ";DATABASE=HRMS;UID=" + os.environ.get("HRMS_UID", "") +
           ";PWD=" + os.environ.get("HRMS_PWD", "") + ";Encrypt=Optional;TrustServerCertificate=Yes")


# ---------------------------------------------------------------- connection ----------
def _connect():
    if all(os.environ.get(k) for k in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_HOST", "SNOWFLAKE_TOKEN_FILE_PATH")):
        import snowflake.connector
        return snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"], host=os.environ["SNOWFLAKE_HOST"],
            authenticator="oauth", token=open(os.environ["SNOWFLAKE_TOKEN_FILE_PATH"]).read().strip(),
            warehouse="COMPUTE_WH", role="DATA_PLATFORM_ADMIN", database="V2RETAIL", schema="MIS_REPORTS")
    sys.path.insert(0, r'E:\ANAND PRAKASH\AUTOMATION')
    import sf_helper
    return sf_helper.connect()


def _num(df):
    """Coerce string-looking columns to numeric (pandas 3 str-dtype safe)."""
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            try:
                df[col] = pd.to_numeric(df[col])
            except (ValueError, TypeError):
                pass
    return df


def inr(v):
    """₹ formatting shared by insights text: Cr / L / absolute."""
    v = float(v or 0)
    if abs(v) >= 1e7: return f"₹{v/1e7:,.2f} Cr"
    if abs(v) >= 1e5: return f"₹{v/1e5:,.1f} L"
    return f"₹{v:,.0f}"


# ---------------------------------------------------------------- cache ----------
def cache_path(frm, to):
    return os.path.join(CACHE, f"atr_{frm or 'ALL'}_{to}.pkl")


def cached_windows():
    out = []
    for f in sorted(os.listdir(CACHE), reverse=True):
        if f.startswith("atr_") and f.endswith(".pkl"):
            try:
                r = pd.read_pickle(os.path.join(CACHE, f))
                out.append(dict(frm=r["frm"] or "All-time", to=r["to"],
                                generated=r["generated"], duration_s=round(r["duration_s"], 1),
                                job_id=f[4:-4]))
            except Exception:
                pass
    return out


def load_cached(job_id):
    return pd.read_pickle(os.path.join(CACHE, f"atr_{job_id}.pkl"))


# ---------------------------------------------------------------- employees ----------
def load_employees(progress=lambda *a: None):
    """{mobile10: dict(ecode,name,desig,loc,act)} — HRMS live first, JSON snapshot fallback.
    act: True active / False inactive / None unknown (snapshot has no status)."""
    try:
        import pyodbc
        cn = pyodbc.connect(HRMS_CS, timeout=15)
        df = pd.read_sql(
            "select e.Ecode ecode, e.[FULL NAME] nm, e.MOBILE mob, "
            "isnull(d.DesignationName,'') desig, isnull(l.LocationName,'') loc, e.IsActive act "
            "from tblEmployee e "
            "left join tblDesignation d on d.DesignationId = e.DesignationId "
            "left join tblLocation l on l.LocationId = e.LocationId", cn)
        cn.close()
        df["mob"] = df.mob.astype(str).str.strip()
        df = df[df.mob.str.fullmatch(r'[6-9][0-9]{9}')]
        out = {r.mob: dict(ecode=r.ecode, name=(r.nm or '').strip(), desig=r.desig, loc=r.loc,
                           act=bool(r.act))
               for r in df.itertuples()}
        n_act = sum(1 for v in out.values() if v["act"])
        return out, f"HRMS live tblEmployee all statuses ({n_act:,} active / {len(out) - n_act:,} inactive with valid mobile)"
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
                                   loc=v[3] if len(v) > 3 else '', act=None)
        return out, "HRMS snapshot emp_by_mobile.json (14-Sep-2026; status unknown)"
    except Exception:
        return {}, "unavailable"


# ---------------------------------------------------------------- main build ----------
def build(frm, to, progress=lambda step, pct=0: None):
    """frm: 'YYYY-MM-DD' or None (all-time) | to: 'YYYY-MM-DD'. Returns results dict."""
    t0 = datetime.datetime.now()
    frm_lit = f"and to_timestamp(s.businessdate/1e9)::date >= DATE '{frm}' " if frm else ""
    frm_lit_b = f"and to_timestamp(businessdate/1e9)::date >= DATE '{frm}' " if frm else ""
    win = f"{frm or 'first card sale'} → {to}"
    progress("Connecting to Snowflake…", 2)
    conn = _connect()
    cur = conn.cursor()

    def q(sql):
        nonlocal cur, conn
        try:
            cur.execute("select 1")
        except Exception:
            try: cur.close()
            except Exception: pass
            try: conn.close()
            except Exception: pass
            conn = _connect()
            cur = conn.cursor()
        cur.execute(sql)
        return _num(pd.DataFrame(cur.fetchall(), columns=[x[0].lower() for x in cur.description]))

    # ---------- base sale lines in window ----------
    progress("Loading sale lines (Axapta POS)"
             + ("" if frm else " — all-time scan, this is the long step") + "…", 8)
    cur.execute(f"""
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
       iff(s.netamount = 0, 0, -s.qty) qty, -s.netamount sale_v,
       s.price price, abs(s.discamount) disc,
       coalesce(c.unit_cost, ca.unit_cost, pm.ac) unit_cost,
       coalesce(a.div,'UNMAPPED') division, coalesce(a.sub_div,'UNMAPPED') sub_division,
       coalesce(a.maj_cat,'UNMAPPED') maj_cat, coalesce(a.rng_seg,'NA') grid,
       coalesce(a.size_1,'NA') size, coalesce(a.color,'NA') colour
from V2RETAIL.CRM_BRONZE.AXAPTA_SALESTRANS s
join V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA b on b.transactionid = s.transactionid
left join members m on m.custmobileno = b.custmobileno
left join V2RETAIL.SILVER.V_VAR_ARTICLE_MASTER a
       on a.var_article::varchar = ltrim(s.itemid,'0')   -- ::varchar: var_article is NUMBER; POS junk rows (e.g. 'T001') would break the numeric cast
left join V2RETAIL.GOLD.LOYALTY_ART_COST c
       on c.store = b.store and c.art = lpad(ltrim(s.itemid,'0'), 13, '0')
left join V2RETAIL.GOLD.LOYALTY_ART_COST_ANY ca
       on ca.art = lpad(ltrim(s.itemid,'0'), 13, '0')
left join (select article_number an, max(try_to_number(article_cost)) ac
           from V2RETAIL.SILVER.VW_PRODUCT_MASTER group by 1) pm
       on pm.an = lpad(ltrim(s.itemid,'0'), 13, '0')
where to_timestamp(s.businessdate/1e9)::date <= DATE '{to}'
  {frm_lit}
  and s.itemid not in (select itemid from card)
""")
    progress("Member registry + card revenue…", 26)
    cur.execute(f"""
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

    enrol = q(f"""
with card as ({CARD_SQL}),
first_card as (
  select b.custmobileno, min(to_timestamp(s.businessdate/1e9))::date d
  from V2RETAIL.CRM_BRONZE.AXAPTA_SALESTRANS s join card c on s.itemid = c.itemid
  join V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA b on b.transactionid = s.transactionid
  where nullif(trim(b.custmobileno),'') is not null
    and to_timestamp(s.businessdate/1e9)::date <= DATE '{to}'
  group by 1)
select date_trunc('week', d)::date wk, count(*) new_members
from first_card
where d >= coalesce(try_to_date('{frm}'), d)
group by 1 order by 1""")

    enrol_tier = q(f"""
select date_trunc('week', card_since)::date wk, tier_rank rnk, count(*) n
from tmp_members where card_since <= DATE '{to}' {'and card_since >= DATE ' + chr(39) + frm + chr(39) if frm else ''}
group by 1, 2 order by 1""")

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
where to_timestamp(s.businessdate/1e9)::date <= DATE '{to}' {frm_lit_b}
group by 1 order by 1""")

    period = q("select min(dt) mn, max(dt) mx from tmp_atr_lines").iloc[0]

    # ---------- KPI + per-member behaviour (in window) ----------
    progress("KPIs + member behaviour…", 42)
    kpi = q("""
select is_member, count(distinct mobile) customers, count(distinct txn) bills,
       sum(sale_v) sale_v, sum(qty) sale_q
from tmp_atr_lines group by 1""").set_index("is_member")

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
        members[c] = members[c].fillna(0)
    members = members[~members.mobile.isin(JUNK_MOBILES) & members.mobile.str.match(r'^[6-9][0-9]{9}$', na=False)]

    # ---------- GM ----------
    progress("Gross margin (cost joins)…", 55)
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

    gm_div = q("""
select division,
       sum(iff(is_member=1, sale_v, 0)) mem_v,
       sum(iff(is_member=1 and unit_cost is not null, sale_v - qty*unit_cost, 0)) mem_gm,
       sum(iff(is_member=1 and unit_cost is not null, sale_v, 0)) mem_costed,
       sum(iff(is_member=0, sale_v, 0)) non_v,
       sum(iff(is_member=0 and unit_cost is not null, sale_v - qty*unit_cost, 0)) non_gm,
       sum(iff(is_member=0 and unit_cost is not null, sale_v, 0)) non_costed
from tmp_atr_lines group by 1 having sum(is_member*sale_v) > 0 order by mem_v desc""")

    # member GM% by division x tier (Silver/Gold/Platinum)
    gdt = q("""
select l.division, m.tier_rank rnk, sum(l.sale_v) sale_v,
       sum(iff(l.unit_cost is not null, l.sale_v - l.qty*l.unit_cost, 0)) gm,
       sum(iff(l.unit_cost is not null, l.sale_v, 0)) costed_sale
from tmp_atr_lines l join tmp_members m on m.mobile = l.mobile
where l.is_member = 1 group by 1, 2""")
    if len(gdt):
        gdt["gmp"] = 100 * gdt.gm / gdt.costed_sale.replace(0, pd.NA)
        piv_g = gdt.pivot_table(index="division", columns="rnk", values="gmp", aggfunc="first")
        piv_v = gdt.pivot_table(index="division", columns="rnk", values="sale_v", aggfunc="sum")
        idx = sorted(set(piv_g.index) | set(piv_v.index))
        def _g(d, rk):
            if rk in piv_g.columns and d in piv_g.index:
                v = piv_g.loc[d, rk]
                return float(v) if pd.notna(v) else None
            return None
        def _v(d, rk):
            if rk in piv_v.columns and d in piv_v.index:
                v = piv_v.loc[d, rk]
                return float(v) if pd.notna(v) else 0.0
            return 0.0
        gm_div_tier = pd.DataFrame([dict(division=d, s_gmp=_g(d, 1), g_gmp=_g(d, 2), p_gmp=_g(d, 3),
                                         s_v=_v(d, 1), g_v=_v(d, 2), p_v=_v(d, 3)) for d in idx])
    else:
        gm_div_tier = pd.DataFrame(columns=["division", "s_gmp", "g_gmp", "p_gmp", "s_v", "g_v", "p_v"])

    tgrid = q("""
select m.tier_rank rnk, l.grid, sum(l.sale_v) v
from tmp_atr_lines l join tmp_members m on m.mobile = l.mobile
where l.is_member = 1 and l.grid in ('E','V','P','SP') group by 1, 2""")

    # ---------- employee factor ----------
    progress("Employee factor (HRMS join)…", 66)
    EMP, emp_src = load_employees(progress)
    empd = q(f"""
select to_timestamp(businessdate/1e9)::date d, transactionid, max(store) store,
       max(custmobileno) mobile, max(acx_empid) empid, max(acx_empdiscamount) empdisc
from V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA
where to_timestamp(businessdate/1e9)::date <= DATE '{to}' {frm_lit_b}
  and nullif(trim(acx_empid),'') is not null
group by 1, 2""")
    empd["mobile"] = empd.mobile.astype(str)
    empd["is_emp"] = empd.mobile.isin(EMP)

    members["is_emp"] = members.mobile.isin(EMP)
    emp_mem = members[members.is_emp].copy()
    for c in ("ecode", "name", "desig", "loc"):
        emp_mem[c] = emp_mem.mobile.map(lambda m: EMP.get(m, {}).get(c, ""))
    emp_mem["status"] = emp_mem.mobile.map(
        lambda m: {True: "Active", False: "Inactive", None: "Unknown"}.get(EMP.get(m, {}).get("act")))
    emp_mem["status"] = emp_mem["status"].fillna("Unknown")
    emp_dd = empd[empd.is_emp]
    emp_dd_mob = set(emp_dd.mobile) & set(emp_mem.mobile)

    # ---------- mix tables ----------
    progress("Mix tables (division / category / grid / size / colour)…", 78)
    def mix(dims, where="1=1"):
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
    weekly = q("""
select date_trunc('week', dt)::date wk,
       sum(iff(is_member=1, sale_v, 0)) mem_v, sum(sale_v) tot_v
from tmp_atr_lines group by 1 order by 1""")

    # ---------- customer BEFORE card-day / card-day / AFTER (per member, all history) ----------
    progress("Customer before/after card-day pass…", 72)
    cur.execute(f"""
create or replace temporary table tmp_cust_befaft as
with card as ({CARD_SQL}),
bl as (
  select to_timestamp(s.businessdate/1e9)::date d, b.custmobileno mobile, s.transactionid txn,
         sum(iff(s.itemid not in (select itemid from card), -s.netamount, 0)) v,
         sum(iff(s.itemid not in (select itemid from card), abs(s.discamount), 0)) disc
  from V2RETAIL.CRM_BRONZE.AXAPTA_SALESTRANS s
  join V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA b on b.transactionid = s.transactionid
  join tmp_members m on m.mobile = b.custmobileno
  where to_timestamp(s.businessdate/1e9)::date <= DATE '{to}'
    and nullif(trim(b.custmobileno),'') is not null
  group by 1, 2, 3)
select bl.mobile,
       count(distinct iff(d < card_since, d, null)) bef_visits_all,
       max(iff(d < card_since, d, null)) bef_last,
       count(distinct iff(d between dateadd(month,-12,card_since) and card_since - 1, d, null)) bef_visits_12m,
       count(iff(d between dateadd(month,-12,card_since) and card_since - 1, txn, null)) bef_bills_12m,
       sum(iff(d between dateadd(month,-12,card_since) and card_since - 1, v, 0)) bef_val_12m,
       count(iff(d = card_since, txn, null)) cd_bills,
       sum(iff(d = card_since, v, 0)) cd_val,
       sum(iff(d = card_since, disc, 0)) cd_disc,
       count(distinct iff(d > card_since, d, null)) aft_visits,
       count(iff(d > card_since, txn, null)) aft_bills,
       sum(iff(d > card_since, v, 0)) aft_val,
       sum(iff(d > card_since, disc, 0)) aft_disc
from bl join tmp_members m on m.mobile = bl.mobile
group by bl.mobile
""")
    cust_befaft = q("select * from tmp_cust_befaft")
    cust_befaft["mobile"] = cust_befaft.mobile.astype(str)
    card_store = q(f"""
with card as ({CARD_SQL}),
b as (select transactionid, max(custmobileno) mobile, max(store) store
      from V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA
      where nullif(trim(custmobileno),'') is not null group by 1),
cl as (select b.mobile mobile, to_timestamp(s.businessdate/1e9)::date d,
              s.transactionid txn, b.store store
       from V2RETAIL.CRM_BRONZE.AXAPTA_SALESTRANS s
       join card c on s.itemid = c.itemid join b on b.transactionid = s.transactionid),
ft as (select mobile, min(d) fd from cl group by 1)
select cl.mobile, (array_agg(store) within group (order by d, txn))[0]::varchar card_store
from cl join ft on ft.mobile = cl.mobile and cl.d = ft.fd
group by 1""")
    card_store["mobile"] = card_store.mobile.astype(str)
    card_store["card_store"] = card_store.card_store.str.strip('"')
    conn.close()

    # ---------- index columns ----------
    for df in (div, subdiv, majcat, grid, grid_div, size, colour, colour_div):
        df["ix"] = (100 * df.mem_share / df.non_share).where(df.non_share > 0)

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

    # ---------- campaigns (anchor: window end) ----------
    progress("CRM campaign target lists…", 88)
    asof = pd.to_datetime(to)
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

    # ---------- customer BEFORE card-day / AFTER frame ----------
    # Definitions reverse-engineered from MEMBERSHIP_CARD_CUSTOMER_BEFORE_CARDDAY_AFTER
    # (05- Deep Dive) and verified 28,474/28,474 rows: FEE RECOVERED = disc buckets
    # (NOT USED if disc=0 / <50% / 50-99% / FULLY at >=100%); HIGH DISC = ttl disc >= fee;
    # RARE SHOPPER = shopped before but <=1 visit in the 12M pre-card; NEW CUST = no
    # pre-card visits; FLAG = joined flags. VISIT = a day with a bill.
    RANK_OF_TIER2 = {v: k for k, v in TIER_NAME.items()}
    cust = (members[["mobile", "tier", "card_since"]]
            .merge(cust_befaft, on="mobile", how="left")
            .merge(card_store, on="mobile", how="left"))
    for c in ("bef_visits_all", "bef_visits_12m", "bef_bills_12m", "bef_val_12m",
              "cd_bills", "cd_val", "cd_disc", "aft_visits", "aft_bills", "aft_val", "aft_disc"):
        cust[c] = cust[c].fillna(0)
    cust["days_since"] = (asof - pd.to_datetime(cust.card_since)).dt.days
    cust["card_fee"] = cust.tier.map(lambda t: TIER_PRICE[RANK_OF_TIER2[t]])
    cust["bef_abv_12m"] = cust.bef_val_12m / cust.bef_bills_12m.replace(0, pd.NA)
    cust["cd_abv"] = cust.cd_val / cust.cd_bills.replace(0, pd.NA)
    cust["aft_abv"] = cust.aft_val / cust.aft_bills.replace(0, pd.NA)
    cust["ttl_disc"] = cust.cd_disc + cust.aft_disc
    cust["disc_pct_fee"] = cust.ttl_disc / cust.card_fee
    cust["fee_recovered"] = cust.apply(
        lambda r: "NOT USED" if r.ttl_disc == 0 else
                  "FULLY" if r.ttl_disc >= r.card_fee else
                  ("50-99%" if r.ttl_disc / r.card_fee >= 0.5 else "<50%"), axis=1)
    cust["is_emp"] = cust.mobile.isin(EMP).map({True: "Y", False: "N"})
    cust["status"] = cust.mobile.map(
        lambda m: {True: "Active", False: "Inactive", None: "Unknown"}.get(EMP.get(m, {}).get("act")))
    cust["status"] = cust["status"].fillna("")
    cust["high_disc"] = ((cust.ttl_disc >= cust.card_fee) & (cust.card_fee > 0)).map({True: "Y", False: "N"})
    cust["rare_shopper"] = ((cust.bef_visits_all >= 1) & (cust.bef_visits_12m <= 1)).map({True: "Y", False: "N"})
    cust["new_cust"] = (cust.bef_visits_all == 0).map({True: "Y", False: "N"})
    def _flags(r):
        f = []
        if r.high_disc == "Y": f.append("HIGH DISC")
        if r.rare_shopper == "Y": f.append("RARE SHOPPER")
        if r.is_emp == "Y": f.append("EMPLOYEE")
        if r.new_cust == "Y": f.append("NEW CUST")
        return ", ".join(f)
    cust["flag"] = cust.apply(_flags, axis=1)
    cust = cust.sort_values("aft_val", ascending=False).reset_index(drop=True)

    COLS = ["mobile", "tier", "card_since", "card_paid", "mem_bills", "mem_sales", "last_dt", "store"]
    stamp = asof.date().isoformat()
    def save_csv(df, name, cols):
        out = df[cols].head(100000).copy()
        for c in ("last_dt", "card_since", "bef_last"):
            if c in out: out[c] = pd.to_datetime(out[c]).dt.date
        p = os.path.join(TGT, f"{stamp}_{name}.csv")
        out.to_csv(p, index=False)
        return p, len(out)

    emp_sup = emp_mem[["mobile", "tier", "card_since", "card_paid", "mem_bills", "mem_sales",
                       "status", "ecode", "name", "desig", "loc"]].sort_values("mem_sales", ascending=False)
    campaigns = {}
    for nm, df, cols in [
        ("winback",   winback, COLS + ["rec_days"]),
        ("onboard",   onboard, COLS),
        ("upgrade",   upg,     COLS),
        ("tradeup",   trade,   COLS),
        ("suppress",  emp_sup, emp_sup.columns.tolist())]:
        p, n = save_csv(df, {"winback": "winback_dormant_members", "onboard": "onboarding_single_bill",
                             "upgrade": "card_upgrade_high_spend", "tradeup": "premium_tradeup_ev_heavy",
                             "suppress": "employee_suppression_list"}[nm], cols)
        campaigns[nm] = dict(file=os.path.basename(p), n=n,
                             value=float(df.mem_sales.sum()) if "mem_sales" in df else 0.0)

    CUST_COLS = ["mobile", "tier", "card_store", "card_since", "days_since",
                 "bef_visits_all", "bef_last", "bef_visits_12m", "bef_bills_12m", "bef_val_12m", "bef_abv_12m",
                 "cd_bills", "cd_val", "cd_abv", "cd_disc",
                 "aft_visits", "aft_bills", "aft_val", "aft_abv", "aft_disc",
                 "ttl_disc", "card_fee", "disc_pct_fee", "fee_recovered",
                 "is_emp", "status", "high_disc", "rare_shopper", "new_cust", "flag"]
    f_cust, n_cust = save_csv(cust, "customer_before_after", CUST_COLS)

    # ---------- KPI numbers ----------
    m, n = kpi.loc[1], kpi.loc[0]
    gmp = gm_kpi.loc[1] if 1 in gm_kpi.index else None
    gmn = gm_kpi.loc[0] if 0 in gm_kpi.index else None
    mem_gmp = 100 * gmp.gm / gmp.costed_sale if gmp is not None and gmp.costed_sale else None
    non_gmp = 100 * gmn.gm / gmn.costed_sale if gmn is not None and gmn.costed_sale else None
    mem_cov = 100 * gmp.costed_sale / gmp.sale_v if gmp is not None and gmp.sale_v else 0

    kpis = dict(
        members=int(m.customers), mem_sales=float(m.sale_v), mem_bills=int(m.bills),
        mem_qty=float(m.sale_q), abv_m=float(m.sale_v / m.bills), abv_n=float(n.sale_v / n.bills),
        ipb_m=float(m.sale_q / m.bills), ipb_n=float(n.sale_q / n.bills),
        asp_m=float(m.sale_v / m.sale_q), asp_n=float(n.sale_v / n.sale_q),
        freq_m=float(m.bills / m.customers), freq_n=float(n.bills / max(n.customers, 1)),
        tot_sales=float(m.sale_v + n.sale_v),
        mem_share_pct=float(100 * m.sale_v / (m.sale_v + n.sale_v)),
        mem_gmp=mem_gmp, non_gmp=non_gmp, mem_cov=mem_cov,
        mem_gm=float(gmp.gm) if gmp is not None else 0.0,
        non_customers=int(n.customers), non_sales=float(n.sale_v), non_bills=int(n.bills),
        card_rev=float(tier_tbl.card_rev.sum()), slab_disc=float(tier_tbl.slab_disc.sum()),
        n_emp_mem=len(emp_mem), emp_mem_sales=float(emp_mem.mem_sales.sum()),
        emp_card_paid=float(emp_mem.card_paid.sum()), emp_card_bills=int(emp_mem.card_bills.sum()),
        n_emp_active=int((emp_mem.status == "Active").sum()),
        n_emp_inactive=int((emp_mem.status == "Inactive").sum()),
        n_emp_unknown=int((emp_mem.status == "Unknown").sum()),
        n_empdisc_bills=int(empd.transactionid.nunique()),
        empdisc_amt=float(empd.empdisc.sum()), n_dd_mob=len(emp_dd_mob),
        n_cust=len(cust),
        cust_new=int((cust.new_cust == "Y").sum()),
        cust_rare=int((cust.rare_shopper == "Y").sum()),
        cust_highdisc=int((cust.high_disc == "Y").sum()),
        cust_fully=int((cust.fee_recovered == "FULLY").sum()),
        cust_partial=int((cust.fee_recovered == "50-99%").sum()),
        cust_half=int((cust.fee_recovered == "<50%").sum()),
        cust_notused=int((cust.fee_recovered == "NOT USED").sum()),
        cust_cd_sales=float(cust.cd_val.sum()), cust_aft_sales=float(cust.aft_val.sum()),
        cust_bef_sales_12m=float(cust.bef_val_12m.sum()),
        cust_ttl_disc=float(cust.ttl_disc.sum()), cust_fees=float(cust.card_fee.sum()),
        n_win=campaigns["winback"]["n"], n_onb=campaigns["onboard"]["n"],
        n_upg=campaigns["upgrade"]["n"], n_trd=campaigns["tradeup"]["n"],
        n_sup=campaigns["suppress"]["n"],
        unmapped_pct=float(100 * div[div.division == 'UNMAPPED'].mem_v.sum() / m.sale_v) if m.sale_v else 0.0,
    )

    # ---------- ATR insights (data-driven, plain text for app + Excel) ----------
    abv_m, abv_n = kpis["abv_m"], kpis["abv_n"]

    def _ranked(df, label, minshare=0.005):
        d = df[(df.mem_share >= minshare) & (df[label] != "UNMAPPED") & (df[label] != "NA")].copy()
        return d.dropna(subset=["ix"]).sort_values("ix")

    mc, sz, cl = _ranked(majcat, "maj_cat"), _ranked(size, "size", 0.003), _ranked(colour, "colour")
    gr = grid[grid.grid.isin(["E", "V", "P", "SP"])].copy()

    def lst(d, cols, k=5, top=True):
        d = d.iloc[::-1] if top else d
        return ", ".join(f"{' / '.join(str(r[c]) for c in cols)} (ix {r.ix:.0f})" for _, r in d.head(k).iterrows())

    _prem = gr[gr.grid.isin(["P", "SP"])]
    prem_ix = (_prem.mem_v.sum() / gr.mem_v.sum()) / (_prem.non_v.sum() / gr.non_v.sum()) * 100 if gr.mem_v.sum() and gr.non_v.sum() else None
    dd_amt = float(empd.empdisc.sum())

    def _pc(v, tot):
        return f"{100*v/tot:.1f}%" if tot else "–"
    _tshare, _tsales = tier_tbl.members.sum() or 1, tier_tbl.mem_sales.sum() or 1
    top_tier = tier_tbl.mem_sales.idxmax() if tier_tbl.mem_sales.sum() else "Silver"
    _gm_txt = ", ".join(f"{t} {r.gmp:.1f}%" if r.gmp is not None and pd.notna(r.gmp) else f"{t} n/a" for t, r in tier_tbl.iterrows())

    insights = [
        ("Basket", f"Members spend {inr(abv_m)} per bill vs {inr(abv_n)} for non-members ({100*(abv_m/abv_n-1):+.0f}%), with {kpis['ipb_m']:.1f} vs {kpis['ipb_n']:.1f} items per bill and ASP {inr(kpis['asp_m'])} vs {inr(kpis['asp_n'])}.",
         "Use card benefit thresholds just above current member ABV to stretch baskets; push add-on categories at billing."),
        ("Price grid", "Member grid index: " + ", ".join(f"{r.grid} {r.ix:.0f}" for _, r in gr.sort_values('grid', key=lambda s: s.map({'E': 0, 'V': 1, 'P': 2, 'SP': 3})).iterrows()) + (f". P+SP combined index {prem_ix:.0f}." if prem_ix else "."),
         "Keep P/SP depth and freshness in member-heavy stores; target E-heavy members with trade-up offers." if (prem_ix or 0) >= 100 else "Members skew to E/V — lead member communication with value pricing; test trade-up offers into P."),
        ("Major categories – members over-index", lst(mc, ["division", "maj_cat"], 6, True),
         "Protect availability (size-sets, depth) in these categories; feature them in member communication and new-arrival alerts."),
        ("Major categories – members under-index", lst(mc, ["division", "maj_cat"], 6, False),
         "Investigate range/price fit for members; use targeted member offers to cross-sell these categories."),
        ("Sizes", "Over-index: " + lst(sz, ["division", "size"], 5, True) + ". Under-index: " + lst(sz, ["division", "size"], 5, False) + ".",
         "Re-weight size ratios in allocation for member-heavy stores towards over-indexed sizes; check broken size-sets."),
        ("Colours", "Over-index: " + lst(cl, ["colour"], 6, True) + ". Under-index: " + lst(cl, ["colour"], 6, False) + ".",
         "Feed member colour preferences into next-season buying and visual merchandising."),
        ("Tiers", f"{top_tier} holds the largest member sales share ({_pc(tier_tbl.loc[top_tier, 'mem_sales'], _tsales)}); Platinum is {_pc(tier_tbl.loc['Platinum', 'mem_sales'], _tsales)}. Member GM% by tier: {_gm_txt}.",
         "Pitch card upgrades to high-spend Silver/Gold members (₹25k/₹50k lists ready); protect Platinum entitlements that drive the 11% slab."),
        ("Employee factor", f"{len(emp_mem):,} employees hold membership cards ({_pc(len(emp_mem), max(len(members), 1))} of members), contributing {inr(emp_mem.mem_sales.sum())} ({_pc(emp_mem.mem_sales.sum(), max(members.mem_sales.sum(), 1))} of member sales). "
                            f"{emp_dd.mobile.nunique():,} employees appear on Axapta staff-discount bills ({int(empd.transactionid.nunique()):,} bills, {inr(dd_amt)}); "
                            f"{len(emp_dd_mob):,} of them also hold a membership card (double-dip: staff discount + member slab).",
         "Audit the double-dip list (targets CSV); policy call on staff card purchases; suppress employee mobiles from member campaigns."),
        ("Member GM", f"Member GM {f'{mem_gmp:.1f}%' if mem_gmp is not None else 'n/a'} vs non-member {f'{non_gmp:.1f}%' if non_gmp is not None else 'n/a'} (costed lines only, {mem_cov:.0f}% value coverage); est. slab discount given {inr(tier_tbl.slab_disc.sum())} vs card revenue {inr(tier_tbl.card_rev.sum())}.",
         "If member GM% trails non-member, re-price the Platinum slab or steer members to higher-margin categories; track discount leakage monthly."),
        ("CRM campaigns", f"Target lists built: dormant winback {campaigns['winback']['n']:,}, single-bill onboarding {campaigns['onboard']['n']:,}, card upgrade {campaigns['upgrade']['n']:,}, E/V trade-up {campaigns['tradeup']['n']:,}, employee suppression {campaigns['suppress']['n']:,}.",
         "Run winback + onboarding via RCS/call-centre this week; upgrade pitch with next-festival offer; exclude the suppression list from all member comms."),
        ("Data quality", f"{kpis['unmapped_pct']:.1f}% of member sales have no article-master match; bills carry no loyalty card ID.",
         "Populate LOYALTYCARDID at POS and close article-master gaps so member tracking is exact."),
    ]

    progress("Finalising…", 96)
    res = dict(
        frm=frm, to=to, win=win, generated=t0.strftime("%d %b %Y %H:%M"),
        duration_s=(datetime.datetime.now() - t0).total_seconds(),
        period_mn=str(period.mn), period_mx=str(period.mx),
        kpis=kpis, emp_src=emp_src,
        weekly=weekly, enrol=enrol, enrol_tier=enrol_tier,
        div=div, subdiv=subdiv, majcat=majcat, grid=grid, grid_div=grid_div,
        size=size, colour=colour, colour_div=colour_div,
        tier_tbl=tier_tbl, gm_div=gm_div, gm_div_tier=gm_div_tier, card_rev=card_rev,
        members=members, emp_mem=emp_mem, emp_dd=empd,
        customers=cust, cust_file=os.path.basename(f_cust),
        campaigns=campaigns, insights=insights,
    )
    tmp_pkl = cache_path(frm, to) + ".tmp"       # atomic write: server may read concurrently
    pd.to_pickle(res, tmp_pkl)
    os.replace(tmp_pkl, cache_path(frm, to))
    progress("Done", 100)
    return res
