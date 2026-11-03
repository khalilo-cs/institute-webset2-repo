"use strict";
// All dynamic text goes through textContent: no HTML injection from data.
const $ = (id) => document.getElementById(id);
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined && text !== null) e.textContent = String(text); if (cls) e.className = cls; return e; };
const pct = (x) => (x === null || x === undefined) ? "—" : Math.round(x * 100) + "%";
const when = (ts) => ts ? new Date(ts * 1000).toLocaleString("ar") : "—";
const TYPES = { A: "معرفة", B: "مهارة", C: "أداة", D: "استراتيجية", E: "تكييف نموذج", F: "تعلم من نموذج آخر" };
const DEC = { accepted: ["معتمد", "ok"], rejected: ["مرفوض", "bad"], pending_approval: ["ينتظر موافقتك", "warn"], rolled_back: ["تراجع", "warn"] };

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}

function table(id, head, rows) {
  const t = $(id); t.replaceChildren();
  const tr = el("tr"); head.forEach(h => tr.appendChild(el("th", h))); t.appendChild(tr);
  if (!rows.length) { const r = el("tr"); const td = el("td", "لا يوجد بعد", "muted"); td.colSpan = head.length; r.appendChild(td); t.appendChild(r); }
  rows.forEach(cells => { const r = el("tr"); cells.forEach(c => { const td = el("td"); if (c instanceof Node) td.appendChild(c); else td.textContent = c ?? ""; r.appendChild(td); }); t.appendChild(r); });
}
function bar(x) { const b = el("div", null, "bar"); const i = el("i"); i.style.width = Math.round((x || 0) * 100) + "%"; b.appendChild(i); const w = el("div", null, "row"); w.append(b, el("span", pct(x))); return w; }
function chip(text, cls) { return el("span", text, "chip " + (cls || "")); }

