"""Inject checked parts into the fixed page template and produce one self-contained HTML file."""
import base64
import html
import json
import os
import re

from checks import UNGROUNDED_LABEL, inline_html, read_template

ICONS = {   # small inline icons for the hero stat cards
    "controls": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6h10M18 6h2M4 12h3M11 12h9M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>',
    "values": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V4M4 20h16"/><path d="M7 15l4-5 3 3 5-7"/></svg>',
    "checks": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/></svg>',
    "explore": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/></svg>',
}


def logo_uri():
    """The university logo, embedded so the page stays a single offline file."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "aub_logo.png")
    try:
        with open(path, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="   # 1x1 transparent


def two_tone(title):
    """Headline with its opening phrase in the accent colour, like the theme's hero."""
    head, sep, tail = title.partition(":")
    if not sep or not tail.strip():
        words = title.split()
        head, tail = " ".join(words[:2]), " ".join(words[2:])
        sep = ""
    return '<span class="hl">%s%s</span> %s' % (inline_html(head.strip()), sep, inline_html(tail.strip()))


DISCLAIMER = "This is a small illustrative demo; it does not reproduce the paper's experimental results."


def safe_script(js):
    """Stop generated code from terminating its own <script> element."""
    js = re.sub(r"</(script)", r"<\\/\1", js or "", flags=re.I)
    return js.replace("<!--", "<\\!--")


def json_for_script(obj):
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def describe_override(controls, over):
    """'set scaling off, T = 5' for an exploration's second step, in the words of the control labels."""
    by, out = {c["id"]: c for c in controls}, []
    for k, v in over.items():
        name = html.unescape(re.sub(r"<[^>]+>", "", by[k]["label"])) if k in by else k
        if isinstance(v, bool):
            out.append("%s %s" % (name, "on" if v else "off"))
        else:
            out.append("%s = %s" % (name, json.dumps(v) if isinstance(v, list) else v))
    return ("set " + ", ".join(out)) if out else ""


def _p(text, cls=""):
    return '<p%s>%s</p>' % (' class="%s"' % cls if cls else "", inline_html(text)) if text else ""


