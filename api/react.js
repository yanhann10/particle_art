/**
 * Vercel edge function: persist a user reaction (favorite / dismiss / star) to
 * scripts/preferences.json via GitHub API.
 *
 * POST /api/react  { piece_id, action: "favorite"|"unfavorite"|"dismiss"|"undismiss"|"star"|"unstar" }
 *              or { actions: [{ piece_id, action }, ...] }
 *   → patches preferences.json marks[piece_id] on GitHub
 *   → returns { ok: true }
 *
 * Required Vercel env vars:
 *   GITHUB_TOKEN  — fine-grained PAT with contents:write on this repo
 *   GITHUB_REPO   — e.g. "yanhann10/particle_art"
 */

const PREFS_PATH = "scripts/preferences.json";
const DELETED_PATH = "scripts/deleted_pieces.json";
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

  const actions = Array.isArray(body.actions)
    ? body.actions
    : [{ piece_id: body.piece_id, action: body.action }];
  if (!actions.length || actions.some(x => !x.piece_id || !x.action)) {
    return new Response(JSON.stringify({ error: "piece_id and action required" }), {
      status: 400, headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  const validActions = ["favorite", "unfavorite", "dismiss", "undismiss", "star", "unstar"];
  if (actions.some(x => !validActions.includes(x.action))) {
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

    // 2. Apply all actions in memory, then persist exactly once. This prevents
    // rapid review decisions from racing and overwriting one another.
    for (const { piece_id, action } of actions) {
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
        delete mark.star;
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

      if (Object.keys(mark).length === 0) delete prefs.marks[piece_id];
      else prefs.marks[piece_id] = mark;
    }

    prefs.updated_at = new Date().toISOString().slice(0, 10);

    // 3. Commit updated preferences.json
    const newContent = JSON.stringify(prefs, null, 2) + "\n";
    const putBody = {
      message: actions.length === 1
        ? `react: ${actions[0].action} ${actions[0].piece_id}`
        : `react: batch ${actions.length} curation decisions`,
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

    // Dismissals also enter the append-only tombstone manifest. Preferences
    // remain the immediate UI fallback if this secondary write is delayed.
    const dismissedIds = actions.filter(x => x.action === "dismiss").map(x => x.piece_id);
    if (dismissedIds.length) {
      const deletedUrl = `https://api.github.com/repos/${repo}/contents/${DELETED_PATH}`;
      const deletedRes = await fetch(`${deletedUrl}?ref=${BRANCH}`, { headers: ghHeaders });
      let deleted = { version: 1, ids: [] }, deletedSha = null;
      if (deletedRes.ok) {
        const file = await deletedRes.json(); deletedSha = file.sha;
        deleted = JSON.parse(atob(file.content.replace(/\n/g, "")));
      }
      deleted.ids = [...new Set([...(deleted.ids || []), ...dismissedIds])].sort();
      deleted.updated_at = new Date().toISOString().slice(0, 10);
      const deletedPut = await fetch(deletedUrl, { method:"PUT", headers:ghHeaders, body:JSON.stringify({
        message:`tombstone ${dismissedIds.length} rejected piece${dismissedIds.length === 1 ? "" : "s"}`,
        content:btoa(unescape(encodeURIComponent(JSON.stringify(deleted,null,2)+"\n"))), branch:BRANCH,
        ...(deletedSha ? { sha:deletedSha } : {}),
      }) });
      if (!deletedPut.ok) console.error(`tombstone PUT ${deletedPut.status}`);
    }

    return new Response(JSON.stringify({ ok: true, applied: actions.length }), {
      headers: { ...cors, "Content-Type": "application/json" },
    });
  } catch (e) {
    return new Response(JSON.stringify({ ok: false, error: String(e) }), {
      status: 502, headers: { ...cors, "Content-Type": "application/json" },
    });
  }
}
