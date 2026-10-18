import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { confirmLivePagesStatus } from "../scripts/refresh/confirm-live-pages.mjs";
import { runModelScoring } from "../scripts/refresh/run-model-scoring.mjs";
import { runSnapshotRefresh } from "../scripts/refresh/run-snapshot-refresh.mjs";
import { shouldUpdateRefreshLedger } from "../scripts/refresh/status-policy.mjs";

const artifactBodies = {
  "data/snapshot.json": { generatedAt: "2026-09-10T08:00:00Z" },
  "data/model-scorebook.json": { rowCount: 500, asOfDate: "2026-09-09" },
  "data/long-horizon-research.json": { rowCount: 500, asOfDate: "2026-09-09" },
  "data/model-monitoring.json": { asOfDate: "2026-09-09" }
};
const expectedArtifacts = Object.fromEntries(Object.entries(artifactBodies).map(([path, body]) =>
  [path, createHash("sha256").update(JSON.stringify(body)).digest("hex")]
));

function response(body, ok = true, status = 200) {
  return {
    ok,
    status,
    async json() {
      return body;
    },
    async text() {
      return JSON.stringify(body);
    }
  };
}

test("confirms live Pages status when the expected refresh is served", async () => {
  const result = await confirmLivePagesStatus({
    pageUrl: "https://example.com/market-pulse",
    expectedRunId: "12345",
    expectedArtifacts,
    attempts: 2,
    delayMs: 0,
    fetchImpl: async (url) => response(artifactBodies[new URL(url).pathname.replace("/market-pulse/", "")] || {
      runId: "12345",
      status: "success",
      publishStatus: "published"
    }),
    sleep: async () => {},
    logger: { log() {}, warn() {} }
  });

  assert.equal(result.confirmed, true);
  assert.equal(result.statusUrl, "https://example.com/market-pulse/data/refresh-status.json?run=12345");
  assert.equal(result.attemptsUsed, 1);
  assert.equal(result.artifactCount, 4);
});

test("does not throw when the live Pages status is stale after deploy", async () => {
  const seenUrls = [];
  const result = await confirmLivePagesStatus({
    pageUrl: "https://example.com/market-pulse/",
    expectedRunId: "new-run",
    expectedArtifacts,
    attempts: 2,
    delayMs: 0,
    fetchImpl: async (url) => {
      seenUrls.push(url);
      return response({
        runId: "old-run",
        status: "success",
        publishStatus: "published"
      });
    },
    sleep: async () => {},
    logger: { log() {}, warn() {} }
  });

  assert.equal(result.confirmed, false);
  assert.equal(result.attemptsUsed, 2);
  assert.match(result.lastError, /old-run/);
  assert.deepEqual(seenUrls, [
    "https://example.com/market-pulse/data/refresh-status.json?run=new-run&attempt=1",
    "https://example.com/market-pulse/data/refresh-status.json?run=new-run&attempt=2"
  ]);
});

test("does not throw when the live Pages status endpoint is temporarily unavailable", async () => {
  const result = await confirmLivePagesStatus({
    pageUrl: "https://example.com/market-pulse/",
    expectedRunId: "new-run",
    expectedArtifacts,
    attempts: 1,
    delayMs: 0,
    fetchImpl: async () => response({}, false, 503),
    sleep: async () => {},
    logger: { log() {}, warn() {} }
  });

  assert.equal(result.confirmed, false);
  assert.match(result.lastError, /HTTP 503/);
});

test("a matching status file cannot conceal stale or missing dashboard artifacts", async () => {
  for (const [path, body] of Object.entries(artifactBodies)) {
    const result = await confirmLivePagesStatus({
      pageUrl: "https://example.com/market-pulse/", expectedRunId: "new-run", expectedArtifacts,
      attempts: 1, logger: { log() {}, warn() {} },
      fetchImpl: async (url, options) => {
        assert.ok(options.signal);
        const requested = new URL(url).pathname.replace("/market-pulse/", "");
        if (requested === path) return response({ ...body, stale: true });
        return response(artifactBodies[requested] || { runId: "new-run", status: "success", publishStatus: "published" });
      }
    });
    assert.equal(result.confirmed, false);
    assert.match(result.lastError, new RegExp(path.replaceAll(".", "\\.")));
  }
});

