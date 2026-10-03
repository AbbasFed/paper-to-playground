#!/usr/bin/env python3
"""Dev-only QA: open generated explainer pages in Chromium and operate them the way the assessor will.

  python dev/browser_check.py PATH [PATH ...] [--report-dir dev/reports] [--headed] [--issues]

PATH is an index.html or a directory (searched recursively for index.html). Each page is served from its
own folder by a local http.server, opened with every non-local request blocked, and run through CHECKS.
Writes <report-dir>/<page>/report.json + 4 screenshots, prints one summary table.
Exit code: 0 = every page passes (WARN allowed), 1 = some page FAILs, 2 = tool error.
Setup: pip install -r dev/requirements-dev.txt && python -m playwright install chromium
"""
import argparse, functools, http.server, json, math, os, re, sys, threading, time
from pathlib import Path
from urllib.parse import urlparse

# Real ids/classes come from the page template (p2p/assets/dom.js + core.js); the regexes are fallbacks
# for pages that don't use it. Keep every selector here so a template change is a one-place update.
SELECTORS = {
    "controls": "input[type=range], input[type=number], input[type=checkbox], select, "
                "table input:not([type]), table input[type=text]",
    "buttons": "button, input[type=button], input[type=submit], input[type=reset], [role=button]",
    "readouts": "#views .tile, #views table, .readout, .readouts, [data-readout]",
    "error": "#error .err, #views .err, [data-error]:not(:empty)",   # inline "something went wrong"
    "snapshot_exclude": "#controls, label, output",   # control echoes don't count as "the page reacted"
    "headings": "h1, h2, h3, h4, h5, h6, summary, legend, caption, figcaption, dt, [role=heading], .flabel",
    "sections": {   # css first (template), else >= min visible headings matching rx (JS regex, case-insensitive)
        "idea": {"css": "#idea", "rx": r"\bidea\b|\bintro|overview|big picture|what is"},
        "why it matters": {"css": "", "rx": r"why (it|this) matters|why (it|this) is (useful|important)|motivation"},
        "symbols": {"css": "table.symbols", "rx": r"symbol|notation|glossary|what .* mean"},
        "how it works": {"css": ".formula", "rx": r"how it works|step|mechanism|relationship|algorithm|procedure"},
        "playground": {"css": "#play, #controls", "rx": r"try it|playground|interactive|experiment|simulat"},
        "2 explorations": {"css": "#expl .ex, .exploration", "rx": r"\bexploration\b", "min": 2},
        "limitation": {"css": "#caveat", "rx": r"limitation|assumption|misconception|misunderstanding|watch out|caveat|pitfall"},
        "source grounding": {"css": "#source", "rx": r"source|grounding|reference|citation"},
    },
    "texts": {   # Python regex over visible text -> status when missing
        "'from the paper' label": (r"from the paper|according to the paper|attributed to the paper|supported by the (excerpt|paper)", "FAIL"),
        "'simplification' label": (r"simplif", "FAIL"),
        "no-reproduction disclaimer": (r"(not|n['’]t|never)\s+(\w+\s+){0,2}(reproduc|replicat)|not a reproduction", "FAIL"),
        "section/equation locator": (r"(\b(section|sec\.|eq\.?|equation)|§)\s*\(?\d", "WARN"),
    },
}
PASS, WARN, FAIL, ERROR = "PASS", "WARN", "FAIL", "ERROR"
ORDER = {PASS: 0, WARN: 1, FAIL: 2, ERROR: 3}
CHECKS = ["offline", "load", "structure", "controls", "interaction", "edges", "buttons", "layout", "readability", "size"]
RANKS = ["crash", "wrong/NaN values", "dead controls", "missing content", "layout", "readability", "other"]
LOCAL = {"localhost", "127.0.0.1", "::1"}
SHOTS = {"desktop_light": (1280, 900, "light"), "desktop_dark": (1280, 900, "dark"),
         "phone_light": (390, 844, "light"), "phone_dark": (390, 844, "dark")}
MAX_FAIL_SHOTS = 6
STATIC_REMOTE = re.compile(r"<(?:script|img|iframe|source|video|audio|embed)\b[^>]*\bsrc\s*=\s*[\"']?(?:https?:)?//[^\s\"'>]*"
                           r"|<link\b[^>]*\bhref\s*=\s*[\"']?(?:https?:)?//[^\s\"'>]*"
                           r"|url\(\s*[\"']?(?:https?:)?//[^\s\"')]*|@import\s+[\"'](?:https?:)?//[^\s\"']*", re.I)

