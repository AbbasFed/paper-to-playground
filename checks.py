"""Deterministic checks on a generated explainer: tag parsing, spec normalisation,
content/grounding checks, and executing the generated JS in an embedded V8 engine."""
import difflib
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
TAGS = ["plan", "content", "controls", "compute", "render", "readouts", "tests"]
REQUIRED = ["content", "controls", "compute", "render", "readouts", "tests"]
JSON_TAGS = {"content": dict, "controls": list, "readouts": list, "tests": list}
ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ALLOWED_INLINE = "b|i|sub|sup|code|em|strong|br"
FORBIDDEN_JS = [
    (r"\bfetch\s*\(", "fetch()"), (r"\bimport\s*\(", "import()"), (r"XMLHttpRequest", "XMLHttpRequest"),
    (r"\bWebSocket\b", "WebSocket"), (r"\bdocument\s*\.", "document (DOM access)"), (r"\bwindow\s*\.", "window"),
    (r"\blocalStorage\b", "localStorage"), (r"Math\.random\s*\(", "Math.random() (use H.rng(seed))"),
    (r"\bDate\b", "Date (non-deterministic)"), (r"https?://", "an http(s) URL"),
]
LATEX_RE = re.compile(r"\\(?:frac|sqrt|sum|text|mathbf|mathrm|cdot|left|right|begin|alpha|beta|times)\b|\$[^$\n]{1,80}\$|\\\(|\\\[")


def check(name, ok, message="", tags=(), severity="hard"):
    return {"name": name, "ok": bool(ok), "severity": severity, "message": message, "tags": list(tags)}


def read_template(name):
    with open(os.path.join(HERE, "templates", name), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- parsing

def strip_fences(s):
    s = s.strip()
    s = re.sub(r"^```[A-Za-z0-9_-]*[ \t]*\r?\n?", "", s)
    s = re.sub(r"\r?\n?```\s*$", "", s)
    return s.strip()


def parse_tags(text):
    """Tolerant extraction of <tag>...</tag>. Models drift: they close <compute> with </content>, or wrap
    parts in tool-call markup such as <parameter name="compute">...</parameter>. A body therefore starts at
    either opening form and ends at the first closing tag of any known name, any tool-call style closer,
    or the next opening tag."""
    found = {}
    text = text or ""
    names = "|".join(TAGS)
    opener = r"<(?:%s)\s*>|<[^<>/]{0,40}\bname\s*=\s*[\"'](?:%s)[\"'][^<>]*>"
    stop = re.compile(r"</(?:%s)\s*>|</[^<>]{0,30}(?:parameter|invoke|function|DSML)[^<>]{0,30}>|" % names + opener % (names, names), re.I)
    for tag in TAGS:
        m = re.search(opener % (tag, tag), text, re.I)
        if not m:
            continue
        rest = text[m.end():]
        end = stop.search(rest)
        body = strip_fences(rest[:end.start()] if end else rest)
        if tag in ("compute", "render") and "}" in body:
            body = body[:body.rindex("}") + 1]      # drop anything trailing the function
        if body:
            found[tag] = body
    return found


def loads_tolerant(s):
    s = strip_fences(s)
    try:
        return json.loads(s)
    except ValueError:
        pass
    a = min([i for i in (s.find("{"), s.find("[")) if i >= 0] or [0])
    try:                                            # valid JSON followed by stray text
        return json.JSONDecoder().raw_decode(s[a:])[0]
    except ValueError:
        pass
    b = max(s.rfind("}"), s.rfind("]"))             # outermost bracket pair, without trailing commas
    t = s[a:b + 1] if b > a else s
    t = re.sub(r",(\s*[}\]])", r"\1", t)
    return json.loads(t)


def inline_html(s):
    """Escape text but keep a small whitelist of inline tags and existing entities."""
    s = "" if s is None else str(s)
    s = re.sub(r"&(?![A-Za-z]{2,8};|#\d{1,6};|#x[0-9A-Fa-f]{1,6};)", "&amp;", s)
    s = s.replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"&lt;(/?)(%s)\s*/?&gt;" % ALLOWED_INLINE, lambda m: "<%s%s>" % (m.group(1), m.group(2).lower()), s, flags=re.I)
    # models often write d_k, m_{t-1}, 10^-8 despite instructions: typeset them properly
    s = re.sub(r"_\{([^{}<>]{1,24})\}", r"<sub>\1</sub>", s)
    s = re.sub(r"\^\{([^{}<>]{1,24})\}", r"<sup>\1</sup>", s)
    s = re.sub(r"(?<=[^\s_<>&;/])_([A-Za-z0-9]{1,3})(?![A-Za-z0-9_])", r"<sub>\1</sub>", s)
    s = re.sub(r"(?<=[^\s^<>&;/])\^([\u2212-]?[A-Za-z0-9]{1,3})(?![A-Za-z0-9])", r"<sup>\1</sup>", s)
    return s


