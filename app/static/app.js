/* Loyalty ATR app — dashboard logic (vanilla JS + Chart.js) */
"use strict";

let D = null;                     // current data payload
let CUR_JOB = null;               // current job id
let CH = {};                      // chart.js instances
let MIX_DIM = "div";
let pollTimer = null;

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

/* ---------------- formatting ---------------- */
const inr = v => {
  if (v == null || isNaN(v)) return "–";
  const a = Math.abs(v);
  if (a >= 1e7) return "₹" + (v / 1e7).toFixed(2) + " Cr";
  if (a >= 1e5) return "₹" + (v / 1e5).toFixed(1) + " L";
  return "₹" + Math.round(v).toLocaleString("en-IN");
};
const nfmt = (v, d = 0) => (v == null || isNaN(v)) ? "–" :
  Number(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
const pfmt = v => (v == null || isNaN(v)) ? "–" : Number(v).toFixed(1) + "%";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const wkLabel = ds => { const d = new Date(ds + "T00:00:00"); return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", yy: "2-digit" }); };

function toast(msg, err = false, ms = 3800) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (err ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = ""), ms);
}

/* ---------------- generic table (sort + search + per-column filters) ---------------- */
function renderTable(el, cols, rows, opts = {}) {
  const raw = opts.rawRows ?? rows;
  let data = [...raw];
  const flt = el._filters || {};
  data = data.filter(r => Object.entries(flt).every(([k, set]) =>
    !set || !set.size || set.has(String(r[k] ?? ""))));

  const st = el._sort || (opts.sortDefault ? { k: opts.sortDefault.k, dir: opts.sortDefault.dir || -1 } : null);
  if (st) {
    data.sort((a, b) => {
      let x = a[st.k], y = b[st.k];
      if (typeof x === "string" || typeof y === "string") { x = String(x ?? ""); y = String(y ?? ""); return st.dir * x.localeCompare(y); }
      x = x == null ? -Infinity : x; y = y == null ? -Infinity : y;
      return st.dir * (x - y);
    });
  }
  const q = (el._q || "").toLowerCase();
  if (q) data = data.filter(r => cols.some(c => String(r[c.k] ?? "").toLowerCase().includes(q)));

  if (!raw.length) { el.innerHTML = `<div style="padding:22px;color:#64748b">No rows in this window.</div>`; return; }
  if (!data.length && (q || Object.values(flt).some(s => s && s.size))) {
    el.innerHTML = `<div style="padding:22px;color:#64748b">No rows match the active filters.</div>`;
    bindTableHead(el, cols, raw, opts, st);
    if (opts.note) noteFor(el, opts, 0);
    return;
  }

  const head = cols.map(c => {
    const arr = st && st.k === c.k ? `<span class="arr">${st.dir < 0 ? "▼" : "▲"}</span>` : "";
    const on = flt[c.k] && flt[c.k].size ? " on" : "";
    return `<th class="${c.num ? "num" : ""}" data-k="${c.k}"><span class="th-l" title="Sort">${esc(c.label)}${arr}</span>` +
           `<button class="funnel${on}" data-fk="${c.k}" title="Filter column">▾</button></th>`;
  }).join("");
  const body = data.map(r => "<tr>" + cols.map(c => {
    let v = c.render ? c.render(r) : (r[c.k] ?? "");
    const cls = [c.num ? "num" : "", c.cls ? c.cls(r) : ""].join(" ").trim();
    return `<td class="${cls}">${v}</td>`;
  }).join("") + "</tr>").join("");

  el.innerHTML = `<table class="dt"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  bindTableHead(el, cols, raw, opts, st);
  if (opts.note) noteFor(el, opts, data.length);
}
function noteFor(el, opts, n) {
  let note = $(".count-note", el.parentElement.parentElement);
  if (!note) { note = document.createElement("div"); note.className = "count-note"; note.style.cssText = "padding:8px 14px"; el.parentElement.parentElement.appendChild(note); }
  note.textContent = opts.note(n);
}
function bindTableHead(el, cols, raw, opts, st) {
  $$(".dt thead th", el).forEach(th => {
    const label = $(".th-l", th);
    label.onclick = () => {
      const k = th.dataset.k, cur = el._sort;
      el._sort = { k, dir: cur && cur.k === k ? -cur.dir : -1 };
      renderTable(el, cols, raw, opts);
    };
    $(".funnel", th).onclick = ev => {
      ev.stopPropagation();
      const col = cols.find(c => c.k === th.dataset.fk);
      openColFilter(el, col, raw, opts, th);
    };
  });
}

/* ---- per-column filter popover ---- */
let cfPop = null;
function closeColFilter() { if (cfPop) { cfPop.remove(); cfPop = null; } }
function openColFilter(el, col, raw, opts, th) {
  const keep = el._filters || {};
  closeColFilter();
  const counts = new Map();
  raw.forEach(r => { const v = String(r[col.k] ?? ""); counts.set(v, (counts.get(v) || 0) + 1); });
  const entries = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const sel = keep[col.k] || new Set();
  cfPop = document.createElement("div");
  cfPop.className = "cf-pop";
  cfPop.innerHTML =
    `<div class="cf-h">Filter — ${esc(col.label)}<span class="cf-x" title="Close">✕</span></div>
     <input class="cf-search" placeholder="Find value…">
     <div class="cf-list">${entries.slice(0, 800).map(([v, n]) => `
       <label class="cf-it"><input type="checkbox" value="${esc(v)}" ${sel.has(v) ? "checked" : ""}>
       <span class="cf-v">${v === "" ? "(blank)" : esc(v)}</span><span class="cf-n">${nfmt(n)}</span></label>`).join("")}
       ${entries.length > 800 ? `<div class="cf-more">+${nfmt(entries.length - 800)} more values — use search</div>` : ""}</div>
     <div class="cf-f"><button data-a="all">All</button><button data-a="clear">Clear filter</button></div>`;
  document.body.appendChild(cfPop);
  const r = th.getBoundingClientRect();
  cfPop.style.top = Math.min(r.bottom + 5, window.innerHeight - 340) + "px";
  cfPop.style.left = Math.max(8, Math.min(r.left - 40, window.innerWidth - 290)) + "px";

  const rerender = () => renderTable(el, cols, raw, opts);
  const checked = () => new Set($$("input:checked", cfPop).map(i => i.value));
  $$(".cf-it input", cfPop).forEach(cb => cb.onchange = () => {
    const set = checked();
    el._filters = el._filters || {};
    if (set.size) el._filters[col.k] = set; else delete el._filters[col.k];
    rerender();
    const on = set.size ? " on" : "";
    const btn = $(`.funnel[data-fk="${col.k}"]`, el);
    if (btn) btn.className = "funnel" + on;
  });
  $(".cf-search", cfPop).oninput = e => {
    const qv = e.target.value.toLowerCase();
    $$(".cf-it", cfPop).forEach(it => it.style.display = it.textContent.toLowerCase().includes(qv) ? "" : "none");
  };
  $(".cf-x", cfPop).onclick = closeColFilter;
  $$(".cf-f button", cfPop).forEach(b => b.onclick = () => {
    if (b.dataset.a === "all") {
      if (el._filters) delete el._filters[col.k];
    } else {
      if (el._filters) delete el._filters[col.k];
    }
    $$("input[type=checkbox]", cfPop).forEach(cb => cb.checked = false);
    rerender();
    const btn = $(`.funnel[data-fk="${col.k}"]`, el);
    if (btn) btn.className = "funnel";
  });
  setTimeout(() => document.addEventListener("click", cfOutside), 0);
  function cfOutside(e) { if (cfPop && !cfPop.contains(e.target)) { closeColFilter(); document.removeEventListener("click", cfOutside); } }
}
function bindSearch(input, el, cols, getRows, opts) {
  input.oninput = () => { el._q = input.value; renderTable(el, cols, getRows(), opts); };
}

/* ---------------- section switching + mobile drawer ---------------- */
const aside = document.querySelector("aside");
const backdrop = document.createElement("div");
backdrop.id = "backdrop";
document.body.appendChild(backdrop);
const closeDrawer = () => { aside.classList.remove("open"); backdrop.classList.remove("show"); };
$("#menu-btn").onclick = () => {
  const open = !aside.classList.contains("open");
  aside.classList.toggle("open", open);
  backdrop.classList.toggle("show", open);
};
backdrop.onclick = closeDrawer;

$("#nav").onclick = e => {
  const b = e.target.closest(".nav-item"); if (!b) return;
  $$(".nav-item").forEach(x => x.classList.toggle("active", x === b));
  $$(".section").forEach(s => s.classList.toggle("on", s.id === "sec-" + b.dataset.sec));
  closeDrawer();
  window.scrollTo({ top: 0 });
};

/* ---------------- charts ---------------- */
function chart(id, cfg) {
  if (CH[id]) CH[id].destroy();
  CH[id] = new Chart($("#" + id), Object.assign({ options: { maintainAspectRatio: false, responsive: true } }, cfg));
}
Chart.defaults.font.family = '"Segoe UI", Arial, sans-serif';
Chart.defaults.font.size = 11;
Chart.defaults.color = "#64748b";
Chart.defaults.animation.duration = 400;
const GRIDC = { E: "#94a3b8", V: "#38bdf8", P: "#2563eb", SP: "#7c3aed" };
const TIERC = { Silver: "#64748b", Gold: "#d97706", Platinum: "#7c3aed" };

/* ---------------- KPI tiles (clickable → source data) ---------------- */
function tile(l, v, c, cls = "", goto = null) {
  const g = goto ? ` data-goto='${JSON.stringify(goto).replace(/'/g, "&#39;")}'` : "";
  return `<div class="kpi ${cls}${goto ? " clickable" : ""}"${g}><div class="l">${l}</div><div class="v">${v}</div><div class="c">${c}</div></div>`;
}
const updn = (m, n) => { if (!n) return ""; const d = 100 * (m / n - 1); return `vs non-member <b class="${d >= 0 ? "up" : "dn"}">${d >= 0 ? "+" : ""}${d.toFixed(0)}%</b>`; };

