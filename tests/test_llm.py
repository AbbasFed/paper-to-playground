"""Offline tests for llm.py: reasoning selection, the model probe and the empty/cut-off reply guard (HTTP is faked)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import llm  # noqa: E402

DEEPSEEK = {"supported": True, "mandatory": False, "default_enabled": True, "supported_efforts": ["max", "high", "low"], "default_effort": "high"}
OFF = {"enabled": False, "exclude": True}


class FakeTrace:
    def __init__(self):
        self.events = []

    def event(self, stage, action, result, **kw):
        self.events.append(dict(kw, stage=stage, action=action, result=result))


def reply(content, finish="stop", ct=800, rt=0):
    return {"status": 200, "json": {"id": "gen-test", "choices": [{"message": {"content": content}, "finish_reason": finish}],
                                    "usage": {"prompt_tokens": 50, "completion_tokens": ct, "completion_tokens_details": {"reasoning_tokens": rt}}}}


def run_chat(replies, reasoning, info=DEEPSEEK):
    sent, replies = [], list(replies)

    def fake_post(payload, key, timeout, box):
        sent.append(payload)
        box.update(replies.pop(0))

    orig, llm._post = llm._post, fake_post
    try:
        trace, budget = FakeTrace(), llm.Budget()
        out = llm.chat("m", [{"role": "user", "content": "x"}], 1000, budget, trace, "generate", reasoning=reasoning, reasoning_info=info)
    finally:
        llm._post = orig
    return out, sent, trace, budget


class ChooseReasoning(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(llm.choose_reasoning("off", DEEPSEEK)[0], OFF)
        self.assertEqual(llm.choose_reasoning("none", DEEPSEEK)[0], OFF)
        self.assertEqual(llm.choose_reasoning("low", DEEPSEEK)[0], {"effort": "low", "exclude": True})
        self.assertEqual(llm.choose_reasoning("medium", DEEPSEEK)[0], {"effort": "low", "exclude": True})   # not offered: next lower
        self.assertEqual(llm.choose_reasoning("auto", DEEPSEEK)[0], {"exclude": True})
        self.assertEqual(llm.choose_reasoning("low", None)[0], {"effort": "low", "exclude": True})          # probe failed
        self.assertEqual(llm.choose_reasoning("low", {"supported": False})[0], {"exclude": True})

    def test_mandatory_cannot_be_disabled(self):
        info = {"supported": True, "mandatory": True, "supported_efforts": ["high", "low"]}
        self.assertEqual(llm.choose_reasoning("off", info)[0], {"effort": "low", "exclude": True})
        self.assertIsNone(llm.lower_reasoning({"effort": "low", "exclude": True}, info))

    def test_lowering(self):
        self.assertEqual(llm.lower_reasoning({"effort": "high", "exclude": True}, DEEPSEEK), {"effort": "low", "exclude": True})
        self.assertEqual(llm.lower_reasoning({"effort": "low", "exclude": True}, DEEPSEEK), OFF)
        self.assertEqual(llm.lower_reasoning({"exclude": True}, DEEPSEEK), {"effort": "low", "exclude": True})   # model default = high
        self.assertIsNone(llm.lower_reasoning(OFF, DEEPSEEK))


class ModelInfo(unittest.TestCase):
    def probe(self, data):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": data}
        orig, llm.requests.get = llm.requests.get, lambda *a, **k: R()
        try:
            budget = llm.Budget()
            return llm.model_info("deepseek/x", budget, FakeTrace()), budget
        finally:
            llm.requests.get = orig

    def test_found_and_counted(self):
        info, budget = self.probe([{"id": "deepseek/x", "supported_parameters": ["reasoning"], "reasoning": {"mandatory": False, "supported_efforts": ["low"]}}])
        self.assertEqual(info, {"mandatory": False, "supported_efforts": ["low"], "supported": True})
        self.assertEqual(budget.requests, 1)

    def test_not_found(self):
        self.assertIsNone(self.probe([{"id": "other"}])[0])


class EmptyContentGuard(unittest.TestCase):
    def test_empty_reply_retried_once_with_lower_effort(self):
        out, sent, trace, budget = run_chat([reply("", ct=900, rt=900), reply("<content>{}</content>")], {"effort": "low", "exclude": True})
        self.assertEqual(out["text"], "<content>{}</content>")
        self.assertEqual(sent[1]["reasoning"], OFF)
        self.assertEqual(budget.requests, 2)
        failed = [e for e in trace.events if e["result"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("empty content", failed[0]["error"])
        self.assertEqual(failed[0]["reasoning_tokens"], 900)
        self.assertNotIn("reasoning_text", str(trace.events))

    def test_visible_tokens_near_zero_counts_as_empty(self):
        out, sent, _, _ = run_chat([reply("ok", ct=500, rt=495), reply("<content>{}</content>")], {"effort": "high", "exclude": True})
        self.assertEqual(sent[1]["reasoning"], {"effort": "low", "exclude": True})
        self.assertEqual(out["reasoning"], {"effort": "low", "exclude": True})

    def test_cut_off_reply_retried_then_salvaged(self):
        out, sent, trace, budget = run_chat([reply("<content>{\"a\":", finish="length"), reply("", ct=0)], {"effort": "low", "exclude": True})
        self.assertEqual(budget.requests, 2)                       # retried once, never more
        self.assertEqual(out["finish_reason"], "length")           # the cut-off text is kept for repair
        self.assertTrue(out["text"].startswith("<content>"))
        self.assertTrue(any(e["result"] == "salvaged" for e in trace.events))

    def test_cut_off_without_lower_effort_is_not_retried(self):
        out, sent, _, budget = run_chat([reply("<content>{", finish="length")], OFF)
        self.assertEqual(budget.requests, 1)
        self.assertEqual(out["finish_reason"], "length")

    def test_exclude_always_sent(self):
        _, sent, _, _ = run_chat([reply("", ct=0), reply("fine " * 50)], {"effort": "high", "exclude": True})
        self.assertTrue(all(p["reasoning"].get("exclude") is True for p in sent))


if __name__ == "__main__":
    unittest.main()
