"""JSONL execution trace: one object per event, flushed immediately so a crash still leaves a record."""
import json
import os
import time


class Trace:
    def __init__(self, path, secret=""):
        self.t0 = time.monotonic()
        self.secret = secret or ""
        self.f = open(path, "w", encoding="utf-8", newline="\n")

    def event(self, stage, action, result, **extra):
        rec = {"t": round(time.monotonic() - self.t0, 3), "stage": stage, "action": action, "result": result}
        rec.update(extra)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        if self.secret and self.secret in line:  # belt and braces: credentials never reach the trace
            line = line.replace(self.secret, "[REDACTED]")
        self.f.write(line + "\n")
        self.f.flush()
        try:
            os.fsync(self.f.fileno())
        except OSError:
            pass

    def close(self):
        try:
            self.f.close()
        except OSError:
            pass
