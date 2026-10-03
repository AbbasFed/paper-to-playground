"""Paper to Playground: turn a paper excerpt + learning brief into one offline interactive explainer.

    python agent.py --input case.json --output out --model MODEL_ID
"""
import argparse
import json
import os
import re
import sys
import traceback

import requests

import assemble
import checks as C
import llm
from trace import Trace

HERE = os.path.dirname(os.path.abspath(__file__))
EXCERPT_KEYS = ["excerpt", "paper_excerpt", "source_excerpt", "text", "section_text", "content", "passage", "section", "body", "paper_text", "abstract"]
NOT_EXCERPT = ("url", "focus", "audience", "brief", "title", "goal", "task", "id", "name")
MAX_EXCERPT_CHARS = 40000     # only very long excerpts are shortened; a normal section is sent whole
GEN_MAX_TOKENS = 9000
REPAIR_MAX_TOKENS = 5000
MAX_REPAIRS = 2
REASONING_CHOICES = ["off", "none", "minimal", "low", "medium", "high", "xhigh", "max", "auto"]


def read_prompt(name):
    with open(os.path.join(HERE, "prompts", name), encoding="utf-8") as f:
        return f.read().strip()


def flatten(case):
    """All fields as strings, keyed by name; one level of nesting becomes 'outer.inner'."""
    out = {}
    for k, v in (case.items() if isinstance(case, dict) else [("input", case)]):
        if isinstance(v, dict):
            for k2, v2 in v.items():
                out["%s.%s" % (k, k2)] = v2 if isinstance(v2, str) else json.dumps(v2, ensure_ascii=False)
        elif v is not None:
            out[str(k)] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return out


def find_excerpt(fields):
    low = {k.lower().split(".")[-1]: k for k in fields}
    for name in EXCERPT_KEYS:
        if name in low and len(fields[low[name]].strip()) >= 40:
            return low[name], "field name"
    rest = [(len(v.strip()), k) for k, v in fields.items() if not any(w in k.lower() for w in NOT_EXCERPT)]
    rest = sorted(r for r in rest if r[0] >= 200)
    if rest:
        return rest[-1][1], "longest text field"
    return None, "not found"


def fetch_source(url):
    """Only used when the case carries no excerpt at all: one short attempt, failure is fine."""
    r = requests.get(url, timeout=3, headers={"User-Agent": "paper-to-playground"})
    r.raise_for_status()
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", r.text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def trim(text, hint=""):
    """Keep the excerpt to a sane size. An over-long text (e.g. a whole fetched paper) is cut to the
    window that mentions the brief's own terms most often, so the requested section survives."""
    if len(text) <= MAX_EXCERPT_CHARS:
        return text
    words = {w for w in re.findall(r"[a-z][a-z-]{4,}", hint.lower())}
    low, best, best_score = text.lower(), 0, -1
    for start in range(0, len(text) - MAX_EXCERPT_CHARS + 1, 1000):
        chunk = low[start:start + MAX_EXCERPT_CHARS]
        score = sum(min(chunk.count(w), 5) for w in words)
        if score > best_score:
            best, best_score = start, score
    return ("[...] " if best else "") + text[best:best + MAX_EXCERPT_CHARS] + " [...]"


def user_message(fields, ex_key, excerpt):
    lines = ["LEARNING BRIEF (fields of case.json)"]
    lines += ["%s: %s" % (k, v.strip()) for k, v in fields.items() if k != ex_key]
    if excerpt:
        lines += ["", "EXCERPT (the only text you may quote):", "<<<", excerpt.strip(), ">>>"]
    else:
        lines += ["", 'No excerpt was supplied. Work from the brief and well-established facts; return "quotes": [].']
    return "\n".join(lines)


def log_checks(trace, stage, results, round_no):
    for c in results:
        trace.event(stage, "check:" + c["name"], "pass" if c["ok"] else ("fail" if c["severity"] == "hard" else "warn"),
                    message=c["message"][:600], round=round_no)
    hard = C.hard_failures(results)
    trace.event(stage, "check_summary", "pass" if not hard else "fail", round=round_no, checks_run=len(results),
                passed=sum(1 for c in results if c["ok"]), hard_failures=len(hard), warnings=sum(1 for c in results if not c["ok"]) - len(hard))


