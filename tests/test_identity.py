import unittest
from datetime import date

from src.identity import Alias, IdentityRegistry


def _alias(org, local_id, token="", name="王磊", phone="138-0000-1024"):
    return Alias(
        organization=org,
        local_id=local_id,
        id_token=token,
        name=name,
        birth_date=date(1992, 5, 14),
        gender="男",
        phone=phone,
        established=date(2026, 3, 8),
    )


class IdentityTest(unittest.TestCase):
    def test_new_person_created(self):
        reg = IdentityRegistry()
        person, mode = reg.register(_alias("企业职业健康中心", "OCC-1", token="T1"))
        self.assertEqual(mode, "created")
        self.assertEqual(person.person_id, "PERSON-0001")

    def test_same_id_token_links_without_duplicate_record(self):
        """企业筛查转医院：同一证件令牌自动挂接，不重复建档。"""
        reg = IdentityRegistry()
        first, _ = reg.register(_alias("企业职业健康中心", "OCC-1", token="T1"))
        second, mode = reg.register(_alias("市眼科医院", "EH-9", token="T1"))
        self.assertEqual(mode, "linked_token")
        self.assertEqual(first.person_id, second.person_id)
        self.assertEqual(len(reg.persons), 1)
        self.assertEqual(
            second.organizations(), ["企业职业健康中心", "市眼科医院"]
        )

    def test_demographic_match_is_pending_until_staff_confirms(self):
        """无证件令牌但人口学信息一致：先挂起，核实后才正式合并。"""
        reg = IdentityRegistry()
        reg.register(_alias("企业职业健康中心", "OCC-1", token="T1"))
        pending, mode = reg.register(_alias("市眼科医院", "EH-9", token=""))
        self.assertEqual(mode, "pending_demographic")
        self.assertEqual(pending.merge_status, "pending_verification")
        self.assertEqual(len(reg.persons), 1)

        confirmed = reg.confirm_merge(pending.person_id, "T1")
        self.assertEqual(confirmed.merge_status, "active")
        self.assertEqual(
            confirmed.organizations(), ["企业职业健康中心", "市眼科医院"]
        )

    def test_different_people_are_not_merged(self):
        reg = IdentityRegistry()
        reg.register(_alias("企业职业健康中心", "OCC-1", token="T1"))
        other, mode = reg.register(
            _alias("市眼科医院", "EH-9", token="T2", name="李明",
                   phone="137-0000-0000")
        )
        self.assertEqual(mode, "created")
        self.assertEqual(len(reg.persons), 2)

    def test_cannot_confirm_merge_to_a_token_owned_by_another_person(self):
        reg = IdentityRegistry()
        reg.register(_alias("企业职业健康中心", "OCC-1", token="T1"))
        other, _ = reg.register(
            Alias(
                organization="另一企业",
                local_id="OCC-2",
                id_token="T9",
                name="李明",
                birth_date=date(1985, 1, 1),
                gender="男",
                phone="135-0000-0000",
            )
        )
        pending, _ = reg.register(_alias("市眼科医院", "EH-9", token=""))
        with self.assertRaises(ValueError):
            reg.confirm_merge(pending.person_id, "T9")


if __name__ == "__main__":
    unittest.main()
