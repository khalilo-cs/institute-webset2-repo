"use strict";
// Dependency-free SVG charts with hover tooltips. Colors come from CSS roles
// (--series-1, --series-2, --grid, --axis) so light/dark swap in one place.
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const s = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
  const pct = (x) => Math.round(x * 100) + "%";

  function tooltip(host) {
    let t = host.querySelector(".viz-tip");
    if (!t) { t = document.createElement("div"); t.className = "viz-tip"; t.hidden = true; host.appendChild(t); }
    return {
      show(lines, x, y) {
        t.replaceChildren(...lines.map(([label, value, color]) => {
          const row = document.createElement("div"); row.className = "viz-tip-row";
          if (color) { const sw = document.createElement("i"); sw.style.background = color; row.appendChild(sw); }
          const l = document.createElement("span"); l.textContent = label; const v = document.createElement("b"); v.textContent = value;
          row.append(l, v); return row;
        }));
        t.hidden = false;
        const hw = host.clientWidth, tw = t.offsetWidth;
        t.style.left = Math.min(Math.max(0, x + 12), hw - tw) + "px"; t.style.top = Math.max(0, y - 10) + "px";
      },
      hide() { t.hidden = true; },
    };
  }

  // Line chart: share of tasks answered by each solver over time (one y-axis, 0–100%).
  function line(host, points, series) {
    host.replaceChildren(); host.classList.add("viz");
    if (!points.length) { host.appendChild(Object.assign(document.createElement("p"), { className: "muted", textContent: "لا توجد مهام بعد." })); return; }
    const W = Math.max(300, host.clientWidth || 600), H = 240, m = { l: 40, r: 70, t: 12, b: 28 };
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: H, role: "img", "aria-label": "من يجيب عن المهام مع الوقت" });
    const xs = points.map((p) => p.tasks), xMax = Math.max(...xs), xMin = Math.min(...xs);
    const X = (v) => m.l + (xMax === xMin ? (W - m.l - m.r) / 2 : ((v - xMin) / (xMax - xMin)) * (W - m.l - m.r));
    const Y = (v) => m.t + (1 - v) * (H - m.t - m.b);
    [0, 0.5, 1].forEach((g) => {
      svg.appendChild(s("line", { x1: m.l, x2: W - m.r, y1: Y(g), y2: Y(g), class: "viz-grid" }));
      const t = s("text", { x: m.l - 6, y: Y(g) + 4, "text-anchor": "end", class: "viz-axis" }); t.textContent = pct(g); svg.appendChild(t);
    });
    const xl = s("text", { x: (W - m.r + m.l) / 2, y: H - 6, "text-anchor": "middle", class: "viz-axis" }); xl.textContent = "عدد المهام"; svg.appendChild(xl);
    series.forEach((se) => {
      const d = points.map((p, i) => `${i ? "L" : "M"}${X(p.tasks).toFixed(1)},${Y(p[se.key]).toFixed(1)}`).join("");
      svg.appendChild(s("path", { d, fill: "none", stroke: `var(${se.color})`, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      const last = points[points.length - 1];
      svg.appendChild(s("circle", { cx: X(last.tasks), cy: Y(last[se.key]), r: 4, fill: `var(${se.color})`, stroke: "var(--card)", "stroke-width": 2 }));
      const lab = s("text", { x: X(last.tasks) + 8, y: Y(last[se.key]) + 4, class: "viz-label" }); lab.textContent = se.label; svg.appendChild(lab);
    });
    const cross = s("line", { y1: m.t, y2: H - m.b, class: "viz-cross", visibility: "hidden" }); svg.appendChild(cross);
    const dots = series.map((se) => { const c = s("circle", { r: 4, fill: `var(${se.color})`, stroke: "var(--card)", "stroke-width": 2, visibility: "hidden" }); svg.appendChild(c); return c; });
    const hit = s("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" }); svg.appendChild(hit);
    host.appendChild(svg);
    const tip = tooltip(host);
    const move = (ev) => {
      const r = svg.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * W;
      let best = points[0]; points.forEach((p) => { if (Math.abs(X(p.tasks) - px) < Math.abs(X(best.tasks) - px)) best = p; });
      cross.setAttribute("x1", X(best.tasks)); cross.setAttribute("x2", X(best.tasks)); cross.setAttribute("visibility", "visible");
      dots.forEach((d, i) => { d.setAttribute("cx", X(best.tasks)); d.setAttribute("cy", Y(best[series[i].key])); d.setAttribute("visibility", "visible"); });
      tip.show([["حتى المهمة", String(best.tasks)], ...series.map((se) => [se.label, pct(best[se.key]), getComputedStyle(host).getPropertyValue(se.color)])],
        (X(best.tasks) / W) * r.width, (ev.clientY - r.top));
    };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerleave", () => { tip.hide(); cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); });
  }

  // Horizontal bars: held-out accuracy per category (single series, magnitude).
  function bars(host, rows) {
    host.replaceChildren(); host.classList.add("viz");
    if (!rows.length) return;
    const W = Math.max(300, host.clientWidth || 600), rowH = 30, m = { l: 120, r: 56, t: 6, b: 6 };
    const H = m.t + m.b + rows.length * rowH;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: H, role: "img", "aria-label": "الدقة على الاختبار المستقل لكل فئة" });
    const X = (v) => (v || 0) * (W - m.l - m.r);
    svg.appendChild(s("line", { x1: m.l, x2: m.l, y1: m.t, y2: H - m.b, class: "viz-grid" }));
    const tip = tooltip(host);
    rows.forEach((r, i) => {
      const y = m.t + i * rowH + 6, h = rowH - 12, w = Math.max(X(r.value), r.value ? 4 : 0);
      const name = s("text", { x: m.l - 8, y: y + h / 2 + 4, "text-anchor": "end", class: "viz-axis" }); name.textContent = r.label; svg.appendChild(name);
      // 4px rounded data-end, square at the baseline
      const path = w > 0 ? `M${m.l},${y}h${w - 4}a4,4 0 0 1 4,4v${h - 8}a4,4 0 0 1 -4,4h${-(w - 4)}z` : "";
      if (path) svg.appendChild(s("path", { d: path, fill: "var(--series-1)" }));
      const val = s("text", { x: m.l + w + 6, y: y + h / 2 + 4, class: "viz-label" }); val.textContent = r.value === null ? "—" : pct(r.value); svg.appendChild(val);
      const hit = s("rect", { x: 0, y: y - 6, width: W, height: rowH, fill: "transparent" });
      hit.addEventListener("pointermove", (ev) => { const b = svg.getBoundingClientRect(); tip.show([[r.label, r.value === null ? "لا قياس" : pct(r.value)], ["عدد أمثلة الاختبار", String(r.n)], ["فاصل الثقة", r.ci]], ev.clientX - b.left, ev.clientY - b.top); });
      hit.addEventListener("pointerleave", () => tip.hide());
      svg.appendChild(hit);
    });
    host.appendChild(svg);
  }

  window.MosaicCharts = { line, bars };
})();
