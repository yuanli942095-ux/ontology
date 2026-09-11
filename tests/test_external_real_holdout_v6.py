from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from build_external_real_holdout_v6 import (  # noqa: E402
    PUBLIC_EVENT_FIELDS,
    assert_seed_profile,
    candidate_mapping,
    expand_full_seed,
    historical_text_collision,
    ontology_terms,
    public_event,
    rdf_xml,
)
from freeze_external_real_holdout_v6 import main as freeze_main  # noqa: E402
from rfc213_direct_repair_ir import benchmark_literal_from_span  # noqa: E402


PILOT_PROFILE = {
    "events": 30,
    "repair": {
        "TEMPORAL_VERSION": 8,
        "GENERAL_RULE_EXCEPTION": 8,
        "CROSS_SENTENCE_SCOPE": 8,
    },
    "safety": {
        "NO_CHANGE": 2,
        "INSUFFICIENT_EVIDENCE": 2,
        "CONFLICTING_EVIDENCE": 2,
    },
    "domains": 10,
    "source_families_per_domain": 3,
    "max_events_per_source_family": 3,
}

FULL_PROFILE = {
    "events": 300,
    "repair": {
        "TEMPORAL_VERSION": 80,
        "GENERAL_RULE_EXCEPTION": 80,
        "CROSS_SENTENCE_SCOPE": 80,
    },
    "safety": {
        "NO_CHANGE": 20,
        "INSUFFICIENT_EVIDENCE": 20,
        "CONFLICTING_EVIDENCE": 20,
    },
    "domains_minimum": 10,
    "source_families_per_domain_minimum": 3,
    "max_source_family_fraction": 0.1,
}


def load_pilot_seed() -> list[dict]:
    path = Path(__file__).resolve().parents[1] / (
        "benchmark/external-real-holdout-v6-direct-ir-blind/private/construction/source-registry-seed.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


class HoldoutV6ProtocolTests(unittest.TestCase):
    def test_pilot_seed_matches_registered_quota(self) -> None:
        assert_seed_profile(load_pilot_seed(), PILOT_PROFILE)

    def test_full_expansion_keeps_pilot_and_hits_300_quota(self) -> None:
        expanded = expand_full_seed(load_pilot_seed())
        self.assertEqual(len(expanded), 300)
        self.assertEqual({row["event_id"] for row in expanded[:30]}, {row["event_id"] for row in load_pilot_seed()})
        assert_seed_profile(expanded, FULL_PROFILE)
        families = {}
        for row in expanded:
            families[f"IETF_RFC_{row['rfc']}"] = families.get(f"IETF_RFC_{row['rfc']}", 0) + 1
        self.assertTrue(all(count <= 30 for count in families.values()))

    def test_gold_candidate_ids_are_balanced_for_24_and_240(self) -> None:
        pilot = [candidate_mapping(index)["gold"] for index in range(24)]
        full = [candidate_mapping(index)["gold"] for index in range(240)]
        self.assertEqual(pilot.count("CAND_001"), 8)
        self.assertEqual(pilot.count("CAND_002"), 8)
        self.assertEqual(pilot.count("CAND_003"), 8)
        self.assertEqual(full.count("CAND_001"), 80)
        self.assertEqual(full.count("CAND_002"), 80)
        self.assertEqual(full.count("CAND_003"), 80)

    def test_public_event_omits_gold_and_uses_safety_labels(self) -> None:
        terms = ontology_terms("H6_E025", "network_routing", "IPv6")
        event = public_event(
            {
                "event_id": "H6_E025",
                "semantic_type": "SAFETY_CONTROL",
                "domain": "network_routing",
                "decision": "NO_CHANGE",
                "title": "IPv6",
            },
            terms,
            ["V6_IETF_RFC8200"],
        )
        self.assertEqual(set(event), set(PUBLIC_EVENT_FIELDS))
        self.assertEqual(event["semantic_type"], "NO_CHANGE")
        self.assertEqual(event["status"], "DRAFT_AWAITING_DUAL_ANNOTATION")
        dumped = json.dumps(event)
        self.assertNotIn("gold", dumped.casefold())
        self.assertNotIn("oracle", dumped.casefold())
        self.assertNotIn("CAND_", dumped)

    def test_v2_literal_contract_uses_dimension_and_nine_tokens(self) -> None:
        old = "transport_security_h6_e004_claim=unmodeled"
        window = "Implementations MUST treat the TLS version as 1.2 when the record layer is used."
        lexical = benchmark_literal_from_span(old, window)
        self.assertTrue(lexical.startswith("transport_security_h6_e004_claim="))
        self.assertEqual(lexical.split("=", 1)[1], "implementations_treat_tls_version_record_layer_used")

    def test_historical_containment_and_fingerprint(self) -> None:
        old = "A validating resolver MUST set the DO bit before sending the query."
        texts = {old.casefold()}
        hashes = {__import__("hashlib").sha256(old.casefold().encode()).hexdigest()}
        from build_external_real_holdout_v6 import normalized_fingerprint

        self.assertTrue(historical_text_collision(old, texts, {normalized_fingerprint(old)}))
        self.assertFalse(historical_text_collision("Completely novel QUIC packet number space rule.", texts, hashes))

    def test_mutant_owl_has_unique_old_literal_and_sentinel(self) -> None:
        from rdflib import Graph

        terms = ontology_terms("H6_E001", "dns_security", "Trust Anchor Update")
        xml = rdf_xml(
            event_id="H6_E001",
            terms=terms,
            lexical="dns_security_h6_e001_claim=unmodeled",
            ontology_suffix="mutant",
        )
        graph = Graph()
        graph.parse(data=xml, format="xml")
        literals = [str(value) for _, _, value in graph if getattr(value, "datatype", None)]
        self.assertEqual(literals.count("dns_security_h6_e001_claim=unmodeled"), 1)
        self.assertIn("preserve", literals)

    def test_freeze_refuses_without_human_annotation(self) -> None:
        with mock.patch(
            "freeze_external_real_holdout_v6.validate",
            return_value={"freeze_ready": False, "status": "DRAFT_AWAITING_DUAL_ANNOTATION"},
        ):
            with self.assertRaises(SystemExit):
                freeze_main(["--profile", "pilot"])


if __name__ == "__main__":
    unittest.main()