test("live confirmation recovers when the entire verified bundle becomes available", async () => {
  const result = await confirmLivePagesStatus({
    pageUrl: "https://example.com/market-pulse/", expectedRunId: "new-run", expectedArtifacts,
    attempts: 2, sleep: async () => {}, logger: { log() {}, warn() {} },
    fetchImpl: async (url) => {
      const parsed = new URL(url);
      const path = parsed.pathname.replace("/market-pulse/", "");
      if (path === "data/model-scorebook.json" && parsed.searchParams.get("attempt") === "1") return response({}, false, 404);
      return response(artifactBodies[path] || { runId: "new-run", status: "success", publishStatus: "published" });
    }
  });
  assert.equal(result.confirmed, true);
  assert.equal(result.attemptsUsed, 2);
});

test("the strict publication CLI exits unsuccessfully when confirmation cannot run", () => {
  const result = spawnSync(process.execPath, ["scripts/refresh/confirm-live-pages.mjs"], {
    cwd: new URL("..", import.meta.url), encoding: "utf8", env: { LIVE_CONFIRM_STRICT: "1" }
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /PAGE_URL is required/);
});

test("retries the snapshot refresh when verification fails once", async () => {
  const calls = [];
  const result = await runSnapshotRefresh({
    attempts: 2,
    retryDelayMs: 0,
    runCommand: async (command, args) => {
      calls.push([command, ...args].join(" "));
      if (calls.length === 2) return 1;
      return 0;
    },
    sleep: async () => {},
    logger: { log() {}, warn() {}, error() {} }
  });

  assert.equal(result.ok, true);
  assert.equal(result.attemptsUsed, 2);
  assert.deepEqual(calls, [
    "node scripts/update-data.mjs",
    "npm run verify",
    "node scripts/update-data.mjs",
    "npm run verify"
  ]);
});

test("retries the snapshot refresh when the refresh command fails once", async () => {
  const calls = [];
  const result = await runSnapshotRefresh({
    attempts: 2,
    retryDelayMs: 0,
    runCommand: async (command, args) => {
      calls.push([command, ...args].join(" "));
      return calls.length === 1 ? 1 : 0;
    },
    sleep: async () => {},
    logger: { log() {}, warn() {}, error() {} }
  });

  assert.equal(result.ok, true);
  assert.equal(result.attemptsUsed, 2);
  assert.deepEqual(calls, [
    "node scripts/update-data.mjs",
    "node scripts/update-data.mjs",
    "npm run verify"
  ]);
});

test("reports failure after all snapshot refresh attempts are exhausted", async () => {
  const result = await runSnapshotRefresh({
    attempts: 2,
    retryDelayMs: 0,
    runCommand: async () => 7,
    sleep: async () => {},
    logger: { log() {}, warn() {}, error() {} }
  });

  assert.equal(result.ok, false);
  assert.equal(result.attemptsUsed, 2);
  assert.equal(result.lastPhase, "refresh");
  assert.equal(result.lastStatus, 7);
});

test("retries a transient tactical scoring failure before running the strategic scorer", async () => {
  const calls = [];
  const result = await runModelScoring({
    attempts: 2,
    retryDelayMs: 0,
    python: "python",
    runCommand: async (_command, args) => {
      calls.push(args.includes("--score-reference-cache") ? "strategic" : "tactical");
      return calls.length === 1 ? 1 : 0;
    },
    sleep: async () => {},
    logger: { log() {}, warn() {}, error() {} }
  });

  assert.equal(result.ok, true);
  assert.equal(result.attemptsUsed, 2);
  assert.deepEqual(calls, ["tactical", "tactical", "strategic"]);
});

test("a delayed EOD feed gets a bounded settlement wait instead of an immediate repeat", async () => {
  // Replay first-attempt failure / first observed recovery from the October 15-16 logs.
  for (const [failedAt, recoveredAt] of [
    ["2026-10-16T01:26:49Z", "2026-10-16T01:41:15Z"],
    ["2026-10-17T01:22:47Z", "2026-10-17T01:34:48Z"]
  ]) {
    let now = Date.parse(failedAt);
    const calls = [];
    const waits = [];
    const result = await runModelScoring({
      python: "python", attempts: 2, retryDelayMs: 90000,
      runCommand: async (_command, args) => {
        const phase = args.includes("--score-reference-cache") ? "strategic" : "tactical";
        calls.push(phase);
        return phase === "tactical" && now < Date.parse(recoveredAt) ? 75 : 0;
      },
      sleep: async (ms) => { waits.push(ms); now += ms; },
      logger: { log() {}, warn() {}, error() {} }
    });
    assert.equal(result.ok, true);
    assert.deepEqual(waits, [15 * 60000]);
    assert.deepEqual(calls, ["tactical", "tactical", "strategic"]);
  }
});

test("persistent incomplete prices never become a successful model refresh", async () => {
  const calls = [];
  const waits = [];
  const result = await runModelScoring({
    python: "python", attempts: 2,
    runCommand: async (_command, args) => { calls.push(args); return 75; },
    sleep: async (ms) => waits.push(ms), logger: { log() {}, warn() {}, error() {} }
  });
  assert.equal(result.ok, false);
  assert.equal(result.lastStatus, 75);
  assert.equal(result.strategicOutcome, "skipped");
  assert.equal(calls.length, 2);
  assert.deepEqual(waits, [15 * 60000]);
});

test("generic model errors keep the short retry instead of waiting for price settlement", async () => {
  for (const failedPhase of ["tactical", "strategic"]) {
    const waits = [];
    const result = await runModelScoring({
      runCommand: async (_command, args) => {
        const phase = args.includes("--score-reference-cache") ? "strategic" : "tactical";
        return phase === failedPhase ? 1 : 0;
      },
      sleep: async (ms) => waits.push(ms), logger: { log() {}, warn() {}, error() {} }
    });
    assert.equal(result.ok, false);
    assert.equal(result.lastPhase, failedPhase);
    assert.deepEqual(waits, [90000]);
  }
});

test("price settlement waits cannot exceed the workflow's fifteen-minute retry budget", async () => {
  const waits = [];
  await runModelScoring({
    readinessRetryDelayMs: 24 * 60 * 60000,
    runCommand: async () => 75,
    sleep: async (ms) => waits.push(ms), logger: { log() {}, warn() {}, error() {} }
  });
  assert.deepEqual(waits, [15 * 60000]);
});

test("retries only the strategic scorer after the tactical cache is ready", async () => {
  const calls = [];
  let strategicCalls = 0;
  const result = await runModelScoring({
    attempts: 2,
    retryDelayMs: 0,
    python: "python",
    runCommand: async (_command, args) => {
      const phase = args.includes("--score-reference-cache") ? "strategic" : "tactical";
      calls.push(phase);
      if (phase === "strategic") strategicCalls += 1;
      return phase === "strategic" && strategicCalls === 1 ? 1 : 0;
    },
    sleep: async () => {},
    logger: { log() {}, warn() {}, error() {} }
  });

  assert.equal(result.ok, true);
  assert.deepEqual(calls, ["tactical", "strategic", "strategic"]);
});

test("model retry exhaustion fails without running a blocked dependent phase", async () => {
  for (const failedPhase of ["tactical", "strategic"]) {
    const calls = [];
    const result = await runModelScoring({
      attempts: 2,
      retryDelayMs: 0,
      runCommand: async (_command, args) => {
        const phase = args.includes("--score-reference-cache") ? "strategic" : "tactical";
        calls.push(phase);
        return phase === failedPhase ? 7 : 0;
      },
      sleep: async () => {},
      logger: { log() {}, warn() {}, error() {} }
    });
    assert.equal(result.ok, false);
    assert.equal(result.attemptsUsed, 2);
    assert.equal(result.lastPhase, failedPhase);
    assert.equal(result.lastStatus, 7);
    assert.deepEqual(calls, failedPhase === "tactical"
      ? ["tactical", "tactical"] : ["tactical", "strategic", "strategic"]);
    assert.equal(result.strategicOutcome, failedPhase === "tactical" ? "skipped" : "failure");
  }
});

test("records a refresh target only after Pages publication is confirmed", () => {
  const base = {
    status: "success",
    publishStatus: "published",
    targetKey: "2026-09-09-evening"
  };
  assert.equal(shouldUpdateRefreshLedger({ ...base, publishConfirmed: false }), false);
  assert.equal(shouldUpdateRefreshLedger({ ...base, publishConfirmed: true }), true);
  assert.equal(shouldUpdateRefreshLedger({ ...base, targetKey: "manual", publishConfirmed: true }), false);
  assert.equal(shouldUpdateRefreshLedger({ ...base, status: "failure", publishConfirmed: true }), false);
});
