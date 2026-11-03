"use strict";
// MOSAIC web app. All dynamic text is inserted with textContent / DOM nodes —
// never innerHTML — so model output and user data cannot inject markup.
const $ = (id) => document.getElementById(id);
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined && text !== null) e.textContent = String(text); if (cls) e.className = cls; return e; };
const pct = (x) => (x === null || x === undefined) ? "—" : Math.round(x * 100) + "%";
const when = (ts) => ts ? new Date(ts * 1000).toLocaleString("ar") : "—";
const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } } };

const ICONS = {
  copy: "M8 8h11v13H8zM5 16H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h11a1 1 0 0 1 1 1v1",
  check: "M5 12l5 5L20 7",
  up: "M7 10v11H3V10zM7 10l4-8a3 3 0 0 1 3 3v4h6a2 2 0 0 1 2 2.3l-1.4 8A2 2 0 0 1 18.6 21H7",
  fix: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
  redo: "M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5",
  edit: "M11 4H4v16h16v-7M18.5 2.5a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4z",
  stop: "M7 7h10v10H7z",
  send: "M12 19V5M5 12l7-7 7 7",
  x: "M18 6L6 18M6 6l12 12",
  speak: "M11 5L6 9H2v6h4l5 4zM15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14",
  file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6",
};
function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "icon");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", ICONS[name]); s.appendChild(p); return s;
}
function iconBtn(name, title, onClick) { const b = el("button"); b.type = "button"; b.title = title; b.setAttribute("aria-label", title); b.appendChild(icon(name)); b.onclick = onClick; return b; }

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt); const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}

