/**
 * Vercel edge function: persist a user reaction (favorite / dismiss / star) to
 * scripts/preferences.json via GitHub API.
 *
 * POST /api/react  { piece_id, action: "favorite"|"unfavorite"|"dismiss"|"undismiss"|"star"|"unstar" }
 *   → patches preferences.json marks[piece_id] on GitHub
 *   → returns { ok: true }
 *
 * Required Vercel env vars:
 *   GITHUB_TOKEN  — fine-grained PAT with contents:write on this repo
 *   GITHUB_REPO   — e.g. "yanhann10/particle_art"
 */

const PREFS_PATH = "scripts/preferences.json";
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

  const { piece_id, action } = body;
  if (!piece_id || !action) {
    return new Response(JSON.stringify({ error: "piece_id and action required" }), {
      status: 400, headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  const validActions = ["favorite", "unfavorite", "dismiss", "undismiss", "star", "unstar"];
  if (!validActions.includes(action)) {
    return new Response(JSON.stringify({ error: `action must be one of: ${validActions.join(", ")}` }), {
      status: 400, headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  const token = process.env.GITHUB_TOKEN;
  const repo  = process.env.GITHUB_REPO;
  if (!token || !repo) {
    // Graceful degradation: client already wrote to localStorage
    return new Response(JSON.stringify({ ok: false, reason: "no github config" }), {
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  const apiBase = `https://api.github.com/repos/${repo}/contents/${PREFS_PATH}`;
  const ghHeaders = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };

  try {
    // 1. Fetch current preferences.json
    const getRes = await fetch(`${apiBase}?ref=${BRANCH}`, { headers: ghHeaders });
    let prefs = { marks: {} };
    let sha = null;
    if (getRes.ok) {
      const file = await getRes.json();
      sha = file.sha;
      prefs = JSON.parse(atob(file.content.replace(/\n/g, "")));
    } else if (getRes.status !== 404) {
      throw new Error(`GitHub GET ${getRes.status}`);
    }

    if (!prefs.marks) prefs.marks = {};

    // 2. Apply the action
    const mark = prefs.marks[piece_id] || {};
    switch (action) {
      case "favorite":
        mark.favorite = true;
        delete mark.drop;
        break;
      case "unfavorite":
        delete mark.favorite;
        break;
      case "dismiss":
        mark.drop = true;
        delete mark.favorite;
        break;
      case "undismiss":
        delete mark.drop;
        break;
      case "star":
        mark.star = true;
        break;
      case "unstar":
        delete mark.star;
        break;
    }

    // If the mark is now empty, remove it entirely
    if (Object.keys(mark).length === 0) {
      delete prefs.marks[piece_id];
    } else {
      prefs.marks[piece_id] = mark;
    }

    prefs.updated_at = new Date().toISOString().slice(0, 10);

    // 3. Commit updated preferences.json
    const newContent = JSON.stringify(prefs, null, 2) + "\n";
    const putBody = {
      message: `react: ${action} ${piece_id}`,
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

    return new Response(JSON.stringify({ ok: true }), {
      headers: { ...cors, "Content-Type": "application/json" },
    });
  } catch (e) {
    return new Response(JSON.stringify({ ok: false, error: String(e) }), {
      status: 502, headers: { ...cors, "Content-Type": "application/json" },
    });
  }
}
