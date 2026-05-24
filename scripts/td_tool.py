#!/usr/bin/env python3
"""td_tool.py — CLI wrapper for TouchDesigner as a rendering/mutation tool.

Lets agents call TouchDesigner (TD) programmatically to render generative art frames.

Communication strategy (priority order):
  1. HTTP mode  — POST to TD's built-in web server DAT at http://localhost:<TD_PORT>.
                  If TD isn't already running, attempts to launch it first.
  2. Script injection mode — write a Python snippet to a temp file, pass via
                  --externaltox flag to the TD binary (fallback if HTTP fails).
  3. Unavailable mode — exit 1 with a clear message so callers can skip TD scoring.

Entry points:
    td_tool.py check                         check TD installation + reachability
    td_tool.py list-templates                list .toe / .json specs in pieces/templates/
    td_tool.py render --toe TEMPLATE --params '{"speed":0.4}' --out /tmp/frame.png

Environment variables:
    TD_PORT   web server port (default 9980)
    TD_HOST   web server host (default localhost)
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------
REPO = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO / "pieces" / "templates"
TD_APP = Path("/Applications/TouchDesigner.app")
TD_BINARY = TD_APP / "Contents" / "MacOS" / "TouchDesigner"

# ---------------------------------------------------------------------------
# Config (overridable via env)
# ---------------------------------------------------------------------------
TD_HOST = os.environ.get("TD_HOST", "localhost")
TD_PORT = int(os.environ.get("TD_PORT", "9980"))
TD_LAUNCH_TIMEOUT = int(os.environ.get("TD_LAUNCH_TIMEOUT", "15"))  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _td_installed() -> bool:
    """Return True if TouchDesigner.app exists at the expected path."""
    return TD_BINARY.exists()


def _td_version() -> str:
    """Return TD version string by reading the app's Info.plist, or 'unknown'."""
    plist = TD_APP / "Contents" / "Info.plist"
    if not plist.exists():
        return "unknown"
    try:
        result = subprocess.run(
            ["defaults", "read", str(plist), "CFBundleShortVersionString"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        v = result.stdout.strip()
        return v if v else "unknown"
    except Exception:
        return "unknown"


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Return True if the TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_post(path: str, payload: dict, timeout: int = 10) -> tuple[int, str]:
    """POST JSON payload to TD web server. Returns (status_code, body)."""
    url = f"http://{TD_HOST}:{TD_PORT}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise ConnectionError(f"Cannot reach TD at {url}: {e.reason}") from e


def _http_get(path: str, timeout: int = 10) -> tuple[int, str]:
    """GET from TD web server. Returns (status_code, body)."""
    url = f"http://{TD_HOST}:{TD_PORT}{path}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise ConnectionError(f"Cannot reach TD at {url}: {e.reason}") from e


def _launch_td(toe_path: Path | None = None) -> bool:
    """Launch TouchDesigner in the background. Returns True if port opens."""
    cmd = [str(TD_BINARY)]
    if toe_path and toe_path.exists():
        cmd.append(str(toe_path))
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        print(f"[td_tool] Failed to launch TD: {e}", file=sys.stderr)
        return False

    deadline = time.monotonic() + TD_LAUNCH_TIMEOUT
    while time.monotonic() < deadline:
        if _port_open(TD_HOST, TD_PORT):
            return True
        time.sleep(0.5)
    return False


def _ensure_td_running(toe_path: Path | None = None) -> bool:
    """Check if TD web server is up; launch if not. Returns True if ready."""
    if _port_open(TD_HOST, TD_PORT):
        return True
    if not _td_installed():
        return False
    print(
        f"[td_tool] TD not running — launching (timeout {TD_LAUNCH_TIMEOUT}s)…",
        file=sys.stderr,
    )
    return _launch_td(toe_path)


def _run_script_via_http(script: str, timeout: int = 30) -> str:
    """Send a Python script to TD via its web server DAT. Returns response body."""
    try:
        status, body = _http_post("/command", {"script": script}, timeout=timeout)
        if status not in (200, 204):
            raise RuntimeError(f"TD web server returned HTTP {status}: {body[:200]}")
        return body
    except ConnectionError:
        # Fallback: try GET with inline cmd param (simple one-liners)
        encoded_script = urllib.request.quote(script)
        status, body = _http_get(f"/?cmd={encoded_script}", timeout=timeout)
        return body


def _run_script_via_injection(script: str, timeout: int = 30) -> str:
    """Write script to a temp .py file and pass to TD via --externaltox."""
    if not _td_installed():
        raise RuntimeError("TouchDesigner binary not found — cannot inject script.")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="td_inject_", delete=False
    ) as f:
        f.write(script)
        tmp_path = f.name
    try:
        result = subprocess.run(
            [str(TD_BINARY), "--externaltox", tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout + result.stderr
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    """Check if TD is installed and whether its web server is reachable."""
    installed = _td_installed()
    if not installed:
        print("status: unavailable")
        print(f"reason: TouchDesigner not found at {TD_BINARY}")
        return 1

    version = _td_version()
    print(f"installed: true")
    print(f"binary: {TD_BINARY}")
    print(f"version: {version}")

    running = _port_open(TD_HOST, TD_PORT)
    print(f"web_server_host: {TD_HOST}")
    print(f"web_server_port: {TD_PORT}")
    print(f"web_server_running: {running}")

    if running:
        # Sanity-ping the web server
        try:
            status, body = _http_get("/", timeout=3)
            print(f"web_server_ping: ok (HTTP {status})")
        except ConnectionError as e:
            print(f"web_server_ping: failed ({e})")
        print("status: ready")
        return 0
    else:
        print("status: installed_not_running")
        print(
            "hint: open TouchDesigner and enable the web server DAT on "
            f"port {TD_PORT} to enable rendering."
        )
        return 1  # not ready for rendering; callers use exit code to skip TD


def cmd_list_templates(args: argparse.Namespace) -> int:
    """List .toe files and .json specs in pieces/templates/."""
    if not TEMPLATES_DIR.exists():
        print(f"[td_tool] Templates directory not found: {TEMPLATES_DIR}", file=sys.stderr)
        return 1

    toe_files = sorted(TEMPLATES_DIR.glob("*.toe"))
    json_files = sorted(TEMPLATES_DIR.glob("*.json"))
    all_files = toe_files + json_files

    if not all_files:
        print("(no templates found)")
        return 0

    print(f"Templates in {TEMPLATES_DIR.relative_to(REPO)}:")
    for p in all_files:
        suffix_label = "toe" if p.suffix == ".toe" else "json-spec"
        # For json specs, try to read the description
        desc = ""
        if p.suffix == ".json":
            try:
                spec = json.loads(p.read_text())
                desc = " — " + spec.get("description", "")
            except Exception:
                pass
        print(f"  {p.name} ({suffix_label}){desc}")

    return 0


def cmd_render(args: argparse.Namespace) -> int:
    """Render a frame using TD — either via HTTP or script injection."""
    toe_arg: str = args.toe
    params_raw: str = args.params or "{}"
    out_path = Path(args.out)
    timeout: int = args.timeout

    # Parse params
    try:
        params: dict = json.loads(params_raw)
    except json.JSONDecodeError as e:
        print(f"[td_tool] Invalid JSON in --params: {e}", file=sys.stderr)
        return 1

    # Resolve template path
    toe_path: Path | None = None
    candidate = Path(toe_arg)
    if not candidate.is_absolute():
        # Try templates dir first
        in_templates = TEMPLATES_DIR / toe_arg
        if in_templates.exists():
            toe_path = in_templates
        elif candidate.exists():
            toe_path = candidate.resolve()
        # For .json specs: extract description, no actual .toe to load
        if toe_path is None and (TEMPLATES_DIR / toe_arg).with_suffix(".json").exists():
            spec_path = (TEMPLATES_DIR / toe_arg).with_suffix(".json")
            toe_path = spec_path  # use as marker; handled below
    else:
        toe_path = candidate if candidate.exists() else None

    if toe_path is None:
        # Non-fatal: we can still try HTTP render with params only
        print(
            f"[td_tool] Warning: template '{toe_arg}' not found; "
            "proceeding with params only.",
            file=sys.stderr,
        )

    # Ensure TD is running (launch if needed)
    td_ready = _ensure_td_running(
        toe_path if toe_path and toe_path.suffix == ".toe" else None
    )

    # Build the render script
    params_json = json.dumps(params)
    out_str = str(out_path.resolve())
    render_script = f"""
import json, td
params = {params_json!r}
out_file = {out_str!r}
# Apply params to operators if they exist
for k, v in params.items():
    for op_name in ['params', 'constant1', 'base']:
        try:
            op(op_name)[k] = v
        except Exception:
            pass
# Render to file
try:
    op('render1').saveImage(out_file)
    print('RENDER_OK:' + out_file)
except Exception as e:
    print('RENDER_ERR:' + str(e))
"""

    if td_ready:
        # Mode 1: HTTP
        try:
            body = _run_script_via_http(render_script, timeout=timeout)
            if "RENDER_OK" in body:
                print(f"render: ok")
                print(f"output: {out_path}")
                return 0
            elif "RENDER_ERR" in body:
                print(f"[td_tool] TD render error: {body}", file=sys.stderr)
                return 1
            else:
                # TD accepted the script but response is opaque — treat as ok
                print(f"render: submitted")
                print(f"output: {out_path}")
                return 0
        except (ConnectionError, RuntimeError) as e:
            print(f"[td_tool] HTTP mode failed: {e}; trying injection…", file=sys.stderr)
            # Fall through to injection mode

    if _td_installed():
        # Mode 2: Script injection
        print("[td_tool] Falling back to script injection mode.", file=sys.stderr)
        try:
            output = _run_script_via_injection(render_script, timeout=timeout)
            if "RENDER_OK" in output:
                print("render: ok")
                print(f"output: {out_path}")
                return 0
            elif "RENDER_ERR" in output:
                print(f"[td_tool] Injection render error: {output}", file=sys.stderr)
                return 1
            else:
                # --externaltox doesn't exec Python scripts; can't verify render happened
                print(
                    "[td_tool] Script injection launched TD but render outcome is unknown.\n"
                    "  TD must be running with a web server DAT on port 9980 for reliable render.",
                    file=sys.stderr,
                )
                return 1
        except Exception as e:
            print(f"[td_tool] Script injection failed: {e}", file=sys.stderr)

    # Mode 3: Unavailable
    print("[td_tool] TouchDesigner unavailable — cannot render frame.", file=sys.stderr)
    print("status: unavailable")
    return 1


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="td_tool.py",
        description="CLI wrapper for TouchDesigner as a generative-art rendering tool.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # ---- check ----
    sub.add_parser(
        "check",
        help="Check if TD is installed and whether its web server is reachable.",
    )

    # ---- list-templates ----
    sub.add_parser(
        "list-templates",
        help="List .toe files and .json specs in pieces/templates/.",
    )

    # ---- render ----
    r = sub.add_parser(
        "render",
        help="Render a single frame using TouchDesigner.",
    )
    r.add_argument(
        "--toe",
        required=True,
        metavar="TEMPLATE",
        help=(
            "Template name or path. Bare names (e.g. 'particle_basic') are resolved "
            "against pieces/templates/; absolute paths are used directly."
        ),
    )
    r.add_argument(
        "--params",
        default="{}",
        metavar="JSON",
        help="JSON object of parameter overrides, e.g. '{\"speed\":0.4}'.",
    )
    r.add_argument(
        "--out",
        required=True,
        metavar="PATH",
        help="Output PNG path, e.g. /tmp/frame.png.",
    )
    r.add_argument(
        "--timeout",
        type=int,
        default=30,
        metavar="SECONDS",
        help="Max seconds to wait for TD to respond (default: 30).",
    )

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "check":
        return cmd_check(args)
    elif args.command == "list-templates":
        return cmd_list_templates(args)
    elif args.command == "render":
        return cmd_render(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
