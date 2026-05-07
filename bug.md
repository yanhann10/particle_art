# bug.md — aesthetic anti-patterns

The aesthetic gate (`scripts/aesthetic_gate.py`, runs between the render-gate
and the commit step in `scripts/mutate.py`) reads this file as context and
asks Claude whether the freshly-rendered piece exhibits any documented
anti-pattern. Failing pieces are REJECTED — they don't ship to Vercel — but
their reusable ideas are extracted to `scripts/idea_extracts.jsonl` for
future reference.

This file is the user's growing taxonomy of "what doesn't work, and why."
Each entry should be specific enough that a critic can spot the pattern in
a new piece's HTML, and concrete enough that a generator can avoid it.

---

## 1. dots-on-intestine / dots-on-tube self-occlusion
**Examples:** jcl (culled 2026-05-07), seen as a recurring pattern in t9w/sfm/9qc/xi6/lk6 territory.
**Pattern:** small particles (Points, sprites) are scattered DENSELY along the surface of a curved tube/coil/intestine form, then the CAMERA ANIMATES through or around the tube. The dense particle layer in front of the tube **occludes** the tube's far side, and as the camera moves the visual reads as a confused dotty mess rather than a coherent volume.
**Why it fails:** the tube is the form, the particles are the texture; when the texture density blocks the form-reading, the piece loses its subject. Worse: the camera motion exposes the occlusion as flicker, not depth.
**Fix:** if a piece uses a tube/coil/intestine + scattered points along it, EITHER (a) reduce particle count to <5k AND make them clearly thinner than the tube radius, OR (b) drop the dot layer and keep the tube as a smooth lit surface, OR (c) keep the dots but freeze the camera (still life, not animation).

## 2. LeePerrySmith head model — HARD BAN
**Examples (ALL CULLED):** 7y7, w64 (original), 4pt, cek, dov, hn4, ljc, and every descendant.
**Pattern:** loading `LeePerrySmith.glb` and sampling its surface as a head-shaped point cloud — typically via `GLTFLoader` + `MeshSurfaceSampler`. The head's geometry has a malformed mouth area that reads as uncanny / painful.
**Why it fails:** user 2026-05-07: "the mouth is wrong and painful to look at." Every single piece using this asset has been culled; this is now a hard ban, not a soft preference.
**Fix:** **NEVER** load `LeePerrySmith.glb` in any new piece, even when the parent's HTML contains the URL. If you inherit a parent that uses LeePerrySmith, you MUST swap to a different model from the pool (Fox, BrainStem, MosquitoInAmber, SheenChair, Horse, Parrot, RobotExpressive, Soldier, Michelle, Stork, Avocado, DamagedHelmet, ABeautifulGame, etc.). The aesthetic gate will reject any piece that contains the literal string `LeePerrySmith` in its HTML — there is no exception.

## 3. sieve / dotty grid surface
**Examples:** h97 (culled 2026-05-07).
**Pattern:** a 2D grid of dots (or 3D regular point lattice) used as the primary visual element, often stretched over a surface or used as a "sieve" through which something passes. Reads as graph-paper, not art.
**Why it fails:** regular grids look like UI debug overlays, not aesthetic statements. Lacks the irregularity / organic emergence that gives generative art its life.
**Fix:** if the piece has a regular grid, INTRODUCE one of: (a) per-cell jitter > 30% of cell size, (b) deterministic pattern variation (Conway, Wolfram, voronoi), (c) replace the grid with a sampled-from-shape distribution (MeshSurfaceSampler).

## 4. blurry / incoherent / no clear subject
**Examples:** 610 (culled 2026-05-07 — "how did it pass aesthetic judge, blurry, lack coherence").
**Pattern:** the piece renders SOMETHING (passes the >0.5% non-bg render gate), but the form has no clear subject — feels like a wash of gradient or a smear of additive-blended particles with no recognizable shape, no figure, no narrative anchor.
**Why it fails:** the user's principles explicitly demand "form must be recognizable — viewer should know what they're looking at." A blurry haze fails this.
**Fix:** every piece must have a CLEAR PRIMARY FORM at the center of frame — a recognizable silhouette, a defined attractor curve, a discrete object, a labeled architecture. If the piece looks like an out-of-focus photograph, redesign with a sharper anchor (geometric primitive, real-object point cloud, or rule-based growth path that draws a definable shape).

## 5. shapeless noise nebula (the original ac2 failure)
**Examples:** ac2 (removed), 9oa (removed), x7n (mutation of ac2, dropped), 9k1, f7r.
**Pattern:** Perlin/curl-noise driving particle motion in a vague cloud, no underlying field-equation or object — pure random walk in noise.
**Fix:** shape MUST come from a field, equation, or object. Strange attractors (Lorenz, Aizawa). Differential growth. MeshSurfaceSampler. L-systems. Boids with rules. Never noise as the primary structural source.

## 6. tiny subject in vast empty canvas
**Examples:** f36 / k2v (single flamingo too small in frame, culled).
**Pattern:** the recognizable form occupies <10% of the viewport at default camera; the rest of the frame is empty.
**Fix:** the form must occupy AT LEAST 30-60% of the frame at the default camera angle. Re-fit the camera if needed.

---

## How to add a new entry

When a piece is culled, add a new numbered entry in this format:

```
## N. <short pattern name>
**Examples:** <piece_id list>
**Pattern:** <what's structurally happening in the HTML / shader>
**Why it fails:** <user's reasoning, quoted if possible>
**Fix:** <concrete generator / mutation directive that avoids the pattern>
```

The aesthetic gate truncates this file to the first ~6000 chars when injected
into the Claude critique call, so keep entries concise and lead with the
pattern's identifying feature.
