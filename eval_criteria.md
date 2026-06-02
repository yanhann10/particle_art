# eval_criteria.md — IMMUTABLE evaluation criteria for the autonomous evolve loop

> The agent may NEVER edit this file. A generated piece is COMMITTED only if it passes
> every binary criterion below; otherwise it is DISCARDED (logged to scripts/rejects/)
> and a new hypothesis is formed next tick. This is the fixed contract of step 4.

## Fixed / immutable rules
- **Provider:** Claude Max subscription ONLY (`claude -p`). On any usage-limit/auth failure
  the tick is skipped — never fall back to paid Bedrock. Guarantees the spend buffer.
- **Time box:** <= 5 min of model work per experiment (enforced by `timeout` + the 240s
  per-call cap in lib_claude). Results stay directly comparable.
- **Cadence:** one experiment every 45 min (paced under the Max 5x 5-hour + weekly limits
  with >=10% headroom; loop self-throttles on limit).
- **Branch:** commits/pushes go to `staging` only. The live gallery (main) is human-curated.
- **No-repeat:** never repeat the same (parent_id x directive_id).

## Binary pass/fail gate (all must pass to COMMIT)
1. **Renders non-empty:** non-background pixels >= 0.5% (validate_render.py).
2. **Contrast:** grayscale stddev > 12 AND luma dynamic range (p99-p1) > 35.
3. **Precheck hard-bans:** 0 violations in precheck.py / standard.json
   (no y-axis autorotate, no per-frame camera/geometry Math.random shake, etc.).
4. **Aesthetic critic:** critic score meets the standing bar and does not regress the
   parent (LLM-as-judge in critic.py).

## Success / failure (step 4)
- **Success** -> all criteria pass -> commit to git history + push to `staging`.
- **Failure** -> any criterion fails OR the script crashes -> discard the change,
  roll back the working tree, log the reject, form a new hypothesis next tick.