let D = null;
async function refresh() {
  D = await api("/api/dashboard");
  const s = D.summary;
  $("net").textContent = s.online ? "متصل" : "غير متصل";
  const k = $("kpis"); k.replaceChildren();
  [["فئات مستقلة عن المعلّم", `${s.graduated} / ${s.categories}`], ["مهارات نشطة", s.active_skills], ["أدوات مطوّرة ذاتيًا", s.tools],
   ["تحسينات معتمدة", s.experiments.accepted || 0], ["تحسينات مرفوضة", s.experiments.rejected || 0], ["تنتظر موافقتك", s.experiments.pending_approval || 0],
   ["استدعاءات المعلّم", s.teacher_calls], ["تكلفة المعلّم $", s.teacher_spend_usd]]
    .forEach(([l, v]) => { const d = el("div", null, "kpi"); d.append(el("b", v), el("span", l)); k.appendChild(d); });

  table("cats", ["الفئة", "اللغة", "الدقة المستقلة", "فاصل الثقة", "اعتماد على المعلّم (آخر 50)", "المهارة النشطة", "الحالة", ""],
    D.categories.map(c => {
      const st = c.graduated ? chip("مستقل عن المعلّم", "ok") : c.frozen ? chip("مجمّد بعد فشل متكرر", "bad") : chip("يتعلم", "warn");
      const act = el("div", null, "row");
      if (c.active_skill) { const b = el("button", "تراجع", "btn alt"); b.onclick = () => api("/api/rollback", { category: c.name }).then(refresh); act.appendChild(b); }
      if (c.frozen) { const b = el("button", "استئناف", "btn alt"); b.onclick = () => api("/api/category/unfreeze", { category: c.name }).then(refresh); act.appendChild(b); }
      if (c.graduated) { const b = el("button", "السماح بالمعلّم مجددًا", "btn alt"); b.onclick = () => confirm("سيعود النظام لاستدعاء نموذج خارجي لهذه الفئة. متأكد؟") && api("/api/category/ungraduate", { category: c.name }).then(refresh); act.appendChild(b); }
      const prog = c.active_skill ? (typeof c.active_skill.program === "string" ? c.active_skill.program : JSON.stringify(c.active_skill.program)) + ` (v${c.active_skill.version})` : "—";
      return [c.name + " — " + c.description, `${c.language} · ${c.language_level}`, bar(c.heldout_acc), `من ${pct(c.heldout_ci[0])} إلى ${pct(c.heldout_ci[1])} (n=${c.heldout_n})`, bar(c.teacher_share_recent), prog, st, act];
    }));

  table("imps", ["الوقت", "النوع", "الفئة", "السبب", "قبل → بعد (اختبار مستقل)", "التكلفة", "المخاطر", "القرار"],
    D.improvements.map(i => {
      const r = i.results; const [dl, dc] = DEC[i.decision] || [i.decision, ""];
      const delta = r.candidate_acc !== undefined ? `${pct(r.baseline_acc)} → ${pct(r.candidate_acc)} (n=${r.n})` : "—";
      return [when(i.created_at), TYPES[i.type] || i.type, i.category, i.reason, delta,
        `${i.cost.compute_seconds}ث · ${i.cost.teacher_calls} استدعاء · $${i.cost.money_usd}`, i.risk, chip(dl, dc)];
    }));
  table("models", ["النموذج", "المزوّد", "مسموح التدريب على مخرجاته", "الاستدعاءات", "الدقة المقاسة", "عدد مرات التحقق"],
    D.models.map(m => [m.id, m.provider, m.allows_training_on_outputs, m.calls, bar(m.measured_accuracy), m.checked]));
  table("sources", ["المصدر", "طريقة الاكتساب", "عدد المعلومات", "متوسط الثقة"], D.knowledge_sources.map(s => [s.source, s.acquired_mode, s.n, s.avg_conf]));
  table("tools", ["الأداة", "الأصل", "الإصدار", "أُنشئت"], D.tools.map(t => [t.name, t.origin, t.version, when(t.created_at)]));
  table("errors", ["الفئة", "النمط", "السبب", "الحل", "التكرار"], D.errors.map(e => [e.category, e.pattern, e.cause, e.fix, e.count]));
  table("clog", ["#", "الوقت", "الفاعل", "النوع", "الكيان", "التفاصيل"], D.changelog.map(c => [c.seq, when(c.ts), c.actor, c.change_type, c.entity, c.detail]));
  table("jobs", ["المهمة", "الفئة", "الأولوية", "الحالة"], D.jobs.map(j => [j.kind, j.category, Number(j.priority).toFixed(2), j.status]));
  table("vq", ["الفئة", "المدخل", "الادعاء", "السبب"], D.verify_queue.map(v => [v.category, v.input, v.claim, v.reason]));
  $("device").textContent = JSON.stringify(D.device, null, 2);

  const p = $("pending"); p.replaceChildren();
  if (!D.pending.length) p.appendChild(el("p", "لا توجد تغييرات تنتظر موافقتك.", "muted"));
  D.pending.forEach(i => {
    const c = el("div", null, "card");
    c.append(el("b", `${TYPES[i.type]} — ${i.category}`), el("p", i.target + " · " + i.reason), el("pre", JSON.stringify(i.results, null, 2)));
    const ok = el("button", "اعتماد", "btn"); ok.onclick = () => api("/api/approve", { improvement_id: i.id, accept: true }).then(refresh);
    const no = el("button", "رفض", "btn bad"); no.onclick = () => api("/api/approve", { improvement_id: i.id, accept: false }).then(refresh);
    const r = el("div", null, "row"); r.append(ok, no); c.appendChild(r); p.appendChild(c);
  });

  ["tcat", "ecat"].forEach(id => { const s = $(id); const v = s.value; s.replaceChildren(); D.categories.forEach(c => { const o = el("option", c.name + " — " + c.description); o.value = c.name; s.appendChild(o); }); if (v) s.value = v; });
}

async function loadSettings() {
  const s = await api("/api/settings"); const box = $("sets"); box.replaceChildren();
  const LABELS = { online: "متصل بالإنترنت", allow_teacher: "السماح بالتعلم من نماذج أخرى", graduation_threshold: "عتبة الاستقلال عن المعلّم", graduation_margin: "هامش المقارنة مع المعلّم",
    min_heldout: "أقل عدد أمثلة للاختبار المستقل", max_regression_drop: "أقصى تراجع مسموح", max_failed_attempts: "محاولات فاشلة قبل التجميد", teacher_budget_per_cycle: "استدعاءات المعلّم لكل دورة",
    money_budget_usd: "الميزانية المالية الكلية $", require_approval_all: "طلب موافقتي على كل تغيير", approval_cost_usd: "تكلفة تتطلب موافقتي $", episode_limit: "حد الخبرات قبل الأرشفة" };
  Object.entries(s).forEach(([key, v]) => {
    box.appendChild(el("label", LABELS[key] || key));
    const i = el("input"); i.dataset.k = key;
    if (typeof v === "boolean") { i.type = "checkbox"; i.checked = v; i.style.width = "auto"; } else { i.type = "number"; i.step = "any"; i.value = v; }
    box.appendChild(i);
  });
}

document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#tabs button").forEach(x => x.classList.toggle("on", x === b));
  document.querySelectorAll("main section").forEach(s => s.classList.toggle("on", s.id === b.dataset.t));
  if (b.dataset.t === "settings") loadSettings();
});