function renderKpis() {
  const k = D.kpis;
  $("#kpi-row1").innerHTML =
    tile("Card members", nfmt(k.members), "buying since card purchase", "", { sec: "cust", tbl: "#cust-table" }) +
    tile("Member sales", inr(k.mem_sales), `${k.mem_share_pct.toFixed(1)}% of total ${inr(k.tot_sales)}`, "good", { sec: "cust", tbl: "#cust-table" }) +
    tile("Member bills", nfmt(k.mem_bills), `${k.freq_m.toFixed(2)} bills / member`, "", { sec: "cust", tbl: "#cust-table" }) +
    tile("Avg bill value", inr(k.abv_m), updn(k.abv_m, k.abv_n) + ` · ${inr(k.abv_n)}`, k.abv_m >= k.abv_n ? "good" : "bad", { sec: "mix" }) +
    tile("Items per bill", k.ipb_m.toFixed(2), updn(k.ipb_m, k.ipb_n) + ` · ${k.ipb_n.toFixed(2)}`, "", { sec: "mix" }) +
    tile("Avg selling price", inr(k.asp_m), updn(k.asp_m, k.asp_n) + ` · ${inr(k.asp_n)}`, "", { sec: "mix" });
  $("#kpi-row2").innerHTML =
    tile("Member GM%", k.mem_gmp == null ? "–" : k.mem_gmp.toFixed(1) + "%",
      `non-member ${k.non_gmp == null ? "–" : k.non_gmp.toFixed(1) + "%"} · ${k.mem_cov.toFixed(0)}% costed`, "purple", { sec: "margin" }) +
    tile("Card revenue", inr(k.card_rev), `est. slab discount ${inr(k.slab_disc)}`, "gold", { sec: "tiers", tbl: "#tier-table" }) +
    tile("Employee members", nfmt(k.n_emp_mem),
      `${k.n_emp_active != null ? `${k.n_emp_active} active · ${k.n_emp_inactive} inactive · ` : ""}${nfmt(k.emp_mem_sales)} sales · ${k.n_dd_mob} double-dip`, k.n_dd_mob ? "bad" : "", { sec: "emp", tbl: "#emp-table" }) +
    tile("Campaign targets", nfmt(k.n_win + k.n_onb + k.n_upg + k.n_trd),
      `winback ${nfmt(k.n_win)} · onboard ${nfmt(k.n_onb)} · upgrade ${nfmt(k.n_upg)}`, "", { sec: "camp" });
}

