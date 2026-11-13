"""Measuring /correlate: does it find the relationships a person labelled, cite the right documents, and
give the same answer when asked again?

The cases live in correlation_cases.json next to this file. Each case is a question, the folders it reads,
whether a relationship should be found, and which documents the answer should cite. A run is scored on:

  found_ok     - did the answer's "relationship found" call match the label
  precision    - of the documents cited, the share that were expected
  recall       - of the expected documents, the share that were cited
  consistency  - across repeats, the share of runs that agree with the most common call

Usage (the API must be running; BEARER_TOKEN from the environment):

    python -m app.evaluation.correlation_eval --base http://localhost:8000 --repeats 3

Pure scoring lives in score_case, so it can be unit-tested without a model.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

CASES_PATH = Path(__file__).with_name("correlation_cases.json")


@dataclass(frozen=True)
class Case:
    question: str
    folders: list[str]
    expect_relation: bool
    expect_sources: list[str]  # file names (not paths) the answer should cite


@dataclass(frozen=True)
class RunOutcome:
    relation_found: bool
    cited: list[str]  # file names


@dataclass(frozen=True)
class CaseScore:
    question: str
    runs: int
    found_ok: float
    precision: float
    recall: float
    consistency: float


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Case(**item) for item in raw]


def outcome_from_response(response: dict) -> RunOutcome:
    """Read one /correlate response. A relationship is found only when the answer cites two or more
    documents: one cited document is a summary, not a link between documents."""
    cross = response.get("cross_doc") or {}
    cited = [Path(p).name for p in cross.get("sources", [])]
    return RunOutcome(relation_found=len(set(cited)) >= 2, cited=cited)


def score_case(case: Case, outcomes: list[RunOutcome]) -> CaseScore:
    if not outcomes:
        raise ValueError("no runs to score")
    found_ok = sum(o.relation_found == case.expect_relation for o in outcomes) / len(outcomes)

    expected = set(case.expect_sources)
    precisions, recalls = [], []
    for o in outcomes:
        cited = set(o.cited)
        if cited:
            precisions.append(len(cited & expected) / len(cited))
        if expected:
            recalls.append(len(cited & expected) / len(expected))
    precision = statistics.fmean(precisions) if precisions else 0.0
    recall = statistics.fmean(recalls) if recalls else (1.0 if not expected else 0.0)

    calls = Counter(o.relation_found for o in outcomes)
    consistency = calls.most_common(1)[0][1] / len(outcomes)
    return CaseScore(case.question, len(outcomes), found_ok, precision, recall, consistency)


def _post_correlate(base: str, token: str, case: Case) -> dict:
    body = json.dumps({"question": case.question, "folders": case.folders}).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/correlate",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.loads(resp.read().decode())


def run_eval(base: str, token: str, repeats: int, cases: list[Case]) -> list[CaseScore]:
    scores = []
    for case in cases:
        outcomes = [outcome_from_response(_post_correlate(base, token, case)) for _ in range(repeats)]
        scores.append(score_case(case, outcomes))
    return scores


def main() -> None:
    parser = argparse.ArgumentParser(description="Score /correlate against the labelled cases.")
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    args = parser.parse_args()

    token = os.environ.get("BEARER_TOKEN", "")
    if not token:
        raise SystemExit("set BEARER_TOKEN in the environment")
    scores = run_eval(args.base, token, args.repeats, load_cases(args.cases))
    for s in scores:
        print(json.dumps(asdict(s), ensure_ascii=False))
    if scores:
        print(
            json.dumps(
                {
                    "mean_found_ok": round(statistics.fmean(s.found_ok for s in scores), 3),
                    "mean_precision": round(statistics.fmean(s.precision for s in scores), 3),
                    "mean_recall": round(statistics.fmean(s.recall for s in scores), 3),
                    "mean_consistency": round(statistics.fmean(s.consistency for s in scores), 3),
                }
            )
        )


if __name__ == "__main__":
    main()
