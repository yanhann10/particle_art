/**
 * Vercel serverless function: drop original piece, re-parent new piece.
 *
 * POST /api/replace { drop_id, new_id }
 *
 * Forwards to VM feedback API which runs the git operations.
 * Falls back gracefully (client handles dismissed via localStorage).
 */

const VM_URL = process.env.MUTATION_API_URL || "";

export const config = { runtime: "edge" };

export default async function handler(req) {
  const cors = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };

  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: cors });
  }

  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "POST only" }), {
      status: 405, headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  let body;
  try { body = await req.json(); } catch {
    return new Response(JSON.stringify({ error: "bad json" }), {
      status: 400, headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  if (!VM_URL) {
    // No VM — client will handle via localStorage drop
    return new Response(JSON.stringify({ ok: false, reason: "no VM configured" }), {
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  try {
    const r = await fetch(`${VM_URL}/replace`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10000),
    });
    const data = await r.json();
    return new Response(JSON.stringify(data), {
      status: r.status, headers: { ...cors, "Content-Type": "application/json" },
    });
  } catch (e) {
    return new Response(JSON.stringify({ ok: false, error: String(e) }), {
      status: 502, headers: { ...cors, "Content-Type": "application/json" },
    });
  }
}