function renderEcon() {
  const k = D.kpis;
  const row = (l, v, s = "") => `<tr><td style="padding:5px 0;color:#475569">${l}</td><td class="num" style="padding:5px 0;font-weight:700">${v}</td><td style="padding:5px 0;color:#94a3b8;font-size:11.5px">${s}</td></tr>`;
  $("#econ-box").innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:13px"><tbody>
    ${row("Card revenue (window)", inr(k.card_rev))}
    ${row("Est. member slab discount", inr(k.slab_disc), "leakage at line level")}
    ${row("Member sales", inr(k.mem_sales), `GM ${k.mem_gmp == null ? "–" : k.mem_gmp.toFixed(1) + "%"}`)}
    ${row("Gross margin — member", inr(k.mem_gm), `coverage ${k.mem_cov.toFixed(0)}% of value costed`)}
    ${row("Unmapped article sales", k.unmapped_pct.toFixed(1) + "%", "no article-master match")}
  </tbody></table>
  <div style="margin-top:10px;font-size:12px;color:#64748b;background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px">
    Net programme effect = incremental GM of member purchases − slab discount + card revenue.
  </div>`;
}

/* ---------------- overview charts ---------------- */
function renderCharts() {
  const w = D.weekly, labels = w.map(r => wkLabel(r.ds));
  chart("ch-weekly", {
    type: "bar",
    data: { labels, datasets: [
      { label: "Member sales", data: w.map(r => r.mem_v / 1e5), backgroundColor: "#4472c4", stack: "s", borderRadius: 3 },
      { label: "Non-member", data: w.map(r => (r.tot_v - r.mem_v) / 1e5), backgroundColor: "#dbe3ef", stack: "s", borderRadius: 3 }]},
    options: { plugins: { tooltip: { callbacks: { label: c => `${c.dataset.label}: ${inr(c.raw * 1e5)}` } } },
      scales: { x: { stacked: true, grid: { display: false } },
        y: { stacked: true, title: { display: true, text: "₹ Lakh" }, ticks: { callback: v => v + " L" } } } }
  });
  const en = D.enrol_tier;
  chart("ch-enrol", {
    type: "bar",
    data: { labels: en.map(r => wkLabel(r.ds)), datasets: [
      { label: "Silver", data: en.map(r => r.s), backgroundColor: TIERC.Silver, stack: "s", borderRadius: 3 },
      { label: "Gold", data: en.map(r => r.g), backgroundColor: TIERC.Gold, stack: "s", borderRadius: 3 },
      { label: "Platinum", data: en.map(r => r.p), backgroundColor: TIERC.Platinum, stack: "s", borderRadius: 3 }]},
    options: { scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, title: { display: true, text: "new members" } } } }
  });
  const g = D.grid;
  chart("ch-grid", {
    type: "doughnut",
    data: { labels: g.map(r => ({ E: "Economy", V: "Value", P: "Premium", SP: "Super Premium" }[r.grid] || r.grid)),
      datasets: [{ data: g.map(r => r.mem_v), backgroundColor: g.map(r => GRIDC[r.grid] || "#cbd5e1"), borderWidth: 2, borderColor: "#fff" }]},
    options: { cutout: "62%", plugins: { legend: { position: "right" }, tooltip: { callbacks: { label: c => `${c.label}: ${inr(c.raw)}` } } } }
  });
  chart("ch-share", {
    type: "line",
    data: { labels, datasets: [{ label: "Member share %", data: w.map(r => 100 * r.mem_v / Math.max(r.tot_v, 1)),
      borderColor: "#4472c4", backgroundColor: "rgba(68,114,196,.12)", fill: true, tension: .35, pointRadius: 3 }]},
    options: { scales: { y: { ticks: { callback: v => v + "%" } }, x: { grid: { display: false } } },
      plugins: { tooltip: { callbacks: { label: c => c.raw.toFixed(1) + "%" } } } }
  });
}

/* ---------------- mix tab ---------------- */
const MIX_META = {
  div:    { labels: ["division"], title: "Division" },
  subdiv: { labels: ["division", "sub_division"], title: "Sub Division" },
  majcat: { labels: ["division", "maj_cat"], title: "Major Category" },
  grid:   { labels: ["grid"], title: "Price Grid" },
  size:   { labels: ["division", "size"], title: "Size" },
  colour: { labels: ["colour"], title: "Colour" },
};
function mixCols() {
  const m = MIX_META[MIX_DIM];
  const labelCols = m.labels.map(k => ({ k, label: k.replace("_", " ") }));
  return [
    ...labelCols,
    { k: "mem_v", label: "Member sales", num: 1, render: r => inr(r.mem_v) },
    { k: "bar", label: "", render: r => `<div class="bar"><span style="width:${mx() ? Math.max(0, 100 * r.mem_v / mx()) : 0}%"></span></div>` },
    { k: "mem_share", label: "Share", num: 1, render: r => pfmt(100 * r.mem_share) },
    { k: "ix", label: "Index", num: 1, render: r => r.ix == null ? "–" : nfmt(r.ix), cls: r => r.ix == null ? "" : (r.ix >= 110 ? "hi" : r.ix <= 90 ? "lo" : "") },
    { k: "e", label: "E %", num: 1, render: r => gpc(r, "e_v") },
    { k: "v", label: "V %", num: 1, render: r => gpc(r, "v_v") },
    { k: "p", label: "P %", num: 1, render: r => gpc(r, "p_v") },
    { k: "sp", label: "SP %", num: 1, render: r => gpc(r, "sp_v") },
    { k: "st", label: "Grid mix", render: r => {
        const t = Math.max(["e_v", "v_v", "p_v", "sp_v"].reduce((s, k) => s + Math.max(r[k] || 0, 0), 0), 1e-9);
        return `<div class="stack">${["e_v", "v_v", "p_v", "sp_v"].map((k, i) =>
          `<span style="width:${100 * Math.max(r[k] || 0, 0) / t}%;background:${Object.values(GRIDC)[i]}"></span>`).join("")}</div>`; } },
  ];
}
const gpc = (r, k) => r.mem_v ? pfmt(100 * r[k] / r.mem_v) : "–";
const mx = () => D ? Math.max(...D[MIX_DIM].map(r => r.mem_v), 0) : 1;

function renderMix() {
  const rows = D[MIX_DIM];
  const el = $("#mix-table");
  el._q = $("#mix-search").value || "";
  $("#mix-search").oninput = () => { el._q = $("#mix-search").value; renderMix(); };
  renderTable(el, mixCols(), rows, { rawRows: rows, sortDefault: { k: "mem_v", dir: -1 },
    note: n => `${n} rows shown · window ${D.win}` });
  $("#mix-chart-title").textContent = `Top 12 ${MIX_META[MIX_DIM].title.toLowerCase()} by member sales`;
  const top = [...rows].sort((a, b) => b.mem_v - a.mem_v).slice(0, 12).reverse();
  chart("ch-mix", {
    type: "bar",
    data: { labels: top.map(r => MIX_META[MIX_DIM].labels.map(k => r[k]).join(" · ")),
      datasets: [{ data: top.map(r => r.mem_v),
        backgroundColor: top.map(r => r.ix == null ? "#94a3b8" : r.ix >= 110 ? "#15803d" : r.ix <= 90 ? "#b91c1c" : "#4472c4"),
        borderRadius: 4 }]},
    options: { indexAxis: "y", plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${inr(c.raw)} · index ${top[c.dataIndex].ix ?? "–"}` } } },
      scales: { x: { ticks: { callback: v => (v / 1e5).toFixed(0) + " L" } }, y: { grid: { display: false } } } }
  });
  $("#grid-div-card").style.display = MIX_DIM === "grid" ? "" : "none";
  $("#colour-div-card").style.display = MIX_DIM === "colour" ? "" : "none";
  if (MIX_DIM === "grid") renderGridDiv();
  if (MIX_DIM === "colour") renderColourDiv();
}
function renderGridDiv() {
  const cols = [{ k: "division", label: "Division" }].concat(["E", "V", "P", "SP"].flatMap(g => [
    { k: g, label: g + " member %", num: 1, render: r => r[g] == null ? "–" : `${r[g].toFixed(0)}% <small style="color:#94a3b8">vs ${r[g + "_non"].toFixed(0)}%</small>` },
    { k: g + "_ix", label: g + " idx", num: 1, render: r => r[g + "_ix"] ?? "–", cls: r => r[g + "_ix"] == null ? "" : (r[g + "_ix"] >= 110 ? "hi" : r[g + "_ix"] <= 90 ? "lo" : "") },
  ]));
  renderTable($("#grid-div-table"), cols, D.grid_div, { rawRows: D.grid_div });
}
function renderColourDiv() {
  const cols = [{ k: "division", label: "Division" }, { k: "colour", label: "Colour" },
    { k: "mem_v", label: "Member sales", num: 1, render: r => inr(r.mem_v) },
    { k: "mem_share", label: "Share", num: 1, render: r => pfmt(100 * r.mem_share) },
    { k: "ix", label: "Index", num: 1, render: r => r.ix == null ? "–" : nfmt(r.ix), cls: r => r.ix == null ? "" : (r.ix >= 110 ? "hi" : r.ix <= 90 ? "lo" : "") },
  ];
  renderTable($("#colour-div-table"), cols, D.colour_div, { rawRows: D.colour_div, sortDefault: { k: "mem_v", dir: -1 } });
}
$("#mix-tabs").onclick = e => {
  const b = e.target.closest("button"); if (!b || !D) return;
  $$("#mix-tabs button").forEach(x => x.classList.toggle("on", x === b));
  MIX_DIM = b.dataset.d; renderMix();
};

