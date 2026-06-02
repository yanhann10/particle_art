import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const inputPath = path.resolve(__dirname, "..", "ThoughtTrace.jsonl");
const outSamplePath = path.resolve(__dirname, "..", "ThoughtTrace.sample10.jsonl");
const outSummaryPath = path.resolve(__dirname, "..", "ThoughtTrace.summary.json");
const outSignalsPath = path.resolve(__dirname, "..", "ThoughtTrace.signals.json");

function parseConvId(id) {
  // Example: user508_task2_conversation1
  const m = /^user(\d+)_task(\d+)_conversation(\d+)$/.exec(id || "");
  if (!m) return { user: null, task: null, conversation: null, raw: String(id || "") };
  return { user: Number(m[1]), task: Number(m[2]), conversation: Number(m[3]), raw: id };
}

function safeJsonParse(line) {
  try {
    return { ok: true, value: JSON.parse(line) };
  } catch (e) {
    return { ok: false, error: String(e?.message || e) };
  }
}

function clamp01(x) {
  if (!Number.isFinite(x)) return 0;
  return Math.max(0, Math.min(1, x));
}

function reactionSentimentProxy(text) {
  // Very small heuristic: counts a few tokens; returns [-1,1]
  const s = String(text || "").toLowerCase();
  const pos = ["great", "good", "thanks", "helpful", "love", "perfect", "nice", "awesome", "ok", "okay"];
  const neg = ["bad", "annoy", "hate", "wrong", "terrible", "awful", "useless", "stupid", "why does it", "doesn't"];
  let score = 0;
  for (const w of pos) if (s.includes(w)) score += 1;
  for (const w of neg) if (s.includes(w)) score -= 1;
  if (score === 0) return 0;
  return Math.max(-1, Math.min(1, score / 4));
}

function extractSignals(obj) {
  const idInfo = parseConvId(obj?.id);
  const messages = Array.isArray(obj?.messages) ? obj.messages : [];
  const userMsgs = messages.filter((m) => m?.type === "user");
  const assistantMsgs = messages.filter((m) => m?.type === "assistant");

  let reasonsCount = 0;
  const reasonsLabels = new Map();
  let expectation = 0; // style_expectation + constraints-like reasons
  let reasonIntensity = 0; // normalized count

  let reactionCount = 0;
  const reactionLabels = new Map();
  let reactionValence = 0; // [-1,1] proxy

  for (const m of messages) {
    const rs = Array.isArray(m?.reasons) ? m.reasons : [];
    reasonsCount += rs.length;
    for (const r of rs) {
      const lab = String(r?.label || "unknown");
      reasonsLabels.set(lab, (reasonsLabels.get(lab) || 0) + 1);
      if (lab === "style_expectation") expectation += 1;
      if (lab === "context_grounding_and_constraints") expectation += 0.75;
    }

    const reacts = Array.isArray(m?.reactions) ? m.reactions : [];
    reactionCount += reacts.length;
    for (const r of reacts) {
      const lab = String(r?.label || "unknown");
      reactionLabels.set(lab, (reactionLabels.get(lab) || 0) + 1);
      reactionValence += reactionSentimentProxy(r?.content);
    }
  }

  const turns = messages.length;
  reasonIntensity = clamp01(reasonsCount / 4);
  expectation = clamp01(expectation / 3);
  reactionValence = reactionCount > 0 ? Math.max(-1, Math.min(1, reactionValence / reactionCount)) : 0;

  // Reorientation proxy: "presentation_style" or "style_expectation" reactions often indicate mismatch → reorient
  const reorientation = clamp01(((reactionLabels.get("presentation_style") || 0) + (reactionLabels.get("style_expectation") || 0)) / 3);

  // Affirmation proxy: positive valence + low reorientation
  const affirmation = clamp01((reactionValence > 0 ? reactionValence : 0) * (1 - reorientation));

  // Satisfaction proxy: map valence [-1,1] to [0,1], discount if reorientation is high
  const satisfaction = clamp01((reactionValence + 1) / 2) * (1 - 0.65 * reorientation);

  return {
    id: idInfo.raw,
    user: idInfo.user,
    task: idInfo.task,
    conversation: idInfo.conversation,
    turns,
    userTurns: userMsgs.length,
    assistantTurns: assistantMsgs.length,
    reasonsCount,
    reactionsCount: reactionCount,
    reasonsLabels: Object.fromEntries([...reasonsLabels.entries()].sort((a, b) => b[1] - a[1])),
    reactionLabels: Object.fromEntries([...reactionLabels.entries()].sort((a, b) => b[1] - a[1])),
    satisfaction,
    expectation,
    reasonIntensity,
    reactionValence,
    reorientation,
    affirmation,
  };
}

async function readAllSignals() {
  const stream = fs.createReadStream(inputPath, { encoding: "utf8" });
  const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });

  const signals = [];
  const parseErrors = [];
  for await (const line of rl) {
    if (!line.trim()) continue;
    const parsed = safeJsonParse(line);
    if (!parsed.ok) {
      parseErrors.push({ error: parsed.error, linePrefix: line.slice(0, 160) });
      continue;
    }
    signals.push(extractSignals(parsed.value));
  }
  return { signals, parseErrors };
}

