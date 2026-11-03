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
    c.append(el("p", r.answer ?? "لا أعرف بعد. علّمني الإجابة الصحيحة."), el("p", "الحل من: " + r.solver + (r.cost ? ` · ${r.cost}` : ""), "note"));
    if (r.knowledge_sources && r.knowledge_sources.length) {
      c.append(el("p", "استندت المحاولة إلى مقتطفات من مصادر المعرفة المحفوظة:", "note"));
      r.knowledge_sources.forEach(src => { const a = el("a", src.title); a.href = src.url; a.target = "_blank"; a.rel = "noopener noreferrer"; c.append(a, el("span", " · ", "note")); });
    }
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
  if (window.AndroidMosaic && typeof window.AndroidMosaic.speak === "function") {
    window.AndroidMosaic.speak(String(text || ""));
    return;
  }
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
  if (window.AndroidMosaic && typeof window.AndroidMosaic.startSpeech === "function") {
    window.AndroidMosaic.startSpeech();
    $("vlisten").textContent = "يستمع الآن…";
    return;
  }
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
$("vstop").onclick = () => {
  if (window.AndroidMosaic && typeof window.AndroidMosaic.stopSpeech === "function") window.AndroidMosaic.stopSpeech();
  if (speechRecognition) speechRecognition.stop();
};
$("vsend").onclick = async () => {
  const input = $("vinput").value.trim();
  if (!input) { $("vresult").textContent = "تحدث أو اكتب سؤالك أولاً."; return; }
  $("vresult").textContent = "يفكر MOSAIC…";
  try {
    const r = await api("/api/task", {category: $("vcat").value || $("tcat").value, input, private: $("vpriv").checked});
    const answer = r.answer || "لم أتمكن من إنتاج إجابة.";
    $("vresult").textContent = answer + " — المصدر: " + (r.solver || "MOSAIC");
    if (r.knowledge_sources && r.knowledge_sources.length) $("vresult").textContent += " · استُخدمت " + r.knowledge_sources.length + " مصادر ويب محفوظة.";
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
let phoneFileIds = [];
$("phonefiles").onchange = async (event) => {
  phoneFileIds = [];
  $("filelist").textContent = "جارٍ رفع الملفات المختارة بأمان…";
  const files = Array.from(event.target.files || []).slice(0, 10);
  for (const file of files) {
    if (file.size > 20 * 1024 * 1024) {
      $("filelist").appendChild(el("p", file.name + ": الحد الأقصى 20MB.", "bad"));
      continue;
    }
    try {
      const saved = await api("/api/files/upload", {
        filename: file.name, content_base64: await encodeFileBase64(file)
      });
      phoneFileIds.push(saved.id);
      $("filelist").appendChild(el("p",
        saved.name + " · " + saved.mime_type + " · " + saved.size_bytes + " bytes · " +
        (saved.extracted_characters ? saved.extracted_characters + " حرف مستخرج" : "محفوظ؛ يحتاج محلل متخصص"),
        "note"));
    } catch (e) { $("filelist").appendChild(el("p", file.name + ": " + e.message, "bad")); }
  }
  if (!files.length) $("filelist").textContent = "لم يتم اختيار ملفات.";
  event.target.value = "";
};
$("fileask").onclick = async () => {
  const question = $("filequestion").value.trim();
  if (!phoneFileIds.length) { $("fileoutput").textContent = "اختر الملفات وارفعها أولاً."; return; }
  $("fileoutput").textContent = "جارٍ تحليل المحتوى…";
  try {
    const r = await api("/api/files/ask", {file_ids: phoneFileIds, question, private: true});
    $("fileoutput").replaceChildren(el("p", r.answer), el("p", "المزوّد: " + (r.provider || "غير محدد"), "note"));
  } catch (e) { $("fileoutput").textContent = e.message; }
};
document.querySelectorAll("#tabs button").forEach(b => {
  const old = b.onclick;
  b.addEventListener("click", () => {
    if (b.dataset.t === "voice") fillVoiceCategories();
    if (b.dataset.t === "knowledge") loadWebSources();
  });
});


// Persistent ChatGPT-style conversation workspace and universal file attachments.
let activeConversationId = null;
let attachedFileIds = [];
function renderChatMessages(messages) {
  const box = $("chatmessages"); box.replaceChildren();
  (messages || []).forEach(m => {
    const wrap = el("div", null, "message-wrap");
    const node = el("div", null, "message " + m.role);
    node.textContent = m.content || "";
    wrap.appendChild(node);
    if (m.role === "assistant" && m.id) {
      const controls = el("div", null, "row chat-feedback");
      const good = el("button", "إجابة مفيدة", "btn alt");
      good.onclick = async () => {
        try {
          await api("/api/chat/feedback", {message_id: m.id, rating: "useful"});
          controls.replaceChildren(el("span", "شكراً، تم حفظ تقييمك.", "note"));
        } catch (e) { alert(e.message); }
      };
      const bad = el("button", "إجابة غير دقيقة", "btn alt");
      bad.onclick = async () => {
        const correction = prompt("اكتب التصحيح الذي تريد من MOSAIC الاستفادة منه:");
        if (correction === null) return;
        if (!correction.trim()) { alert("اكتب التصحيح أولاً."); return; }
        const allowTraining = confirm(
          "هل تسمح باستخدام هذا التصحيح تحديداً في تدريب محلي لمحوّل MOSAIC؟\n\n" +
          "لن تُرسل بياناته إلى مزوّد سحابي. اختر إلغاءً للاحتفاظ به للبحث فقط دون تدريب."
        );
        try {
          await api("/api/chat/feedback", {
            message_id: m.id, rating: "not_accurate", correction,
            allow_training: allowTraining
          });
          const status = el("span",
            allowTraining ? "حُفظ التصحيح للمعرفة والتدريب المحلي المصرّح به." :
              "حُفظ التصحيح للاسترجاع فقط، ولم يُصرّح بالتدريب.",
            "note");
          controls.replaceChildren(status);
          if (allowTraining) {
            const revoke = el("button", "سحب إذن التدريب", "btn alt");
            revoke.onclick = async () => {
              revoke.disabled = true;
              try {
                await api("/api/chat/feedback", {
                  message_id: m.id, rating: "not_accurate", correction,
                  allow_training: false
                });
                controls.replaceChildren(el("span",
                  "سُحب الإذن وأُبطلت ملفات التدريب المجهزة. بقي التصحيح للاسترجاع فقط.",
                  "note"));
              } catch (e) {
                revoke.disabled = false;
                alert(e.message);
              }
            };
            controls.appendChild(revoke);
          }
        } catch (e) { alert(e.message); }
      };
      controls.append(good, bad);
      wrap.appendChild(controls);
    }
    box.appendChild(wrap);
  });
  box.scrollTop = box.scrollHeight;
}
let showingArchived = false;
async function loadConversationList() {
  const query = $("chatsearch") ? $("chatsearch").value.trim() : "";
  const params = new URLSearchParams();
  if (query && !showingArchived) params.set("q", query);
  if (showingArchived) params.set("archived", "1");
  const suffix = params.toString() ? "?" + params.toString() : "";
  const r = await api("/api/conversations" + suffix);
  const box = $("conversationlist"); box.replaceChildren();
  (r.conversations || []).forEach(c => {
    const row = el("div", null, "row");
    const open = el("button", c.title + " · " + c.message_count, "btn alt");
    open.style.flex = "1"; open.style.textAlign = "start"; open.onclick = () => openConversation(c.id);
    const rename = el("button", "✎", "btn alt");
    rename.title = "إعادة تسمية";
    rename.onclick = async () => {
      const title = prompt("اسم المحادثة", c.title);
      if (title !== null) { await api("/api/conversations/" + c.id + "/rename", {title}); await loadConversationList(); }
    };
    const archive = el("button", showingArchived ? "↩" : "▣", "btn alt");
    archive.title = showingArchived ? "استعادة من الأرشيف" : "أرشفة المحادثة";
    archive.onclick = async () => {
      await api("/api/conversations/" + c.id + "/archive", {archived: !showingArchived});
      if (activeConversationId === c.id && !showingArchived) {
        activeConversationId = null; await createChat();
      }
      await loadConversationList();
    };
    const remove = el("button", "×", "btn bad");
    remove.title = "حذف المحادثة";
    remove.onclick = async () => {
      if (!confirm("حذف هذه المحادثة؟")) return;
      await api("/api/conversations/" + c.id + "/delete", {});
      if (activeConversationId === c.id) { activeConversationId = null; await createChat(); }
      await loadConversationList();
    };
    row.append(open, rename, archive, remove); box.appendChild(row);
  });
}
async function openConversation(cid) {
  const c = await api("/api/conversations/" + encodeURIComponent(cid));
  activeConversationId = c.id;
  $("chatheading").textContent = c.title;
  renderChatMessages(c.messages);
  $("chatprovider").textContent = "";
  attachedFileIds = [];
  $("chatattachments").replaceChildren();
  (c.files || []).forEach(f => {
    const a = el("a", f.original_name + " · " + f.mime_type, "filepill");
    a.href = "/api/files/" + encodeURIComponent(f.id);
    a.target = "_blank"; a.rel = "noopener noreferrer";
    $("chatattachments").appendChild(a);
  });
  await loadConversationList();
}
async function createChat() {
  const c = await api("/api/conversations", {title: "محادثة جديدة"});
  await openConversation(c.id);
  $("chatinput").focus();
}
async function encodeFileBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode(...bytes.subarray(i, i + step));
  }
  return btoa(binary);
}
$("chatfiles").addEventListener("change", async (event) => {
  const files = Array.from(event.target.files || []);
  for (const file of files) {
    if (file.size > 20 * 1024 * 1024) {
      $("chatattachments").appendChild(el("p", file.name + ": الحد الأقصى 20MB.", "bad"));
      continue;
    }
    try {
      const saved = await api("/api/files/upload", {
        filename: file.name, content_base64: await encodeFileBase64(file),
        conversation_id: activeConversationId
      });
      attachedFileIds.push(saved.id);
      $("chatattachments").appendChild(el("span",
        saved.name + " · " + saved.status + " · " + saved.extracted_characters + " حرف",
        "filepill"));
    } catch (e) {
      $("chatattachments").appendChild(el("p", file.name + ": " + e.message, "bad"));
    }
  }
  event.target.value = "";
});
$("chatlearnfiles").onclick = async () => {
  if (!attachedFileIds.length) { alert("أرفق ملفات أولاً."); return; }
  const button = $("chatlearnfiles");
  button.disabled = true; button.textContent = "جارٍ تحليل النصوص والصور…";
  try {
    const result = await api("/api/knowledge/ingest", {
      file_ids: attachedFileIds, private: $("chatprivate").checked
    });
    const indexed = result.results.reduce((sum, item) => sum + (item.indexed || 0), 0);
    const images = result.results.filter(item => item.status === "indexed_vision").length;
    const visionUnavailable = result.results.filter(item => item.status === "vision_unavailable").length;
    const limited = result.results.filter(item => item.status === "vision_limit_reached").length;
    const skipped = result.results.filter(item => !item.indexed).length;
    alert("اكتمل التعلم: " + indexed + " مقطعاً، منها " + images +
      " صورة فُهم محتواها. ملفات تعذر فهمها: " + skipped +
      (visionUnavailable ? "، تعذر تشغيل نموذج الرؤية لعدد " + visionUnavailable : "") +
      (limited ? "، تجاوزت الصور الإضافية حد المعالجة " + limited : "") +
      ". أصبحت المعرفة متاحة للأسئلة اللاحقة.");
  } catch (e) { alert("تعذر التعلم من الملفات: " + e.message); }
  finally { button.disabled = false; button.textContent = "تعلم من الملفات المحددة"; }
};
$("chatclearfiles").onclick = () => {
  attachedFileIds = [];
  $("chatattachments").replaceChildren();
  $("chatfiles").value = "";
};
$("newchat").onclick = () => createChat().catch(e => alert(e.message));
$("chatsend").onclick = async () => {
  const prompt = $("chatinput").value.trim();
  if (!prompt) return;
  if (!activeConversationId) await createChat();
  $("chatsend").disabled = true;
  $("chatsend").textContent = "جارٍ الإرسال…";
  try {
    const r = await api("/api/chat", {
      conversation_id: activeConversationId, message: prompt,
      file_ids: attachedFileIds, private: $("chatprivate").checked
    });
    $("chatinput").value = "";
    attachedFileIds = [];
    $("chatattachments").replaceChildren();
    $("chatprovider").textContent = "المزوّد: " + r.provider;
    await openConversation(activeConversationId);
    $("chatprovider").textContent = "المزوّد: " + r.provider;
    if (r.knowledge_sources && r.knowledge_sources.length) {
      const refs = el("div", "المصادر: " + r.knowledge_sources.map(x => x.title).join("، "), "note");
      $("chatmessages").appendChild(refs);
    }
  } catch (e) {
    $("chatmessages").appendChild(el("div", e.message, "bad"));
  } finally {
    $("chatsend").disabled = false;
    $("chatsend").textContent = "إرسال";
  }
};
$("chatinput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("chatsend").click();
  }
});
document.querySelectorAll("#tabs button").forEach(b => b.addEventListener("click", () => {
  if (b.dataset.t === "chat") {
    if (!activeConversationId) createChat().catch(e => $("chatmessages").textContent = e.message);
    else loadConversationList().catch(() => {});
  }
}));
(async function showProviderStatus() {
  try {
    const p = await api("/api/provider/status");
    const modes = [];
    if (p.local_configured) modes.push("محلي: " + p.local_model);
    if (p.cloud_configured) modes.push("سحابي: " + p.cloud_model);
    $("net").textContent = modes.length ? "النماذج جاهزة · " + modes.join(" | ") : "لم يتم إعداد نموذج بعد";
    $("net").className = "chip " + (modes.length ? "ok" : "warn");
  } catch (_) { $("net").textContent = "تعذر فحص إعداد النموذج"; }
})();
(async function initChat() {
  try {
    const r = await api("/api/conversations");
    if (r.conversations.length) await openConversation(r.conversations[0].id);
    else await createChat();
  } catch (e) { $("chatmessages").textContent = "تعذر تهيئة المحادثات: " + e.message; }
})();

