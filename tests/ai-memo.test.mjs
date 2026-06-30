import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

import { buildAiMemoInputPayload, buildAiRecommendationResponseSchema } from "../scripts/snapshot/ai-memo.mjs";

const fixture = (name) => new URL(`./fixtures/${name}`, import.meta.url);

test("builds a bounded AI memo input payload from deterministic dashboard inputs", async () => {
  const context = JSON.parse(await readFile(fixture("ai-memo-context.json"), "utf8"));
  const payload = buildAiMemoInputPayload({
    ...context,
    generatedAt: "2026-06-29T15:00:00Z",
    compactCandidate: (item) => ({ symbol: item.symbol, score: item.score, type: item.type }),
    macroReleaseLookbackHours: 96,
    validRecommendationSymbols: ["ABC"]
  });

  assert.equal(payload.generatedAt, "2026-06-29T15:00:00Z");
  assert.equal(payload.marketIntelligence.officialMacro.releases[0].id, "M4");
  assert.deepEqual(payload.validRecommendationSymbols, ["ABC"]);
  assert.deepEqual(payload.sectorPerformance[0], {
    sector: "Technology",
    symbol: "XLK",
    change1d: 0.12,
    change5d: 1.2,
    change30d: 4.57,
    ytd: 12.34,
    relative30d: 2.22,
    above50: true,
    above200: true,
    rsi14: 61
  });
  assert.equal(payload.topMomentum.length, 2);
});

test("builds dynamic AI response schema enums for symbols and source refs", async () => {
  const schema = buildAiRecommendationResponseSchema({
    validSymbols: ["ABC"],
    validAvoidSymbols: ["XYZ"],
    sourceRefIds: ["S1", "M4", "C1-IR"]
  });
  assert.deepEqual(schema.properties.recommendations.items.properties.symbol.enum, ["ABC"]);
  assert.deepEqual(schema.properties.avoidList.properties.companies.items.properties.symbol.enum, ["XYZ"]);
  assert.deepEqual(schema.properties.sourceRefs.items.enum, ["S1", "M4", "C1-IR"]);
});