JS = r"""
(() => {
  const S = __SEL__;
  const vis = e => {
    if (!e || !e.isConnected) return false;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && (!e.checkVisibility || e.checkVisibility({checkVisibilityCSS: true, visibilityProperty: true}));
  };
  const desc = e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + [...e.classList].slice(0, 2).map(c => '.' + c).join('');
  const svgs = () => [...document.querySelectorAll('svg')].filter(s => vis(s) && !s.parentElement.closest('svg'));
  window.__bc = {
    /* what an assessor would notice changing: SVG markup + readout text + visible numbers outside the controls */
    snap() {
      const nums = [], w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let n; (n = w.nextNode());) {
        const p = n.parentElement;
        if (!p || p.closest('script,style,svg,' + S.snapshot_exclude) || !vis(p)) continue;
        nums.push(...(n.nodeValue.match(/-?\d+(?:\.\d+)?(?:e[-+]?\d+)?/gi) || []));
      }
      return svgs().map(s => s.outerHTML).join('') + '\u0001' +
        [...document.querySelectorAll(S.readouts)].filter(vis).map(e => e.innerText).join('|') + '\u0001' + nums.join(' ');
    },
    state() {
      const txt = document.body.innerText || '', re = /\b(NaN|Infinity|undefined)\b/g, samples = [];
      let bad = 0, attr = 0, shapes = 0;
      for (let m; (m = re.exec(txt));) { bad++; if (samples.length < 5) samples.push(txt.slice(Math.max(0, m.index - 40), m.index + 20).replace(/\s+/g, ' ')); }
      const ss = svgs();
      ss.forEach(s => s.querySelectorAll('*').forEach(e => {
        if (/^(rect|circle|ellipse|line|path|polyline|polygon|text|image|use)$/.test(e.tagName)) shapes++;
        for (const a of e.attributes) if (/NaN|Infinity|undefined/.test(a.value)) attr++;
      }));
      const err = [...document.querySelectorAll(S.error)].filter(vis).map(e => e.innerText.trim()).join(' | ').slice(0, 160);
      return {snap: this.snap(), bad, samples, attr, svgs: ss.length, shapes, err};
    },
    controls() {
      const out = [];
      document.querySelectorAll(S.controls).forEach(e => {
        if (!vis(e) || e.disabled || e.readOnly) return;
        const tag = e.tagName.toLowerCase(), type = (e.getAttribute('type') || '').toLowerCase(), d = e.dataset;
        const kind = tag === 'select' ? 'select' : ['range', 'number', 'checkbox'].includes(type) ? type : 'text';
        let sel = tag + (type ? '[type="' + type + '"]' : tag === 'input' ? ':not([type])' : '');
        if (e.id) sel += '#' + CSS.escape(e.id);
        else if (d.id != null) {
          sel += '[data-id=' + JSON.stringify(d.id) + ']';
          for (const k of ['i', 'j']) sel += d[k] != null ? '[data-' + k + '=' + JSON.stringify(d[k]) + ']' : ':not([data-' + k + '])';
        } else if (e.name) sel += '[name=' + JSON.stringify(e.name) + ']';
        const nth = [...document.querySelectorAll(sel)].indexOf(e);
        const key = d.id != null ? [d.id, d.i, d.j].filter(x => x != null).join('.') : (e.id || e.name || sel + '@' + nth);
        const num = a => { const v = parseFloat(e.getAttribute(a)); return isFinite(v) ? v : null; };
        const lab = e.getAttribute('aria-label') || (e.labels && e.labels[0] ? e.labels[0].innerText : '') || e.title || key;
        out.push({sel, nth, key, kind, label: lab.trim().replace(/\s+/g, ' ').slice(0, 60), min: num('min'), max: num('max'),
                  step: num('step'), options: tag === 'select' ? e.options.length : 0});
      });
      return out;
    },
    buttons() {
      return [...document.querySelectorAll(S.buttons)].map((e, i) => ({i, ok: vis(e) && !e.disabled,
        text: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 50)})).filter(b => b.ok);
    },
    structure() {
      const H = [...document.querySelectorAll(S.headings)].filter(vis).map(e => e.innerText.trim().replace(/\s+/g, ' ')).filter(Boolean);
      const sections = {};
      for (const [k, d] of Object.entries(S.sections)) {
        const min = d.min || 1, css = d.css ? [...document.querySelectorAll(d.css)].filter(vis) : [];
        const rx = new RegExp(d.rx, 'i'), hs = H.filter(h => rx.test(h));
        sections[k] = {ok: css.length >= min || hs.length >= min, n: Math.max(css.length, hs.length),
          how: css.length >= min ? 'css ' + d.css : hs.length ? ('heading "' + hs.slice(0, 2).join('" / "')).slice(0, 90) + '"' : 'not found'};
      }
      const ss = svgs().map(s => s.getBoundingClientRect()).sort((a, b) => b.width * b.height - a.width * a.height);
      return {sections, svgs: ss.length, big: ss.length ? Math.round(ss[0].width) + 'x' + Math.round(ss[0].height) : '', text: document.body.innerText};
    },
    readability(minPx) {
      const t = [...document.querySelectorAll('svg text')].filter(e => vis(e) && e.textContent.trim()), small = [], over = [];
      const boxes = t.map(e => {
        const m = e.getScreenCTM(), px = parseFloat(getComputedStyle(e).fontSize) * (m ? Math.sqrt(Math.abs(m.a * m.d - m.b * m.c)) : 1);
        if (px < minPx) small.push({text: e.textContent.trim().slice(0, 30), px: +px.toFixed(1)});
        return {r: e.getBoundingClientRect(), s: e.textContent.trim().slice(0, 30)};
      });
      for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i].r, b = boxes[j].r;
        const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        const f = w > 0 && h > 0 ? w * h / Math.min(a.width * a.height, b.width * b.height) : 0;
        if (f > 0.3) over.push({a: boxes[i].s, b: boxes[j].s, frac: +f.toFixed(2)});
      }
      return {n: t.length, small, over};
    },
    overflow() {
      const de = document.documentElement, cw = de.clientWidth, sw = Math.max(de.scrollWidth, document.body.scrollWidth), off = [];
      if (sw > cw + 2) for (const e of document.querySelectorAll('body *')) {
        const r = e.getBoundingClientRect(), p = e.parentElement.getBoundingClientRect();
        if (r.width > 0 && r.right > cw + 2 && p.right <= cw + 2) off.push(desc(e) + ' (right edge ' + Math.round(r.right) + 'px)');
        if (off.length >= 4) break;
      }
      return {sw, cw, off};
    },
  };
})();
"""
SET_JS = """(e, v) => {
  if (e.type === 'checkbox') e.checked = !e.checked;
  else if (e.tagName === 'SELECT') e.selectedIndex = (e.selectedIndex + 1) % e.options.length;
  else e.value = v;
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
}"""
SETTLE_JS = "() => Promise.race([new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))), new Promise(r => setTimeout(r, 200))])"


