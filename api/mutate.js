/**
 * Vercel edge function: queue a mutation directive via GitHub API.
 *
 * POST /api/mutate  { parent_id, directive }
 *   → appends to scripts/pending_directives.jsonl via GitHub commit
 *   → returns { queued: true }
 *
 * GET /api/mutate?job_id=<id>
 *   → returns { status: "polling" } (kept for compare.html compatibility)
 *
 * Required Vercel env vars:
 *   GITHUB_TOKEN  — fine-grained PAT with contents:write on this repo
 *   GITHUB_REPO   — e.g. "yanhann10/particle_art"
 */

const QUEUE_PATH = "scripts/pending_directives.jsonl";
const BRANCH = "main";

export const config = { runtime: "edge" };

export default async function handler(req) {
  const cors = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };

  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: cors });
  }

  // GET: kept for compare.html compatibility (no real-time job tracking)
  if (req.method === "GET") {
    return new Response(JSON.stringify({ status: "polling" }), {
      headers: { ...cors, "Content-Type": "application/json" },
    });
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

  const { parent_id, directive } = body;
  if (!parent_id || !directive) {
    return new Response(JSON.stringify({ error: "parent_id and directive required" }), {
      status: 400, headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  const token = process.env.GITHUB_TOKEN;
  const repo  = process.env.GITHUB_REPO;
  if (!token || !repo) {
    return new Response(JSON.stringify({ queued: false, reason: "no github config" }), {
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  const apiBase = `https://api.github.com/repos/${repo}/contents/${QUEUE_PATH}`;
  const ghHeaders = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };

  try {
    // 1. Fetch current file (need SHA for update)
    const getRes = await fetch(`${apiBase}?ref=${BRANCH}`, { headers: ghHeaders });
    let existingContent = "";
    let sha = null;
    if (getRes.ok) {
      const file = await getRes.json();
      sha = file.sha;
      existingContent = atob(file.content.replace(/\n/g, ""));
    } else if (getRes.status !== 404) {
      throw new Error(`GitHub GET ${getRes.status}`);
    }

    // 2. Append new directive line
    const entry = {
      source: "user_feedback",
      parent_id,
      directive,
      queued_at: new Date().toISOString(),
    };
    const newContent = existingContent.trimEnd() + "\n" + JSON.stringify(entry) + "\n";

    // 3. Commit updated file
    const putBody = {
      message: `queue: feedback on ${parent_id} — "${directive.slice(0, 60)}"`,
      content: btoa(unescape(encodeURIComponent(newContent))),
      branch: BRANCH,
      ...(sha ? { sha } : {}),
    };
    const putRes = await fetch(apiBase, {
      method: "PUT",
      headers: ghHeaders,
      body: JSON.stringify(putBody),
    });
    if (!putRes.ok) {
      const err = await putRes.text();
      throw new Error(`GitHub PUT ${putRes.status}: ${err.slice(0, 200)}`);
    }

    return new Response(JSON.stringify({ queued: true }), {
      headers: { ...cors, "Content-Type": "application/json" },
    });
  } catch (e) {
    return new Response(JSON.stringify({ queued: false, error: String(e) }), {
      status: 502, headers: { ...cors, "Content-Type": "application/json" },
    });
  }
}
