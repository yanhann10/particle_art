#!/usr/bin/env python3
"""Static pre-deployment HTML checker.

Runs regex patterns from standard.json against generated piece HTML.
Called by mutate.py and improv_tick.py before the render gate so
banned code patterns never reach git.

Standalone usage:
    python3 scripts/precheck.py pieces/<id>/              # check a piece dir
    python3 scripts/precheck.py pieces/<id>/index.html   # check a file directly
    python3 scripts/precheck.py --all                     # check all pieces in gallery

Exit codes (standalone):
    0  passed
    1  hard violation (reject)
    2  warnings only

Imported usage:
    from precheck import run
    result = run(html_string)
    # result = {"passed": bool, "violations": [...], "warnings": [...]}
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STANDARD = REPO / "standard.json"


def _load_standard() -> dict:
    if not STANDARD.exists():
        return {"hard_bans": [], "warnings": []}
    try:
        return json.loads(STANDARD.read_text())
    except Exception:
        return {"hard_bans": [], "warnings": []}


def run(html: str) -> dict:
    """Check HTML against standard.json patterns.

    Returns:
        {
            "passed":     bool,              # False if any hard violation
            "violations": [{"id", "description", "match"}],
            "warnings":   [{"id", "description", "match"}],
        }
    """
    standard = _load_standard()
    violations, warnings = [], []

    for ban in standard.get("hard_bans", []):
        pat = ban.get("pattern", "")
        if not pat:
            continue
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            violations.append({
                "id": ban["id"],
                "description": ban["description"],
                "match": m.group(0)[:120],
                "fix": ban.get("fix", ""),
            })

    for warn in standard.get("warnings", []):
        pat = warn.get("pattern", "")
        if not pat:
            continue
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            warnings.append({
                "id": warn["id"],
                "description": warn["description"],
                "match": m.group(0)[:120],
                "note": warn.get("note", ""),
            })

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "warnings": warnings,
    }


def check_file(path: Path) -> dict:
    if path.is_dir():
        path = path / "index.html"
    return run(path.read_text())


def _print_result(piece_id: str, result: dict) -> None:
    status = "PASS" if result["passed"] else "FAIL"
    v_count = len(result["violations"])
    w_count = len(result["warnings"])
    print(f"[{status}] {piece_id}  violations={v_count}  warnings={w_count}")
    for v in result["violations"]:
        print(f"  REJECT  {v['id']}: {v['description']}")
        print(f"          match: {v['match']!r}")
        if v.get("fix"):
            print(f"          fix:   {v['fix']}")
    for w in result["warnings"]:
        print(f"  WARN    {w['id']}: {w['description']}")
        print(f"          match: {w['match']!r}")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="piece dir or HTML file to check")
    ap.add_argument("--all", action="store_true", help="check all pieces in gallery")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args()

    if args.all:
        pieces_dir = REPO / "pieces"
        results = {}
        fail_count = 0
        for p in sorted(pieces_dir.iterdir()):
            html_path = p / "index.html"
            if not html_path.exists():
                continue
            r = check_file(html_path)
            results[p.name] = r
            if not r["passed"]:
                fail_count += 1
            if not args.json:
                _print_result(p.name, r)
        if args.json:
            print(json.dumps(results, indent=2))
        print(f"\n{len(results)} pieces checked — {fail_count} hard violations")
        sys.exit(1 if fail_count else 0)

    if not args.target:
        ap.print_help()
        sys.exit(2)

    path = Path(args.target)
    result = check_file(path)
    piece_id = path.parent.name if path.name == "index.html" else path.name
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_result(piece_id, result)
    if not result["passed"]:
        sys.exit(1)
    elif result["warnings"]:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
