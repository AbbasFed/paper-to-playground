"""OpenRouter chat client with a hard per-case budget (requests, completion tokens, wall clock)."""
import os
import threading
import time

import requests

URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
MAX_REQUESTS = 10
MAX_COMPLETION_TOKENS = 30000
DEADLINE_S = 510.0        # 8.5 min: leaves margin inside the 10 min limit for assembly and writing
CALL_TIMEOUT_S = 150.0
TOKEN_MARGIN = 500        # never plan to spend the last few completion tokens
EMPTY_CONTENT_TOKENS = 16  # visible tokens (completion - reasoning) at or below this mean the model wrote nothing
EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]   # OpenRouter effort scale, lowest first


class Budget:
    def __init__(self):
        self.t0 = time.monotonic()
        self.requests = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0
        self.unverified_tokens = 0    # max_tokens of calls that returned no usage (assumed spent)

    def elapsed(self):
        return time.monotonic() - self.t0

    def time_left(self):
        return DEADLINE_S - self.elapsed()

    def tokens_left(self):
        return MAX_COMPLETION_TOKENS - TOKEN_MARGIN - self.completion_tokens - self.unverified_tokens

    def allow(self, min_tokens=1200, min_time=20.0):
        """Return '' if another request fits in the budget, else the reason it does not."""
        if self.requests >= MAX_REQUESTS:
            return "request cap reached (%d/%d)" % (self.requests, MAX_REQUESTS)
        if self.tokens_left() < min_tokens:
            return "completion-token budget nearly spent (%d/%d)" % (self.completion_tokens, MAX_COMPLETION_TOKENS)
        if self.time_left() < min_time:
            return "time budget nearly spent (%.0fs elapsed)" % self.elapsed()
        return ""

    def summary(self):
        return {"requests": self.requests, "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "reasoning_tokens": self.reasoning_tokens, "total_tokens": self.prompt_tokens + self.completion_tokens,
                "unverified_completion_reserve": self.unverified_tokens,
                "elapsed_s": round(self.elapsed(), 2)}


# ---------------------------------------------------------------- reasoning settings

def model_info(model, budget, trace):
    """The model's `reasoning` capabilities from GET /api/v1/models, or None if they cannot be read.
    The lookup is an OpenRouter API request, so it counts toward the request cap."""
    why = budget.allow(min_tokens=0, min_time=60.0)
    if why:
        trace.event("prepare", "model_info", "skipped", reason=why)
        return None
    budget.requests += 1
    t = time.monotonic()
    try:
        r = requests.get(MODELS_URL, timeout=(5, 20), headers={"User-Agent": "paper-to-playground"})
        r.raise_for_status()
        m = next((x for x in r.json().get("data", []) if isinstance(x, dict) and x.get("id") == model), None)
    except (requests.RequestException, ValueError, AttributeError) as e:
        trace.event("prepare", "model_info", "failed", error=type(e).__name__, request_no=budget.requests,
                    elapsed_s=round(time.monotonic() - t, 2))
        return None
    if m is None:
        trace.event("prepare", "model_info", "not_found", model=model, request_no=budget.requests, elapsed_s=round(time.monotonic() - t, 2))
        return None
    info = dict(m.get("reasoning") or {}, supported="reasoning" in (m.get("supported_parameters") or []))
    trace.event("prepare", "model_info", "ok", model=model, reasoning=info, request_no=budget.requests, elapsed_s=round(time.monotonic() - t, 2))
    return info


def _efforts(info):
    return [e for e in EFFORTS if e in ((info or {}).get("supported_efforts") or [])]


def choose_reasoning(requested, info):
    """Map --reasoning to a `reasoning` object this model accepts. Reasoning text is always excluded.
    Returns (object or None, why)."""
    if info is not None and not info.get("supported"):
        return {"exclude": True}, "model does not advertise reasoning support"
    efforts, mandatory = _efforts(info), bool((info or {}).get("mandatory"))
    if requested == "auto":
        return {"exclude": True}, "model default"
    if requested in ("off", "none"):
        if mandatory and efforts:   # cannot be switched off: use the cheapest allowed effort instead
            return {"effort": efforts[0], "exclude": True}, "reasoning is mandatory for this model; lowest effort %r" % efforts[0]
        return {"enabled": False, "exclude": True}, "reasoning disabled"
    if info is None or not efforts or requested in efforts:
        return {"effort": requested, "exclude": True}, "effort %r" % requested
    lower = [e for e in efforts if EFFORTS.index(e) <= EFFORTS.index(requested)]
    pick = lower[-1] if lower else efforts[0]
    return {"effort": pick, "exclude": True}, "effort %r not offered (supported: %s); using %r" % (requested, ", ".join(efforts), pick)


def lower_reasoning(reasoning, info):
    """One step down the effort scale for a retry, or None when nothing lower is allowed."""
    if not reasoning or reasoning.get("enabled") is False:
        return None
    efforts = _efforts(info) or ["low", "medium", "high"]
    cur = reasoning.get("effort") or (info or {}).get("default_effort") or "high"
    below = [e for e in efforts if cur in EFFORTS and EFFORTS.index(e) < EFFORTS.index(cur)]
    if below:
        return {"effort": below[-1], "exclude": True}
    return None if (info or {}).get("mandatory") else {"enabled": False, "exclude": True}