def repair_message(fields, ex_key, excerpt, tags, results):
    failed = [c for c in results if not c["ok"]]
    hard = [c for c in failed if c["severity"] == "hard"]
    involved = []
    for c in hard:
        involved += [t for t in c["tags"] if t not in involved]
    context = list(involved)
    if any(t in involved for t in ("compute", "render", "tests", "readouts")):
        context += [t for t in ("controls", "compute") if t not in context]
    brief = "\n".join("%s: %s" % (k, v.strip()[:700]) for k, v in fields.items() if k != ex_key)
    if excerpt and any(c["name"] == "quotes_grounded" for c in hard):
        brief += "\n\nEXCERPT (the only text you may quote):\n<<<\n%s\n>>>" % excerpt.strip()
    body = "\n".join("<%s>\n%s\n</%s>" % (t, tags[t], t) for t in C.TAGS if t in context and tags.get(t)) or "(none of the required tags were found)"
    msg = (read_prompt("repair.txt").replace("{{BRIEF}}", brief)
           .replace("{{FAILURES}}", "\n".join("- [%s] %s" % (c["name"], c["message"][:500]) for c in hard + [c for c in failed if c["severity"] != "hard"]))
           .replace("{{TAGS}}", body))
    return msg, involved


def score(results):
    return (1 if C.usable(results) else 0, -len(C.hard_failures(results)))


