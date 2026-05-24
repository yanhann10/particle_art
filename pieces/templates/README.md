# pieces/templates/

This directory holds TouchDesigner template specs used by `scripts/td_tool.py`.

## File types

### `.toe` files
Binary TouchDesigner project files. These are the actual templates loaded by TD
when rendering. Place real `.toe` files here after authoring them in TouchDesigner.

### `.json` spec files
Human-readable parameter descriptions for each template. When no actual `.toe`
file exists yet, the JSON spec still lets `td_tool.py list-templates` show what
parameters the template will expose once it's built.

## How td_tool.py uses templates

```
td_tool.py render --toe particle_basic --params '{"speed":0.4}' --out /tmp/frame.png
```

1. Resolves `particle_basic` → looks for `particle_basic.toe` here first,
   then for `particle_basic.json` as a fallback spec.
2. If a `.toe` is found, passes it to TouchDesigner (via HTTP web server or
   script-injection fallback).
3. Parameters in `--params` JSON are applied to TD operators before the render.
4. Output is written to `--out` as a PNG.

## Adding a new template

1. Author the `.toe` in TouchDesigner, expose parameters via `constant` or
   `parameter` CHOPs named to match the JSON spec keys.
2. Drop the `.toe` here.
3. Add (or update) the matching `.json` spec in this directory.
4. Verify with:
   ```
   python3 scripts/td_tool.py list-templates
   python3 scripts/td_tool.py render --toe <name> --params '{}' --out /tmp/test.png
   ```

## Template naming

Use lowercase `snake_case` names matching the piece technique, e.g.:
- `particle_basic.toe` / `particle_basic.json`
- `differential_growth.toe` / `differential_growth.json`
- `strange_attractor.toe` / `strange_attractor.json`