$("chatsearch").addEventListener("input", () => loadConversationList().catch(() => {}));

$("archivetoggle").onclick = async () => {
  showingArchived = !showingArchived;
  $("archivetoggle").textContent = showingArchived ? "عرض المحادثات النشطة" : "عرض الأرشيف";
  await loadConversationList();
};


// Explicit-consent local model training controls.
let mosaicTrainingCommands = "";
function psQuote(value) {
  return "'" + String(value).replace(/'/g, "''") + "'";
}
async function loadTrainingStatus() {
  try {
    const s = await api("/api/training/status");
    $("trainingstatus").textContent =
      "تصحيحات وافقت على تدريبها: " + s.consented_corrections +
      " · تصحيحات محفوظة للاسترجاع فقط: " + s.corrections_not_consented +
      (s.dataset_prepared ? " · مجموعة بيانات مجهزة: " + s.dataset.total_examples + " مثال" : "") +
      (s.adapter_trained ? " · يوجد تقرير تدريب سابق" : "");
    $("trainingdownload").disabled = !s.dataset_prepared;
    if (!s.dataset_prepared) {
      mosaicTrainingCommands = "";
      $("trainingcopy").disabled = true;
      $("trainingcopy").textContent = "نسخ أوامر التدريب";
    }
    if (s.adapter_trained) {
      const r = s.training_report;
      $("trainingserve").textContent =
        "نتيجة فحص الاختبار المستقل: " + (r.regression_guard_passed ? "اجتاز حد عدم التراجع" : "لم يجتز حد عدم التراجع") +
        "\nخسارة النموذج الأساسي: " + r.baseline_eval_loss +
        "\nخسارة المحوّل: " + r.adapter_eval_loss +
        "\nالنموذج الأساسي: " + r.base_model +
        "\nالمحوّل: " + s.adapter_dir +
        "\n\nلم يُفعّل تلقائياً. راجع النتائج قبل اعتماده.";
    }
  } catch (e) {
    $("trainingstatus").textContent = "تعذر قراءة حالة التدريب: " + e.message;
  }
}
$("trainingprepare").onclick = async () => {
  const button = $("trainingprepare");
  button.disabled = true;
  $("trainingresult").textContent = "جارٍ تجهيز التصحيحات التي منحتها إذناً للتدريب فقط…";
  try {
    const r = await api("/api/training/prepare", {});
    const readyToTrain = r.train_examples >= 6 && r.eval_examples >= 2;
    $("trainingresult").textContent =
      "تم تجهيز " + r.total_examples + " مثالاً: " + r.train_examples +
      " للتدريب و" + r.eval_examples + " للاختبار المستقل. المسار: " + r.output_dir +
      (readyToTrain ? "" : " · لا تكفي البيانات بعد؛ يلزم 6 أمثلة تدريب ومثالان مستقلان على الأقل.") ;
    $("trainingdownload").disabled = false;
    const selectedModel = $("trainingmodel").value.trim() || "Qwen/Qwen2.5-0.5B-Instruct";
    mosaicTrainingCommands =
      "cd mosaic\n" +
      "# CPU-only PyTorch (إذا كان لديك NVIDIA GPU، ثبّت إصدار CUDA المطابق بدلاً من هذا السطر)\n" +
      "python -m pip install torch --index-url https://download.pytorch.org/whl/cpu\n" +
      "python -m pip install -r training-requirements.txt\n" +
      "# فك ضغط mosaic-training-data.zip إلى هذا المجلد أولاً: .\\mosaic-training-data\n" +
      "python -m mosaic.training train --data " + psQuote(".\\mosaic-training-data") +
      " --output " + psQuote(".\\mosaic-adapters\\mosaic-lora") +
      " --model " + psQuote(selectedModel);
    $("trainingcommand").textContent = mosaicTrainingCommands;
    const adapterPath = ".\\mosaic-adapters\\mosaic-lora";
    $("trainingserve").textContent =
      "# Terminal 1: start the local model (after training completes)\n" +
      "cd mosaic\n" +
      "python -m mosaic.model_server --model " + psQuote(selectedModel) + " --adapter " + psQuote(adapterPath) +
      "\n\n# Terminal 2: set these before starting MOSAIC\n" +
      '$env:MOSAIC_LOCAL_BASE_URL="http://127.0.0.1:8766/v1"\n' +
      '$env:MOSAIC_LOCAL_MODEL="mosaic-local"';
    $("trainingcopy").disabled = !readyToTrain;
    await loadTrainingStatus();
  } catch (e) {
    $("trainingresult").textContent = "تعذر تجهيز بيانات التدريب: " + e.message;
  } finally { button.disabled = false; }
};
$("trainingcopy").onclick = async () => {
  if (!mosaicTrainingCommands) return;
  try {
    await navigator.clipboard.writeText(mosaicTrainingCommands);
    $("trainingcopy").textContent = "نُسخت الأوامر";
  } catch (_) {
    $("trainingresult").textContent = "تعذر النسخ تلقائياً؛ انسخ الأوامر المعروضة يدوياً.";
  }
};
document.querySelectorAll("#tabs button").forEach(b => {
  b.addEventListener("click", () => {
    if (b.dataset.t === "training") loadTrainingStatus();
  });
});

$("trainingdownload").onclick = () => {
  window.location.href = "/api/training/download";
};
