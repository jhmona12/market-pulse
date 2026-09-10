import assert from "node:assert/strict";
import test from "node:test";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expectedTargetsForNow } from "../scripts/monitor-refreshes.mjs";

test("a monitor delayed past midnight still checks the prior evening", () => {
  const result = expectedTargetsForNow(new Date("2026-09-10T08:20:28Z"));
  assert.deepEqual(result.expected, ["2026-09-09-morning", "2026-09-09-evening"]);
});

test("each window rolls forward only when its own cutoff is due", () => {
  assert.deepEqual(expectedTargetsForNow(new Date("2026-09-10T14:29:59Z")).expected,
    ["2026-09-09-morning", "2026-09-09-evening"]);
  assert.deepEqual(expectedTargetsForNow(new Date("2026-09-10T14:30:00Z")).expected,
    ["2026-09-10-morning", "2026-09-09-evening"]);
  assert.deepEqual(expectedTargetsForNow(new Date("2026-09-11T01:30:00Z")).expected,
    ["2026-09-10-morning", "2026-09-10-evening"]);
});

test("prior Pacific dates survive DST and year boundaries", () => {
  for (const [instant, day] of [
    ["2026-03-08T10:15:00Z", "2026-03-07"],
    ["2026-11-01T09:15:00Z", "2026-10-31"],
    ["2027-01-01T09:15:00Z", "2026-12-31"]
  ]) {
    assert.deepEqual(expectedTargetsForNow(new Date(instant)).expected, [`${day}-morning`, `${day}-evening`]);
  }
});

test("the monitor CLI exits unsuccessfully for the missed evening after midnight", (t) => {
  const root = mkdtempSync(join(tmpdir(), "market-pulse-monitor-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, "scripts"));
  mkdirSync(join(root, "data"));
  writeFileSync(join(root, "scripts/monitor-refreshes.mjs"), readFileSync(new URL("../scripts/monitor-refreshes.mjs", import.meta.url)));
  writeFileSync(join(root, "data/refresh-ledger.json"), JSON.stringify({ successfulTargets: ["2026-09-09-morning"] }));
  const result = spawnSync(process.execPath, ["scripts/monitor-refreshes.mjs"], {
    cwd: root, encoding: "utf8",
    env: { ACTUAL_START_UTC_OVERRIDE: "2026-09-10T08:20:28Z", SCHEDULE_TIMEZONE: "America/Los_Angeles" }
  });
  assert.equal(result.status, 1);
  assert.deepEqual(JSON.parse(result.stdout).missing, ["2026-09-09-evening"]);
});