def _num(v, default=None):
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return v if v == v and abs(v) != float("inf") else default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _tidy(x):
    return int(x) if isinstance(x, float) and x.is_integer() and abs(x) < 1e15 else x


def normalise_controls(spec):
    """Coerce the model's control spec into the exact shape the template expects."""
    out, notes, errors, seen = [], [], [], set()
    alias = {"range": "slider", "checkbox": "toggle", "boolean": "toggle", "bool": "toggle", "dropdown": "select",
             "choice": "select", "array": "vector", "list": "vector", "int": "number", "float": "number"}
    for i, c in enumerate(spec if isinstance(spec, list) else []):
        if not isinstance(c, dict):
            errors.append("control #%d is not an object" % (i + 1))
            continue
        typ = alias.get(str(c.get("type", "")).lower(), str(c.get("type", "")).lower())
        cid = str(c.get("id", ""))
        if not ID_RE.match(cid):
            errors.append("control #%d has an invalid id %r (must be a JS identifier)" % (i + 1, cid))
            continue
        if cid in seen:
            errors.append("duplicate control id %r" % cid)
            continue
        n = {"id": cid, "type": typ, "label": inline_html(c.get("label") or cid)}
        if typ in ("slider", "number", "vector", "matrix"):
            d = c.get("default")
            flat = []
            if typ == "vector":
                d = d if isinstance(d, list) else []
                length = int(_num(c.get("length"), len(d)) or len(d))
                d = [_num(v, 0) for v in d][:length] + [0] * max(0, length - len(d))
                if not 1 <= len(d) <= 12:
                    errors.append("vector %r needs a default with 1-12 numbers" % cid)
                    continue
                flat = d
            elif typ == "matrix":
                if not (isinstance(d, list) and d and all(isinstance(r, list) and r for r in d)):
                    errors.append("matrix %r needs a default like [[..],[..]]" % cid)
                    continue
                cols = max(len(r) for r in d)
                d = [[_num(v, 0) for v in r] + [0] * (cols - len(r)) for r in d]
                if len(d) > 8 or cols > 8:
                    errors.append("matrix %r is larger than 8x8" % cid)
                    continue
                flat = [v for r in d for v in r]
            else:
                d = _num(d)
                flat = [d] if d is not None else []
            lo, hi = _num(c.get("min")), _num(c.get("max"))
            if lo is None:
                lo = min(flat + [0]) - (0 if typ in ("slider", "number") else 5)
                notes.append("%s: min missing, set to %s" % (cid, lo))
            if hi is None:
                hi = max(flat + [1]) + (0 if typ in ("slider", "number") else 5)
                notes.append("%s: max missing, set to %s" % (cid, hi))
            if lo > hi:
                lo, hi = hi, lo
            if flat and (min(flat) < lo or max(flat) > hi):   # the model's own default wins over its range
                lo, hi = min(lo, min(flat)), max(hi, max(flat))
                notes.append("%s: range widened to [%s, %s] to contain its default" % (cid, lo, hi))
            if lo == hi:
                errors.append("control %r has min == max" % cid)
                continue
            step = _num(c.get("step"))
            if not step or step <= 0:
                step = (hi - lo) / 100.0
            clamp = lambda v: min(hi, max(lo, v))
            if typ == "vector":
                if any(v < lo or v > hi for v in d):
                    notes.append("%s: default entries clamped into [%s, %s]" % (cid, lo, hi))
                n["default"] = [_tidy(clamp(v)) for v in d]
                n["length"] = len(d)
                if isinstance(c.get("labels"), list):
                    n["labels"] = [str(x) for x in c["labels"]][:len(d)]
                if c.get("lengthFrom"):
                    n["lengthFrom"] = str(c["lengthFrom"])
            elif typ == "matrix":
                n["default"] = [[_tidy(clamp(v)) for v in r] for r in d]
                n["rows"], n["cols"] = len(d), len(d[0])
            else:
                if d is None:
                    d = lo
                    notes.append("%s: default missing, set to min" % cid)
                elif d < lo or d > hi:
                    notes.append("%s: default %s outside [%s, %s], clamped" % (cid, d, lo, hi))
                n["default"] = _tidy(clamp(d))
            n["min"], n["max"], n["step"] = _tidy(lo), _tidy(hi), _tidy(step)
        elif typ == "toggle":
            d = c.get("default")
            n["default"] = d is True or str(d).lower() in ("true", "1", "on", "yes")
        elif typ == "select":
            opts = []
            for o in c.get("options") if isinstance(c.get("options"), list) else []:
                if isinstance(o, dict):
                    v = o.get("value", o.get("id", o.get("label")))
                    opts.append({"value": v, "label": str(o.get("label", v))})
                else:
                    opts.append({"value": o, "label": str(o)})
            opts = [o for o in opts if isinstance(o["value"], (str, int, float)) and not isinstance(o["value"], bool)]
            if len(opts) < 2:
                errors.append("select %r needs at least 2 options" % cid)
                continue
            n["options"] = opts
            vals = [o["value"] for o in opts]
            n["default"] = c.get("default") if c.get("default") in vals else vals[0]
        else:
            errors.append("control %r has unsupported type %r" % (cid, c.get("type")))
            continue
        seen.add(cid)
        out.append(n)
    sliders = {c["id"] for c in out if c["type"] in ("slider", "number")}
    for c in out:
        if c.get("lengthFrom") and c["lengthFrom"] not in sliders:
            notes.append("%s: lengthFrom %r is not a slider id, ignored" % (c["id"], c["lengthFrom"]))
            del c["lengthFrom"]
    return out, notes, errors


