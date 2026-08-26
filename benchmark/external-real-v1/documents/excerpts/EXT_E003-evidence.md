# EXT_E003 Evidence Note

Source pair:

- 2025 source PDF: `EXT_SRC_001_BEIJING_AGRI_INSURANCE_2025_TERMS.pdf`
- 2026 source PDF: `EXT_SRC_001_BEIJING_AGRI_INSURANCE_2026_TERMS.pdf`
- Publisher: 北京市农业农村局
- Retrieval date: 2026-08-26

Candidate event:

- `event_id`: `EXT_E003`
- Domain: insurance
- Semantic type: `GENERAL_RULE_EXCEPTION`
- Target subject: 梨种植保险
- Target predicate: 冻害损失赔偿公式

Evidence summary:

- The 2026 comparison table page 13 includes the梨种植保险赔偿处理 item for freeze damage.
- The old text uses a formula based on effective insured amount and the portion above a 50 percent loss threshold.
- The 2026 text changes the calculation to the accident-date per-mu compensation limit multiplied by the loss rate.

Public candidate values:

- `CAND_001`: `formula=effective_amount_times_loss_minus_50_over_50`
- `CAND_002`: `formula=accident_date_limit_times_loss_rate`
- `CAND_003`: `formula=effective_amount_times_loss_rate`

Status:

- This public event fixes source documents, candidate values, candidate operations, mutant OWL, and candidate OWL artifacts before private Oracle adjudication.