# ---------------------------------------------------------------- chat

def _post(payload, key, timeout, box):
    try:
        r = requests.post(URL, json=payload, timeout=(10, timeout),
                          headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "X-Title": "Paper to Playground"})
        box["status"] = r.status_code
        try:
            box["json"] = r.json()
        except ValueError:
            box["text"] = r.text[:300]
    except requests.RequestException as e:
        box["error"] = type(e).__name__


def chat(model, messages, max_tokens, budget, trace, stage, temperature=0.3, reasoning=None, max_attempts=3,
         reasoning_info=None, provider=None):
    """One logical call with retries. Every HTTP attempt counts toward the request cap.
    A reply that is empty or cut off at max_tokens is logged as a failure and retried once with lower
    reasoning effort; if that also fails, a cut-off reply is still returned so repair can finish it.
    Returns {'text', 'finish_reason', 'reasoning'} or None."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    backoff = [2.0, 5.0, 8.0]
    attempt, failures, guard_used, salvage = 0, 0, False, ""
    while True:
        why = budget.allow()
        if why:
            trace.event(stage, "llm_call", "skipped", reason=why)
            break
        cap = int(min(max_tokens, budget.tokens_left()))
        timeout = max(5.0, min(CALL_TIMEOUT_S, budget.time_left() - 10.0))
        payload = {"model": model, "messages": messages, "max_tokens": cap, "temperature": temperature, "usage": {"include": True}}
        if reasoning:
            payload["reasoning"] = reasoning
        if provider:
            payload["provider"] = provider
        budget.requests += 1
        attempt += 1
        t = time.monotonic()
        box = {}
        th = threading.Thread(target=_post, args=(payload, key, timeout, box), daemon=True)
        th.start()
        th.join(timeout + 12.0)   # hard wall-clock stop even if the server trickles bytes
        elapsed = round(time.monotonic() - t, 2)
        data = box.get("json") if isinstance(box.get("json"), dict) else {}
        status = box.get("status")
        usage = data.get("usage") or {}
        pt, ct = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        rt = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
        budget.prompt_tokens += pt
        budget.completion_tokens += ct
        budget.reasoning_tokens += rt
        if not usage and status in (None, 200):
            budget.unverified_tokens += cap
        info = {"request_no": budget.requests, "attempt": attempt, "model": model, "max_tokens": cap, "reasoning": reasoning, "http_status": status,
                "generation_id": data.get("id"), "prompt_tokens": pt, "completion_tokens": ct, "reasoning_tokens": rt, "elapsed_s": elapsed}

        choice = (data.get("choices") or [{}])[0] if status == 200 else {}
        choice = choice if isinstance(choice, dict) else {}
        text = (choice.get("message") or {}).get("content") or ""
        if status == 200 and isinstance(text, str) and not data.get("error") and not choice.get("error"):
            fr = choice.get("finish_reason")
            info["finish_reason"] = fr
            empty = not text.strip() or (ct - rt <= EMPTY_CONTENT_TOKENS and len(text.strip()) < 400)
            if not empty and fr != "length":
                trace.event(stage, "llm_call", "ok", chars=len(text), **info)
                return {"text": text, "finish_reason": fr, "reasoning": reasoning}
            if empty:
                problem = "empty content (%d completion - %d reasoning tokens)" % (ct, rt)
            else:
                problem = "cut off at max_tokens (finish_reason=length; %d completion tokens, %d of them reasoning)" % (ct, rt)
                salvage = text if len(text) > len(salvage) else salvage
            lower = lower_reasoning(reasoning, reasoning_info)
            retry = not guard_used and (lower is not None or empty)
            trace.event(stage, "llm_call", "failed", error=problem, chars=len(text), will_retry=retry,
                        retry_reasoning=(lower or reasoning) if retry else None, **info)
            if not retry:
                break
            guard_used = True
            reasoning = lower or reasoning
            continue

        failures += 1
        err = data.get("error") if isinstance(data.get("error"), dict) else (choice.get("error") if isinstance(choice.get("error"), dict) else {})
        msg = str(err.get("message") or box.get("error") or box.get("text") or ("timeout" if th.is_alive() else "empty response"))[:240]
        if key:
            msg = msg.replace(key, "[REDACTED]")
        retry = status is None or status in (408, 409, 425, 429) or status >= 500 or status == 200
        if status == 400 and reasoning and "reason" in msg.lower():
            reasoning, retry = None, True   # model rejects the reasoning setting: retry without it
        trace.event(stage, "llm_call", "failed", error=msg, will_retry=bool(retry and failures < max_attempts), **info)
        if not retry or failures >= max_attempts:
            break
        time.sleep(min(backoff[min(failures, len(backoff)) - 1], max(0.0, budget.time_left() - 20.0)))
    if salvage:
        trace.event(stage, "llm_call", "salvaged", chars=len(salvage), reason="kept the cut-off reply; missing parts go to repair")
        return {"text": salvage, "finish_reason": "length", "reasoning": reasoning}
    return None
