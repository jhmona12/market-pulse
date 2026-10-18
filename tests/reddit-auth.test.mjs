import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { checkRedditAccess, fetchRedditAccessToken, RedditAccessError } from "../scripts/ingest/reddit-auth.mjs";

const env = {
  REDDIT_CLIENT_ID: "fixture-client",
  REDDIT_CLIENT_SECRET: "fixture-secret",
  REDDIT_USER_AGENT: "linux:market-pulse-test:v1 (by /u/example)"
};
const token = { access_token: "fixture-token", token_type: "bearer" };
const listing = { kind: "Listing", data: { children: [
  { kind: "t3", data: { id: "example", title: "Content must never be logged", score: 42, num_comments: 5 } }
] } };
const response = (data, status = 200) => new Response(JSON.stringify(data), { status });

test("Reddit diagnostic checks token then OAuth listing and returns only safe counts", async () => {
  const calls = [];
  const result = await checkRedditAccess(env, { fetchImpl: async (url, options) => {
    calls.push({ url, options });
    return response(calls.length === 1 ? token : listing);
  } });
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, "https://www.reddit.com/api/v1/access_token");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.body.toString(), "grant_type=client_credentials");
  assert.equal(calls[0].options.headers.authorization, `Basic ${Buffer.from("fixture-client:fixture-secret").toString("base64")}`);
  assert.equal(calls[1].url, "https://oauth.reddit.com/r/wallstreetbets/hot?limit=5");
  assert.equal(calls[1].options.headers.authorization, "Bearer fixture-token");
  for (const call of calls) {
    assert.equal(call.options.headers["user-agent"], env.REDDIT_USER_AGENT);
    assert.equal(call.options.redirect, "error");
    assert.ok(call.options.signal instanceof AbortSignal);
  }
  assert.deepEqual(result, { status: "passed", authMode: "oauth", subreddit: "wallstreetbets", postsReturned: 1, postsWithEngagementMetrics: 1 });
  assert.doesNotMatch(JSON.stringify(result), /fixture|Content must never/);
});

test("Reddit diagnostic fails before making requests if any secret is missing or blank", async () => {
  for (const key of Object.keys(env)) {
    for (const value of [undefined, "", "  "]) {
      await assert.rejects(checkRedditAccess({ ...env, [key]: value }, {
        fetchImpl: () => assert.fail("No network calls with incomplete configuration")
      }), (error) => error instanceof RedditAccessError && error.message.includes(key));
    }
  }
});

test("Production Reddit auth retains its explicit public fallback when credentials are absent", async () => {
  const result = await fetchRedditAccessToken({ fetchImpl: () => assert.fail("Unexpected network call") });
  assert.equal(result.token, null);
  assert.equal(result.mode, "public");
});

test("Reddit diagnostic distinguishes denied, rate-limited and server errors without leaking response bodies", async () => {
  for (const status of [401, 403, 429, 503]) {
    for (const failingCall of [1, 2]) {
      let calls = 0;
      await assert.rejects(checkRedditAccess(env, { fetchImpl: async () => {
        calls += 1;
        return calls === failingCall ? response({ error: "fixture-secret fixture-token" }, status) : response(token);
      } }), (error) => {
        assert.ok(error instanceof RedditAccessError);
        assert.match(error.message, new RegExp(`HTTP ${status}`));
        assert.match(error.message, new RegExp(failingCall === 1 ? "OAuth token" : "Authenticated listing"));
        assert.doesNotMatch(error.message, /fixture/);
        return true;
      });
      assert.equal(calls, failingCall, "No RSS fallback or auth retry can mask a failure");
    }
  }
});

test("Reddit auth rejects malformed tokens, including successful HTTP error responses", async () => {
  for (const data of [null, {}, { ...token, error: "invalid_client" }, { ...token, access_token: " " },
    { ...token, access_token: 123 }, { ...token, token_type: "not-bearer" }]) {
    await assert.rejects(checkRedditAccess(env, { fetchImpl: async () => response(data) }), /valid bearer token/);
  }
});

test("Reddit diagnostic rejects JSON that is not a post listing", async () => {
  for (const data of [{ error: 403 }, { kind: "Listing", data: { children: {} } },
    { kind: "Listing", data: { children: [{ kind: "t1", data: {} }] } }]) {
    let calls = 0;
    await assert.rejects(checkRedditAccess(env, { fetchImpl: async () => response(++calls === 1 ? token : data) }), /not a Reddit post listing/);
  }
});

test("Reddit diagnostic does not invent engagement metrics for empty or incomplete samples", async () => {
  for (const children of [[], [{ kind: "t3", data: { score: "42", num_comments: null } }]]) {
    let calls = 0;
    const result = await checkRedditAccess(env, { fetchImpl: async () => response(++calls === 1 ? token : { kind: "Listing", data: { children } }) });
    assert.equal(result.postsReturned, children.length);
    assert.equal(result.postsWithEngagementMetrics, 0);
  }
});

test("Reddit diagnostic sanitizes network and JSON parsing exceptions", async () => {
  for (const fetchImpl of [
    async () => { throw new Error("Leaked header: fixture-secret"); },
    async () => new Response("fixture-token invalid JSON", { status: 200 })
  ]) {
    await assert.rejects(checkRedditAccess(env, { fetchImpl }), (error) => {
      assert.ok(error instanceof RedditAccessError);
      assert.doesNotMatch(error.message, /fixture|Leaked/);
      return true;
    });
  }
});

test("Reddit auth request has a bounded timeout", async () => {
  await assert.rejects(checkRedditAccess(env, {
    timeoutMs: 5,
    fetchImpl: (_url, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(new Error("fixture-secret")), { once: true });
    })
  }), /OAuth token: request timed out/);
});

test("Reddit diagnostic workflow is isolated from refresh, deployment and AI credentials", async () => {
  const workflow = await readFile(new URL("../.github/workflows/check-reddit-auth.yml", import.meta.url), "utf8");
  for (const name of Object.keys(env)) assert.ok(workflow.includes(name + ": ${{ secrets." + name + " }}"));
  assert.match(workflow, /contents: read/);
  assert.match(workflow, /persist-credentials: false/);
  assert.match(workflow, /timeout-minutes: 3/);
  assert.match(workflow, /npm run check:reddit/);
  assert.doesNotMatch(workflow, /OPENAI_API_KEY|npm run refresh|score_live_rank_model|deploy-pages|upload-artifact/);
});
