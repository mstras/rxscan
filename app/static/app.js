/* RxScan 1.1 front end. Vanilla JS, no build step. */
"use strict";
const $ = (id) => document.getElementById(id);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem("rxscan." + k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("rxscan." + k, JSON.stringify(v)); } catch {} },
};
const S = { me: null, images: [], rows: [], busy: false, view: "scan", notice: null, users: null, vision: null };
const PRIMARY_EDIT = ["medication", "strength", "dosage_form", "quantity", "days_supply", "refills_remaining", "date_filled",
  "prescriber", "patient_paid", "insurance_paid", "pharmacy_name", "notes"];
const ADMIN_VIEWS = ["people", "reading", "google"];
const TITLES = { review: "Review", settings: "Settings", people: "People", reading: "Reading receipts", google: "Google setup" };

/* ---------------- basics ---------------- */
async function api(path, opts = {}) {
  let r;
  try { r = await fetch(path, opts); }
  catch { const e = new Error("You're offline, or the server can't be reached."); e.offline = true; throw e; }
  let body = null;
  try { body = await r.json(); } catch {}
  if (r.status === 401 && body?.login) {
    saveDraft(true);
    location.assign("/login?next=" + encodeURIComponent(location.pathname + location.hash));
    throw new Error("Signed out. Taking you to sign in…");
  }
  if (!r.ok) {
    const e = new Error(typeof body?.detail === "string" ? body.detail : `Something went wrong (HTTP ${r.status}).`);
    e.reconnect = !!body?.reconnect; e.status = r.status; throw e;
  }
  return body;
}
const post = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data ?? {}) });
const put = (path, data) => api(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data ?? {}) });

function toast(msg, actLabel, act, sticky) {
  $("toastMsg").textContent = msg;
  $("toastAct").hidden = !actLabel; $("toastAct").textContent = actLabel || "";
  $("toastAct").onclick = () => { $("toast").hidden = true; act && act(); };
  $("toast").hidden = false;
  clearTimeout(toast.t);
  if (!actLabel && !sticky) toast.t = setTimeout(() => ($("toast").hidden = true), 4500);
}
$("toastX").onclick = () => ($("toast").hidden = true);

function handleError(e, statusEl) {
  if (e.reconnect) {
    toast("RxScan needs you to sign in with Google again.", "Sign in", () => location.assign("/auth/google/start?consent=1&next=" + encodeURIComponent(location.hash || "/")), true);
  }
  if (statusEl) setStatus(statusEl, e.message, "err"); else if (!e.reconnect) toast(e.message);
}
function setStatus(el, msg, kind = "") { el.textContent = msg || ""; el.className = "status " + kind; }

const fmtDate = (d) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d || "");
  if (!m) return d || "";
  return new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
};
const money = (v) => { const n = parseFloat(String(v || "").replace(/[$,]/g, "")); return isNaN(n) ? "" : "$" + n.toFixed(2); };
const header = (k) => S.me?.fields.find((f) => f.key === k)?.header || k;
const isGoogle = () => S.me?.user.kind === "google";
const isAdmin = () => S.me?.user.role === "admin";
const isDesktop = () => matchMedia("(min-width: 900px)").matches;

/* ---------------- routing ---------------- */
function route() {
  let v = (location.hash || "#scan").slice(1) || "scan";
  if (!["scan", "review", "settings", ...ADMIN_VIEWS].includes(v)) v = "scan";
  if (ADMIN_VIEWS.includes(v) && !isAdmin()) v = "settings";
  if (v === "review" && isDesktop()) v = "scan";
  S.view = v;
  document.body.dataset.view = v;
  document.body.classList.toggle("sub", v !== "scan");
  $("topTitle").textContent = TITLES[v] || "";
  const main = v === "review" ? "scan" : v;
  $$("main.view").forEach((m) => (m.hidden = m.id !== "v-" + main));
  $$(".side a[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === (v === "review" ? "scan" : v)));
  $("bar").hidden = !["scan", "review"].includes(v);
  ({ settings: renderSettings, people: loadPeople, reading: loadVision, google: loadGoogle }[v] || (() => {}))();
  updateBar();
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
matchMedia("(min-width: 900px)").addEventListener("change", route);
$("backBtn").onclick = () => (S.view === "review" ? (location.hash = "#scan") : history.length > 1 ? history.back() : (location.hash = "#scan"));

/* ---------------- who am I ---------------- */
function renderUser() {
  const u = S.me.user;
  $$("[data-name]").forEach((el) => (el.textContent = u.name || u.email || "Admin"));
  $$("[data-email]").forEach((el) => (el.textContent = u.kind === "local" ? "Signed in with the admin password" : u.email));
  $$("[data-avatar]").forEach((el) => {
    el.innerHTML = u.picture ? `<img src="${esc(u.picture)}" alt="" referrerpolicy="no-referrer">` : esc((u.name || u.email || "A")[0].toUpperCase());
  });
  $$(".adminnav").forEach((n) => (n.hidden = !isAdmin()));
  $("rolePill").hidden = !isAdmin();
  $("adminLinks").hidden = !isAdmin();
}

async function firstRunSetup() {
  if (!isGoogle() || !S.me.needs_setup) return;
  showNotice("wait", "Setting up your sheet and photo folder…", "This takes a few seconds, only the first time.");
  try {
    const r = await post("/api/me/setup");
    S.me.settings = r.settings; S.me.needs_setup = false;
    const first = (S.me.user.name || "").split(" ")[0] || "there";
    const parts = [];
    if (r.created.sheet) parts.push(`a sheet called “${r.created.sheet}”`);
    if (r.created.folder) parts.push(`a “${r.created.folder}” folder`);
    showNotice("ok", `You're set up, ${first}.`, parts.length ? `We made ${parts.join(" and ")} in your Drive. Change either below.` : "");
    renderDest();
  } catch (e) { showNotice("wait", "Couldn't finish setting up.", e.message); handleError(e, null); }
}

function showNotice(kind, title, text, html) {
  const icon = kind === "ok" ? "i-check" : "i-clock";
  $("notices").innerHTML = `<div class="notice ${kind}"><svg><use href="#${icon}"/></svg><div><strong>${esc(title)}</strong>${text || html ? `<p>${html || esc(text)}</p>` : ""}</div></div>`;
}
function clearNotice() { $("notices").innerHTML = ""; }

/* ---------------- where things go ---------------- */
function legacySheet() { return store.get("legacySheet", null); }
function renderDest() {
  let rows = "", folder = "";
  if (isGoogle()) {
    const st = S.me.settings || {};
    rows = st.sheet_title ? `${st.sheet_title} › ${st.tab || "Prescriptions"}` : "Not chosen yet";
    folder = st.folder_name ? `${st.folder_name}${st.year_folders ? " › " + new Date().getFullYear() : ""}` : "Not chosen yet";
  } else {
    const ls = legacySheet();
    rows = ls ? `${ls.title} › ${ls.tab}` : "Choose in Google setup";
    folder = S.me.legacy?.receipts?.storage === "off" ? "Not saved" : (S.me.legacy?.receipts?.folder || "Server");
  }
  const chg = isGoogle() ? "#settings" : (isAdmin() ? "#google" : "#settings");
  $("dest").innerHTML = `
    <div class="dest"><div class="ic"><svg><use href="#i-sheet"/></svg></div><span class="k">Rows go to</span><a class="chg" href="${chg}">Change</a><span class="v">${esc(rows)}</span></div>
    <div class="dest"><div class="ic"><svg><use href="#i-folder"/></svg></div><span class="k">Receipt photos go to</span><a class="chg" href="${chg}">Change</a><span class="v">${esc(folder)}</span></div>`;
}

/* ---------------- photos & parsing ---------------- */
const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()));
function addFiles(list) {
  let n = 0;
  for (const f of list) {
    if (!f.type.startsWith("image/") && !/\.(heic|heif)$/i.test(f.name)) continue;
    const name = f.name && !/^image\.(jpe?g|png)$/i.test(f.name) ? f.name : `photo-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "")}-${S.images.length + 1}.jpg`;
    S.images.push({ id: uid(), file: f, name, url: URL.createObjectURL(f), status: "new" });
    n++;
  }
  if (!n) return;
  clearNotice();
  renderQueue(); updateBar(); parseAll();
}

