"""Eval runner (docs/spec.md section 12).

    uv run python -m evals.run --tier fast                 # retrieval + scope-leak, no LLM calls
    uv run python -m evals.run --tier slow                 # full answers, judge, tools
    uv run python -m evals.run --tier fast --backend fake  # offline plumbing check
    uv run python -m evals.run --tier slow --log "add reranker"   # also append to docs/experiments.md

Exit code 1 when the gate fails: any scope leak, or a metric more than --margin
below the stored baseline for the same tier and backend.
"""

import argparse
import json
import re
import statistics
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

import yaml

from apps.api import prompts
from apps.api.chat import ChatRequest, ChatService, ChatSettings, collect
from apps.api.cli import load_all
from apps.api.config import Settings
from apps.api.db import make_pool, run_migrations
from apps.api.llm import build_models
from apps.api.retrieval import RetrievalConfig, retrieve
from apps.api.scope import Claims, build_scope
from apps.api.tools.student_records import StudentRecords

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "golden" / "cases.yaml"
CALIBRATION = ROOT / "evals" / "golden" / "judge_calibration.yaml"
BASELINE = ROOT / "evals" / "baseline.json"
RUNS = ROOT / "evals" / "runs"
EXPERIMENTS = ROOT / "docs" / "experiments.md"
DATA_TODAY = yaml.safe_load((ROOT / "data" / "plan.yaml").read_text())["today_for_data"]

SLOW_ONLY = {"follow_up", "tool"}
CALIBRATION_GATE = 0.85
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_CITATION = re.compile(r"\[(\d+)\]")
_SENTENCE = re.compile(r"(?<=[.!?।])\s+")


# --- helpers ---------------------------------------------------------------------


def claims_for(case: dict) -> Claims | None:
    if case["role"] == "anonymous":
        return None
    return Claims(
        sub=case.get("user") or f"eval-{case['role']}-{case['college']}",
        role=case["role"],
        college=case["college"],
    )


def has_fact(answer: str, variants: list[str]) -> bool:
    text = answer.lower()
    return any(v.lower() in text for v in variants)


