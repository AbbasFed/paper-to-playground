"""The assembled page renders every required section; "How it works" shows content.steps in order."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import assemble  # noqa: E402
import checks as C  # noqa: E402
from fixtures import CONTENT, EXCERPT, tags  # noqa: E402


def page(**over):
    parts, results, _ = C.evaluate(tags(**over), EXCERPT)
    C.finalise(parts, results, None)
    return assemble.build_html(parts, {"source_url": "https://example.org/paper", "audience": "students"})


def section(html, sid):
    m = re.search(r'<section id="%s"[^>]*>(.*?)</section>' % sid, html, re.S)
    return m.group(1) if m else None


class HowItWorks(unittest.TestCase):
    def test_steps_rendered_in_order(self):
        html = page()
        how = section(html, "how")
        self.assertIn("How it works", how)
        self.assertEqual(re.findall(r"<li>(.*?)</li>", how), CONTENT["steps"])
        self.assertIn('href="#how"', html)

    def test_missing_steps_never_leave_an_empty_section(self):
        how = section(page(content=dict(CONTENT, steps=[])), "how")
        self.assertNotIn("<ol", how)
        self.assertIn("No step-by-step breakdown", how)

    def test_every_required_section_present(self):
        html = page()
        for sid in ("idea", "symbols", "how", "playground", "explore", "limitation", "grounding"):
            self.assertTrue(section(html, sid), sid)
        self.assertIn("does not reproduce the paper", html)


if __name__ == "__main__":
    unittest.main()
