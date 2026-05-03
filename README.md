# particle_art

A steerable evolutionary gallery of particle-cloud generative art pieces.
Each piece is a single self-contained HTML file (three.js + GLSL) and lives under `pieces/<3-char-code>/`.

## Layout

```
particle_art/
├── index.html               # gallery / steering UI
├── lineage.json             # manifest of all pieces + parent-child edges
├── vercel.json              # static-deploy config
├── pieces/
│   └── <code>/
│       ├── index.html       # the piece
│       └── meta.json        # direction, stack, parent_id, fitness…
├── thumbs/                  # PNG thumbnails (rendered by CI)
├── scripts/                 # mutation + validation scripts (Phase 4+)
└── .github/workflows/       # thumbnail render + Vercel deploy
```

## Round 1 candidates

| code | direction          | input               | particles |
|------|--------------------|---------------------|-----------|
| ac2  | curl-noise nebula  | none (ambient)      | 30,000    |
| m4d  | hand-sculpted      | MediaPipe Hands     | 50,000    |
| 9k1  | audio-reactive     | microphone (FFT×5)  | 20,000    |
| f7r  | gpgpu flow field   | pointer (move+click)| 65,536    |

## Steering protocol

Each cell on the gallery has a 3-char code. Communicate preference by code:

- `keep ac2` → preserve direction, mutate within
- `mutate m4d → more chaos` → directed mutation
- `cross ac2 + 9k1` → hybrid offspring
- `drop f7r` → cull from next round

## Local dev

```bash
# any static file server works
python3 -m http.server 8080
# → http://localhost:8080
```

## Deployment

Vercel auto-deploys from `main`. Static-only — no build step.

## Lineage tracking

Every piece carries `parent_id`, `generation`, `mutation_directive` in `meta.json`.
The top-level `lineage.json` is the source of truth for the gallery. Round-by-round
edits append new pieces and edges; existing pieces are never overwritten.
