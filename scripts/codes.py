"""Generate short 3-char alphanumeric ids for pieces, collision-checked against lineage."""
import json
import random
import string
from pathlib import Path

ALPHABET = string.ascii_lowercase + string.digits  # 36 chars
LEN = 3
# 36^3 = 46,656 possible. Plenty for portfolio scale.


def existing_ids(lineage_path: Path) -> set[str]:
    if not lineage_path.exists():
        return set()
    data = json.loads(lineage_path.read_text())
    return {p["id"] for p in data.get("pieces", [])}


def generate(lineage_path: Path, n: int = 1) -> list[str]:
    used = existing_ids(lineage_path)
    out = []
    rng = random.SystemRandom()
    while len(out) < n:
        candidate = "".join(rng.choice(ALPHABET) for _ in range(LEN))
        if candidate in used or candidate in out:
            continue
        # bias against codes that look like numbers only or letters only — visual variety
        if candidate.isdigit() or candidate.isalpha():
            if rng.random() < 0.5:
                continue
        out.append(candidate)
    return out


if __name__ == "__main__":
    import sys
    repo = Path(__file__).resolve().parent.parent
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print("\n".join(generate(repo / "lineage.json", n=n)))
