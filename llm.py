"""OpenRouter chat client with a hard per-case budget (requests, completion tokens, wall clock)."""
import os
import threading
import time

import requests

URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_REQUESTS = 10
MAX_COMPLETION_TOKENS = 30000
DEADLINE_S = 510.0        # 8.5 min: leaves margin inside the 10 min limit for assembly and writing
CALL_TIMEOUT_S = 150.0
TOKEN_MARGIN = 500        # never plan to spend the last few completion tokens


class Budget:
    def __init__(self):
        self.t0 = time.monotonic()
        self.requests = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0

    def elapsed(self):
        return time.monotonic() - self.t0

    def time_left(self):
        return DEADLINE_S - self.elapsed()

    def tokens_left(self):
        return MAX_COMPLETION_TOKENS - TOKEN_MARGIN - self.completion_tokens

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
                "elapsed_s": round(self.elapsed(), 2)}


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


def chat(model, messages, max_tokens, budget, trace, stage, temperature=0.3, reasoning=None, max_attempts=3):
    """One logical call with retries. Every HTTP attempt counts toward the request cap.
    Returns {'text', 'finish_reason'} or None."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    backoff = [2.0, 5.0, 8.0]
    for attempt in range(max_attempts):
        why = budget.allow()
        if why:
            trace.event(stage, "llm_call", "skipped", reason=why)
            return None
        cap = int(min(max_tokens, budget.tokens_left()))
        timeout = max(5.0, min(CALL_TIMEOUT_S, budget.time_left() - 10.0))
        payload = {"model": model, "messages": messages, "max_tokens": cap, "temperature": temperature, "usage": {"include": True}}
        if reasoning:
            payload["reasoning"] = reasoning
        budget.requests += 1
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
        info = {"request_no": budget.requests, "attempt": attempt + 1, "model": model, "max_tokens": cap, "http_status": status,
                "generation_id": data.get("id"), "prompt_tokens": pt, "completion_tokens": ct, "reasoning_tokens": rt, "elapsed_s": elapsed}

        choice = (data.get("choices") or [{}])[0] if status == 200 else {}
        text = ((choice.get("message") or {}).get("content") or "") if isinstance(choice, dict) else ""
        if status == 200 and isinstance(text, str) and text.strip() and not data.get("error"):
            info["finish_reason"] = choice.get("finish_reason")
            trace.event(stage, "llm_call", "ok", chars=len(text), **info)
            return {"text": text, "finish_reason": choice.get("finish_reason")}

        err = data.get("error") if isinstance(data.get("error"), dict) else (choice.get("error") if isinstance(choice, dict) and isinstance(choice.get("error"), dict) else {})
        msg = str(err.get("message") or box.get("error") or box.get("text") or ("timeout" if th.is_alive() else "empty response"))[:240]
        if key:
            msg = msg.replace(key, "[REDACTED]")
        retry = status is None or status in (408, 409, 425, 429) or status >= 500 or status == 200
        if status == 400 and reasoning and "reason" in msg.lower():
            reasoning, retry = None, True   # model rejects the reasoning setting: retry without it
        trace.event(stage, "llm_call", "failed", error=msg, will_retry=bool(retry and attempt + 1 < max_attempts), **info)
        if not retry:
            return None
        if attempt + 1 < max_attempts:
            time.sleep(min(backoff[attempt], max(0.0, budget.time_left() - 20.0)))
    return None
