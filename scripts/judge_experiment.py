#!/usr/bin/env python3
"""Analyze the Gemini-vs-Claude video-judge experiment (issue #13).

Ground truth: user marks in preferences.json — favorites (should score high)
vs drops (should score low). The judges never saw the marks.

Metrics per backend:
    AUC        — P(random favorite outscores random drop), Mann-Whitney
    best-acc   — accuracy at the best integer threshold
    means      — mean score per group

Usage:
    scripts/judge_experiment.py --fav-ids fav.txt --drop-ids drop.txt \
        --results gemini=/tmp/pa_eval/gemini.jsonl claude=/tmp/pa_eval/claude.jsonl
"""
import argparse
import json
from pathlib import Path


def load_scores(path: Path) -> dict[str, float]:
    out = {}
    for line in path.read_text().splitlines():
        try:
            d = json.loads(line)
            if "score" in d:
                out[d["id"]] = float(d["score"])
        except Exception:
            pass
    return out


def auc(pos: list[float], neg: list[float]) -> float:
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def best_threshold(pos: list[float], neg: list[float]) -> tuple[float, float]:
    best_t, best_a = 0, 0.0
    for t in [x * 0.5 for x in range(2, 21)]:
        acc = (sum(p >= t for p in pos) + sum(n < t for n in neg)) / (len(pos) + len(neg))
        if acc > best_a:
            best_a, best_t = acc, t
    return best_t, best_a


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fav-ids", type=Path, required=True)
    ap.add_argument("--drop-ids", type=Path, required=True)
    ap.add_argument("--results", nargs="+", required=True, metavar="NAME=PATH")
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()

    favs = set(args.fav_ids.read_text().split())
    drops = set(args.drop_ids.read_text().split())

    report = {}
    print(f"{'backend':<10} {'n_fav':>5} {'n_drop':>6} {'AUC':>6} {'thr':>5} {'acc':>6} "
          f"{'fav_mean':>8} {'drop_mean':>9}")
    for spec in args.results:
        name, path = spec.split("=", 1)
        scores = load_scores(Path(path))
        pos = [scores[i] for i in favs if i in scores]
        neg = [scores[i] for i in drops if i in scores]
        if not pos or not neg:
            print(f"{name:<10} insufficient data ({len(pos)} fav / {len(neg)} drop)")
            continue
        a = auc(pos, neg)
        t, acc = best_threshold(pos, neg)
        fm, dm = sum(pos) / len(pos), sum(neg) / len(neg)
        print(f"{name:<10} {len(pos):>5} {len(neg):>6} {a:>6.3f} {t:>5.1f} {acc:>6.3f} "
              f"{fm:>8.2f} {dm:>9.2f}")
        report[name] = {"n_fav": len(pos), "n_drop": len(neg), "auc": round(a, 4),
                        "best_threshold": t, "best_accuracy": round(acc, 4),
                        "fav_mean": round(fm, 3), "drop_mean": round(dm, 3)}

    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