function renderQueue() {
  $("batchBox").hidden = !S.images.length;
  $("batchCount").textContent = S.images.length ? `${S.images.length} photo${S.images.length === 1 ? "" : "s"}` : "";
  const labels = { new: ["busy", "Waiting"], queued: ["wait", "Offline"], parsing: ["busy", "Reading…"], none: ["wait", "No Rx found"], error: ["err", "Didn't read"] };
  $("queue").innerHTML = "";
  S.images.forEach((im, i) => {
    const [cls, txt] = im.status === "done" ? ["ok", `${im.count} found`] : labels[im.status] || ["busy", ""];
    const d = document.createElement("div"); d.className = "shot";
    d.innerHTML = `<img alt="" src="${im.url}"><button class="x" aria-label="Remove photo">✕</button><span class="state ${cls}">${txt}</span>`;
    d.querySelector("img").onerror = (e) => e.target.remove();
    d.querySelector(".x").onclick = () => {
      URL.revokeObjectURL(im.url); S.images.splice(i, 1);
      S.rows = S.rows.filter((r) => r._imgId !== im.id);
      renderQueue(); renderLabels(); updateBar();
    };
    $("queue").appendChild(d);
  });
  const queued = S.images.filter((im) => im.status === "queued").length;
  if (queued && !$("notices").textContent.includes("offline")) showNotice("wait", "You're offline.", `${queued === 1 ? "A photo is" : queued + " photos are"} saved on this device and will be read as soon as you're back online.`);
}

async function parseAll() {
  if (S.busy) return;
  const todo = S.images.filter((im) => ["new", "queued", "error"].includes(im.status));
  if (!todo.length) return;
  if (!navigator.onLine) { todo.forEach((im) => (im.status = "queued")); renderQueue(); updateBar(); return; }
  S.busy = true; updateBar();
  for (const im of todo) {
    im.status = "parsing"; renderQueue();
    try {
      const fd = new FormData(); fd.append("file", im.file, im.name);
      const res = await api("/api/parse", { method: "POST", body: fd });
      im.count = res.rows.length;
      im.status = res.rows.length && res.is_prescription_receipt ? "done" : "none";
      res.rows.forEach((r) => S.rows.push({ ...r, _imgId: im.id, _uncertain: r._uncertain || [] }));
    } catch (e) {
      if (e.offline) { S.images.filter((x) => ["new", "parsing", "error"].includes(x.status)).forEach((x) => (x.status = "queued")); renderQueue(); break; }
      im.status = "error"; im.err = e.message; toast(`${im.name}: ${e.message}`);
    }
    renderQueue(); renderLabels(); saveDraft(true);
  }
  S.busy = false; updateBar();
}

/* ---------------- labels ---------------- */
function renderLabels() {
  const box = $("labels");
  box.innerHTML = "";
  $("labelsEmpty").hidden = !!S.rows.length;
  $("labelsHead").hidden = !S.rows.length;
  S.rows.forEach((row, i) => box.appendChild(labelCard(row, i)));
  const n = S.rows.length, photos = S.images.length;
  $("scanSub").textContent = n ? `${n} prescription${n === 1 ? "" : "s"} from ${photos} photo${photos === 1 ? "" : "s"} ${n === 1 ? "is" : "are"} ready to check.` : "Photograph a receipt to get started.";
}

