// Orbital mission control: streams the institute's event log and shows the study it produces.
const CREW = {
  director: { name: "Director", img: "/assets/capy-director.svg", role: "plans, rules on reviews, owns SOPs" },
  "water-lab": { name: "Water Lab", img: "/assets/capy-water.svg", role: "measures lakes from orbit" },
  "fire-lab": { name: "Fire Lab", img: "/assets/capy-fire.svg", role: "maps burn scars from orbit" },
  reviewer: { name: "Reviewer", img: "/assets/capy-reviewer.svg", role: "signs off every finding" },
  auditor: { name: "Auditor", img: "/assets/capy-auditor.svg", role: "traces every number to a tool" },
  institute: { name: "Orbital", img: "/assets/capy-astronaut.svg", role: "" },
};
const MODEL_OF = { director: "director", "water-lab": "lab", "fire-lab": "lab", reviewer: "reviewer" };
const params = new URLSearchParams(location.search);
const replay = params.get("replay") === "1";
const speed = Number(params.get("speed") || 8);
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const runsUrl = (p) => "/runs/" + String(p).split("/runs/").pop();

let session = params.get("session");
let cursor = 0, models = {}, ledger = [], study = null, activeKey = null, pollTimer = null;
const doing = {};

// ---------- crew ----------
function renderCrew(active) {
  $("crew").innerHTML = ["director", "water-lab", "fire-lab", "reviewer", "auditor"].map((id) => {
    const c = CREW[id];
    const model = prettyModel(models[MODEL_OF[id]]) || (id === "auditor" ? "deterministic code, no model" : "");
    const vision = models.vision && (id === "water-lab" || id === "reviewer") ? "+ SAM 2.1 · open weights, run locally" : "";
    return `<div class="member ${active === id ? "active" : ""}">
      <img src="${c.img}" alt="">
      <div><div class="name">${c.name}</div><div class="model">${esc(model)}</div>${vision ? `<div class="model">${vision}</div>` : ""}
      <div class="doing">${esc(doing[id] || c.role)}</div></div></div>`;
  }).join("");
}

// ---------- feed ----------
function who(ev) {
  const c = CREW[ev.actor] || { name: ev.actor, img: "/assets/capy.svg" };
  const tag = ev.tag || ev.study_id;
  return { c, head: `<div class="who"><b>${esc(c.name)}</b>${tag ? ` <span class="tag">${esc(tag)}</span>` : ""} · ${new Date(ev.ts * 1000).toLocaleTimeString()}</div>` };
}

// Tools whose payload is rendered as its own card (finding, review, bulletin) or that add nothing to read.
const QUIET_TOOLS = new Set(["submit_finding", "submit_review", "publish_bulletin", "finish_planning", "finish_decisions", "assign_study", "publish", "send_back", "issue_sop"]);

function argsLine(args) {
  const s = Object.entries(args || {}).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ");
  return s.length > 140 ? s.slice(0, 140) + "…" : s;
}

function prettyModel(m) {
  if (!m) return "";
  if (m.includes("muse-spark-open")) return "Muse Spark Open · open weights on Baseten";
  if (m.startsWith("muse-spark")) return "Muse Spark " + m.split("-").pop() + " · Meta Model API";
  if (m.startsWith("muse-wonder")) return "Muse Wonder · Meta Model API";
  return m;
}

