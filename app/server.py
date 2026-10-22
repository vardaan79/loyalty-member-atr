"""Loyalty Card Member ATR — interactive web app (Flask).

Run:  python server.py   (opens on http://127.0.0.1:5015)
  - POST /api/run {frm,to} queues a Snowflake build (single-flight worker thread,
    live progress), results cached per window in app/cache for instant reload.
  - GET  /api/data/<job> serves the result payload for the dashboard.
  - GET  /api/excel/<job> streams the V2-format workbook (built once per window).
  - GET  /api/targets/<job>/<name> streams a campaign CSV.
"""
import os, math, threading, traceback, datetime
import pandas as pd
from flask import Flask, render_template, jsonify, request, send_file

import engine
import excel_export

PORT = 5015
APP_DIR = os.path.dirname(os.path.abspath(__file__))
TGT_DIR = engine.TGT

TARGET_FILES = {"winback": "winback_dormant_members", "onboard": "onboarding_single_bill",
                "upgrade": "card_upgrade_high_spend", "tradeup": "premium_tradeup_ev_heavy",
                "suppress": "employee_suppression_list", "customers": "customer_before_after"}

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0     # never heuristically cache the UI


@app.after_request
def _no_cache_ui(resp):
    """The dashboard iterates fast — always revalidate HTML/JS/CSS so updates load."""
    if (resp.content_type or "").startswith(("text/html", "text/css", "application/javascript")):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


JOBS = {}                       # job_id -> dict(status/step/pct/frm/to/error/res)
QUEUE = []
CV = threading.Condition()


def _worker():
    """Single-flight: one Snowflake build at a time, queued FIFO."""
    while True:
        with CV:
            while not QUEUE:
                CV.wait()
            job_id = QUEUE.pop(0)
        job = JOBS[job_id]
        try:
            def progress(step, pct=0):
                with CV:
                    job["step"], job["pct"] = step, pct
            res = engine.build(job["frm"], job["to"], progress)
            with CV:
                job.update(status="done", pct=100, step="Done", res=res,
                           job_id=job["cache_key"])
        except Exception as e:
            traceback.print_exc()
            with CV:
                job.update(status="error", error=str(e))


threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------- payload ----------
def _round(df, cols_ndigits):
    out = df.copy()
    for c, nd in cols_ndigits.items():
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce").round(nd)
    return out


def mix_payload(df, labels, cap=None):
    d = df.head(cap) if cap else df
    d = _round(d, {"mem_v": 0, "non_v": 0, "mem_q": 0, "mem_share": 4, "non_share": 4,
                   "e_v": 0, "v_v": 0, "p_v": 0, "sp_v": 0, "ix": 1})
    for c in labels:
        d[c] = d[c].astype(str).replace("nan", "")
    return d.to_dict("records")


