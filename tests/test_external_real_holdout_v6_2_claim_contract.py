import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from holdout_v6_2_claim_contract import (
    build_claim_fields,
    leakage_hits,
    strengthen_until_unique,
    unique_top_window,
    unique_property_window,
    discriminator_tokens,
)


def test_repair_claim_uniquely_ranks_gold_window() -> None:
    windows = [
        ("1", "Clients MAY ignore unknown TLS extensions."),
        ("2", "The PTO period is a loss-detection timer used after handshake confirmation."),
        ("3", "Servers MUST NOT send more than one Retry packet per UDP datagram."),
    ]
    gold = windows[1][1]
    others = [windows[0][1], windows[2][1]]
    fields = build_claim_fields(
        event_id="H6_E281",
        domain="quic_transport",
        subject_label="Microsoft Corp. normative subject",
        predicate_iri="https://example.org#pto_claim",
        rfc_title="QUIC Loss Detection and Congestion Control",
        gold_window_text=gold,
        other_window_texts=others,
        current_lexical="quic_transport_h6_e281_claim=unmodeled",
        decision="REPAIR",
        new_lexical="quic_transport_h6_e281_claim=pto_period_least_kgranularity",
    )
    fields = strengthen_until_unique(fields, windows, "2", gold, others, new_lexical="quic_transport_h6_e281_claim=pto_period_least_kgranularity")
    disc = discriminator_tokens(fields["target_property_label"])
    assert fields["target_entity_label"].startswith("QUIC Loss Detection")
    assert "evidence-grounded" not in fields["target_property_label"].lower()
    assert fields["current_value_surface"] == "No current assertion recorded."
    assert unique_top_window(disc, windows) == "2"


def test_public_target_does_not_leak_new_value() -> None:
    gold = "Signers MUST sign using rsa-sha256 and MUST NOT use rsa-sha1."
    fields = build_claim_fields(
        event_id="H6_E170",
        domain="email_security",
        subject_label="Cryptographic Algorithm and Key Usage Update to normative subject",
        predicate_iri="https://example.org#dkim_claim",
        rfc_title="Cryptographic Algorithm and Key Usage Update to DKIM",
        gold_window_text=gold,
        other_window_texts=["TLSRPT reports MAY be delivered by email."],
        current_lexical="email_security_h6_e170_claim=unmodeled",
        decision="REPAIR",
        new_lexical="email_security_h6_e170_claim=signers_sign_using_rsa-sha256_not_rsa-sha1",
    )
    new_lexical = "email_security_h6_e170_claim=signers_sign_using_rsa-sha256_not_rsa-sha1"
    public = " ".join(fields[key] for key in (
        "target_entity_label", "target_property_label", "value_neutral_question",
        "target_cq", "current_value_surface", "current_value_semantics",
    ))
    pin = " ".join(fields[key] for key in (
        "target_entity_label", "target_property_label", "value_neutral_question", "target_cq",
    ))
    assert "new_value_lexical" not in leakage_hits(public, new_lexical, gold, pin_text=pin)
    assert "rfc2119_in_target" not in leakage_hits(public, new_lexical, gold, pin_text=pin)
    assert "rsa-sha256" not in fields["target_property_label"].lower()
    assert "MUST sign using rsa-sha256" not in public


def test_phrase_pin_breaks_bag_of_words_tie() -> None:
    windows = [
        ("1", "o For cases not defined in +cbor-seq, process as specified in xxx/yyy+cbor-seq."),
        ("3", "o For cases defined in +cbor-seq, if the fragment identifier does not resolve, process as specified in xxx/yyy+cbor-seq."),
    ]
    assert unique_property_window("cases not defined in +cbor-seq", windows) == "1"


def test_title_window_does_not_leak_when_entity_is_shortened() -> None:
    gold = "Cryptographic Algorithm and Key Usage Update to DomainKeys Identified Mail (DKIM)"
    entity = "DomainKeys Identified Mail (DKIM)"
    prop = "Key Usage Update document title"
    question = f"What is the current {prop} for {entity}?"
    public = "\n".join([entity, prop, question])
    new_lexical = "email_security_h6_e169_claim=cryptographic_algorithm_key_usage_update_domainkeys_identified_mail_dkim"
    assert gold.lower() not in public.lower()
    assert "full_gold_window" not in leakage_hits(public, new_lexical, gold, pin_text=public)
    assert "new_value_lexical" not in leakage_hits(public, new_lexical, gold, pin_text=public)
