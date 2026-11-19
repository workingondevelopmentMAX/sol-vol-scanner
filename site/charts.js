/* Plain-SVG chart helpers shared by index.html and lab.html. Drawing and scaling only: no calculations. */
(function () {
  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const NS = "http://www.w3.org/2000/svg";
  function el(tag, attrs, parent) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent && parent.appendChild(e); return e; }
  function niceTicks(lo, hi, n = 4) {
    const span = hi - lo || 1, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || mag * 10;
    const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10)); return out;
  }
  function frame(host, H = 220) {
    host.innerHTML = ""; const W = Math.max(280, Math.round(host.clientWidth || 520)), m = {l:42, r:12, t:10, b:26};
    const svg = el("svg", {viewBox:`0 0 ${W} ${H}`, role:"img"}, host);
    const tip = document.createElement("div"); tip.className = "tip"; host.appendChild(tip);
    return {svg, tip, W, H, m, iw:W - m.l - m.r, ih:H - m.t - m.b};
  }
  function axes(f, x, y, yt, fmtY, xt, fmtX) {
    yt.forEach(v => { el("line", {x1:f.m.l, x2:f.W - f.m.r, y1:y(v), y2:y(v), stroke:css("--grid"), "stroke-width":1}, f.svg);
      const t = el("text", {x:f.m.l - 6, y:y(v) + 4, "text-anchor":"end", "font-size":11, fill:css("--text-muted")}, f.svg); t.textContent = fmtY(v); });
    xt.forEach((v, i) => { const t = el("text", {x:x(v), y:f.H - 8, "text-anchor":i === 0 && x(v) < f.m.l + 20 ? "start" : (i === xt.length - 1 && x(v) > f.W - f.m.r - 20 ? "end" : "middle"), "font-size":11, fill:css("--text-muted")}, f.svg); t.textContent = fmtX(v); });
  }
  function path(pts) { let d = "", pen = false; pts.forEach(p => { if (p == null) { pen = false; return; } d += (pen ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1); pen = true; }); return d; }
  function hover(f, xs, onMove) {
    const ov = el("rect", {x:f.m.l, y:f.m.t, width:f.iw, height:f.ih, fill:"transparent"}, f.svg);
    const cross = el("line", {y1:f.m.t, y2:f.m.t + f.ih, stroke:css("--text-muted"), "stroke-width":1, "stroke-dasharray":"3 3", visibility:"hidden"}, f.svg);
    const move = ev => {
      const r = f.svg.getBoundingClientRect(), px = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left;
      const sx = px * f.W / r.width; let best = 0;
      xs.forEach((v, i) => { if (Math.abs(v - sx) < Math.abs(xs[best] - sx)) best = i; });
      cross.setAttribute("x1", xs[best]); cross.setAttribute("x2", xs[best]); cross.setAttribute("visibility", "visible");
      f.tip.innerHTML = onMove(best); f.tip.style.display = "block";
      const tx = Math.min(Math.max(xs[best] * r.width / f.W - f.tip.offsetWidth / 2, 0), r.width - f.tip.offsetWidth);
      f.tip.style.left = tx + "px"; f.tip.style.top = "-6px";
    };
    const out = () => { cross.setAttribute("visibility", "hidden"); f.tip.style.display = "none"; };
    ov.addEventListener("mousemove", move); ov.addEventListener("touchstart", move, {passive:true}); ov.addEventListener("touchmove", move, {passive:true});
    ov.addEventListener("mouseleave", out); ov.addEventListener("touchend", () => setTimeout(out, 1500));
  }

  window.Charts = {css, el, niceTicks, frame, axes, path, hover};
})();