function labelCard(row, i) {
  const el = document.createElement("article");
  const flagged = (row._uncertain || []).filter((k) => row[k] !== undefined);
  el.className = "rx" + (flagged.length ? " flag" : "");
  const im = S.images.find((x) => x.id === row._imgId);
  const strength = [row.strength, (row.dosage_form || "").toLowerCase()].filter(Boolean).join(" ");
  const flagText = flagged.length ? `${flagged.map(header).join(", ")} ${flagged.length === 1 ? "was" : "were"} hard to read on the photo. Tap Edit to check, or leave ${flagged.length === 1 ? "it" : "them"} as is.` : "";
  const band = `<div class="rx-band"><b>Rx ${esc(row.rx_number || "—")}</b><span>Filled</span><span class="date">${esc(fmtDate(row.date_filled))}</span></div>`;
  if (row._edit) {
    const keys = row._all ? S.me.fields.map((f) => f.key) : PRIMARY_EDIT;
    el.innerHTML = band + `<div class="rx-body"><div class="fields">${keys.map((k) => {
      const full = ["medication", "prescriber", "pharmacy_name", "notes", "pharmacy_address", "insurance_plan"].includes(k) ? " full" : "";
      const cls = (row._uncertain || []).includes(k) ? "flag" : row[k] ? "" : "blank";
      return `<div class="f${full}"><label for="f${i}${k}">${esc(header(k))}</label><input id="f${i}${k}" data-k="${k}" class="${cls}" value="${esc(row[k])}" placeholder="Not on receipt"></div>`;
    }).join("")}</div>
      <div class="rx-tools"><button data-act="done">Done editing</button>${row._all ? "" : `<button data-act="all">All ${S.me.fields.length} fields</button>`}<button class="del" data-act="del">Remove</button></div></div>`;
    $$("input[data-k]", el).forEach((inp) => (inp.oninput = () => {
      const k = inp.dataset.k; row[k] = inp.value;
      row._uncertain = (row._uncertain || []).filter((x) => x !== k);
      inp.className = inp.value ? "" : "blank"; saveDraft();
    }));
  } else {
    const paid = money(row.patient_paid), retail = money(row.retail_price);
    el.innerHTML = band + `${im ? `<a class="rx-thumb" href="${im.url}" target="_blank" rel="noopener" aria-label="Open the receipt photo"><img alt="" src="${im.url}"></a>` : ""}
      <div class="rx-body"><div class="rx-drug">${esc(row.medication || "Unnamed prescription")}</div><div class="rx-strength">${esc(strength)}</div>
      <div class="rx-grid"><div><div class="k">Quantity</div><div class="v">${esc(row.quantity || "—")}</div></div><div><div class="k">Days</div><div class="v">${esc(row.days_supply || "—")}</div></div><div><div class="k">Refills</div><div class="v">${esc(row.refills_remaining || "—")}</div></div></div>
      <div class="rx-who">${row.pharmacy_name ? `<span><strong>${esc(row.pharmacy_name)}</strong></span>` : ""}${row.prescriber ? `<span>Prescribed by ${esc(row.prescriber)}</span>` : ""}</div>
      <div class="rx-pay"><span class="k">You paid</span><span><div class="v${paid ? "" : " none"}">${paid || "—"}</div>${retail ? `<div class="sub">Retail ${retail}</div>` : ""}</span></div>
      ${flagText ? `<div class="flagnote"><b>Check this</b><span>${esc(flagText)}</span></div>` : ""}
      <div class="rx-tools"><button data-act="edit">Edit</button><button data-act="allv">All ${S.me.fields.length} fields</button><button class="del" data-act="del">Remove</button></div></div>`;
  }
  el.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (!act) return;
    if (act === "edit") row._edit = true;
    if (act === "allv" || act === "all") { row._edit = true; row._all = true; }
    if (act === "done") { row._edit = false; row._all = false; }
    if (act === "del") { S.rows.splice(i, 1); }
    renderLabels(); updateBar(); saveDraft();
    if (act !== "del") el.isConnected || $("labels").children[i]?.scrollIntoView({ block: "nearest" });
  });
  return el;
}

/* ---------------- append ---------------- */
function destReady() {
  if (isGoogle()) return !!(S.me.settings?.sheet_id && S.me.settings?.folder_id);
  return !!legacySheet();
}
async function appendAll() {
  if (!S.rows.length || S.busy) return;
  if (!destReady()) { toast(isGoogle() ? "Choose a sheet and photo folder first." : "Choose a sheet in Google setup first.", "Open", () => (location.hash = isGoogle() ? "#settings" : "#google")); return; }
  S.busy = true; updateBar();
  const storageOff = !isGoogle() && S.me.legacy?.receipts?.storage === "off";
  try {
    if (!storageOff) {
      const used = S.images.filter((im) => S.rows.some((r) => r._imgId === im.id) && !im.receiptUrl);
      for (const [n, im] of used.entries()) {
        setBar(`Saving photo ${n + 1} of ${used.length}…`);
        const mine = S.rows.filter((r) => r._imgId === im.id);
        const fd = new FormData();
        fd.append("file", im.file, im.name);
        fd.append("date", mine[0]?.date_filled || "");
        fd.append("pharmacy", mine[0]?.pharmacy_name || "");
        fd.append("medication", mine.map((r) => r.medication).filter(Boolean).join(", "));
        const res = await api("/api/receipts", { method: "POST", body: fd });
        im.receiptUrl = res.url || ""; saveDraft(true);
      }
    }
    setBar("Adding to your sheet…");
    S.rows.forEach((r) => (r.receipt_url = S.images.find((x) => x.id === r._imgId)?.receiptUrl || ""));
    const keys = S.me.all_keys;
    const rows = S.rows.map((r) => Object.fromEntries(keys.map((k) => [k, r[k] ?? ""])));
    const body = { rows };
    if (!isGoogle()) { const ls = legacySheet(); body.sheet = ls.id; body.tab = ls.tab; }
    const res = await post("/api/append", body);
    const n = res.appended, title = res.sheet || legacySheet()?.title || "your sheet";
    const folder = isGoogle() ? `${S.me.settings.folder_name}${S.me.settings.year_folders ? " › " + new Date().getFullYear() : ""}` : "";
    S.images.forEach((im) => URL.revokeObjectURL(im.url));
    S.images = []; S.rows = [];
    renderQueue(); renderLabels(); saveDraft(true);
    showNotice("ok", `Added ${n} prescription${n === 1 ? "" : "s"} to ${title}.`, "",
      `${folder ? `Receipt photos are in ${esc(folder)} and linked from each row. ` : ""}<a href="${esc(res.url)}" target="_blank" rel="noopener">Open sheet</a>`);
    if (S.view === "review") location.hash = "#scan";
  } catch (e) {
    handleError(e, null);
    if (!e.reconnect) toast(e.offline ? "You're offline. Your labels are saved; add them when you're back online." : e.message);
  } finally { S.busy = false; updateBar(); }
}