function summarize(name, r) {
  if (!r) return "";
  if (r.error) return `<span class="badge bad">error</span>${esc(r.error)}`;
  switch (name) {
    case "measure_water_series":
      return r.latest ? `${r.months_measured} months measured under <b>${esc(r.sop)}</b> · latest ${r.latest.date}: <b>${r.latest.area_km2} km²</b> · min ${r.minimum.area_km2} km² (${r.minimum.date})` : "";
    case "compare_with_ground_truth":
    case "get_ground_truth":
      return r.agreement?.pearson_r != null ? `vs USBR gauge: <b>r = ${r.agreement.pearson_r}</b> (n=${r.agreement.n}) · scatter ±${r.agreement.rmse_km2} km²${Array.isArray(r.outliers_vs_gauge) ? ` · ${r.outliers_vs_gauge.length} outlier(s)` : ""}` : "";
    case "get_series_stats":
      return r.latest ? `${r.months_measured} months · latest ${r.latest.area_km2} km² · ${esc(r.sop)}` : "";
    case "watch_timelapse":
      return r.frames_watched != null
        ? `🎬 watched ${r.frames_watched} months as ${r.clips} time-lapse clip(s) in ${r.seconds}s · ${(() => {
            const n = r.flagged_count ?? (Array.isArray(r.flagged) ? r.flagged.length : null);
            const worst = Array.isArray(r.shown) && r.shown.length ? `worst: <b>${r.shown.join(", ")}</b>` : "";
            return n === 0 ? "<b>no problem months</b>" : [n != null ? `<b>flagged ${n} month(s)</b>` : "", worst].filter(Boolean).join(" · ");
          })()}`
        : "";
    case "sam_check":
      return r.months_checked != null
        ? `🔍 SAM 2.1 outlined the lake in ${r.months_checked} months · ${r.disagreement_count
            ? `<b>disagrees with the water index in ${r.disagreement_count}</b>${Array.isArray(r.shown) ? `: ${r.shown.join(", ")}` : ""}`
            : "<b>agrees with the water index every month</b>"}${r.sam_vs_gauge?.pearson_r != null
            ? ` · SAM vs gauge r = ${r.sam_vs_gauge.pearson_r}` : ""}`
        : "";
    case "view_evidence":
      return Array.isArray(r.shown) && r.shown.length ? `looked at ${r.shown.join(", ")}` : "";
    case "recompute_point":
      return r.area_km2 != null ? `re-measured ${r.date} from raw imagery: <b>${r.area_km2.toFixed(1)} km²</b>` : "";
    case "methods_study":
    case "water_methods_study":
      return Array.isArray(r.variants)
        ? "ranked methods: " + r.variants.slice(0, 3).map((v) => `${esc(v.method)} r=${v.pearson_r}`).join(" · ")
        : `compared water-detection methods and quality gates against the gauge (${r.dates_compared ?? "?"} dates)`;
    case "fire_methods_study":
      return Array.isArray(r.variants)
        ? "ranked procedures: " + r.variants.slice(0, 3).map((v) => `${esc(v.method)} IoU ${v.iou}`).join(" · ")
        : "scored burn-mapping procedures against the official perimeter";
    case "current_sop":
      return r.current ? `current SOP ${esc(r.current.version)}` : "";
    case "issue_sop":
      return r.version ? `issued <b>${esc(r.version)}</b>` : "";
    case "measure_burn_scar":
    case "get_burn_result":
      return r.burned_km2 != null ? `burned <b>${r.burned_km2} km²</b> vs official perimeter ${r.official_perimeter_km2} km² · <b>IoU ${r.agreement_with_perimeter.iou}</b> · ${r.burned_outside_perimeter_km2} km² outside the perimeter · ${esc(r.sop)}` : "";
    case "recompute_burn":
      return r.burned_km2 != null ? `recomputed from raw imagery: <b>${r.burned_km2.toFixed(1)} km²</b> burned · IoU ${r.iou.toFixed(3)}` : "";
    case "list_fires":
      return Array.isArray(r.fires) ? `${r.fires.length} large fires this year` : "";
    default:
      return "";
  }
}

function claimList(claims) {
  return (claims || []).map((c) => `<li>${esc(c.statement)}${c.value != null ? ` — <b>${esc(c.value)} ${esc(c.unit || "")}</b>` : ""}</li>`).join("");
}

