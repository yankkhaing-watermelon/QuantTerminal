import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "publish.py"
spec = importlib.util.spec_from_file_location("quant_publish", MODULE_PATH)
publish = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(publish)


class Step13PublisherTests(unittest.TestCase):
    def test_content_addressed_run_id_is_order_independent(self):
        payload = {"scan_date": "2026-08-28"}
        left = [{"symbol": "B", "score": 2.0}, {"symbol": "A", "score": 1.0}]
        right = [{"score": 1.0, "symbol": "A"}, {"score": 2.0, "symbol": "B"}]
        self.assertEqual(
            publish.content_addressed_run_id(payload, left),
            publish.content_addressed_run_id(payload, right),
        )

    def test_content_change_changes_run_id(self):
        payload = {"scan_date": "2026-08-28"}
        before = [{"symbol": "A", "score": 1.0}]
        after = [{"symbol": "A", "score": 1.1}]
        self.assertNotEqual(
            publish.content_addressed_run_id(payload, before),
            publish.content_addressed_run_id(payload, after),
        )

    def test_run_id_keeps_scan_date(self):
        run_id = publish.content_addressed_run_id({"scan_date": "2026-08-28"}, [{"symbol": "A"}])
        self.assertRegex(run_id, r"^qv5-2026-08-28-[0-9a-f]{12}$")


if __name__ == "__main__":
    unittest.main()
