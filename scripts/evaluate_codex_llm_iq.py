import argparse
import csv
import json
import math
import os
import random
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ph_iq import generator, psychometrics

DEFAULT_MAX_LEVEL = 256
DEFAULT_INSTANCES_PER_LEVEL = 2
CLASS_CYCLE = ["sigma", "pi"]
DEFAULT_PROMPT_LOG = "docs/codex_llm_iq_prompt_log.jsonl"
DEFAULT_RESULTS_PATH = "docs/codex_llm_iq_results.json"
DEFAULT_ASSESSMENT_PATH = "docs/codex_llm_iq_assessment.json"
DEFAULT_ITEM_REPORT_PATH = "docs/codex_llm_iq_item_report.csv"
DEFAULT_SEARCH_MODE = "auto"
DEFAULT_TIMEOUT_SEC = 45
DEFAULT_WORKDIR = "/tmp"
DEFAULT_MAX_INPUT_VARS = 32
DEFAULT_MAX_INPUT_VARS_SCALE = True
DEFAULT_GRID_OUTPUT_DIR = "docs/codex_llm_iq_grid"
DEFAULT_GRID_SUMMARY_PATH = "docs/codex_llm_iq_grid_summary.json"
DEFAULT_TIMEOUT_RETRY_MULTIPLIER = 2.0
MIN_CONFIRM_INSTANCES = 2
MIN_GATE_COUNT = 8
MAX_GATE_COUNT = 256
GATE_MULTIPLIER = 3
CERTIFIED_METHOD = "strategy_circuit_selector"


def _negate(lit: int) -> int:
    return -lit


def _and_gate(a: int, b: int, next_var: int, clauses: list) -> Tuple[int, int]:
    out = next_var
    next_var += 1
    clauses.append([_negate(a), _negate(b), out])
    clauses.append([a, _negate(out)])
    clauses.append([b, _negate(out)])
    return out, next_var


def _or_gate(a: int, b: int, next_var: int, clauses: list) -> Tuple[int, int]:
    out = next_var
    next_var += 1
    clauses.append([a, b, _negate(out)])
    clauses.append([_negate(a), out])
    clauses.append([_negate(b), out])
    return out, next_var


def _alias_gate(lit: int, next_var: int, clauses: list) -> Tuple[int, int]:
    out = next_var
    next_var += 1
    clauses.append([_negate(out), lit])
    clauses.append([out, _negate(lit)])
    return out, next_var


def _lit_value(lit: int, values: Dict[int, bool]) -> bool:
    base = values[abs(lit)]
    return base if lit > 0 else not base


def _build_selector(
    universals: list,
    pattern: Dict[int, bool],
    next_var: int,
    clauses: list,
) -> Tuple[int, int]:
    if not universals:
        selector = next_var
        next_var += 1
        clauses.append([selector])
        return selector, next_var

    selector_inputs = []
    for var in universals:
        lit = var if pattern[var] else -var
        alias, next_var = _alias_gate(lit, next_var, clauses)
        selector_inputs.append(alias)

    current = selector_inputs
    while len(current) > 1:
        b = current.pop()
        a = current.pop()
        out, next_var = _and_gate(a, b, next_var, clauses)
        current.append(out)
    return current[0], next_var


def _build_random_circuit(
    universals: list,
    rng: random.Random,
    gate_count: int,
    next_var: int,
    clauses: list,
) -> Tuple[int, int, list]:
    if not universals:
        out = next_var
        next_var += 1
        gate_defs = []
        return out, next_var, gate_defs

    pool = list(universals)
    gate_defs = []
    for _ in range(gate_count):
        a = rng.choice(pool)
        if rng.choice([True, False]):
            a = -a
        b = rng.choice(pool)
        if rng.choice([True, False]):
            b = -b
        if rng.choice([True, False]):
            out, next_var = _and_gate(a, b, next_var, clauses)
            op = "and"
        else:
            out, next_var = _or_gate(a, b, next_var, clauses)
            op = "or"
        gate_defs.append((op, out, a, b))
        pool.append(out)
    return gate_defs[-1][1], next_var, gate_defs


