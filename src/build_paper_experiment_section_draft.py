from __future__ import annotations

"""Generate a paper-style experiment section draft from current artifacts."""

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(r"G:\LearnAI\ontology-evolution")
OUT = ROOT / "output"
PREFIX = "paper-experiment-section-draft"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def extract_table(markdown: str, title: str) -> str:
    lines = markdown.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == title:
            start = index + 1
            break
    if start is None:
        return ""
    table: list[str] = []
    for line in lines[start:]:
        if line.startswith("|"):
            table.append(line)
        elif table:
            break
    return "\n".join(table)


def main() -> int:
    main_table = read(OUT / "paper-experiment-main-table.md")
    failure_analysis = read(OUT / "auto-policy-v3-failure-case-analysis.md")
    method_diagrams = read(OUT / "method-flow-and-input-isolation-diagrams.md")
    reproducibility = read(OUT / "reproducibility-package.md")

    headline_table = extract_table(main_table, "# Paper Experiment Main Table")
    failure_type_table = extract_table(failure_analysis, "## Summary By Source And Semantic Type")
    negative_variant_table = extract_table(failure_analysis, "## Negative Safety Unsafe Selections By Variant")

    md = f"""# Paper Experiment Section Draft

This draft is written as a paper-facing experiment section. It is intentionally conservative: it separates candidate selection from executable OWL repair closure, treats manual formal policy as an upper-bound condition, and keeps the external-validity limits visible.

## 5. Experimental Setup

### 5.1 Benchmark

We evaluate on `external-real-v1`, a 30-event structured public-source validation benchmark for ontology semantic drift induced by versioned normative documents. The benchmark is separate from the controlled `semantic-v2` diagnostic benchmark. It contains three balanced semantic types: `TEMPORAL_VERSION`, `GENERAL_RULE_EXCEPTION`, and `CROSS_SENTENCE_SCOPE`, with 10 events per type. The public-source events are derived from three source families: Beijing agricultural-insurance policy materials, W3C WCAG 2.1/2.2 public standard/change-summary pages, and NIST SP 800-63-3/800-63-4 public guideline pages.

Each event contains public event metadata, public source evidence, a mutant OWL file, three finite repair candidates, candidate OWL artifacts, and a private Oracle row. Public files and generated artifacts are validated before evaluation. The private Oracle is loaded only after model outputs, candidate selections, gate decisions, and repair-closure rows are fixed. We do not position `external-real-v1` as a natural-document-understanding benchmark: its evidence notes and event metadata are deliberately structured enough to audit candidate-blind semantic normalization and executable repair closure.

### 5.2 Compared Methods

We compare three groups of methods.

First, candidate-information baselines measure how much information the model receives at selection time. `DIRECT_FREE` generates an answer directly from public evidence without seeing the candidate list. `OPTION_VALUE_ONLY` sees candidate option IDs and display values. `OPTION_FORMAL_OPERATION` sees candidate option IDs and formal repair operations. `OPTION_FORMAL_POLICY` additionally sees manually structured policy facts and prioritized rules. `OPTION_FORMAL_POLICY_HARD_GATE` executes those manual facts/rules symbolically and is treated as a policy-available upper bound, not as a fully automatic method.

Second, the automatic policy-construction line removes candidate and Oracle access from generation. `AUTO_POLICY_V2` asks Qwen to produce a free-form semantic result from public evidence only. `AUTO_POLICY_V3` instead asks for a structured canonical result and then applies a deterministic normalizer to map it into the controlled semantic vocabulary required by finite repair selection. The framework is domain-general in its stages, but the canonical vocabulary and normalizer are domain-specific resources. Porting to a new document family requires defining or inducing the corresponding canonical schema.

Third, `AUTO_POLICY_V3_PLUS_GATE` adds a lightweight uncertainty gate before candidate values are exposed. The gate does not call Qwen and remains candidate-blind. It checks generated status, canonical output, and evidence uncertainty markers; if uncertainty is detected, it abstains before candidate selection.

### 5.3 Metrics

We report call-level Oracle selection accuracy, strict event success, OWL repair closure, negative-safety rates, runtime/tokens, and policy construction cost. OWL repair closure requires that the selected candidate artifact is loaded, the reasoner returns a consistent ontology, the selected repair operation is reflected in RDF triples, and repair CQs pass. For proportions we report Wilson 95% confidence intervals where available. For paired comparisons we use exact McNemar/binomial tests on matched event-run pairs.

### 5.4 Input Isolation

Figure 1 in `output/method-flow-and-input-isolation-diagrams.md` summarizes the Auto Policy V3 pipeline. Figure 2 records the input-isolation boundary: Qwen generation receives public documents, evidence notes, event metadata, and target schema only. It does not receive candidate IDs, candidate values, candidate operations, manually written formal-policy files, or Oracle labels. Candidate operations become visible only after the semantic result is fixed, and the private Oracle is used only for offline metrics. Figure 3 distinguishes the manual-policy upper-bound line from the automatic V3 line.

## 6. Results

### 6.1 Main Results

The main result is summarized in Table 1.

{headline_table}

The direct baseline reaches 91.33% selection accuracy on the external-real-v1 benchmark. Providing only candidate values raises selection accuracy to 99.33%, showing that visible answer values are strong task guidance. The policy-available LLM and symbolic hard-gate conditions both reach 100.00%, but they require manually structured policy information and therefore should be interpreted as upper-bound conditions rather than fully automatic methods.

The automatic candidate-blind line shows the central result. `AUTO_POLICY_V2` reaches 82.67% repair closure, whereas `AUTO_POLICY_V3` reaches 100.00% repair closure and 30/30 strict event success. The paired test between V2 and V3 has 26 discordant matched event-run pairs, all favoring V3, with p=2.98e-08. This supports the claim that controlled canonical output plus deterministic normalization materially improves candidate-blind repair closure on the current benchmark.

Adding the uncertainty gate preserves 100.00% normal-set selection accuracy and V3 repair closure while improving negative safe abstain to 98.67%. The paired test against ungated negative safety has 62 discordant pairs, all favoring the gate, with p=8.47e-16.

### 6.2 Component Ablation

The component ablation separates three effects. `AUTO_POLICY_V2` uses Qwen but emits a free-form semantic result and reaches 82.67% repair closure. `V3_NO_NORMALIZER` uses the canonical prompt but evaluates the model's own emitted semantic string without deterministic normalization, dropping repair closure to 36.67%. `AUTO_POLICY_V3` combines canonical output with deterministic normalization and reaches 100.00%.

The metadata/evidence-only normalizer also reaches 100.00% on the current evidence notes without Qwen. This is an important validity boundary: the current external-real-v1 evidence notes are highly structured enough that deterministic normalization can solve the benchmark. Therefore the safe claim is not that Qwen alone understands arbitrary normative documents, but that the V3 pipeline provides a candidate-blind canonicalization-and-repair mechanism that closes a structured public-source validation benchmark under the current canonical schemas.

### 6.3 Robustness

V3 remains at 100.00% repair closure when evidence lines are shuffled and when evidence is rewritten into a less templated paragraph without distractors. However, under metadata-light near-miss distractor evidence, repair closure drops to 61.11% with Wilson 95% CI 50.78%-70.53%. This indicates that V3 is robust to formatting perturbation but not to adversarial or near-miss evidence contamination.

### 6.4 Safety Analysis

Ungated V3 is not a fail-closed safety mechanism. In the negative-safety experiment, overall safe abstain is 57.33% and unsafe selection is 42.67%. The most severe variants are `DISTRACTOR_DOMINATES` and `CONFLICTING_EVIDENCE`.

{negative_variant_table}

The conflict/uncertainty gate substantially improves this behavior. After gating, negative safe abstain reaches 148/150 = 98.67% with Wilson 95% CI 95.27%-99.63%, while the normal set remains at 150/150. The gate does not prove general safety under arbitrary natural conflicts, but it demonstrates that a candidate-blind uncertainty layer can prevent most unsafe selections in the tested negative conditions.

### 6.5 Failure Cases

The failure analysis shows that distractor robustness failures are concentrated in WCAG temporal-version and cross-sentence-scope events.

{failure_type_table}

The residual unsafe selections after applying the gate are limited to two `EXT_E003` formula cases under `MISSING_KEY_FIELD` and `NO_MATCHING_CANDIDATE`. In both cases, the negative mutation retained a normal-looking formula signal, and the candidate-blind gate had no observable missing/conflict/distractor marker. These residual cases should be discussed as a limitation of the negative-case construction and of marker-based uncertainty gating.

## 7. Threats To Validity

### 7.1 Manual Policy Upper Bound

The policy-available methods consume manually structured facts and rules. Their 100.00% accuracy is useful as an upper-bound comparison, but it must not be presented as a fully automatic result. The reported policy cost is a heuristic complexity score unless replaced by measured annotation time.

### 7.2 Benchmark External Validity

The current 30-event external-real-v1 benchmark is a structured public-source validation benchmark, not a large independently annotated real-world corpus or a natural-document-understanding benchmark. The W3C/NIST rows are script-formalized from official public change-summary materials. The metadata/evidence-only normalizer reaching 100.00% confirms that the evidence notes are too templated to isolate Qwen's document-understanding contribution. Broader claims require additional independently annotated real revision events, less templated free-text evidence, and adversarial near-miss examples not generated from the same templates.

### 7.3 Canonical Schema Generalization

V3 depends on a predefined canonical vocabulary and a deterministic normalizer. The pipeline stages are reusable, but the canonical schema is not automatically learned and may not transfer unchanged to another regulatory domain. A stronger future version should study semi-automatic canonical vocabulary construction from document families, rule-template induction, and cross-domain reuse of schema fragments.

### 7.4 Qwen Contribution

Because the metadata/evidence-only normalizer also reaches 100.00% on the current benchmark, the present results do not isolate a strong standalone Qwen document-understanding contribution. The strongest supported contribution is the candidate-blind canonicalization, deterministic normalization, uncertainty gating, and executable OWL repair-closure pipeline.

### 7.5 Safety Generalization

The uncertainty gate is rule-based and validated on tested synthetic uncertainty markers. It should not be claimed to guarantee safety under arbitrary naturally occurring conflict, incomplete evidence, or adversarial distractors. The distractor robustness experiment shows a clear remaining weakness. A stronger safety claim requires a separate natural-conflict benchmark collected from real document revisions, including contradictory change notes, overlapping effective dates, obsolete-but-authoritative clauses, and source-level disagreement.

### 7.6 Repair Candidate Generation

Repair closure validates selected finite candidate OWL artifacts. It does not yet prove fully automatic repair-candidate generation from raw document changes. A complete repair paper should separately evaluate candidate generation, candidate ranking, Reasoner consistency, and CQ regression.

## Reproducibility

The reproduction runbook is provided in `output/reproducibility-package.md`. It contains a full Qwen-dependent path and a faster offline rebuild path. The full path currently expects approximately 1440 Qwen calls; the offline path assumes raw Qwen outputs already exist and rebuilds summaries, statistics, tables, failure analysis, and diagrams.
"""

    md_path = OUT / f"{PREFIX}.md"
    json_path = OUT / f"{PREFIX}.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "markdown": str(md_path),
                "sources": [
                    str(OUT / "paper-experiment-main-table.md"),
                    str(OUT / "auto-policy-v3-failure-case-analysis.md"),
                    str(OUT / "method-flow-and-input-isolation-diagrams.md"),
                    str(OUT / "reproducibility-package.md"),
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"markdown={md_path}")
    print(f"json={json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
