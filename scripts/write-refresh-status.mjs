import { readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const status = process.argv[2] || "unknown";
const message = process.argv.slice(3).join(" ").trim() || process.env.REFRESH_STATUS_MESSAGE || null;

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

async function readJson(path) {
  try {
    return JSON.parse(await readFile(join(root, path), "utf8"));
  } catch {
    return null;
  }
}

const snapshot = await readJson("data/snapshot.json");
const scorebook = await readJson("data/model-scorebook.json");
const repository = process.env.GITHUB_REPOSITORY || null;
const runId = process.env.GITHUB_RUN_ID || null;

const payload = {
  generatedAt: new Date().toISOString(),
  status,
  message,
  workflow: process.env.GITHUB_WORKFLOW || "Refresh Market Data",
  eventName: process.env.GITHUB_EVENT_NAME || null,
  repository,
  runId,
  runAttempt: process.env.GITHUB_RUN_ATTEMPT || null,
  runUrl: repository && runId ? `https://github.com/${repository}/actions/runs/${runId}` : null,
  headRef: process.env.GITHUB_REF_NAME || null,
  headSha: process.env.GITHUB_SHA || null,
  scheduledUtc: process.env.SCHEDULED_UTC || null,
  scheduledPacific: process.env.SCHEDULED_PACIFIC || null,
  actualStartUtc: process.env.ACTUAL_START_UTC || null,
  scheduleDelayMinutes: finiteNumber(process.env.SCHEDULE_DELAY_MINUTES),
  snapshotGeneratedAt: snapshot?.generatedAt || null,
  modelAsOfDate: snapshot?.model?.asOfDate || scorebook?.asOfDate || null,
  modelScoredCount: finiteNumber(snapshot?.model?.scoredCount ?? scorebook?.rowCount),
  scorebookRows: finiteNumber(scorebook?.rowCount),
  opportunities: finiteNumber(snapshot?.opportunities?.length)
};

await writeFile(join(root, "data/refresh-status.json"), `${JSON.stringify(payload, null, 2)}\n`);