/* ---------------- bottom bar & header button ---------------- */
function setBar(text) { $("barPrimary").textContent = text; $("appendBtnTop").textContent = text; }
function updateBar() {
  const n = S.rows.length;
  const pending = S.images.some((im) => ["new", "parsing", "queued"].includes(im.status));
  const top = $("appendBtnTop");
  top.disabled = S.busy || !n || !navigator.onLine;
  top.textContent = n ? `Add ${n} to sheet` : "Add to sheet";
  $("clearBtn").hidden = !S.images.length && !n;
  const p = $("barPrimary"), s = $("barSecondary");
  if (S.view === "review") {
    s.hidden = false; s.textContent = "Add photos"; s.onclick = () => (location.hash = "#scan");
    p.disabled = S.busy || !n || !navigator.onLine;
    p.textContent = n ? `Add ${n} to sheet` : "Nothing to add";
    p.onclick = appendAll;
  } else {
    s.hidden = true;
    p.disabled = !n && !pending;
    p.textContent = n ? `Review ${n} prescription${n === 1 ? "" : "s"}` : pending ? "Reading photos…" : "Nothing to review yet";
    p.onclick = () => (location.hash = "#review");
  }
  saveDraft();
}
$("appendBtnTop").onclick = appendAll;
$("clearBtn").onclick = () => { if (!confirm("Clear these photos and labels?")) return; S.images.forEach((im) => URL.revokeObjectURL(im.url)); S.images = []; S.rows = []; renderQueue(); renderLabels(); updateBar(); };

/* ---------------- capture inputs ---------------- */
$("takePhoto").onclick = () => (isDesktop() ? $("files").click() : $("cam").click());
$("pickFiles").onclick = () => $("files").click();
$("cam").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
$("files").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
document.addEventListener("paste", (e) => { const fs = [...(e.clipboardData?.files || [])]; if (fs.length) addFiles(fs); });
function capLabels() {
  const d = isDesktop();
  $("takePhoto").innerHTML = `<svg><use href="#${d ? "i-photos" : "i-camera"}"/></svg>${d ? "Choose photos" : "Take photo"}`;
  $("pickFiles").innerHTML = `<svg><use href="#${d ? "i-camera" : "i-photos"}"/></svg>${d ? "Use camera" : "Choose photos"}`;
  $("pickFiles").onclick = () => (d ? $("cam").click() : $("files").click());
  $("dropHint").hidden = !d;
}
matchMedia("(min-width: 900px)").addEventListener("change", capLabels);
["dragenter", "dragover"].forEach((t) => document.addEventListener(t, (e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); document.body.classList.add("dropping"); } }));
["dragleave", "drop"].forEach((t) => document.addEventListener(t, (e) => { if (t === "drop" || e.target === document.documentElement) document.body.classList.remove("dropping"); }));
document.addEventListener("drop", (e) => { if (e.dataTransfer?.files?.length) { e.preventDefault(); addFiles(e.dataTransfer.files); location.hash = "#scan"; } });

