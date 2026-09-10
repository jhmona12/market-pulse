import assert from "node:assert/strict";
import { test } from "node:test";

import { fetchJsonWithRetry, fetchTextWithRetry } from "../scripts/ingest/http.mjs";

function response({ ok, status, statusText, value, onCancel }) {
  return {
    ok,
    status,
    statusText,
    headers: { get() { return null; } },
    body: { async cancel() { onCancel?.(); } },
    async json() { return value; },
    async text() { return String(value); }
  };
}

test("does not retry permanent HTTP failures and cancels their response body", async () => {
  let calls = 0;
  let cancellations = 0;
  await assert.rejects(
    fetchTextWithRetry("https://example.com/missing", {
      retries: 4,
      fetchImpl: async () => {
        calls += 1;
        return response({ ok: false, status: 404, statusText: "Not Found", onCancel: () => { cancellations += 1; } });
      },
      sleep: async () => {},
      delayForAttempt: () => 0
    }),
    /404 Not Found/
  );
  assert.equal(calls, 1);
  assert.equal(cancellations, 1);
});

test("follows redirects explicitly and cancels intermediate bodies", async () => {
  const urls = [];
  let cancellations = 0;
  const value = await fetchTextWithRetry("https://example.com/start", {
    retries: 0,
    fetchImpl: async (url, options) => {
      urls.push([url, options.redirect]);
      if (url.endsWith("/start")) {
        return {
          ...response({ ok: false, status: 302, statusText: "Found", onCancel: () => { cancellations += 1; } }),
          headers: { get(name) { return name === "location" ? "/final" : null; } }
        };
      }
      return response({ ok: true, status: 200, statusText: "OK", value: "done" });
    }
  });
  assert.equal(value, "done");
  assert.deepEqual(urls, [
    ["https://example.com/start", "manual"],
    ["https://example.com/final", "manual"]
  ]);
  assert.equal(cancellations, 1);
});

test("stops redirect loops at the configured limit", async () => {
  let calls = 0;
  await assert.rejects(
    fetchTextWithRetry("https://example.com/loop", {
      retries: 0,
      maxRedirects: 2,
      fetchImpl: async () => {
        calls += 1;
        return {
          ...response({ ok: false, status: 302, statusText: "Found" }),
          headers: { get(name) { return name === "location" ? "/loop" : null; } }
        };
      }
    }),
    /Redirect limit exceeded after 2 hop/
  );
  assert.equal(calls, 3);
});

test("retries rate limits after cancelling the failed body", async () => {
  let calls = 0;
  let cancellations = 0;
  const payload = await fetchJsonWithRetry("https://example.com/rate-limited", {
    retries: 2,
    fetchImpl: async () => {
      calls += 1;
      if (calls === 1) {
        return response({ ok: false, status: 429, statusText: "Too Many Requests", onCancel: () => { cancellations += 1; } });
      }
      return response({ ok: true, status: 200, statusText: "OK", value: { ready: true } });
    },
    sleep: async () => {},
    delayForAttempt: () => 0
  });
  assert.deepEqual(payload, { ready: true });
  assert.equal(calls, 2);
  assert.equal(cancellations, 1);
});

test("retries transient network errors", async () => {
  let calls = 0;
  const text = await fetchTextWithRetry("https://example.com/transient", {
    retries: 1,
    fetchImpl: async () => {
      calls += 1;
      if (calls === 1) throw new TypeError("fetch failed");
      return response({ ok: true, status: 200, statusText: "OK", value: "ready" });
    },
    sleep: async () => {},
    delayForAttempt: () => 0
  });
  assert.equal(text, "ready");
  assert.equal(calls, 2);
});

test("cross-origin redirects never forward authorization or cookies", async () => {
  const receivedHeaders = [];
  await fetchTextWithRetry("https://oauth.reddit.com/start", {
    retries: 0,
    headers: { Authorization: "Bearer test-only", Cookie: "test-only", Accept: "text/plain" },
    fetchImpl: async (url, options) => {
      receivedHeaders.push(new Headers(options.headers));
      if (url.endsWith("/start")) return {
        ...response({ ok: false, status: 302 }),
        headers: new Headers({ location: "https://example.com/final" })
      };
      return response({ ok: true, status: 200, value: "done" });
    }
  });
  assert.equal(receivedHeaders[0].get("authorization"), "Bearer test-only");
  assert.equal(receivedHeaders[1].get("authorization"), null);
  assert.equal(receivedHeaders[1].get("cookie"), null);
  assert.equal(receivedHeaders[1].get("accept"), "text/plain");
});
