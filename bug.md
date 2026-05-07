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

## 6. messy occlusion-move / guts-like visual (geu)
**Examples:** geu (culled 2026-05-07).
**Pattern:** the camera moves through dense overlapping geometry that lacks a clear depth-stratification, so the visual reads as a writhing wet mass — viscera, intestines, undefined organic guts. Particle/line layers occlude each other in chaotic non-figurative ways.
**Why it fails:** user 2026-05-07 — "messy occlusion move or the guts like visual." When you can't tell foreground from background and the camera-motion makes the confusion worse, the piece reads as queasy, not aesthetic.
**Fix:** ensure depth-stratification — give EACH layer a distinct z-band so layers read as discrete; let camera motion REVEAL form, not stir it. If form has organic-tube geometry, render with shading that distinguishes near/far. No more than 2 overlapping translucent layers at any pixel.

## 7. PowerPoint-2010 motion / cheap animation (6ip)
**Examples:** 6ip (culled 2026-05-07).
**Pattern:** point movement that looks like a stock PowerPoint transition or 2010-era After Effects template — tween-y, zooming-in-from-corner, swirl-then-stop, pulsing-on-beat in obvious sync, lazy LERP-to-mouse, "spinning particles" without semantic meaning.
**Why it fails:** user 2026-05-07 — "looks like 2010 powerpoint motion bad."
**THE 3-QUESTION MOTION RUBRIC** (now part of the aesthetic gate):
  1. Would you show this AT MoMA?
  2. Would you START A HOLLYWOOD FILM with this?
  3. Would you put it on the LANDING PAGE of a billion-dollar AI startup?
**If the answer is 3 nos, don't ship it.** The aesthetic gate now asks this question explicitly per piece. Motion must have *intent* — gravity, breathing, signal-propagation, narrative arc — never bare easing.

## 8. barbell / dumbbell render artifact
**Examples:** 5i1 (the current 5i1 has a small barbell-shape — two pale dots connected by a vertical line — at the end of the structure). User flagged 2026-05-07.
**Pattern:** unintended render geometry: typically a degenerate L-system terminal, an unconsumed control-point in a CatmullRomCurve3, or a forgotten debug-marker (two endpoint spheres + a line) accidentally left in the scene.
**Fix:** when descending from 5i1, EXPLICITLY remove or hide any 2-endpoint-sphere-with-connecting-line geometry. Verify by inspecting the rendered thumbnail for unexpected dumbbell shapes. The aesthetic gate should flag this if it persists.

## 9. low-contrast / faint render — "cant see"
**Examples:** jwu, kjn, 7wq, si0 (all culled 2026-05-07).
**Pattern:** the piece passes the non-bg pixel-fraction gate, but the EYE reads it as nearly empty — the foreground form is too faint against the background, contrast is too low to perceive, or the entire image sits in one narrow luminance band.
**Why it fails:** user 2026-05-07 — "too faint cant see / cant see / blurry, contrast very low / blurry."
**Hard gate (now enforced in scripts/validate_render.py):**
- grayscale stddev < 12.0 (out of 255) → REJECT
- p95 - p5 luminance < 35.0 → REJECT
**Fix in generation:** every piece must have at least ONE high-contrast element — either bright foreground on dark bg or vice-versa, with a luminance delta of ≥ 50 channel-units between the form and the background. If the piece's design is "everything in mid-grey", abandon and pick another direction.

## 10. movement-not-meaningful (no readable motion law)
**Examples:** dcs (culled 2026-05-07).
**Pattern:** particles move but the motion is incoherent — no readable law, no narrative arc, no rhythmic structure. The eye cannot name what's happening (compare to the 10-word motion vocabulary in scripts/motion_graph.json).
**Why it fails:** user 2026-05-07 — "movement not meaningful." Motion that doesn't resolve to one of the 10 vocabulary words (drift / pulse / cascade / spiral / murmur / ascend / shatter / trace / breathe / propagate) is failure.
**Fix:** ALL pieces must declare their motion via `<!-- MOTION: <word>, intensity: ... -->` in the HTML head, and the eye should be able to verify the declaration in 5 seconds of watching.

## 11. empty composition
**Examples:** usm (culled 2026-05-07).
**Pattern:** the piece passes the non-bg gate by virtue of a tiny element, but most of the canvas is dead space and the form doesn't earn that void. Distinct from #9 (which is everywhere-faint); this is "subject too small / sparse to support the empty negative space around it."
**Fix:** subject must occupy ≥ 25% of frame at default camera, OR the negative space must be doing intentional compositional work (Eliasson's atmosphere, Shiota's web stretching to edges, Riley wave-interference filling the field).

## 12. RECURRING BAD-STYLE MEMORY (user 2026-05-07 batch cull)
**Examples (all culled, no shared pattern but recorded for memory):** wjb, cp2, 8aa, zo2, mac, 7cb, b3s, cyf.
**Pattern:** user labeled "very bad" without elaboration — meaning the style read as immediately wrong on sight. Reasons inferred from neighbors: rotation-only motion, generic chaotic dispersion, off-center compositions, garish palettes, derivative of culled styles. The aesthetic gate should treat any piece whose IMMEDIATE READ resembles these culled examples as suspect — when in doubt about a borderline render, REJECT.

## 13. tiny subject in vast empty canvas
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
