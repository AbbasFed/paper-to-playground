"""Every check in checks.py can fail: each one is fed a deliberately broken variant of a known-good candidate.

    python -m unittest tests.test_checks_can_fail      (or: python tests/test_checks_can_fail.py --report)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import assemble  # noqa: E402
import checks as C  # noqa: E402
from fixtures import COMPUTE, CONTENT, CONTROLS, EXCERPT, READOUTS, RENDER, TESTS, tags  # noqa: E402

GOOD_QUOTE = CONTENT["quotes"][0]


def explore_set(s):
    return dict(CONTENT, explorations=[dict(CONTENT["explorations"][0], set=s), CONTENT["explorations"][1]])


def drop(name):
    t = tags()
    del t[name]
    return t


def no_engine():
    def boom():
        raise RuntimeError("engine missing")
    orig, C.new_engine = C.new_engine, boom
    try:
        return C.evaluate(tags(), EXCERPT)[1]
    finally:
        C.new_engine = orig


def good_page():
    parts, results, _ = C.evaluate(tags(), EXCERPT)
    C.finalise(parts, results, None)
    return assemble.build_html(parts, {"source_url": "", "audience": ""})


def cand(desc, **over):
    return desc, lambda: C.evaluate(tags(**over), EXCERPT)[1]


# check name -> (deliberately bad input, how to get the results)
CASES = {
    "tags_present": ("no <render> tag", lambda: C.evaluate(drop("render"), EXCERPT)[1]),
    "json_content": cand("<content> is '{title: oops'", content="{title: oops"),
    "json_controls": cand("<controls> is '[{type: slider'", controls="[{type: slider"),
    "json_readouts": cand("<readouts> is '[{'", readouts="[{"),
    "json_tests": cand("<tests> is '[{,]'", tests="[{,]"),
    "content_complete": cand("empty limitation and formula", content=dict(CONTENT, limitation="", formula="")),
    "content_fallbacks": cand("empty 'why' and no steps (template fallbacks, no repair)", content=dict(CONTENT, why="", steps=[])),
    "content_no_latex": cand("formula with \\begin{pmatrix}", content=dict(CONTENT, formula="\\begin{pmatrix} a \\end{pmatrix}")),
    "quotes_grounded": cand("the only quote is not in the excerpt", content=dict(CONTENT, quotes=["words that never occur in the source text"])),
    "quotes_dropped": cand("one good and one invented quote", content=dict(CONTENT, quotes=[GOOD_QUOTE, "an invented sentence about attention heads"])),
    "controls_valid": cand("a control id '2bad' (not a JS identifier)", controls=CONTROLS + [{"type": "slider", "id": "2bad", "min": 0, "max": 1, "default": 0}]),
    "controls_autofixed": cand("a slider without max", controls=CONTROLS + [{"type": "slider", "id": "extra", "min": 0, "default": 0}]),
    "controls_fitted": cand("exploration sets k=4 above its max 3", content=explore_set({"k": 4})),
    "explorations_apply": cand("exploration sets an unknown control", content=explore_set({"zzz": 1})),
    "tests_params_shape": cand("test params name an unknown control", tests=TESTS + [{"name": "bad params", "params": {"zzz": 1}, "expect": "true"}]),
    "code_defines_compute": cand("compute renamed to calc", compute=COMPUTE.replace("function compute", "function calc")),
    "code_defines_render": cand("render renamed to draw2", render=RENDER.replace("function render", "function draw2")),
    "code_pure_compute": cand("compute calls Math.random()", compute=COMPUTE.replace("var rs", "var noise = Math.random();\n  var rs")),
    "code_pure_render": cand("render reads document.title", render=RENDER.replace("return H.grid", "var t = document.title;\n  return H.grid")),
    "js_engine": ("embedded V8 cannot start", no_engine),
    "js_load_compute": cand("compute has a syntax error", compute="function compute(p) { return {; }"),
    "js_load_render": cand("render has a syntax error", render="function render(p, out) { return '<svg>' + ; }"),
    "compute_defaults": cand("compute returns NaN at the defaults", compute=COMPUTE.replace("total: H.sum(p.p)", "total: NaN")),
    "edge_sweep": cand("compute throws when n is at its max", compute=COMPUTE.replace("var rs", "if (p.n === 5) throw new Error('max breaks');\n  var rs")),
    "edge_infinity": cand("compute divides by k-1 (Infinity at k=1)", compute=COMPUTE.replace("grand: H.sum(rs)", "grand: H.sum(rs) / (p.k - 1)")),
    "controls_meaningful": cand("neither compute nor render uses the values in M", compute=COMPUTE.replace("H.sum(row)", "0"),
                                render=RENDER.replace("matrix: p.M", "matrix: [[0]]")),
    "test: known case": cand("known case expects total 99", tests=[TESTS[0], TESTS[1], dict(TESTS[2], expect="out.total === 99")]),
    "tests_present": cand("no tests", tests=[]),
    "invariant: total stays small": cand("invariant true at defaults, false when p is all 9",
                                         tests=TESTS + [{"name": "total stays small", "params": {}, "expect": "out.total < 20"}]),
    "render_defaults": cand("render returns no <svg>", render="function render(p, out) { return '<div>no picture</div>'; }"),
    "render_edges": cand("render prints NaN when n is at its max",
                         render=RENDER.replace("return H.grid", "if (p.n === 5) return '<svg><text>NaN</text></svg>';\n  return H.grid")),
    "explorations_at_defaults": cand("exploration 1 sets n to its default 3", content=explore_set({"n": 3})),
    "explorations_distinct": cand("both explorations set n = 2, r = 1 and nothing else",
                                  content=dict(CONTENT, explorations=[dict(e, set={"n": 2, "r": 1}, then={}) for e in CONTENT["explorations"]])),
    "exploration_numbers": cand("exploration text quotes 7.25, a value compute() never produces there",
                                content=dict(CONTENT, explorations=[dict(CONTENT["explorations"][0], observe="The total reads 7.25 after shrinking"),
                                                                    CONTENT["explorations"][1]])),
    "exploration_claims": cand("exploration 1 claims a total above 100",
                               content=dict(CONTENT, explorations=[dict(CONTENT["explorations"][0], expect="out.total > 100"),
                                                                   CONTENT["explorations"][1]])),
    "resize_sweep": cand("compute throws at the inner size n=4", compute=COMPUTE.replace("var rs", "if (p.p.length === 4) throw new Error('size 4');\n  var rs")),
    "readouts_resolve": cand("no readout key exists in out", readouts=[{"key": "nope1", "label": "a"}, {"key": "nope2", "label": "b"}]),
    "page_self_contained": ("page with a CDN stylesheet", lambda: C.page_checks(good_page() + '<link rel="stylesheet" href="https://cdn.example/x.css">')),
    "page_js_syntax": ("page with a broken inline script", lambda: C.page_checks(good_page() + "<script>function (</script>")),
    "page_size": ("page over 1 MB", lambda: C.page_checks(good_page() + "x" * 1_000_001)),
    "page_no_secret": ("page containing the API key", lambda: C.page_checks(good_page() + "sk-test-123", "sk-test-123")),
}
# by design never fails: with no excerpt there is nothing to verify quotes against (logged as a soft pass)
CANNOT_FAIL = {"quotes_grounded (no-excerpt variant)": "always ok=True: quotes are simply omitted when no excerpt exists"}


class GoodBaseline(unittest.TestCase):
    def test_good_candidate_fails_nothing(self):
        self.assertEqual([c["name"] for c in C.evaluate(tags(), EXCERPT)[1] if not c["ok"]], [])
        self.assertEqual([c["name"] for c in C.page_checks(good_page(), "sk-test-123") if not c["ok"]], [])


class ExplorationClaims(unittest.TestCase):
    def claim(self, expect):
        content = dict(CONTENT, explorations=[dict(CONTENT["explorations"][0], expect=expect), CONTENT["explorations"][1]])
        return [c for c in C.evaluate(tags(content=content), EXCERPT)[1] if c["name"] == "exploration_claims"][0]

    def test_true_claim_passes(self):
        self.assertTrue(self.claim("out.count === 1 && out.total >= 0")["ok"])

    def test_false_claim_is_hard_and_names_the_false_part(self):
        c = self.claim("out.count === 1 && out.total > 100")
        self.assertEqual((c["ok"], c["severity"]), (False, "hard"))
        self.assertIn("out.total > 100", c["message"])

    def test_expect_reading_a_missing_output_is_only_unverified(self):
        # rowSums is a vector: rowSums[0][0] does not exist, so this says nothing about the claim
        c = self.claim("out.count === 1 && Math.abs(out.rowSums[0][0] - 1) < 1e-9")
        self.assertEqual((c["ok"], c["severity"]), (False, "soft"))
        self.assertIn("malformed", c["message"])


class EveryCheckCanFail(unittest.TestCase):
    def test_each_check_fails_on_its_bad_input(self):
        for name, (desc, get) in CASES.items():
            with self.subTest(check=name, bad_input=desc):
                hit = [c for c in get() if c["name"] == name]
                self.assertTrue(hit, "check %r was not produced" % name)
                self.assertFalse(hit[0]["ok"], "check %r did not fail" % name)

    def test_no_check_is_left_out(self):
        produced = set()
        for _, get in CASES.values():
            produced |= {c["name"] for c in get()}
        produced |= {c["name"] for c in C.evaluate(tags(), EXCERPT)[1]} | {c["name"] for c in C.page_checks(good_page(), "k")}
        generic = {n for n in produced if not n.startswith(("test: ", "invariant: "))}
        self.assertEqual(generic - set(CASES), set(), "checks without a failing case")


if __name__ == "__main__":
    if "--report" in sys.argv:
        for name, (desc, get) in CASES.items():
            hit = [c for c in get() if c["name"] == name]
            print("%-28s %-5s %-62s %s" % (name, "FAIL" if hit and not hit[0]["ok"] else "??", desc, (hit[0]["message"] if hit else "not produced")[:90]))
        for name, why in CANNOT_FAIL.items():
            print("%-28s %-5s %s" % (name, "never", why))
    else:
        unittest.main()
