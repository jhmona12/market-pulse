import { appendFileSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const schedule = process.env.SCHEDULE || "";
const eventName = process.env.GITHUB_EVENT_NAME || "";
const timezone = process.env.SCHEDULE_TIMEZONE || "America/Los_Angeles";
const actualStart = process.env.ACTUAL_START_UTC_OVERRIDE
  ? new Date(process.env.ACTUAL_START_UTC_OVERRIDE)
  : new Date();

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

function localDateKey(parts) {
  return `${parts.year}-${two(parts.month)}-${two(parts.day)}`;
}

function zonedParts(date, timeZone) {
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

function zonedDateToUtc({ year, month, day, hour, minute, second = 0 }, timeZone) {
  const utcGuess = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  const actual = zonedParts(utcGuess, timeZone);
  const expectedEpoch = Date.UTC(year, month - 1, day, hour, minute, second);
  const actualEpoch = Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute, actual.second);
  return new Date(utcGuess.getTime() + expectedEpoch - actualEpoch);
}

function subtractOneLocalDay(parts) {
  const date = new Date(Date.UTC(parts.year, parts.month - 1, parts.day));
  date.setUTCDate(date.getUTCDate() - 1);
  return {
    year: date.getUTCFullYear(),
    month: date.getUTCMonth() + 1,
    day: date.getUTCDate(),
    hour: parts.hour,
    minute: parts.minute,
    second: parts.second || 0
  };
}

function targetWindowForHour(hour) {
  if (hour === 5) return "morning";
  if (hour === 16) return "evening";
  return "off_window";
}

function ledgerHasTarget(targetKey) {
  const ledger = readJson("data/refresh-ledger.json") || {};
  const successfulTargets = Array.isArray(ledger.successfulTargets) ? ledger.successfulTargets : [];
  if (successfulTargets.some((entry) => entry === targetKey || entry?.targetKey === targetKey)) return true;

  const prior = readJson("data/refresh-status.json") || {};
  return prior.status === "success" && prior.refreshTargetKey === targetKey;
}

function writeOutputs(values) {
  Object.entries(values).forEach(([key, value]) => {
    console.log(`${key}=${value ?? ""}`);
  });

  if (process.env.GITHUB_OUTPUT) {
    appendFileSync(
      process.env.GITHUB_OUTPUT,
      Object.entries(values)
        .map(([key, value]) => `${key}=${value ?? ""}`)
        .join("\n") + "\n"
    );
  }

  if (process.env.GITHUB_ENV) {
    const envMap = {
      scheduled_utc: "SCHEDULED_UTC",
      scheduled_pacific: "SCHEDULED_PACIFIC",
      actual_start_utc: "ACTUAL_START_UTC",
      schedule_delay_minutes: "SCHEDULE_DELAY_MINUTES",
      refresh_target_key: "REFRESH_TARGET_KEY",
      refresh_target_window: "REFRESH_TARGET_WINDOW",
      skip_reason: "REFRESH_SKIP_REASON"
    };
    appendFileSync(
      process.env.GITHUB_ENV,
      Object.entries(envMap)
        .map(([key, envKey]) => `${envKey}=${values[key] ?? ""}`)
        .join("\n") + "\n"
    );
  }
}

if (eventName === "workflow_dispatch") {
  writeOutputs({
    run_refresh: "true",
    scheduled_utc: "manual",
    scheduled_pacific: "manual",
    actual_start_utc: actualStart.toISOString(),
    schedule_delay_minutes: 0,
    refresh_target_key: "manual",
    refresh_target_window: "manual",
    skip_reason: ""
  });
  process.exit(0);
}

const [minuteRaw, hourRaw] = schedule.trim().split(/\s+/);
const scheduledMinute = Number(minuteRaw);
const scheduledHour = Number(hourRaw);

if (!Number.isInteger(scheduledMinute) || !Number.isInteger(scheduledHour)) {
  writeOutputs({
    run_refresh: "false",
    scheduled_utc: "",
    scheduled_pacific: "",
    actual_start_utc: actualStart.toISOString(),
    schedule_delay_minutes: "",
    refresh_target_key: "",
    refresh_target_window: "unknown",
    skip_reason: `unknown schedule slot: ${schedule || "empty"}`
  });
  process.exit(0);
}

const actualLocal = zonedParts(actualStart, timezone);
let scheduledLocal = {
  year: actualLocal.year,
  month: actualLocal.month,
  day: actualLocal.day,
  hour: scheduledHour,
  minute: scheduledMinute,
  second: 0
};
let scheduledUtc = zonedDateToUtc(scheduledLocal, timezone);
if (scheduledUtc.getTime() - actualStart.getTime() > 60 * 60 * 1000) {
  scheduledLocal = subtractOneLocalDay(scheduledLocal);
  scheduledUtc = zonedDateToUtc(scheduledLocal, timezone);
}

const targetWindow = targetWindowForHour(scheduledHour);
const targetKey = targetWindow === "off_window" ? "" : `${localDateKey(scheduledLocal)}-${targetWindow}`;
const alreadyRefreshed = targetKey ? ledgerHasTarget(targetKey) : false;
const shouldRun = targetWindow !== "off_window" && !alreadyRefreshed;
const delayMinutes = Math.round((actualStart.getTime() - scheduledUtc.getTime()) / 60000);
const scheduledPacific = `${localDateKey(scheduledLocal)}T${two(scheduledHour)}:${two(scheduledMinute)}:00[${timezone}]`;
const skipReason = targetWindow === "off_window"
  ? "scheduled slot is outside the Pacific refresh windows"
  : alreadyRefreshed
    ? `${targetWindow} refresh already succeeded for ${localDateKey(scheduledLocal)}`
    : "";

writeOutputs({
  run_refresh: shouldRun ? "true" : "false",
  scheduled_utc: scheduledUtc.toISOString(),
  scheduled_pacific: scheduledPacific,
  actual_start_utc: actualStart.toISOString(),
  schedule_delay_minutes: delayMinutes,
  refresh_target_key: targetKey,
  refresh_target_window: targetWindow,
  skip_reason: skipReason
});