/* ---------------- ATR actions ---------------- */
function renderAtr() {
  const editKey = "atr-edits-" + CUR_JOB;
  const edits = JSON.parse(localStorage.getItem(editKey) || "{}");
  const rows = D.insights.map(([area, obs, act]) => {
    const e = edits[area] || {};
    return { area, obs, act, owner: e.owner || "", status: e.status || "" };
  });
  const cols = [
    { k: "area", label: "Area", render: r => `<span class="atr-area">${esc(r.area)}</span>` },
    { k: "obs", label: "Observation", render: r => esc(r.obs), cls: () => "obs" },
    { k: "act", label: "Recommended action", render: r => esc(r.act), cls: () => "act" },
    { k: "owner", label: "Owner", render: r => `<span contenteditable data-a="${esc(r.area)}" data-f="owner">${esc(r.owner)}</span>` },
    { k: "status", label: "Status", render: r => `<span contenteditable data-a="${esc(r.area)}" data-f="status">${esc(r.status)}</span>` },
  ];
  renderTable($("#atr-table"), cols, rows, { rawRows: rows });
  $$("#atr-table [contenteditable]").forEach(cell => {
    cell.oninput = () => {
      const store = JSON.parse(localStorage.getItem(editKey) || "{}");
      store[cell.dataset.a] = store[cell.dataset.a] || {};
      store[cell.dataset.a][cell.dataset.f] = cell.textContent.trim();
      localStorage.setItem(editKey, JSON.stringify(store));
    };
  });
}

/* ---------------- weekly trend ---------------- */
function renderTrend() {
  const et = {}; D.enrol_tier.forEach(r => et[r.ds] = r);
  const en = {}; D.enrol.forEach(r => en[r.ds] = r.n);
  const rows = D.weekly.map(r => ({ ds: r.ds, ds_l: wkLabel(r.ds), mem_v: r.mem_v, tot_v: r.tot_v,
    share: 100 * r.mem_v / Math.max(r.tot_v, 1), n: en[r.ds] || 0,
    s: (et[r.ds] || {}).s || 0, g: (et[r.ds] || {}).g || 0, p: (et[r.ds] || {}).p || 0 }));
  const cols = [
    { k: "ds_l", label: "Week starting" },
    { k: "mem_v", label: "Member sales", num: 1, render: r => inr(r.mem_v) },
    { k: "tot_v", label: "Total sales", num: 1, render: r => inr(r.tot_v) },
    { k: "share", label: "Member share", num: 1, render: r => pfmt(r.share) },
    { k: "n", label: "New members", num: 1, render: r => nfmt(r.n) },
    { k: "s", label: "Silver", num: 1 }, { k: "g", label: "Gold", num: 1 }, { k: "p", label: "Platinum", num: 1 },
  ];
  renderTable($("#trend-table"), cols, rows, { rawRows: rows, sortDefault: { k: "ds", dir: 1 },
    note: n => `${n} weeks` });
  bindSearch($("#trend-search"), $("#trend-table"), cols, () => rows, { rawRows: rows, sortDefault: { k: "ds", dir: 1 } });
}