def override_problems(controls, over):
    """Why a {controlId: value} override would not be applied as written (sizes are fixed)."""
    by, out = {c["id"]: c for c in controls}, []
    for k, v in (over or {}).items():
        c = by.get(k)
        if c is None:
            out.append("%r is not a control id" % k)
        elif c["type"] == "vector" and not (isinstance(v, list) and (len(v) == c["length"] or (c.get("lengthFrom") and 1 <= len(v) <= c["length"]))):
            out.append("%s must be a list of exactly %d numbers (vector size is fixed%s)" % (k, c["length"], "; %s selects how many are used" % c["lengthFrom"] if c.get("lengthFrom") else ""))
        elif c["type"] == "matrix" and not (isinstance(v, list) and len(v) == c["rows"] and all(isinstance(r, list) and len(r) == c["cols"] for r in v)):
            out.append("%s must be exactly %dx%d like its default (matrix size is fixed)" % (k, c["rows"], c["cols"]))
        elif c["type"] in ("slider", "number") and not (isinstance(v, (int, float)) and not isinstance(v, bool) and c["min"] <= v <= c["max"]):
            out.append("%s=%r is outside the control range [%s, %s]" % (k, v, c["min"], c["max"]))
        elif c["type"] in ("vector", "matrix"):
            flat = v if c["type"] == "vector" else [x for r in v for x in r]
            if any(not isinstance(x, (int, float)) or isinstance(x, bool) or x < c["min"] or x > c["max"] for x in flat):
                out.append("%s has entries outside the control range [%s, %s]" % (k, c["min"], c["max"]))
        elif c["type"] == "select" and v not in [o["value"] for o in c["options"]]:
            out.append("%s=%r is not one of its options" % (k, v))
        elif c["type"] == "toggle" and not isinstance(v, bool):
            out.append("%s must be true or false" % k)
    return out


