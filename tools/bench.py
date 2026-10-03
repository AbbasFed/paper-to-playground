"""Run agent.py repeatedly on practice cases and summarise the traces (tokens, calls, latency, repairs, checks).

    python tools/bench.py --runs 3 --label baseline
    python tools/bench.py --runs 2 --reasoning none low --label ab
    python tools/bench.py --summarise runs/baseline          (re-read existing traces only)

Spends real API tokens. The key comes from OPENROUTER_API_KEY or, failing that, a git-ignored .env file; it is
passed only to the agent processes and never printed. Outputs go to runs/<label>/ (git-ignored).
"""
import argparse
import glob
import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = "deepseek/deepseek-v4.1-flash"


def api_key():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    path = os.path.join(ROOT, ".env")
    if not key and os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                name, _, value = line.strip().partition("=")
                if name.strip().removeprefix("export ").strip() == "OPENROUTER_API_KEY":
                    key = value.strip().strip("\"'")
    return key


def run_one(job, key, model):
    os.makedirs(job["out"], exist_ok=True)
    env = dict(os.environ, OPENROUTER_API_KEY=key)
    t = time.monotonic()
    p = subprocess.run([sys.executable, os.path.join(ROOT, "agent.py"), "--input", job["case"], "--output", job["out"],
                        "--model", model, "--reasoning", job["reasoning"]], env=env, capture_output=True, text=True, timeout=900)
    job.update(exit_code=p.returncode, wall_s=round(time.monotonic() - t, 1))
    with open(os.path.join(job["out"], "bench.json"), "w", encoding="utf-8") as f:
        json.dump(job, f)
    return job


def read_trace(out):
    """Facts from one run's trace.jsonl (never its prompts or replies)."""
    ev = []
    with open(os.path.join(out, "trace.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                ev.append(json.loads(line))
    done = next((e for e in reversed(ev) if e["action"] == "exit"), {})
    page = next((e for e in reversed(ev) if e["action"] == "write_page"), {})
    meta = {}
    if os.path.exists(os.path.join(out, "bench.json")):
        with open(os.path.join(out, "bench.json"), encoding="utf-8") as f:
            meta = json.load(f)
    return {"exit_code": done.get("exit_code", meta.get("exit_code")), "wall_s": meta.get("wall_s", done.get("elapsed_s")),
            "requests": done.get("requests", 0), "prompt": done.get("prompt_tokens", 0), "completion": done.get("completion_tokens", 0),
            "reasoning": done.get("reasoning_tokens", 0), "total": done.get("total_tokens", 0),
            "checks_run": done.get("checks_run", 0), "checks_passed": done.get("checks_passed", 0),
            "repairs": sum(1 for e in ev if e["stage"] == "repair" and e["action"] == "revision"),
            "unresolved": page.get("unresolved", ["(no page written)"]),
            "guard": sum(1 for e in ev if e["action"] == "llm_call" and e["result"] == "failed" and str(e.get("error", "")).startswith(("empty content", "cut off"))),
            "relabelled": [e["field"] for e in ev if e["action"] == "label_grounding" and e["result"] == "replaced"],
            "sent": next((e.get("sent") for e in ev if e["action"] == "reasoning_setting"), None)}


def stats(xs):
    return "%.0f / %.0f / %.0f" % (statistics.mean(xs), min(xs), max(xs)) if xs else "-"


def summarise(base):
    rows = {}
    for trace in sorted(glob.glob(os.path.join(base, "*", "*", "run*", "trace.jsonl"))):
        out = os.path.dirname(trace)
        case, reasoning = out.split(os.sep)[-3], out.split(os.sep)[-2]
        rows.setdefault((case, reasoning), []).append(read_trace(out))
    head = ["case", "reasoning", "runs", "total tokens mean/min/max", "prompt", "completion", "reasoning tok", "API calls",
            "wall s", "repairs", "checks passed", "pass", "exit codes"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for (case, reasoning), rs in sorted(rows.items()):
        ok = [r for r in rs if r["exit_code"] == 0 and not r["unresolved"]]
        lines.append("| " + " | ".join([case, reasoning, str(len(rs)), stats([r["total"] for r in rs]), stats([r["prompt"] for r in rs]),
                                         stats([r["completion"] for r in rs]), stats([r["reasoning"] for r in rs]),
                                         stats([r["requests"] for r in rs]), stats([r["wall_s"] or 0 for r in rs]), stats([r["repairs"] for r in rs]),
                                         ", ".join("%d/%d" % (r["checks_passed"], r["checks_run"]) for r in rs),
                                         "%d/%d" % (len(ok), len(rs)), ", ".join(str(r["exit_code"]) for r in rs)]) + " |")
    extra = [(c, rz, i + 1, r) for (c, rz), rs in sorted(rows.items()) for i, r in enumerate(rs) if r["unresolved"] or r["guard"] or r["relabelled"]]
    notes = ["- %s / %s / run%d: unresolved=%s guard_retries=%d relabelled=%s" % (c, rz, i, r["unresolved"], r["guard"], r["relabelled"]) for c, rz, i, r in extra]
    text = "\n".join(lines + ([""] + notes if notes else []))
    with open(os.path.join(base, "summary.md"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    with open(os.path.join(base, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"%s/%s" % k: v for k, v in rows.items()}, f, indent=1)
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cases", nargs="*", help="case files (default: cases/*.json and cases/real/*.json)")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--reasoning", nargs="+", default=["off"])
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--label", default="bench")
    ap.add_argument("--summarise", metavar="DIR", help="only summarise an existing runs directory")
    a = ap.parse_args()
    if a.summarise:
        print(summarise(a.summarise))
        return 0
    key = api_key()
    if not key:
        print("OPENROUTER_API_KEY is not set and no .env provides it", file=sys.stderr)
        return 2
    cases = a.cases or sorted(glob.glob(os.path.join(ROOT, "cases", "*.json")) + glob.glob(os.path.join(ROOT, "cases", "real", "*.json")))
    base = os.path.join(ROOT, "runs", a.label)
    jobs = [{"case": c, "reasoning": r, "out": os.path.join(base, os.path.splitext(os.path.basename(c))[0], r, "run%d" % i)}
            for c in cases for r in a.reasoning for i in range(1, a.runs + 1)]
    print("%d runs (%d cases x %s x %d), %d in parallel -> %s" % (len(jobs), len(cases), "/".join(a.reasoning), a.runs, a.parallel, base), flush=True)
    with ThreadPoolExecutor(a.parallel) as pool:
        for j in pool.map(lambda j: run_one(j, key, a.model), jobs):
            print("  %-40s exit=%s %5.1fs" % (os.path.relpath(j["out"], base), j["exit_code"], j["wall_s"]), flush=True)
    print(summarise(base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
