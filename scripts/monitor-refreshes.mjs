import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const timezone = process.env.SCHEDULE_TIMEZONE || "America/Los_Angeles";
const morningCutoff = process.env.REFRESH_MONITOR_MORNING_CUTOFF || "07:30";
const eveningCutoff = process.env.REFRESH_MONITOR_EVENING_CUTOFF || "18:30";
const liveDashboardBaseUrl = process.env.LIVE_DASHBOARD_BASE_URL || "";
const now = process.env.ACTUAL_START_UTC_OVERRIDE ? new Date(process.env.ACTUAL_START_UTC_OVERRIDE) : new Date();

function readJson(path) {
  try {
    return JSON.parse(readFileSync(join(root, path), "utf8"));
  } catch {
    return null;
  }
}

function two(value) {
  return String(value).padStart(2, "0");
}

function localParts(date, timeZone) {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23"
  });
  const mapped = Object.fromEntries(formatter.formatToParts(date).map((part) => [part.type, part.value]));
  return {
    year: Number(mapped.year),
    month: Number(mapped.month),
    day: Number(mapped.day),
    hour: Number(mapped.hour),
    minute: Number(mapped.minute),
    second: Number(mapped.second)
  };
}

function localDateKey(parts) {
  return `${parts.year}-${two(parts.month)}-${two(parts.day)}`;
}

function minutesFromClock(value) {
  const [hour, minute] = String(value || "").split(":").map(Number);
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) throw new Error(`Invalid monitor cutoff: ${value}`);
  return hour * 60 + minute;
}

function completedTargets() {
  const targets = new Set();
  const ledger = readJson("data/refresh-ledger.json") || {};
  (Array.isArray(ledger.successfulTargets) ? ledger.successfulTargets : []).forEach((entry) => {
    const key = typeof entry === "string" ? entry : entry?.targetKey;
    const publishStatus = typeof entry === "string" ? "published" : entry?.publishStatus;
    if (key && (!publishStatus || publishStatus === "published")) targets.add(key);
  });

  const status = readJson("data/refresh-status.json") || {};
  if (status.status === "success" && status.publishStatus === "published" && status.refreshTargetKey) {
    targets.add(status.refreshTargetKey);
  }
  return targets;
}

function dashboardUrl(path) {
  if (!liveDashboardBaseUrl) return null;
  return new URL(path, liveDashboardBaseUrl.endsWith("/") ? liveDashboardBaseUrl : `${liveDashboardBaseUrl}/`).toString();
}

async function fetchJson(url) {
  const response = await fetch(url, {
    cache: "no-store",
    signal: AbortSignal.timeout(15000)
  });
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
  return response.json();
}

async function liveCompletedTargets() {
  const targets = new Set();
  const ledgerUrl = dashboardUrl("data/refresh-ledger.json");
  const statusUrl = dashboardUrl("data/refresh-status.json");
  if (!ledgerUrl || !statusUrl) return { targets, checked: false, errors: [] };

  const errors = [];
  try {
    const ledger = await fetchJson(ledgerUrl);
    (Array.isArray(ledger?.successfulTargets) ? ledger.successfulTargets : []).forEach((entry) => {
      const key = typeof entry === "string" ? entry : entry?.targetKey;
      const publishStatus = typeof entry === "string" ? "published" : entry?.publishStatus;
      if (key && (!publishStatus || publishStatus === "published")) targets.add(key);
    });
  } catch (error) {
    errors.push(`live ledger unavailable: ${error.message}`);
  }

  try {
    const status = await fetchJson(statusUrl);
    if (status?.status === "success" && status?.publishStatus === "published" && status?.refreshTargetKey) {
      targets.add(status.refreshTargetKey);
    }
  } catch (error) {
    errors.push(`live status unavailable: ${error.message}`);
  }

  return { targets, checked: true, errors };
}

function expectedTargetsForNow(date = now) {
  const parts = localParts(date, timezone);
  const dateKey = localDateKey(parts);
  const currentMinutes = parts.hour * 60 + parts.minute;
  const expected = [];
  if (currentMinutes >= minutesFromClock(morningCutoff)) expected.push(`${dateKey}-morning`);
  if (currentMinutes >= minutesFromClock(eveningCutoff)) expected.push(`${dateKey}-evening`);
  return { dateKey, localTime: `${two(parts.hour)}:${two(parts.minute)}:${two(parts.second)}`, expected };
}

async function monitorRefreshes() {
  const completed = completedTargets();
  const liveCompleted = await liveCompletedTargets();
  const { dateKey, localTime, expected } = expectedTargetsForNow();
  const missing = expected.filter((targetKey) => !completed.has(targetKey));
  const missingLive = liveCompleted.checked
    ? expected.filter((targetKey) => !liveCompleted.targets.has(targetKey))
    : [];
  const summary = {
    generatedAt: now.toISOString(),
    timezone,
    localDate: dateKey,
    localTime,
    morningCutoff,
    eveningCutoff,
    expected,
    missing,
    missingLive,
    liveDashboardBaseUrl: liveDashboardBaseUrl || null,
    liveCheckErrors: liveCompleted.errors,
    latestCompletedTargets: [...completed].sort().slice(-8),
    latestLiveCompletedTargets: [...liveCompleted.targets].sort().slice(-8)
  };

  console.log(JSON.stringify(summary, null, 2));
  if (missing.length) {
    console.error(`Missing completed refresh target(s): ${missing.join(", ")}`);
    process.exitCode = 1;
  }
  if (missingLive.length) {
    console.error(`Live dashboard is missing refresh target(s): ${missingLive.join(", ")}`);
    process.exitCode = 1;
  }
  if (liveCompleted.errors.length) {
    console.error(`Live dashboard refresh check could not read all required files: ${liveCompleted.errors.join("; ")}`);
    process.exitCode = 1;
  }
}

export { completedTargets, expectedTargetsForNow, monitorRefreshes };

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await monitorRefreshes();
}
