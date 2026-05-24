"""Provider abstraction: Claude Code subscription PRIMARY, AWS Bedrock FALLBACK.

Both return the assistant's text response to a user prompt. Subscription is preferred
because it's bounded by the user's plan (no per-token cost); Bedrock is paid per token
and gated by budget.py.
"""
import json
import os
import subprocess
from typing import Optional

# rough cost per call estimate for budget checks
BEDROCK_COST_ESTIMATE_USD = 0.07  # ~3k in + 4k out @ Sonnet 4.5 pricing

BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")
# cross-region inference profiles work in most US regions
BEDROCK_MODEL_ID = os.environ.get(
    "PARTICLE_ART_BEDROCK_MODEL",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
)


class ProviderError(RuntimeError):
    pass


def call_subscription(system: str, user: str, timeout_s: int = 240) -> Optional[str]:
    """Run `claude -p` with the user's subscription. Returns text or None if unavailable."""
    if not _which("claude"):
        return None
    full_prompt = f"{system}\n\n---\n\n{user}"
    try:
        res = subprocess.run(
            ["claude", "-p", full_prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=timeout_s,
        )
        if res.returncode != 0:
            return None
        return res.stdout
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def call_bedrock(system: str, user: str, max_tokens: int = 8000) -> str:
    """Call Claude on AWS Bedrock. Raises ProviderError on failure."""
    try:
        import boto3  # type: ignore
    except ImportError:
        raise ProviderError("boto3 not installed; run: pip install boto3")
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    try:
        resp = client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        # standard messages API shape
        for block in payload.get("content", []):
            if block.get("type") == "text":
                return block["text"]
        raise ProviderError(f"no text block in bedrock response: {payload}")
    except Exception as e:
        raise ProviderError(f"bedrock invoke failed: {e}")


def call_bedrock_vision(system: str, user_text: str, image_b64: str,
                        media_type: str = "image/jpeg",
                        model_id: str | None = None,
                        max_tokens: int = 1024) -> str:
    """Multimodal Bedrock call with one image using Claude (Anthropic format).
    image_b64: base64-encoded image bytes. Raises ProviderError on failure."""
    if model_id is None:
        model_id = BEDROCK_MODEL_ID
    try:
        import boto3  # type: ignore
    except ImportError:
        raise ProviderError("boto3 not installed")
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": media_type, "data": image_b64,
                }},
                {"type": "text", "text": user_text},
            ],
        }],
    }
    try:
        resp = client.invoke_model(
            modelId=model_id, body=json.dumps(body),
            contentType="application/json", accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        for block in payload.get("content", []):
            if block.get("type") == "text":
                return block["text"]
        raise ProviderError(f"no text block: {payload}")
    except Exception as e:
        raise ProviderError(f"bedrock vision failed: {e}")


def call(system: str, user: str) -> tuple[str, str]:
    """Try Bedrock (if PARTICLE_ART_PROVIDER=bedrock) or subscription → Bedrock. Returns (response_text, provider_used)."""
    if os.environ.get("PARTICLE_ART_PROVIDER") == "bedrock":
        out = call_bedrock(system, user)
        return out, "bedrock"
    out = call_subscription(system, user)
    if out:
        return out, "subscription"
    out = call_bedrock(system, user)
    return out, "bedrock"


def _which(cmd: str) -> bool:
    return subprocess.run(["which", cmd], capture_output=True).returncode == 0


if __name__ == "__main__":
    txt, prov = call(
        "You are a particle-art shader assistant.",
        "Reply with exactly the word PONG.",
    )
    print(f"[{prov}] {txt!r}")
