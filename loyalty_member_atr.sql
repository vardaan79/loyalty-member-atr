-- ATR: Loyalty card member sales break-up
-- Division > Sub Division > Maj Cat > Grid (E/V/P/SP = RNG_SEG) > Size > Colour
-- Member  = mobile on any bill containing a loyalty card article; counted from first card purchase date.
-- Axapta stores sales as negative NETAMOUNT/QTY -> sign flipped. Card articles excluded from sales.
--
-- v2 (21-Oct-2026): sections 2-5 added — tier segmentation (Silver/Gold/Platinum),
-- employee factor (HRMS mobiles + Axapta ACX_EMPID), member GM (GOLD.LOYALTY_ART_COST),
-- and CRM campaign target frames. The orchestrator for all of this is
-- reports/build_loyalty_atr.py (builds the HTML + target CSVs); the statements below
-- are the standalone SQL equivalents.
--
-- Card tiers (validated in MEMBERSHIP CARD automation — do not change):
--   000000001730000018 = Silver   Rs  200 card, 2% slab
--   000000001730000003 = Gold     Rs  500 card, 5% slab
--   000000001730000004 = Platinum Rs 1000 card, 11% slab
with card as (select distinct card_item itemid from V2RETAIL.MIS_REPORTS.LOYALTY_BILLS
              union select '000000001730000018' union select '000000001730000003'
              union select '000000001730000004'),  -- static union: LOYALTY_BILLS may lag
members as (
  select b.custmobileno, min(to_timestamp(s.businessdate/1e9))::date card_since
  from V2RETAIL.CRM_BRONZE.AXAPTA_SALESTRANS s
  join card c on s.itemid = c.itemid
  join V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA b on b.transactionid = s.transactionid
  where nullif(trim(b.custmobileno),'') is not null
  group by 1
),
lines as (
  select b.custmobileno, s.transactionid, ltrim(s.itemid,'0') art, -s.qty qty, -s.netamount sale_v
  from V2RETAIL.CRM_BRONZE.AXAPTA_SALESTRANS s
  join V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA b on b.transactionid = s.transactionid
  join members m on m.custmobileno = b.custmobileno
  where to_timestamp(s.businessdate/1e9)::date >= m.card_since
    and s.itemid not in (select itemid from card)
)
select coalesce(a.div,'UNMAPPED') division, a.sub_div sub_division, a.maj_cat,
       coalesce(a.rng_seg,'NA') grid, a.size_1 size, a.color colour,
       sum(l.qty)::int sale_q, round(sum(l.sale_v),0) sale_v,
       count(distinct l.transactionid) bills, count(distinct l.custmobileno) customers,
       round(100*ratio_to_report(sum(l.sale_v)) over (partition by a.maj_cat),2) pct_of_majcat,
       round(100*ratio_to_report(sum(l.sale_v)) over (),3) pct_of_total
from lines l
left join V2RETAIL.SILVER.V_VAR_ARTICLE_MASTER a on a.var_article = l.art
group by all
order by division, sub_division, maj_cat, sale_v desc;

-- ============================================================================
-- 2. TIER SEGMENTATION — Silver / Gold / Platinum by highest card SKU held
-- ============================================================================
-- select mobile, min(d) card_since, max(rnk) tier_rank, sum(v) card_paid,
--        count(distinct txn) card_bills
-- from (
--   select b.custmobileno mobile, to_timestamp(s.businessdate/1e9)::date d,
--          s.transactionid txn,
--          iff(s.itemid='000000001730000004',3, iff(s.itemid='000000001730000003',2,1)) rnk,
--          -s.netamount v
--   from V2RETAIL.CRM_BRONZE.AXAPTA_SALESTRANS s
--   join card c on s.itemid = c.itemid
--   join V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA b on b.transactionid = s.transactionid
--   where nullif(trim(b.custmobileno),'') is not null)
-- group by 1;
-- Join the break-up above on custmobileno = mobile for tier-wise mix (sales, ABV,
-- frequency, E/V/P/SP grid shares, GM% per tier).

-- ============================================================================
-- 3. EMPLOYEE FACTOR — employees inside the member base + double-dip detection
-- ============================================================================
-- Employee roster: HRMS (SQL Server 192.168.151.28\hrms) tblEmployee IsActive=1,
-- mobile normalised to 10 digits — joined in the Python builder (pyodbc), fallback
-- snapshot: MEMBERSHIP CARD\emp_by_mobile.json.
-- Employees holding cards = members ∩ employee mobiles (joined in pandas).
-- Axapta employee-discount bills in the ATR period (staff-discount mechanism):
-- select to_timestamp(businessdate/1e9)::date d, transactionid, max(store) store,
--        max(custmobileno) mobile, max(acx_empid) empid, max(acx_empdiscamount) empdisc
-- from V2RETAIL.CRM_BRONZE.AXAPTA_BILL_WISE_DATA
-- where to_timestamp(businessdate/1e9)::date >= (select min(card_since) from members)
--   and nullif(trim(acx_empid),'') is not null
-- group by 1, 2;
-- Double-dip = emp-disc bill whose payer mobile is BOTH an employee and a member.

-- ============================================================================
-- 4. MEMBER GM — unit cost via GOLD loyalty cost tables (line level)
-- ============================================================================
-- Bill-level COSTAMOUNT is empty in Axapta; use store x article actuals:
--   primary : V2RETAIL.GOLD.LOYALTY_ART_COST      (STORE, ART = 13-digit, UNIT_COST)
--   fallback: V2RETAIL.GOLD.LOYALTY_ART_COST_ANY  (ART-level)
--   fallback: V2RETAIL.SILVER.VW_PRODUCT_MASTER.ARTICLE_COST
-- Join key: lpad(ltrim(s.itemid,'0'),13,'0')  (bronze ITEMID = 5 zeros + 13-digit SAP article)
-- GM line = sale_v - qty * unit_cost   (costed lines only; ex-tax cost vs
-- MRP-inclusive sale -> GM% reads rich vs books — same caveat as the margin report)
-- Sanity check vs the data-platform store-day GM table:
-- select * from V2RETAIL.GOLD.LOYALTY_GM_STORE_DAY;  -- STORE, BUSINESS_DATE, NS_EX, COGS, NS_NOCOST

-- ============================================================================
-- 5. CRM CAMPAIGN TARGET FRAMES (built in pandas by the builder, exported as CSV)
-- ============================================================================
-- winback    : members, 2+ post-enrolment bills, no purchase in last 30 days
-- onboarding : members, <=1 post-enrolment bill (bought card, never returned)
-- upgrade    : Silver with >= Rs 25k / Gold with >= Rs 50k member-period spend
-- trade-up   : >= 80% of member-period spend in E/V grids
-- suppression: employee mobiles holding cards (exclude from member campaigns)
-- All lists junk-mobile filtered (^[6-9]\d{9}$, no 9999999999-style test mobiles).