def res_payload(res, job_id):
    k = dict(res["kpis"])
    for kk, dv in (("n_emp_active", None), ("n_emp_inactive", None), ("n_emp_unknown", None),
                   ("n_cust", 0), ("cust_new", 0), ("cust_rare", 0), ("cust_highdisc", 0),
                   ("cust_fully", 0), ("cust_partial", 0), ("cust_half", 0), ("cust_notused", 0),
                   ("cust_cd_sales", 0.0), ("cust_aft_sales", 0.0), ("cust_bef_sales_12m", 0.0),
                   ("cust_ttl_disc", 0.0), ("cust_fees", 0.0)):
        k.setdefault(kk, dv)

    et = res["enrol_tier"].pivot_table(index="wk", columns="rnk", values="n", aggfunc="sum").fillna(0) if len(res["enrol_tier"]) else None
    enrol_rows = []
    if et is not None:
        for wk, r in et.iterrows():
            enrol_rows.append(dict(ds=pd.to_datetime(wk).strftime("%Y-%m-%d"),
                                   s=int(r.get(1, 0)), g=int(r.get(2, 0)), p=int(r.get(3, 0))))

    # tiers
    tier_rows = []
    RANK = {"Silver": 1, "Gold": 2, "Platinum": 3}
    ts = res["tier_tbl"]
    for t, r in ts.iterrows():
        tier_rows.append(dict(
            tier=t, card=f"₹{engine.TIER_PRICE[RANK[t]]:,} · {engine.TIER_RATE[RANK[t]]*100:.0f}% slab",
            members=int(r.members),
            member_share=round(100 * r.members / max(ts.members.sum(), 1), 1),
            mem_sales=round(float(r.mem_sales)), sales_share=round(100 * r.mem_sales / max(ts.mem_sales.sum(), 1), 1),
            card_rev=round(float(r.card_rev)), buyers=int(r.buyers), freq=round(r.freq, 2),
            abv=round(float(r.mem_abv)) if pd.notna(r.mem_abv) else None,
            gmp=round(r.gmp, 1) if r.gmp is not None and pd.notna(r.gmp) else None,
            slab_disc=round(float(r.slab_disc)),
            e_pct=round(r.e_pct, 1) if r.e_pct is not None and pd.notna(r.e_pct) else None,
            v_pct=round(r.v_pct, 1) if r.v_pct is not None and pd.notna(r.v_pct) else None,
            p_pct=round(r.p_pct, 1) if r.p_pct is not None and pd.notna(r.p_pct) else None,
            sp_pct=round(r.sp_pct, 1) if r.sp_pct is not None and pd.notna(r.sp_pct) else None))

    # GM by division (+ tier split when the window was built with v3.2)
    gdt = res.get("gm_div_tier")
    if gdt is not None and len(gdt):
        gdt = gdt.set_index("division")
    gm_rows = []
    for r in res["gm_div"].itertuples():
        t = gdt.loc[r.division] if gdt is not None and r.division in gdt.index else None
        gm_rows.append(dict(
            division=r.division, mem_v=round(float(r.mem_v)),
            mem_gmp=round(100 * r.mem_gm / r.mem_costed, 1) if r.mem_costed else None,
            s_gmp=(round(float(t.s_gmp), 1) if t is not None and pd.notna(t.s_gmp) else None),
            g_gmp=(round(float(t.g_gmp), 1) if t is not None and pd.notna(t.g_gmp) else None),
            p_gmp=(round(float(t.p_gmp), 1) if t is not None and pd.notna(t.p_gmp) else None),
            s_v=(round(float(t.s_v)) if t is not None else None),
            g_v=(round(float(t.g_v)) if t is not None else None),
            p_v=(round(float(t.p_v)) if t is not None else None),
            non_v=round(float(r.non_v)),
            non_gmp=round(100 * r.non_gm / r.non_costed, 1) if r.non_costed else None))

    # weekly
    weekly = [dict(ds=pd.to_datetime(r.wk).strftime("%Y-%m-%d"), mem_v=round(float(r.mem_v)),
                   tot_v=round(float(r.tot_v))) for r in res["weekly"].itertuples()]
    enrol = [dict(ds=pd.to_datetime(r.wk).strftime("%Y-%m-%d"), n=int(r.new_members))
             for r in res["enrol"].itertuples()]

    # grid-in-division matrix
    gd_rows = []
    for dv in ("MENS", "LADIES", "KIDS"):
        sub = res["grid_div"][res["grid_div"].division == dv]
        tm, tn = sub.mem_v.sum(), sub.non_v.sum()
        row = dict(division=dv)
        for g in ("E", "V", "P", "SP"):
            rr = sub[sub.grid == g]
            if len(rr):
                ms, ns = rr.mem_v.iloc[0] / tm if tm else 0, rr.non_v.iloc[0] / tn if tn else 0
                row[g] = round(100 * ms, 1)
                row[f"{g}_non"] = round(100 * ns, 1)
                row[f"{g}_ix"] = round(100 * ms / ns) if ns else None
            else:
                row[g] = row[f"{g}_non"] = row[f"{g}_ix"] = None
        gd_rows.append(row)

    # employees (top 300 preview) with double-dip stats
    dd_bills = res["emp_dd"].groupby("mobile").transactionid.nunique().to_dict()
    dd_amt = res["emp_dd"].groupby("mobile").empdisc.sum().to_dict()
    emp = res["emp_mem"].sort_values("mem_sales", ascending=False).head(300)
    emp_rows = [dict(ecode=str(r.ecode), name=str(r.name), desig=str(r.desig), loc=str(r.loc),
                     mobile=r.mobile, tier=r.tier, status=str(r.status), card_bills=int(r.card_bills),
                     card_paid=round(float(r.card_paid)), mem_bills=int(r.mem_bills),
                     mem_sales=round(float(r.mem_sales)),
                     dd_bills=int(dd_bills.get(r.mobile, 0)), dd_amt=round(float(dd_amt.get(r.mobile, 0))))
                for r in emp.itertuples()]

    # customers BEFORE/AFTER (preview; full list in Excel + CSV)
    d = lambda v, nd=2: (None if v is None or pd.isna(v) else round(float(v), nd))
    cust_frame = res.get("customers")
    cu = cust_frame.head(400) if cust_frame is not None else cust_frame
    cust_rows = [dict(mobile=r.mobile, tier=r.tier, store=str(r.card_store or ""),
                      card_since=str(pd.to_datetime(r.card_since).date()), days_since=int(r.days_since),
                      bef_visits=int(r.bef_visits_all),
                      bef_last=(str(pd.to_datetime(r.bef_last).date()) if pd.notna(r.bef_last) else None),
                      bef_v12=int(r.bef_visits_12m), bef_b12=int(r.bef_bills_12m), bef_val=round(float(r.bef_val_12m)),
                      cd_bills=int(r.cd_bills), cd_val=round(float(r.cd_val)), cd_disc=round(float(r.cd_disc)),
                      aft_visits=int(r.aft_visits), aft_bills=int(r.aft_bills), aft_val=round(float(r.aft_val)),
                      aft_abv=d(r.aft_abv), aft_disc=round(float(r.aft_disc)),
                      ttl_disc=round(float(r.ttl_disc)), fee=int(r.card_fee),
                      disc_pct=d(r.disc_pct_fee, 3), rec=r.fee_recovered, emp=r.is_emp, status=str(r.status or ""),
                      high=r.high_disc, rare=r.rare_shopper, new=r.new_cust, flag=r.flag)
                 for r in cu.itertuples()]

    # campaign previews (same ranked rules as Excel)
    lists = excel_export._campaign_frames(res)
    camp = {}
    for key, meta in dict(winback="Dormant winback", onboard="Onboarding nudge", upgrade="Card upgrade",
                          tradeup="Premium trade-up", suppress="Employee suppression").items():
        c = res["campaigns"][key]
        src = (res["emp_mem"].sort_values("mem_sales", ascending=False) if key == "suppress"
               else lists[key])
        prev = []
        for r in src.head(25).itertuples():
            prev.append(dict(mobile=r.mobile, tier=r.tier, mem_bills=int(r.mem_bills),
                             mem_sales=round(float(r.mem_sales)),
                             last_dt=(str(pd.to_datetime(r.last_dt).date()) if key != "suppress"
                                      else str(getattr(r, "ecode", ""))),
                             extra=(f"{int(getattr(r, 'rec_days', 0))}d dormant"
                                    if key == "winback" and hasattr(r, "rec_days")
                                    else str(getattr(r, "store", "") or ""))))
        camp[key] = dict(name=meta, file=c["file"], n=c["n"], value=round(c["value"]), preview=prev)

    gridmix = mix_payload(res["grid"][res["grid"].grid.isin(["E", "V", "P", "SP"])], ["grid"])
    return dict(
        job_id=job_id, frm=res["frm"], to=res["to"], win=res["win"], generated=res["generated"],
        duration_s=round(res["duration_s"], 1), period_mn=res["period_mn"], period_mx=res["period_mx"],
        emp_src=res["emp_src"], kpis=k, insights=res["insights"],
        weekly=weekly, enrol=enrol, enrol_tier=enrol_rows,
        div=mix_payload(res["div"], ["division"]),
        subdiv=mix_payload(res["subdiv"], ["division", "sub_division"], 100),
        majcat=mix_payload(res["majcat"], ["division", "maj_cat"], 100),
        grid=gridmix, grid_div=gd_rows,
        size=mix_payload(res["size"][res["size"]["size"] != "NA"], ["division", "size"], 60),
        colour=mix_payload(res["colour"][res["colour"].colour != "NA"], ["colour"], 50),
        colour_div=mix_payload(res["colour_div"][res["colour_div"].colour != "NA"],
                               ["division", "colour"], 80),
        tiers=tier_rows, gm_div=gm_rows, employees=emp_rows, campaigns=camp,
        customers=cust_rows or [], cust_file=res.get("cust_file"),
        totals=dict(n_members_listed=int(len(res["members"])),
                    n_emp_listed=int(len(res["emp_mem"])),
                    n_cust_listed=int(len(res.get("customers")) if res.get("customers") is not None else 0)))