// ============================================================ theme
const THEMES = ["auto", "light", "dark"], THEME_LABEL = { auto: "المظهر: تلقائي", light: "المظهر: فاتح", dark: "المظهر: داكن" };
function applyTheme(t) { if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", t); $("themelabel").textContent = THEME_LABEL[t]; }
let theme = store.get("mosaic-theme") || "auto"; applyTheme(theme);
$("themebtn").onclick = () => { theme = THEMES[(THEMES.indexOf(theme) + 1) % 3]; store.set("mosaic-theme", theme); applyTheme(theme); if (VIEW === "dash") renderCharts(); };

// ============================================================ views & sidebar
let VIEW = "chat";
function showView(v) {
  VIEW = v;
  document.querySelectorAll(".view").forEach((s) => s.classList.toggle("on", s.id === "v-" + v));
  document.querySelectorAll("#sidenav button[data-v]").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  $("app").classList.remove("open");
  if (v === "dash" || v === "memory" || v === "teach") refreshDash();
  if (v === "settings") loadSettings();
}
document.querySelectorAll("#sidenav button[data-v]").forEach((b) => b.onclick = () => showView(b.dataset.v));
$("menubtn").onclick = () => $("app").classList.toggle("open");
$("scrim").onclick = () => $("app").classList.remove("open");

// ============================================================ markdown → DOM
function inline(parent, text) {
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g; let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
    const t = m[0];
    if (t[0] === "`") parent.appendChild(el("code", t.slice(1, -1)));
    else if (t.startsWith("**")) parent.appendChild(el("strong", t.slice(2, -2)));
    else if (t[0] === "*") parent.appendChild(el("em", t.slice(1, -1)));
    else { const mm = t.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/); const a = el("a", mm[1]); a.href = mm[2]; a.target = "_blank"; a.rel = "noopener noreferrer"; parent.appendChild(a); }
    last = m.index + t.length;
  }
  if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
}
function codeBlock(lang, code) {
  const wrap = el("div", null, "codeblock"); const bar = el("div", null, "cbar"); bar.appendChild(el("span", lang || "code"));
  const cp = el("button", "نسخ"); cp.onclick = () => copyText(code, cp, "نسخ"); bar.appendChild(cp);
  const pre = el("pre"); pre.appendChild(el("code", code)); wrap.append(bar, pre); return wrap;
}
function renderMd(text) {
  const root = el("div", null, "md"); const lines = String(text).split("\n"); let list = null, i = 0, para = [];
  const flush = () => { if (para.length) { const p = el("p"); inline(p, para.join(" ")); root.appendChild(p); para = []; } };
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^```(\w*)/);
    if (fence) { flush(); list = null; const code = []; i++; while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]); i++; root.appendChild(codeBlock(fence[1], code.join("\n"))); continue; }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      flush(); list = null; const tbl = el("table"); const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const hr = el("tr"); cells(line).forEach((c) => { const th = el("th"); inline(th, c); hr.appendChild(th); }); tbl.appendChild(hr); i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { const tr = el("tr"); cells(lines[i]).forEach((c) => { const td = el("td"); inline(td, c); tr.appendChild(td); }); tbl.appendChild(tr); i++; }
      root.appendChild(tbl); continue;
    }
    const h = line.match(/^(#{1,3})\s+(.*)/), li = line.match(/^\s*(?:[-*•]|(\d+)[.)])\s+(.*)/), bq = line.match(/^>\s?(.*)/);
    if (h) { flush(); list = null; const e = el("h" + h[1].length); inline(e, h[2]); root.appendChild(e); }
    else if (li) { flush(); const tag = li[1] ? "ol" : "ul"; if (!list || list.tagName.toLowerCase() !== tag) { list = el(tag); root.appendChild(list); } const e = el("li"); inline(e, li[2]); list.appendChild(e); }
    else if (bq) { flush(); list = null; const e = el("blockquote"); inline(e, bq[1]); root.appendChild(e); }
    else if (line.trim() === "") { flush(); list = null; }
    else { list = null; para.push(line); }
    i++;
  }
  flush(); return root;
}
async function copyText(text, btn, label) {
  try { await navigator.clipboard.writeText(text); } catch { const t = el("textarea", text); document.body.appendChild(t); t.select(); try { document.execCommand("copy"); } catch { /* ignore */ } t.remove(); }
  if (label) { btn.textContent = "تم النسخ"; setTimeout(() => { btn.textContent = label; }, 1500); }
  else { btn.replaceChildren(icon("check")); btn.classList.add("done"); setTimeout(() => { btn.replaceChildren(icon("copy")); btn.classList.remove("done"); }, 1500); }
}

// ============================================================ model picker & status
const MODELS = [
  ["none|", "بدون نموذج (ذاكرة MOSAIC فقط)"],
  ["anthropic|claude-opus-5-5", "Claude Opus 5.5"],
  ["anthropic|claude-sonnet-5-5", "Claude Sonnet 5.5"],
  ["anthropic|claude-haiku-5-5", "Claude Haiku 5.5"],
];
async function loadStatus() {
  const [st, sets] = await Promise.all([api("/api/chat/status"), api("/api/settings")]);
  const sel = $("modelpick"); sel.replaceChildren();
  const model = sets.llm_backend === "none" ? "" : sets.llm_model || (sets.llm_backend === "anthropic" ? "claude-sonnet-5-5" : "qwen2.5:3b");
  const cur = `${sets.llm_backend}|${model}`;
  const local = sets.llm_backend === "openai_compat" ? model : "qwen2.5:3b";
  const opts = [...MODELS, [`openai_compat|${local}`, `نموذج محلي: ${local}`]];
  if (!opts.some(([v]) => v === cur)) opts.push([cur, cur.replace("|", ": ")]);
  opts.forEach(([v, t]) => { const o = el("option", t); o.value = v; sel.appendChild(o); });
  sel.value = cur;
  $("statusdot").classList.toggle("on", !!st.backend);
  $("statustext").textContent = st.backend ? (st.local ? "يعمل على جهازك" : "متصل") :
    st.configured ? "النموذج غير متاح الآن: أجيب من ذاكرتي" : "بدون نموذج: أجيب من ذاكرتي";
}
$("modelpick").onchange = async (e) => {
  const [backend, model] = e.target.value.split("|");
  try { await api("/api/chat/model", { llm_backend: backend, llm_model: model }); } catch (err) { alert(err.message); }
  loadStatus();
};

// ============================================================ conversations
let CONV = null, CONVS = [];
function groupOf(ts) {
  const d = new Date(ts * 1000), now = new Date(), day = 864e5;
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (d >= start) return "اليوم"; if (d >= start - day) return "أمس"; if (d >= start - 7 * day) return "آخر 7 أيام"; return "أقدم";
}
function renderConvs(list) {
  const box = $("convlist"); box.replaceChildren(); let g = null;
  if (!list.length) box.appendChild(el("p", $("convsearch").value ? "لا نتائج" : "لا توجد محادثات بعد", "note group"));
  list.forEach((c) => {
    const gg = groupOf(c.updated_at); if (gg !== g) { g = gg; box.appendChild(el("div", g, "group")); }
    const it = el("div", null, "conv" + (c.id === CONV ? " on" : "")); it.appendChild(el("span", c.title)); it.title = c.title;
    const ren = iconBtn("edit", "إعادة تسمية", async (e) => { e.stopPropagation(); const t = prompt("اسم المحادثة:", c.title); if (t && t.trim()) { await api("/api/chat/rename", { conversation_id: c.id, title: t }); loadConvs(); } }); ren.className = "act";
    const del = iconBtn("x", "حذف", async (e) => { e.stopPropagation(); if (!confirm("حذف هذه المحادثة نهائيًا؟")) return; await api("/api/chat/delete", { conversation_id: c.id }); if (CONV === c.id) newChat(); loadConvs(); }); del.className = "act";
    it.append(ren, del); it.onclick = () => openConv(c.id); box.appendChild(it);
  });
}
async function loadConvs() {
  const q = $("convsearch").value.trim();
  CONVS = q ? await api("/api/chat/search?q=" + encodeURIComponent(q)) : await api("/api/chat/conversations");
  renderConvs(CONVS);
}
let searchTimer = null; $("convsearch").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadConvs, 200); };

let setThreadVisible = function (on) { $("welcome").hidden = on; $("thread").hidden = !on; };
function newChat() { if (BUSY && CTRL) CTRL.abort(); CONV = null; $("thread").replaceChildren(); setThreadVisible(false); showView("chat"); renderConvs(CONVS); $("msgin").focus(); }
$("newchat").onclick = newChat;
async function openConv(id) {
  CONV = id; showView("chat");
  const msgs = await api("/api/chat/messages?id=" + encodeURIComponent(id));
  const t = $("thread"); t.replaceChildren(); msgs.forEach((m) => t.appendChild(turnNode(m)));
  setThreadVisible(true); markLast(); renderConvs(CONVS); scrollDown(true);
}

// ============================================================ messages
const SRC = (s) => !s ? "" : s.startsWith("mosaic") ? "من ذاكرة MOSAIC · يعمل بدون إنترنت" : s.startsWith("llm:") ? s.slice(4) : s === "none" ? "بدون نموذج" : s === "error" ? "خطأ" : s;
function attsNode(atts) {
  const box = el("div", null, "atts");
  (atts || []).forEach((a) => {
    if (a.kind === "image") { const img = el("img"); img.alt = a.name; img.src = `data:${a.media_type};base64,${a.data}`; box.appendChild(img); }
    else { const f = el("span", null, "file"); f.append(icon("file"), el("span", a.name)); box.appendChild(f); }
  });
  return box;
}
function turnNode(m) {
  const wrap = el("div", null, "turn " + m.role); wrap.dataset.id = m.id || "";
  const atts = typeof m.attachments === "string" ? JSON.parse(m.attachments || "[]") : (m.attachments || []);
  if (m.role === "user") {
    const col = el("div"); col.style.maxWidth = "85%";
    if (atts.length) col.appendChild(attsNode(atts));
    if (m.content) col.appendChild(el("div", m.content, "bubble"));
    wrap.appendChild(col);
    const tools = el("div", null, "msgtools tools");
    if (m.id) tools.appendChild(iconBtn("edit", "تعديل وإعادة الإرسال", () => startEdit(wrap, { ...m, attachments: atts })));
    tools.appendChild(iconBtn("copy", "نسخ", (e) => copyText(m.content, e.currentTarget)));
    wrap.appendChild(tools);
    return wrap;
  }
  const who = el("div", null, "who"); const logo = el("img"); logo.src = "/icon.svg"; logo.alt = ""; who.append(logo, el("span", "MOSAIC"));
  const body = el("div"); body.className = "body"; body.appendChild(renderMd(m.content || ""));
  wrap.append(who, body);
  if (m.id) wrap.appendChild(toolsFor(m));
  return wrap;
}
function toolsFor(m) {
  const t = el("div", null, "msgtools");
  t.appendChild(iconBtn("copy", "نسخ", (e) => copyText(m.content, e.currentTarget)));
  if ("speechSynthesis" in window) t.appendChild(iconBtn("speak", "قراءة بصوت عالٍ", () => speak(m.content)));
  if (m.source !== "error") {
    const up = iconBtn("up", "إجابة جيدة", async () => { await api("/api/chat/rate", { message_id: m.id, good: true }); up.classList.add("done"); });
    if (m.rating === 1) up.classList.add("done");
    const fix = iconBtn("fix", "صحّح الإجابة ليتعلم MOSAIC", async () => {
      const c = prompt("اكتب الإجابة الصحيحة. سأحفظها وأستخدمها لاحقًا حتى بدون إنترنت:");
      if (c && c.trim()) { await api("/api/chat/rate", { message_id: m.id, good: false, correction: c }); fix.classList.add("done"); fix.title = "تعلّمت التصحيح"; }
    });
    t.append(up, fix);
  }
  const re = iconBtn("redo", "إعادة توليد الإجابة", () => regenerate()); re.classList.add("regen"); t.appendChild(re);
  t.appendChild(el("span", SRC(m.source), "src"));
  return t;
}
function markLast() { // only the newest assistant reply can be regenerated
  const turns = [...$("thread").querySelectorAll(".turn.assistant")];
  turns.forEach((t, i) => { const r = t.querySelector(".regen"); if (r) r.hidden = i !== turns.length - 1; });
}
function startEdit(wrap, m) {
  if (BUSY) return;
  const box = el("div", null, "editbox"); box.style.width = "100%";
  const ta = el("textarea"); ta.value = m.content; box.appendChild(ta);
  const btns = el("div", null, "btns");
  const ok = el("button", "إرسال", "btn"); const cancel = el("button", "إلغاء", "btn alt");
  btns.append(ok, cancel); box.appendChild(btns);
  const old = [...wrap.childNodes]; wrap.replaceChildren(box); ta.focus();
  cancel.onclick = () => wrap.replaceChildren(...old);
  ok.onclick = () => {
    const text = ta.value.trim(); if (!text) return;
    let n = wrap.nextSibling; while (n) { const nx = n.nextSibling; n.remove(); n = nx; }
    wrap.replaceWith(turnNode({ role: "user", content: text, id: m.id, attachments: m.attachments }));
    stream("/api/chat/edit", { message_id: m.id, text });
  };
}

// ============================================================ streaming
let BUSY = false, CTRL = null;
function scrollDown(force) {
  const v = $("v-chat"); const near = v.scrollHeight - v.scrollTop - v.clientHeight < 160;
  if (force || near) v.scrollTop = v.scrollHeight;
}
function setBusy(b) {
  BUSY = b; const s = $("sendbtn"); s.replaceChildren(icon(b ? "stop" : "send")); s.title = b ? "إيقاف" : "إرسال";
  s.disabled = !b && !($("msgin").value.trim() || PENDING.length);
}
async function stream(url, body) {
  setThreadVisible(true); setBusy(true);
  const node = turnNode({ role: "assistant", content: "" }); const bodyEl = node.querySelector(".body");
  const dots = el("div", null, "thinking"); dots.append(el("i"), el("i"), el("i")); bodyEl.replaceChildren(dots);
  $("thread").appendChild(node); scrollDown(true);
  CTRL = new AbortController(); let text = "", final = null, raf = 0, aborted = false;
  const paint = () => { raf = 0; const md = renderMd(text); md.classList.add("cursor"); bodyEl.replaceChildren(md); scrollDown(); };
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: CTRL.signal });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || r.status); }
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true }); let k;
      while ((k = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, k); buf = buf.slice(k + 2);
        if (!chunk.startsWith("data:")) continue;
        const ev = JSON.parse(chunk.slice(5));
        if (ev.type === "meta") { if (CONV !== ev.conversation_id) { CONV = ev.conversation_id; loadConvs(); } }
        else if (ev.type === "delta") { text += ev.text; if (!raf) raf = requestAnimationFrame(paint); }
        else if (ev.type === "done") final = ev;
      }
    }
  } catch (err) {
    if (err.name === "AbortError") aborted = true; else text += (text ? "\n\n" : "") + "⚠️ " + err.message;
  }
  if (raf) cancelAnimationFrame(raf);
  CTRL = null; setBusy(false);
  if (final) node.replaceWith(turnNode({ role: "assistant", id: final.message_id, content: final.content, source: final.source }));
  else if (aborted && CONV) { setTimeout(() => CONV && openConv(CONV), 300); } // reload what the server kept
  else bodyEl.replaceChildren(renderMd(text || "⚠️ لم يصل رد"));
  markLast(); loadConvs(); scrollDown();
}
function regenerate() {
  if (BUSY || !CONV) return;
  const turns = $("thread").querySelectorAll(".turn.assistant"); const last = turns[turns.length - 1];
  if (last) last.remove();
  stream("/api/chat/regenerate", { conversation_id: CONV });
}

// ============================================================ composer & attachments
let PENDING = [];
const IMG = ["image/png", "image/jpeg", "image/webp", "image/gif"];
function renderPending() {
  const box = $("pending-atts"); box.replaceChildren();
  PENDING.forEach((a, i) => {
    const p = el("span", null, "patt");
    if (a.kind === "image") { const im = el("img"); im.src = `data:${a.media_type};base64,${a.data}`; im.alt = ""; p.appendChild(im); } else p.appendChild(icon("file"));
    p.appendChild(el("span", a.name)); const x = el("button", "✕"); x.type = "button"; x.title = "إزالة"; x.onclick = () => { PENDING.splice(i, 1); renderPending(); };
    p.appendChild(x); box.appendChild(p);
  });
  setBusy(BUSY);
}
async function addFiles(files) {
  for (const f of files) {
    if (PENDING.length >= 5) { alert("خمسة مرفقات كحد أقصى"); break; }
    if (IMG.includes(f.type)) {
      if (f.size > 5 * 1024 * 1024) { alert(`${f.name}: الصورة أكبر من 5 ميغابايت`); continue; }
      const url = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
      PENDING.push({ kind: "image", name: f.name, media_type: f.type, data: String(url).split(",")[1] });
    } else {
      if (f.size > 300 * 1024) { alert(`${f.name}: الملف النصي أكبر من 300 كيلوبايت`); continue; }
      PENDING.push({ kind: "text", name: f.name, text: await f.text() });
    }
  }
  renderPending();
}
$("attachbtn").onclick = () => $("filein").click();
$("filein").onchange = (e) => { addFiles([...e.target.files]); e.target.value = ""; };
$("msgin").addEventListener("paste", (e) => { const fs = [...((e.clipboardData && e.clipboardData.files) || [])]; if (fs.length) { e.preventDefault(); addFiles(fs); } });
const grow = () => { const t = $("msgin"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 220) + "px"; setBusy(BUSY); };
$("msgin").addEventListener("input", grow);
$("msgin").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); } });
$("composer").onsubmit = (e) => {
  e.preventDefault();
  if (BUSY) { if (CTRL) CTRL.abort(); return; }
  const text = $("msgin").value.trim(); if (!text && !PENDING.length) return;
  const atts = PENDING; PENDING = []; renderPending();
  $("msgin").value = ""; grow(); setThreadVisible(true);
  $("thread").appendChild(turnNode({ role: "user", content: text, attachments: atts }));
  stream("/api/chat/stream", { conversation_id: CONV, text, attachments: atts });
};

const SUGGESTIONS = [
  ["اشرح لي فكرة", "كيف يعمل التعلم المستمر في الذكاء الاصطناعي؟"],
  ["اكتب كودًا", "اكتب دالة بايثون تتحقق من صحة بريد إلكتروني مع أمثلة"],
  ["ساعدني في الكتابة", "صغ رسالة رسمية قصيرة لطلب إجازة من العمل"],
  ["علّمني شيئًا عنك", "تذكر أن اسمي خليل وأنني أطوّر مشروع MOSAIC"],
];
SUGGESTIONS.forEach(([t, q]) => { const b = el("button"); b.type = "button"; b.append(el("b", t), el("span", q)); b.onclick = () => { $("msgin").value = q; grow(); $("msgin").focus(); }; $("suggest").appendChild(b); });
const hour = new Date().getHours(); $("greet").textContent = hour < 12 ? "صباح الخير. كيف أستطيع مساعدتك؟" : hour < 18 ? "مرحبًا. كيف أستطيع مساعدتك اليوم؟" : "مساء الخير. بماذا أساعدك؟";

// ============================================================ dashboard
const TYPES = { A: "معرفة", B: "مهارة", C: "أداة", D: "استراتيجية", E: "تكييف نموذج", F: "تعلم من نموذج آخر" };
const DEC = { accepted: ["معتمد", "ok"], rejected: ["مرفوض", "bad"], pending_approval: ["ينتظر موافقتك", "warn"] };
function table(id, head, rows) {
  const t = $(id); t.replaceChildren(); const tr = el("tr"); head.forEach((h) => tr.appendChild(el("th", h))); t.appendChild(tr);
  if (!rows.length) { const r = el("tr"); const td = el("td", "لا يوجد بعد", "muted"); td.colSpan = head.length; r.appendChild(td); t.appendChild(r); }
  rows.forEach((cells) => { const r = el("tr"); cells.forEach((c) => { const td = el("td"); if (c instanceof Node) td.appendChild(c); else td.textContent = c ?? ""; r.appendChild(td); }); t.appendChild(r); });
}
function bar(x) { const w = el("div", null, "barrow"); const b = el("div", null, "bar"); const i = el("i"); i.style.width = Math.round((x || 0) * 100) + "%"; b.appendChild(i); w.append(b, el("span", pct(x))); return w; }
const chip = (t, c) => el("span", t, "chip " + (c || ""));
function kpis(box, items) { box.replaceChildren(); items.forEach(([l, v]) => { const d = el("div", null, "kpi"); d.append(el("b", v), el("span", l)); box.appendChild(d); }); }

let D = null;
function renderCharts() {
  if (!D) return;
  MosaicCharts.line($("chart-line"), D.timeline, [{ key: "mosaic", label: "MOSAIC", color: "--series-1" }, { key: "teacher", label: "المعلّم", color: "--series-2" }]);
  MosaicCharts.bars($("chart-bars"), D.categories.filter((c) => c.name !== "chat" || c.heldout_n).map((c) => ({ label: c.name, value: c.heldout_acc, n: c.heldout_n, ci: `${pct(c.heldout_ci[0])} إلى ${pct(c.heldout_ci[1])}` })));
}
async function refreshDash() {
  D = await api("/api/dashboard"); const s = D.summary;
  kpis($("kpis"), [["فئات مستقلة عن المعلّم", `${s.graduated} / ${s.categories}`], ["مهارات نشطة", s.active_skills], ["أدوات طوّرها النظام", s.tools],
    ["تحسينات معتمدة", s.experiments.accepted || 0], ["تحسينات مرفوضة", s.experiments.rejected || 0], ["تنتظر موافقتك", s.experiments.pending_approval || 0],
    ["استدعاءات المعلّم", s.teacher_calls], ["تكلفة المعلّم $", s.teacher_spend_usd]]);
  const c = D.chat; kpis($("chatkpis"), [["ردود المساعد", c.assistant_messages], ["أجاب عنها MOSAIC وحده", c.answered_by_mosaic], ["نسبة الاستقلال", pct(c.independence)],
    ["تصحيحات وحقائق تعلّمها", c.learned_facts], ["ملاحظات محفوظة", c.saved_notes], ["تقييمات إيجابية", `${c.positive} / ${c.rated}`]]);
  if (VIEW === "dash") renderCharts();
  table("cats", ["الفئة", "اللغة والمستوى", "الدقة المستقلة", "اعتماد على المعلّم", "المهارة النشطة", "الحالة", ""],
    D.categories.map((c) => {
      const st = c.graduated ? chip("مستقل عن المعلّم", "ok") : c.frozen ? chip("مجمّد بعد فشل متكرر", "bad") : chip("يتعلم", "warn");
      const act = el("div", null, "btns");
      if (c.active_skill) { const b = el("button", "تراجع", "btn alt"); b.onclick = () => api("/api/rollback", { category: c.name }).then(refreshDash); act.appendChild(b); }
      if (c.frozen) { const b = el("button", "استئناف", "btn alt"); b.onclick = () => api("/api/category/unfreeze", { category: c.name }).then(refreshDash); act.appendChild(b); }
      if (c.graduated) { const b = el("button", "السماح بالمعلّم مجددًا", "btn alt"); b.onclick = () => confirm("سيعود النظام لاستدعاء نموذج خارجي لهذه الفئة. متأكد؟") && api("/api/category/ungraduate", { category: c.name }).then(refreshDash); act.appendChild(b); }
      const prog = c.active_skill ? (typeof c.active_skill.program === "string" ? c.active_skill.program : JSON.stringify(c.active_skill.program)) + ` (v${c.active_skill.version})` : "—";
      return [c.name + " — " + c.description, `${c.language} · ${c.language_level}`, bar(c.heldout_acc), bar(c.teacher_share_recent), prog, st, act];
    }));
  table("imps", ["الوقت", "النوع", "الفئة", "السبب", "قبل ← بعد", "التكلفة", "القرار"],
    D.improvements.map((i) => { const r = i.results; const [dl, dc] = DEC[i.decision] || [i.decision, ""];
      return [when(i.created_at), TYPES[i.type] || i.type, i.category, i.reason, r.candidate_acc !== undefined ? `${pct(r.baseline_acc)} ← ${pct(r.candidate_acc)} (n=${r.n})` : "—",
        `${i.cost.teacher_calls} استدعاء · $${i.cost.money_usd}`, chip(dl, dc)]; }));
  table("models", ["النموذج", "مسموح التدريب على مخرجاته", "الاستدعاءات", "الدقة المقاسة", "مرات التحقق"], D.models.map((m) => [m.id, m.allows_training_on_outputs, m.calls, bar(m.measured_accuracy), m.checked]));
  table("sources", ["المصدر", "طريقة الاكتساب", "عدد المعلومات", "متوسط الثقة"], D.knowledge_sources.map((x) => [x.source, x.acquired_mode, x.n, x.avg_conf]));
  table("tools", ["الأداة", "الأصل", "الإصدار"], D.tools.map((t) => [t.name, t.origin, t.version]));
  table("errors", ["الفئة", "النمط", "الحل", "التكرار"], D.errors.map((e) => [e.category, e.pattern, e.fix, e.count]));
  table("clog", ["#", "الوقت", "الفاعل", "النوع", "الكيان"], D.changelog.map((x) => [x.seq, when(x.ts), x.actor, x.change_type, x.entity]));
  table("jobs", ["المهمة", "الفئة", "الأولوية", "الحالة"], D.jobs.map((j) => [j.kind, j.category, Number(j.priority).toFixed(2), j.status]));
  table("vq", ["الفئة", "المدخل", "الادعاء", "السبب"], D.verify_queue.map((v) => [v.category, v.input, v.claim, v.reason]));
  $("device").textContent = JSON.stringify(D.device, null, 2);
  const badge = $("pendingbadge"); badge.hidden = !D.pending.length; badge.textContent = D.pending.length;
  const p = $("pending"); p.replaceChildren();
  if (!D.pending.length) p.appendChild(el("p", "لا توجد تغييرات تنتظر موافقتك.", "muted"));
  D.pending.forEach((i) => {
    const card = el("div", null, "card"); card.append(el("b", `${TYPES[i.type]} — ${i.category}`), el("p", i.target + " · " + i.reason), el("pre", JSON.stringify(i.results, null, 2), "box"));
    const ok = el("button", "اعتماد", "btn"); ok.onclick = () => api("/api/approve", { improvement_id: i.id, accept: true }).then(refreshDash);
    const no = el("button", "رفض", "btn bad"); no.onclick = () => api("/api/approve", { improvement_id: i.id, accept: false }).then(refreshDash);
    const r = el("div", null, "btns"); r.append(ok, no); card.appendChild(r); p.appendChild(card);
  });
  ["tcat", "ecat"].forEach((id) => { const sel = $(id); const v = sel.value; sel.replaceChildren(); D.categories.forEach((x) => { const o = el("option", x.name + " — " + x.description); o.value = x.name; sel.appendChild(o); }); if (v) sel.value = v; });
  loadMemory();
}
let resizeT = null; window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { if (VIEW === "dash") renderCharts(); }, 150); });
$("learn").onclick = async () => {
  $("learnmsg").textContent = "جارٍ التعلم…";
  try { const r = await api("/api/learn", {}); $("learnmsg").textContent = r.skipped ? "تأجيل: " + r.skipped : r.results.map((x) => `${x.category}: ${x.status}`).join(" · ") || "لا شيء يستحق التعلم الآن"; }
  catch (e) { $("learnmsg").textContent = e.message; }
  refreshDash();
};

// ============================================================ memory
async function loadMemory() {
  const prefs = await api("/api/memory"); const box = $("memlist"); box.replaceChildren();
  if (!prefs.length) box.appendChild(el("p", "لا شيء محفوظ بعد.", "muted"));
  prefs.forEach((p) => { const r = el("div", null, "btns"); r.style.justifyContent = "space-between"; r.appendChild(el("span", p.value)); const x = el("button", "حذف", "btn alt"); x.onclick = () => api("/api/memory/delete", { id: p.id }).then(loadMemory); r.appendChild(x); box.appendChild(r); });
}

// ============================================================ teach & tasks
$("tgo").onclick = async () => {
  const out = $("tout"); out.replaceChildren();
  try {
    const r = await api("/api/task", { category: $("tcat").value, input: $("tin").value, private: $("tpriv").checked });
    const c = el("div", null, "card"); c.append(el("p", r.answer ?? "لا أعرف بعد. علّمني الإجابة الصحيحة."), el("p", "الحل من: " + r.solver + (r.cost ? ` · $${r.cost}` : ""), "note"));
    const fix = el("input"); fix.className = "f"; fix.placeholder = "الإجابة الصحيحة (للتصحيح)"; const row = el("div", null, "btns");
    const mk = (t, kind, needs) => { const b = el("button", t, "btn alt"); b.onclick = async () => { try { await api("/api/feedback", { episode_id: r.episode_id, kind, correct_answer: needs ? fix.value : null, note: fix.value }); b.textContent = "✓ " + t; } catch (e) { alert(e.message); } }; return b; };
    row.append(mk("صحيح", "correct"), mk("تصحيح موثوق", "correction", true), mk("رأي شخصي", "opinion"), mk("يحتاج تحقق", "needs_verification", true), mk("غير موثوق", "unreliable"));
    c.append(fix, row); out.appendChild(c);
  } catch (e) { out.appendChild(el("p", e.message, "bad")); }
};
$("cadd").onclick = () => api("/api/category", { name: $("cname").value.trim(), kind: $("ckind").value, description: $("cdesc").value, language: $("clang").value })
  .then(() => { $("cmsg").textContent = "أُضيفت"; refreshDash(); }).catch((e) => { $("cmsg").textContent = e.message; });
$("eadd").onclick = () => {
  const pairs = $("epairs").value.split("\n").map((l) => l.split(/\t|\|/)).filter((p) => p.length >= 2).map((p) => [p[0].trim(), p.slice(1).join("|").trim()]);
  api("/api/examples", { category: $("ecat").value, pairs, split: $("esplit").value || null }).then((r) => { $("emsg").textContent = `تطوير: ${r.dev} · اختبار مستقل: ${r.heldout}`; refreshDash(); }).catch((e) => { $("emsg").textContent = e.message; });
};

// ============================================================ settings
const LABELS = { online: "متصل بالإنترنت", allow_teacher: "السماح بالتعلم من نماذج أخرى", graduation_threshold: "عتبة الاستقلال عن المعلّم", graduation_margin: "هامش المقارنة مع المعلّم",
  min_heldout: "أقل عدد أمثلة للاختبار المستقل", max_regression_drop: "أقصى تراجع مسموح", max_failed_attempts: "محاولات فاشلة قبل التجميد", teacher_budget_per_cycle: "استدعاءات المعلّم لكل دورة",
  money_budget_usd: "الميزانية المالية الكلية للمعلّم $", require_approval_all: "اطلب موافقتي على كل تغيير", approval_cost_usd: "تكلفة تتطلب موافقتي $", episode_limit: "حد الخبرات قبل الأرشفة",
  llm_backend: "النموذج اللغوي للمحادثة", llm_model: "اسم النموذج", llm_base_url: "عنوان خادم النموذج المحلي (متوافق مع OpenAI)",
  allow_cloud_llm: "السماح للنماذج السحابية برؤية محادثاتي", custom_instructions: "تعليمات مخصصة: كيف تريد أن يجيبك MOSAIC دائمًا؟", memory_answer_threshold: "عتبة الإجابة من الذاكرة مباشرة (0–1)" };
async function loadSettings() {
  const s = await api("/api/settings"); const box = $("sets"); box.replaceChildren();
  Object.entries(s).forEach(([key, v]) => {
    box.appendChild(el("label", LABELS[key] || key, "l")); let i;
    if (key === "custom_instructions") { i = el("textarea"); i.className = "f"; i.rows = 4; i.value = v; i.placeholder = "مثال: أجب باختصار، وخاطبني باسم خليل، واستخدم أمثلة برمجية بلغة Python."; }
    else if (key === "llm_backend") { i = el("select"); i.className = "f"; [["none", "بدون نموذج"], ["anthropic", "Claude (يحتاج ANTHROPIC_API_KEY)"], ["openai_compat", "متوافق مع OpenAI: Ollama / llama.cpp / LM Studio"]].forEach(([val, t]) => { const o = el("option", t); o.value = val; i.appendChild(o); }); i.value = v; }
    else { i = el("input"); if (typeof v === "boolean") { i.type = "checkbox"; i.checked = v; } else if (typeof v === "string") { i.type = "text"; i.className = "f"; i.value = v; i.dir = "ltr"; } else { i.type = "number"; i.className = "f"; i.step = "any"; i.value = v; } }
    i.dataset.k = key; i.dataset.kind = typeof v; box.appendChild(i);
  });
}
$("ssave").onclick = () => {
  const body = {}; document.querySelectorAll("#sets input, #sets select, #sets textarea").forEach((i) => { body[i.dataset.k] = i.type === "checkbox" ? i.checked : i.dataset.kind === "string" ? i.value : Number(i.value); });
  api("/api/settings", body).then(() => { $("smsg").textContent = "حُفظت"; loadStatus(); }).catch((e) => { $("smsg").textContent = e.message; });
};

// ============================================================ voice: dictation & read-aloud (browser-native, on-device where supported)
function speak(text) {
  speechSynthesis.cancel();
  const plain = String(text).replace(/```[\s\S]*?```/g, " (كود) ").replace(/[#*`>|_-]/g, " ");
  const u = new SpeechSynthesisUtterance(plain); u.lang = /[\u0600-\u06FF]/.test(plain) ? "ar-SA" : "en-US"; speechSynthesis.speak(u);
}
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if (SR) {
  const mic = $("micbtn"); mic.hidden = false; let rec = null;
  mic.onclick = () => {
    if (rec) { rec.stop(); return; }
    rec = new SR(); rec.lang = "ar-SA"; rec.interimResults = true; rec.continuous = false;
    const base = $("msgin").value ? $("msgin").value + " " : "";
    rec.onresult = (e) => { $("msgin").value = base + [...e.results].map((r) => r[0].transcript).join(""); grow(); };
    rec.onend = () => { rec = null; mic.classList.remove("rec"); };
    rec.onerror = () => { rec = null; mic.classList.remove("rec"); };
    mic.classList.add("rec"); rec.start();
  };
}

// ============================================================ export conversation
$("exportbtn").onclick = async () => {
  if (!CONV) return;
  const msgs = await api("/api/chat/messages?id=" + encodeURIComponent(CONV));
  const title = (CONVS.find((c) => c.id === CONV) || {}).title || "محادثة";
  const md = `# ${title}\n\n` + msgs.map((m) => `**${m.role === "user" ? "أنت" : "MOSAIC"}:**\n\n${m.content}\n`).join("\n---\n\n");
  const a = el("a"); a.href = URL.createObjectURL(new Blob([md], { type: "text/markdown" })); a.download = title.slice(0, 40).replace(/[\\/:*?"<>|]/g, "_") + ".md";
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};
const _setThread = setThreadVisible;
setThreadVisible = function (on) { _setThread(on); $("exportbtn").hidden = !on; };

// ============================================================ inside the Android app
if (window.MosaicApp) {
  const b = el("button"); b.type = "button"; b.appendChild(icon("redo")); b.appendChild(el("span", "إعدادات الاتصال"));
  b.onclick = () => window.MosaicApp.openSetup(); $("sidenav").appendChild(b);
}

// ============================================================ boot
setBusy(false); loadStatus(); loadConvs(); refreshDash();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