def rel(p):
    try:
        return os.path.relpath(p).replace(os.sep, "/")
    except ValueError:
        return str(p)


def first_line(e):
    return str(e).strip().splitlines()[0][:200] if str(e).strip() else type(e).__name__


def fmt(v):
    return str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:.6g}"


def rng(c):
    """(min, max, step) of a numeric control, with the HTML defaults for range inputs."""
    lo, hi, st = c["min"], c["max"], c["step"]
    if c["kind"] == "range":
        lo, hi = (0.0 if lo is None else lo), (100.0 if hi is None else hi)
    return lo, hi, (st if st and st > 0 else (1.0 if c["kind"] == "range" else None))


def pick(cur, c, frac):
    """A valid value roughly frac*range away from cur (other direction if that leaves the range)."""
    lo, hi, st = rng(c)
    try:
        x = float(cur)
    except (TypeError, ValueError):
        x = lo if lo is not None else 0.0
    if lo is not None and hi is not None and hi > lo:
        v = x + frac * (hi - lo)
        if not lo <= v <= hi:
            v = x - frac * (hi - lo)
        v = min(max(v, lo), hi)
    else:
        v = x + math.copysign(st or 1.0, frac)
    if st:
        base = lo if lo is not None else 0.0
        v = base + round((v - base) / st) * st
    if abs(v - x) < 1e-9:   # rounding landed back on the old value
        v = x + (st or 1.0) * (1 if hi is None or x + (st or 1.0) <= hi else -1)
    return fmt(v)