def run(args, trace, key, budget):
    out_html = os.path.join(args.output, "index.html")

    # ---- prepare
    with open(args.input, encoding="utf-8-sig") as f:
        case = json.load(f)
    fields = flatten(case)
    ex_key, how = find_excerpt(fields)
    hint = " ".join(v for k, v in fields.items() if k != ex_key and not v.startswith("http"))
    excerpt = trim(fields[ex_key], hint) if ex_key else ""
    trace.event("prepare", "parse_case", "ok", fields={k: len(v) for k, v in fields.items()})
    url = next((v for k, v in fields.items() if "url" in k.lower() and v.startswith("http")), "")
    if not excerpt and url:
        try:
            excerpt = trim(fetch_source(url), hint)
            trace.event("prepare", "fetch_source_url", "ok" if excerpt else "empty", chars=len(excerpt))
        except Exception as e:  # noqa: BLE001 - offline assessment: just continue without it
            trace.event("prepare", "fetch_source_url", "failed", error=type(e).__name__)
    trace.event("prepare", "find_excerpt", "ok" if excerpt else "missing", field=ex_key, method=how, chars=len(excerpt),
                trimmed=bool(ex_key and len(fields[ex_key]) > MAX_EXCERPT_CHARS))
    meta = {"source_url": url, "audience": next((v for k, v in fields.items() if "audience" in k.lower()), "")}

    if not key:
        trace.event("generate", "llm_call", "failed", error="OPENROUTER_API_KEY is not set in the environment")
        write(out_html, assemble.failure_page(fields, "No API key available, so nothing could be generated."))
        return 2, []

    system = read_prompt("system.txt")
    user = user_message(fields, ex_key, excerpt)
    info = llm.model_info(args.model, budget, trace)
    reasoning, why = llm.choose_reasoning(args.reasoning, info)
    trace.event("prepare", "reasoning_setting", "ok", requested=args.reasoning, sent=reasoning, reason=why)
    opts = {"temperature": args.temperature, "reasoning_info": info, "provider": args.provider}

    # ---- generate (a reply with no usable tags is regenerated once)
    tags, results, parts, report = {}, [], None, None
    for attempt in range(2):
        resp = llm.chat(args.model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                        GEN_MAX_TOKENS, budget, trace, "generate", reasoning=reasoning, **opts)
        if resp is None:
            break
        reasoning = resp["reasoning"]   # keep a lowered effort for later calls
        tags = C.parse_tags(resp["text"])
        trace.event("generate", "parse_tags", "ok" if len(tags) >= 4 else "failed", tags_found=sorted(tags), finish_reason=resp["finish_reason"])
        if args.debug:
            write(os.path.join(args.output, "debug_generate_%d.txt" % (attempt + 1)), resp["text"])
        if tags.get("plan"):
            trace.event("plan", "explanation_plan", "ok", plan=re.sub(r"\s+", " ", tags["plan"])[:700])
        if len(tags) >= 4:
            break
        trace.event("generate", "revision", "regenerate", reason="reply did not follow the tag contract")
    if len(tags) < 4:
        trace.event("generate", "candidate", "failed", error="no usable model output")
        write(out_html, assemble.failure_page(fields, "The model did not return a usable explainer."))
        return 1, []

    # ---- check, then repair only what failed
    parts, results, report = C.evaluate(tags, excerpt)
    log_checks(trace, "check", results, 0)
    best = (score(results), tags, parts, results, report)
    for rnd in range(1, MAX_REPAIRS + 1):
        hard = C.hard_failures(best[3])
        if not hard:
            break
        if rnd > 1 and all(c["name"].startswith("test: ") for c in hard):
            trace.event("repair", "skip", "not_needed", round=rnd, reason="only known-case tests still fail; they are removed from the page instead")
            break
        why = budget.allow(min_tokens=1500, min_time=30.0)
        if why:
            trace.event("repair", "skip", "budget", reason=why, round=rnd)
            break
        msg, involved = repair_message(fields, ex_key, excerpt, best[1], best[3])
        trace.event("repair", "revision", "requested", round=rnd, tags=involved,
                    reasons=[c["name"] + ": " + c["message"][:200] for c in C.hard_failures(best[3])])
        resp = llm.chat(args.model, [{"role": "system", "content": system}, {"role": "user", "content": msg}],
                        REPAIR_MAX_TOKENS, budget, trace, "repair", reasoning=reasoning, max_attempts=2, **opts)
        if resp is None:
            break
        reasoning = resp["reasoning"]
        if args.debug:
            write(os.path.join(args.output, "debug_repair_%d.txt" % rnd), resp["text"])
        new = {t: v for t, v in C.parse_tags(resp["text"]).items() if t != "plan"}
        if not new:
            trace.event("repair", "merge", "failed", round=rnd, error="repair reply contained no tags")
            continue
        merged = dict(best[1])
        merged.update(new)
        p2, r2, rep2 = C.evaluate(merged, excerpt)
        log_checks(trace, "check", r2, rnd)
        better = score(r2) >= best[0]
        trace.event("repair", "merge", "adopted" if better else "rejected", round=rnd, tags_replaced=sorted(new),
                    hard_failures_before=len(C.hard_failures(best[3])), hard_failures_after=len(C.hard_failures(r2)))
        if better:
            best = (score(r2), merged, p2, r2, rep2)

    # ---- assemble the best candidate
    _, tags, parts, results, report = best
    relabelled = C.ground_labels(parts["content"], [fields[ex_key] if ex_key else excerpt] + [v for k, v in fields.items() if k != ex_key])
    for note in relabelled:
        trace.event("assemble", "label_grounding", "replaced", **note)
    if not relabelled:
        trace.event("assemble", "label_grounding", "ok", section=parts["content"]["section"], equation=parts["content"]["equation"])
    for note in C.finalise(parts, results, report):
        trace.event("assemble", "degrade", "applied", message=note)
    html = assemble.build_html(parts, meta)
    final = C.page_checks(html, key)
    log_checks(trace, "assemble", final, "final")
    if any(c["name"] == "page_no_secret" and not c["ok"] for c in final):
        html = html.replace(key, "")
    write(out_html, html)
    ok = C.usable(results)
    trace.event("assemble", "write_page", "ok" if ok else "degraded", path="index.html", bytes=len(html.encode("utf-8")),
                unresolved=[c["name"] for c in C.hard_failures(results)])
    return (0 if ok else 1), results


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser(description="Generate an offline interactive explainer from a paper excerpt.")
    ap.add_argument("--input", required=True, help="case.json")
    ap.add_argument("--output", required=True, help="output directory")
    ap.add_argument("--model", required=True, help="OpenRouter model id")
    ap.add_argument("--reasoning", choices=REASONING_CHOICES, default=os.environ.get("P2P_REASONING", "off"),
                    help="reasoning effort (off = none = disabled, auto = model default); mapped to what the model supports")
    ap.add_argument("--provider-prefs", default=os.environ.get("P2P_PROVIDER_PREFS", ""),
                    help='OpenRouter provider preferences as a JSON object, e.g. \'{"sort": "throughput"}\'; off unless given')
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--debug", action="store_true", help="also save raw model replies in the output directory")
    args = ap.parse_args()

    os.makedirs(args.output, exist_ok=True)
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    trace = Trace(os.path.join(args.output, "trace.jsonl"), key)
    args.provider = None
    if args.provider_prefs.strip():
        try:
            args.provider = json.loads(args.provider_prefs)
        except ValueError:
            pass
        if not isinstance(args.provider, dict):
            trace.event("start", "provider_prefs", "ignored", error="--provider-prefs must be a JSON object")
            args.provider = None
    trace.event("start", "run", "ok", input=os.path.basename(args.input), model=args.model, reasoning=args.reasoning,
                provider_prefs=args.provider or "off",
                limits={"requests": llm.MAX_REQUESTS, "completion_tokens": llm.MAX_COMPLETION_TOKENS, "deadline_s": llm.DEADLINE_S})
    code, budget, results = 1, llm.Budget(), []
    try:
        code, results = run(args, trace, key, budget)
    except Exception as e:  # noqa: BLE001 - never die without a trace
        tb = traceback.extract_tb(e.__traceback__)[-1]
        trace.event("error", "exception", "failed", error="%s: %s" % (type(e).__name__, str(e)[:300]), where="%s:%d" % (os.path.basename(tb.filename), tb.lineno))
        code = 1
    summary = budget.summary()
    trace.event("done", "exit", "success" if code == 0 else "failure", exit_code=code, checks_run=len(results),
                checks_passed=sum(1 for c in results if c["ok"]), **summary)
    trace.close()
    print("%s exit=%d %s" % ("OK" if code == 0 else "FAILED", code, json.dumps(summary)))
    return code


if __name__ == "__main__":
    sys.exit(main())
