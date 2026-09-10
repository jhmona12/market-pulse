import assert from "node:assert/strict";
import { test } from "node:test";

import { buildLongHorizonRows } from "../scripts/update-data.mjs";

test("reuses scorebook beta and trailing returns in long-horizon display rows", () => {
  const longHorizonRankings = {
    status: "ready",
    asOfDate: "2026-09-08",
    rankings: [{
      symbol: "ABC",
      name: "ABC Corp",
      sector: "Industrials",
      modelRank: 1,
      modelUniverseCount: 1,
      modelScore: 0.4,
      modelPercentile: 100,
      close: 100,
      beta60d: 9,
      return7: 91,
      return14: 92,
      return30: 93,
      return60: 94,
      return90: 95,
      ytdReturn: 96,
      asOfDate: "2026-09-08"
    }]
  };
  const tactical = {
    symbol: "ABC",
    modelRank: 4,
    modelPercentile: 97,
    setupType: "momentum_confirmed",
    setupTags: ["Momentum Confirmed"],
    beta60d: 1.2,
    return7: 1,
    return14: 2,
    return30: 3,
    return60: 4,
    return90: 5,
    ytdReturn: 6,
    asOfDate: "2026-09-08"
  };
  const rows = buildLongHorizonRows({
    longHorizonRankings,
    modelScorebook: { rows: [tactical] },
    stockMetadata: new Map([["ABC", { industry: "Machinery" }]]),
    marketCapCache: { bySymbol: new Map([["ABC", { text: "$12.0B", value: 12_000_000_000 }]]) }
  });

  assert.equal(rows[0].longModelRank, 1);
  assert.equal(rows[0].tacticalModelRank, 4);
  assert.deepEqual(
    [rows[0].beta60d, rows[0].return7, rows[0].return14, rows[0].return30, rows[0].return60, rows[0].return90, rows[0].ytdReturn],
    [1.2, 1, 2, 3, 4, 5, 6]
  );
});
