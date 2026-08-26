# Formal-Policy Annotation-Time Pilot Protocol

## Goal

This pilot measures the human effort required to structure formal policies for semantic-v2 events. It complements the existing `policy_complexity_points` proxy and must not be reported as a calibrated cost model.

## Scope

The default pilot contains 12 test events, four per semantic type:

| Semantic Type | Events |
|---|---|
| `TEMPORAL_VERSION` | E14, E17, E21, E42 |
| `GENERAL_RULE_EXCEPTION` | E27, E30, E31, E45 |
| `CROSS_SENTENCE_SCOPE` | E34, E37, E41, E47 |

These events include the main failure-focused cases while preserving balanced type coverage.

## Materials Allowed During Annotation

Annotators may use only public benchmark materials:

- old/new document excerpts referenced by the event;
- event `case_context` and target metadata;
- candidate formal operations;
- candidate display values if needed to bind rule outputs;
- the public ontology/mutant OWL needed to understand the target relation.

Annotators must not use:

- private Oracle files or Oracle candidate IDs;
- previous model outputs, failure tables, or per-run predictions;
- artificial candidate descriptions;
- the already completed event formal-policy file, unless the task is explicitly a timed review rather than from-scratch structuring.

## Timing Rule

Start the timer when the annotator first opens the event materials. Stop the timer when the annotator produces a formal-policy file that they believe is executable.

Include time spent on:

- reading source documents;
- identifying facts;
- normalizing fact values into the controlled symbolic vocabulary;
- writing prioritized rules and conditions;
- running the public hard-gate validator;
- revising the policy after validator failures.

Exclude time spent on:

- installing software;
- unrelated interruptions;
- looking at Oracle labels;
- discussing the answer with someone who has seen Oracle or model outputs.

## Required Record

Fill one CSV row per annotator/event attempt:

- `annotator_id`;
- `start_time_iso`;
- `end_time_iso`;
- `elapsed_minutes`;
- `unique_gate_decision`;
- `selected_candidate_id_after_gate`;
- `revision_count`;
- any `blocking_issue` or `notes`.

After annotation is complete, an experiment administrator may perform a blind check against the private Oracle and fill `oracle_correct_after_blind_check`. This check must happen after the timer has stopped.

## Reporting Rule

Report these measurements as a pilot study, not a definitive cost model. The current complexity formula remains a proxy:

```text
policy_complexity_points = 8 + 3*facts + 5*rules + 2*conditions + 4*source_docs
```

The pilot should be used to report observed annotation minutes, revision counts, gate uniqueness, and whether measured minutes correlate with the proxy.