def edge_plan(c):
    """(op, arg, valid, label) edge cases for one control."""
    k, (lo, hi, st) = c["kind"], rng(c)
    if k == "range":
        return [("Home", None, True, "min (Home key)"), ("End", None, True, "max (End key)")]
    if k == "checkbox":
        return [("check", True, True, "checked"), ("check", False, True, "unchecked")]
    if k == "select":
        return [("option", i, True, f"option {i + 1}/{c['options']}") for i in range(c["options"])]
    plan = []
    if k == "number":
        plan += [("fill", fmt(v), True, f"{n} {fmt(v)}") for n, v in (("min", lo), ("max", hi)) if v is not None]
        s = (st or 1.0) * 10
        for v in (fmt(hi + s) if hi is not None else "1000000", fmt(lo - s) if lo is not None else "-1000000"):
            plan.append(("fill", v, False, f"out-of-range {v}"))
    plan.append(("fill", "", False, "empty input"))
    if k == "text":
        plan.append(("fill", "abc", False, "non-numeric 'abc'"))
    return plan


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(folder):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(folder)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class PageCheck:
    def __init__(self, browser, index, out_dir, name):
        self.browser, self.index, self.dir, self.name = browser, Path(index), Path(out_dir), name
        self.findings, self.notes, self.events, self.remote, self.shots, self.ctl = [], {}, [], [], {}, []
        self.phase, self.fail_shots, self.meaningful, self.overflow, self.error = "load", 0, 0, 0, None
        self.url, self.base, self.seen_warnings = "", {}, set()

    # ------------------------------------------------------------------ plumbing
    def find(self, check, status, rank, symptom, evidence="", control=None, shot=None):
        self.findings.append({"check": check, "status": status, "rank": rank, "symptom": symptom[:200],
                              "evidence": first_line(evidence) if evidence else "", "control": control, "shot": shot})

    def ev(self, expr, arg=None):
        return self.page.evaluate(expr, arg)

    def settle(self, ms=60):
        self.ev(SETTLE_JS)
        self.page.wait_for_timeout(ms)

    def reload(self):
        phase, self.phase = self.phase, "reload"   # load-time noise was already reported once
        self.page.reload(wait_until="load")
        self.settle(150)
        self.phase = phase

    def wait_change(self, before, timeout=0.6):
        t0 = time.time()
        while True:
            self.settle(30)
            now = self.ev("__bc.snap()")
            if now != before or time.time() - t0 > timeout:
                return now

    def on_route(self, route):
        # Offline emulation. Playwright's offline=True also blocks 127.0.0.1 (net::ERR_INTERNET_DISCONNECTED),
        # so the page could not be served locally; instead every non-local request fails the way it would offline.
        u = urlparse(route.request.url)
        if u.hostname in LOCAL:
            return route.fulfill(status=204, body="") if u.path == "/favicon.ico" else route.continue_()
        self.remote.append(f"{route.request.resource_type}: {route.request.url[:150]}")
        route.abort("internetdisconnected")

    def on_console(self, m):
        if m.type in ("error", "warning"):
            self.events.append({"phase": self.phase, "kind": m.type, "text": m.text[:300]})

    def after(self, check, n0, cid, valid, what):
        """Inspect page health after an action; FAIL findings get a screenshot of the broken state."""
        i0 = len(self.findings)
        for e in self.events[n0:]:
            if e["kind"] == "warning":
                if e["text"] not in self.seen_warnings:   # the same warning once per page is enough
                    self.seen_warnings.add(e["text"])
                    self.find(check, WARN, 6, f"console warning after {what}", e["text"], cid)
            else:
                self.find(check, FAIL, 0, ("uncaught exception" if e["kind"] == "pageerror" else "console error") + f" after {what}", e["text"], cid)
        st = self.ev("__bc.state()")
        if self.base["svgs"] and (not st["svgs"] or not st["shapes"]):
            self.find(check, FAIL, 0, f"SVG blank or gone after {what}", f"{st['svgs']} visible svg, {st['shapes']} shapes", cid)
        if st["attr"] > self.base["attr"] or st["bad"] > self.base["bad"]:
            new = [s for s in st["samples"] if s not in self.base["samples"]] or [f"{st['attr']} SVG attributes contain NaN/Infinity"]
            self.find(check, FAIL if valid else WARN, 1, f"NaN/Infinity/undefined after {what}", new[0], cid)
        if valid and st["err"] and not self.base["err"]:
            self.find(check, FAIL, 1, f"inline error shown after {what}", st["err"], cid)
        if any(f["status"] == FAIL for f in self.findings[i0:]) and self.fail_shots < MAX_FAIL_SHOTS:
            self.fail_shots += 1
            safe = re.sub(r"[^\w.-]+", "_", str(cid))[:30]
            path = self.dir / f"fail_{self.fail_shots}_{safe}.png"
            self.page.screenshot(path=str(path), full_page=True)
            for f in self.findings[i0:]:
                f["shot"] = rel(path)
        return st

    # ------------------------------------------------------------------ checks
    def run(self):
        srv = serve(self.index.parent)
        ctx = None
        try:
            ctx = self.browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme="light", service_workers="block")
            ctx.add_init_script(JS.replace("__SEL__", json.dumps(SELECTORS)))
            ctx.route("**/*", self.on_route)
            self.page = page = ctx.new_page()
            page.set_default_timeout(4000)
            page.on("pageerror", lambda e: self.events.append({"phase": self.phase, "kind": "pageerror", "text": str(e)[:300]}))
            page.on("console", self.on_console)
            page.on("dialog", lambda d: d.dismiss())
            page.on("websocket", lambda ws: urlparse(ws.url).hostname in LOCAL or self.remote.append("websocket: " + ws.url))
            self.url = f"http://127.0.0.1:{srv.server_address[1]}/{self.index.name}"
            try:
                page.goto(self.url, wait_until="load", timeout=15000)
                self.settle(300)
            except Exception as e:
                self.find("load", FAIL, 0, "page did not load", e)
                return self.result()
            self.check_load()
            self.check_structure()
            self.check_layout()
            self.check_readability()
            self.check_controls()
            self.check_buttons()
        except Exception as e:   # tool problem, not a page problem: keep partial results, exit 2
            self.error = f"{type(e).__name__}: {first_line(e)}"
        finally:
            self.check_offline_and_size()
            if ctx:
                ctx.close()
            srv.shutdown()
            srv.server_close()
        return self.result()

    def check_load(self):
        for e in self.events:
            if e["kind"] == "warning":
                self.find("load", WARN, 6, "console warning on load", e["text"])
            else:
                self.find("load", FAIL, 0, "uncaught exception on load" if e["kind"] == "pageerror" else "console error on load", e["text"])
        st = self.base = self.ev("__bc.state()")
        if st["attr"]:
            self.find("load", FAIL, 1, "SVG attributes contain NaN/Infinity at load", f"{st['attr']} attributes")
        if st["bad"]:
            self.find("load", WARN, 1, "NaN/Infinity/undefined in visible text at load", st["samples"][0])
        if st["err"]:
            self.find("load", FAIL, 1, "inline error visible at load", st["err"])
        self.page.wait_for_timeout(250)
        if self.ev("__bc.snap()") != st["snap"]:
            self.find("interaction", WARN, 6, "page changes on its own (animation?); dead-control detection is unreliable")
        self.notes["load"] = "no page errors, console errors or warnings"

    def check_structure(self):
        s = self.ev("__bc.structure()")
        self.structure = s["sections"]
        if not s["svgs"]:
            self.find("structure", FAIL, 3, "no visible <svg> with nonzero size")
        for k, r in s["sections"].items():
            if not r["ok"]:
                self.find("structure", FAIL, 3, f"missing section: {k}", f"{r['n']} match(es) for {SELECTORS['sections'][k]['css'] or 'headings'} /{SELECTORS['sections'][k]['rx']}/")
        for k, (rx, status) in SELECTORS["texts"].items():
            if not re.search(rx, s["text"], re.I):
                self.find("structure", status, 3, f"missing text: {k}", f"no match for /{rx}/ in visible text")
        self.notes["structure"] = f"{len(s['sections'])} sections + {len(SELECTORS['texts'])} texts found; {s['svgs']} svg (largest {s['big']})"

    def check_layout(self):
        self.phase = "layout"
        for name, (w, h, scheme) in SHOTS.items():
            self.page.set_viewport_size({"width": w, "height": h})
            self.page.emulate_media(color_scheme=scheme)
            self.reload()
            path = self.dir / f"{name}.png"
            self.page.screenshot(path=str(path), full_page=True)
            self.shots[name] = rel(path)
            if w < 500 and scheme == "light":
                o = self.ev("__bc.overflow()")
                self.overflow = o["sw"] - o["cw"]
                if self.overflow > 2:
                    self.find("layout", FAIL, 4, f"horizontal overflow at {w}px wide (+{self.overflow}px)",
                              "; ".join(o["off"]) or f"scrollWidth {o['sw']} > clientWidth {o['cw']}", shot=self.shots[name])
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.emulate_media(color_scheme="light")
        self.reload()
        self.notes["layout"] = "no horizontal overflow at 390px; screenshots: " + ", ".join(self.shots.values())

    def check_readability(self):
        r = self.ev("m => __bc.readability(m)", 9)
        if r["small"]:
            self.find("readability", WARN, 5, f"{len(r['small'])} SVG label(s) render below 9px at 1280px wide",
                      ", ".join(f"'{x['text']}' {x['px']}px" for x in r["small"][:3]), shot=self.shots.get("desktop_light"))
        if r["over"]:
            self.find("readability", WARN, 5, f"{len(r['over'])} overlapping SVG label pair(s)",
                      ", ".join(f"'{o['a']}' / '{o['b']}' {round(o['frac'] * 100)}%" for o in r["over"][:3]), shot=self.shots.get("desktop_light"))
        self.notes["readability"] = f"{r['n']} SVG labels: none under 9px, no overlaps"

    def check_controls(self):
        self.ctl = self.ev("__bc.controls()")
        keys = sorted({c["key"] for c in self.ctl})
        if len(keys) < 2:
            self.find("controls", FAIL, 2, f"only {len(keys)} distinct control(s) found", ", ".join(keys) or "no range/number/checkbox/select inputs")
        self.notes["controls"] = f"{len(keys)} distinct controls ({len(self.ctl)} inputs): {', '.join(keys)}"[:300]
        for c in self.ctl:
            self.test_control(c)
        self.notes["interaction"] = f"{self.meaningful}/{len(self.ctl)} inputs change the visual/readouts both ways"
        self.notes["edges"] = "min/max, every option, both checkbox states, empty/out-of-range: no crash"

    def operate(self, loc, c, method):
        k = c["kind"]
        if method == "js":
            loc.evaluate(SET_JS, pick(loc.input_value(), c, -0.4) if k in ("range", "number", "text") else None)
        elif k == "range":
            lo, hi, st = rng(c)
            v = float(loc.input_value())
            for _ in range(max(1, min(20, round(0.1 * (hi - lo) / st)))):
                loc.press("ArrowRight" if v + st <= hi else "ArrowLeft")
        elif k == "checkbox":
            loc.click()
        elif k == "select":
            loc.select_option(index=(loc.evaluate("e => e.selectedIndex") + 1) % c["options"])
        else:
            loc.fill(pick(loc.input_value(), c, 0.25))
            loc.press("Tab")

    def test_control(self, c):
        cid = c["key"] if c["label"] in (c["key"], "") else f"{c['key']} ({c['label']})"
        lo, hi, _ = rng(c)
        self.reload()
        loc = self.page.locator(c["sel"]).nth(c["nth"])
        if (c["kind"] == "select" and c["options"] < 2) or (c["kind"] == "range" and hi <= lo):
            self.find("interaction", WARN, 2, "control has only one possible value", f"min={lo} max={hi} options={c['options']}", cid)
            return
        self.phase, ok = "interaction", 0
        for method, label in (("user", "real user input"), ("js", "JS set + input/change events")):
            before, n0 = self.ev("__bc.snap()"), len(self.events)
            try:
                self.operate(loc, c, method)
            except Exception as e:
                self.find("interaction", FAIL, 2, f"control cannot be operated by {label}", e, cid)
                continue
            changed = self.wait_change(before) != before
            self.after("interaction", n0, cid, True, label)
            ok += changed
            if not changed:
                self.find("interaction", FAIL, 2, f"control does nothing ({label})", "SVG, readouts and visible numbers unchanged", cid)
        self.meaningful += ok == 2
        self.phase = "edges"
        for op, arg, valid, label in edge_plan(c):
            n0 = len(self.events)
            try:
                if op in ("Home", "End"):
                    loc.press(op)
                elif op == "check":
                    loc.set_checked(arg)
                elif op == "option":
                    loc.select_option(index=arg)
                else:
                    loc.fill(arg)
                    loc.press("Tab")
            except Exception as e:
                self.find("edges", WARN, 6, f"could not apply edge value: {label}", e, cid)
                continue
            self.settle(60)
            self.after("edges", n0, cid, valid, f"{'valid' if valid else 'invalid'} input {label}")

    def check_buttons(self):
        self.reload()
        btns = self.ev("__bc.buttons()")
        self.notes["buttons"] = f"{len(btns)} buttons clicked without errors: " + ", ".join(b["text"] for b in btns)
        movable = next((c for c in self.ctl if c["kind"] != "select" or c["options"] > 1), None)
        for b in btns:
            cid = b["text"] or f"button #{b['i']}"
            is_reset = re.search(r"\breset|restore|default", b["text"], re.I)
            self.phase = "buttons"
            self.reload()
            start = self.ev("__bc.snap()")
            if is_reset and movable:   # give Reset something to undo
                try:
                    self.operate(self.page.locator(movable["sel"]).nth(movable["nth"]), movable, "js")
                    self.settle()
                except Exception:
                    pass
            before, n0 = self.ev("__bc.snap()"), len(self.events)
            try:
                self.page.locator(SELECTORS["buttons"]).nth(b["i"]).click()
            except Exception as e:
                self.find("buttons", FAIL, 2, "button cannot be clicked", e, cid)
                continue
            now = self.wait_change(before)
            self.after("buttons", n0, cid, True, f"clicking '{cid}'")
            if is_reset and before != start and now != start:
                self.find("buttons", WARN, 2, "Reset does not restore the starting state", "snapshot differs from fresh page", cid)
            elif not is_reset and now == before:
                self.find("buttons", WARN, 2, "button has no visible effect", "SVG, readouts and visible numbers unchanged", cid)

    def check_offline_and_size(self):
        for u in dict.fromkeys(self.remote):
            self.find("offline", FAIL, 0, "request to a non-local host (page is not self-contained)", u)
        html = self.index.read_text(encoding="utf-8", errors="replace")
        for m in list(dict.fromkeys(m.group(0) for m in STATIC_REMOTE.finditer(html)))[:5]:
            self.find("offline", WARN, 0, "HTML references a remote resource", m[:150])
        self.notes["offline"] = "no requests left localhost (non-local requests are blocked and recorded)"
        size = self.index.stat().st_size
        if size > 1_000_000:
            self.find("size", WARN, 6, f"file is {size / 1e6:.2f} MB (> 1 MB)")
        self.notes["size"] = f"{size / 1024:.0f} KB"

    def result(self):
        checks = []
        for name in CHECKS:
            fs = [f for f in self.findings if f["check"] == name]
            status = max((f["status"] for f in fs), key=ORDER.get, default=PASS)
            msg = "; ".join(dict.fromkeys(f["symptom"] for f in fs)) if fs else self.notes.get(name, "not run")
            checks.append({"name": name, "status": status, "message": msg[:500]})
        overall = ERROR if self.error else max((c["status"] for c in checks), key=ORDER.get)
        return {"page": self.name, "path": rel(self.index), "url": self.url, "overall": overall, "tool_error": self.error,
                "checks": checks, "findings": self.findings, "controls": self.ctl, "sections": getattr(self, "structure", {}),
                "screenshots": self.shots, "non_local_requests": list(dict.fromkeys(self.remote)), "events": self.events,
                "stats": {"controls": len({c["key"] for c in self.ctl}), "inputs": len(self.ctl), "meaningful": self.meaningful,
                          "edge_failures": sum(f["check"] == "edges" and f["status"] == FAIL for f in self.findings),
                          "overflow_px": self.overflow, "warnings": sum(f["status"] == WARN for f in self.findings)}}