def ratio(values: list[bool | float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[index]


# --- fast tier: retrieval only ----------------------------------------------------------


def run_retrieval_case(conn, case, models, config: RetrievalConfig) -> dict:
    scope = build_scope(claims_for(case))
    result = retrieve(conn, scope, case["question"], models.embedder, models.reranker, config)
    candidates, final = result.stages["candidates"], result.stages["final"]
    expected = set(case.get("expected_docs", []))
    forbidden = set(case.get("forbidden_docs", []))
    row = {
        "id": case["id"],
        "type": case["type"],
        "language": case["language"],
        "leaked": sorted(forbidden & (set(candidates) | set(final))),
    }
    if expected:
        row["recall_candidates"] = bool(expected & set(candidates))
        row["recall_at_5"] = bool(expected & set(final))
        rank = next((i for i, slug in enumerate(final, start=1) if slug in expected), None)
        row["reciprocal_rank"] = 1 / rank if rank else 0.0
    row["top_score"] = result.top_score
    return row


# --- slow tier: full answers -------------------------------------------------------------


def judge_claim(llm, claim: str, source: str) -> bool:
    result = llm.chat(prompts.judge_messages(claim, source), max_tokens=5)
    return "not_supported" not in result.text.lower() and "supported" in result.text.lower()


def faithfulness(llm, answer: str, sources: list[str]) -> float | None:
    """Share of the answer's sentences that the judge finds supported by the sources they cite."""
    sentences = [s for s in _SENTENCE.split(answer.strip()) if len(s.split()) >= 3]
    if not sentences or not sources:
        return None
    supported = []
    for sentence in sentences:
        numbers = [int(n) for n in _CITATION.findall(sentence) if 1 <= int(n) <= len(sources)]
        source = "\n\n".join(sources[n - 1] for n in numbers) if numbers else "\n\n".join(sources)
        supported.append(judge_claim(llm, _CITATION.sub("", sentence).strip(), source))
    return sum(supported) / len(supported)


def run_chat_case(service: ChatService, pool, case, models, login) -> dict:
    claims = claims_for(case)
    token = login(case["user"]) if case.get("user") else None
    conversation_id = None
    for earlier in case.get("history", []):
        prior = collect(service.stream(ChatRequest(earlier, claims, token, conversation_id)))
        conversation_id = prior["conversation_id"]

    started = time.perf_counter()
    result = collect(service.stream(ChatRequest(case["question"], claims, token, conversation_id)))
    wall_ms = int((time.perf_counter() - started) * 1000)

    answer = result["answer"]
    retrieved = result.get("retrieved", {})
    seen = set(retrieved.get("candidates", [])) | set(retrieved.get("final", []))
    cited = {c["slug"] for c in result["citations"]}
    forbidden = set(case.get("forbidden_docs", []))
    sources = result.get("sources", [])

    row = {
        "id": case["id"],
        "type": case["type"],
        "language": case["language"],
        "outcome": result.get("outcome"),
        "answer": answer,
        "cited": sorted(cited),
        "leaked": sorted(forbidden & (seen | cited)),
        "leaked_facts": [f for f in case.get("forbidden_facts", []) if f.lower() in answer.lower()],
        "wall_ms": wall_ms,
    }
    if retrieved and case.get("expected_docs"):
        expected = set(case["expected_docs"])
        row["recall_candidates"] = bool(expected & set(retrieved["candidates"]))
        row["recall_at_5"] = bool(expected & set(retrieved["final"]))
        rank = next((i for i, slug in enumerate(retrieved["final"], start=1) if slug in expected), None)
        row["reciprocal_rank"] = 1 / rank if rank else 0.0
    if case.get("expected_outcome"):
        row["outcome_ok"] = result.get("outcome") == case["expected_outcome"]
    if case.get("expected_facts") and result.get("outcome") in ("answered", "tool"):
        row["fact_match"] = all(has_fact(answer, group) for group in case["expected_facts"])
    elif case.get("expected_facts"):
        row["fact_match"] = False
    if result.get("outcome") == "answered":
        numbers = [int(n) for n in _CITATION.findall(answer)]
        valid = bool(numbers) and all(1 <= n <= len(sources) for n in numbers)
        if case.get("expected_docs"):
            valid = valid and bool(cited & set(case["expected_docs"]))
        row["citation_valid"] = valid
        row["faithfulness"] = faithfulness(models.llm, answer, sources)
        question_script = bool(_DEVANAGARI.search(case["question"]))
        row["language_match"] = bool(_DEVANAGARI.search(answer)) == question_script
    if case.get("expected_tools"):
        row["tools_ok"] = set(case["expected_tools"]) <= set(result.get("tools_called", []))
        if case.get("expect_pending_action"):
            row["tools_ok"] = row["tools_ok"] and bool(result.get("pending_action_id"))

    with pool.connection() as conn:
        usage = conn.execute(
            "SELECT prompt_tokens, completion_tokens, first_token_ms, total_ms FROM answers WHERE turn_id = %s",
            (result["turn_id"],),
        ).fetchone()
    if usage:
        row.update(
            {k: usage[k] for k in ("prompt_tokens", "completion_tokens", "first_token_ms", "total_ms")}
        )
    return row


# --- aggregation, gate, reporting --------------------------------------------------------


def summarise(rows: list[dict], tier: str) -> dict:
    def pick(key, where=lambda r: True):
        return [r[key] for r in rows if key in r and r[key] is not None and where(r)]

    leak_rows = [r for r in rows if r["type"] == "scope_leak"]
    metrics = {
        "cases": len(rows),
        "scope_leak_pass": ratio([not r["leaked"] and not r.get("leaked_facts") for r in leak_rows]),
        "no_stale_docs": ratio([not r["leaked"] for r in rows if r["type"] in ("superseded", "abstain")]),
        "recall_candidates": ratio(pick("recall_candidates")),
        "recall_at_5": ratio(pick("recall_at_5")),
        "mrr": ratio(pick("reciprocal_rank")),
    }
    by_language = {}
    for language in sorted({r["language"] for r in rows}):
        recall = pick("recall_at_5", lambda r, lang=language: r["language"] == lang)
        by_language[language] = {"recall_at_5": ratio(recall)}
    if tier == "slow":
        should_abstain = [r for r in rows if r["type"] == "abstain"]
        abstained = [r for r in rows if r.get("outcome") == "abstained"]
        metrics.update(
            {
                "fact_match": ratio(pick("fact_match")),
                "citation_valid": ratio(pick("citation_valid")),
                "faithfulness": ratio(pick("faithfulness")),
                "language_match": ratio(pick("language_match")),
                "outcome_ok": ratio(pick("outcome_ok")),
                "abstain_recall": ratio([r.get("outcome") == "abstained" for r in should_abstain]),
                "abstain_precision": ratio(
                    [r["type"] in ("abstain", "scope_leak") or r["id"] in ("s05",) for r in abstained]
                ),
                "tools_ok": ratio(pick("tools_ok")),
                "p50_total_ms": percentile(pick("total_ms"), 50),
                "p95_total_ms": percentile(pick("total_ms"), 95),
                "p95_first_token_ms": percentile(pick("first_token_ms"), 95),
                "avg_tokens_per_answer": round(
                    statistics.mean(
                        [
                            r["prompt_tokens"] + r["completion_tokens"]
                            for r in rows
                            if r.get("prompt_tokens") is not None
                        ]
                    ),
                    1,
                )
                if any(r.get("prompt_tokens") is not None for r in rows)
                else None,
            }
        )
        for language in by_language:
            facts = pick("fact_match", lambda r, lang=language: r["language"] == lang)
            by_language[language]["fact_match"] = ratio(facts)
    metrics["by_language"] = by_language
    return metrics


# Metrics where higher is better and a drop fails the gate. Latency is reported, not gated.
GATED = (
    "recall_candidates",
    "recall_at_5",
    "mrr",
    "no_stale_docs",
    "fact_match",
    "citation_valid",
    "language_match",
    "outcome_ok",
    "abstain_recall",
    "tools_ok",
)


def gate(metrics: dict, baseline: dict | None, margin: float, calibration: float | None) -> list[str]:
    failures = []
    if metrics["scope_leak_pass"] is not None and metrics["scope_leak_pass"] < 1.0:
        failures.append(f"scope_leak_pass is {metrics['scope_leak_pass']} (must be 1.0)")
    gated = list(GATED)
    if calibration is not None and calibration >= CALIBRATION_GATE:
        gated.append("faithfulness")  # only trusted once the judge agrees with hand labels
    for key in gated:
        now, before = metrics.get(key), (baseline or {}).get(key)
        if now is not None and before is not None and now < before - margin:
            failures.append(f"{key} dropped from {before} to {now} (margin {margin})")
    return failures


def calibrate(llm) -> float | None:
    if not CALIBRATION.exists():
        return None
    items = yaml.safe_load(CALIBRATION.read_text())
    agree = [
        judge_claim(llm, item["claim"], item["source"]) == (item["label"] == "supported") for item in items
    ]
    return ratio(agree)


def print_report(metrics: dict, rows: list[dict], failures: list[str]) -> None:
    print("\n| metric | value |\n|---|---|")
    for key, value in metrics.items():
        if key != "by_language":
            print(f"| {key} | {value} |")
    print("\n| language | " + " | ".join(next(iter(metrics["by_language"].values())).keys()) + " |")
    for language, values in metrics["by_language"].items():
        print(f"| {language} | " + " | ".join(str(v) for v in values.values()) + " |")
    misses = [
        r["id"]
        for r in rows
        if r.get("recall_at_5") is False or r.get("fact_match") is False or r.get("outcome_ok") is False
    ]
    if misses:
        print(f"\nmisses: {', '.join(misses)}")
    leaks = [
        f"{r['id']}: {r['leaked'] or r.get('leaked_facts')}"
        for r in rows
        if r["leaked"] or r.get("leaked_facts")
    ]
    if leaks:
        print("\nLEAKS:\n  " + "\n  ".join(leaks))
    print("\nGATE: " + ("FAIL\n  " + "\n  ".join(failures) if failures else "pass"))


def append_experiment(label: str, key: str, metrics: dict, failures: list[str]) -> None:
    if not EXPERIMENTS.exists():
        return
    row = (
        f"| {date.today().isoformat()} | {label} ({key}) | {metrics.get('recall_at_5')} | "
        f"{metrics.get('fact_match', '-')} | {metrics.get('faithfulness', '-')} | "
        f"{metrics.get('p95_total_ms', '-')} | {'no' if failures else 'tbd'} | |\n"
    )
    with EXPERIMENTS.open("a") as handle:
        handle.write(row)


# --- main ---------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.run")
    parser.add_argument("--tier", choices=("fast", "slow"), default="fast")
    parser.add_argument("--split", choices=("dev", "heldout", "all"), default="all")
    parser.add_argument("--backend", choices=("fake", "ollama"))
    parser.add_argument("--mode", choices=("vector", "hybrid"))
    parser.add_argument("--reranker", choices=("none", "fake", "http"))
    parser.add_argument("--threshold", type=float, help="abstain threshold")
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--margin", type=float, default=0.05)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--log", metavar="CHANGE", help="append a row to docs/experiments.md")
    args = parser.parse_args(argv)

    settings = Settings()
    for name, value in (
        ("ai_backend", args.backend),
        ("retrieval_mode", args.mode),
        ("reranker", args.reranker),
        ("abstain_threshold", args.threshold),
    ):
        if value is not None:
            setattr(settings, name, value)
    key = f"{args.tier}:{settings.ai_backend}"

    cases = yaml.safe_load(CASES.read_text())
    if args.split != "all":
        cases = [c for c in cases if c["split"] == args.split]
    if args.tier == "fast":
        cases = [c for c in cases if c["type"] not in SLOW_ONLY]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    models = build_models(settings)
    run_migrations(settings.database_url)
    pool = make_pool(settings.database_url)
    config = RetrievalConfig(settings.retrieval_mode, settings.candidates_k, settings.final_k, DATA_TODAY)
    print(
        f"{key}: {len(cases)} cases, mode={settings.retrieval_mode}, reranker={settings.reranker}, "
        f"llm={models.llm.model}, embedder={models.embedder.model_id}"
    )

    rows: list[dict] = []
    calibration = None
    try:
        with pool.connection() as conn:
            load_all(conn, models.embedder, quiet=True)  # only changed documents are re-embedded

        if args.tier == "fast":
            with pool.connection() as conn:
                rows = [run_retrieval_case(conn, case, models, config) for case in cases]
        else:
            from fastapi.testclient import TestClient

            from apps.student_api.main import Settings as StudentSettings
            from apps.student_api.main import create_app as create_student_app

            with (
                tempfile.TemporaryDirectory() as keys,
                TestClient(
                    create_student_app(
                        replace_settings(StudentSettings(), settings.database_url, Path(keys))
                    ),
                    base_url="http://student-api",
                ) as student_api,
            ):

                def login(username: str) -> str:
                    response = student_api.post("/login", json={"username": username, "password": "password"})
                    response.raise_for_status()
                    return response.json()["access_token"]

                service = ChatService(
                    pool,
                    models,
                    StudentRecords(client=student_api),
                    ChatSettings(
                        retrieval=config,
                        abstain_threshold=settings.abstain_threshold,
                        answer_max_tokens=settings.answer_max_tokens,
                        include_retrieval_debug=True,
                    ),
                )
                for number, case in enumerate(cases, start=1):
                    rows.append(run_chat_case(service, pool, case, models, login))
                    print(f"  [{number}/{len(cases)}] {case['id']} -> {rows[-1]['outcome']}", flush=True)
            calibration = calibrate(models.llm)
    finally:
        pool.close()

    metrics = summarise(rows, args.tier)
    if calibration is not None:
        metrics["judge_calibration"] = calibration
    baselines = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    failures = gate(metrics, baselines.get(key), args.margin, calibration)
    print_report(metrics, rows, failures)

    RUNS.mkdir(parents=True, exist_ok=True)
    out = (
        RUNS
        / f"{datetime.now():%Y%m%d-%H%M%S}-{args.tier}-{settings.ai_backend}-{settings.retrieval_mode}.json"
    )
    out.write_text(
        json.dumps({"key": key, "metrics": metrics, "rows": rows}, indent=2, ensure_ascii=False, default=str)
    )
    print(f"\nwritten {out.relative_to(ROOT)}")

    if args.update_baseline:
        if any("scope_leak" in f for f in failures):
            print("not updating the baseline: scope leaks")
            return 1
        baselines[key] = {k: v for k, v in metrics.items() if k in GATED or k == "faithfulness"}
        BASELINE.write_text(json.dumps(baselines, indent=2, sort_keys=True) + "\n")
        print(f"baseline updated for {key}")
        failures = [f for f in failures if "scope_leak" in f]
    if args.log:
        append_experiment(args.log, key, metrics, failures)
    return 1 if failures else 0


def replace_settings(student_settings, database_url: str, keys_dir: Path):
    return student_settings.model_copy(
        update={"database_url": database_url, "student_api_keys_dir": keys_dir}
    )


if __name__ == "__main__":
    sys.exit(main())
