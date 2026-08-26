# External Real V1 Collection Protocol

## Purpose

`external-real-v1` is an external validation set for versioned normative-document ontology semantic drift. It is separate from the controlled `semantic-v2` benchmark.

## Inclusion Criteria

Each event must come from a real public document revision and must satisfy all criteria:

- the old and new documents are publicly attributable;
- the relevant excerpt is sufficient to identify one ontology target property or class relation;
- the event has a clear version, exception, or cross-sentence scope decision;
- at least two plausible finite repair candidates can be generated;
- the Oracle answer is adjudicated after public inputs and candidates are frozen.

## Exclusion Criteria

Do not include:

- controlled or synthetic semantic-v2 events;
- events constructed after seeing a model failure;
- events where the answer appears in a public candidate description;
- events whose source documents cannot be redistributed or quoted as short excerpts;
- events whose Oracle requires private knowledge not present in the public source.

## Freeze Procedure

1. Fill public templates under `input/`.
2. Add source excerpts under `documents/`.
3. Add mutant OWL and candidate OWL artifacts only after the public event is stable.
4. Run `src/validate_external_real_v1.py`.
5. Commit the public benchmark state.
6. Only then fill `private/external-real-oracle-template.csv`.
7. Commit or archive a private integrity manifest separately.

## Allowed Public Inputs

Public files may include:

- event metadata;
- old/new source excerpts;
- source URLs and retrieval dates;
- target ontology labels;
- candidate formal operations;
- candidate display values.

Public files must not include:

- Oracle candidate IDs;
- correct-answer hints;
- model outputs;
- post-hoc failure notes.

## Reporting

Report this set as external validation. Do not merge it with the controlled 30-test benchmark unless the paper explicitly distinguishes controlled and external results.
