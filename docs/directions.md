# Generative Art Direction Lineage

Each "direction" is a conceptual seed — a way of thinking about form, process, and motion. The autonomous worker evolves descendants from these seeds. The user steers by marking pieces keep/drop.

---

## 1. Mathematical Attractors
**Core idea:** Strange attractors as form — differential equations whose trajectories trace intricate, deterministic-but-never-repeating shapes in 3D space.
**Seeds:** zs4 (Lorenz butterfly), 2n2 (Aizawa vortex), xs4 (4-attractor cycler: Lorenz → Aizawa → Thomas → Halvorsen + volumetric fog beam)
**What user likes:** Defined shape, mathematical rigor, the butterfly/vortex silhouette. xs4's multi-click traversal is a "multi-click exemplar."
**Mutation directions:** color drift, fog depth, multi-state traversal, cross-pollinate with other attractors

---

## 2. Organic Morphogenesis — Differential Growth
**Core idea:** Curves that grow by adding nodes where curvature is highest, repelling nearby nodes, constrained to a surface. Produces organic, edge-filling forms like lichen or coral.
**Seeds:** 7ea (2D differential growth), 5gm (3D with age-driven vertical rise — "user-directed 7ea successor")
**Descendants (render styles on same growth algorithm):**
- 2iy — calligraphic ink tube, Persian nasta'liq energy, paper-on-cream
- t9w — vascular biomorphic tube, tapered + pulsing + lit
- ajo — arabesque architectural ribbon, gold on midnight
**What user likes:** Algorithmic morphogenesis — process visible in form. "Organic but defined."
**Mutation directions:** tube material, growth speed, color, canvas texture

---

## 3. Object Point Clouds
**Core idea:** Recognizable real-world 3D objects (Stanford 3D scan archive) dissolved into particle clouds — form implies itself through density.
**Seeds:** n8q (Stanford Lucy angel statue — "recognizable, the right direction")
**What user likes:** Legible form, real sculpture reference, the tension between dissolution and recognition.
**Mutation directions:** different CC0 models, dispersion law, color by depth/normal, audio-reactivity

---

## 4. L-Systems / Botanical
**Core idea:** Lindenmayer systems producing branching tree/plant structures — recursive rewriting rules.
**Seeds:** x09 (L-system tree — "has hope"), kam (cypress ↔ art-deco tower morph — "love it after click"), 5i1 (harmattan L-system → PROJECT HAIL MARY sci-fi register)
**What user likes:** Vertical structure, architectural translation of natural forms, click-state morphing
**Mutation directions:** rule set variation, art-deco materiality, sci-fi register (5i1 branch)

---

## 5. Boids / Flocking with Emotional Arc
**Core idea:** Reynolds boids (separation, alignment, cohesion) with narrative overlay — emotional journey mapped onto collective behavior.
**Seeds:** nsz (Reynolds boids — "has hope, lacks emotional depth"), sav (480 birds + 1 straggler, TOGETHER→LOSS→SEEKING→RETURN with dawn-sky gradient)
**What user likes:** sav — the life-experience emotional arc. Pure boids alone insufficient.
**Mutation directions:** different narrative arcs, different flock/straggler ratios, color and weather

---

## 6. Line / Wave Interference → Calyx Morph
**Core idea:** Bridget Riley-style wave-interference line patterns that morph into floral/calyx bowl forms over time.
**Seeds:** 2g4 (Riley wave-interference — "line vocabulary user likes, needs atmosphere"), 6mk (Riley→calyx morph)
**Descendants:** ere (6mk wrapped in inhabited atmosphere — parchment + window light + paper grain + dust motes)
**What user likes:** Line vocabulary, the atmosphere of ere. zv4 is a "love it" cross-pollinated descendant (spiral motion_word).
**Mutation directions:** atmosphere depth, color, morph speed, cross-pollinate with other favorites

---