$("learn").onclick = async () => {
  $("learnmsg").textContent = "جارٍ التعلم…";
  try { const r = await api("/api/learn", {}); $("learnmsg").textContent = r.skipped ? "تأجيل: " + r.skipped : r.results.map(x => `${x.category}: ${x.status}`).join(" · ") || "لا شيء يستحق التعلم الآن"; }
  catch (e) { $("learnmsg").textContent = e.message; }
  refresh();
};

$("tgo").onclick = async () => {
  const out = $("tout"); out.replaceChildren();
  try {
    const r = await api("/api/task", { category: $("tcat").value, input: $("tin").value, private: $("tpriv").checked });
    const c = el("div", null, "card");
    c.append(el("p", r.answer ?? "لا أعرف بعد. علّمني الإجابة الصحيحة."), el("p", "الحل من: " + r.solver + (r.cost ? ` · $${r.cost}` : ""), "note"));
    const fix = el("input"); fix.placeholder = "الإجابة الصحيحة (للتصحيح)";
    const row = el("div", null, "row");
    const mk = (t, kind, needs) => { const b = el("button", t, "btn alt"); b.onclick = async () => { try { await api("/api/feedback", { episode_id: r.episode_id, kind, correct_answer: needs ? fix.value : null, note: fix.value }); b.textContent = "✓ " + t; refresh(); } catch (e) { alert(e.message); } }; return b; };
    row.append(mk("صحيح", "correct"), mk("تصحيح موثوق", "correction", true), mk("رأي شخصي", "opinion"), mk("يحتاج تحقق", "needs_verification", true), mk("غير موثوق", "unreliable"));
    c.append(fix, row); out.appendChild(c);
  } catch (e) { out.appendChild(el("p", e.message, "bad")); }
};

$("cadd").onclick = () => api("/api/category", { name: $("cname").value.trim(), kind: $("ckind").value, description: $("cdesc").value, language: $("clang").value })
  .then(() => { $("cmsg").textContent = "أُضيفت"; refresh(); }).catch(e => $("cmsg").textContent = e.message);
$("eadd").onclick = () => {
  const pairs = $("epairs").value.split("\n").map(l => l.split(/\t|\|/)).filter(p => p.length >= 2).map(p => [p[0].trim(), p.slice(1).join("|").trim()]);
  api("/api/examples", { category: $("ecat").value, pairs, split: $("esplit").value || null })
    .then(r => { $("emsg").textContent = `تطوير: ${r.dev} · اختبار مستقل: ${r.heldout}`; refresh(); }).catch(e => $("emsg").textContent = e.message);
};
$("ssave").onclick = () => {
  const body = {}; document.querySelectorAll("#sets input").forEach(i => body[i.dataset.k] = i.type === "checkbox" ? i.checked : Number(i.value));
  api("/api/settings", body).then(() => { $("smsg").textContent = "حُفظت"; refresh(); }).catch(e => $("smsg").textContent = e.message);
};

refresh();