/* ---------------- tiers ---------------- */
function renderTiers() {
  const cards = { Silver: "silver", Gold: "gold", Platinum: "platinum" };
  $("#tier-cards").innerHTML = D.tiers.map(t => `
    <div class="tier-card ${cards[t.tier]} clickable" data-goto='{"sec":"tiers","tbl":"#tier-table","key":"tier","val":"${t.tier}"}' title="Show this tier in the table below">
      <h4>${t.tier} card</h4><div class="fee">${t.card}</div>
      <div class="big">${inr(t.mem_sales)}</div>
      <div class="sm">${nfmt(t.members)} members · ${t.sales_share.toFixed(1)}% of member sales</div>
      <div class="sm">GM% ${t.gmp ?? "–"} · ABV ${t.abv == null ? "–" : inr(t.abv)}</div>
    </div>`).join("");
  const cols = [
    { k: "tier", label: "Tier", render: r => `<span class="tag ${r.tier.toLowerCase()}">${r.tier}</span>` },
    { k: "card", label: "Card" },
    { k: "members", label: "Members", num: 1, render: r => nfmt(r.members) },
    { k: "member_share", label: "Member share", num: 1, render: r => pfmt(r.member_share) },
    { k: "mem_sales", label: "Member sales", num: 1, render: r => inr(r.mem_sales) },
    { k: "sales_share", label: "Sales share", num: 1, render: r => pfmt(r.sales_share) },
    { k: "card_rev", label: "Card revenue", num: 1, render: r => inr(r.card_rev) },
    { k: "buyers", label: "Buyers", num: 1, render: r => nfmt(r.buyers) },
    { k: "freq", label: "Bills/member", num: 1 },
    { k: "abv", label: "ABV", num: 1, render: r => r.abv == null ? "–" : inr(r.abv) },
    { k: "gmp", label: "GM%", num: 1, render: r => r.gmp == null ? "–" : r.gmp.toFixed(1) + "%" },
    { k: "slab_disc", label: "Est. slab disc.", num: 1, render: r => inr(r.slab_disc) },
    { k: "e_pct", label: "E", num: 1, render: r => r.e_pct == null ? "–" : r.e_pct.toFixed(0) + "%" },
    { k: "v_pct", label: "V", num: 1, render: r => r.v_pct == null ? "–" : r.v_pct.toFixed(0) + "%" },
    { k: "p_pct", label: "P", num: 1, render: r => r.p_pct == null ? "–" : r.p_pct.toFixed(0) + "%" },
    { k: "sp_pct", label: "SP", num: 1, render: r => r.sp_pct == null ? "–" : r.sp_pct.toFixed(0) + "%" },
  ];
  renderTable($("#tier-table"), cols, D.tiers, { rawRows: D.tiers });
  chart("ch-tier", {
    type: "bar",
    data: { labels: D.tiers.map(t => t.tier), datasets: [
      { type: "bar", label: "Member sales (₹L)", data: D.tiers.map(t => t.mem_sales / 1e5),
        backgroundColor: D.tiers.map(t => TIERC[t.tier]), borderRadius: 4, yAxisID: "y" },
      { type: "line", label: "GM%", data: D.tiers.map(t => t.gmp), borderColor: "#b45309",
        backgroundColor: "#b45309", tension: .3, pointRadius: 4, yAxisID: "y2" }]},
    options: { scales: { y: { title: { display: true, text: "₹ Lakh" } },
      y2: { position: "right", grid: { display: false }, title: { display: true, text: "GM %" } }, x: { grid: { display: false } } } }
  });
  chart("ch-tier-grid", {
    type: "bar",
    data: { labels: D.tiers.map(t => t.tier), datasets: ["E", "V", "P", "SP"].map((g, i) => ({
      label: g, data: D.tiers.map(t => t[g.toLowerCase() + "_pct"] ?? 0),
      backgroundColor: Object.values(GRIDC)[i], stack: "s", borderRadius: 2 }))},
    options: { indexAxis: "y", scales: { x: { stacked: true, max: 100, ticks: { callback: v => v + "%" } }, y: { stacked: true, grid: { display: false } } } }
  });
}

/* ---------------- customers (BEFORE card-day / AFTER) ---------------- */
const recTag = r => `<span class="tag ${r.rec === "FULLY" ? "rec-full" : r.rec === "50-99%" ? "rec-mid" : "rec-low"}">${esc(r.rec)}</span>`;

function renderCust() {
  const k = D.kpis;
  if (!D.customers.length) {
    $("#cust-kpis").innerHTML = "";
    $("#cust-table").innerHTML = `<div style="padding:22px;color:#64748b">Customer BEFORE/AFTER not in this cached window — re-run the analysis to build it.</div>`;
    return;
  }
  $("#cust-kpis").innerHTML =
    tile("Customers", nfmt(k.n_cust), `card buyers in registry · window end ${wkLabel(D.to)}`, "", { sec: "cust", tbl: "#cust-table" }) +
    tile("New customers", nfmt(k.cust_new), `${(100 * k.cust_new / Math.max(k.n_cust, 1)).toFixed(1)}% had zero pre-card visits — click for list`, "good", { sec: "cust", tbl: "#cust-table", key: "new", val: "Y" }) +
    tile("Rare shoppers", nfmt(k.cust_rare), `≤1 visit in 12M before card day — click for list`, "gold", { sec: "cust", tbl: "#cust-table", key: "rare", val: "Y" }) +
    tile("High discount", nfmt(k.cust_highdisc), `total discount ≥ card fee — click for list`, "bad", { sec: "cust", tbl: "#cust-table", key: "high", val: "Y" }) +
    tile("Fee fully recovered", nfmt(k.cust_fully), `${nfmt(k.cust_partial)} at 50–99% · ${nfmt(k.cust_half)} below 50% — click for list`, "purple", { sec: "cust", tbl: "#cust-table", key: "rec", val: "FULLY" }) +
    tile("Fees vs discount", inr(k.cust_fees), `discount given ${inr(k.cust_ttl_disc)}`, k.cust_ttl_disc > k.cust_fees ? "bad" : "good", { sec: "cust", tbl: "#cust-table" });

  const rows = D.customers;
  const cols = [
    { k: "mobile", label: "Mobile" },
    { k: "tier", label: "Tier", render: r => `<span class="tag ${r.tier.toLowerCase()}">${r.tier}</span>` },
    { k: "store", label: "Store" },
    { k: "card_since", label: "Card date", render: r => wkLabel(r.card_since) },
    { k: "days_since", label: "Days", num: 1 },
    { k: "bef_visits", label: "Bef visits", num: 1 },
    { k: "bef_v12", label: "Bef 12M", num: 1 },
    { k: "bef_val", label: "Bef val 12M", num: 1, render: r => inr(r.bef_val) },
    { k: "cd_bills", label: "CD bills", num: 1 },
    { k: "cd_val", label: "CD value", num: 1, render: r => inr(r.cd_val) },
    { k: "aft_visits", label: "Aft visits", num: 1 },
    { k: "aft_bills", label: "Aft bills", num: 1 },
    { k: "aft_val", label: "Aft value", num: 1, render: r => inr(r.aft_val) },
    { k: "aft_abv", label: "Aft ABV", num: 1, render: r => r.aft_abv == null ? "–" : inr(r.aft_abv) },
    { k: "aft_disc", label: "Aft disc", num: 1, render: r => inr(r.aft_disc) },
    { k: "ttl_disc", label: "Ttl disc", num: 1, render: r => inr(r.ttl_disc) },
    { k: "fee", label: "Fee", num: 1, render: r => inr(r.fee) },
    { k: "rec", label: "Fee recovered", render: recTag },
    { k: "emp", label: "Emp", render: r => r.emp === "Y" ? `<span class="tag dd">EMP</span>` : "" },
    { k: "status", label: "Status", render: r => r.status ? `<span class="tag ${r.status.toLowerCase()}">${r.status}</span>` : "" },
    { k: "new", label: "New", render: r => r.new === "Y" ? "Y" : "" },
    { k: "rare", label: "Rare", render: r => r.rare === "Y" ? "Y" : "" },
    { k: "high", label: "Hi-disc", render: r => r.high === "Y" ? `<span style="color:var(--bad);font-weight:700">Y</span>` : "" },
    { k: "flag", label: "Flag" },
  ];
  renderTable($("#cust-table"), cols, rows, { rawRows: rows, sortDefault: { k: "aft_val", dir: -1 },
    note: n => `Top ${n} by after-card value · ${nfmt(D.totals.n_cust_listed)} customers in window · full list in Excel / CSV` });
  bindSearch($("#cust-search"), $("#cust-table"), cols, () => rows, { rawRows: rows, sortDefault: { k: "aft_val", dir: -1 } });
  $("#cust-csv-btn").onclick = () => { window.location.href = `/api/targets/${CUR_JOB}/customers`; };
}

