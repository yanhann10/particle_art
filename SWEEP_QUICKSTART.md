# Hyperparameter Sweep Quick Start

## One-Time Setup (5 minutes)

```bash
bash ~/git_repo/particle_art/scripts/setup_sweep.sh
```

This:
- Initializes `.sweep_manifest.json` with all 1088 pieces (status: pending)
- Makes all scripts executable
- Shows progress summary

## Option 1: Sweep a Single Piece

```bash
/hyperparam-sweep v21
```

Outputs: `~/.particle_art/.sweep_v21.json` (winners + all results)

## Option 2: Parallel Sweep of All Pieces (1000+)

**Terminal 1:**
```bash
bash ~/git_repo/particle_art/scripts/parallel_sweep_launcher.sh session_1
```

**Terminal 2:**
```bash
bash ~/git_repo/particle_art/scripts/parallel_sweep_launcher.sh session_2
```

**Terminal 3–10** (similarly):
```bash
bash ~/git_repo/particle_art/scripts/parallel_sweep_launcher.sh session_3
...
bash ~/git_repo/particle_art/scripts/parallel_sweep_launcher.sh session_10
```

Monitor in any terminal:
```bash
python3 ~/git_repo/particle_art/scripts/sweep_coordinator.py status
```

Example output:
```
Total: 1088
  pending: 1050 (96.5%)
  optimizing: 25 (2.3%)
  done: 13 (1.2%)
```

Each session will:
1. Claim 5 pieces atomically
2. Spawn 5 agents in parallel
3. Wait for all to complete
4. Mark them done
5. Claim next 5 and repeat

**Estimated time**: ~5–6 hours for all 1088 pieces at full parallelism (10 sessions × 50 pieces/hour)

## Creating a Parametric Piece

For a new piece (e.g., `xkm`), make it testable:

1. **Add query param reads** to `pieces/xkm/index.html`:
   ```javascript
   const params = new URLSearchParams(window.location.search);
   const RADIUS = parseFloat(params.get('r') || '0.5');
   const OPACITY = parseFloat(params.get('opacity') || '0.4');
   ```

2. **Replace hardcoded values** with these vars throughout the script

3. **Create `pieces/xkm/hyperparam.json`**:
   ```json
   {
     "grid": {
       "r": [0.4, 0.5, 0.6],
       "opacity": [0.3, 0.4, 0.5]
     },
     "warmup_ms": 5000,
     "constraints": {
       "min_sharpness": 150,
       "min_stddev": 12,
       "visual_distinctness_threshold": 0.15
     }
   }
   ```

4. **Test**: `/hyperparam-sweep xkm`

## Understanding Results

Results saved to `~/.particle_art/.sweep_<piece_id>.json`:

```json
{
  "piece_id": "v21",
  "total_tested": 9,
  "passing": 9,
  "winners": [
    {
      "params": { "cube_scale": 0.04, "opacity": 0.5 },
      "metrics": { "sharpness": 6587.1, "stddev": 33.8, "range": 102.7 },
      "thumb": "pieces/v21/_sweep_3.png"
    }
  ]
}
```

- `total_tested`: How many combos were tried
- `passing`: How many passed aesthetic gates (sharpness > min_sharpness)
- `winners`: Top 1–3 by sharpness, filtered for >15% visual distinctness
- `thumb`: Screenshot of each winner

## Troubleshooting

**Session hung?** Check if manifest lock file is stale:
```bash
ls -la ~/.particle_art/.sweep_manifest.lock
rm ~/.particle_art/.sweep_manifest.lock  # If needed
```

**All pieces failing gates?** Lower constraints in hyperparam.json:
```json
"constraints": {
  "min_sharpness": 100,  // was 150
  "min_stddev": 8,       // was 12
}
```

**Timeout errors?** Increase `warmup_ms` for 3D or async-loader pieces:
```json
"warmup_ms": 9000  // was 5000
```

**Need to mark piece done manually?**
```bash
python3 ~/git_repo/particle_art/scripts/sweep_coordinator.py mark <piece_id> done
```

## Performance Notes

- **Per-piece time**: 10–30 seconds (depends on warmup + grid size)
- **Single browser context**: No per-combo startup overhead
- **9-combo grid** (v21 test): ~30 seconds total
- **27-combo grid**: ~90 seconds total
- **Parallelism**: 10 sessions × 5 agents = 50 pieces concurrent

## Next: Auto-Integration

Future work (TBD):
- Auto-detect hardcoded numerics in piece code
- Generate hyperparam.json templates automatically
- Auto-invoke sweep before piece deployment
- Integrate winners back into piece code

For now: manual parametrization + manual sweep invocation.
