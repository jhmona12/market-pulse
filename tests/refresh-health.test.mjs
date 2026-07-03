import test from "node:test";
import assert from "node:assert/strict";
import { confirmLivePagesStatus } from "../scripts/refresh/confirm-live-pages.mjs";

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
