import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { buildAiDeeperRead, deterministicDeeperRead } from "../scripts/update-data.mjs";

const candidates = JSON.parse(readFileSync(new URL("./fixtures/deeper-read-candidates.json", import.meta.url)));

test("nonempty deterministic Deeper Read uses existing source-summary logic", () => {
  const result = deterministicDeeperRead(candidates, "AI output did not pass the evidence guardrails.");
  assert.equal(result.status, "ready");
  assert.equal(result.cards.length, 1);
  assert.match(result.cards[0].thesis, /^Higher refinancing costs could slow investment/);
  assert.equal(result.cards[0].sourceRef, "S1");
  assert.equal(result.cards[0].url, candidates[0].url);
  assert.match(result.summary, /AI output did not pass/);
});

test("missing API key does not prevent source-based Deeper Read", async () => {
  const result = await buildAiDeeperRead({ apiKey: "", candidates, history: {} });
  assert.equal(result.cards.length, 1);
  assert.match(result.summary, /OPENAI_API_KEY was not configured/);
});

test("no candidates returns an explicit thin section rather than invented analysis", () => {
  const result = deterministicDeeperRead([]);
  assert.equal(result.status, "thin");
  assert.deepEqual(result.cards, []);
});

test("accepted AI analysis is preserved with source-owned attribution", async () => {
  const thesis = "Debt maturities, rather than aggregate earnings, identify refinancing exposure.";
  const result = await buildAiDeeperRead({
    apiKey: "fixture-not-a-real-key", aiModel: "gpt-5-nano", candidates,
    sourceTape: candidates.map((candidate) => ({ ...candidate, id: candidate.sourceRef })),
    sourceRefIds: ["S1"], recordUsage: async () => {},
    fetchImpl: async () => ({ ok: true, json: async () => ({ output_text: JSON.stringify({
      status: "ready", cards: [{
        sourceRef: "S1", title: "AI cannot override the source title", thesis,
        whyItMatters: "Cash reserves and fixed-rate borrowing reduce immediate exposure.",
        marketReadThrough: "Monitor: interest coverage and maturity schedules.",
        variantAngle: "Inference: refinancing costs could weigh on investment.", confidence: "Medium"
      }]
    }) }) })
  });
  assert.equal(result.status, "ready");
  assert.equal(result.cards.length, 1);
  assert.equal(result.cards[0].thesis, thesis);
  assert.equal(result.cards[0].title, candidates[0].title);
  assert.equal(result.cards[0].url, candidates[0].url);
  assert.doesNotMatch(result.summary, /unavailable|failed/);
});

for (const [name, fetchImpl, expectedReason] of [
  ["rejected evidence", async () => ({ ok: true, json: async () => ({ output_text: JSON.stringify({ status: "ready", cards: [] }) }) }), /evidence guardrails/],
  ["malformed model JSON", async () => ({ ok: true, json: async () => ({ output_text: "not json" }) }), /AI call failed/],
  ["API error", async () => ({ ok: false, status: 429, json: async () => ({ error: { message: "rate limited" } }) }), /rate limited/],
  ["network error", async () => { throw new Error("network unavailable"); }, /network unavailable/]
]) {
  test(`Deeper Read survives ${name} with cited deterministic output`, async (t) => {
    const originalClearTimeout = globalThis.clearTimeout;
    const clear = t.mock.method(globalThis, "clearTimeout", originalClearTimeout);
    const usage = [];
    const result = await buildAiDeeperRead({
      apiKey: "fixture-not-a-real-key", aiModel: "gpt-5-nano", candidates,
      sourceTape: candidates.map((candidate) => ({ ...candidate, id: candidate.sourceRef })),
      sourceRefIds: ["S1"], fetchImpl, recordUsage: async (row) => usage.push(row)
    });
    assert.equal(result.cards.length, 1);
    assert.equal(result.cards[0].sourceRef, "S1");
    assert.match(result.cards[0].thesis, /could slow investment/);
    assert.match(result.summary, expectedReason);
    assert.equal(clear.mock.callCount(), 1, "network rejection must also release the timeout");
    assert.ok(usage.length);
  });
}
