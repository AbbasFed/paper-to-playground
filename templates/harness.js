/* Offline checker, run inside the embedded JS engine after helpers.js and the generated
   compute()/render() have been loaded. Returns a JSON string describing what happened. */
function __runChecks(spec) {
  'use strict';
  var controls = spec.controls, tests = spec.tests || [], readouts = spec.readouts || [];
  var R = { fatal: null, defaults: null, settings: 0, edgeErrors: [], edgeNaN: [], edgeInf: [], inert: [], tests: [], invariant: [],
    render: null, renderEdge: [], readoutsMissing: [], outKeys: [], resize: [], resizeSettings: 0 };

  function short(v, k) { var s; try { s = JSON.stringify(v); } catch (e) { s = String(v); } s = String(s); return s.length > k ? s.slice(0, k) + '…' : s; }
  function bad(v, path, acc, depth) {
    if (acc.nan.length + acc.inf.length > 6 || depth > 6) return;
    if (typeof v === 'number') { if (isNaN(v)) acc.nan.push(path); else if (!isFinite(v)) acc.inf.push(path); return; }
    if (v && typeof v === 'object') Object.keys(v).forEach(function (k) { bad(v[k], path ? path + (Array.isArray(v) ? '[' + k + ']' : '.' + k) : k, acc, depth + 1); });
  }
  function run(raw) {
    var p = PG.params(controls, raw), out = compute(p);
    if (!out || typeof out !== 'object' || Array.isArray(out)) throw new Error('compute() must return a plain object of named values');
    return { p: p, out: out };
  }
  function draw(p, out) {
    var s = render(p, out);
    if (typeof s !== 'string' || s.indexOf('<svg') < 0) return 'render() must return a string containing an <svg> element';
    if (s.length > 400000) return 'render() output is too large (' + s.length + ' chars)';
    var m = />[^<]*\b(NaN|undefined)\b[^<]*</.exec(s) || /\[object Object\]/.exec(s);
    if (m) return 'the visual displays "' + m[0].replace(/[<>]/g, '').slice(0, 60) + '" (a label or value is NaN/undefined)';
    if (/="[^"]*\bNaN\b/.test(s)) return 'the visual has NaN coordinates/sizes';
    return null;
  }

  if (typeof compute !== 'function') { R.fatal = 'compute is not defined as a function'; return JSON.stringify(R); }
  if (typeof render !== 'function') R.render = 'render is not defined as a function';

  var def = PG.defaults(controls), base, baseStr;
  try {
    base = run(def); baseStr = JSON.stringify(base.out);
    var acc = { nan: [], inf: [] }; bad(base.out, '', acc, 0);
    R.outKeys = Object.keys(base.out);
    if (acc.nan.length || acc.inf.length) R.defaults = 'non-finite outputs at default controls: ' + acc.nan.concat(acc.inf).join(', ');
  } catch (e) { R.fatal = 'compute(defaults) threw: ' + String(e && e.message || e); return JSON.stringify(R); }

  if (!R.render) {
    try { R.render = draw(base.p, base.out); } catch (e2) { R.render = 'render(defaults) threw: ' + String(e2 && e2.message || e2); }
  }
  readouts.forEach(function (r) { var v = PG.get(base.out, r.key); if (v === undefined || typeof v === 'function') R.readoutsMissing.push(r.key); });

  /* settings to sweep: every control at its extremes / every option */
  var sweep = [];
  function add(c, label, v) { var raw = JSON.parse(JSON.stringify(def)); raw[c.id] = PG.coerce(c, v); sweep.push({ id: c.id, label: c.id + '=' + label, raw: raw }); }
  controls.forEach(function (c) {
    if (c.type === 'slider' || c.type === 'number') {
      add(c, c.min, c.min); add(c, c.max, c.max);
      var mid = c.min + Math.round((c.max - c.min) / 2 / c.step) * c.step; if (mid !== c.default) add(c, mid, mid);
    } else if (c.type === 'toggle') { add(c, 'true', true); add(c, 'false', false); }
    else if (c.type === 'select') c.options.forEach(function (o) { add(c, o.value, o.value); });
    else if (c.type === 'vector') {
      var fill = function (v) { return c.default.map(function () { return v; }); };
      add(c, 'all ' + c.min, fill(c.min)); add(c, 'all ' + c.max, fill(c.max));
      var one = fill(c.min); one[0] = c.max; add(c, 'first ' + c.max + ', rest ' + c.min, one);
      var bump = c.default.slice(); bump[bump.length - 1] = bump[bump.length - 1] === c.max ? c.min : c.max; add(c, 'last element changed', bump);
    } else if (c.type === 'matrix') {
      var fm = function (v) { return c.default.map(function (r) { return r.map(function () { return v; }); }); };
      add(c, 'all ' + c.min, fm(c.min)); add(c, 'all ' + c.max, fm(c.max));
      var b2 = JSON.parse(JSON.stringify(c.default)); b2[0][0] = b2[0][0] === c.max ? c.min : c.max; add(c, 'first entry changed', b2);
    }
  });
  /* second base: other controls moved off their defaults, so a control that only matters
     in some situations (e.g. a toggle that needs unequal inputs) is not called inert */
  var alt = JSON.parse(JSON.stringify(def));
  controls.forEach(function (c) {
    var ramp = function (i, k) { return c.min + Math.round((c.max - c.min) * (i + 1) / (k + 1) / c.step) * c.step; };
    if (c.type === 'vector') alt[c.id] = PG.coerce(c, c.default.map(function (_, i) { return ramp(i, c.length); }));
    else if (c.type === 'matrix') alt[c.id] = PG.coerce(c, c.default.map(function (r, i) { return r.map(function (_, j) { return ramp(i * c.cols + j, c.rows * c.cols); }); }));
  });
  /* further bases: each toggle state / select option of the ramped base, because one control often gates another */
  var bases = [alt];
  controls.forEach(function (c) {
    var vals = c.type === 'toggle' ? [true, false] : c.type === 'select' ? c.options.map(function (o) { return o.value; }) : [];
    vals.forEach(function (v) { if (bases.length < 10 && v !== def[c.id]) { var b = JSON.parse(JSON.stringify(alt)); b[c.id] = v; bases.push(b); } });
  });
  bases = bases.map(function (b) { try { return { raw: b, str: JSON.stringify(run(b).out) }; } catch (e) { return null; } }).filter(Boolean);
  var invariants = tests.filter(function (t) { return !t.params || !Object.keys(t.params).length; });
  var changed = {}, seenInv = {};
  sweep.forEach(function (s) {
    var r;
    try { r = run(s.raw); } catch (e) { if (R.edgeErrors.length < 6) R.edgeErrors.push('compute threw at ' + s.label + ': ' + String(e && e.message || e)); return; }
    if (JSON.stringify(r.out) !== baseStr) changed[s.id] = 1;
    else if (!changed[s.id]) bases.forEach(function (bs) {
      if (changed[s.id]) return;
      try { var a2 = JSON.parse(JSON.stringify(bs.raw)); a2[s.id] = s.raw[s.id]; if (JSON.stringify(run(a2).out) !== bs.str) changed[s.id] = 1; } catch (e4) {}
    });
    var acc = { nan: [], inf: [] }; bad(r.out, '', acc, 0);
    if (acc.nan.length && R.edgeNaN.length < 6) R.edgeNaN.push('NaN at ' + s.label + ' in out.' + acc.nan.slice(0, 3).join(', out.'));
    if (acc.inf.length && R.edgeInf.length < 6) R.edgeInf.push('Infinity at ' + s.label + ' in out.' + acc.inf.slice(0, 3).join(', out.'));
    if (typeof render === 'function') {
      var msg;
      try { msg = draw(r.p, r.out); } catch (e3) { msg = 'render threw: ' + String(e3 && e3.message || e3); }
      if (msg && R.renderEdge.length < 6) R.renderEdge.push('at ' + s.label + ': ' + msg);
    }
    var declared = typeof r.out.warning === 'string' && r.out.warning.trim() !== '';   /* compute() says: invalid setting */
    if (declared) R.warned = (R.warned || 0) + 1;
    if (!acc.nan.length && !declared) invariants.forEach(function (t) {
      if (seenInv[t.name]) return;
      var v = PG.expect(t.expect, r.out, r.p);
      if (!v.pass) { seenInv[t.name] = 1; R.invariant.push({ name: t.name, at: s.label, error: v.error || '', got: short(r.out, 300) }); }
    });
  });
  R.settings = sweep.length;
  controls.forEach(function (c) { if (!changed[c.id]) R.inert.push(c.id); });

  /* resizing: every vector/matrix tied to a size slider runs at every size that slider allows (1 included);
     compute() must get exactly that shape, and compute/render must work at each size */
  var byId = {};
  controls.forEach(function (c) { byId[c.id] = c; });
  function resizeFail(m) { if (R.resize.length < 6) R.resize.push(m); }
  controls.forEach(function (c) {
    [c.lengthFrom, c.rowsFrom, c.colsFrom].forEach(function (src) {
      var s = src && byId[src];
      if (!s || (c.type !== 'vector' && c.type !== 'matrix')) return;
      for (var n = Math.max(1, Math.ceil(s.min)); n <= Math.floor(s.max) && n <= 16; n++) {
        var raw = JSON.parse(JSON.stringify(def)), label = src + '=' + n, r;
        raw[src] = n;
        try { r = run(raw); } catch (e) { resizeFail('compute threw at ' + label + ': ' + String(e && e.message || e)); continue; }
        R.resizeSettings++;
        var got = r.p[c.id], want = PG.size(c, raw);
        var shapeOk = Array.isArray(got) && (c.type === 'vector' ? got.length === want.len
          : got.length === want.rows && got.every(function (row) { return Array.isArray(row) && row.length === want.cols; }));
        if (!shapeOk) resizeFail(c.id + ' was not passed to compute() as ' + (c.type === 'vector' ? want.len + ' entries' : want.rows + 'x' + want.cols) + ' at ' + label);
        var acc = { nan: [], inf: [] }; bad(r.out, '', acc, 0);
        if (acc.nan.length) resizeFail('NaN at ' + label + ' in out.' + acc.nan.slice(0, 3).join(', out.'));
        if (typeof render === 'function') {
          var m2;
          try { m2 = draw(r.p, r.out); } catch (e2) { m2 = 'render threw: ' + String(e2 && e2.message || e2); }
          if (m2) resizeFail('at ' + label + ': ' + m2);
        }
      }
    });
  });

  tests.forEach(function (t) {
    var m = PG.merge(controls, def, t.params || {}), rec = { name: t.name, pass: false, error: '', got: '' };
    if (m.unknown.length) { rec.error = 'params uses unknown control id(s): ' + m.unknown.join(', '); R.tests.push(rec); return; }
    try {
      var r = run(m.raw), v = PG.expect(t.expect, r.out, r.p);
      rec.pass = v.pass; rec.error = v.error || '';
      if (!v.pass) rec.got = 'p=' + short(r.p, 200) + ' out=' + short(r.out, 400);
    } catch (e) { rec.error = 'compute threw: ' + String(e && e.message || e); }
    R.tests.push(rec);
  });

  /* numbers quoted in an exploration's "observe" text must be numbers the calculation really
     produces at the settings that exploration describes (model prose is otherwise unchecked) */
  R.explorationNumbers = [];
  /* a decimal, optionally in scientific notation: 1.69e-5, 1.69 x 10^-5, 1.69·10^{−5} */
  var NUM = /[\u2212-]?\d+\.\d+(?:(?:[eE]|\s*[\u00d7x\u00b7*]\s*10\s*\^?\s*\{?\s*)([\u2212-]?\d+)\}?)?/g;
  var SUPER = { '\u207b': '-', '\u2070': '0', '\u00b9': '1', '\u00b2': '2', '\u00b3': '3', '\u2074': '4', '\u2075': '5', '\u2076': '6', '\u2077': '7', '\u2078': '8', '\u2079': '9' };
  function text(s) {
    return String(s || '').replace(/<sup>\s*([^<]*)<\/sup>/g, '^$1').replace(/<[^>]+>/g, ' ')
      .replace(/[\u207b\u2070\u00b9\u00b2\u00b3\u2074-\u2079]+/g, function (m) { return '^' + m.split('').map(function (ch) { return SUPER[ch]; }).join(''); });
  }
  function toNum(s) { return Number(String(s).replace(/\u2212/g, '-')); }
  function parse(tok) {   /* -> {x: magnitude, tol: half a unit in the last quoted digit} */
    var m = /^([\u2212-]?\d+\.(\d+))(?:(?:[eE]|\s*[\u00d7x\u00b7*]\s*10\s*\^?\s*\{?\s*)([\u2212-]?\d+)\}?)?$/.exec(tok);
    var e = m && m[3] !== undefined ? toNum(m[3]) : 0, mant = m ? toNum(m[1]) : toNum(tok), d = m ? m[2].length : 0;
    return { x: Math.abs(mant) * Math.pow(10, e), tol: 0.6 * Math.pow(10, e - d) };
  }
  function gather(v, acc, depth) {
    if (typeof v === 'number' && isFinite(v)) acc.push(Math.abs(v));
    else if (v && typeof v === 'object' && depth < 4) Object.keys(v).forEach(function (k) { gather(v[k], acc, depth + 1); });
  }
  (spec.explorations || []).forEach(function (ex, idx) {
    var quoted = text(ex.observe).match(NUM) || [];
    if (!quoted.length) return;
    var base = PG.merge(controls, def, ex.set || {}).raw, states = [base, def], seen = [];
    /* "set SNR to 20, then to -10": also try each number named in the change text on each numeric control */
    var named = (text(ex.change).match(/[\u2212-]?\d+(?:\.\d+)?/g) || []).map(toNum);
    controls.forEach(function (c) {
      if (c.type !== 'slider' && c.type !== 'number') return;
      named.forEach(function (v) {
        if (states.length >= 30 || v < c.min || v > c.max) return;
        var s2 = JSON.parse(JSON.stringify(base)); s2[c.id] = v; states.push(s2);
      });
    });
    /* "... then switch scaling off": every state is also tried with each toggle flipped / each option chosen */
    controls.forEach(function (c) {
      var vals = c.type === 'toggle' ? [true, false] : c.type === 'select' ? c.options.map(function (o) { return o.value; }) : [];
      states.slice().forEach(function (st) {
        vals.forEach(function (v) {
          if (states.length >= 90 || st[c.id] === v) return;
          var s3 = JSON.parse(JSON.stringify(st)); s3[c.id] = v; states.push(s3);
        });
      });
    });
    var scal = [];
    states.forEach(function (st, k) {
      try {
        var r = run(st); gather(r.out, seen, 0); gather(r.p, seen, 0);
        if (k === 0) Object.keys(r.out).forEach(function (key) { if (typeof r.out[key] === 'number' && scal.length < 10) scal.push(key + '=' + H.fmt(r.out[key], 4)); });
      } catch (e) {}
    });
    var missing = [];
    quoted.forEach(function (tok) {
      var q = parse(tok), x = q.x, tol = q.tol;
      var ok = seen.some(function (v) { return Math.abs(v - x) <= tol || Math.abs(v * 100 - x) <= tol; });   /* also as a percentage */
      if (!ok && missing.indexOf(tok) < 0) missing.push(tok);
    });
    if (missing.length) R.explorationNumbers.push({ index: idx + 1, numbers: missing, computed: scal.join(', ') });
  });
  return JSON.stringify(R);
}
