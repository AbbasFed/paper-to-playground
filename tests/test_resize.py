"""Resizing: spec normalisation, the V8 resize sweep, and (if Playwright is installed) the real page in Chromium."""
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import assemble  # noqa: E402
import checks as C  # noqa: E402
from fixtures import COMPUTE, CONTROLS, EXCERPT, tags  # noqa: E402


def by_name(results):
    return {c["name"]: c for c in results}


class SpecNormalisation(unittest.TestCase):
    def test_size_sliders_become_integers_from_1_and_matrices_are_padded(self):
        spec = [{"type": "slider", "id": "r", "min": 0, "max": 3.5, "step": 0.5, "default": 2},
                {"type": "matrix", "id": "M", "default": [[1, 2], [3, 4]], "min": 0, "max": 9, "rowsFrom": "r", "colsFrom": "r"}]
        controls, _, errors = C.normalise_controls(spec)
        self.assertEqual(errors, [])
        C.fit_controls(controls, [])
        r, m = controls
        self.assertEqual((r["min"], r["max"], r["step"]), (1, 3, 1))
        self.assertEqual((m["rows"], m["cols"]), (3, 3))
        self.assertEqual(m["default"], [[1, 2, 2], [3, 4, 4], [3, 4, 4]])

    def test_bad_size_link_is_dropped(self):
        controls, notes, _ = C.normalise_controls([{"type": "toggle", "id": "t"}, {"type": "matrix", "id": "M", "default": [[1]], "min": 0, "max": 1, "rowsFrom": "t"}])
        self.assertNotIn("rowsFrom", controls[1])
        self.assertTrue(any("rowsFrom" in n for n in notes))

    def test_smaller_overrides_only_for_linked_matrices(self):
        controls, _, _ = C.normalise_controls(CONTROLS)
        C.fit_controls(controls, [])
        self.assertEqual(C.override_problems(controls, {"M": [[1]], "p": [1, 2]}), [])
        fixed = [dict(c) for c in controls]
        del fixed[4]["rowsFrom"], fixed[4]["colsFrom"]
        self.assertEqual(len(C.override_problems(fixed, {"M": [[1]]})), 1)


class ResizeSweep(unittest.TestCase):
    def test_good_candidate_passes_at_every_size(self):
        _, results, rep = C.evaluate(tags(), EXCERPT)
        r = by_name(results)
        self.assertTrue(r["resize_sweep"]["ok"], r["resize_sweep"]["message"])
        self.assertEqual(rep["resizeSettings"], 5 + 3 + 3)        # n = 1..5, r = 1..3, k = 1..3
        self.assertEqual(C.hard_failures(results), [])

    def test_failure_at_an_inner_size_is_caught(self):
        bad = COMPUTE.replace("var rs", "if (p.p.length === 4) throw new Error('size four breaks');\n  var rs")
        r = by_name(C.evaluate(tags(compute=bad), EXCERPT)[1])
        self.assertTrue(r["edge_sweep"]["ok"])                   # min, max and middle sizes all work ...
        self.assertFalse(r["resize_sweep"]["ok"])                # ... only the full size sweep finds n = 4
        self.assertIn("n=4", r["resize_sweep"]["message"])

    def test_render_failure_at_size_one_is_caught(self):
        bad_render = "function render(p, out) { if (p.M.length === 1) return 'no svg'; return H.bars({labels: ['a'], values: [out.total]}); }"
        r = by_name(C.evaluate(tags(render=bad_render), EXCERPT)[1])
        self.assertFalse(r["resize_sweep"]["ok"])
        self.assertIn("r=1", r["resize_sweep"]["message"])


try:
    from playwright.sync_api import sync_playwright
except ImportError:   # dev-only dependency (dev/requirements-dev.txt)
    sync_playwright = None


@unittest.skipUnless(sync_playwright, "Playwright not installed (dev-only)")
class PageInBrowser(unittest.TestCase):
    """The template's own resizing code: size 1, shrinking and growing, typed values kept, no page errors."""

    def setUp(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch()

    def tearDown(self):
        self.browser.close()
        self.pw.stop()

    def open(self, candidate):
        parts, results, _ = C.evaluate(candidate, EXCERPT)
        C.finalise(parts, results, None)
        path = os.path.join(tempfile.mkdtemp(), "index.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(assemble.build_html(parts, {"source_url": "", "audience": ""}))
        page, errors = self.browser.new_page(), []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("file:///" + path.replace("\\", "/"))
        ids = [c["id"] for c in parts["controls"]]
        return page, errors, lambda cid: page.locator("#pg-controls .ctl").nth(ids.index(cid)).locator(".cells input[type=number]")

    def assertHealthy(self, page, errors):
        self.assertEqual(errors, [])
        self.assertTrue(page.locator("#pg-error").is_hidden())
        self.assertIsNone(re.search(r"\b(NaN|undefined)\b", page.inner_text("body")))

    def test_size_typed_into_a_number_box(self):
        controls = [dict(c) for c in CONTROLS]
        controls[0] = dict(controls[0], type="number")            # the size is typed, not slid
        page, errors, cells = self.open(tags(controls=controls))
        for typed, want in (("1", 1), ("5", 5), ("2", 2)):
            page.locator("#ctl-n").fill(typed)
            self.assertEqual(cells("p").count(), want, "after typing %s" % typed)
        self.assertHealthy(page, errors)

    def test_resizing_in_chromium(self):
        page, errors, cells = self.open(tags())
        total = lambda: page.locator("#pg-readouts tr").nth(0).inner_text()
        page.locator("#ctl-n").press("Home")                     # size 1
        self.assertEqual(cells("p").count(), 1)
        cells("p").nth(0).fill("7")
        self.assertIn("7", total())
        page.locator("#ctl-n").press("End")                      # grow: the typed 7 survives
        self.assertEqual(cells("p").count(), 5)
        self.assertEqual(cells("p").nth(0).input_value(), "7")
        page.locator("#ctl-n").press("Home")
        page.locator("#ctl-n").press("ArrowRight")               # shrink back to 2
        self.assertEqual(cells("p").count(), 2)

        for slider in ("#ctl-r", "#ctl-k"):
            page.locator(slider).press("Home")
        self.assertEqual(cells("M").count(), 1)                  # 1x1 matrix
        cells("M").nth(0).fill("-9")
        for slider in ("#ctl-r", "#ctl-k"):
            page.locator(slider).press("End")
        self.assertEqual(cells("M").count(), 9)
        self.assertEqual(cells("M").nth(0).input_value(), "-9")

        page.locator("button[data-set]").nth(0).click()          # exploration: n = 1, r = 1
        self.assertEqual(cells("p").count(), 1)
        page.locator("#pg-reset").click()                         # defaults: n = 3, r = k = 2
        self.assertEqual((cells("p").count(), cells("M").count()), (3, 4))
        self.assertHealthy(page, errors)


if __name__ == "__main__":
    unittest.main()