/* ---------------- employees ---------------- */
function renderEmp() {
  const k = D.kpis;
  $("#emp-kpis").innerHTML =
    tile("Employees holding cards", nfmt(k.n_emp_mem),
      `${k.n_emp_active != null ? `${k.n_emp_active} active · ${k.n_emp_inactive} inactive · ` : ""}${(100 * k.n_emp_mem / Math.max(D.totals.n_members_listed, 1)).toFixed(1)}% of member base`, "bad") +
    tile("Employee member sales", inr(k.emp_mem_sales), `${(100 * k.emp_mem_sales / Math.max(k.mem_sales, 1)).toFixed(1)}% of member sales`) +
    tile("Employee card spend", inr(k.emp_card_paid), `${nfmt(k.emp_card_bills)} card purchases`) +
    tile("Staff-discount bills", nfmt(k.n_empdisc_bills), `${inr(k.empdisc_amt)} · ${k.n_dd_mob} double-dip mobiles`, k.n_dd_mob ? "bad" : "");
  $("#emp-src").textContent = "Source: " + D.emp_src;
  const rows = D.employees;
  const cols = [
    { k: "ecode", label: "E-code" }, { k: "name", label: "Name" },
    { k: "desig", label: "Designation" }, { k: "loc", label: "Location" },
    { k: "mobile", label: "Mobile" },
    { k: "tier", label: "Tier", render: r => `<span class="tag ${r.tier.toLowerCase()}">${r.tier}</span>` },
    { k: "status", label: "Status", render: r => `<span class="tag ${r.status.toLowerCase()}">${r.status}</span>` },
    { k: "card_bills", label: "Card bills", num: 1 },
    { k: "card_paid", label: "Card paid", num: 1, render: r => inr(r.card_paid) },
    { k: "mem_bills", label: "Member bills", num: 1 },
    { k: "mem_sales", label: "Member sales", num: 1, render: r => inr(r.mem_sales) },
    { k: "dd_bills", label: "DD bills", num: 1, cls: r => r.dd_bills ? "lo" : "" },
    { k: "dd_amt", label: "DD amount", num: 1, render: r => r.dd_amt ? inr(r.dd_amt) : "–", cls: r => r.dd_amt ? "lo" : "" },
    { k: "flag", label: "", render: r => r.dd_bills ? `<span class="tag dd">double-dip</span>` : "" },
  ];
  renderTable($("#emp-table"), cols, rows, { rawRows: rows, sortDefault: { k: "mem_sales", dir: -1 },
    note: n => `Top ${n} shown · ${nfmt(D.totals.n_emp_listed)} employee members in window · full list in Excel / CSV` });
  bindSearch($("#emp-search"), $("#emp-table"), cols, () => rows, { rawRows: rows, sortDefault: { k: "mem_sales", dir: -1 } });
}

/* ---------------- margin ---------------- */
const tcol = { s: "#475569", g: "#b45309", p: "#6d28d9" };
const tierGmp = (r, k, c) => {
  const v = r[k];
  if (v == null) return "–";
  const bad = r.non_gmp != null && v < r.non_gmp;   // below the non-member benchmark → red
  return `<span style="color:${bad ? "var(--bad)" : c};font-weight:650">${v.toFixed(1)}%</span>`;
};