def build_html(parts, case):
    c = parts["content"]
    esc = html.escape
    # a label the checker could not trace to the excerpt is not shown as if it were a section or equation number
    c = dict(c, section="" if c["section"] == UNGROUNDED_LABEL else c["section"],
             equation="" if c["equation"] == UNGROUNDED_LABEL else c["equation"])
    if c["equation"].strip().lower() == c["section"].strip().lower():
        c = dict(c, equation="")
    formula = re.sub(r"(?<!&gt)(?<!&lt)(?<!&amp)(?<!&#\d\d)(?<!&#\d\d\d)(?<!&#\d\d\d\d);\s+|\s*\n\s*", "<br>", inline_html(c["formula"]).strip())
    formula = re.sub(r"(\(\d{1,2}\))\s+(?=\S)", r"\1<br>", formula)   # one numbered equation per line
    formula = re.sub(r"<br>\s*[|,;]\s*", "<br>", formula)             # drop a separator left at the start of a line
    formula = re.sub(r"\.\s+(?=[^.<]{1,40}=)", "<br>", formula)         # "A = b. C = d" -> two lines
    eqs = [e.strip() for e in formula.split("<br>") if e.strip()]
    longest = max([len(re.sub(r"<[^>]+>", "", e)) for e in eqs] or [0])
    formula = "".join('<span class="eq">%s</span>' % e for e in eqs)
    fcls = "formula long" if longest > 34 else "formula"
    cite = " · ".join(esc(x) for x in (c["paper"], c["section"], c["equation"]) if x)
    title = c["title"] or c["paper"] or "Interactive explainer"

    chips = "".join('<span class="chip">%s</span>' % esc(x) for x in (c["paper"], c["section"], c["equation"]) if x)
    header = '<div class="chips"><span class="chip solid">From the paper</span>%s</div>\n<h1>%s</h1>' % (chips, two_tone(title))
    if case.get("audience"):
        header += '\n<p class="sub">Written for: %s</p>' % esc(case["audience"])
    header += ('\n<div class="actions"><a class="btn" href="#playground">Open the playground</a>'
               '<a class="btn ghost" href="#grounding">See the source</a></div>')

    n_checks = len(parts["tests"])
    hero_card = '<div class="hero-card"><span class="tag paper">From the paper%s</span>%s<p class="muted small" style="margin:10px 0 0;text-align:center">%s</p>%s</div>' % (
        " · " + esc(c["equation"] or c["section"]) if (c["equation"] or c["section"]) else "",
        '<div class="%s">%s</div>' % (fcls, formula) if c["formula"] else "", cite or "Source paper",
        '<div class="badge"><b>%d</b> live checks<br>on the calculation</div>' % n_checks if n_checks else "")
    stats = '<div class="stats">%s</div>' % "".join(
        '<div class="stat"><span class="ico">%s</span><div><b>%d</b><span>%s</span></div></div>' % (ICONS[k], n, label)
        for k, n, label in (("controls", len(parts["controls"]), "interactive controls"), ("values", len(parts["readouts"]), "live computed values"),
                            ("checks", n_checks, "live checks"), ("explore", len(c["explorations"]), "guided explorations")))

    idea = '<div class="idea-grid"><div>%s</div>%s</div>' % (
        _p(c["intro"], "lead"), '<p class="why"><b>Why it matters</b>%s</p>' % inline_html(c["why"]) if c["why"] else "")

    symbols = "<table><thead><tr><th>Symbol</th><th>Meaning</th></tr></thead><tbody>%s</tbody></table>" % "".join(
        '<tr><td class="sym"><span>%s</span></td><td>%s</td></tr>' % (inline_html(s["symbol"]), inline_html(s["meaning"])) for s in c["symbols"])
    steps = ('<ol class="steps">%s</ol>' % "".join("<li>%s</li>" % inline_html(s) for s in c["steps"]) if c["steps"] else
             '<p class="muted">No step-by-step breakdown was generated for this page; the governing equation is under "The idea".</p>')

    cards, ex_data = [], []
    for i, e in enumerate(c["explorations"]):
        then = describe_override(parts["controls"], e.get("then") or {})
        ex_data.append({"title": inline_html(e["title"]) if e.get("title") else "Try this", "observe": inline_html(e["observe"]),
                        "set": e.get("set") or {}, "then": e.get("then") or {}, "thenText": then, "verified": bool(e.get("verified"))})
        btn = '<p style="margin:0"><button type="button" class="try" data-ex="%d" data-state="a">%s &#9654;</button>%s</p>' % (
            i, "1 · Set it up" if then else "Try it in the playground",
            ' <button type="button" class="try ghost" data-ex="%d" data-state="b">2 · Then %s &#9654;</button>' % (i, esc(then)) if then else "")
        cards.append('<div class="card"><h3><small>Exploration %d</small>%s</h3><dl><dt>Change</dt><dd>%s</dd><dt>Observe</dt><dd>%s</dd><dt>Why</dt><dd>%s</dd></dl>%s</div>' % (
            i + 1, inline_html(e["title"]) if e.get("title") else "Try this", inline_html(e["change"]), inline_html(e["observe"]), inline_html(e["why"]), btn))
    explorations = '<div class="cards">%s</div>' % "".join(cards)

    quotes = "".join("<blockquote>“%s”</blockquote>" % inline_html(re.sub(r"</?(?:sub|sup)>", "", q.strip(' "“”'))) for q in c["quotes"])
    src = '<p class="lead"><b>%s</b>%s</p>' % (esc(c["paper"] or "Source paper"), "".join(", " + esc(x) for x in (c["section"], c["equation"]) if x))
    if case.get("source_url"):
        src += '<p class="muted small mono">%s</p>' % esc(case["source_url"])
    grounding = src
    grounding += '<div class="from-paper"><span class="tag paper">From the paper</span>%s%s</div>' % (
        '<div class="%s">%s</div>' % (fcls, formula) if c["formula"] else "",
        quotes or '<p class="muted small">No verbatim quote could be verified against the supplied text.</p>')
    grounding += '<div class="from-ours"><span class="tag ours">Our example / simplification</span><ul class="plain">%s</ul><p style="margin-top:8px"><b>%s</b></p></div>' % (
        "".join("<li>%s</li>" % inline_html(s) for s in c["simplifications"]) or "<li>The numbers in the playground are small made-up inputs.</li>", DISCLAIMER)

    data = {"controls": parts["controls"], "readouts": parts["readouts"], "tests": parts["tests"], "explorations": ex_data,
            "dropped": int(parts.get("dropped_tests") or 0)}
    fills = {
        "TITLE_TEXT": esc(re.sub(r"<[^>]+>", "", title)),
        "STYLE": read_template("style.css"),
        "HEADER": header, "HERO_CARD": hero_card, "STATS": stats, "LOGO": logo_uri(), "IDEA": idea, "SYMBOLS": symbols, "STEPS": steps, "EXPLORATIONS": explorations,
        "LIMITATION": _p(c["limitation"]), "GROUNDING": grounding,
        "DATA": json_for_script(data),
        "HELPERS": read_template("helpers.js"),
        "MODEL_JS": safe_script(parts.get("compute", "")) + "\n" + safe_script(parts.get("render", "")),
    }
    # single pass, so text injected for one placeholder is never rescanned for another
    return re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: fills.get(m.group(1), ""), read_template("page.html"))


def failure_page(case, reason):
    """Last-resort page when no usable candidate exists (the agent still exits nonzero)."""
    esc = html.escape
    rows = "".join("<tr><td>%s</td><td>%s</td></tr>" % (esc(k), esc(str(v)[:600])) for k, v in case.items())
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>Explainer could not be generated</title><style>%s</style></head><body><main><section><h1>Explainer could not be generated</h1>"
            '<div class="err">%s</div><table><tbody>%s</tbody></table></section></main></body></html>') % (read_template("style.css"), esc(reason), rows)