// MOSAIC extended workspaces: speech, consent-based local files, and web knowledge.
let speechRecognition = null;
function speechAvailable() { return !!(window.SpeechRecognition || window.webkitSpeechRecognition); }
function say(text) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(String(text || ""));
  utterance.lang = "ar";
  window.speechSynthesis.speak(utterance);
}
function fillVoiceCategories() {
  const src = $("tcat"), dst = $("vcat");
  if (!src || !dst) return;
  dst.replaceChildren(...Array.from(src.options).map(o => {
    const n = document.createElement("option"); n.value = o.value; n.textContent = o.textContent; return n;
  }));
}
$("vlisten").onclick = () => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { $("vresult").textContent = "التعرف الصوتي غير مدعوم في WebView هذا الجهاز. يمكنك استخدام لوحة المفاتيح الصوتية في الهاتف ثم إرسال النص."; return; }
  try {
    speechRecognition = new SR();
    speechRecognition.lang = "ar-SA";
    speechRecognition.continuous = false;
    speechRecognition.interimResults = true;
    speechRecognition.onresult = (event) => {
      let text = "";
      for (let i = event.resultIndex; i < event.results.length; i++) text += event.results[i][0].transcript;
      $("vinput").value = text;
    };
    speechRecognition.onerror = (event) => { $("vresult").textContent = "تعذر التعرف على الكلام: " + event.error; };
    speechRecognition.onend = () => { $("vlisten").textContent = "🎙 ابدأ الاستماع"; };
    speechRecognition.start();
    $("vlisten").textContent = "يستمع الآن…";
  } catch (e) { $("vresult").textContent = e.message; }
};
$("vstop").onclick = () => { if (speechRecognition) speechRecognition.stop(); };
$("vsend").onclick = async () => {
  const input = $("vinput").value.trim();
  if (!input) { $("vresult").textContent = "تحدث أو اكتب سؤالك أولاً."; return; }
  $("vresult").textContent = "يفكر MOSAIC…";
  try {
    const r = await api("/api/task", {category: $("vcat").value || $("tcat").value, input, private: false});
    const answer = r.answer || "لم أتمكن من إنتاج إجابة.";
    $("vresult").textContent = answer + " — المصدر: " + (r.solver || "MOSAIC");
    if ($("vspeak").checked) say(answer);
  } catch (e) { $("vresult").textContent = e.message; }
};
$("sitelearn").onclick = async () => {
  const url = $("siteurl").value.trim();
  if (!url) { $("siteprogress").textContent = "أدخل رابطاً أولاً."; return; }
  const max_pages = Math.max(1, Math.min(8, Number($("sitepages").value) || 8));
  $("siteprogress").textContent = "جارٍ فحص الموقع واحترام robots.txt…";
  $("sitelearn").disabled = true;
  try {
    const r = await api("/api/learn-url", {url, max_pages});
    $("siteprogress").textContent = "اكتمل الجمع: " + r.pages_saved + " صفحة محفوظة، " + r.pages_skipped + " صفحة متجاوزة.";
    const box = document.createElement("div");
    (r.pages || []).forEach(p => {
      const item = el("div", null, "resultitem");
      const a = el("a", p.title); a.href = p.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      item.append(a, el("div", p.characters + " حرف · SHA-256 " + p.sha256.slice(0, 16), "note"));
      box.appendChild(item);
    });
    if (r.skipped && r.skipped.length) box.appendChild(el("p", "بعض الصفحات لم تُجمع: " + r.skipped.map(x => x.reason).join("؛ "), "note"));
    $("siteprogress").appendChild(box);
    loadWebSources();
  } catch (e) { $("siteprogress").textContent = "تعذر التعلم: " + e.message; }
  finally { $("sitelearn").disabled = false; }
};
$("knowsearch").onclick = async () => {
  $("knowresults").replaceChildren(el("p", "جارٍ البحث…", "note"));
  try {
    const r = await api("/api/search-knowledge", {query: $("knowledgequery").value});
    const box = $("knowresults"); box.replaceChildren();
    if (!r.results.length) box.appendChild(el("p", "لم أعثر على نتائج. أضف مواقع أو جرّب كلمات أخرى.", "note"));
    r.results.forEach(x => {
      const item = el("div", null, "resultitem");
      const a = el("a", x.title); a.href = x.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      item.append(a, el("p", x.excerpt), el("span", "درجة التطابق: " + x.score, "chip"));
      box.appendChild(item);
    });
  } catch (e) { $("knowresults").textContent = e.message; }
};
async function loadWebSources() {
  try {
    const r = await api("/api/web-sources");
    const box = $("sourcelist"); box.replaceChildren();
    if (!r.sources.length) box.appendChild(el("p", "لا توجد مصادر محفوظة بعد.", "note"));
    r.sources.forEach(s => {
      const item = el("div", null, "resultitem");
      const a = el("a", s.title || s.url); a.href = s.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      item.append(a, el("div", s.characters + " حرف · " + when(s.fetched_at), "note"));
      box.appendChild(item);
    });
  } catch (e) { $("sourcelist").textContent = e.message; }
}
$("reloadsources").onclick = loadWebSources;
let selectedFileTexts = [];
$("phonefiles").onchange = async (event) => {
  selectedFileTexts = [];
  const files = Array.from(event.target.files || []).slice(0, 10);
  for (const file of files) {
    if (file.size > 1_000_000) continue;
    try {
      const text = await file.text();
      selectedFileTexts.push({name: file.name, text: text.slice(0, 100_000)});
    } catch (_) {}
  }
  $("filelist").textContent = "تمت قراءة " + selectedFileTexts.length + " ملف نصي من الملفات التي اخترتها فقط.";
};
$("fileask").onclick = () => {
  const q = $("filequestion").value.trim();
  const terms = q.toLowerCase().split(/\\s+/).filter(x => x.length > 2);
  const box = $("fileoutput"); box.replaceChildren();
  if (!selectedFileTexts.length) { box.textContent = "اختر ملفات نصية أولاً."; return; }
  selectedFileTexts.forEach(f => {
    const lines = f.text.split(/\\r?\\n/);
    const matches = lines.filter(line => !terms.length || terms.some(term => line.toLowerCase().includes(term))).slice(0, 8);
    const item = el("div", null, "resultitem");
    item.append(el("b", f.name), el("pre", matches.join("\\n") || "لم توجد أسطر مطابقة."));
    box.appendChild(item);
  });
};
document.querySelectorAll("#tabs button").forEach(b => {
  const old = b.onclick;
  b.addEventListener("click", () => {
    if (b.dataset.t === "voice") fillVoiceCategories();
    if (b.dataset.t === "knowledge") loadWebSources();
  });
});
