import unittest
from datetime import date

from src.rules import RiskInputs, RuleBook
from pathlib import Path


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.book = RuleBook.from_path(Path("fixtures/rules.json"))

    def test_two_versions_with_non_overlapping_effective_windows(self):
        self.assertEqual(self.book.versions(), [1, 2])
        self.assertEqual(self.book.effective_set(date(2026, 8, 31)).version, 1)
        self.assertEqual(self.book.effective_set(date(2026, 9, 1)).version, 2)

    def test_lifestyle_default(self):
        result = self.book.stratify(
            RiskInputs(date(2026, 3, 1), {"osdi": 4, "screen_hours": 4})
        )
        self.assertEqual(result.level, "lifestyle")
        self.assertEqual(result.rule_version, 1)

    def test_review_when_osdi_and_screen_hours_v1(self):
        result = self.book.stratify(
            RiskInputs(date(2026, 3, 10), {"osdi": 18, "screen_hours": 9})
        )
        self.assertEqual(result.level, "review")
        self.assertEqual(result.rule_version, 1)
        self.assertTrue(result.reasons)

    def test_medical_when_symptom_persists_two_weeks(self):
        result = self.book.stratify(
            RiskInputs(date(2026, 4, 2), {"symptom_duration_days": 24})
        )
        self.assertEqual(result.level, "medical")
        self.assertIn("异物感", result.reasons[0])

    def test_urgent_when_corneal_risk(self):
        result = self.book.stratify(
            RiskInputs(date(2026, 9, 22), {"doctor_corneal_risk": True})
        )
        self.assertEqual(result.level, "urgent")
        self.assertTrue(
            any("角膜" in r for r in result.reasons)
        )

    def test_urgent_when_steroid_self_medicated(self):
        result = self.book.stratify(
            RiskInputs(date(2026, 9, 22), {"steroid_drops_without_prescription": True})
        )
        self.assertEqual(result.level, "urgent")
        self.assertTrue(any("激素" in r for r in result.reasons))
        self.assertTrue(any("停止" in a for a in result.actions))

    def test_surgery_intention_is_review_under_v1_but_medical_under_v2(self):
        values = {"planning_refractive_surgery": True}
        v1 = self.book.stratify(RiskInputs(date(2026, 8, 31), values))
        v2 = self.book.stratify(RiskInputs(date(2026, 9, 1), values))
        self.assertEqual(v1.level, "review")
        self.assertEqual(v2.level, "medical")

    def test_vasoconstrictor_is_review_under_v1_but_medical_under_v2(self):
        values = {"vasoconstrictor_drops": True}
        v1 = self.book.stratify(RiskInputs(date(2026, 8, 31), values))
        v2 = self.book.stratify(RiskInputs(date(2026, 9, 1), values))
        self.assertEqual(v1.level, "review")
        self.assertEqual(v2.level, "medical")

    def test_screen_hours_threshold_lowered_in_v2(self):
        values = {"screen_hours": 7}
        self.assertEqual(
            self.book.stratify(RiskInputs(date(2026, 8, 31), values)).level,
            "lifestyle",
        )
        self.assertEqual(
            self.book.stratify(RiskInputs(date(2026, 9, 1), values)).level,
            "review",
        )

    def test_but_threshold_relaxed_in_v2(self):
        # BUT=6：v1 为就医级（≤5才就医？6不满足），v2 为就医级（≤6）。
        values = {"but_seconds": 6}
        self.assertEqual(
            self.book.stratify(RiskInputs(date(2026, 8, 31), values)).level,
            "review",
        )
        self.assertEqual(
            self.book.stratify(RiskInputs(date(2026, 9, 1), values)).level,
            "medical",
        )

    def test_no_rule_set_before_first_version(self):
        with self.assertRaises(LookupError):
            self.book.effective_set(date(2024, 1, 1))


if __name__ == "__main__":
    unittest.main()