# ---------------------------------------------------------------------- CLI
def find_pages(paths):
    pages = []
    for p in map(Path, paths):
        if p.is_file():
            pages.append(p)
        elif p.is_dir():
            pages += sorted(p.rglob("index.html"))
        else:
            raise FileNotFoundError(f"no such file or directory: {p}")
    return list(dict.fromkeys(p.resolve() for p in pages))


def short_names(pages):
    """Shortest trailing folder path that is unique among the pages, e.g. good, attention_out."""
    parts = []
    for p in pages:
        try:
            parts.append(p.parent.relative_to(Path.cwd().resolve()).parts or (p.parent.name,))
        except ValueError:
            parts.append(p.parent.parts[-3:])
    names = []
    for i, ps in enumerate(parts):
        k = 1
        while k < len(ps) and any(ps[-k:] == q[-k:] for j, q in enumerate(parts) if j != i):
            k += 1
        names.append(re.sub(r"[^\w.-]+", "_", "_".join(ps[-k:])) or "page")
    return [n if names.count(n) == 1 else f"{n}-{i + 1}" for i, n in enumerate(names)]


def print_table(results):
    cols = ["page", "load", "controls found", "controls meaningful", "edge failures", "overflow", "warnings", "overall"]
    rows = []
    for r in results:
        s, c = r["stats"], {x["name"]: x["status"] for x in r["checks"]}
        rows.append([r["page"], max(c["offline"], c["load"], key=ORDER.get), f"{s['controls']} ({s['inputs']} inputs)",
                     f"{s['meaningful']}/{s['inputs']}", s["edge_failures"], f"+{s['overflow_px']}px" if s["overflow_px"] > 2 else "no",
                     s["warnings"], r["overall"]])
    w = [max(len(str(x[i])) for x in [cols] + rows) for i in range(len(cols))]
    line = lambda row: "| " + " | ".join(str(x).ljust(w[i]) for i, x in enumerate(row)) + " |"
    print(line(cols))
    print("|" + "|".join("-" * (n + 2) for n in w) + "|")
    for row in rows:
        print(line(row))
    for r in results:
        print(f"\n{r['page']}  ({r['path']}, {r.get('seconds', '?')}s)  -> {r['overall']}" + (f"  TOOL ERROR: {r['tool_error']}" if r["tool_error"] else ""))
        for c in r["checks"]:
            print(f"  {c['status']:<4}  {c['name']:<11}  {c['message']}")