# ---------------------------------------------------------------- routes ----------
@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/state")
def state():
    with CV:
        running = [dict(job_id=j["cache_key"], frm=j["frm"], to=j["to"], step=j["step"], pct=j["pct"])
                   for j in JOBS.values() if j["status"] == "running"]
    return jsonify(cached=engine.cached_windows(), running=running)


@app.post("/api/run")
def run():
    d = request.get_json(force=True)
    frm = (d.get("frm") or "").strip() or None
    to = (d.get("to") or "").strip() or datetime.date.today().isoformat()
    for v in (frm, to):
        if v:
            try:
                datetime.date.fromisoformat(v)
            except ValueError:
                return jsonify(error=f"Bad date: {v}"), 400
    if frm and frm > to:
        return jsonify(error="From date must be ≤ to date"), 400
    key = f"{frm or 'ALL'}_{to}"
    with CV:
        for j in JOBS.values():                      # same window running or done → reuse
            if j["cache_key"] == key and j["status"] in ("running", "done"):
                return jsonify(job_id=j["cache_key"], reused=True)
        JOBS[key] = dict(cache_key=key, frm=frm, to=to, status="running",
                         step="Queued…", pct=1, error=None, res=None)
        QUEUE.append(key)
        CV.notify_all()
    return jsonify(job_id=key)


