from __future__ import annotations

"""Write paper-ready Mermaid diagrams for method flow and input isolation."""

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(r"G:\LearnAI\ontology-evolution")
OUT = ROOT / "output"
PREFIX = "method-flow-and-input-isolation"


METHOD_FLOW = """flowchart LR
    subgraph PublicInputs["Public benchmark inputs"]
        Docs["Public source documents<br/>and evidence notes"]
        Meta["Public event metadata<br/>type, target, context"]
        Mutant["Mutant OWL"]
        Candidates["Finite public repair candidates<br/>operations and candidate OWLs"]
    end

    subgraph Generation["Auto Policy V3 generation"]
        Prompt["Prompt assembly<br/>excludes candidates, Oracle,<br/>and manual formal-policy files"]
        Qwen["Qwen canonical JSON"]
        Schema["Schema validation<br/>forbidden marker audit"]
        Normalize["Deterministic canonical normalizer<br/>to controlled semantic_result"]
    end

    subgraph Gate["V3+Gate uncertainty control"]
        Uncertainty["Candidate-blind uncertainty gate<br/>checks status, canonical output,<br/>and evidence markers"]
    end

    subgraph SelectionRepair["Selection and executable repair"]
        Selector["Candidate selector<br/>matches semantic_result to finite candidates"]
        CandidateOwl["Selected candidate OWL artifact"]
        Reasoner["Reasoner consistency gate"]
        CqChecks["Repair CQs<br/>source trigger and selected-operation check"]
    end

    subgraph Evaluation["Offline evaluation only"]
        Oracle["Private Oracle<br/>loaded after decisions are fixed"]
        Metrics["Selection accuracy,<br/>repair closure, safety metrics"]
    end

    Docs --> Prompt
    Meta --> Prompt
    Prompt --> Qwen --> Schema --> Normalize --> Uncertainty
    Uncertainty -- PASS --> Selector
    Uncertainty -- ABSTAIN --> Metrics
    Candidates --> Selector
    Selector --> CandidateOwl
    Mutant --> CandidateOwl
    CandidateOwl --> Reasoner --> CqChecks --> Metrics
    Oracle --> Metrics
"""


INPUT_ISOLATION = """flowchart TB
    subgraph Legend["Legend"]
        Public["green = public / allowed online"]
        Blocked["red = blocked until evaluation"]
        Derived["blue = generated or derived online"]
    end

    subgraph Stage1["Stage 1: V3 Qwen generation"]
        S1Allowed["Allowed: public documents, evidence notes,<br/>event metadata, target schema"]
        S1Blocked["Blocked: candidate IDs, candidate values,<br/>candidate operations, manual formal-policy,<br/>private Oracle"]
    end

    subgraph Stage2["Stage 2: validation and normalization"]
        S2Allowed["Allowed: Qwen output, public metadata,<br/>public evidence, predefined canonical vocabulary"]
        S2Blocked["Blocked: private Oracle and manual formal-policy"]
    end

    subgraph Stage3["Stage 3: uncertainty gate"]
        S3Allowed["Allowed: generation status, canonical result,<br/>candidate-blind evidence markers"]
        S3Blocked["Blocked: private Oracle and candidate values<br/>before gate decision"]
    end

    subgraph Stage4["Stage 4: candidate selection and repair"]
        S4Allowed["Allowed after semantic_result is fixed:<br/>finite candidate operations and candidate OWLs"]
        S4Blocked["Blocked: private Oracle until all selections<br/>and closure rows are fixed"]
    end

    subgraph Stage5["Stage 5: evaluation"]
        S5Allowed["Allowed: private Oracle loaded for metrics only"]
        S5Output["Outputs: Oracle accuracy, Reasoner pass,<br/>CQ regression, safety rates"]
    end

    S1Allowed --> S2Allowed --> S3Allowed --> S4Allowed --> S5Allowed --> S5Output
    S1Blocked -. isolation boundary .-> S2Blocked
    S2Blocked -. isolation boundary .-> S3Blocked
    S3Blocked -. isolation boundary .-> S4Blocked

    classDef public fill:#e8f5e9,stroke:#2e7d32,color:#111;
    classDef blocked fill:#ffebee,stroke:#c62828,color:#111;
    classDef derived fill:#e3f2fd,stroke:#1565c0,color:#111;
    class Public,S1Allowed,S4Allowed,S5Allowed public;
    class Blocked,S1Blocked,S2Blocked,S3Blocked,S4Blocked blocked;
    class Derived,S2Allowed,S3Allowed,S5Output derived;
"""


