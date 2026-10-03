"""Inject checked parts into the fixed page template and produce one self-contained HTML file."""
import html
import json
import re

from checks import inline_html, read_template

DISCLAIMER = "This is a small illustrative demo; it does not reproduce the paper's experimental results."


def safe_script(js):
    """Stop generated code from terminating its own <script> element."""
    js = re.sub(r"</(script)", r"<\\/\1", js or "", flags=re.I)
    return js.replace("<!--", "<\\!--")


def json_for_script(obj):
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _p(text, cls=""):
    return '<p%s>%s</p>' % (' class="%s"' % cls if cls else "", inline_html(text)) if text else ""


def build_html(parts, case):
    c = parts["content"]
    esc = html.escape
    if c["equation"].strip().lower() == c["section"].strip().lower():
        c = dict(c, equation="")
    formula = re.sub(r";\s+", ";<br>", inline_html(c["formula"]))
    cite = " · ".join(esc(x) for x in (c["paper"], c["section"], c["equation"]) if x)
    title = c["title"] or c["paper"] or "Interactive explainer"

    chips = "".join('<span class="chip">%s</span>' % esc(x) for x in (c["paper"], c["section"], c["equation"]) if x)
    header = '<div class="chips"><span class="chip solid">From the paper</span>%s</div>\n<h1>%s</h1>' % (chips, inline_html(title))
    if case.get("audience"):
        header += '\n<p>Written for: %s</p>' % esc(case["audience"])

    idea = _p(c["intro"], "lead")
    if c["why"]:
        idea += '<p class="why"><b>Why it matters.</b> %s</p>' % inline_html(c["why"])
    if c["formula"]:
        idea += ('<div class="from-paper"><span class="tag paper">From the paper%s</span><div class="formula">%s</div></div>'
                 % (" · " + esc(c["equation"] or c["section"]) if (c["equation"] or c["section"]) else "", formula))

    symbols = "<table><thead><tr><th>Symbol</th><th>Meaning</th></tr></thead><tbody>%s</tbody></table>" % "".join(
        '<tr><td class="sym"><span>%s</span></td><td>%s</td></tr>' % (inline_html(s["symbol"]), inline_html(s["meaning"])) for s in c["symbols"])
    steps = '<ol class="steps">%s</ol>' % "".join("<li>%s</li>" % inline_html(s) for s in c["steps"])

    cards = []
    for i, e in enumerate(c["explorations"]):
        btn = ('<p style="margin:0"><button type="button" class="try" data-set="%s">Try it in the playground &#9654;</button></p>'
               % esc(json.dumps(e["set"]), quote=True)) if e.get("set") else ""
        cards.append('<div class="card"><h3><small>Exploration %d</small>%s</h3><dl><dt>Change</dt><dd>%s</dd><dt>Observe</dt><dd>%s</dd><dt>Why</dt><dd>%s</dd></dl>%s</div>' % (
            i + 1, inline_html(e["title"]) if e.get("title") else "Try this", inline_html(e["change"]), inline_html(e["observe"]), inline_html(e["why"]), btn))
    explorations = '<div class="cards">%s</div>' % "".join(cards)

    quotes = "".join("<blockquote>“%s”</blockquote>" % esc(q.strip(' "“”')) for q in c["quotes"])
    src = '<p class="lead"><b>%s</b>%s</p>' % (esc(c["paper"] or "Source paper"), "".join(", " + esc(x) for x in (c["section"], c["equation"]) if x))
    if case.get("source_url"):
        src += '<p class="muted small mono">%s</p>' % esc(case["source_url"])
    grounding = src
    grounding += '<div class="from-paper"><span class="tag paper">From the paper</span>%s%s</div>' % (
        '<div class="formula">%s</div>' % formula if c["formula"] else "",
        quotes or '<p class="muted small">No verbatim quote could be verified against the supplied text.</p>')
    grounding += '<div class="from-ours"><span class="tag ours">Our example / simplification</span><ul class="plain">%s</ul><p style="margin-top:8px"><b>%s</b></p></div>' % (
        "".join("<li>%s</li>" % inline_html(s) for s in c["simplifications"]) or "<li>The numbers in the playground are small made-up inputs.</li>", DISCLAIMER)

    data = {"controls": parts["controls"], "readouts": parts["readouts"], "tests": parts["tests"]}
    fills = {
        "TITLE_TEXT": esc(re.sub(r"<[^>]+>", "", title)),
        "STYLE": read_template("style.css"),
        "HEADER": header, "IDEA": idea, "SYMBOLS": symbols, "STEPS": steps, "EXPLORATIONS": explorations,
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