def _evaluate_circuit(
    gate_defs: list,
    assignment: Dict[int, bool],
) -> bool:
    values = dict(assignment)
    for op, out, a, b in gate_defs:
        a_val = _lit_value(a, values)
        b_val = _lit_value(b, values)
        if op == "and":
            values[out] = a_val and b_val
        else:
            values[out] = a_val or b_val
    if not gate_defs:
        raise ValueError("gate_defs must be non-empty when evaluating a circuit")
    return values[gate_defs[-1][1]]


def _last_exist_block_index(blocks: list) -> Optional[int]:
    indices = [idx for idx, block in enumerate(blocks) if block["quantifier"] == "exists"]
    return max(indices) if indices else None


def _append_existential_vars(blocks: list, var_ids: list) -> None:
    if not var_ids:
        return
    idx = _last_exist_block_index(blocks)
    if idx is None:
        raise ValueError("no existential block available for new variables")
    blocks[idx]["variables"].extend(var_ids)


def build_problem(level, instance_index, *, max_input_vars):
    qclass = CLASS_CYCLE[instance_index % len(CLASS_CYCLE)]
    num_vars = level + 1
    num_clauses = 1
    seed = level * 100 + instance_index
    rng = random.Random(seed)
    base_problem = generator.generate_qbf_problem(
        level=level,
        qclass=qclass,
        num_vars=num_vars,
        num_clauses=num_clauses,
        clause_size=2,
        seed=seed,
    )
    blocks = base_problem["quantifier_blocks"]
    target_truth = rng.choice([True, False])
    last_exist_idx = _last_exist_block_index(blocks)

    if last_exist_idx is None:
        clauses = []
        first_var = blocks[0]["variables"][0]
        if target_truth:
            clauses.append([first_var, -first_var])
        else:
            clauses.append([first_var])
        base_problem["clauses"] = clauses
        base_problem["num_clauses"] = len(clauses)
        base_problem["clause_size"] = max(len(clause) for clause in clauses)
        return (
            base_problem,
            qclass,
            seed,
            num_vars,
            len(clauses),
            base_problem["clause_size"],
            target_truth,
        )

    universals = []
    for idx, block in enumerate(blocks):
        if idx >= last_exist_idx:
            break
        if block["quantifier"] == "forall":
            universals.extend(block["variables"])

    if len(universals) > max_input_vars:
        rng.shuffle(universals)
        universals = universals[:max_input_vars]

    pattern = {var: rng.choice([True, False]) for var in universals}
    clauses = []
    next_var = base_problem["num_vars"] + 1
    gate_count = max(MIN_GATE_COUNT, min(MAX_GATE_COUNT, len(universals) * GATE_MULTIPLIER))
    output_var, next_var, gate_defs = _build_random_circuit(
        universals, rng, gate_count, next_var, clauses
    )

    if universals:
        output_value = _evaluate_circuit(gate_defs, pattern)
    else:
        output_value = rng.choice([True, False])
        clauses.append([output_var] if output_value else [-output_var])

    selector_var, next_var = _build_selector(universals, pattern, next_var, clauses)

    output_lit = output_var if output_value else -output_var
    if target_truth:
        constraint_lit = output_lit
    else:
        constraint_lit = -output_lit
    clauses.append([-selector_var, constraint_lit])

    rng.shuffle(clauses)
    new_vars = list(range(base_problem["num_vars"] + 1, next_var))
    _append_existential_vars(blocks, new_vars)
    base_problem["num_vars"] = next_var - 1
    base_problem["clauses"] = clauses
    base_problem["num_clauses"] = len(clauses)
    base_problem["clause_size"] = max(len(clause) for clause in clauses)
    return (
        base_problem,
        qclass,
        seed,
        base_problem["num_vars"],
        len(clauses),
        base_problem["clause_size"],
        target_truth,
    )