function stratifiedSample(signals, fraction, keyFn) {
  const by = new Map();
  for (const s of signals) {
    const k = keyFn(s);
    if (!by.has(k)) by.set(k, []);
    by.get(k).push(s);
  }
  const picked = [];
  for (const [k, arr] of by.entries()) {
    const n = Math.max(1, Math.round(arr.length * fraction));
    // deterministic-ish shuffle based on id hash
    const sorted = [...arr].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    // take evenly spaced
    for (let i = 0; i < n; i++) {
      const idx = Math.floor((i + 0.5) * (sorted.length / n));
      picked.push(sorted[Math.min(sorted.length - 1, idx)]);
    }
  }
  // stable output ordering
  return picked.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

function topK(mapObj, k) {
  return Object.fromEntries(Object.entries(mapObj).slice(0, k));
}

function summarize(signals, parseErrors) {
  const n = signals.length;
  const byTask = new Map();
  const reasonsLabelTotals = new Map();
  const reactionLabelTotals = new Map();

  function accLabelTotals(target, labels) {
    for (const [k, v] of Object.entries(labels || {})) {
      target.set(k, (target.get(k) || 0) + v);
    }
  }

  let sumTurns = 0;
  let sumReasons = 0;
  let sumReacts = 0;
  let sumSat = 0;
  let sumExp = 0;
  let sumReorient = 0;

  for (const s of signals) {
    sumTurns += s.turns;
    sumReasons += s.reasonsCount;
    sumReacts += s.reactionsCount;
    sumSat += s.satisfaction;
    sumExp += s.expectation;
    sumReorient += s.reorientation;

    accLabelTotals(reasonsLabelTotals, s.reasonsLabels);
    accLabelTotals(reactionLabelTotals, s.reactionLabels);

    const t = s.task ?? "unknown";
    if (!byTask.has(t)) byTask.set(t, { n: 0, sat: 0, exp: 0, reorient: 0, turns: 0 });
    const b = byTask.get(t);
    b.n += 1;
    b.sat += s.satisfaction;
    b.exp += s.expectation;
    b.reorient += s.reorientation;
    b.turns += s.turns;
  }

  function avg(x) {
    return n ? x / n : 0;
  }

  const byTaskOut = Object.fromEntries(
    [...byTask.entries()]
      .sort((a, b) => Number(a[0]) - Number(b[0]))
      .map(([t, v]) => [
        String(t),
        {
          n: v.n,
          avgTurns: v.turns / v.n,
          avgSatisfaction: v.sat / v.n,
          avgExpectation: v.exp / v.n,
          avgReorientation: v.reorient / v.n,
        },
      ])
  );

  const sortedReasons = [...reasonsLabelTotals.entries()].sort((a, b) => b[1] - a[1]);
  const sortedReacts = [...reactionLabelTotals.entries()].sort((a, b) => b[1] - a[1]);

  return {
    inputPath,
    conversations: n,
    parseErrors: parseErrors.length,
    avgTurns: avg(sumTurns),
    avgReasonsPerConversation: avg(sumReasons),
    avgReactionsPerConversation: avg(sumReacts),
    avgSatisfaction: avg(sumSat),
    avgExpectation: avg(sumExp),
    avgReorientation: avg(sumReorient),
    topReasonLabels: Object.fromEntries(sortedReasons.slice(0, 12)),
    topReactionLabels: Object.fromEntries(sortedReacts.slice(0, 12)),
    byTask: byTaskOut,
  };
}

async function main() {
  if (!fs.existsSync(inputPath)) {
    console.error(`Missing input: ${inputPath}`);
    process.exit(1);
  }

  const { signals, parseErrors } = await readAllSignals();

  // 10% stratified by task id; fallback: by user if task missing
  const sampleSignals = stratifiedSample(signals, 0.1, (s) => `task:${s.task ?? "unknown"}`);
  const sampleIds = new Set(sampleSignals.map((s) => s.id));

  // Second pass: write sampled original JSONL lines (exact lines, not the derived signals)
  const inStream = fs.createReadStream(inputPath, { encoding: "utf8" });
  const rl = readline.createInterface({ input: inStream, crlfDelay: Infinity });
  const out = fs.createWriteStream(outSamplePath, { encoding: "utf8" });

  for await (const line of rl) {
    if (!line.trim()) continue;
    const parsed = safeJsonParse(line);
    if (!parsed.ok) continue;
    const id = String(parsed.value?.id || "");
    if (sampleIds.has(id)) out.write(line.trimEnd() + "\n");
  }
  out.end();

  const summary = summarize(signals, parseErrors);
  fs.writeFileSync(outSummaryPath, JSON.stringify(summary, null, 2) + "\n", "utf8");

  // Signals (small, safe to ship in-browser)
  fs.writeFileSync(
    outSignalsPath,
    JSON.stringify(
      signals.map((s) => ({
        id: s.id,
        user: s.user,
        task: s.task,
        conversation: s.conversation,
        turns: s.turns,
        reasonsCount: s.reasonsCount,
        reactionsCount: s.reactionsCount,
        reasonsLabels: topK(s.reasonsLabels, 6),
        reactionLabels: topK(s.reactionLabels, 6),
        satisfaction: s.satisfaction,
        expectation: s.expectation,
        reasonIntensity: s.reasonIntensity,
        reactionValence: s.reactionValence,
        reorientation: s.reorientation,
        affirmation: s.affirmation,
      })),
      null,
      2
    ) + "\n",
    "utf8"
  );

  console.log(`Wrote sample: ${path.relative(process.cwd(), outSamplePath)} (${sampleSignals.length}/${signals.length})`);
  console.log(`Wrote summary: ${path.relative(process.cwd(), outSummaryPath)}`);
  console.log(`Wrote signals: ${path.relative(process.cwd(), outSignalsPath)} (${signals.length})`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