function renderMargin() {
  const cols = [
    { k: "division", label: "Division" },
    { k: "mem_v", label: "Member sales", num: 1, render: r => inr(r.mem_v) },
    { k: "mem_gmp", label: "Member GM%", num: 1, render: r => r.mem_gmp == null ? "–" : r.mem_gmp.toFixed(1) + "%", cls: r => (r.mem_gmp != null && r.non_gmp != null && r.mem_gmp < r.non_gmp) ? "lo" : "hi" },
    { k: "s_gmp", label: "Silver GM%", num: 1, render: r => tierGmp(r, "s_gmp", tcol.s) },
    { k: "g_gmp", label: "Gold GM%", num: 1, render: r => tierGmp(r, "g_gmp", tcol.g) },
    { k: "p_gmp", label: "Platinum GM%", num: 1, render: r => tierGmp(r, "p_gmp", tcol.p) },
    { k: "non_v", label: "Non-member sales", num: 1, render: r => inr(r.non_v) },
    { k: "non_gmp", label: "Non-member GM%", num: 1, render: r => r.non_gmp == null ? "–" : r.non_gmp.toFixed(1) + "%" },
  ];
  renderTable($("#gm-table"), cols, D.gm_div, { rawRows: D.gm_div });
  chart("ch-gm", {
    type: "bar",
    data: { labels: D.gm_div.map(r => r.division), datasets: [
      { label: "Member GM%", data: D.gm_div.map(r => r.mem_gmp), backgroundColor: "#4472c4", borderRadius: 3 },
      { label: "Non-member GM%", data: D.gm_div.map(r => r.non_gmp), backgroundColor: "#dbe3ef", borderRadius: 3 }]},
    options: { scales: { y: { ticks: { callback: v => v + "%" } }, x: { grid: { display: false } } } }
  });
  const k = D.kpis;
  $("#margin-note").innerHTML = `
    <div class="method">
      <b>Window economics</b><br>
      Card revenue <b>${inr(k.card_rev)}</b> · est. member slab discount given <b>${inr(k.slab_disc)}</b><br>
      Member sales <b>${inr(k.mem_sales)}</b> at ${k.mem_gmp == null ? "–" : k.mem_gmp.toFixed(1) + "%"} GM
      (${inr(k.mem_gm)} gross margin) vs non-member ${k.non_gmp == null ? "–" : k.non_gmp.toFixed(1) + "%"}<br><br>
      <b>Net programme margin effect</b> = incremental GM of member purchases − slab discount + card revenue.<br><br>
      <span style="color:#b45309">Costs are ex-tax landed; sales are MRP-inclusive → GM% reads rich vs books
      (costed-line coverage ${k.mem_cov.toFixed(0)}%).</span>
    </div>`;
}

/* ---------------- campaigns ---------------- */
function renderCamp() {
  const K = { winback: "n_win", onboard: "n_onb", upgrade: "n_upg", tradeup: "n_trd", suppress: "n_sup" };
  $("#camp-grid").innerHTML = Object.entries(D.campaigns).map(([key, c]) => `
    <div class="camp-card">
      <div class="head"><b>${esc(c.name)}</b><span class="tag ${key === "suppress" ? "dd" : "gold"}">${key === "suppress" ? "suppression" : "action list"}</span></div>
      <div class="rule">${{ winback: "2+ member bills, no purchase in 30+ days", onboard: "Bought a card, never returned (1 bill)",
        upgrade: "Silver ≥ ₹25k / Gold ≥ ₹50k spend", tradeup: "≥80% of spend in E/V grids",
        suppress: "Employee mobiles holding cards — exclude from comms" }[key]}</div>
      <div class="card-b nopad"><div class="tbl-wrap" style="max-height:230px" id="camp-tbl-${key}"></div></div>
      <div class="stats">
        <div><b>${nfmt(c.n)}</b><span>targets</span></div>
        <div><b>${inr(c.value)}</b><span>member sales</span></div>
      </div>
      <div class="foot">
        <button class="btn btn-ghost btn-mini" data-csv="${key}">⤓ CSV</button>
      </div>
    </div>`).join("");
  Object.entries(D.campaigns).forEach(([key, c]) => {
    const cols = [
      { k: "mobile", label: "Mobile" },
      { k: "tier", label: "Tier", render: r => `<span class="tag ${r.tier.toLowerCase()}">${r.tier}</span>` },
      { k: "mem_sales", label: "Sales", num: 1, render: r => inr(r.mem_sales) },
      { k: "mem_bills", label: "Bills", num: 1 },
      { k: "last_dt", label: key === "suppress" ? "E-code" : "Last shop" },
      { k: "extra", label: "Info" },
    ];
    renderTable($("#camp-tbl-" + key), cols, c.preview, { rawRows: c.preview, sortDefault: { k: "mem_sales", dir: -1 },
      note: n => `Top ${n} preview of ${nfmt(c.n)}` });
  });
  $$("#camp-grid [data-csv]").forEach(b => b.onclick = () => {
    window.location.href = `/api/targets/${CUR_JOB}/${b.dataset.csv}`;
  });
}

/* ---------------- render all ---------------- */
const SECTION_RENDER = { atr: renderAtr, mix: renderMix, trend: renderTrend, tiers: renderTiers,
                         cust: renderCust, emp: renderEmp, margin: renderMargin, camp: renderCamp };

document.addEventListener("click", e => {
  const card = e.target.closest("[data-goto]");
  if (!card) return;
  let g;
  try { g = JSON.parse(card.dataset.goto); } catch { return; }
  const nav = $(`.nav-item[data-sec=${g.sec}]`);
  if (nav) nav.click();
  if (g.tbl) {
    const t = $(g.tbl);
    if (t) {
      t._filters = {};
      t._q = "";
      if (g.key) t._filters[g.key] = new Set([g.val]);
      const inp = $("#" + t.id.replace("-table", "-search"));
      if (inp) inp.value = "";
    }
  }
  if (SECTION_RENDER[g.sec]) SECTION_RENDER[g.sec]();
  window.scrollTo({ top: 0 });
});

function renderAll() {
  $("#hero").style.display = "none";
  $("#dash").style.display = "";
  $("#period-chip").style.display = "";
  $("#period-txt").textContent = `${D.frm ? wkLabel(D.frm) : "All-time"} → ${wkLabel(D.to)} · ${D.period_mn.slice(0, 10)} → ${D.period_mx.slice(0, 10)}`;
  $("#gen-at").textContent = D.generated;
  $("#excel-btn").disabled = false;
  closeColFilter();
  ["#mix-table", "#cust-table", "#emp-table", "#trend-table", "#atr-table", "#tier-table", "#gm-table"].forEach(id => {
    const t = $(id);
    if (t) { t._filters = {}; t._sort = null; t._q = ""; }
  });
  ["#mix-search", "#cust-search", "#emp-search", "#trend-search"].forEach(id => { const i = $(id); if (i) i.value = ""; });
  // render each section independently — one failure never blanks the dashboard
  const sections = [[renderKpis, "KPIs"], [renderEcon, "Economics"], [renderCharts, "Charts"], [renderAtr, "ATR"],
                    [renderMix, "Mix"], [renderTrend, "Trend"], [renderTiers, "Tiers"], [renderCust, "Customers"],
                    [renderEmp, "Employees"], [renderMargin, "Margin"], [renderCamp, "Campaigns"]];
  const errors = [];
  sections.forEach(([fn, nm]) => { try { fn(); } catch (e) { console.error(nm, e); errors.push(nm); } });
  if (errors.length) toast("Loaded, but these sections failed: " + errors.join(", "), true, 8000);
}

