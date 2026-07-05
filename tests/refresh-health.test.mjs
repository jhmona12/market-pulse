import test from "node:test";
import assert from "node:assert/strict";
import { confirmLivePagesStatus } from "../scripts/refresh/confirm-live-pages.mjs";
import { runSnapshotRefresh } from "../scripts/refresh/run-snapshot-refresh.mjs";

function response(body, ok = true, status = 200) {
  return {
    ok,
    status,
    async json() {
      return body;
    }
  };
}

test("confirms live Pages status when the expected refresh is served", async () => {
  const result = await confirmLivePagesStatus({
    pageUrl: "https://example.com/market-pulse",
    expectedRunId: "12345",
    attempts: 2,
    delayMs: 0,
    fetchImpl: async () => response({
      runId: "12345",
      status: "success",
      publishStatus: "published"
    }),
    sleep: async () => {},
    logger: { log() {}, warn() {} }
  });

  assert.equal(result.confirmed, true);
  assert.equal(result.statusUrl, "https://example.com/market-pulse/data/refresh-status.json");
  assert.equal(result.attemptsUsed, 1);
});

test("does not throw when the live Pages status is stale after deploy", async () => {
  const seenUrls = [];
  const result = await confirmLivePagesStatus({
    pageUrl: "https://example.com/market-pulse/",
    expectedRunId: "new-run",
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
    "https://example.com/market-pulse/data/refresh-status.json",
    "https://example.com/market-pulse/data/refresh-status.json"
  ]);
});

test("does not throw when the live Pages status endpoint is temporarily unavailable", async () => {
  const result = await confirmLivePagesStatus({
    pageUrl: "https://example.com/market-pulse/",
    expectedRunId: "new-run",
    attempts: 1,
    delayMs: 0,
    fetchImpl: async () => response({}, false, 503),
    sleep: async () => {},
    logger: { log() {}, warn() {} }
  });

  assert.equal(result.confirmed, false);
  assert.match(result.lastError, /HTTP 503/);
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