/* ---------------- drafts (IndexedDB) ---------------- */
let saveTimer = null, restoring = false;
function saveDraft(now) {
  if (restoring || !self.RxDB || !S.me) return;
  clearTimeout(saveTimer);
  const run = async () => {
    const key = "draft:" + (S.me.user.email || "admin");
    const draft = {
      images: S.images.map(({ id, name, file, status, count, receiptUrl }) => ({ id, name, blob: file, status: status === "parsing" ? "new" : status, count, receiptUrl })),
      rows: S.rows.map((r) => { const { _edit, _all, ...rest } = r; return rest; }),
    };
    try { if (!draft.images.length && !draft.rows.length) await RxDB.del(key); else await RxDB.set(key, draft); } catch {}
    const n = draft.rows.length + draft.images.filter((i) => ["new", "queued", "error"].includes(i.status)).length;
    try { n ? navigator.setAppBadge?.(n) : navigator.clearAppBadge?.(); } catch {}
  };
  if (now) run(); else saveTimer = setTimeout(run, 200);
}
async function restoreDraft() {
  if (!self.RxDB) return;
  restoring = true;
  try {
    const d = await RxDB.get("draft:" + (S.me.user.email || "admin"));
    if (d && (d.images?.length || d.rows?.length)) {
      S.images = (d.images || []).filter((i) => i.blob).map((i) => ({ ...i, file: i.blob, url: URL.createObjectURL(i.blob) }));
      S.rows = d.rows || [];
    }
  } catch {}
  restoring = false;
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") saveDraft(true); else retryQueued(); });
window.addEventListener("pagehide", () => saveDraft(true));
function retryQueued() { if (navigator.onLine && !S.busy && S.images.some((im) => im.status === "queued")) { clearNotice(); parseAll(); } }
setInterval(retryQueued, 30000);
function setOnline() { $("offlinePill").hidden = navigator.onLine; retryQueued(); updateBar(); }
window.addEventListener("online", setOnline); window.addEventListener("offline", setOnline);

/* ---------------- Google Picker ---------------- */
let pickerLoaded = null;
function loadPicker() {
  pickerLoaded = pickerLoaded || new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "https://apis.google.com/js/api.js";
    s.onload = () => gapi.load("picker", { callback: res, onerror: () => rej(new Error("Couldn't load the Google Picker.")) });
    s.onerror = () => { pickerLoaded = null; rej(new Error("Couldn't load the Google Picker. Check your connection.")); };
    document.head.appendChild(s);
  });
  return pickerLoaded;
}
async function pick(kind) {
  const [cfg] = await Promise.all([api("/api/me/picker"), loadPicker()]);
  return new Promise((resolve) => {
    const P = google.picker;
    const view = kind === "sheet"
      ? new P.DocsView(P.ViewId.SPREADSHEETS).setMode(P.DocsViewMode.LIST)
      : new P.DocsView(P.ViewId.FOLDERS).setIncludeFolders(true).setSelectFolderEnabled(true).setMimeTypes("application/vnd.google-apps.folder");
    new P.PickerBuilder()
      .addView(view)
      .setOAuthToken(cfg.token).setDeveloperKey(cfg.api_key).setAppId(cfg.app_id)
      .setTitle(kind === "sheet" ? "Choose a sheet for your prescriptions" : "Choose a folder for receipt photos")
      .setCallback((d) => {
        if (d.action === P.Action.PICKED) resolve(d.docs[0]);
        else if (d.action === P.Action.CANCEL) resolve(null);
      })
      .build().setVisible(true);
  });
}

/* ---------------- your settings ---------------- */
async function renderSettings() {
  const g = isGoogle();
  const st = S.me.settings || {};
  if (g) {
    $("sheetGroup").innerHTML = `
      <div class="row"><div class="main"><div class="t">${esc(st.sheet_title || "No sheet yet")}</div><div class="s">${st.sheet_id ? `<a href="https://docs.google.com/spreadsheets/d/${esc(st.sheet_id)}/edit" target="_blank" rel="noopener">Open in Google Sheets</a>` : "Rows from your receipts go here"}</div></div><button class="act" id="chgSheet">Change</button></div>
      <div class="row" ${st.sheet_id ? "" : "hidden"}><div class="main"><div class="t">Tab</div><div class="s">Which tab rows are added to</div></div><select id="tabSel" aria-label="Tab"><option>${esc(st.tab || "Prescriptions")}</option></select></div>
      <div class="row"><div class="main"><div class="t">Make a new sheet</div><div class="s">Starts with the column headers already in place</div></div><button class="act" id="newSheet">Create</button></div>`;
    $("folderGroup").innerHTML = `
      <div class="row"><div class="main"><div class="t">${esc(st.folder_name || "No folder yet")}</div><div class="s">${st.folder_id ? `<a href="https://drive.google.com/drive/folders/${esc(st.folder_id)}" target="_blank" rel="noopener">Open in Google Drive</a>` : "Receipt photos are saved here"}</div></div><button class="act" id="chgFolder">Change</button></div>
      <div class="row"><div class="main"><div class="t">A folder for each year</div><div class="s">${esc(st.folder_name || "Folder")} › ${new Date().getFullYear()}</div></div><button class="toggle ${st.year_folders ? "" : "off"}" id="yearToggle" role="switch" aria-checked="${!!st.year_folders}" aria-label="A folder for each year"></button></div>
      <div class="row"><div class="main"><div class="t">Make a new folder</div><div class="s">In My Drive. You can move it later.</div></div><button class="act" id="newFolder">Create</button></div>`;
    $("chgSheet").onclick = () => change("sheet");
    $("chgFolder").onclick = () => change("folder");
    $("newSheet").onclick = async () => {
      const name = prompt("Name for the new sheet:", `RxScan ${new Date().getFullYear()}`); if (!name) return;
      try { const r = await post("/api/me/sheet/new", { name }); S.me.settings = r.settings; renderSettings(); renderDest(); setStatus($("settingsStatus"), `Rows will go to “${r.settings.sheet_title}”.`, "ok"); } catch (e) { handleError(e, $("settingsStatus")); }
    };
    $("newFolder").onclick = async () => {
      const name = prompt("Name for the new folder:", "RxScan Receipts"); if (!name) return;
      try { const r = await post("/api/me/folder/new", { name }); S.me.settings = r.settings; renderSettings(); renderDest(); setStatus($("settingsStatus"), `Photos will go to “${r.settings.folder_name}”.`, "ok"); } catch (e) { handleError(e, $("settingsStatus")); }
    };
    $("yearToggle").onclick = async () => {
      try { const r = await put("/api/me/settings", { year_folders: !st.year_folders }); S.me.settings = r.settings; renderSettings(); renderDest(); } catch (e) { handleError(e, $("settingsStatus")); }
    };
    if (st.sheet_id) api("/api/me/sheet/tabs").then(({ tabs }) => {
      const sel = $("tabSel"); if (!sel) return;
      sel.innerHTML = tabs.map((t) => `<option ${t === st.tab ? "selected" : ""}>${esc(t)}</option>`).join("");
      sel.onchange = async () => { try { const r = await put("/api/me/settings", { tab: sel.value }); S.me.settings = r.settings; renderDest(); setStatus($("settingsStatus"), `Rows will go to the “${sel.value}” tab.`, "ok"); } catch (e) { handleError(e, $("settingsStatus")); } };
    }).catch(() => {});
  } else {
    const ls = legacySheet();
    const note = `<div class="row"><div class="main"><div class="t">${esc(ls ? ls.title + " › " + ls.tab : "Server-wide sheet")}</div><div class="s">With the admin password, RxScan uses the older server-wide connection.</div></div><a class="act" href="#google">Open</a></div>`;
    $("sheetGroup").innerHTML = note;
    $("folderGroup").innerHTML = `<div class="row"><div class="main"><div class="t">${esc(S.me.legacy?.receipts?.folder || "Server")}</div><div class="s">Set by the older connection</div></div><a class="act" href="#google">Open</a></div>`;
  }
  // admin summaries
  if (isAdmin()) {
    $("visionSummary").textContent = `${({ gemini: "Gemini", anthropic: "Claude", ollama: "Ollama", mock: "Test mode" })[S.me.vision.provider] || S.me.vision.provider} · ${S.me.vision.model}`.replace(" · ", ", ");
    $("googleSummary").textContent = S.me.google.configured ? (S.me.google.picker ? "People can sign in with Google" : "Sign-in works; Picker values missing") : "Not set up yet";
    api("/api/admin/users").then(({ users }) => ($("peopleSummary").textContent = `${users.length} ${users.length === 1 ? "person" : "people"} can sign in`)).catch(() => {});
  }
  // install row
  const standalone = navigator.standalone || matchMedia("(display-mode: standalone)").matches;
  $("installH").hidden = $("installGroup").hidden = standalone;
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  $("installHint").textContent = ios ? "In Safari: Share, then “Add to Home Screen”" : installEvt ? "Opens in its own window, like an app" : "Use your browser's menu: Install app";
  $("installBtn").hidden = !installEvt;
}

