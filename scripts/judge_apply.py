#!/usr/bin/env python3
"""Apply video-judge results to staging.json (issue #13).

Reads judge_video.py result jsonl, demotes pieces scoring ≤ threshold to
staging.json with the judge's verdict + resemblance. User favorites are
never demoted. With --require-prior-run, only demotes pieces ALSO demoted
by the named earlier run (2-run consistency: a piece must fail twice to
stay staged) and RESTORES pieces from that run that passed this one.

Usage:
    scripts/judge_apply.py results.jsonl --threshold 4 --run-tag video-judge-r1
    scripts/judge_apply.py r2.jsonl --threshold 4 --run-tag video-judge-r2 \
        --require-prior-run video-judge-r1
"""
import argparse
import datetime
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STAGING = REPO / "staging.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path, nargs="+")
    ap.add_argument("--threshold", type=float, default=4.0,
                    help="demote when score ≤ this (default 4)")
    ap.add_argument("--run-tag", default="")
    ap.add_argument("--require-prior-run", metavar="TAG",
                    help="2-run consistency vs this earlier run (see docstring)")
    ap.add_argument("--enrich-run", metavar="TAG",
                    help="pieces already staged under this run: attach video score/verdict/"
                         "resemblance, and RESTORE to main any scoring > threshold "
                         "(video judge overrides the static gate, e.g. autoRotate)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    staging = json.loads(STAGING.read_text())
    prefs = json.loads((REPO / "scripts" / "preferences.json").read_text())
    favorites = {k for k, v in prefs.get("marks", {}).items() if v.get("favorite")}

    results = {}
    for path in args.results:
        for line in path.read_text().splitlines():
            try:
                d = json.loads(line)
                if "score" in d:
                    results[d["id"]] = d
            except Exception:
                pass

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    staged = staging["staged"]
    demoted, restored, kept = [], [], []

    enriched = restored_enrich = 0
    for pid, r in results.items():
        if pid in favorites:
            continue
        fails = r["score"] <= args.threshold
        prior = staged.get(pid)

        # enrich mode: pieces demoted by an earlier (static) run get the video
        # verdict; video judge overrides the static gate when it scores > threshold
        if args.enrich_run and prior and prior.get("run") == args.enrich_run:
            if fails:
                prior.update({"score": r["score"], "verdict": r["verdict"],
                              "resembles": r.get("resembles"),
                              "video_judge": r.get("model"),
                              "static_reason": prior.get("verdict")})
                enriched += 1
            else:   # video judge says it's good → restore to main
                del staged[pid]
                restored_enrich += 1
            continue
        if args.require_prior_run and prior and prior.get("run") == args.require_prior_run:
            if fails:   # confirmed by both runs
                prior.update({"run": f"{args.require_prior_run}+{args.run_tag}",
                              "score": r["score"], "verdict": r["verdict"],
                              "resembles": r.get("resembles"), "judge": r.get("model"),
                              "staged_at": now})
                kept.append(pid)
            else:       # run 2 disagrees → restore to main
                del staged[pid]
                restored.append(pid)
        elif fails and pid not in staged:
            staged[pid] = {"run": args.run_tag, "judge": r.get("model"),
                           "score": r["score"], "verdict": r["verdict"],
                           "resembles": r.get("resembles"), "staged_at": now}
            demoted.append(pid)

    print(f"{len(results)} judged · threshold ≤{args.threshold:g} · "
          f"{len(demoted)} newly demoted · {len(kept)} confirmed · {len(restored)} restored")
    if args.enrich_run:
        print(f"enrich[{args.enrich_run}]: {enriched} kept+enriched · "
              f"{restored_enrich} restored to main (video judge > threshold)")
    if args.dry_run:
        return
    staging["updated_at"] = now
    STAGING.write_text(json.dumps(staging, indent=2) + "\n")
    print("wrote staging.json")


if __name__ == "__main__":
    main()
