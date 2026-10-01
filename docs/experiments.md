# Experiment log

One row per change to retrieval, prompts or models (docs/spec.md section 12.7).
`uv run python -m evals.run --tier slow --log "<change>"` appends a row; fill in "Kept?" and "Note" by hand.
Rejected changes stay here: they feed the README's "what didn't work".

Rows marked `fake` come from the deterministic stand-in models. They prove the pipeline and the scope
filter work; they say nothing about answer quality.

| Date | Change | recall@5 | Fact match | Faithfulness | p95 latency (ms) | Kept? | Note |
|------|--------|----------|------------|--------------|------------------|-------|------|
| 2026-10-01 | baseline: hybrid search, no reranker, threshold 0 (fake models) (slow:fake) | 0.885 | 0.702 | 1.0 | 6 | tbd | |