## 7. Physarum / Slime Mold
**Core idea:** Physarum polycephalum simulation — agents follow nutrient trails, deposit trail, trails diffuse and decay, producing vein-like networks.
**Seeds:** physarum_v2 (autonomous worker direction — xs4 parent)
**Status:** Render gate rejections in recent cron ticks (low contrast, timeout). Still being evolved.
**Mutation directions:** trail decay rate, agent count, color scheme, substrate texture

---

## 8. Audio-Reactive Shells
**Core idea:** Geometric shell forms driven by audio frequency analysis — form responds to sound.
**Seeds:** 8ug (audio-reactive shells, sodium-amber + ice-cyan duotone — "not bad")
**What user likes:** The duotone palette, the shell geometry. Parent was 9k1 (noise blob, culled).
**Mutation directions:** different shell geometries, different audio mappings, palette variations

---

## 9. Kirigami — Carve + Bend  *(new seed 2026-05-21)*
**Core idea:** A flat square with incised radial cuts (attached, not removed), flaps bent into curved 3D arcs using cylindrical arc geometry. Paper sculpture in raking directional light.
**Seeds:** 9f6 ("incision fold" — 6 radial petals, breathing + slow drift, click to unfold)
**Technical pattern:** THREE.ShapeGeometry with holes for flat base; custom BufferGeometry quad-strip following cylindrical arc (y=R·sin(a), z=R·(1-cos(a))); each flap in a pivot group rotated radially outward.
**What user wants:** More cut patterns (Voronoi, Fibonacci spiral, arabesque tiling), different fold geometries, different light angles.
**Mutation directions:** carve_bend directive (weight 2.0 in mutation_directives.json)

---

## 10. Chop + Rearrange  *(new seed 2026-05-21)*
**Core idea:** Take a donor volume, cut it with another shape geometry, fully separate the fragments, rearrange with independent transforms (scale, rotate, translate) into a harmonious composition. Unlike kirigami — pieces fully detach and are free.
**Seeds:** 3pv ("chop and rearrange" — building now)
**References:** Seattle Sculpture Park large metal sculptures, cut-apart tubes rearranged.
**Technical pattern (proposed):** Clip-plane fragment isolation — N copies of base geometry, each copy has material.clippingPlanes restricting its visible slice of space.
**Mutation directions:** different donor volumes, different cut geometries, different rearrangement logics

---

## 11. Tilt 2D into 3D  *(new seed 2026-05-21)*
**Core idea:** Flat 2D geometric shapes (circles/rings, plates, arcs, organic blobs) tilted boldly in 3D space to create public outdoor sculpture compositions. Each shape reads as a flat form pushed into 3D by rotation. Ground plane + sky backdrop + shadow mapping for outdoor sculpture feel.
**Seeds:** mt1 ("tilted planes" — building now)
**References:** Mark di Suvero (steel rings/beams), stacked disk sculptures, Elmgreen & Dragset, Yorkshire Sculpture Park pieces.
**Technical pattern:** CylinderGeometry (disk), TorusGeometry (ring), BoxGeometry (plate), ExtrudeGeometry (organic flat form) + PCFSoftShadowMap + ground plane.
**Mutation directions:** shape vocabulary, color palette, tilt angles, composition logic, bending (slightly curved planes)

---

## Anti-patterns (culled / documented in bug.md)
- **Noise blobs** (9k1, f7r, x7n): shapeless, no internal form logic
- **LeePerrySmith head scans** (hn4 + others): banned 2026-05-07
- **Sieve/grid overlay** (h97): "not a huge fan of sieve style visual"
- **Dots-on-intestine** (jcl): movement causes self-occlusion
- **PowerPoint motion** (6ip): "looks bad, like 2010 powerpoint motion"
- **Rotation overuse** (2ie): "its just rotation"
- **Blurry/low-contrast renders** (7wq, si0, 610): aesthetic gate fails retroactively
- **Guts-like / messy occlusion** (geu): culled
