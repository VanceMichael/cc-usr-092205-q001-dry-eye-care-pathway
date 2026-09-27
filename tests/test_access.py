import unittest
from datetime import date

from src.access import (
    MIN_CELL_SIZE,
    AccessControl,
    AccessDenied,
    ROLE_CLINICIAN,
    ROLE_EMPLOYER,
    ROLE_PATIENT,
    SCOPE_TRANSFER_RECORD,
    SCOPE_VIEW_RECORD,
    population_risk,
)


class AccessTest(unittest.TestCase):
    def setUp(self):
        self.ac = AccessControl()
        self.day = date(2026, 4, 8)

    def test_patient_can_view_own_record(self):
        self.assertTrue(
            self.ac.is_authorized("P1", SCOPE_VIEW_RECORD, ROLE_PATIENT, "P1", self.day)
        )

    def test_clinician_needs_consent(self):
        self.assertFalse(
            self.ac.is_authorized(
                "P1", SCOPE_VIEW_RECORD, ROLE_CLINICIAN, "clinician@医院", self.day
            )
        )
        self.ac.grant("P1", SCOPE_VIEW_RECORD, "clinician@医院", self.day, "转诊")
        self.assertTrue(
            self.ac.is_authorized(
                "P1", SCOPE_VIEW_RECORD, ROLE_CLINICIAN, "clinician@医院", self.day
            )
        )

    def test_consent_scoped_to_grantee_only(self):
        self.ac.grant("P1", SCOPE_TRANSFER_RECORD, "clinician@甲医院", self.day, "转诊")
        self.assertFalse(
            self.ac.is_authorized(
                "P1", SCOPE_TRANSFER_RECORD, ROLE_CLINICIAN, "clinician@乙医院", self.day
            )
        )

    def test_revoked_consent_blocks_access_but_audit_remains(self):
        self.ac.grant("P1", SCOPE_VIEW_RECORD, "clinician@医院", self.day, "转诊")
        self.ac.revoke("P1", SCOPE_VIEW_RECORD, "clinician@医院", date(2026, 5, 1))
        self.assertFalse(
            self.ac.is_authorized(
                "P1", SCOPE_VIEW_RECORD, ROLE_CLINICIAN, "clinician@医院",
                date(2026, 5, 2),
            )
        )
        # 授权与撤回记录都保留（只追加的授权审计）。
        self.assertEqual(len(self.ac.grants_audit("P1")), 1)
        self.assertIsNotNone(self.ac.grants_audit("P1")[0]["revoked_on"])

    def test_employer_never_gets_individual_record(self):
        self.ac.grant("P1", SCOPE_VIEW_RECORD, "employer@企业", self.day, "尝试授权也无效")
        with self.assertRaises(AccessDenied):
            self.ac.require_person_record(
                "P1", ROLE_EMPLOYER, "employer@企业", self.day
            )

    def test_employer_gets_only_cohort_view(self):
        # 群体风险不经过个体闸门，也不含任何个体字段。
        levels = {f"p{i}": "review" for i in range(10)}
        view = population_risk(levels, self.day)
        self.assertFalse(view["contained_individual_data"])
        self.assertEqual(view["total_screened"], 10)

    def test_small_cohort_fully_suppressed(self):
        levels = {f"p{i}": "urgent" for i in range(MIN_CELL_SIZE - 1)}
        view = population_risk(levels, self.day)
        self.assertTrue(all(b["suppressed"] for b in view["buckets"]))
        self.assertIsNone(view["need_medical_or_above"])

    def test_small_high_risk_bucket_folds_upward(self):
        """urgent 仅 2 人时折叠进 medical，且无法用相减反推出 urgent 人数。"""
        levels = {"lifestyle": 12, "review": 8, "medical": 6, "urgent": 2}
        person_levels = {
            f"{lvl}-{i}": lvl for lvl, n in levels.items() for i in range(n)
        }
        view = population_risk(person_levels, self.day)
        names = {b["level_name"]: b for b in view["buckets"]}
        self.assertNotIn("紧急就医", names)
        folded = names["眼科就医及以上"]
        self.assertEqual(folded["count"], 8)
        self.assertEqual(set(folded["levels"]), {"medical", "urgent"})
        # 汇总就医人数与折叠桶一致，相减得不到被隐匿的 2 人。
        self.assertEqual(view["need_medical_or_above"], 8)


if __name__ == "__main__":
    unittest.main()
