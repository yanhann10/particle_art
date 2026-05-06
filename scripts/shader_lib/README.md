# scripts/shader_lib/ — proven starting priors

Each file is a vetted, copy-pasteable scaffold for a common particle-art technique. The mutation worker (and humans) can pick one as the *foundation* of a new piece, then mutate the surface layer (palette, motion law, geometry) on top. This raises the floor — fewer pieces start from zero.

## Inventory

| file | technique | cite-from |
|---|---|---|
| `curl_noise_3d.glsl` | proper 3D curl-noise via 4-axis sampling | n8q, ac2 (corrected) |
| `gpgpu_pingpong.html` | minimal 100k-particle GPGPU FBO scaffold | zs4, 2n2, xs4, f7r |
| `mesh_surface_sampler.js` | GLTFLoader + MeshSurfaceSampler with bbox-normalize | n8q, w64, hn4 |
| `differential_growth_3d.js` | self-avoiding curve in 3D + age-driven rise | 5gm, 2iy, t9w, ajo |
| `ribbon_swept_curve.js` | CatmullRomCurve3 → variable-radius tube or flat ribbon | 2iy, t9w, ajo |
| `audio_fft_bands.js` | mic-FFT split into 5 frequency bands | 8ug |
| `lsystem_3d_turtle.js` | stochastic L-system + 3D-axis turtle | x09, kam |
| `pose_morph_lerp.js` | two-pose particle lerp (compatible with any sampling) | kam, hn4, xs4 |

## Usage in directives

A new directive `compose_with_shader_lib_unit` can be sampled by mutate.py:

> Pick one foundation from `scripts/shader_lib/`. Use it verbatim as the structural backbone of the new piece. THEN apply the parent's surface signature (palette, gestures, render primitive) on top. The library unit must remain recognizable in the output — a reviewer who knows the library should see the unit as the skeleton.

This ensures every output has a known-good starting point and saves token budget on re-deriving the same patterns.

## Adding a new unit

Each unit must be:
- < 200 lines
- self-contained (no other lib units imported)
- battle-tested in at least one shipped piece (cite the piece in the inventory)
- documented at the top of the file: what it produces, what it expects, what to vary
