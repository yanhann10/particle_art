/**
 * Vercel serverless function: proxy to VM feedback API.
 *
 * POST /api/mutate  { parent_id, directive }  → { job_id }
 * GET  /api/mutate?job_id=<id>               → { status, new_id? }
 *
 * Set MUTATION_API_URL in Vercel project env vars to point at the VM:
 *   http://ec2-13-223-233-226.compute-1.amazonaws.com:7654
 *
 * If the env var is unset the endpoint returns { queued: true } so the
 * piece page can fall back to manual polling of lineage.json.
 */

const VM_URL = process.env.MUTATION_API_URL || "";

export const config = { runtime: "edge" };

export default async function handler(req) {
  const origin = req.headers.get("origin") || "*";
  const cors = {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };

  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: cors });
  }

  // ── GET: status proxy ──────────────────────────────────────────────
  if (req.method === "GET") {
    const url = new URL(req.url);
    const jobId = url.searchParams.get("job_id");
    if (!jobId) {
      return new Response(JSON.stringify({ error: "missing job_id" }), {
        status: 400, headers: { ...cors, "Content-Type": "application/json" },
      });
    }
    if (!VM_URL) {
      return new Response(JSON.stringify({ status: "polling" }), {
        headers: { ...cors, "Content-Type": "application/json" },
      });
    }
    try {
      const r = await fetch(`${VM_URL}/status/${jobId}`, { signal: AbortSignal.timeout(8000) });
      const data = await r.json();
      return new Response(JSON.stringify(data), {
        status: r.status, headers: { ...cors, "Content-Type": "application/json" },
      });
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e) }), {
        status: 502, headers: { ...cors, "Content-Type": "application/json" },
      });
    }
  }

  // ── POST: submit mutation ──────────────────────────────────────────
  if (req.method === "POST") {
    let body;
    try {
      body = await req.json();
    } catch {
      return new Response(JSON.stringify({ error: "bad json" }), {
        status: 400, headers: { ...cors, "Content-Type": "application/json" },
      });
    }

    const { parent_id, directive } = body;
    if (!parent_id || !directive) {
      return new Response(JSON.stringify({ error: "parent_id and directive required" }), {
        status: 400, headers: { ...cors, "Content-Type": "application/json" },
      });
    }

    if (!VM_URL) {
      // No VM configured — return queued so piece page falls back to lineage polling
      return new Response(JSON.stringify({ queued: true, vm: false }), {
        headers: { ...cors, "Content-Type": "application/json" },
      });
    }

    try {
      const r = await fetch(`${VM_URL}/mutate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parent_id, directive }),
        signal: AbortSignal.timeout(12000),
      });
      const data = await r.json();
      return new Response(JSON.stringify(data), {
        status: r.status, headers: { ...cors, "Content-Type": "application/json" },
      });
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e), vm: false }), {
        status: 502, headers: { ...cors, "Content-Type": "application/json" },
      });
    }
  }

  return new Response(JSON.stringify({ error: "method not allowed" }), {
    status: 405, headers: { ...cors, "Content-Type": "application/json" },
  });
}