function render(ev) {
  const { c, head } = who(ev);
  let body = "", cls = "";
  switch (ev.kind) {
    case "start":
      if (ev.actor === "institute") {
        models = ev.roles || {};
        body = `<div class="card"><h4>New research cycle</h4>${esc(ev.text)}</div>`;
      } else {
        body = `<div class="text">${esc(ev.text)}</div>`;
      }
      break;
    case "round":
      return divider(`ROUND ${ev.round}`);
    case "assign":
      studies[ev.study.id] = ev.study;
      body = `<div class="text">📋 Assigned <b>${esc(ev.study.id)}</b> to the ${esc(ev.study.lab)} lab: ${esc(ev.study.question)}</div>`;
      break;
    case "thought":
      cls = "thought";
      body = `<div class="text">💭 ${esc(ev.text.slice(0, 220))}${ev.text.length > 220 ? "…" : ""}</div>`;
      break;
    case "say":
      body = `<div class="text">${esc(ev.text.slice(0, 360))}${ev.text.length > 360 ? "…" : ""}</div>`;
      break;
    case "tool_call":
      if (QUIET_TOOLS.has(ev.text)) return null;
      body = `<div class="text"><span class="chip">${esc(ev.text)}(${esc(argsLine(ev.args))})</span></div>`;
      break;
    case "tool_result": {
      if (QUIET_TOOLS.has(ev.text)) return null;
      const s = summarize(ev.text, ev.result);
      const imgs = (ev.images || []).map((p) => `<img src="${runsUrl(p)}" alt="evidence" onclick="window.open(this.src)">`).join("");
      if (!s && !imgs) return null;
      body = `${s ? `<div class="result">↳ ${s}</div>` : ""}${imgs ? `<div class="thumbs">${imgs}</div>` : ""}`;
      break;
    }
    case "finding": {
      const f = ev.finding || {};
      body = `<div class="card finding"><h4>🔬 ${esc(f.headline)}</h4><div class="text">${esc(f.summary)}</div>
        <ul>${claimList(f.claims)}</ul><div style="margin-top:6px"><span class="badge info">confidence: ${esc(f.confidence)}</span></div></div>`;
      break;
    }
    case "audit": {
      const bad = (ev.audit || []).filter((a) => a.status === "untraced");
      body = `<div class="text">${bad.length ? '<span class="badge bad">untraced</span>' : '<span class="badge good">all traced</span>'}${esc(ev.text)}</div>
        ${bad.length ? `<ul>${bad.map((a) => `<li>${esc(a.value)} — ${esc(a.statement)}</li>`).join("")}</ul>` : ""}`;
      bad.forEach((a) => addLedger(`Number audit: ${a.value} (“${a.statement}”) not found in any tool output`));
      break;
    }
    case "review": {
      const r = ev.review || {};
      const ok = r.verdict === "accept";
      body = `<div class="card review ${ok ? "accept" : "revise"}"><h4>${ok ? '<span class="badge good">ACCEPTED</span>' : '<span class="badge warn">REVISE</span>'} ${esc(ev.text)}</h4>
        ${(r.issues || []).length ? `<ul>${r.issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : ""}
        ${(r.sop_suggestions || []).length ? `<div class="result">Suggested SOP changes: ${esc(r.sop_suggestions.join(" · "))}</div>` : ""}</div>`;
      if (!ok) (r.issues || []).forEach((i) => addLedger(`Reviewer: ${i}`));
      break;
    }
    case "sop_update": {
      const s = ev.sop || {};
      body = `<div class="card sop-card"><h4>📜 New procedure: ${esc(s.version)} <span class="sub">replaces ${esc(s.previous)}</span></h4>
        <div class="text">${esc(s.reason)}</div><div class="result">changes: <span class="chip">${esc(JSON.stringify(s.changes))}</span></div></div>`;
      break;
    }
    case "send_back":
      body = `<div class="text"><span class="badge warn">sent back</span>${esc(ev.text)}</div>`;
      break;
    case "publish":
      body = `<div class="text"><span class="badge good">published</span>${esc(ev.text)}</div>`;
      break;
    case "bulletin":
      showBulletin(ev.text, ev.markdown);
      body = `<div class="card"><h4>📰 Bulletin published: ${esc(ev.text)}</h4><button class="btn" onclick="document.getElementById('bulletin').classList.remove('hidden')">Read it</button></div>`;
      break;
    case "done":
      setStatus("done");
      return divider(ev.text.toUpperCase());
    case "error":
      body = `<div class="text"><span class="badge bad">error</span>${esc(ev.text)}</div>`;
      break;
    default:
      body = `<div class="text">${esc(ev.text)}</div>`;
  }
  const el = document.createElement("div");
  el.className = `ev ${cls}`;
  el.innerHTML = `<img class="av" src="${c.img}" alt=""><div class="body">${head}${body}</div>`;
  return el;
}

function divider(text) {
  const el = document.createElement("div");
  el.className = "divider";
  el.textContent = text;
  return el;
}

function activity(ev) {
  const map = {
    thought: "thinking…", tool_call: `running ${ev.text}`, finding: "finding submitted",
    review: `verdict: ${ev.review?.verdict}`, audit: ev.text, sop_update: `issued ${ev.sop?.version}`,
    publish: "published a finding", send_back: "sent a study back", bulletin: "wrote the bulletin", assign: "assigning studies",
  };
  return map[ev.kind];
}

function addLedger(text) {
  ledger.push(text);
  $("ledger").innerHTML = ledger.map((t) => `<li>${esc(t)}</li>`).join("");
}

function push(ev) {
  const el = render(ev);
  if (el) {
    $("feed").appendChild(el);
    $("feed").scrollTop = $("feed").scrollHeight;
  }
  follow(ev);
  const actor = ev.actor;
  if (activity(ev)) doing[actor] = activity(ev);
  renderCrew(["thought", "tool_call", "say", "start", "assign"].includes(ev.kind) ? actor : null);
}

// ---------- bulletin ----------
function md(text) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\*(.+?)\*/g, "<i>$1</i>");
  let html = "", list = false;
  for (const line of String(text || "").split("\n")) {
    const l = line.trim();
    if (/^[-*] /.test(l)) { if (!list) { html += "<ul>"; list = true; } html += `<li>${inline(l.slice(2))}</li>`; continue; }
    if (list) { html += "</ul>"; list = false; }
    if (/^#{1,3} /.test(l)) html += `<h2>${inline(l.replace(/^#+ /, ""))}</h2>`;
    else if (l) html += `<p>${inline(l)}</p>`;
  }
  return html + (list ? "</ul>" : "");
}

function showBulletin(title, markdown) {
  $("bulletin").innerHTML = `<button class="btn close" onclick="this.parentElement.classList.add('hidden')">Close</button>
    <div class="head"><img src="/assets/capy-director.svg" alt=""><h1>${esc(title)}</h1></div>${md(markdown)}`;
}

// ---------- study panel: Lake Mead series, or one fire's burn map ----------
let fires = {}, view = "lake-mead", fireKind = "overlay";
const studies = {}; // study id -> study (from assign events), to know which panel an event belongs to
const sopNow = { water: "water-sop-1", fire: "fire-sop-1" };

const scoped = (url) => (session ? `${url}?session=${encodeURIComponent(session)}` : url);

async function loadStudy() {
  [study, fires] = await Promise.all([fetch(scoped("/api/study/lake-mead")).then((r) => r.json()), fetch(scoped("/api/fires")).then((r) => r.json())]);
  activeKey = null;
  showWater(study.versions[0]?.key);
}

async function refreshFires() {
  fires = await (await fetch(scoped("/api/fires"))).json();
  renderSwitch();
}

async function refreshStudy() {
  study = await (await fetch(scoped("/api/study/lake-mead"))).json();
}

const fireSlug = (target) => {
  const [name, state] = String(target).split(",").map((s) => s.trim());
  return `${name.toLowerCase().replace(/ /g, "-")}-${(state || "").toLowerCase()}`;
};

function renderSwitch() {
  const items = [["lake-mead", "💧 Lake Mead"]].concat(Object.entries(fires).map(([slug, f]) => [slug, `🔥 ${f.fire.name} Fire`]));
  $("switch").innerHTML = items.map(([id, label]) => `<button data-id="${id}" class="${id === view ? "on" : ""}">${label}</button>`).join("");
  $("switch").querySelectorAll("button").forEach((b) => (b.onclick = () => (b.dataset.id === "lake-mead" ? showWater() : showFire(b.dataset.id))));
}

function versionTabs(versions, key, pick) {
  $("tabs").innerHTML = versions.map((v) => `<button class="tab ${v.key === key ? "on" : ""}" data-key="${v.key}">${v.version}</button>`).join("");
  $("tabs").querySelectorAll(".tab").forEach((b) => (b.onclick = () => pick(b.dataset.key)));
}

function stats(rows) {
  $("stats").innerHTML = rows.map(([k, val]) => `<div class="stat"><div class="k">${k}</div><div class="v">${val}</div></div>`).join("");
}

function selectVersion(version) {
  if (version.startsWith("water")) {
    const v = study?.versions.filter((x) => x.version === version).pop();
    if (v) showWater(v.key);
    return;
  }
  for (const [slug, f] of Object.entries(fires)) {
    const v = f.versions.filter((x) => x.version === version).pop();
    if (v) return showFire(slug, v.key);
  }
}

function showWater(key) {
  view = "lake-mead";
  renderSwitch();
  $("water-view").classList.remove("hidden");
  $("fire-view").classList.add("hidden");
  $("study-title").textContent = study.name;
  activeKey = key || activeKey || study.versions[0]?.key;
  const v = study.versions.find((x) => x.key === activeKey);
  if (!v) {  // nothing measured yet
    $("tabs").innerHTML = "";
    stats([]);
    $("chart").innerHTML = `<p class="muted">Measurements appear here once the Water Lab has run.</p>`;
    $("timelapse").classList.add("hidden");
    return;
  }
  versionTabs(study.versions, v.key, showWater);
  const pts = v.points, last = pts[pts.length - 1] || {};
  const r = v.grade?.pearson_r;
  stats([
    ["months", pts.length],
    ["latest km²", last.area?.toFixed(1) ?? "–"],
    ["r vs gauge", r == null ? "–" : r.toFixed(3)],
    ["scatter", v.grade?.rmse_km2 == null ? "–" : `±${v.grade.rmse_km2}`],
  ]);
  chart(v);
  const vid = $("timelapse");
  vid.classList.toggle("hidden", !v.timelapse);
  if (!v.timelapse) vid.removeAttribute("src");
  else if (!vid.src.endsWith(v.timelapse)) { vid.src = v.timelapse; vid.play().catch(() => {}); }
}

function showFire(slug, key) {
  const f = fires[slug];
  if (!f) return;
  view = slug;
  renderSwitch();
  $("water-view").classList.add("hidden");
  $("fire-view").classList.remove("hidden");
  $("study-title").textContent = `${f.fire.name} Fire, ${f.fire.state} · officially ${f.fire.official_acres.toLocaleString()} acres`;
  const v = f.versions.find((x) => x.key === key) || f.versions[f.versions.length - 1];
  versionTabs(f.versions, v.key, (k) => showFire(slug, k));
  const m = v.measurement;
  stats([
    ["burned km²", m.burned_km2.toFixed(0)],
    ["IoU vs perimeter", m.iou.toFixed(3)],
    ["precision", m.precision.toFixed(2)],
    ["outside perim. km²", m.burned_outside_perimeter_km2.toFixed(0)],
  ]);
  const show = (kind) => {
    fireKind = kind;
    $("fire-img").src = v.evidence[kind];
    $("fire-kinds").querySelectorAll("img").forEach((i) => i.classList.toggle("on", i.dataset.kind === kind));
  };
  $("fire-kinds").innerHTML = Object.entries(v.evidence).map(([k, src]) => `<img src="${src}" data-kind="${k}" title="${k}">`).join("");
  $("fire-kinds").querySelectorAll("img").forEach((i) => (i.onclick = () => show(i.dataset.kind)));
  show(v.evidence[fireKind] ? fireKind : "overlay");
}

function follow(ev) {
  // Keep the study panel on whatever the institute is working on.
  const s = studies[ev.tag || ev.study_id];
  if (ev.kind === "sop_update") {
    sopNow[ev.sop.lab] = ev.sop.version;
    renderSop();
    (ev.sop.lab === "fire" ? refreshFires() : refreshStudy()).then(() => selectVersion(ev.sop.version));
  } else if (s && ["finding", "review"].includes(ev.kind)) {
    // Show the procedure in force at this point of the run, not the session's final one.
    if (s.lab === "water") refreshStudy().then(() => selectVersion(sopNow.water));
    else refreshFires().then(() => {
      const slug = fireSlug(s.target);
      showFire(slug, fires[slug]?.versions.filter((v) => v.version === sopNow.fire).pop()?.key);
    });
  }
}

function renderSop() {
  $("sop").innerHTML = `Procedures in force<br>💧 Water Lab: <b>${esc(sopNow.water)}</b><br>🔥 Fire Lab: <b>${esc(sopNow.fire)}</b>`;
}

function chart(v) {
  const W = 440, H = 210, L = 38, R = 40, T = 10, B = 24;
  const pts = v.points;
  if (!pts.length) { $("chart").innerHTML = ""; return; }
  const t = (d) => new Date(d).getTime();
  const x0 = t(pts[0].date), x1 = t(pts[pts.length - 1].date);
  const truth = study.truth.filter((p) => t(p.date) >= x0 - 2.6e9 && t(p.date) <= x1 + 2.6e9);
  const lo = Math.min(...pts.map((p) => p.lo)) - 10, hi = Math.max(...pts.map((p) => p.hi)) + 10;
  const e0 = Math.min(...truth.map((p) => p.elev)) - 3, e1 = Math.max(...truth.map((p) => p.elev)) + 3;
  const X = (d) => L + ((t(d) - x0) / (x1 - x0)) * (W - L - R);
  const Y = (a) => T + (1 - (a - lo) / (hi - lo)) * (H - T - B);
  const Ye = (e) => T + (1 - (e - e0) / (e1 - e0)) * (H - T - B);
  const outliers = new Set((v.grade?.outliers || []).map((o) => o.date));
  const band = pts.map((p) => `${X(p.date)},${Y(p.hi)}`).concat(pts.slice().reverse().map((p) => `${X(p.date)},${Y(p.lo)}`)).join(" ");
  const line = pts.map((p) => `${X(p.date)},${Y(p.area)}`).join(" ");
  const tline = truth.map((p) => `${X(p.date)},${Ye(p.elev)}`).join(" ");
  let years = "";
  for (let y = new Date(x0).getFullYear() + 1; y <= new Date(x1).getFullYear(); y++) {
    const xx = X(`${y}-01-01`);
    years += `<line x1="${xx}" x2="${xx}" y1="${T}" y2="${H - B}" stroke="#1d2b44"/><text x="${xx}" y="${H - 8}" fill="#8aa3c2" font-size="10" text-anchor="middle">${y}</text>`;
  }
  const ticks = [lo + 10, (lo + hi) / 2, hi - 10].map((a) => `<text x="${L - 4}" y="${Y(a) + 3}" fill="#22d3ee" font-size="10" text-anchor="end">${a.toFixed(0)}</text>`).join("");
  const eticks = [e0 + 3, (e0 + e1) / 2, e1 - 3].map((e) => `<text x="${W - R + 4}" y="${Ye(e) + 3}" fill="#fbbf24" font-size="10">${e.toFixed(0)}</text>`).join("");
  const dots = pts.map((p, i) => `<circle cx="${X(p.date)}" cy="${Y(p.area)}" r="${outliers.has(p.date) ? 4 : 2.2}" fill="${outliers.has(p.date) ? "#f472b6" : "#22d3ee"}" data-i="${i}"/>`).join("");
  $("chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}">
    ${years}${ticks}${eticks}
    <polygon points="${band}" fill="#22d3ee" opacity=".14"/>
    <polyline points="${tline}" fill="none" stroke="#fbbf24" stroke-width="1.6" stroke-dasharray="4 3"/>
    <polyline points="${line}" fill="none" stroke="#22d3ee" stroke-width="2"/>${dots}
    <text x="${L}" y="${T + 10}" fill="#22d3ee" font-size="10">water area, km² (satellite)</text>
    <text x="${W - R}" y="${T + 10}" fill="#fbbf24" font-size="10" text-anchor="end">elevation, ft (USBR gauge)</text>
  </svg>`;
  $("chart").querySelectorAll("circle").forEach((c) => {
    const p = pts[c.dataset.i];
    c.onmouseenter = (e) => {
      $("tip").innerHTML = `<b>${p.date}</b> · ${p.area.toFixed(1)} km²${p.warnings?.length ? ` · ⚠ ${esc(p.warnings[0])}` : ""}<img src="${p.evidence}">`;
      $("tip").style.left = Math.min(e.clientX + 12, innerWidth - 290) + "px";
      $("tip").style.top = Math.max(e.clientY - 200, 10) + "px";
      $("tip").classList.remove("hidden");
    };
    c.onmouseleave = () => $("tip").classList.add("hidden");
  });
}

// ---------- session plumbing ----------
function setStatus(s) {
  $("status").className = `pill ${s}`;
  $("status").textContent = { live: "● live", replay: "▶ replay" }[s] || s;
}

function reset() {
  cursor = 0; ledger = []; models = {};
  Object.keys(doing).forEach((k) => delete doing[k]);
  $("feed").innerHTML = "";
  $("ledger").innerHTML = '<li class="muted">Nothing caught yet.</li>';
  Object.assign(sopNow, { water: "water-sop-1", fire: "fire-sop-1" });
  renderSop();
  renderCrew(null);
}

async function poll() {
  const r = await (await fetch(`/api/sessions/${session}/events?after=${cursor}`)).json();
  r.events.forEach(push);
  cursor = r.next;
  if (!r.events.some((e) => e.kind === "done")) pollTimer = setTimeout(poll, 1000);
}

async function play() {
  const r = await (await fetch(`/api/sessions/${session}/events`)).json();
  setStatus("replay");  // a recorded cycle played back, never shown as live
  let prev = r.events[0]?.ts || 0;
  for (const ev of r.events) {
    const gap = Math.min(2600, Math.max(140, ((ev.ts - prev) * 1000) / speed));
    prev = ev.ts;
    await new Promise((res) => setTimeout(res, gap));
    push(ev);
  }
}

// Scripted replay for recording the demo: the recorder plays event ranges over exact durations.
async function manual() {
  const r = await (await fetch(`/api/sessions/${session}/events`)).json();
  let i = 0;
  setStatus("replay");
  window.orbital = {
    events: r.events,
    showBulletin: () => $("bulletin").classList.remove("hidden"),
    hideBulletin: () => $("bulletin").classList.add("hidden"),
    async playTo(end, ms) {
      const n = Math.max(1, end - i);
      const gap = ms / n;
      while (i < end) {
        push(r.events[i++]);
        await new Promise((res) => setTimeout(res, gap));
      }
    },
  };
}

async function open(sid) {
  clearTimeout(pollTimer);
  session = sid;
  reset();
  await loadStudy();
  if (!sid) return welcome();
  if (params.get("replay") === "manual") return manual();
  if (replay) return play();
  setStatus("live");
  poll();
}

function welcome() {
  $("feed").innerHTML = `<div class="ev"><img class="av" src="/assets/capy-astronaut.svg" alt=""><div class="body">
    <div class="card"><h4>No research cycles yet</h4>
    Press <b>Run a new cycle</b> and the crew takes the standing briefing: how Lake Mead's surface has changed since
    2018, checked against the official gauge, and the burn scar of this summer's Tartar Fire in Idaho. Every thought,
    tool call, image and check appears here as it happens.
    <p class="muted">The first cycle measures 106 months of Sentinel-2 imagery from scratch, so it takes a while;
    later cycles reuse those measurements. Needs <code>BASETEN_API_KEY</code>; the SAM 2.1 cross-check needs
    <code>uv sync --extra sam</code>.</p></div></div></div>`;
}

async function init() {
  renderCrew(null);
  const list = await (await fetch("/api/sessions")).json();
  $("session").innerHTML = list.map((s) => `<option value="${s.id}">${s.id}${s.done ? "" : s.stopped ? " (stopped)" : " (running)"}</option>`).join("");
  $("session").classList.toggle("hidden", !list.length);
  session = session || list[0]?.id;
  if (session) $("session").value = session;
  $("session").onchange = (e) => open(e.target.value);
  $("run").onclick = async () => {
    $("run").disabled = true;
    const r = await fetch("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (!r.ok) {
      const why = (await r.json().catch(() => ({}))).detail || `could not start a cycle (${r.status})`;
      $("feed").insertAdjacentHTML("afterbegin", `<div class="ev"><img class="av" src="/assets/capy-astronaut.svg" alt="">
        <div class="body"><div class="card"><h4>Not started</h4>${esc(why)}</div></div></div>`);
      $("run").disabled = false;
      return;
    }
    const { id } = await r.json();
    $("session").classList.remove("hidden");
    $("session").insertAdjacentHTML("afterbegin", `<option value="${id}">${id} (running)</option>`);
    $("session").value = id;
    open(id);
  };
  open(session);
}

init();