def fit_controls(controls, overrides):
    """Make the control spec consistent with how the model itself uses it: a vector tied to a size
    slider must be as long as the slider's max, and ranges must admit the values in set/params."""
    notes, by = [], {c["id"]: c for c in controls}
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    for over in overrides:
        for k, v in (over or {}).items():
            c = by.get(k)
            if c is None or c["type"] not in ("slider", "number", "vector", "matrix"):
                continue
            flat = [v] if num(v) else [x for r in v for x in (r if isinstance(r, list) else [r])] if isinstance(v, list) else []
            flat = [x for x in flat if num(x)]
            if flat and (min(flat) < c["min"] or max(flat) > c["max"]):
                lo, hi = min(c["min"], *flat), max(c["max"], *flat)
                if (hi - lo) <= 20 * (c["max"] - c["min"]):
                    notes.append("%s: range widened from [%s, %s] to [%s, %s] to admit a value used in an exploration/test" % (k, c["min"], c["max"], lo, hi))
                    c["min"], c["max"] = lo, hi
    for c in controls:
        s = by.get(c.get("lengthFrom", ""))
        if s is not None:
            want = int(min(12, s["max"]))
            if s["max"] > 12:
                s["max"] = 12
            if s["min"] < 1:
                s["min"] = 1
            s["default"] = min(max(s["default"], s["min"]), s["max"])
            if c["length"] < want:
                notes.append("%s: default padded from %d to %d entries to match slider %s" % (c["id"], c["length"], want, s["id"]))
                c["default"] = c["default"] + [c["default"][-1]] * (want - c["length"])
                c["length"] = want
    return notes


def _strs(v):
    if isinstance(v, str):
        v = [v]
    return [str(x).strip() for x in (v if isinstance(v, list) else []) if isinstance(x, (str, int, float)) and str(x).strip()]


def normalise_content(c):
    g = lambda k, *alts: next((str(c[x]).strip() for x in (k,) + alts if isinstance(c.get(x), (str, int, float)) and str(c[x]).strip()), "")
    syms = []
    raw = c.get("symbols")
    if isinstance(raw, dict):
        raw = [{"symbol": k, "meaning": v} for k, v in raw.items()]
    for s in raw if isinstance(raw, list) else []:
        if isinstance(s, dict) and s.get("symbol") and s.get("meaning"):
            syms.append({"symbol": str(s["symbol"]), "meaning": str(s["meaning"])})
        elif isinstance(s, list) and len(s) == 2:
            syms.append({"symbol": str(s[0]), "meaning": str(s[1])})
    exps = []
    for e in c.get("explorations") if isinstance(c.get("explorations"), list) else []:
        if isinstance(e, dict):
            exps.append({"title": str(e.get("title") or "").strip(), "change": str(e.get("change") or "").strip(),
                         "observe": str(e.get("observe") or "").strip(), "why": str(e.get("why") or "").strip(),
                         "set": e.get("set") if isinstance(e.get("set"), dict) else {}})
    return {"title": g("title"), "paper": g("paper", "paper_title"), "section": g("section"), "equation": g("equation"),
            "formula": g("formula"), "intro": g("intro"), "why": g("why", "why_it_matters"), "symbols": syms,
            "steps": _strs(c.get("steps")), "explorations": exps, "limitation": g("limitation"),
            "quotes": _strs(c.get("quotes")), "simplifications": _strs(c.get("simplifications"))}


# ---------------------------------------------------------------- grounding