@app.get("/api/job/<job_id>")
def job(job_id):
    with CV:
        j = JOBS.get(job_id)
        if not j:
            return jsonify(error="unknown job"), 404
        return jsonify(status=j["status"], step=j["step"], pct=j["pct"],
                       frm=j["frm"], to=j["to"], error=j["error"])


def _get_res(job_id):
    with CV:
        j = JOBS.get(job_id)
        if j and j.get("res") is not None:
            return j["res"]
    try:
        return engine.load_cached(job_id)
    except Exception:
        return None


def _clean(o):
    """NaN/inf are invalid JSON — browsers' JSON.parse rejects them. Convert to null."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


_PAYLOADS = {}                                  # serialised payload cache per window


@app.get("/api/data/<job_id>")
def data(job_id):
    if job_id in _PAYLOADS:                     # instant reloads
        return jsonify(_PAYLOADS[job_id])
    res = _get_res(job_id)
    if res is None:
        return jsonify(error="no result for this window — run it first"), 404
    payload = _clean(res_payload(res, job_id))
    _PAYLOADS[job_id] = payload
    return jsonify(payload)


@app.get("/api/excel/<job_id>")
def excel(job_id):
    res = _get_res(job_id)
    if res is None:
        return jsonify(error="no result for this window"), 404
    fname = f"Loyalty_ATR_{(res['frm'] or 'ALL').replace('-', '')}_{res['to'].replace('-', '')}.xlsx"
    path = os.path.join(excel_export.OUT, fname)
    if not os.path.exists(path):
        excel_export.export(res, path)
    return send_file(path, as_attachment=True, download_name=fname)


@app.get("/api/targets/<job_id>/<name>")
def targets(job_id, name):
    if name not in TARGET_FILES:
        return jsonify(error="unknown list"), 404
    stamp = job_id.split("_")[-1]
    p = os.path.join(TGT_DIR, f"{stamp}_{TARGET_FILES[name]}.csv")
    if not os.path.exists(p):
        return jsonify(error=f"not found: {os.path.basename(p)}"), 404
    return send_file(p, as_attachment=True)


if __name__ == "__main__":
    print(f"Loyalty ATR app → http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