def print_issues(results):
    """Ready-to-paste ISSUES.md lines, crash > wrong/NaN values > dead controls > ... ; repeats are folded."""
    groups = {}
    for r in results:
        for f in r["findings"]:
            g = groups.setdefault((r["page"], f["check"], f["symptom"], f["evidence"]), {"f": f, "page": r["page"], "n": 0, "ctl": []})
            g["n"] += 1
            if f["control"] and f["control"] not in g["ctl"]:
                g["ctl"].append(f["control"])
            g["f"] = g["f"] if g["f"]["shot"] or not f["shot"] else f
    print("\n## Issues (ranked: " + " > ".join(RANKS) + ")")
    for g in sorted(groups.values(), key=lambda g: (g["f"]["rank"], -ORDER[g["f"]["status"]], g["page"])):
        f, times = g["f"], f" (x{g['n']})" if g["n"] > 1 else ""
        meta = [f"{f['check']} {f['status']}"] + ([f"control {', '.join(g['ctl'][:3])}" + (" …" if len(g["ctl"]) > 3 else "")] if g["ctl"] else []) + ([f["shot"]] if f["shot"] else [])
        print(f"- [{g['page']}] {f['symptom']}{times} — {f['evidence'] or RANKS[f['rank']]} ({', '.join(meta)})")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Operate generated explainer pages in Chromium like the assessor.")
    ap.add_argument("paths", nargs="+", metavar="PATH", help="index.html file or directory to search recursively")
    ap.add_argument("--report-dir", default="dev/reports")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--issues", action="store_true", help="print ranked ISSUES.md lines")
    a = ap.parse_args(argv)
    try:
        pages = find_pages(a.paths)
        from playwright.sync_api import sync_playwright
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ImportError:
        print("error: playwright missing. Run: pip install -r dev/requirements-dev.txt && python -m playwright install chromium", file=sys.stderr)
        return 2
    if not pages:
        print("error: no index.html found", file=sys.stderr)
        return 2
    results = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not a.headed)
            for page, name in zip(pages, short_names(pages)):
                out = Path(a.report_dir) / name
                out.mkdir(parents=True, exist_ok=True)
                print(f"checking {rel(page)} ...", file=sys.stderr, flush=True)
                t0 = time.time()
                r = PageCheck(browser, page, out, name).run()
                r["seconds"] = round(time.time() - t0, 1)
                (out / "report.json").write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
                results.append(r)
            browser.close()
    except Exception as e:
        print(f"error: {type(e).__name__}: {first_line(e)}", file=sys.stderr)
        return 2
    print_table(results)
    if a.issues:
        print_issues(results)
    if any(r["overall"] == ERROR for r in results):
        return 2
    return 1 if any(r["overall"] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
