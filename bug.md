# bug.md — aesthetic guidance

## Core reject rules

* Avoid visual clutter. The main form should stay clear and readable.
* Avoid accidental overlap that hides the subject. Motion should reveal depth, not confuse it.
* Avoid faint or muddy visuals. The subject must stand out with strong contrast.

## First-principles aesthetic guidance

* Every piece needs a clear subject. The viewer should instantly know what to look at.
* Beauty comes from coherence plus surprise. Not randomness alone.
* Motion should feel intentional, like breathing, drifting, blooming, collapsing, or signaling.
* Layers should support the form, not fight each other. Texture must never overpower structure.
* Contrast creates presence. Weak contrast feels invisible or unfinished.
* Organic variation feels alive. Perfect grids and repetitive motion feel artificial or cheap.
* Negative space should feel deliberate, not empty.
* A stunning piece evokes emotion in seconds: awe, calm, mystery, elegance, tension, wonder.
* Good art feels inevitable. Every element appears necessary.
* Sublime visuals balance clarity with discovery: recognizable at first glance, richer over time.

## Beauty heuristic

> Clear form, intentional motion, strong contrast, controlled complexity, emotional atmosphere.

## Anti-pattern heuristic

> If the eye feels confused, strained, bored, or unable to find the subject within 2 seconds, reject it.

## Hard bans

* **LeePerrySmith.glb** — never load this model. Reject any piece containing the string `LeePerrySmith`.
* **Radial red-line / thread-burst** — never generate bright lines radiating outward from a central orb/disc (the Chiharu Shiota red-thread family restyled). User 2026-06-01: "never do these radial red line again." Banned regardless of color — a verdigris/teal recolor (m8p) of the red original (i09) is equally rejected. The structure is the violation, not the hue.

## Code-detectable visual failures — REJECT if present

These are specific patterns in the GLSL / Three.js code that reliably produce bad visuals.
**Check the shader code and material config, not just the HTML structure.**

### Blurry / indistinct particles
* Fragment shader uses `exp(-r * N)` (gaussian falloff) where N ≤ 15 on the main form — produces soft blobs, not marks. Reject unless the piece is intentionally a fog/smoke piece. Sharp marks require N ≥ 20 (or `alphaTest ≥ 0.5` with a hard-edge texture).
* `halo` term added on top of `core` using `exp(-r * 6.0)` or similar low exponent — doubles blur radius, makes every particle look smeared. Reject if halo exponent < 8.
* `sizeAttenuation: true` combined with particles far from camera — makes distant particles microscopic and unreadable.

### Faint / invisible form
* Base alpha < 0.55 on the main particle material — form reads as translucent fog, not solid mark. `AdditiveBlending` amplifies the problem: low-alpha additive particles vanish against a dark background.
* Fog density ≥ 0.025 with `AdditiveBlending` — the combination kills midground and background particles, leaving only a small near-camera zone visible. Result: the form "fades into nothing."
* Wire / line opacity ≤ 0.25 — invisible structure, no readable form.

### Moves too fast / jittery / illegible motion
* Per-particle position jitter > 0.15 world units (e.g. `hash(...) * 0.22`) — at standard camera distance this blurs the silhouette into noise.
* Camera `autoRotate` with speed > 0.8, or `lerp` toward target with α > 0.05 per frame — produces nausea-inducing chase camera.
* Growth/simulation tick calling `requestAnimationFrame` AND a `setInterval` in the same piece — double-stepping causes motion at 2× intended speed.
