/**
 * Vercel edge function: persist a saved grade config to scripts/saved_configs.json
 * via the GitHub API, so the mutation worker can later read user-saved gradings.
 *
 * POST /api/config  { piece_id, config: { id, hue, sat, bri, con } }
 *   → appends config to saved_configs.configs[piece_id] on GitHub
 *   → returns { ok: true }
 *
 * The client always writes to localStorage first; this is best-effort durable backup.
 *
 * Required Vercel env vars:
 *   GITHUB_TOKEN  — fine-grained PAT with contents:write on this repo
 *   GITHUB_REPO   — e.g. "yanhann10/particle_art"
 */

const CONFIGS_PATH = "scripts/saved_configs.json";
const BRANCH = "main";

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

  const { piece_id, config: cfg } = body;
  if (!piece_id || !cfg || typeof cfg !== "object") {
    return new Response(JSON.stringify({ error: "piece_id and config required" }), {
      status: 400, headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  // Whitelist + coerce the numeric fields we expect from the grade panel.
  const clean = {
    id: Number(cfg.id) || Date.now(),
    hue: Number(cfg.hue) || 0,
    sat: Number(cfg.sat) || 1,
    bri: Number(cfg.bri) || 1,
    con: Number(cfg.con) || 1,
  };

  const token = process.env.GITHUB_TOKEN;
  const repo = process.env.GITHUB_REPO;
  if (!token || !repo) {
    // Graceful degradation: client already wrote to localStorage.
    return new Response(JSON.stringify({ ok: false, reason: "no github config" }), {
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  const apiBase = `https://api.github.com/repos/${repo}/contents/${CONFIGS_PATH}`;
  const ghHeaders = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };

  try {
    // 1. Fetch current saved_configs.json (may not exist yet).
    const getRes = await fetch(`${apiBase}?ref=${BRANCH}`, { headers: ghHeaders });
    let store = { configs: {} };
    let sha = null;
    if (getRes.ok) {
      const file = await getRes.json();
      sha = file.sha;
      store = JSON.parse(atob(file.content.replace(/\n/g, "")));
    } else if (getRes.status !== 404) {
      throw new Error(`GitHub GET ${getRes.status}`);
    }
    if (!store.configs) store.configs = {};

    // 2. Append the config to this piece's list.
    if (!Array.isArray(store.configs[piece_id])) store.configs[piece_id] = [];
    store.configs[piece_id].push(clean);
    store.updated_at = new Date().toISOString();

    // 3. Commit.
    const newContent = JSON.stringify(store, null, 2) + "\n";
    const putBody = {
      message: `config: save grade for ${piece_id}`,
      content: btoa(unescape(encodeURIComponent(newContent))),
      branch: BRANCH,
      ...(sha ? { sha } : {}),
    };
    const putRes = await fetch(apiBase, {
      method: "PUT", headers: ghHeaders, body: JSON.stringify(putBody),
    });
    if (!putRes.ok) {
      const err = await putRes.text();
      throw new Error(`GitHub PUT ${putRes.status}: ${err.slice(0, 200)}`);
    }

    return new Response(JSON.stringify({ ok: true }), {
      headers: { ...cors, "Content-Type": "application/json" },
    });
  } catch (e) {
    return new Response(JSON.stringify({ ok: false, error: String(e) }), {
      status: 502, headers: { ...cors, "Content-Type": "application/json" },
    });
  }
}
