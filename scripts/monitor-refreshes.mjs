import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const timezone = process.env.SCHEDULE_TIMEZONE || "America/Los_Angeles";
const morningCutoff = process.env.REFRESH_MONITOR_MORNING_CUTOFF || "07:30";
const eveningCutoff = process.env.REFRESH_MONITOR_EVENING_CUTOFF || "18:30";
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
    if (key) targets.add(key);
  });

  const status = readJson("data/refresh-status.json") || {};
  if (status.status === "success" && status.refreshTargetKey) targets.add(status.refreshTargetKey);
  return targets;
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

function monitorRefreshes() {
  const completed = completedTargets();
  const { dateKey, localTime, expected } = expectedTargetsForNow();
  const missing = expected.filter((targetKey) => !completed.has(targetKey));
  const summary = {
    generatedAt: now.toISOString(),
    timezone,
    localDate: dateKey,
    localTime,
    morningCutoff,
    eveningCutoff,
    expected,
    missing,
    latestCompletedTargets: [...completed].sort().slice(-8)
  };

  console.log(JSON.stringify(summary, null, 2));
  if (missing.length) {
    console.error(`Missing completed refresh target(s): ${missing.join(", ")}`);
    process.exitCode = 1;
  }
}

export { completedTargets, expectedTargetsForNow, monitorRefreshes };

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  monitorRefreshes();
}
