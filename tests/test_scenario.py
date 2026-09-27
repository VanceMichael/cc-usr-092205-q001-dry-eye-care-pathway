import unittest
from datetime import date

from src.access import (
    AccessDenied,
    ROLE_CLINICIAN,
    ROLE_EMPLOYER,
    SCOPE_TRANSFER_RECORD,
)
from src.scenario import build_all

LEVEL_ZH = {
    "lifestyle": "生活干预",
    "review": "安排复查",
    "medical": "眼科就医",
    "urgent": "紧急就医",
}


class ScenarioEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = build_all()

    def test_no_duplicate_records_across_organizations(self):
        registry = self.result["registry"]
        by_id = {p.person_id: p for p in registry.persons}
        wanglei = by_id[self.result["person_ids"]["wanglei"]]
        self.assertEqual(
            wanglei.organizations(), ["企业职业健康中心", "市眼科医院"]
        )
        self.assertEqual(wanglei.merge_status, "active")

        # 陈静在医院无证件令牌，先待核实，确认后才并入同一档案。
        chenjing = by_id[self.result["person_ids"]["chenjing"]]
        self.assertEqual(chenjing.merge_status, "active")
        self.assertEqual(
            chenjing.organizations(), ["企业职业健康中心", "市眼科医院"]
        )

    def test_wanglei_level_journey_uses_rules_effective_at_the_time(self):
        tl = self.result["timelines"]["wanglei"]
        pairs = [(c.date.isoformat(), c.rule_version, c.level) for c in tl.conclusions]
        # 3月企业筛查：v1 复查级
        self.assertTrue(all(d == "2026-03-10" and v == 1 and lvl == "review"
                            for d, v, lvl in pairs if d == "2026-03-10"))
        # 4月症状持续：v1 就医级
        self.assertTrue(all(v == 1 and lvl == "medical"
                            for d, v, lvl in pairs if d.startswith("2026-04")))
        # 8月20日炎症控制、新问卷降级为复查（仍是 v1）
        aug20 = [p for p in pairs if p[0] == "2026-08-20"]
        self.assertEqual(aug20[-1], ("2026-08-20", 1, "review"))
        # 9月15日事实未变但 v2 生效，手术诉求升级为就医级
        sep15 = [p for p in pairs if p[0] == "2026-09-15"]
        self.assertEqual(sep15, [("2026-09-15", 2, "medical")])
        # 9月22日激素眼药水+角膜风险：v2 紧急就医
        self.assertTrue(all(v == 2 and lvl == "urgent"
                            for d, v, lvl in pairs if d == "2026-09-22"))

    def test_old_conclusions_are_never_rewritten(self):
        tl = self.result["timelines"]["wanglei"]
        # 8月20日的降级结论始终是 v1，即使 v2 在9月生效也不被追溯改写。
        c12 = tl.conclusions[11]
        self.assertEqual(c12.date.isoformat(), "2026-08-20")
        self.assertEqual((c12.rule_version, c12.level), (1, "review"))
        # 新结论以 supersedes 接续，且全部保留。
        self.assertEqual(tl.conclusions[12].supersedes, 12)
        self.assertEqual(len(tl.conclusions), 17)
        tl.verify()

    def test_escalation_history_explains_why_symptoms_escalated(self):
        review = self.result["wanglei_review"]
        escalations = review["escalations"]
        self.assertEqual(
            [e["date"] for e in escalations],
            ["2026-04-02", "2026-09-15", "2026-09-22"],
        )
        self.assertIn("症状持续≥14天", escalations[0]["reasons"][0])
        self.assertTrue(any("近视手术" in r for r in escalations[1]["reasons"]))
        self.assertTrue(any("激素" in r for r in escalations[2]["reasons"]))

    def test_stop_self_medication_directives_have_clear_dates(self):
        review = self.result["wanglei_review"]
        directives = review["stop_self_medication"]
        dates = [(d["date"], d["level"]) for d in directives]
        self.assertEqual(
            dates, [("2026-04-02", "眼科就医"), ("2026-09-22", "紧急就医")]
        )
        self.assertIn("立即停用", directives[1]["directive"])

    def test_actions_taken_form_a_continuous_care_story(self):
        actions = self.result["wanglei_review"]["actions_taken"]
        text = "\n".join(actions)
        self.assertIn("20-20-20", text)                       # 生活干预
        self.assertIn("转诊至市眼科医院", text)               # 企业→医院转诊
        self.assertIn("指导逐步停用缩血管眼药水", text)        # 医院处理
        self.assertIn("转诊至眼科急诊", text)                 # 升级急诊
        # 同一条建议只在首次下达时出现一次。
        self.assertEqual(text.count("24-72小时内至眼科急诊"), 1)

    def test_reminders_are_individualized_not_one_size_fits_all(self):
        w_titles = [r["title"] for r in self.result["wanglei_reminders"]]
        l_titles = [r["title"] for r in self.result["linchen_reminders"]]
        self.assertTrue(any("含激素眼药水" in t for t in w_titles))
        self.assertTrue(any("近视手术前" in t for t in w_titles))
        self.assertTrue(any("紧急" in t or "就诊" in t for t in w_titles))
        # 低风险的林晨收到的是常规提醒，绝不含紧急/停用内容。
        self.assertFalse(any("停用" in t for t in l_titles))
        self.assertTrue(any("无需特殊用药" in t for t in l_titles))

    def test_clinician_access_requires_patient_consent(self):
        ac = self.result["access"]
        pid = self.result["person_ids"]["wanglei"]
        # 转诊当日本人已授权，医院医生可调阅。
        self.assertTrue(
            ac.is_authorized(
                pid, SCOPE_TRANSFER_RECORD, ROLE_CLINICIAN,
                "clinician@市眼科医院", date(2026, 4, 8),
            )
        )
        # 未获授权的其他医院不能调阅。
        self.assertFalse(
            ac.is_authorized(
                pid, SCOPE_TRANSFER_RECORD, ROLE_CLINICIAN,
                "clinician@其他医院", date(2026, 4, 8),
            )
        )

    def test_employer_is_denied_individual_even_with_consent_claim(self):
        ac = self.result["access"]
        pid = self.result["person_ids"]["wanglei"]
        with self.assertRaises(AccessDenied):
            ac.require_person_record(
                pid, ROLE_EMPLOYER, "employer@某企业", date(2026, 9, 25)
            )

    def test_employer_view_is_group_only_with_small_cell_folding(self):
        view = self.result["employer_view"]
        self.assertFalse(view["contained_individual_data"])
        names = {b["level_name"]: b["count"] for b in view["buckets"]}
        self.assertEqual(names.get("生活干预"), 10)
        self.assertEqual(names.get("安排复查"), 7)
        # 紧急仅 2 人，向上折叠为“眼科就医及以上”共 7 人，不单独披露。
        self.assertNotIn("紧急就医", names)
        self.assertEqual(names["眼科就医及以上"], 7)
        self.assertEqual(view["need_medical_or_above"], 7)


if __name__ == "__main__":
    unittest.main()
