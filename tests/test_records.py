import unittest
from datetime import date

from src.records import CareTimeline, TimelineIntegrityError
from src.rules import RiskInputs, RuleBook
from pathlib import Path

RULES = Path("fixtures/rules.json")


class TimelineTest(unittest.TestCase):
    def setUp(self):
        self.book = RuleBook.from_path(RULES)

    def _timeline(self) -> CareTimeline:
        tl = CareTimeline("PERSON-TEST")
        tl.append_event(
            date(2026, 3, 10),
            "screening_questionnaire",
            "技师",
            {"osdi": 18, "symptom_duration_days": 10, "vision_fluctuation": True},
        )
        tl.append_event(
            date(2026, 3, 10),
            "medication_change",
            "患者",
            {"self_medication": True, "vasoconstrictor_drops": False},
        )
        return tl

    def test_fact_snapshot_accumulates_groups(self):
        tl = self._timeline()
        snap = tl.fact_snapshot(date(2026, 3, 10))
        self.assertEqual(snap["osdi"], 18)
        self.assertTrue(snap["self_medication"])

    def test_same_group_new_report_replaces_old_fields(self):
        """新问卷是完整新报告：旧问卷里有、新问卷里没有的症状不再残留。"""
        tl = self._timeline()
        tl.append_event(
            date(2026, 8, 20),
            "screening_questionnaire",
            "技师",
            {"osdi": 8, "symptom_duration_days": 2},
        )
        snap = tl.fact_snapshot(date(2026, 8, 20))
        self.assertEqual(snap["osdi"], 8)
        self.assertNotIn("vision_fluctuation", snap)
        # 另一事实组（用药）不受影响。
        self.assertTrue(snap["self_medication"])

    def test_snapshot_as_of_ignores_later_events(self):
        tl = self._timeline()
        tl.append_event(date(2026, 4, 2), "patient_update", "患者",
                        {"symptom_duration_days": 24})
        snap = tl.fact_snapshot(date(2026, 3, 31))
        self.assertEqual(snap["symptom_duration_days"], 10)

    def test_hash_chain_verifies_clean(self):
        tl = self._timeline()
        strat = self.book.stratify(
            RiskInputs(date(2026, 3, 10), tl.fact_snapshot(date(2026, 3, 10)))
        )
        tl.append_conclusion(date(2026, 3, 10), 1, strat.as_dict())
        tl.verify()  # 不抛异常即通过

    def test_tampering_an_old_event_breaks_chain(self):
        tl = self._timeline()
        # 旧结论不可改写：直接篡改底层载荷必须被发现。
        event = tl.events[0]
        object.__setattr__(event, "payload", {"osdi": 99})
        with self.assertRaises(TimelineIntegrityError):
            tl.verify()

    def test_tampering_a_conclusion_breaks_chain(self):
        tl = self._timeline()
        strat = self.book.stratify(
            RiskInputs(date(2026, 3, 10), tl.fact_snapshot(date(2026, 3, 10)))
        )
        c = tl.append_conclusion(date(2026, 3, 10), 1, strat.as_dict())
        object.__setattr__(c, "level", "urgent")
        with self.assertRaises(TimelineIntegrityError):
            tl.verify()

    def test_conclusion_requires_existing_event(self):
        tl = CareTimeline("PERSON-TEST")
        with self.assertRaises(ValueError):
            tl.append_conclusion(date(2026, 3, 10), 99, {"rule_version": 1,
                            "level": "review", "matched_rule_id": "x",
                            "reasons": [], "actions": []})

    def test_escalation_history_compares_with_previous_conclusion(self):
        """好转后再次恶化也要出现在升级史中。"""
        tl = CareTimeline("PERSON-TEST")
        levels = ["review", "medical", "review", "medical", "urgent"]
        for i, level in enumerate(levels, start=1):
            tl.append_event(date(2026, 3, 1 + i), "patient_update", "患者", {})
            self._append(tl, i, level)
        names = [c.level for c in tl.escalation_history()]
        self.assertEqual(names, ["medical", "medical", "urgent"])

    @staticmethod
    def _append(tl: CareTimeline, seq: int, level: str) -> None:
        from src.records import Conclusion, digest
        prev = tl.conclusions[-1].event_hash if tl.conclusions else ""
        c = Conclusion(
            seq=len(tl.conclusions) + 1,
            date=date(2026, 3, 1 + seq),
            event_ref=seq,
            rule_version=1,
            level=level,
            matched_rule_id="x",
            reasons=[],
            actions=[],
            prev_hash=prev,
        )
        h = digest(["PERSON-TEST", "conclusion", c.seq, c.date.isoformat(),
                    c.event_ref, c.rule_version, c.level, c.matched_rule_id,
                    c.matched_rule_name, c.reasons, c.actions, c.note,
                    c.supersedes, prev])
        object.__setattr__(c, "event_hash", h)
        tl.conclusions.append(c)

    def test_unknown_event_type_rejected(self):
        tl = CareTimeline("PERSON-TEST")
        with self.assertRaises(ValueError):
            tl.append_event(date(2026, 3, 10), "not_a_real_event", "患者", {})


if __name__ == "__main__":
    unittest.main()
