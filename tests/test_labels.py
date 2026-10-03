"""Offline tests for section/equation label grounding."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from checks import UNGROUNDED_LABEL, _norm, ground_labels, label_grounded, quote_score  # noqa: E402

with open(os.path.join(ROOT, "cases", "attention.json"), encoding="utf-8") as f:
    CASE = json.load(f)
ATT = [CASE["excerpt"], CASE["focus"], CASE["title"]]


class Labels(unittest.TestCase):
    def test_supported_labels_kept(self):
        for label, kind in (("§3.2.1", "section"), ("Section 3.2.1", "section"), ("Eq. (1)", "equation"), ("Equation 1", "equation"),
                            ("Scaled Dot-Product Attention", "section"), ("Attention(Q, K, V)", "equation")):
            self.assertTrue(label_grounded(label, kind, ATT), label)

    def test_invented_labels_rejected(self):
        for label, kind in (("§3.2.2", "section"), ("Section 4", "section"), ("Eq. (2)", "equation"), ("Multi-head attention", "section"), ("", "section")):
            self.assertFalse(label_grounded(label, kind, ATT), label)

    def test_other_case_fields_count(self):
        self.assertTrue(label_grounded("§6", "section", ["entropy is defined below", "Shannon, Section 6: choice and uncertainty"]))
        self.assertFalse(label_grounded("§6", "section", ["entropy with 6 outcomes"]))   # a bare 6 is not a section label

    def test_named_labels_written_as_in_the_source(self):
        src = ["Algorithm 1: a generic update rule. Theorem 2: the only H of this form.", "3.1 Training and inference"]
        for label, kind in (("Algorithm 1", "section"), ("Algorithm 1", "equation"), ("Theorem 2", "equation"), ("§3.1, Algorithm 1", "section")):
            self.assertTrue(label_grounded(label, kind, src), label)
        for label, kind in (("Algorithm 2", "section"), ("Theorem 1", "equation"), ("§3.2, Algorithm 1", "section"), ("Algorithm 12", "section")):
            self.assertFalse(label_grounded(label, kind, src), label)

    def test_replacement_and_notes(self):
        content = {"section": "§3.2.1", "equation": "Eq. (7)"}
        notes = ground_labels(content, ATT)
        self.assertEqual(content, {"section": "§3.2.1", "equation": UNGROUNDED_LABEL})
        self.assertEqual([n["field"] for n in notes], ["equation"])


class Quotes(unittest.TestCase):
    EX = _norm(CASE["excerpt"])

    def test_formatting_differences_are_tolerated(self):
        for q in ("we scale the dot products by 1/√d<sub>k</sub>", "We compute the dot products of the query with all keys, ... apply a softmax function"):
            self.assertEqual(quote_score(q, self.EX), 1.0, q)

    def test_a_changed_word_is_not_verbatim(self):
        for q in ("we do not scale the dot products by 1/√d_k", "We compute the dot products of the key with all queries"):
            self.assertEqual(quote_score(q, self.EX), 0.0, q)


if __name__ == "__main__":
    unittest.main()