def build_prompt(problem):
    qdimacs = generator.to_qdimacs(problem)
    return (
        "You are solving a quantified Boolean formula (QBF). Your output should be the word "
        "true, or the word false, depending on the truth value of the formula.\n"
        "The formula is specified in the QDIMACS format.\n"
        "Variables are numbered 1..N; negative literals mean negation.\n"
        "Do not run tools or commands; answer directly.\n"
        "QDIMACS:\n"
        f"{qdimacs}"
    )


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        snippet = text[start : end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            return None


def _extract_answer(text: str) -> Optional[bool]:
    payload = _extract_json(text)
    if isinstance(payload, dict):
        answer = payload.get("answer")
        if isinstance(answer, bool):
            return answer
    lowered = text.lower()
    match = re.search(r"answer[^a-z0-9]*(true|false)", lowered)
    if match:
        return match.group(1) == "true"
    tokens = re.findall(r"\btrue\b|\bfalse\b", lowered)
    if not tokens:
        return None
    if len(set(tokens)) == 1:
        return tokens[0] == "true"
    for line in reversed(text.splitlines()):
        stripped = line.strip().strip(" \t\r\n\"'.,;:()[]{}")
        if stripped.lower() in ("true", "false"):
            return stripped.lower() == "true"
    return None


def _run_codex(
    prompt: str,
    output_path: Path,
    *,
    timeout_sec: int,
    workdir: str,
) -> Tuple[int, str, str, bool]:
    command = [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--color",
        "never",
        "--skip-git-repo-check",
        "-C",
        workdir,
        "--output-last-message",
        str(output_path),
        "-",
    ]
    def _coerce_text(value):
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout_sec)
        return proc.returncode, _coerce_text(stdout), _coerce_text(stderr), False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
        return 124, _coerce_text(stdout), _coerce_text(stderr), True


def _read_response(path: Path) -> str:
    if path.exists():
        text = path.read_text(encoding="utf-8")
        path.unlink()
        return text
    return ""


