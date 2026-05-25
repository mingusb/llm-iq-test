import argparse
import json
import sys
from typing import List, Optional

from . import assessment, generator


def _parse_int_list(value: str) -> List[int]:
    parts = [part.strip() for part in value.split(",")]
    ints: List[int] = []
    for part in parts:
        if not part:
            continue
        ints.append(int(part))
    if not ints:
        raise argparse.ArgumentTypeError("expected a comma-separated list of integers")
    return ints


def _write_output(text: str, output_path: Optional[str]) -> None:
    if output_path:
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polynomial hierarchy IQ harness utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen_parser = subparsers.add_parser("generate", help="Generate a QBF problem.")
    gen_parser.add_argument("--level", type=int, required=True, help="Polynomial hierarchy level.")
    gen_parser.add_argument("--class", dest="qclass", choices=["sigma", "pi"], required=True)
    gen_parser.add_argument("--vars", dest="num_vars", type=int, required=True)
    gen_parser.add_argument("--clauses", dest="num_clauses", type=int, required=True)
    gen_parser.add_argument("--clause-size", dest="clause_size", type=int, default=3)
    gen_parser.add_argument("--seed", type=int, default=None)
    gen_parser.add_argument("--variables-per-block", type=_parse_int_list, default=None)
    gen_parser.add_argument(
        "--format",
        choices=["qdimacs", "json"],
        default="qdimacs",
        help="Output format.",
    )
    gen_parser.add_argument("--output", "-o", default=None)

    assess_parser = subparsers.add_parser("assess", help="Assess highest solved level.")
    assess_parser.add_argument("--input", "-i", required=True, help="Path to JSON results.")
    policy = assess_parser.add_mutually_exclusive_group()
    policy.add_argument(
        "--require-all",
        action="store_true",
        help="Level is solved only if all results at that level are solved.",
    )
    policy.add_argument(
        "--require-any",
        action="store_true",
        help="Level is solved if any result at that level is solved.",
    )
    assess_parser.add_argument("--output", "-o", default=None)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "generate":
        problem = generator.generate_qbf_problem(
            level=args.level,
            qclass=args.qclass,
            num_vars=args.num_vars,
            num_clauses=args.num_clauses,
            clause_size=args.clause_size,
            seed=args.seed,
            variables_per_block=args.variables_per_block,
        )
        if args.format == "json":
            output = generator.to_json(problem)
        else:
            output = generator.to_qdimacs(problem)
        _write_output(output, args.output)
        return 0

    if args.command == "assess":
        data = _load_json(args.input)
        require_all = True
        if args.require_any:
            require_all = False
        if args.require_all:
            require_all = True
        summary = assessment.assess_highest_level(data, require_all=require_all)
        output = json.dumps(summary, indent=2, sort_keys=True) + "\n"
        _write_output(output, args.output)
        return 0

    parser.error("unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
