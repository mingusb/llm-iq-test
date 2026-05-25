import argparse
import csv
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_PATH = ROOT / "scripts" / "evaluate_codex_llm_iq.py"


def _load_evaluator():
    spec = importlib.util.spec_from_file_location("evaluate_codex_llm_iq", EVAL_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"unable to load evaluator from {EVAL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an LLM IQ prompt corpus for a range of PH levels."
    )
    parser.add_argument("--min-level", type=int, default=1)
    parser.add_argument("--max-level", type=int, default=1000)
    parser.add_argument("--output-dir", default="llm_iq_prompt_corpus")
    parser.add_argument("--answers-path", default="")
    parser.add_argument("--max-input-vars", type=int, default=0)
    parser.add_argument(
        "--max-input-vars-scale",
        dest="max_input_vars_scale",
        action="store_true",
        default=True,
        help="Scale max-input-vars with level (cap at --max-input-vars).",
    )
    parser.add_argument(
        "--max-input-vars-fixed",
        dest="max_input_vars_scale",
        action="store_false",
        help="Use a fixed --max-input-vars value for all levels.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.min_level <= 0:
        raise SystemExit("--min-level must be positive")
    if args.max_level < args.min_level:
        raise SystemExit("--max-level must be >= --min-level")

    max_input_vars = args.max_input_vars or args.max_level
    if max_input_vars <= 0:
        raise SystemExit("--max-input-vars must be positive")

    evaluator = _load_evaluator()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    width = len(str(args.max_level))
    answers_path = Path(args.answers_path) if args.answers_path else output_dir / "answers.csv"

    with answers_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["level", "answer"])
        for level in range(args.min_level, args.max_level + 1):
            instance_index = level - args.min_level
            level_max_input_vars = evaluator._max_input_vars_for_level(
                level, max_input_vars, args.max_input_vars_scale
            )
            (
                problem,
                _qclass,
                _seed,
                _num_vars,
                _num_clauses,
                _clause_size,
                truth,
            ) = evaluator.build_problem(
                level, instance_index, max_input_vars=level_max_input_vars
            )
            prompt = evaluator.build_prompt(problem)
            filename = f"level_{level:0{width}d}.txt"
            (output_dir / filename).write_text(prompt, encoding="utf-8")
            writer.writerow([level, "true" if truth else "false"])


if __name__ == "__main__":
    main()