async function change(kind) {
  const st = $("settingsStatus");
  if (!S.me.google.picker) { setStatus(st, "Choosing an existing file needs the Google Picker, which isn't set up yet. Use Create instead, or ask the admin to finish Google setup.", "warn"); return; }
  try {
    setStatus(st, "Opening Google…");
    const doc = await pick(kind);
    if (!doc) { setStatus(st, ""); return; }
    const r = await post(kind === "sheet" ? "/api/me/sheet" : "/api/me/folder", { id: doc.id });
    S.me.settings = r.settings; renderSettings(); renderDest();
    setStatus(st, kind === "sheet" ? `Rows will go to “${r.settings.sheet_title}” › ${r.settings.tab}.` : `Photos will go to “${r.settings.folder_name}”.`, "ok");
  } catch (e) { handleError(e, st); }
}

/* ---------------- admin: people ---------------- */
const ago = (t) => {
  if (!t) return "Never";
  const d = new Date(t * 1000), now = new Date();
  if (d.toDateString() === now.toDateString()) return "Today";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};
async function loadPeople() {
  try { const { users } = await api("/api/admin/users"); S.users = users; renderPeople(); }
  catch (e) { handleError(e, $("peopleStatus")); }
}
function renderPeople() {
  const me = S.me.user;
  $("peopleRows").innerHTML = S.users.map((u) => {
    const self_ = me.kind === "google" && me.id === u.id;
    const invited = u.status !== "active";
    const role = u.role === "admin" ? `<span class="pill green">Admin</span>` : invited ? `<span class="pill amber">Invited</span>` : "Member";
    const ini = u.picture ? `<span class="ini avatar"><img src="${esc(u.picture)}" alt="" referrerpolicy="no-referrer"></span>` : `<span class="ini">${esc(invited && !u.name ? "?" : (u.name || u.email)[0].toUpperCase())}</span>`;
    const acts = self_ ? `<span class="muted">You</span>` : `${u.role === "admin" ? `<button class="act" data-role="member" data-id="${u.id}">Make member</button>` : `<button class="act" data-role="admin" data-id="${u.id}">Make admin</button>`}<button class="act red" data-del="${u.id}">Remove</button>`;
    return `<tr><td><div class="who">${ini}<div><div class="t">${esc(u.name || u.email)}</div><div class="muted">${esc(invited && !u.name ? "Hasn't signed in yet" : u.email)}</div></div></div></td>
      <td data-k="Role">${role}</td><td data-k="Sheet">${esc(u.sheet_title || (invited ? "Made on first sign-in" : "—"))}</td><td data-k="Last used">${ago(u.last_seen)}</td><td>${acts}</td></tr>`;
  }).join("");
}
$("peopleRows").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const st = $("peopleStatus");
  try {
    if (b.dataset.del) {
      const u = S.users.find((x) => x.id == b.dataset.del);
      if (!confirm(`Remove ${u.name || u.email}? They'll be signed out. Their sheet and photos stay in their Drive.`)) return;
      S.users = (await api(`/api/admin/users/${u.id}`, { method: "DELETE" })).users;
      setStatus(st, `Removed ${u.email}.`, "ok");
    } else if (b.dataset.role) {
      S.users = (await api(`/api/admin/users/${b.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role: b.dataset.role }) })).users;
      setStatus(st, "Role updated.", "ok");
    }
    renderPeople();
  } catch (err) { handleError(err, st); }
});
$("addForm").onsubmit = async (e) => {
  e.preventDefault();
  const email = $("addEmail").value.trim(); if (!email) return;
  try {
    S.users = (await post("/api/admin/users", { email })).users; renderPeople(); $("addEmail").value = "";
    setStatus($("peopleStatus"), `Added ${email}. They can sign in with Google now.`, "ok");
  } catch (err) { handleError(err, $("peopleStatus")); }
};

/* ---------------- admin: reading receipts ---------------- */
const SUGGEST = {
  gemini: [["gemini-3.8-flash", "recommended"], ["gemini-3.5-flash-lite", "cheapest"], ["gemini-4-argon", "most capable"]],
  anthropic: [["claude-sonnet-5-5", "recommended"], ["claude-haiku-5-5", "cheaper"]],
  ollama: [["qwen2.5vl:7b", ""], ["gemma3:12b", ""], ["llama3.2-vision", ""]],
};
let vProvider = "gemini";
async function loadVision() {
  try { S.vision = await api("/api/settings"); } catch (e) { return handleError(e, $("visionStatus")); }
  const st = S.vision.settings;
  $("providerSeg").innerHTML = S.vision.providers.map((p) => `<span role="tab" tabindex="0" data-p="${p}">${({ gemini: "Gemini", anthropic: "Claude", ollama: "Ollama" })[p]}</span>`).join("");
  $$("#providerSeg span").forEach((sp) => (sp.onclick = sp.onkeydown = (e) => { if (e.type === "keydown" && e.key !== "Enter") return; setProvider(sp.dataset.p); }));
  setProvider(st.vision_provider.value);
  if (S.vision.mock) setStatus($("visionStatus"), "Test mode (MOCK_PARSER) is on: receipts return sample data whatever you choose here.", "warn");
}
function setProvider(p) {
  vProvider = p;
  const st = S.vision.settings;
  $$("#providerSeg span").forEach((sp) => sp.classList.toggle("on", sp.dataset.p === p));
  $("keyField").hidden = p === "ollama"; $("urlField").hidden = p !== "ollama";
  const key = st[p + "_api_key"];
  $("vKey").value = ""; $("vKey").placeholder = key?.set ? `Saved (${key.hint}). Leave blank to keep it.` : "Paste your API key";
  $("vKeySrc").textContent = key?.source === "env" ? "(from .env)" : "";
  $("vUrl").value = st.ollama_url?.value || "";
  $("vModel").value = st[p + "_model"]?.value || "";
  $("vSuggest").innerHTML = SUGGEST[p].map(([id, tag]) => `<button type="button" class="chip" data-m="${id}">${id}${tag ? ` <small>${tag}</small>` : ""}</button>`).join("");
  $$("#vSuggest .chip").forEach((c) => (c.onclick = () => ($("vModel").value = c.dataset.m)));
}
const probe = () => ({ provider: vProvider, model: $("vModel").value.trim(), api_key: $("vKey").value.trim(), url: $("vUrl").value.trim() });
$("vLoad").onclick = async () => {
  try { const { models } = await post("/api/settings/models", probe()); $("vModels").innerHTML = models.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join(""); setStatus($("visionStatus"), `${models.length} models available. Pick one in the Model box.`, "ok"); }
  catch (e) { handleError(e, $("visionStatus")); }
};
$("vTest").onclick = async () => {
  try { const r = await post("/api/settings/test", probe()); setStatus($("visionStatus"), r.message, r.message.startsWith("Connected.") ? "ok" : "warn"); }
  catch (e) { handleError(e, $("visionStatus")); }
};
$("vSave").onclick = async () => {
  const values = { vision_provider: vProvider, [vProvider + "_model"]: $("vModel").value.trim() };
  if (vProvider === "ollama") values.ollama_url = $("vUrl").value.trim(); else if ($("vKey").value.trim()) values[vProvider + "_api_key"] = $("vKey").value.trim();
  try { S.vision = await put("/api/settings", { values }); S.me = await api("/api/me"); setProvider(vProvider); setStatus($("visionStatus"), "Saved.", "ok"); }
  catch (e) { handleError(e, $("visionStatus")); }
};

/* ---------------- admin: Google setup ---------------- */
let G = null;
async function loadGoogle() {
  try { G = await api("/api/admin/google"); } catch (e) { return handleError(e, $("gStatus")); }
  const v = G.values;
  $("gClientId").value = v.google_client_id.value || "";
  $("gSecret").value = ""; $("gSecret").placeholder = v.google_client_secret.set ? `Saved (${v.google_client_secret.hint}). Leave blank to keep it.` : "";
  $("gApiKey").value = v.google_api_key.value || "";
  $("gProject").value = v.google_project_number.value || "";
  const done = (b) => `<span class="pill ${b ? "green" : "amber"}">${b ? "Done" : "To do"}</span>`;
  const filled = [v.google_client_id.value, v.google_client_secret.set, v.google_api_key.value, v.google_project_number.value].filter(Boolean).length;
  $("gSteps").innerHTML = `
    <div class="row"><div class="main"><div class="t">Create a project and turn on three APIs</div><div class="s">APIs &amp; Services › Library: Google Sheets API, Google Drive API, Google Picker API</div></div>${done(G.configured)}</div>
    <div class="row"><div class="main"><div class="t">Set up the consent screen</div><div class="s">Google Auth platform › Branding and Audience: External. Add the scope <b>drive.file</b>, then <b>Publish app</b> so sign-ins don't expire every 7 days.</div></div></div>
    <div class="row"><div class="main"><div class="t">Create a sign-in client</div><div class="s">Clients › Create client › Web application. Authorized JavaScript origin and redirect URI:</div>
      <div class="copyline"><code>${esc(G.origin)}</code><button class="act" data-copy="${esc(G.origin)}">Copy</button></div>
      <div class="copyline"><code>${esc(G.redirect_uri)}</code><button class="act" data-copy="${esc(G.redirect_uri)}">Copy</button></div></div></div>
    <div class="row"><div class="main"><div class="t">Create an API key</div><div class="s">APIs &amp; Services › Credentials. Restrict it to the Google Picker API and this website.</div></div>${done(G.picker)}</div>
    <div class="row"><div class="main"><div class="t">Paste the four values</div><div class="s">Client ID, client secret, API key, project number (on the project's dashboard)</div></div><span class="pill ${filled === 4 ? "green" : "amber"}">${filled} of 4</span></div>`;
  $$("[data-copy]").forEach((b) => (b.onclick = async () => { try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copied"; } catch { prompt("Copy this:", b.dataset.copy); } }));
  $("gNotice").innerHTML = !G.https ? `<div class="notice wait"><svg><use href="#i-clock"/></svg><div><strong>Google needs an https address.</strong><p>Open RxScan through your Pangolin address (for example https://rxscan.strasserco.com) before creating the client, so the addresses above are right. Or set PUBLIC_URL.</p></div></div>`
    : filled < 4 ? `<div class="notice wait"><svg><use href="#i-clock"/></svg><div><strong>${4 - filled} value${4 - filled === 1 ? "" : "s"} left.</strong><p>Until the client ID and secret are saved, sign-in stays on the admin password.</p></div></div>` : "";
  // legacy
  const ls = legacySheet();
  $("legacySheet").value = ls?.url || S.me.legacy?.default_sheet_url || "";
  if (ls) $("legacyTab").innerHTML = `<option>${esc(ls.tab)}</option>`;
  $("legacyBox").hidden = S.me.user.kind !== "local" && !S.me.legacy;
}
$$("[data-gsave]").forEach((b) => (b.onclick = async () => {
  const values = { google_client_id: $("gClientId").value.trim(), google_api_key: $("gApiKey").value.trim(), google_project_number: $("gProject").value.trim().replace(/\D/g, "") };
  if ($("gSecret").value.trim()) values.google_client_secret = $("gSecret").value.trim();
  const st = $("gStatus");
  try {
    await put("/api/settings", { values }); S.me = await api("/api/me");
    setStatus(st, "Saved. Testing…");
    const t = await post("/api/admin/google/test");
    setStatus(st, `${t.message} ${S.me.user.kind === "local" ? "Next: add your Gmail under People, then sign out and sign in with Google." : ""}`, "ok");
  } catch (e) { handleError(e, st); }
  loadGoogle();
}));
$("copyScript").onclick = async () => {
  try {
    const { script } = await post("/api/settings/apps-script");
    $("scriptText").value = script; $("scriptText").hidden = false;
    try { await navigator.clipboard.writeText(script); setStatus($("legacyStatus"), "Script copied. Paste it into a new Apps Script project and deploy it as a web app.", "ok"); }
    catch { $("scriptText").select(); setStatus($("legacyStatus"), "Select the text in the box and copy it.", "warn"); }
  } catch (e) { handleError(e, $("legacyStatus")); }
};
$("bridgeConnect").onclick = async () => {
  const url = $("bridgeUrl").value.trim(); if (!url) return;
  try { const t = await post("/api/settings/sheets-test", { url }); await put("/api/settings", { values: { apps_script_url: url } }); S.me = await api("/api/me"); setStatus($("legacyStatus"), t.message, t.outdated ? "warn" : "ok"); }
  catch (e) { handleError(e, $("legacyStatus")); }
};
$("legacySheet").onchange = async () => {
  const url = $("legacySheet").value.trim(); if (!url) return;
  try {
    const info = await post("/api/sheet", { sheet: url });
    $("legacyTab").innerHTML = info.tabs.map((t) => `<option>${esc(t)}</option>`).join("");
    store.set("legacySheet", { id: info.id, url: info.url, title: info.title, tab: info.tabs[0] });
    setStatus($("legacyStatus"), `Admin-password scans go to “${info.title}”.`, "ok"); renderDest();
  } catch (e) { handleError(e, $("legacyStatus")); }
};
$("legacyTab").onchange = () => { const ls = legacySheet(); if (ls) { ls.tab = $("legacyTab").value; store.set("legacySheet", ls); renderDest(); } };

/* ---------------- PWA: install, updates, share target ---------------- */
let installEvt = null;
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvt = e; if (S.view === "settings") renderSettings(); });
$("installBtn").onclick = async () => { if (!installEvt) return; installEvt.prompt(); await installEvt.userChoice.catch(() => {}); installEvt = null; renderSettings(); };
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).then((reg) => {
    const offer = (w) => toast("A new version of RxScan is ready.", "Reload", () => w.postMessage("skipWaiting"));
    if (reg.waiting && navigator.serviceWorker.controller) offer(reg.waiting);
    reg.addEventListener("updatefound", () => { const w = reg.installing; w?.addEventListener("statechange", () => { if (w.state === "installed" && navigator.serviceWorker.controller) offer(w); }); });
    setInterval(() => reg.update().catch(() => {}), 3600 * 1000);
  }).catch(() => {});
  // Reload only when an update replaces a running version, not on the very first install.
  const hadController = !!navigator.serviceWorker.controller;
  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (hadController && !reloading) { reloading = true; location.reload(); } });
}
navigator.storage?.persist?.().catch(() => {});
async function takeInbox() {
  if (!self.RxDB) return;
  try { const items = await RxDB.inboxTake(); if (items.length) { addFiles(items.map((it) => new File([it.blob], it.name, { type: it.type || it.blob.type || "image/jpeg" }))); toast(`Added ${items.length} shared photo${items.length === 1 ? "" : "s"}.`); } } catch {}
}

/* ---------------- boot ---------------- */
(async () => {
  try { S.me = await api("/api/me"); store.set("me", S.me); }
  catch (e) { S.me = store.get("me", null); if (!S.me) { document.body.innerHTML = `<p class="empty"><b>Can't reach RxScan</b>${esc(e.message)}</p>`; return; } }
  renderUser(); capLabels();
  await restoreDraft();
  renderQueue(); renderLabels(); renderDest();
  route(); setOnline();
  if (navigator.onLine) firstRunSetup();
  const q = new URLSearchParams(location.search);
  await takeInbox();
  if (q.get("action") === "camera") { try { $("cam").click(); } catch {} }
  if (q.toString()) history.replaceState(null, "", "/" + location.hash);
  if (S.images.some((im) => ["new", "queued"].includes(im.status))) parseAll();
  if (isAdmin() && S.me.user.kind === "local" && !S.me.google.configured) toast("Set up Google sign-in so everyone gets their own sheet.", "Set up", () => (location.hash = "#google"));
})();
