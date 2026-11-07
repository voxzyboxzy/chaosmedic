"""python -m chaosmedic.eval"""

from __future__ import annotations

import argparse
import json

from chaosmedic.eval.harness import RESULTS_PATH, check_report, run_eval, write_results


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the rules diagnoser on held-out incidents.")
    parser.add_argument("--check", action="store_true", help="exit non-zero if accuracy regresses")
    parser.add_argument("--min-accuracy", type=float, default=0.8)
    parser.add_argument("--write", action="store_true", help="write eval/results.json")
    args = parser.parse_args()
    report = run_eval()
    if args.check:
        check_report(report, args.min_accuracy, RESULTS_PATH)
    if args.write or not args.check:
        path = write_results(report)
        print(f"wrote {path}")
    printable = {key: value for key, value in report.items() if key != "cases"}
    print(json.dumps(printable, indent=2, sort_keys=True))
    print()
    print(f"{'incident':<32} {'predicted':<28} {'ok':<5} {'extra':<6} {'recovered'}")
    for row in report["cases"]:
        predicted = f"{row['predicted_fault']}/{row['predicted_service']}"
        print(
            f"{row['id']:<32} {predicted:<28} "
            f"{'yes' if row['correct_root_cause'] else 'no':<5} "
            f"{row['unnecessary_actions']:<6} "
            f"{'yes' if row['recovered'] else 'no'}"
        )


if __name__ == "__main__":
    main()
