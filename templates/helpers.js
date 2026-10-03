/* Paper to Playground helper library.
   H  : pure drawing + math helpers available to generated compute()/render() code.
   PG : parameter plumbing shared by the page runtime and the offline checker.
   No DOM access here, so the same file runs in the browser and in the embedded JS engine. */
var H = (function () {
  'use strict';
  var H = {};
  var NAMED = { accent: 'var(--accent)', primary: 'var(--primary)', fg: 'var(--fg)', muted: 'var(--muted)', line: 'var(--line)',
    good: 'var(--good)', bad: 'var(--bad)', warn: 'var(--ours)', paper: 'var(--paper)', none: 'none', bg: 'var(--card)' };
  H.colors = ['var(--c1)', 'var(--c2)', 'var(--c3)', 'var(--c4)', 'var(--c5)', 'var(--c6)'];

  function col(c, def) {
    if (c === undefined || c === null || c === '') return def;
    if (typeof c === 'number') return H.colors[((Math.round(c) % 6) + 6) % 6];
    if (NAMED[c]) return NAMED[c];
    return String(c).replace(/[;"<>]/g, '');
  }
  function n(v, d) { v = Number(v); return isFinite(v) ? v : d; }
  function r2(v) { return Math.round(v * 100) / 100; }
  function arr(a) { return Array.isArray(a) ? a : (a === undefined || a === null ? [] : [a]); }
  function mat(m) {
    m = arr(m);
    if (m.length && !Array.isArray(m[0])) return [m];
    return m.map(arr);
  }

  /* ---------- math ---------- */
  H.clamp = function (x, lo, hi) { return Math.min(hi, Math.max(lo, x)); };
  H.round = function (x, d) { var k = Math.pow(10, d === undefined ? 0 : d); return Math.round(x * k) / k; };
  H.sum = function (a) { var s = 0; a = arr(a); for (var i = 0; i < a.length; i++) s += Number(a[i]); return s; };
  H.mean = function (a) { a = arr(a); return a.length ? H.sum(a) / a.length : 0; };
  H.dot = function (a, b) { var s = 0, k = Math.min(arr(a).length, arr(b).length); for (var i = 0; i < k; i++) s += a[i] * b[i]; return s; };
  H.transpose = function (m) {
    m = mat(m); if (!m.length) return [];
    return m[0].map(function (_, j) { return m.map(function (row) { return row[j]; }); });
  };
  H.matmul = function (a, b) {
    a = mat(a); b = mat(b); var bt = H.transpose(b);
    return a.map(function (row) { return bt.map(function (c) { return H.dot(row, c); }); });
  };
  H.softmax = function (a, T) {
    a = arr(a).map(Number); if (!a.length) return [];
    T = (T === undefined || !(T > 0)) ? 1 : T;
    var mx = Math.max.apply(null, a);
    var e = a.map(function (v) { return Math.exp((v - mx) / T); });
    var s = H.sum(e);
    return e.map(function (v) { return v / s; });
  };
  H.log2 = function (x) { return Math.log(x) / Math.LN2; };
  H.linspace = function (a, b, k) {
    k = Math.max(1, Math.round(n(k, 2))); if (k === 1) return [a];
    var o = []; for (var i = 0; i < k; i++) o.push(a + (b - a) * i / (k - 1)); return o;
  };
  H.range = function (k) { var o = []; for (var i = 0; i < k; i++) o.push(i); return o; };
  H.argmax = function (a) { var bi = -1, bv = -Infinity; arr(a).forEach(function (v, i) { if (v > bv) { bv = v; bi = i; } }); return bi; };
  /* seeded pseudo-random generator (mulberry32): var rnd = H.rng(7); rnd() -> [0,1) */
  H.rng = function (seed) {
    var s = (Math.round(n(seed, 1)) >>> 0) || 1;
    return function () {
      s = (s + 0x6D2B79F5) >>> 0; var t = s;
      t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  };
  H.fmt = function (x, d) {
    if (typeof x === 'boolean') return x ? 'yes' : 'no';
    if (typeof x === 'string') return x;
    if (x === null || x === undefined) return '–';
    if (Array.isArray(x)) return '[' + x.map(function (v) { return H.fmt(v, d); }).join(', ') + ']';
    x = Number(x);
    if (isNaN(x)) return 'NaN';
    if (!isFinite(x)) return x > 0 ? '∞' : '−∞';
    d = (d === undefined || d === null || !isFinite(d)) ? 3 : Math.max(0, Math.min(10, Math.round(d)));
    if (x !== 0 && Math.abs(x) < 0.5 * Math.pow(10, -d)) return x.toExponential(2).replace('-', '−');
    if (Math.abs(x) >= 1e7) return x.toExponential(2).replace('-', '−');
    var s = x.toFixed(d);
    if (s.indexOf('.') >= 0) s = s.replace(/0+$/, '').replace(/\.$/, '');
    if (s === '-0') s = '0';
    return s.replace('-', '−');
  };
  H.esc = function (s) {
    return String(s === undefined || s === null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  };

  /* ---------- svg primitives ---------- */
  H.svg = function (w, h, inner) {
    w = n(w, 480); h = n(h, 260);
    return '<svg class="pg-svg" viewBox="0 0 ' + w + ' ' + h + '" width="' + w + '" height="' + h + '" role="img">' +
      (Array.isArray(inner) ? inner.join('') : (inner || '')) + '</svg>';
  };
  /* d_k, m_{t-1}, 10^-8 in labels become real sub/superscripts */
  function script(t) {
    function span(kind) { return function (m, pre, braced, bare) { return pre + '<tspan baseline-shift="' + kind + '" style="font-size:72%">' + (braced !== undefined ? braced : bare) + '</tspan>'; }; }
    return t.replace(/([^\s_])_(?:\{([^{}]{1,24})\}|([A-Za-z0-9]{1,3})(?![A-Za-z0-9_]))/g, span('sub'))
      .replace(/([^\s^])\^(?:\{([^{}]{1,24})\}|([\u2212-]?[A-Za-z0-9]{1,3})(?![A-Za-z0-9]))/g, span('super'));
  }
  H.text = function (x, y, str, o) {
    o = o || {};
    var st = 'fill:' + col(o.color || o.fill, 'var(--fg)') + ';font-size:' + n(o.size, 12) + 'px;' + (o.bold ? 'font-weight:600;' : '');
    var tr = o.rotate ? ' transform="rotate(' + n(o.rotate, 0) + ' ' + r2(n(x, 0)) + ' ' + r2(n(y, 0)) + ')"' : '';
    return '<text x="' + r2(n(x, 0)) + '" y="' + r2(n(y, 0)) + '" text-anchor="' + (o.anchor === 'middle' || o.anchor === 'end' ? o.anchor : 'start') + '" style="' + st + '"' + tr + '>' + script(H.esc(H.fmt(str))) + '</text>';
  };
  function T(x, y, str, o) { o = o || {}; var q = { anchor: 'middle' }; for (var k in o) q[k] = o[k]; return H.text(x, y, str, q); }
  H.line = function (x1, y1, x2, y2, o) {
    o = o || {};
    return '<line x1="' + r2(n(x1, 0)) + '" y1="' + r2(n(y1, 0)) + '" x2="' + r2(n(x2, 0)) + '" y2="' + r2(n(y2, 0)) +
      '" style="stroke:' + col(o.color || o.stroke, 'var(--fg)') + ';stroke-width:' + n(o.width, 1.5) + (o.dash ? ';stroke-dasharray:' + (o.dash === true ? '4 3' : String(o.dash).replace(/[^0-9 .,]/g, '')) : '') + '"/>';
  };
  H.rect = function (x, y, w, h, o) {
    o = o || {};
    return '<rect x="' + r2(n(x, 0)) + '" y="' + r2(n(y, 0)) + '" width="' + r2(Math.max(0, n(w, 0))) + '" height="' + r2(Math.max(0, n(h, 0))) +
      '" rx="' + n(o.rx, 0) + '" style="fill:' + col(o.fill || o.color, 'var(--c1)') + ';stroke:' + col(o.stroke, 'none') +
      ';stroke-width:' + n(o.width, 1) + ';fill-opacity:' + n(o.opacity, 1) + '"/>';
  };
  H.circle = function (cx, cy, r, o) {
    o = o || {};
    return '<circle cx="' + r2(n(cx, 0)) + '" cy="' + r2(n(cy, 0)) + '" r="' + r2(Math.max(0, n(r, 3))) + '" style="fill:' + col(o.fill || o.color, 'var(--c1)') +
      ';stroke:' + col(o.stroke, 'none') + ';stroke-width:' + n(o.width, 1) + ';fill-opacity:' + n(o.opacity, 1) + '"/>';
  };
  H.path = function (points, o) {
    o = o || {};
    var d = '', pen = false;
    arr(points).forEach(function (p) {
      var x = Number(p && p[0]), y = Number(p && p[1]);
      if (!isFinite(x) || !isFinite(y)) { pen = false; return; }
      d += (pen ? 'L' : 'M') + r2(x) + ' ' + r2(y) + ' '; pen = true;
    });
    return '<path d="' + d + '" style="fill:' + col(o.fill, 'none') + ';stroke:' + col(o.color || o.stroke, 'var(--c1)') + ';stroke-width:' + n(o.width, 2) +
      (o.dash ? ';stroke-dasharray:4 3' : '') + ';fill-opacity:' + n(o.opacity, 1) + '"/>';
  };
  H.arrow = function (x1, y1, x2, y2, o) {
    o = o || {};
    x1 = n(x1, 0); y1 = n(y1, 0); x2 = n(x2, 0); y2 = n(y2, 0);
    var c = col(o.color, 'var(--muted)'), w = n(o.width, 1.5);
    var dx = x2 - x1, dy = y2 - y1, L = Math.sqrt(dx * dx + dy * dy) || 1, ux = dx / L, uy = dy / L, hs = 5 + w * 1.5;
    var bx = x2 - ux * hs, by = y2 - uy * hs;
    var s = H.line(x1, y1, bx, by, { color: c, width: w, dash: o.dash });
    s += '<polygon points="' + r2(x2) + ',' + r2(y2) + ' ' + r2(bx - uy * hs * 0.5) + ',' + r2(by + ux * hs * 0.5) + ' ' + r2(bx + uy * hs * 0.5) + ',' + r2(by - ux * hs * 0.5) + '" style="fill:' + c + '"/>';
    if (o.label !== undefined && o.label !== '') s += T((x1 + x2) / 2 + uy * 12, (y1 + y2) / 2 - ux * 7 + (ux ? 0 : 4), o.label, { size: 12, color: o.labelColor || 'muted' });
    return s;
  };

  /* ---------- charts (each returns a complete <svg>) ---------- */
  function ticks(lo, hi, k) {
    var span = hi - lo;
    if (!(span > 0)) { span = Math.abs(lo) || 1; lo -= span / 2; hi = lo + span; }
    var step = Math.pow(10, Math.floor(Math.log(span / k) / Math.LN10)), e = span / k / step;
    if (e >= 7.5) step *= 10; else if (e >= 3.5) step *= 5; else if (e >= 1.5) step *= 2;
    var t = [], v = Math.ceil(lo / step - 1e-9) * step;
    for (; v <= hi + step * 1e-9 && t.length < 40; v += step) t.push(Math.abs(v) < step * 1e-9 ? 0 : v);
    return { t: t, d: Math.max(0, Math.min(8, -Math.floor(Math.log(step) / Math.LN10 + 1e-9))) };
  }
  function finite(a) { return a.filter(function (v) { return typeof v === 'number' && isFinite(v); }); }
  function hlSet(h) { var s = {}; arr(h).forEach(function (i) { s[i] = 1; }); return s; }

  H.bars = function (o) {
    o = o || {};
    var vals = arr(o.values).map(Number), k = vals.length;
    var labels = arr(o.labels), w = n(o.w, 390), h = n(o.h, 280), dg = o.digits === undefined ? 3 : o.digits;
    var m = { l: 54, r: 12, t: o.title ? 42 : 18, b: o.xLabel ? 52 : 32 };
    var f = finite(vals), lo = Math.min(0, f.length ? Math.min.apply(null, f) : 0), hi = Math.max(0, f.length ? Math.max.apply(null, f) : 1);
    if (o.min !== undefined && isFinite(o.min)) lo = Math.min(lo, Number(o.min));
    var span0 = hi - lo;
    if (o.max !== undefined && isFinite(o.max)) hi = Math.max(hi, Number(o.max)); else hi = hi + span0 * 0.12;
    if (lo < 0) lo -= span0 * 0.14;   /* room for value labels under negative bars */
    if (hi === lo) hi = lo + 1;
    var tk = ticks(lo, hi, 5), widest = 0;
    tk.t.forEach(function (v) { widest = Math.max(widest, H.fmt(v, tk.d).length); });
    m.l = Math.max(m.l, widest * 7.4 + 26);   /* long tick labels (e.g. 1.00e-10) must not be clipped */
    var pw = w - m.l - m.r, ph = h - m.t - m.b;
    var Y = function (v) { return m.t + ph - (v - lo) / (hi - lo) * ph; };
    var hl = hlSet(o.highlight), s = '';
    if (o.title) s += T(w / 2, 20, o.title, { size: 15, bold: true });
    tk.t.forEach(function (v) {
      s += H.line(m.l, Y(v), w - m.r, Y(v), { color: 'line', width: 1, dash: '3 4' });
      s += T(m.l - 6, Y(v) + 4, H.fmt(v, tk.d), { anchor: 'end', size: 12.5, color: 'muted' });
    });
    var slot = k ? pw / k : pw, bw = Math.min(64, slot * 0.7);
    for (var i = 0; i < k; i++) {
      var v = isFinite(vals[i]) ? vals[i] : 0, cx = m.l + slot * (i + 0.5), y0 = Y(0), y1 = Y(H.clamp(v, lo, hi));
      var c = o.colors ? col(arr(o.colors)[i], 'var(--c1)') : (hl[i] ? 'var(--accent)' : col(o.color, 'var(--c1)'));
      s += H.rect(cx - bw / 2, Math.min(y0, y1), bw, Math.abs(y1 - y0), { fill: c, rx: 5, opacity: (o.highlight !== undefined && !hl[i]) ? 0.45 : 0.95 });
      if (k <= 16) s += T(cx, (v >= 0 ? Math.min(y0, y1) - 5 : Math.max(y0, y1) + 13), H.fmt(vals[i], dg), { size: k > 10 ? 10 : 13, bold: true });
      if (k <= 24) s += T(cx, m.t + ph + 17, labels[i] === undefined ? String(i + 1) : labels[i], { size: k > 12 ? 10 : 12.5, color: 'muted' });
    }
    s += H.line(m.l, Y(0), w - m.r, Y(0), { color: 'fg', width: 1 });
    if (o.yLabel) s += T(13, m.t + ph / 2, o.yLabel, { size: 12.5, color: 'muted', rotate: -90 });
    if (o.xLabel) s += T(m.l + pw / 2, h - 8, o.xLabel, { size: 12.5, color: 'muted' });
    return H.svg(w, h, s);
  };

  H.lines = function (o) {
    o = o || {};
    var cat = arr(o.x).length && arr(o.x).some(function (v) { return typeof v !== 'number' || !isFinite(v); }) ? arr(o.x).map(String) : null;
    function catX(v) { if (!cat || typeof v === 'number') return Number(v); var k = cat.indexOf(String(v)); return k < 0 ? NaN : k; }
    var series = arr(o.series).map(function (sr, i) {
      var y = arr(sr && sr.y).map(Number);
      var x = arr(sr && sr.x).length ? arr(sr.x).map(Number) : (arr(o.x).length && !cat ? arr(o.x).map(Number) : H.range(y.length));
      return { name: sr && sr.name !== undefined ? String(sr.name) : '', x: x, y: y, color: col(sr && sr.color, H.colors[i % 6]), dash: sr && sr.dash };
    });
    var pts = arr(o.points).map(function (q) { return { x: catX(q && q.x), y: Number(q && q.y), label: q && q.label, color: q && q.color }; });
    var marks = arr(o.markers).map(function (q) { return { x: catX(q && q.x), label: q && q.label }; });
    var w = n(o.w, 400), h = n(o.h, 290), named = series.filter(function (sr) { return sr.name; });
    var m = { l: 56, r: 16, t: (o.title ? 40 : 14) + (named.length ? 18 : 0), b: 48 };
    var xs = [], ys = [];
    series.forEach(function (sr) { xs = xs.concat(finite(sr.x)); ys = ys.concat(finite(sr.y)); });
    pts.forEach(function (p) { if (isFinite(p.x)) xs.push(Number(p.x)); if (isFinite(p.y)) ys.push(Number(p.y)); });
    marks.forEach(function (p) { if (isFinite(p.x)) xs.push(Number(p.x)); });
    var x0 = xs.length ? Math.min.apply(null, xs) : 0, x1 = xs.length ? Math.max.apply(null, xs) : 1;
    var y0 = ys.length ? Math.min.apply(null, ys) : 0, y1 = ys.length ? Math.max.apply(null, ys) : 1;
    if (x1 === x0) { x0 -= 0.5; x1 += 0.5; }
    var pad = (y1 - y0) * 0.06 || Math.abs(y0) * 0.1 || 0.5; y0 -= pad; y1 += pad;
    /* a requested axis range may widen the view but never clips data into a false plateau */
    if (o.yMin !== undefined && isFinite(o.yMin)) y0 = Math.min(y0, Number(o.yMin));
    if (o.yMax !== undefined && isFinite(o.yMax)) y1 = Math.max(y1, Number(o.yMax));
    if (y1 <= y0) y1 = y0 + 1;
    var pw = w - m.l - m.r, ph = h - m.t - m.b;
    var X = function (v) { return m.l + (v - x0) / (x1 - x0) * pw; }, Y = function (v) { return m.t + ph - (H.clamp(v, y0, y1) - y0) / (y1 - y0) * ph; };
    var s = '', tx = ticks(x0, x1, 6), ty = ticks(y0, y1, 5), widest = 0;
    ty.t.forEach(function (v) { widest = Math.max(widest, H.fmt(v, ty.d).length); });
    if (widest * 7.4 + 26 > m.l) { m.l = widest * 7.4 + 26; pw = w - m.l - m.r; }
    if (o.title) s += T(w / 2, 20, o.title, { size: 15, bold: true });
    ty.t.forEach(function (v) { s += H.line(m.l, Y(v), w - m.r, Y(v), { color: 'line', width: 1, dash: '3 4' }) + T(m.l - 6, Y(v) + 4, H.fmt(v, ty.d), { anchor: 'end', size: 12.5, color: 'muted' }); });
    if (cat) tx = { t: H.range(cat.length).filter(function (i) { return cat.length <= 12 || i % Math.ceil(cat.length / 12) === 0; }), d: 0 };
    tx.t.forEach(function (v) { s += H.line(X(v), m.t + ph, X(v), m.t + ph + 4, { color: 'muted', width: 1 }) + T(X(v), m.t + ph + 16, cat ? cat[v] : H.fmt(v, tx.d), { size: 12.5, color: 'muted' }); });
    s += H.line(m.l, m.t + ph, w - m.r, m.t + ph, { color: 'fg', width: 1 }) + H.line(m.l, m.t, m.l, m.t + ph, { color: 'fg', width: 1 });
    marks.forEach(function (mk) {
      if (!isFinite(mk.x)) return;
      s += H.line(X(mk.x), m.t, X(mk.x), m.t + ph, { color: 'accent', width: 1.5, dash: true });
      if (mk.label !== undefined && mk.label !== '') s += T(H.clamp(X(mk.x), m.l + 30, w - m.r - 30), m.t + ph - 7, mk.label, { size: 11, color: 'accent', bold: true });
    });
    series.forEach(function (sr) {
      s += H.path(sr.x.map(function (xv, i) { return [isFinite(xv) && isFinite(sr.y[i]) ? X(xv) : NaN, isFinite(sr.y[i]) ? Y(sr.y[i]) : NaN]; }), { color: sr.color, width: 2.6, dash: sr.dash });
      if (sr.x.length <= 24) sr.x.forEach(function (xv, i) { if (isFinite(xv) && isFinite(sr.y[i])) s += H.circle(X(xv), Y(sr.y[i]), 3.2, { fill: sr.color }); });
    });
    pts.forEach(function (p) {
      if (!isFinite(p.x) || !isFinite(p.y)) return;
      s += H.circle(X(p.x), Y(p.y), 5.5, { fill: col(p.color, 'var(--accent)'), stroke: 'bg', width: 2 });
      if (p.label !== undefined) s += T(H.clamp(X(p.x), m.l + 40, w - m.r - 40), Y(p.y) - 10, p.label, { size: 11, bold: true });
    });
    var lx = m.l;
    named.forEach(function (sr) {
      s += H.line(lx, m.t - 10, lx + 16, m.t - 10, { color: sr.color, width: 3 }) + T(lx + 21, m.t - 6, sr.name, { anchor: 'start', size: 12.5 });
      lx += 36 + sr.name.length * 7.2;
    });
    if (o.yLabel) s += T(13, m.t + ph / 2, o.yLabel, { size: 12.5, color: 'muted', rotate: -90 });
    if (o.xLabel) s += T(m.l + pw / 2, h - 8, o.xLabel, { size: 12.5, color: 'muted' });
    return H.svg(w, h, s);
  };

  H.heatmap = function (o) {
    o = o || {};
    var M = mat(o.matrix), rows = M.length, cols = rows ? Math.max.apply(null, M.map(function (r) { return r.length; })) : 0;
    var rl = arr(o.rowLabels), cl = arr(o.colLabels), dg = o.digits === undefined ? 2 : o.digits, shade = o.shade !== false;
    var fm = typeof o.fmt === 'function' ? o.fmt : function (v) { return H.fmt(v, dg); };
    var maxL = 0; rl.forEach(function (t) { maxL = Math.max(maxL, String(t).length); });
    var left = rl.length ? Math.min(170, 14 + maxL * 7.4) : 8, top = (o.title ? 34 : 6) + (cl.length ? 20 : 0);
    var longest = 4; M.forEach(function (r) { r.forEach(function (v) { longest = Math.max(longest, String(fm(v)).length); }); });
    cl.forEach(function (t) { longest = Math.max(longest, String(t).length * 0.9); });
    var cw = n(o.cellW, H.clamp(longest * 8.2 + 16, 52, 120)), ch = n(o.cellH, 36);
    var w = Math.max(left + cols * cw + 8, o.title ? String(o.title).length * 8.4 + 16 : 0), h = top + rows * ch + 8;
    var all = []; M.forEach(function (r) { all = all.concat(finite(r.map(Number))); });
    var lo = o.min !== undefined ? Number(o.min) : (all.length ? Math.min.apply(null, all) : 0), hi = o.max !== undefined ? Number(o.max) : (all.length ? Math.max.apply(null, all) : 1);
    var hl = {}; arr(o.highlight).forEach(function (rc) { if (Array.isArray(rc)) hl[rc[0] + ',' + rc[1]] = 1; });
    var s = '';
    if (o.title) s += T(w / 2, 17, o.title, { size: 15, bold: true });
    for (var j = 0; j < cols; j++) if (cl[j] !== undefined) s += T(left + cw * (j + 0.5), top - 6, cl[j], { size: 12.5, color: 'muted' });
    for (var i = 0; i < rows; i++) {
      if (rl[i] !== undefined) s += T(left - 6, top + ch * (i + 0.5) + 4, rl[i], { anchor: 'end', size: 12.5, color: 'muted' });
      for (j = 0; j < cols; j++) {
        var v = M[i][j], x = left + cw * j, y = top + ch * i, isN = typeof v === 'number' && isFinite(v);
        var t = isN && hi > lo ? (v - lo) / (hi - lo) : (isN && shade && hi === lo ? 0.5 : 0);
        var on = hl[i + ',' + j] || o.highlightRow === i || o.highlightCol === j;
        s += H.rect(x + 1, y + 1, cw - 2, ch - 2, { fill: 'var(--card)', stroke: on ? 'accent' : 'line', width: on ? 2.5 : 1, rx: 6 });
        if (shade && isN) s += H.rect(x + 2, y + 2, cw - 4, ch - 4, { fill: col(o.color, 'var(--c1)'), opacity: 0.1 + 0.6 * H.clamp(t, 0, 1), rx: 5 });
        s += T(x + cw / 2, y + ch / 2 + 5, v === undefined ? '' : fm(v), { size: 14, bold: !!on });
      }
    }
    return H.svg(w, h, s);
  };
  H.matrixTable = function (matrix, o) {
    o = o || {}; var q = { matrix: matrix, shade: false };
    for (var k in o) q[k] = o[k];
    return H.heatmap(q);
  };
  H.vectorTable = function (vec, o) {
    o = o || {}; var q = { matrix: [arr(vec)], shade: false };
    for (var k in o) q[k] = o[k];
    if (o.labels && !o.colLabels) q.colLabels = o.labels;
    return H.heatmap(q);
  };

  H.flow = function (o) {
    o = o || {};
    var off = o.title ? 24 : 0, nodes = arr(o.nodes).map(function (nd, i) {
      nd = nd || {};
      var label = String(nd.label !== undefined ? nd.label : (nd.id !== undefined ? nd.id : '')), sub = nd.sub !== undefined ? H.fmt(nd.sub) : '';
      return { id: String(nd.id !== undefined ? nd.id : i), label: label, sub: sub, x: Number(nd.x), y: Number(nd.y), hl: !!nd.highlight,
        bw: Math.max(78, 8 * Math.max(label.length, sub.length) + 24), bh: sub ? 52 : 36 };
    });
    if (nodes.some(function (nd) { return !isFinite(nd.x) || !isFinite(nd.y); })) {
      var gap = 50; arr(o.edges).forEach(function (e) { if (e && e.label !== undefined) gap = Math.max(gap, String(e.label).length * 7.2 + 26); });
      var cx = 14; nodes.forEach(function (nd) { nd.x = cx + nd.bw / 2; nd.y = 44; cx += nd.bw + gap; });
    }
    else {
      /* spread hand-placed nodes apart until every edge has room for its arrow and label */
      var byId = {}, sc = 1, x0 = Infinity;
      nodes.forEach(function (nd) { byId[nd.id] = nd; x0 = Math.min(x0, nd.x - nd.bw / 2); });
      arr(o.edges).forEach(function (e) {
        var a = byId[String(e && e.from)], b = byId[String(e && e.to)]; if (!a || !b || a === b) return;
        var dx = Math.abs(b.x - a.x), dy = Math.abs(b.y - a.y);
        if (dx > 1 && dy < (a.bh + b.bh) / 2) sc = Math.max(sc, ((e.label !== undefined ? String(e.label).length * 7.2 + 28 : 36) + (a.bw + b.bw) / 2) / dx);
      });
      sc = Math.min(sc, 4);
      nodes.forEach(function (nd) { nd.x = (nd.x - x0) * sc + 12 + (sc > 1 ? 0 : x0 - 12 > 0 ? x0 - 12 : 0); });
      if (sc > 1) o = { title: o.title, edges: o.edges, h: o.h };
    }
    var w = n(o.w, 0), h = n(o.h, 0), by = {};
    nodes.forEach(function (nd) { nd.y += off; by[nd.id] = nd; w = Math.max(w, nd.x + nd.bw / 2 + 12); h = Math.max(h, nd.y + nd.bh / 2 + 12); });
    function edge(a, b) {
      var dx = b.x - a.x, dy = b.y - a.y; if (!dx && !dy) return [a.x, a.y];
      var t = Math.min(dx ? (a.bw / 2 + 2) / Math.abs(dx) : Infinity, dy ? (a.bh / 2 + 2) / Math.abs(dy) : Infinity);
      return [a.x + dx * t, a.y + dy * t];
    }
    var s = o.title ? T(w / 2, 17, o.title, { size: 15, bold: true }) : '';
    arr(o.edges).forEach(function (e) {
      var a = by[String(e && e.from)], b = by[String(e && e.to)]; if (!a || !b || a === b) return;
      var p = edge(a, b), q = edge(b, a);
      s += H.arrow(p[0], p[1], q[0], q[1], { label: e.label, color: e.highlight ? 'accent' : 'muted' });
    });
    nodes.forEach(function (nd) {
      s += H.rect(nd.x - nd.bw / 2, nd.y - nd.bh / 2, nd.bw, nd.bh, { fill: 'var(--card)', stroke: nd.hl ? 'accent' : 'primary', width: nd.hl ? 2.8 : 1.6, rx: 10 });
      s += T(nd.x, nd.y + (nd.sub ? -4 : 5), nd.label, { size: 13.5, bold: true });
      if (nd.sub) s += T(nd.x, nd.y + 16, nd.sub, { size: 12.5, color: nd.hl ? 'accent' : 'muted' });
    });
    return H.svg(w, h, s);
  };

  /* place several panels (svg/html strings) side by side; stacks on narrow screens */
  H.grid = function (cols, items) {
    if (Array.isArray(cols)) { items = cols; cols = items.length; }
    cols = Math.max(1, Math.min(2, Math.round(n(cols, 2))));   /* more than 2 columns makes labels unreadable */
    return '<div class="pg-grid" style="--cols:' + cols + '">' + arr(items).map(function (it) {
      var m = /<svg[^>]* width="([0-9.]+)"/.exec(it || '');   /* wide drawings get a full row so they stay readable */
      return '<div class="pg-cell' + (m && Number(m[1]) > 520 ? ' wide' : '') + '">' + (it || '') + '</div>';
    }).join('') + '</div>';
  };
  H.note = function (str) { return '<p class="pg-note">' + H.esc(str) + '</p>'; };
  return H;
})();

var PG = (function () {
  'use strict';
  var PG = {};
  function clone(v) { return JSON.parse(JSON.stringify(v)); }
  function num(v, c, fb) {
    v = Number(v); if (!isFinite(v)) return fb;
    if (typeof c.min === 'number') v = Math.max(c.min, v);
    if (typeof c.max === 'number') v = Math.min(c.max, v);
    return v;
  }
  /* force any value into the shape/range a control allows */
  PG.coerce = function (c, v) {
    var d = c.default, i, j, o;
    if (c.type === 'toggle') return v === true || v === 'true' || v === 1;
    if (c.type === 'select') { for (i = 0; i < c.options.length; i++) if (String(c.options[i].value) === String(v)) return c.options[i].value; return d; }
    if (c.type === 'vector') {
      v = Array.isArray(v) ? v : []; o = [];
      for (i = 0; i < c.length; i++) o.push(num(i < v.length ? v[i] : d[i], c, d[i]));
      return o;
    }
    if (c.type === 'matrix') {
      v = Array.isArray(v) ? v : []; o = [];
      for (i = 0; i < c.rows; i++) { o.push([]); for (j = 0; j < c.cols; j++) o[i].push(num(Array.isArray(v[i]) && j < v[i].length ? v[i][j] : d[i][j], c, d[i][j])); }
      return o;
    }
    return num(v, c, d);
  };
  PG.defaults = function (controls) { var raw = {}; controls.forEach(function (c) { raw[c.id] = clone(c.default); }); return raw; };
  PG.merge = function (controls, raw, over) {
    var out = clone(raw), unknown = [];
    over = over || {};
    Object.keys(over).forEach(function (k) {
      var c = null; controls.forEach(function (x) { if (x.id === k) c = x; });
      if (!c) { unknown.push(k); return; }
      out[k] = PG.coerce(c, over[k]);
    });
    return { raw: out, unknown: unknown };
  };
  /* the part of a vector/matrix in use: its size slider's value, clamped to 1..declared size. raw always keeps
     the full declared size, so shrinking and growing again never loses entries */
  PG.size = function (c, raw) {
    function pick(src, max) { var v = src ? Number(raw[src]) : NaN; return isFinite(v) ? Math.max(1, Math.min(max, Math.round(v))) : max; }
    if (c.type === 'vector') return { len: pick(c.lengthFrom, c.length) };
    if (c.type === 'matrix') return { rows: pick(c.rowsFrom, c.rows), cols: pick(c.colsFrom, c.cols) };
    return {};
  };
  /* the object handed to compute()/render(): vectors and matrices tied to size sliders are cut to that size */
  PG.params = function (controls, raw) {
    var p = clone(raw);
    controls.forEach(function (c) {
      var s = PG.size(c, raw);
      if (c.type === 'vector' && c.lengthFrom) p[c.id] = p[c.id].slice(0, s.len);
      else if (c.type === 'matrix' && (c.rowsFrom || c.colsFrom)) p[c.id] = p[c.id].slice(0, s.rows).map(function (r) { return r.slice(0, s.cols); });
    });
    return p;
  };
  PG.get = function (obj, path) {
    var parts = String(path).split(/[.\[\]]+/).filter(function (s) { return s !== ''; }), cur = obj;
    for (var i = 0; i < parts.length; i++) { if (cur === null || cur === undefined) return undefined; cur = cur[parts[i]]; }
    return cur;
  };
  PG.expect = function (expr, out, p) {
    try { return { pass: !!(new Function('out', 'p', 'H', 'return (' + expr + ');'))(out, p, H) }; }
    catch (e) { return { pass: false, error: String(e && e.message || e) }; }
  };
  return PG;
})();
