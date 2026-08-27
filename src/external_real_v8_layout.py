from __future__ import annotations

"""Stage-aware paths for external-real-v8-grounded.

Gold-assisted dataset curation may read rules/candidates/oracle. Model stages
may not. Construction reads only public/. Candidate selection may add
repair-stage/. Evaluation may add private/. Hard Gate may add rules/ as an
upper bound, never as Auto Policy construction input.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK = ROOT / "benchmark" / "external-real-v8-grounded"

STAGES = {
    "construction": frozenset({"public"}),
    "candidate_selection": frozenset({"public", "repair-stage"}),
    "evaluation": frozenset({"public", "repair-stage", "private"}),
    "hard_gate": frozenset({"public", "repair-stage", "rules"}),
    "curation": frozenset({"public", "repair-stage", "private", "rules"}),
}

FULL_METADATA_FIELDS = (
    "title",
    "case_context",
    "subject_label",
    "predicate_label",
    "domain",
    "semantic_type",
)
LIGHT_METADATA_FIELDS = (
    "subject_label",
    "predicate_label",
    "domain",
    "semantic_type",
)

SUPPORT_GATE_VERSION = "v8.1-semantic-adjudication"


class StageAccessError(PermissionError):
    """Raised when a method stage reads a directory it is not allowed to use."""


class BenchmarkLayout:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or DEFAULT_BENCHMARK).resolve()

    @property
    def public(self) -> Path:
        return self.root / "public"

    @property
    def repair_stage(self) -> Path:
        return self.root / "repair-stage"

    @property
    def private(self) -> Path:
        return self.root / "private"

    @property
    def rules(self) -> Path:
        return self.root / "rules"

    @property
    def public_events(self) -> Path:
        return self.public / "events"

    @property
    def public_documents(self) -> Path:
        return self.public / "documents"

    @property
    def public_excerpts(self) -> Path:
        return self.public / "excerpts"

    @property
    def public_retrieval(self) -> Path:
        return self.public / "retrieval"

    @property
    def event_csv(self) -> Path:
        return self.public_events / "external-real-event-template.csv"

    @property
    def document_csv(self) -> Path:
        return self.public_documents / "external-real-document-template.csv"

    @property
    def retrieval_csv(self) -> Path:
        return self.public_retrieval / "external-real-v8-event-retrieval.csv"

    @property
    def family_csv(self) -> Path:
        return self.public_retrieval / "external-real-v8-grounded-source-families.csv"

    @property
    def alignment_csv(self) -> Path:
        return self.public_retrieval / "external-real-v8-event-source-alignment.csv"

    @property
    def registry_csv(self) -> Path:
        return self.public_retrieval / "normative-source-registry.csv"

    @property
    def query_contracts(self) -> Path:
        return self.public_retrieval / "query-contracts.json"

    @property
    def support_adjudication_csv(self) -> Path:
        return self.public_retrieval / "support-adjudication.csv"

    @property
    def metadata_leakage_csv(self) -> Path:
        return self.public_retrieval / "metadata-leakage-audit.csv"

    @property
    def candidate_csv(self) -> Path:
        return self.repair_stage / "candidates" / "external-real-candidate-template.csv"

    @property
    def operations_dir(self) -> Path:
        return self.repair_stage / "operations"

    @property
    def mutants_dir(self) -> Path:
        return self.repair_stage / "mutants"

    @property
    def oracle_csv(self) -> Path:
        return self.private / "oracle" / "external-real-oracle-template.csv"

    def zone_roots(self) -> dict[str, Path]:
        return {
            "public": self.public,
            "repair-stage": self.repair_stage,
            "private": self.private,
            "rules": self.rules,
        }

    def allowed_roots(self, stage: str) -> list[Path]:
        if stage not in STAGES:
            raise ValueError(f"unknown stage: {stage}")
        roots = self.zone_roots()
        return [roots[name] for name in STAGES[stage]]

    def zone_for(self, path: Path) -> str | None:
        resolved = path.resolve()
        for name, root in self.zone_roots().items():
            if not root.exists():
                continue
            try:
                resolved.relative_to(root.resolve())
                return name
            except ValueError:
                continue
        return None

    def assert_readable(self, stage: str, path: Path) -> Path:
        zone = self.zone_for(path)
        allowed = STAGES[stage]
        if zone is None:
            raise StageAccessError(f"{stage} path is outside the v8 benchmark: {path}")
        if zone not in allowed:
            raise StageAccessError(
                f"{stage} cannot read {zone}/ ({path}). Allowed: {sorted(allowed)}"
            )
        return path


    def public_candidate_paths(self) -> list[Path]:
        if not self.public.exists():
            return []
        return [
            path
            for path in self.public.rglob("*")
            if path.is_file() and "candidate" in path.name.lower()
        ]


def is_staged_layout(benchmark_dir: Path) -> bool:
    return (benchmark_dir / "public" / "events").is_dir() or (
        benchmark_dir / "public" / "excerpts"
    ).is_dir()


def construction_paths(benchmark_dir: Path) -> dict[str, Path]:
    """Paths Auto Policy Construction may read."""
    if is_staged_layout(benchmark_dir):
        layout = BenchmarkLayout(benchmark_dir)
        return {
            "event_csv": layout.event_csv,
            "document_csv": layout.document_csv,
            "document_dir": layout.public_documents,
            "excerpt_dir": layout.public_excerpts,
            "retrieval_dir": layout.public_retrieval,
        }
    return {
        "event_csv": benchmark_dir / "input" / "external-real-event-template.csv",
        "document_csv": benchmark_dir / "input" / "external-real-document-template.csv",
        "document_dir": benchmark_dir / "documents",
        "excerpt_dir": benchmark_dir / "documents" / "excerpts",
        "retrieval_dir": benchmark_dir / "source-intake",
    }


def candidate_selection_paths(benchmark_dir: Path) -> dict[str, Path]:
    """Paths Candidate Selection may read: public + repair-stage."""
    paths = construction_paths(benchmark_dir)
    if is_staged_layout(benchmark_dir):
        layout = BenchmarkLayout(benchmark_dir)
        paths["candidate_csv"] = layout.candidate_csv
        paths["operations_dir"] = layout.operations_dir
        paths["mutants_dir"] = layout.mutants_dir
        return paths
    paths["candidate_csv"] = benchmark_dir / "input" / "external-real-candidate-template.csv"
    paths["operations_dir"] = benchmark_dir / "input"
    paths["mutants_dir"] = benchmark_dir / "mutants"
    return paths


def evaluation_paths(benchmark_dir: Path) -> dict[str, Path]:
    """Paths Evaluation may read: public + repair-stage + private."""
    paths = candidate_selection_paths(benchmark_dir)
    if is_staged_layout(benchmark_dir):
        paths["oracle_csv"] = BenchmarkLayout(benchmark_dir).oracle_csv
        return paths
    paths["oracle_csv"] = benchmark_dir / "private" / "external-real-oracle-template.csv"
    return paths


def hard_gate_paths(benchmark_dir: Path) -> dict[str, Path]:
    """Hard Gate may read public + repair-stage + rules as an upper bound."""
    paths = candidate_selection_paths(benchmark_dir)
    paths["rules_dir"] = benchmark_dir / "rules"
    return paths