def _run_codex_with_retry(
    prompt: str,
    output_path: Path,
    *,
    timeout_sec: int,
    timeout_retry_sec: int,
    workdir: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    first_returncode, first_stdout, first_stderr, first_timed_out = _run_codex(
        prompt,
        output_path,
        timeout_sec=timeout_sec,
        workdir=workdir,
    )
    first_response = _read_response(output_path)
    attempt = {
        "returncode": first_returncode,
        "stdout": first_stdout,
        "stderr": first_stderr,
        "timed_out": first_timed_out,
        "response": first_response,
        "timeout_sec": timeout_sec,
    }

    retry = {
        "used": False,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "timed_out": None,
        "response": "",
        "timeout_sec": timeout_retry_sec,
    }

    if first_timed_out and timeout_retry_sec > timeout_sec:
        retry_path = output_path.with_suffix(output_path.suffix + ".retry")
        retry_used = True
        retry_returncode, retry_stdout, retry_stderr, retry_timed_out = _run_codex(
            prompt,
            retry_path,
            timeout_sec=timeout_retry_sec,
            workdir=workdir,
        )
        retry_response = _read_response(retry_path)
        retry.update(
            {
                "used": retry_used,
                "returncode": retry_returncode,
                "stdout": retry_stdout,
                "stderr": retry_stderr,
                "timed_out": retry_timed_out,
                "response": retry_response,
            }
        )
    return attempt, retry


def _record_prompt(log_path: Path, payload: Dict[str, Any]) -> None:
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _evaluate_level(
    level: int,
    instances_per_level: int,
    *,
    total_items: int,
    results: list,
    prompt_log_path: Path,
    results_path: Path,
    timeout_sec: int,
    timeout_retry_sec: int,
    workdir: str,
    max_input_vars: int,
) -> Tuple[int, bool]:
    solved = True
    for instance_index in range(instances_per_level):
        total_items += 1
        (
            problem,
            qclass,
            seed,
            num_vars,
            num_clauses,
            clause_size,
            truth_value,
        ) = build_problem(level, instance_index, max_input_vars=max_input_vars)
        prompt = build_prompt(problem)
        response_path = (
            results_path.parent / f"codex_llm_response_{level}_{instance_index}.txt"
        )
        attempt, retry = _run_codex_with_retry(
            prompt,
            response_path,
            timeout_sec=timeout_sec,
            timeout_retry_sec=timeout_retry_sec,
            workdir=workdir,
        )
        retry_used = retry["used"]
        response_text = retry["response"] if retry_used else attempt["response"]
        timed_out = retry["timed_out"] if retry_used else attempt["timed_out"]
        returncode = retry["returncode"] if retry_used else attempt["returncode"]
        answer = _extract_answer(response_text)
        valid = answer is not None
        correct = bool(valid and answer == truth_value)
        solved = solved and correct

        item = {
            "id": total_items,
            "level": level,
            "qclass": qclass,
            "seed": seed,
            "num_vars": num_vars,
            "num_clauses": num_clauses,
            "clause_size": clause_size,
            "truth": truth_value,
            "model_answer": answer,
            "valid_response": valid,
            "correct": correct,
            "codex_returncode": returncode,
            "timed_out": timed_out,
            "timeout_sec": timeout_sec,
            "timeout_retry_sec": timeout_retry_sec if retry_used else None,
            "initial_timed_out": attempt["timed_out"],
            "retry_used": retry_used,
            "retry_timed_out": retry["timed_out"] if retry_used else None,
            "codex_returncode_initial": attempt["returncode"],
            "codex_returncode_retry": retry["returncode"] if retry_used else None,
        }
        results.append(item)

        _record_prompt(
            prompt_log_path,
            {
                "id": total_items,
                "level": level,
                "prompt": prompt,
                "response": response_text.strip(),
                "response_initial": attempt["response"].strip(),
                "response_retry": retry["response"].strip() if retry_used else "",
                "stdout": (retry["stdout"] if retry_used else attempt["stdout"]).strip(),
                "stderr": (retry["stderr"] if retry_used else attempt["stderr"]).strip(),
                "stdout_initial": attempt["stdout"].strip(),
                "stderr_initial": attempt["stderr"].strip(),
                "stdout_retry": retry["stdout"].strip() if retry_used else "",
                "stderr_retry": retry["stderr"].strip() if retry_used else "",
                "truth": truth_value,
                "model_answer": answer,
                "correct": correct,
                "timed_out": timed_out,
                "initial_timed_out": attempt["timed_out"],
                "retry_used": retry_used,
                "retry_timed_out": retry["timed_out"] if retry_used else None,
                "timeout_sec": timeout_sec,
                "timeout_retry_sec": timeout_retry_sec if retry_used else None,
            },
        )
    return total_items, solved


def _level_summary(results):
    per_level = {}
    for entry in results:
        level = entry["level"]
        data = per_level.setdefault(level, {"total": 0, "correct": 0})
        data["total"] += 1
        data["correct"] += 1 if entry["correct"] else 0
    summary = []
    highest = 0
    for level in sorted(per_level):
        solved = per_level[level]["correct"] == per_level[level]["total"]
        if solved:
            highest = level
        summary.append(
            {
                "level": level,
                "total": per_level[level]["total"],
                "correct": per_level[level]["correct"],
                "solved": solved,
            }
        )
    return highest, summary


def _auto_search(
    max_level: int,
    evaluate_level,
) -> Tuple[int, list, bool]:
    steps = []
    low = 0
    high = 1
    capped = False

    while True:
        if high > max_level:
            high = max_level
        solved = evaluate_level(high)
        steps.append({"phase": "expand", "level": high, "solved": solved})
        if not solved:
            break
        low = high
        if high >= max_level:
            capped = True
            break
        high = min(max_level, high * 2)

    if capped:
        return low, steps, True

    failed_level = steps[-1]["level"]
    low_bound = low + 1
    high_bound = failed_level - 1
    if low_bound > high_bound:
        return low, steps, False

    lo = low
    hi = high_bound
    while lo < hi:
        mid = (lo + hi + 1) // 2
        solved = evaluate_level(mid)
        steps.append({"phase": "binary", "level": mid, "solved": solved})
        if solved:
            lo = mid
        else:
            hi = mid - 1
    return lo, steps, False


def _parse_int_list(value: Optional[str]) -> list:
    if value is None:
        return []
    cleaned = value.strip()
    if not cleaned:
        return []
    entries = []
    for part in cleaned.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            entries.append(int(part))
        except ValueError as exc:
            raise SystemExit(f"invalid integer list value: {part}") from exc
    return entries


def _grid_basename(search_mode: str, timeout_sec: int, max_input_vars: int) -> str:
    return f"codex_llm_iq_{search_mode}_t{timeout_sec}_v{max_input_vars}"


def _max_input_vars_for_level(level: int, max_input_vars: int, scale_with_level: bool) -> int:
    if scale_with_level:
        return min(max_input_vars, level)
    return max_input_vars


def _resolve_timeout_retry_sec(
    timeout_sec: int,
    timeout_retry_sec: int,
    timeout_retry_multiplier: float,
) -> int:
    if timeout_retry_sec:
        if timeout_retry_sec <= timeout_sec:
            raise SystemExit("--timeout-retry-sec must exceed --timeout-sec")
        return timeout_retry_sec
    if timeout_retry_multiplier <= 1:
        return timeout_sec + 1
    return max(timeout_sec + 1, int(math.ceil(timeout_sec * timeout_retry_multiplier)))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Codex LLM IQ via QBF prompts.")
    parser.add_argument("--max-level", type=int, default=DEFAULT_MAX_LEVEL)
    parser.add_argument("--instances-per-level", type=int, default=DEFAULT_INSTANCES_PER_LEVEL)
    parser.add_argument(
        "--search-mode",
        choices=("linear", "binary", "auto"),
        default=DEFAULT_SEARCH_MODE,
        help="Level evaluation strategy: linear sweep, binary search, or auto expand + binary.",
    )
    parser.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument(
        "--timeout-retry-sec",
        type=int,
        default=0,
        help="Retry timeout (seconds) if the initial attempt times out.",
    )
    parser.add_argument(
        "--timeout-retry-multiplier",
        type=float,
        default=DEFAULT_TIMEOUT_RETRY_MULTIPLIER,
        help="Multiplier for retry timeout when --timeout-retry-sec is not set.",
    )
    parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    parser.add_argument("--max-input-vars", type=int, default=DEFAULT_MAX_INPUT_VARS)
    parser.add_argument(
        "--max-input-vars-scale",
        dest="max_input_vars_scale",
        action="store_true",
        default=DEFAULT_MAX_INPUT_VARS_SCALE,
        help="Scale max-input-vars with level (cap at --max-input-vars).",
    )
    parser.add_argument(
        "--max-input-vars-fixed",
        dest="max_input_vars_scale",
        action="store_false",
        help="Use a fixed --max-input-vars value for all levels.",
    )
    parser.add_argument("--prompt-log", default=DEFAULT_PROMPT_LOG)
    parser.add_argument("--results-path", default=DEFAULT_RESULTS_PATH)
    parser.add_argument("--assessment-path", default=DEFAULT_ASSESSMENT_PATH)
    parser.add_argument("--item-report-path", default=DEFAULT_ITEM_REPORT_PATH)
    parser.add_argument(
        "--grid-timeouts",
        default="",
        help="Comma-separated timeout values to run a grid search.",
    )
    parser.add_argument(
        "--grid-max-input-vars",
        default="",
        help="Comma-separated max-input-var caps to run a grid search.",
    )
    parser.add_argument("--grid-output-dir", default=DEFAULT_GRID_OUTPUT_DIR)
    parser.add_argument("--grid-summary-path", default=DEFAULT_GRID_SUMMARY_PATH)
    return parser.parse_args()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_item_report(path: Path, results) -> None:
    fieldnames = [
        "id",
        "level",
        "truth",
        "model_answer",
        "correct",
        "valid_response",
        "timed_out",
        "initial_timed_out",
        "retry_used",
        "retry_timed_out",
        "timeout_sec",
        "timeout_retry_sec",
        "codex_returncode",
        "codex_returncode_initial",
        "codex_returncode_retry",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for entry in results:
            answer = entry["model_answer"]
            writer.writerow(
                {
                    "id": entry["id"],
                    "level": entry["level"],
                    "truth": entry["truth"],
                    "model_answer": "" if answer is None else answer,
                    "correct": entry["correct"],
                    "valid_response": entry["valid_response"],
                    "timed_out": entry["timed_out"],
                    "initial_timed_out": entry["initial_timed_out"],
                    "retry_used": entry["retry_used"],
                    "retry_timed_out": entry["retry_timed_out"],
                    "timeout_sec": entry["timeout_sec"],
                    "timeout_retry_sec": entry["timeout_retry_sec"] or "",
                    "codex_returncode": entry["codex_returncode"],
                    "codex_returncode_initial": entry["codex_returncode_initial"],
                    "codex_returncode_retry": entry["codex_returncode_retry"] or "",
                }
            )


def _evaluate_run(
    *,
    max_level: int,
    instances_per_level: int,
    search_mode: str,
    timeout_sec: int,
    timeout_retry_sec: int,
    timeout_retry_multiplier: float,
    workdir: str,
    prompt_log_path: Path,
    results_path: Path,
    assessment_path: Path,
    item_report_path: Path,
    max_input_vars: int,
    max_input_vars_scale: bool,
    run_label: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if max_level <= 0:
        raise SystemExit("--max-level must be positive")
    if instances_per_level <= 0:
        raise SystemExit("--instances-per-level must be positive")
    if timeout_sec <= 0:
        raise SystemExit("--timeout-sec must be positive")
    if timeout_retry_sec < 0:
        raise SystemExit("--timeout-retry-sec must be non-negative")
    if timeout_retry_multiplier <= 0:
        raise SystemExit("--timeout-retry-multiplier must be positive")
    if max_input_vars <= 0:
        raise SystemExit("--max-input-vars must be positive")

    _ensure_parent(prompt_log_path)
    _ensure_parent(results_path)
    _ensure_parent(assessment_path)
    _ensure_parent(item_report_path)

    if prompt_log_path.exists():
        prompt_log_path.unlink()
    if results_path.exists():
        results_path.unlink()
    if assessment_path.exists():
        assessment_path.unlink()
    if item_report_path.exists():
        item_report_path.unlink()

    effective_instances_per_level = max(instances_per_level, MIN_CONFIRM_INSTANCES)
    retry_timeout_sec = _resolve_timeout_retry_sec(
        timeout_sec, timeout_retry_sec, timeout_retry_multiplier
    )

    results = []
    total_items = 0
    search_steps = []
    level_cache = {}

    def evaluate_level(level: int) -> bool:
        nonlocal total_items
        if level in level_cache:
            return level_cache[level]
        level_max_input_vars = _max_input_vars_for_level(
            level, max_input_vars, max_input_vars_scale
        )
        total_items, solved = _evaluate_level(
            level,
            effective_instances_per_level,
            total_items=total_items,
            results=results,
            prompt_log_path=prompt_log_path,
            results_path=results_path,
            timeout_sec=timeout_sec,
            timeout_retry_sec=retry_timeout_sec,
            workdir=workdir,
            max_input_vars=level_max_input_vars,
        )
        level_cache[level] = solved
        return solved

    capped_by_max_level = False
    highest_override = None
    if search_mode == "linear":
        for level in range(1, max_level + 1):
            solved = evaluate_level(level)
            search_steps.append({"level": level, "solved": solved})
    elif search_mode == "binary":
        low = 0
        high = max_level
        while low < high:
            mid = (low + high + 1) // 2
            solved = evaluate_level(mid)
            search_steps.append({"level": mid, "solved": solved})
            if solved:
                low = mid
            else:
                high = mid - 1
    elif search_mode == "auto":
        highest_override, search_steps, capped_by_max_level = _auto_search(
            max_level, evaluate_level
        )
    else:
        raise SystemExit(f"unsupported search mode: {search_mode}")

    difficulty_raw = [entry["level"] for entry in results]
    difficulty_std = psychometrics.standardize(difficulty_raw)
    responses = [entry["correct"] for entry in results]
    theta_payload = psychometrics.estimate_theta_rasch(responses, difficulty_std)
    iq_score = psychometrics.iq_from_theta(theta_payload["theta"])
    highest_solved, per_level = _level_summary(results)
    if highest_override is not None:
        highest_solved = highest_override

    evaluated_levels = sorted({entry["level"] for entry in results})
    assessment_payload = {
        "method": "rasch_1pl_map",
        "theta": theta_payload["theta"],
        "theta_se": theta_payload["se"],
        "log_posterior": theta_payload["log_posterior"],
        "iq_score": iq_score,
        "iq_score_rounded": round(iq_score, 2),
        "items": len(results),
        "correct": sum(responses),
        "accuracy": sum(responses) / len(responses) if responses else 0.0,
        "highest_solved_level": highest_solved,
        "difficulty_mean": sum(difficulty_raw) / len(difficulty_raw),
        "difficulty_std": math.sqrt(
            sum((value - sum(difficulty_raw) / len(difficulty_raw)) ** 2 for value in difficulty_raw)
            / len(difficulty_raw)
        ),
        "search_mode": search_mode,
        "timeout_retry_sec": retry_timeout_sec,
        "timeout_retry_multiplier": timeout_retry_multiplier,
        "capped_by_max_level": capped_by_max_level,
        "evaluated_levels": evaluated_levels,
        "search_steps": search_steps,
        "levels": per_level,
    }

    metadata = {
        "levels": list(range(1, max_level + 1)),
        "evaluated_levels": evaluated_levels,
        "instances_per_level": effective_instances_per_level,
        "min_confirm_instances": MIN_CONFIRM_INSTANCES,
        "class_cycle": CLASS_CYCLE,
        "scoring": "correct == ground truth",
        "ground_truth_method": "certified_construction",
        "certified_method": CERTIFIED_METHOD,
        "certified_max_input_vars": max_input_vars,
        "certified_max_input_vars_cap": max_input_vars,
        "certified_max_input_vars_scale": max_input_vars_scale,
        "certified_gate_multiplier": GATE_MULTIPLIER,
        "certified_gate_bounds": [MIN_GATE_COUNT, MAX_GATE_COUNT],
        "timeout_sec": timeout_sec,
        "timeout_retry_sec": retry_timeout_sec,
        "timeout_retry_multiplier": timeout_retry_multiplier,
        "workdir": workdir,
        "search_mode": search_mode,
        "search_steps": search_steps,
        "capped_by_max_level": capped_by_max_level,
    }
    if instances_per_level != effective_instances_per_level:
        metadata["requested_instances_per_level"] = instances_per_level
    if run_label:
        metadata["run_label"] = run_label

    results_payload = {
        "metadata": metadata,
        "results": results,
    }

    results_path.write_text(
        json.dumps(results_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    assessment_path.write_text(
        json.dumps(assessment_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_item_report(item_report_path, results)

    return assessment_payload, results_payload


def main():
    args = _parse_args()
    grid_timeouts = _parse_int_list(args.grid_timeouts)
    grid_max_input_vars = _parse_int_list(args.grid_max_input_vars)
    grid_mode = bool(grid_timeouts or grid_max_input_vars)

    if grid_mode:
        if not grid_timeouts or not grid_max_input_vars:
            raise SystemExit(
                "grid search requires both --grid-timeouts and --grid-max-input-vars"
            )
        for timeout in grid_timeouts:
            if timeout <= 0:
                raise SystemExit("--grid-timeouts must be positive")
        for max_input_vars in grid_max_input_vars:
            if max_input_vars <= 0:
                raise SystemExit("--grid-max-input-vars must be positive")

        output_dir = Path(args.grid_output_dir)
        summary_path = Path(args.grid_summary_path)
        _ensure_parent(summary_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        if summary_path.exists():
            summary_path.unlink()

        runs = []
        for timeout_sec in grid_timeouts:
            for max_input_vars in grid_max_input_vars:
                run_label = _grid_basename(args.search_mode, timeout_sec, max_input_vars)
                prompt_log_path = output_dir / f"{run_label}_prompt_log.jsonl"
                results_path = output_dir / f"{run_label}_results.json"
                assessment_path = output_dir / f"{run_label}_assessment.json"
                item_report_path = output_dir / f"{run_label}_item_report.csv"
                assessment_payload, results_payload = _evaluate_run(
                    max_level=args.max_level,
                    instances_per_level=args.instances_per_level,
                    search_mode=args.search_mode,
                    timeout_sec=timeout_sec,
                    timeout_retry_sec=args.timeout_retry_sec,
                    timeout_retry_multiplier=args.timeout_retry_multiplier,
                    workdir=args.workdir,
                    prompt_log_path=prompt_log_path,
                    results_path=results_path,
                    assessment_path=assessment_path,
                    item_report_path=item_report_path,
                    max_input_vars=max_input_vars,
                    max_input_vars_scale=args.max_input_vars_scale,
                    run_label=run_label,
                )
                metadata = results_payload["metadata"]
                runs.append(
                    {
                        "run_label": run_label,
                        "timeout_sec": timeout_sec,
                        "timeout_retry_sec": metadata.get("timeout_retry_sec"),
                        "timeout_retry_multiplier": metadata.get("timeout_retry_multiplier"),
                        "max_input_vars": max_input_vars,
                        "max_input_vars_scale": metadata.get("certified_max_input_vars_scale"),
                        "search_mode": args.search_mode,
                        "max_level": args.max_level,
                        "instances_per_level": metadata.get("instances_per_level"),
                        "requested_instances_per_level": metadata.get(
                            "requested_instances_per_level"
                        ),
                        "min_confirm_instances": metadata.get("min_confirm_instances"),
                        "highest_solved_level": assessment_payload["highest_solved_level"],
                        "iq_score": assessment_payload["iq_score_rounded"],
                        "accuracy": assessment_payload["accuracy"],
                        "items": assessment_payload["items"],
                        "capped_by_max_level": assessment_payload["capped_by_max_level"],
                        "evaluated_levels": assessment_payload["evaluated_levels"],
                        "assessment_path": str(assessment_path),
                        "results_path": str(results_path),
                        "prompt_log_path": str(prompt_log_path),
                        "item_report_path": str(item_report_path),
                    }
                )

        summary_payload = {
            "grid": {
                "timeouts_sec": grid_timeouts,
                "max_input_vars": grid_max_input_vars,
                "max_input_vars_scale": args.max_input_vars_scale,
                "search_mode": args.search_mode,
                "max_level": args.max_level,
                "instances_per_level": args.instances_per_level,
                "min_confirm_instances": MIN_CONFIRM_INSTANCES,
                "timeout_retry_sec": args.timeout_retry_sec,
                "timeout_retry_multiplier": args.timeout_retry_multiplier,
            },
            "runs": runs,
        }
        summary_path.write_text(
            json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return

    prompt_log_path = Path(args.prompt_log)
    results_path = Path(args.results_path)
    assessment_path = Path(args.assessment_path)
    item_report_path = Path(args.item_report_path)

    _evaluate_run(
        max_level=args.max_level,
        instances_per_level=args.instances_per_level,
        search_mode=args.search_mode,
        timeout_sec=args.timeout_sec,
        timeout_retry_sec=args.timeout_retry_sec,
        timeout_retry_multiplier=args.timeout_retry_multiplier,
        workdir=args.workdir,
        prompt_log_path=prompt_log_path,
        results_path=results_path,
        assessment_path=assessment_path,
        item_report_path=item_report_path,
        max_input_vars=args.max_input_vars,
        max_input_vars_scale=args.max_input_vars_scale,
    )


if __name__ == "__main__":
    main()
