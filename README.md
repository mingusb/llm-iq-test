# 🧪 llm-iq-test

<p align="center">
  <img src="https://img.shields.io/badge/python-3.x-blue?style=for-the-badge" alt="Python">
  <img src="https://img.shields.io/badge/status-active-success?style=for-the-badge" alt="Status">
</p>

<p align="center"><b>This repository provides utilities to generate quantified Boolean formula (QBF) problems at any finite polynomial-hierarchy level and to assess the highest level an LLM can solve as an IQ proxy.</b></p>

---

## 📑 Index
- [🚀 Overview](#-overview)
- [🏁 Quickstart](#-quickstart)
- [📦 Prerequisites & Dependencies](#-prerequisites--dependencies)

---

## 🚀 Overview

This harness labels each level by the number of quantifier blocks in the QBF. In Complexity Zoo terms, true QBF with k alternations starting with an existential block is Sigma_k^P-complete; starting with a universal block is Pi_k^P-complete. The generator matches that quantifier structure, so the level labels map to PH tiers, but the mapping is structural rather than a proof of worst-case hardness:

- Instances are synthetic and do not guarantee completeness or worst-case hardness for the corresponding PH class.
- The certified constructions and variable caps bias the distribution of instances; results reflect this benchmark, not class membership.
- The IQ proxy reports the highest level solved on sampled instances and uses monotonic assumptions in binary/auto search modes.

### Formats

- QDIMACS output includes alternating quantifier blocks followed by CNF clauses.
- JSON output includes quantifier blocks, clauses, and metadata.

---

## 🏁 Quickstart

### Generate problems

Generate a Sigma_k or Pi_k QBF problem as QDIMACS or JSON:

```bash
python -m ph_iq.cli generate --level 3 --class sigma --vars 12 --clauses 40 --clause-size 3 --seed 7 --format qdimacs -o problem.qdimacs
python -m ph_iq.cli generate --level 2 --class pi --vars 8 --clauses 20 --clause-size 3 --seed 11 --format json -o problem.json
```

Optional: specify per-block variable counts with `--variables-per-block 2,3,4`.

### Assess IQ proxy

Create a JSON results file with per-problem outcomes:

```json
{
  "results": [
    {"level": 1, "solved": true},
    {"level": 2, "solved": true},
    {"level": 3, "solved": false}
  ]
}
```

Assess the highest solved level:

```bash
python -m ph_iq.cli assess --input results.json --require-all
```

The assessment returns the maximum level where all results at that level are solved (`--require-all`) or where any result at that level is solved (`--require-any`).

### Codex LLM IQ evaluation

Run a multi-level LLM evaluation that prompts `codex exec` with QBF instances, records model responses, and computes a psychometric IQ estimate. Ground truth is certified by construction using a randomized circuit/selector strategy encoding. By default the script uses auto expansion + binary search to find the highest solved level with fewer queries; use a linear sweep for full coverage:

```bash
python scripts/evaluate_codex_llm_iq.py
python scripts/evaluate_codex_llm_iq.py --search-mode linear
```

Outputs:

- `docs/codex_llm_iq_results.json`
- `docs/codex_llm_iq_assessment.json`
- `docs/codex_llm_iq_prompt_log.jsonl`
- `docs/codex_llm_iq_item_report.csv`

Defaults to 256 levels; override with `--max-level`. Auto/binary search assumes monotonic performance by level.

By default the evaluator runs at least two instances per level to confirm correctness/incorrectness; override with `--instances-per-level` (minimum 2). Timeouts trigger a retry with more time; tune via `--timeout-retry-sec` or `--timeout-retry-multiplier` (default 2x). The certified construction scales `max-input-vars` with the level (capped by `--max-input-vars`); use `--max-input-vars-fixed` to force a constant cap.

Tune the certified construction input cap with `--max-input-vars` or run a grid search over timeouts and max-input-vars:

```bash
python scripts/evaluate_codex_llm_iq.py --max-input-vars 64
python scripts/evaluate_codex_llm_iq.py --grid-timeouts 120,240 --grid-max-input-vars 16,32,64 --max-level 1024 --search-mode auto
```

Grid search outputs:

- `docs/codex_llm_iq_grid_summary.json`
- `docs/codex_llm_iq_grid/` (per-run results, assessments, prompt logs, and item reports)

### Prompt corpus

Generate a prompt corpus for levels 1..1000 (one prompt per level):

```bash
python scripts/generate_llm_iq_prompt_corpus.py
```

Outputs:

- `llm_iq_prompt_corpus/level_0001.txt` .. `llm_iq_prompt_corpus/level_1000.txt`
- `llm_iq_prompt_corpus/answers.csv`

---

## 📦 Prerequisites & Dependencies

- Python 3.x

---
