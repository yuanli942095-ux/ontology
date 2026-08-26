# EXT_E001 Evidence Note

Source pair:

- 2025 source PDF: `EXT_SRC_001_BEIJING_AGRI_INSURANCE_2025_TERMS.pdf`
- 2026 source PDF: `EXT_SRC_001_BEIJING_AGRI_INSURANCE_2026_TERMS.pdf`
- Publisher: 北京市农业农村局
- Retrieval date: 2026-08-26

Candidate event:

- `event_id`: `EXT_E001`
- Domain: insurance
- Semantic type: `TEMPORAL_VERSION`
- Target subject: 叶类、根茎类蔬菜、茄果类及其他类蔬菜轮作
- Target predicate: 每亩保险金额分项

Evidence summary:

- 2025 rate table page 7 lists the relevant rotation category with total insurance amount `2000` and no separate sub-amount for spring versus summer/autumn planting.
- 2025 article page 121 states that the policyholder may insure spring open-field vegetables, summer/autumn open-field vegetables, and rotation open-field vegetables, or separately insure spring or summer/autumn open-field vegetables.
- 2026 comparison table page 13 changes the rotation formulation: the total per-mu amount remains `2000`, but the amount is split into spring `1100` and summer/autumn `900`.
- 2026 article page 119 repeats the operational table with spring `1100` and summer/autumn `900` for the same rotation category.

Public candidate values:

- `CAND_001`: `total_only=2000`
- `CAND_002`: `spring=1100;summer_autumn=900;total=2000`
- `CAND_003`: `spring=1000;summer_autumn=1000;total=2000`

Status:

- This public event fixes source documents, candidate values, candidate operations, mutant OWL, and candidate OWL artifacts before private Oracle adjudication.