def _norm(s):
    s = s.lower()
    for a, b in (("\u2018", "'"), ("\u2019", "'"), ("\u201c", '"'), ("\u201d", '"'), ("\u2013", "-"), ("\u2014", "-"), ("\u2212", "-"), ("\u00a0", " ")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def quote_score(quote, excerpt_norm):
    """1.0 if the quote is in the excerpt, else the best fuzzy ratio against a same-length window."""
    q = _norm(re.sub(r"<[^>]+>", "", quote)).strip(" \"'")
    pieces = [p.strip(" \"'.,;:") for p in re.split("\\.\\.\\.|\u2026|\\[\\.\\.\\.\\]", q) if len(p.strip(" \"'.,;:")) >= 4]
    if not pieces or not excerpt_norm:
        return 0.0
    worst = 1.0
    for p in pieces:
        if p in excerpt_norm:
            continue
        sm = difflib.SequenceMatcher(None, excerpt_norm, p, autojunk=False)
        m = sm.find_longest_match(0, len(excerpt_norm), 0, len(p))
        start = max(0, m.a - m.b)
        best = 0.0
        for shift in (0, -2, 2, -5, 5):
            w = excerpt_norm[max(0, start + shift):max(0, start + shift) + len(p)]
            best = max(best, difflib.SequenceMatcher(None, w, p, autojunk=False).ratio())
        worst = min(worst, best)
    return worst


# ---------------------------------------------------------------- JS engine

def new_engine():
    from py_mini_racer import MiniRacer
    return MiniRacer()


def js_error(e):
    msg = str(e).strip().splitlines()
    keep = [l.strip() for l in msg if l.strip()][:3]
    return " | ".join(keep)[:300]


def js_syntax_ok(src):
    """Syntax-check a script without running it. Returns '' or the error text."""
    try:
        ctx = new_engine()
        ctx.eval("new Function(%s); 0" % json.dumps(src), timeout_sec=5)
        return ""
    except ImportError:
        return ""
    except Exception as e:  # noqa: BLE001 - engine raises its own error types
        return js_error(e)


def run_js_checks(parts):
    """Load helpers + generated code into V8 and run the harness. Returns (checks, report)."""
    out = []
    try:
        ctx = new_engine()
    except Exception as e:  # noqa: BLE001
        return [check("js_engine", False, "embedded JS engine unavailable (%s); JS checks skipped" % type(e).__name__, severity="soft")], None
    ctx.eval(read_template("helpers.js"))
    loaded = True
    for tag in ("compute", "render"):
        try:
            ctx.eval(parts[tag], timeout_sec=5)
            out.append(check("js_load_" + tag, True, "<%s> code loaded" % tag))
        except Exception as e:  # noqa: BLE001
            out.append(check("js_load_" + tag, False, "<%s> has a JavaScript error: %s" % (tag, js_error(e)), [tag]))
            loaded = loaded and tag != "compute"
    if not loaded:
        return out, None
    ctx.eval(read_template("harness.js"))
    spec = {"controls": parts["controls"], "tests": parts["tests"], "readouts": parts["readouts"]}
    try:
        rep = json.loads(ctx.eval("__runChecks(%s)" % json.dumps(spec), timeout_sec=15))
    except Exception as e:  # noqa: BLE001
        out.append(check("compute_defaults", False, "running the code failed or timed out (infinite loop?): %s" % js_error(e), ["compute", "render"]))
        return out, None

    if rep["fatal"]:
        out.append(check("compute_defaults", False, rep["fatal"], ["compute", "controls"]))
        return out, rep
    out.append(check("compute_defaults", not rep["defaults"], rep["defaults"] or "compute(defaults) ran; all outputs finite", ["compute", "controls"]))
    edge = rep["edgeErrors"] + rep["edgeNaN"]
    out.append(check("edge_sweep", not edge, "; ".join(edge) if edge else "compute ran at %d extreme control settings without exceptions or NaN" % rep["settings"], ["compute", "controls"]))
    if rep["edgeInf"]:
        out.append(check("edge_infinity", False, "; ".join(rep["edgeInf"]), ["compute", "controls"], "soft"))
    out.append(check("controls_meaningful", not rep["inert"],
                     ("control(s) %s do not change any output of compute()" % ", ".join(rep["inert"])) if rep["inert"] else "every control changes at least one output",
                     ["compute", "controls"]))
    failed = [t for t in rep["tests"] if not t["pass"]]
    for t in rep["tests"]:
        out.append(check("test: " + t["name"], t["pass"], "pass" if t["pass"] else ("%s %s" % (t["error"], t["got"])).strip(), ["tests", "compute"]))
    if not rep["tests"]:
        out.append(check("tests_present", False, "no usable tests supplied", ["tests"]))
    for t in rep["invariant"]:
        if t["name"] not in {f["name"] for f in failed}:
            out.append(check("invariant: " + t["name"], False, "invariant (params {}) is false at %s. %s out=%s" % (t["at"], t["error"], t["got"]), ["tests", "compute"]))
    out.append(check("render_defaults", not rep["render"], rep["render"] or "render(defaults) returned an SVG visual", ["render"]))
    out.append(check("render_edges", not rep["renderEdge"], "; ".join(rep["renderEdge"]) if rep["renderEdge"] else "render ran at every extreme setting", ["render", "compute"]))
    return out, rep


# ---------------------------------------------------------------- candidate evaluation

def evaluate(tags, excerpt):
    """Parse + check one candidate (dict tag -> raw text). Returns (parts, checks, report)."""
    checks, parts = [], {"plan": tags.get("plan", "")}
    missing = [t for t in REQUIRED if not tags.get(t)]
    checks.append(check("tags_present", not missing, ("missing tag(s): " + ", ".join("<%s>" % t for t in missing)) if missing else "all required tags present", missing))
    parsed = {}
    for tag, typ in JSON_TAGS.items():
        if not tags.get(tag):
            continue
        try:
            v = loads_tolerant(tags[tag])
            if not isinstance(v, typ):
                raise ValueError("expected a JSON %s" % ("object" if typ is dict else "array"))
            parsed[tag] = v
            checks.append(check("json_" + tag, True, "<%s> parsed" % tag))
        except ValueError as e:
            checks.append(check("json_" + tag, False, "<%s> is not valid JSON: %s" % (tag, str(e)[:160]), [tag]))

    # content
    content = normalise_content(parsed.get("content", {}))
    parts["content"] = content
    if "content" in parsed:
        gaps = []
        for k in ("title", "paper", "section", "equation", "formula", "intro", "why", "limitation"):
            if not content[k]:
                gaps.append("%s is empty" % k)
        if len(content["symbols"]) < 2:
            gaps.append("needs at least 2 symbols")
        if len(content["steps"]) < 2:
            gaps.append("needs at least 2 steps")
        if len(content["explorations"]) != 2:
            gaps.append("needs exactly 2 explorations (got %d)" % len(content["explorations"]))
        for i, e in enumerate(content["explorations"]):
            for k in ("change", "observe", "why"):
                if len(e[k]) < 8:
                    gaps.append("exploration %d has no %s" % (i + 1, k))
        if not content["simplifications"]:
            gaps.append("needs at least 1 simplification")
        checks.append(check("content_complete", not gaps, "; ".join(gaps) if gaps else "idea, symbols, steps, 2 explorations, limitation, citation, simplifications present", ["content"]))
        blob = json.dumps(parsed["content"], ensure_ascii=False)
        m = LATEX_RE.search(blob.replace("\\\\", "\\"))
        checks.append(check("content_no_latex", not m, ("content contains LaTeX markup %r; use HTML <sub>/<sup> and Unicode" % m.group(0)) if m else "no LaTeX markup", ["content"]))
        # grounding
        if excerpt:
            en = _norm(excerpt)
            scored = [(q, quote_score(q, en)) for q in content["quotes"]]
            good = [q for q, s in scored if s >= 0.9]
            badq = ["%r (match %.2f)" % (q[:70], s) for q, s in scored if s < 0.9]
            content["quotes"] = good
            if good:
                checks.append(check("quotes_grounded", True, "%d quote(s) found verbatim in the excerpt" % len(good)))
                if badq:
                    checks.append(check("quotes_dropped", False, "dropped quote(s) not found in the excerpt: " + "; ".join(badq), ["content"], "soft"))
            else:
                checks.append(check("quotes_grounded", False, "no quote matches the excerpt verbatim" + (": " + "; ".join(badq) if badq else " (none given)") + ". Copy 1-3 short quotes exactly from the excerpt.", ["content"]))
        else:
            content["quotes"] = []
            checks.append(check("quotes_grounded", True, "no excerpt available; quotes omitted because they cannot be verified", severity="soft"))

    # controls
    controls, notes, errors = normalise_controls(parsed.get("controls", []))
    parts["controls"] = controls
    if "controls" in parsed:
        if len(controls) < 2:
            errors.append("at least 2 valid controls are required (got %d)" % len(controls))
        checks.append(check("controls_valid", not errors, "; ".join(errors) if errors else "%d valid controls, unique ids, defaults within range" % len(controls), ["controls"]))
        if notes:
            checks.append(check("controls_autofixed", False, "; ".join(notes), ["controls"], "soft"))
    raw_tests = [t for t in parsed.get("tests", []) if isinstance(t, dict)]
    fit = fit_controls(controls, [e["set"] for e in content["explorations"]] + [t.get("params") for t in raw_tests if isinstance(t.get("params"), dict)])
    if fit:
        checks.append(check("controls_fitted", False, "; ".join(fit), ["controls"], "soft"))
    if "content" in parsed and "controls" in parsed and not errors:
        probs = []
        for i, e in enumerate(content["explorations"]):
            probs += ["exploration %d set: %s" % (i + 1, m) for m in override_problems(controls, e["set"])]
            if not e["set"]:
                probs.append("exploration %d has no \"set\" values for its button" % (i + 1))
        checks.append(check("explorations_apply", not probs, "; ".join(probs) if probs else "both explorations can be applied to the controls", ["content", "controls"]))
    ids = {c["id"] for c in controls}
    for e in content["explorations"]:
        e["set"] = {k: v for k, v in e["set"].items() if k in ids}

    # readouts / tests
    readouts = []
    for r in parsed.get("readouts", []):
        if isinstance(r, dict) and r.get("key"):
            d = r.get("digits", r.get("format"))
            if isinstance(d, str):
                m = re.search(r"\d+", d)
                d = int(m.group(0)) if m else None
            readouts.append({"key": str(r["key"]).replace("out.", "", 1) if str(r["key"]).startswith("out.") else str(r["key"]),
                             "label": inline_html(r.get("label") or r["key"]),
                             "digits": int(d) if isinstance(d, (int, float)) and 0 <= d <= 10 else 3,
                             "unit": inline_html(r.get("unit") or "")})
    parts["readouts"] = readouts
    tests = []
    for t in parsed.get("tests", []):
        if isinstance(t, dict) and isinstance(t.get("expect"), str) and t["expect"].strip():
            tests.append({"name": inline_html(t.get("name") or t["expect"]), "params": t.get("params") if isinstance(t.get("params"), dict) else {},
                          "expect": t["expect"].strip().rstrip(";")})
    parts["tests"] = tests
    if tests and "controls" in parsed and not errors:
        probs = ["test %r params: %s" % (t["name"][:50], m) for t in tests for m in override_problems(controls, t["params"])]
        if probs:
            checks.append(check("tests_params_shape", False, "; ".join(probs[:6]), ["tests"]))

    # code
    for tag in ("compute", "render"):
        code = tags.get(tag, "")
        parts[tag] = code
        if not code:
            continue
        if not re.search(r"\b%s\b" % tag, code):
            checks.append(check("code_defines_" + tag, False, "<%s> must define function %s" % (tag, tag), [tag]))
        hits = [label for rx, label in FORBIDDEN_JS if re.search(rx, code)]
        checks.append(check("code_pure_" + tag, not hits, ("<%s> uses forbidden %s" % (tag, ", ".join(hits))) if hits else "<%s> is self-contained and deterministic" % tag, [tag]))

    report = None
    if parts.get("compute") and "controls" in parsed:
        js, report = run_js_checks(parts)
        checks.extend(js)
        if report and not report["fatal"]:
            miss = set(report["readoutsMissing"])
            keep = [r for r in readouts if r["key"] not in miss]
            if miss and len(keep) >= 2:
                parts["readouts"] = keep
                checks.append(check("readouts_resolve", False, "dropped readout key(s) not found in out: " + ", ".join(sorted(miss)), ["readouts"], "soft"))
            elif miss or not readouts:
                checks.append(check("readouts_resolve", False, "readout key(s) %s do not exist in out; available keys: %s" % (", ".join(sorted(miss)) or "(none given)", ", ".join(report["outKeys"])), ["readouts"]))
            else:
                checks.append(check("readouts_resolve", True, "%d readout keys resolve in out" % len(readouts)))
    return parts, checks, report


def hard_failures(checks):
    return [c for c in checks if not c["ok"] and c["severity"] == "hard"]


def usable(checks):
    """A page is worth shipping if compute and render work at defaults with >= 2 controls."""
    ok = {c["name"]: c["ok"] for c in checks}
    return bool(ok.get("compute_defaults") and ok.get("render_defaults") and ok.get("controls_valid"))


def finalise(parts, checks, report):
    """Make the best candidate presentable: drop failing tests, backfill readouts. Returns notes."""
    notes = []
    failed = {c["name"].split(": ", 1)[1] for c in checks if not c["ok"] and c["name"].startswith(("test: ", "invariant: "))}
    if failed:
        parts["tests"] = [t for t in parts["tests"] if t["name"] not in failed]
        notes.append("removed %d failing test(s) from the page: %s" % (len(failed), "; ".join(sorted(failed))))
    if report and not report.get("fatal"):
        miss = set(report["readoutsMissing"])
        parts["readouts"] = [r for r in parts["readouts"] if r["key"] not in miss]
        if not parts["readouts"]:
            parts["readouts"] = [{"key": k, "label": inline_html(k), "digits": 3, "unit": ""} for k in report["outKeys"][:8]]
            notes.append("readouts backfilled from compute() output keys")
    return notes


# ---------------------------------------------------------------- final page

PAGE_RULES = [
    (r"(?:src|href)\s*=\s*[\"']?\s*(?:https?:)?//", "remote src/href"),
    (r"url\(\s*[\"']?\s*(?:https?:)?//", "remote url()"),
    (r"\bfetch\s*\(", "fetch("), (r"\bimport\s*\(", "import("), (r"XMLHttpRequest", "XMLHttpRequest"),
    (r"<link\b", "<link>"), (r"@import", "@import"), (r"<iframe\b", "<iframe>"), (r"<script[^>]+\bsrc\s*=", "<script src>"),
]


def page_checks(html, secret=""):
    out = []
    hits = [label for rx, label in PAGE_RULES if re.search(rx, html, re.I)]
    out.append(check("page_self_contained", not hits, ("page contains " + ", ".join(hits)) if hits else "no remote resources, fetch, import or XHR in the page", ["compute", "render"]))
    bad = []
    for i, m in enumerate(re.finditer(r"<script(?![^>]*application/json)[^>]*>(.*?)</script>", html, re.S | re.I)):
        err = js_syntax_ok(m.group(1))
        if err:
            bad.append("script %d: %s" % (i + 1, err))
    out.append(check("page_js_syntax", not bad, "; ".join(bad) if bad else "all inline scripts parse", ["compute", "render"]))
    size = len(html.encode("utf-8"))
    out.append(check("page_size", size < 1_000_000, "%d bytes" % size))
    if secret:
        out.append(check("page_no_secret", secret not in html, "API key absent from page" if secret not in html else "API key found in page"))
    return out
