# EXT_E002 Evidence Note

Source pair:

- 2025 source PDF: `EXT_SRC_001_BEIJING_AGRI_INSURANCE_2025_TERMS.pdf`
- 2026 source PDF: `EXT_SRC_001_BEIJING_AGRI_INSURANCE_2026_TERMS.pdf`
- Publisher: 北京市农业农村局
- Retrieval date: 2026-08-26

Candidate event:

- `event_id`: `EXT_E002`
- Domain: insurance
- Semantic type: `CROSS_SENTENCE_SCOPE`
- Target subject: 小麦种植及小麦完全成本保险
- Target predicate: 倒伏程度定义

Evidence summary:

- The 2026 comparison table page 13 shows the article-level lodging hazard clause and separately says the interpretation section adds a five-level lodging definition.
- The same comparison entry enumerates five levels: no lodging, slight lodging below 30 degrees, medium lodging 30-45 degrees, relatively heavy lodging 45-60 degrees, and severe lodging above 60 degrees.
- The target ontology value therefore needs to represent the cross-sentence definition scope, not only the short article-level hazard phrase.

Status:

- This public event fixes source documents, candidate values, candidate operations, mutant OWL, and candidate OWL artifacts before private Oracle adjudication.