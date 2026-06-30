import test from "node:test";
import assert from "node:assert/strict";
import { parseTickerInput } from "../src/dashboard/ticker-input.js";
import { buildSourceRefMap, sourceRefLabels, sortedSourceArticles } from "../src/dashboard/source-refs.js";

test("parses pasted ticker lists consistently for browser and API use", () => {
  const tickers = parseTickerInput(" $googl, BRK.B\nBATS:MTUM; spy GOOGL bad:ticker:extra ", 5);
  assert.deepEqual(tickers, ["GOOGL", "BRK.B", "MTUM", "SPY"]);
});

test("sorts and labels source refs from research and official macro inputs", () => {
  const snapshot = {
    sources: [
      {
        name: "Source A Research",
        articles: [{ title: "Older", url: "https://a.example/older", publishedAt: "2026-05-01T10:00:00Z" }]
      },
      {
        name: "Source B | Markets",
        articles: [{ title: "Newer", url: "https://b.example/newer", publishedAt: "2026-05-02T10:00:00Z" }]
      }
    ],
    marketIntelligence: {
      officialMacro: {
        releases: [{ id: "O1", sourceName: "BLS", event: "Employment Situation", sourceUrl: "https://bls.example/jobs" }]
      }
    }
  };

  assert.deepEqual(sortedSourceArticles(snapshot.sources).map((article) => article.title), ["Newer", "Older"]);
  const refs = buildSourceRefMap(snapshot);
  assert.deepEqual(sourceRefLabels(["S1", "O1", "C1-EARNINGS"], "AAPL", refs), ["Source B", "BLS", "AAPL Earnings"]);
});