/* ---------------- run + poll ---------------- */
async function runAnalysis() {
  const frm = $("#frm").value || null, to = $("#to").value || null;
  const btn = $("#run-btn");
  btn.disabled = true;
  $("#prog").classList.add("show");
  setProg("Queuing…", 1);
  if (!frm) toast("All-time window — the first scan is the long one (~5–10 min). It is still working even when the bar holds still.", false, 8000);
  try {
    const r = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ frm, to }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "run failed");
    CUR_JOB = j.job_id;
    startPolling();
  } catch (e) {
    btn.disabled = false; $("#prog").classList.remove("show"); toast(e.message, true);
  }
}
let stepSince = Date.now(), lastStep = "", stepTimer = null;
function setProg(step, pct) {
  if (step !== lastStep) { lastStep = step; stepSince = Date.now(); }
  $("#prog-step").textContent = step;
  $("#prog-pct").textContent = Math.round(pct) + "%";
  $("#prog-bar").style.width = pct + "%";
  renderElapsed();
  clearInterval(stepTimer);
  stepTimer = setInterval(renderElapsed, 1000);
}
function renderElapsed() {
  const s = Math.floor((Date.now() - stepSince) / 1000);
  const txt = s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
  const el = $("#prog-elapsed");
  if (el) el.textContent = "· " + txt + " on this step";
}
function stopElapsed() { clearInterval(stepTimer); }
let pollBusy = false;
function startPolling() {
  clearInterval(pollTimer);          // never stack intervals across re-runs/reloads
  stopElapsed();
  pollBusy = false;
  pollTimer = setInterval(poll, 1300);
}
async function poll() {
  if (pollBusy || !CUR_JOB) return;  // re-entrant guard: one poll in flight, ever
  pollBusy = true;
  try {
    const j = await (await fetch("/api/job/" + CUR_JOB)).json();
    if (j.status === "running") { setProg(j.step, j.pct); return; }
    clearInterval(pollTimer); pollTimer = null; stopElapsed();
    if (j.status === "error") { setProg("Failed: " + j.error, 100); toast(j.error, true); $("#run-btn").disabled = false; return; }
    setProg("Loading dashboard…", 100);
    await loadData(CUR_JOB);
    $("#run-btn").disabled = false;
    setTimeout(() => $("#prog").classList.remove("show"), 900);
    toast(`Analysis ready — window ${D.frm || "All-time"} → ${D.to} (${D.duration_s}s query time)`);
  } catch (e) {
    clearInterval(pollTimer); pollTimer = null;
    toast(e.message, true); $("#run-btn").disabled = false;
  } finally { pollBusy = false; }
}
async function loadData(job) {
  const r = await fetch("/api/data/" + job);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || "load failed");
  D = j; CUR_JOB = job;
  if (D.frm) { $("#frm").value = D.frm; setPreset("custom", false); } else setPreset("all", false);
  $("#to").value = D.to;
  renderAll();
}

/* ---------------- presets + recent ---------------- */
const today = () => new Date().toISOString().slice(0, 10);
function setPreset(p, setDates = true) {
  $$("#presets button").forEach(b => b.classList.toggle("on", b.dataset.p === p));
  if (!setDates) return;
  const t = new Date();
  if (p === "all") $("#frm").value = "";
  if (p === "d30") { const d = new Date(t - 30 * 864e5); $("#frm").value = d.toISOString().slice(0, 10); }
  if (p === "d90") { const d = new Date(t - 90 * 864e5); $("#frm").value = d.toISOString().slice(0, 10); }
  if (p === "mtd") $("#frm").value = t.toISOString().slice(0, 8) + "01";
  if (p === "fy") $("#frm").value = "2026-04-01";
  $("#to").value = today();
}
$("#presets").onclick = e => { const b = e.target.closest("button"); if (b) setPreset(b.dataset.p); };
["#frm", "#to"].forEach(s => $(s).addEventListener("change", () => {
  $$("#presets button").forEach(b => b.classList.toggle("on", b.dataset.p === "custom"));
}));
$("#run-btn").onclick = runAnalysis;
$("#hero-run").onclick = () => { $(".nav-item[data-sec=overview]").click(); runAnalysis(); };
$("#excel-btn").onclick = () => {
  if (!CUR_JOB) return;
  toast("Building the V2 workbook — download starts in a moment…");
  window.location.href = "/api/excel/" + CUR_JOB;
};
$("#camp-excel-btn").onclick = () => {
  if (!CUR_JOB) return;
  toast("Building the V2 workbook…");
  window.location.href = "/api/excel/" + CUR_JOB;
};
$("#recent-btn").onclick = async () => {
  const pop = $("#recent-pop");
  pop.classList.toggle("show");
  if (!pop.classList.contains("show")) return;
  const st = await (await fetch("/api/state")).json();
  pop.innerHTML = st.cached.length
    ? st.cached.map(c => `<div class="it" data-job="${c.job_id}"><span>${c.frm === "All-time" ? "⭐ All-time" : c.frm} → ${c.to}</span><small>${c.generated}</small></div>`).join("")
    : `<div class="empty">No saved windows yet — run one.</div>`;
  $$(".it", pop).forEach(it => it.onclick = async () => {
    pop.classList.remove("show");
    try { await loadData(it.dataset.job); toast(`Loaded window ${D.frm || "All-time"} → ${D.to} (cached)`); }
    catch (e) { toast(e.message, true); }
  });
};
document.addEventListener("click", e => {
  if (!e.target.closest(".recent")) $("#recent-pop").classList.remove("show");
});

/* ---------------- boot ---------------- */
(async function boot() {
  $("#to").value = today();
  setPreset("all");
  try {
    const st = await (await fetch("/api/state")).json();
    if (st.running.length) {                    // a build is already running (e.g. reload mid-run)
      CUR_JOB = st.running[0].job_id;
      $("#run-btn").disabled = true;
      $("#prog").classList.add("show"); setProg(st.running[0].step, st.running[0].pct);
      startPolling();
      return;
    }
    if (st.cached.length) await loadData(st.cached[0].job_id);   // newest saved window, instant
  } catch (e) { console.warn(e); }
})();