BOUNDARY = """flowchart LR
    subgraph ManualPolicy["Policy-available upper-bound line"]
        FP["OPTION_FORMAL_POLICY<br/>Qwen reads manual facts/rules<br/>and candidate operations"]
        HG["OPTION_FORMAL_POLICY_HARD_GATE<br/>symbolic execution of manual facts/rules"]
        Cost["Non-zero construction effort<br/>1330 heuristic policy-complexity points"]
        FP --> HG --> Cost
    end

    subgraph AutoPolicy["Automatic policy construction line"]
        V2["AUTO_POLICY_V2<br/>candidate-blind free-form semantic_result"]
        V3["AUTO_POLICY_V3<br/>candidate-blind canonical JSON<br/>plus deterministic normalizer"]
        Gate["AUTO_POLICY_V3_PLUS_GATE<br/>candidate-blind uncertainty gate"]
        V2 --> V3 --> Gate
    end

    subgraph RepairEval["Common downstream closure"]
        Select["Finite candidate selector"]
        Repair["OWL repair artifact"]
        Reason["Reasoner + CQ regression"]
        Eval["Oracle loaded only for final metrics"]
        Select --> Repair --> Reason --> Eval
    end

    HG --> Select
    Gate --> Select

    Claim1["Safe claim:<br/>V3 improves automatic candidate-blind repair closure"]
    Claim2["Unsafe claim:<br/>manual hard gate is not a fully automatic method"]
    Gate --> Claim1
    HG --> Claim2
"""


def write(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def main() -> int:
    method_path = OUT / "auto-policy-v3-method-flow.mmd"
    isolation_path = OUT / "auto-policy-v3-input-isolation.mmd"
    boundary_path = OUT / "auto-policy-v3-policy-boundary.mmd"
    md_path = OUT / f"{PREFIX}-diagrams.md"
    json_path = OUT / f"{PREFIX}-diagrams.json"

    write(method_path, METHOD_FLOW)
    write(isolation_path, INPUT_ISOLATION)
    write(boundary_path, BOUNDARY)

    md = f"""# Method Flow And Input Isolation Diagrams

These diagrams are paper-facing summaries of the external-real-v1 Auto Policy V3 pipeline. They are descriptive artifacts built from existing scripts/results and do not run Qwen or modify benchmark data.

## Figure 1. Auto Policy V3 And V3+Gate Pipeline

```mermaid
{METHOD_FLOW.rstrip()}
```

Caption: Auto Policy V3 generates a candidate-blind canonical semantic policy from public evidence, normalizes it into a controlled semantic result, optionally applies a candidate-blind uncertainty gate, then performs finite candidate selection and executable OWL repair validation. The private Oracle is loaded only after selections and closure rows are fixed.

## Figure 2. Input Isolation By Stage

```mermaid
{INPUT_ISOLATION.rstrip()}
```

Caption: The strongest leakage boundary is the V3 generation stage: Qwen receives public source evidence and target metadata only, not candidate IDs, candidate values, manual formal-policy rules, or Oracle labels. Candidate operations become visible only after the semantic result is fixed, and the private Oracle is reserved for offline metrics.

## Figure 3. Manual-Policy Upper Bound Versus Automatic V3

```mermaid
{BOUNDARY.rstrip()}
```

Caption: `OPTION_FORMAL_POLICY` and `OPTION_FORMAL_POLICY_HARD_GATE` are policy-available upper bounds because they depend on manually structured facts/rules. `AUTO_POLICY_V3` and `AUTO_POLICY_V3_PLUS_GATE` are the automatic candidate-blind line evaluated against the same downstream candidate repair and closure machinery.

## Paper-Safe Use

- Use Figure 1 for the method section.
- Use Figure 2 when discussing leakage prevention and Oracle isolation.
- Use Figure 3 when explaining why the symbolic hard gate is an upper bound rather than the main automatic method.
- Do not claim that V3 learns symbolic rules automatically; it uses a predefined canonical vocabulary and deterministic normalization.
"""
    write(md_path, md)
    json_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "outputs": {
                    "method_flow": str(method_path),
                    "input_isolation": str(isolation_path),
                    "policy_boundary": str(boundary_path),
                    "markdown": str(md_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"method_flow={method_path}")
    print(f"input_isolation={isolation_path}")
    print(f"policy_boundary={boundary_path}")
    print(f"markdown={md_path}")
    print(f"json={json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
