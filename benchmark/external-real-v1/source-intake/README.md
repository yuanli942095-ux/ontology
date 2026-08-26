# External Real V1 Source Intake

Use this folder before an item becomes a benchmark event.

The intake table records candidate source revisions and their screening status.
Rows in this folder are not benchmark events and must not be reported as
validation results.

Recommended flow:

1. Add a candidate source pair to `external-real-source-intake.csv`.
2. Mark whether redistribution/quotation is allowed.
3. Extract short source snippets into `../documents/`.
4. Fill public event, document, and candidate templates under `../input/`.
5. Run `src/update_external_real_v1_document_hashes.py --allow-empty`.
6. Run `src/validate_external_real_v1.py`.
7. Freeze public files before filling the private Oracle.
