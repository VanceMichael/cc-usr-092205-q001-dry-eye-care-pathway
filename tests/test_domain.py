import unittest
from pathlib import Path
from src.domain import load_domain

class DomainTest(unittest.TestCase):
    def test_fixture_matches_domain(self):
        value = load_domain(Path("fixtures/domain.json"))
        self.assertEqual(value["domain"], "dry-eye-care-pathway")
        self.assertGreaterEqual(len(value["constraints"]), 2)

    def test_fixture_records_care_guarantees(self):
        value = load_domain(Path("fixtures/domain.json"))
        constraints = "；".join(value["constraints"])
        self.assertIn("只追加不改写", constraints)
        self.assertIn("本人授权", constraints)
        self.assertIn("去重", constraints)

if __name__ == "__main__":
    unittest.main()
