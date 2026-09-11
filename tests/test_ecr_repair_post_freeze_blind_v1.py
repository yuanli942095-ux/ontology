from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecr_repair_post_freeze_blind_v1_annotation import (  # noqa: E402
    AnnotationWorkflowError,
    generate_templates,
)
from ecr_repair_post_freeze_blind_v1_common import (  # noqa: E402
    AS_OF,
    BENCHMARK_DIR,
    DOMAINS,
    html_to_structured_text,
)
from freeze_ecr_repair_post_freeze_blind_v1 import official_oracle_ready  # noqa: E402


class CommonHelperTests(unittest.TestCase):
    def test_html_keeps_list_and_table_cells(self) -> None:
        raw = """
        <html><nav>Cookie banner</nav><body>
        <h1>Title</h1>
        <p>First paragraph.</p>
        <ul><li>MUST do one</li><li>SHOULD do two</li></ul>
        <table><tr><th>Alg</th><th>Use</th></tr><tr><td>none</td><td>no</td></tr></table>
        <script>alert(1)</script>
        </body></html>
        """
        text = html_to_structured_text(raw)
        self.assertIn("MUST do one", text)
        self.assertIn("none", text)
        self.assertNotIn("Cookie banner", text)
        self.assertNotIn("alert(1)", text)


class AnnotationTemplateTests(unittest.TestCase):
    def test_templates_are_blank_distinct_and_differently_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = Path(tmp) / "events.jsonl"
            events.write_text(
                '{"event_id":"BLIND_E001"}\n{"event_id":"BLIND_E002"}\n{"event_id":"BLIND_E003"}\n',
                encoding="utf-8",
            )
            output = Path(tmp) / "ann"
            path_a, path_b = generate_templates(events, output, "ANN_A", "ANN_B")
            with path_a.open(encoding="utf-8-sig", newline="") as handle:
                rows_a = list(csv.DictReader(handle))
            with path_b.open(encoding="utf-8-sig", newline="") as handle:
                rows_b = list(csv.DictReader(handle))
            self.assertEqual({row["event_id"] for row in rows_a}, {"BLIND_E001", "BLIND_E002", "BLIND_E003"})
            self.assertTrue(all(row["decision"] == "" for row in rows_a))
            self.assertTrue(all(row["new_lexical"] == "" for row in rows_b))
            self.assertNotEqual([row["event_id"] for row in rows_a], [row["event_id"] for row in rows_b])

    def test_same_annotator_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = Path(tmp) / "events.jsonl"
            events.write_text('{"event_id":"BLIND_E001"}\n', encoding="utf-8")
            with self.assertRaises(AnnotationWorkflowError):
                generate_templates(events, Path(tmp), "ANN_A", "ann_a")


class Tranche1ProtocolTests(unittest.TestCase):
    def test_oracle_directory_has_no_official_gold(self) -> None:
        self.assertFalse(official_oracle_ready())
        official = [
            path.name
            for path in (BENCHMARK_DIR / "private/oracle").glob("*")
            if path.is_file() and path.name.lower() != "readme.md"
        ]
        self.assertEqual(official, [])

    def test_public_schema_omits_partition(self) -> None:
        schema = json.loads(
            (BENCHMARK_DIR / "public/events/event.schema.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("partition", schema["properties"])
        self.assertEqual(schema["properties"]["as_of"]["const"], AS_OF)

    def test_sampling_protocol_locks_full_quota_and_forbids_v24(self) -> None:
        protocol = json.loads(
            (BENCHMARK_DIR / "private/construction/sampling-protocol.json").read_text(encoding="utf-8")
        )
        self.assertEqual(protocol["quotas"]["full"]["events"], 80)
        self.assertEqual(protocol["quotas"]["full"]["partitions"]["REPAIR"], 56)
        self.assertEqual(protocol["v24_execution_before_benchmark_freeze"], "PROHIBITED")
        self.assertEqual(protocol["official_oracle_before_dual_annotation"], "PROHIBITED")
        self.assertEqual(set(protocol["quotas"]["full"]["domains"]), set(DOMAINS))

    def test_roster_registers_three_distinct_human_roles(self) -> None:
        roster = json.loads(
            (BENCHMARK_DIR / "private/annotation/annotator-roster.json").read_text(encoding="utf-8")
        )
        by_role = {item["role"]: item for item in roster["annotators"]}
        self.assertEqual(set(by_role), {"ANN_A", "ANN_B", "ADJ_C"})
        self.assertTrue(all(item["status"] == "REGISTERED" for item in by_role.values()))
        public_ids = [item["public_id"] for item in by_role.values()]
        self.assertEqual(len(public_ids), len(set(public_ids)))

    def test_leakage_scan_skips_official_source_text(self) -> None:
        from scan_ecr_repair_post_freeze_blind_v1_leakage import scan

        report = scan()
        self.assertEqual(report["PUBLIC_GOLD_LEAKAGE"], "PASS")
        self.assertFalse(report["ORACLE_PRESENT"])
        self.assertTrue(
            all("documents/text" not in error and "documents/raw" not in error for error in report["errors"])
        )


if __name__ == "__main__":
    unittest.main()
