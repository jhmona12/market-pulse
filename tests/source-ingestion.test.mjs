import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

import {
  extractArticleCandidates,
  extractFeedItems,
  parseMarkdownSources,
  publishedDateFromHtml,
  sortArticlesNewestFirst,
  titleFromHtml
} from "../scripts/ingest/sources.mjs";
import {
  bottomModelSectorClusters,
  cleanArticleConclusion,
  cleanDailyRead,
  dailyReadPassesFactGuardrails,
  deeperReadCardPassesGuardrails,
  groundedCompanyNews,
  groundedMacroContext,
  hasSubstantiveDeeperReadEvidence
} from "../scripts/update-data.mjs";

const fixture = (name) => new URL(`./fixtures/${name}`, import.meta.url);

test("parses the Markdown source registry table", async () => {
  const markdown = await readFile(fixture("news-sources.md"), "utf8");
  const sources = parseMarkdownSources(markdown);
  assert.equal(sources.length, 2);
  assert.deepEqual(sources[0], {
    name: "Example Markets",
    url: "https://example.com/markets",
    category: "Professional commentary",
    cadence: "Daily",
    trust: "High",
    notes: "Macro and market strategy"
  });
});

test("extracts article candidates and ignores obvious navigation links", async () => {
  const html = await readFile(fixture("source-page.html"), "utf8");
  const candidates = extractArticleCandidates(html, { name: "Example Markets", url: "https://example.com/markets" });
  assert.ok(candidates.some((item) => item.url === "https://example.com/insights/2026/06/markets-look-through-noise"));
  assert.ok(!candidates.some((item) => item.url.includes("privacy") || item.url.includes("about")));
  assert.equal(titleFromHtml(html, "Fallback"), "Markets Look Through Noise");
  assert.equal(publishedDateFromHtml(html), "2026-06-28T13:30:00.000Z");
});

test("extracts feed items and sorts newest first", async () => {
  const feed = await readFile(fixture("feed.xml"), "utf8");
  const articles = extractFeedItems(feed, { name: "Example Markets", url: "https://example.com/feed.xml", notes: "fallback" });
  assert.equal(articles.length, 1);
  assert.equal(articles[0].url, "https://example.com/insights/rate-path");
  assert.equal(sortArticlesNewestFirst([{ title: "old", publishedAt: "2026-01-01T00:00:00Z" }, ...articles])[0].title, "Central banks reset the rate path");
});

test("removes source-page navigation debris from market conclusions", () => {
  const text = cleanArticleConclusion(
    "U.S. sanctions airlines as oil rises Skip Navigation Markets Business Investing Tech Politics",
    "U.S. sanctions airlines as oil rises"
  );
  assert.equal(text, "");
});

test("uses only supplied company headlines for recommendation news", () => {
  const result = groundedCompanyNews({
    news: [{
      id: "C1-N1",
      title: "Example Corp raises full-year guidance",
      url: "https://example.com/company-guidance",
      sourceName: "Example News",
      publishedAt: new Date().toISOString()
    }]
  });
  assert.equal(result.sourceId, "C1-N1");
  assert.match(result.text, /^Recent company-specific headline to investigate: Example Corp raises full-year guidance/);
  assert.match(result.text, /not proof that it caused the price move/);
});

test("trims and rejects vague AI Daily Read language before publication", () => {
  const dailyRead = cleanDailyRead({
    headline: "Mega-cap momentum holds at the core while market drivers frame the tape.",
    body: "Brent crude rose above $100 after supply disruption renewed inflation pressure. Higher energy costs can keep yields elevated, so long-duration positions need stricter confirmation. A third sentence should be removed. A fourth sentence should also be removed.",
    keyTakeaways: [
      "Market drivers: Brent crude rose after a supply disruption, so energy-sensitive inflation risk remains elevated.",
      "Breadth: Sectors show breadth erosion in weaker names (ABC, XYZ rebound-watch).",
      "Model read: Leaders offer cross-check for activation."
    ],
    watchItems: [
      "Positioning risk: Monitor RSI and volume signals in top-decile names.",
      "Next catalyst: Reassess Treasury yields after CPI on 2026-09-11 at 8:30 AM ET."
    ]
  });

  assert.equal(dailyRead.headline, "");
  assert.doesNotMatch(dailyRead.body, /third sentence|fourth sentence/i);
  assert.deepEqual(dailyRead.keyTakeaways, [
    "Market drivers: Brent crude rose after a supply disruption, so energy-sensitive inflation risk remains elevated."
  ]);
  assert.deepEqual(dailyRead.watchItems, [
    "Next catalyst: Reassess Treasury yields after CPI on 2026-09-11 at 8:30 AM ET."
  ]);
  assert.equal(dailyReadPassesFactGuardrails(dailyRead), true);
});

test("rejects model-led or unsupported AI Daily Read claims", () => {
  const dailyRead = cleanDailyRead({
    headline: "Momentum leads the top decile while oil pressure tests risk controls.",
    body: "Higher oil is lifting inflation risk while the macro calendar hints at HH uncertainty. Model leaders remain concentrated in technology.",
    keyTakeaways: [
      "Model read: Top-decile momentum remains concentrated in ABC and XYZ.",
      "Market drivers: Model leadership confirms a tactical long setup."
    ],
    watchItems: ["Risk: Exit if the 10Y yield extends beyond 4.9%."]
  });
  assert.equal(dailyReadPassesFactGuardrails(dailyRead), false);
});

test("grounds recommendation macro context in a supplied professional source", () => {
  const marketIntelligence = {
    professionalDrivers: [{
      title: "Diesel supply crunch adds to inflation pressures",
      summary: "Global refinery disruption is lifting diesel costs and adding to inflation pressure.",
      sourceName: "Example Research",
      publishedAt: "2026-09-08T12:00:00Z",
      url: "https://example.com/diesel",
      themes: ["Rates and central banks", "Commodities and energy"]
    }]
  };
  const result = groundedMacroContext(marketIntelligence, [{ id: "S1", url: "https://example.com/diesel" }]);
  assert.equal(result.sourceRef, "S1");
  assert.match(result.link, /^Portfolio backdrop, not a company catalyst:/);
  assert.match(result.link, /diesel costs/i);
  assert.match(result.evidence, /Example Research, 2026-09-08/);
});

test("does not turn generic trade language into an unsupported U.S.-China claim", () => {
  const marketIntelligence = {
    professionalDrivers: [{
      title: "Diesel supply crunch adds to inflation pressures",
      summary: "Global refinery disruption is lifting diesel costs and adding to inflation pressure.",
      excerpt: "Seaborne trade has fallen during the Iran conflict as refineries struggle to meet demand.",
      sourceName: "Example Research",
      publishedAt: "2026-09-08T12:00:00Z",
      url: "https://example.com/diesel",
      themes: ["Geopolitics and policy", "Rates and central banks", "Commodities and energy"]
    }]
  };
  const result = groundedMacroContext(marketIntelligence, [{ id: "S1", url: "https://example.com/diesel" }]);
  assert.match(result.link, /diesel costs/i);
  assert.doesNotMatch(result.link, /U\.S\.-China/i);
  assert.match(result.evidence, /Diesel supply crunch/i);
});

test("requires substantive article text before Deeper Read analysis", () => {
  assert.equal(hasSubstantiveDeeperReadEvidence({
    sourceName: "Axios Markets RSS",
    title: "AI frenzy means that debt can be interest free",
    summary: "AI frenzy means that debt can be interest free axios.com",
    excerpt: "AI frenzy means that debt can be interest free axios.com"
  }), false);
  assert.equal(hasSubstantiveDeeperReadEvidence({
    sourceName: "Example Research",
    title: "Diesel supply crunch adds to inflation pressure",
    summary: "Global refinery disruption is lifting diesel costs and adding to inflation pressure.",
    excerpt: "Refinery outages have reduced global diesel supply while transport demand remains firm. Wholesale diesel has risen faster than crude, raising freight and manufacturing costs. The report says the mismatch may persist while damaged refining capacity is repaired."
  }), true);
});

test("normalizes weak-sector clusters for sector size", () => {
  const opportunities = Array.from({ length: 100 }, (_, index) => ({
    symbol: `T${index}`,
    type: "stock",
    sector: index < 80 ? "Large Sector" : "Small Sector",
    modelRank: index + 1,
    modelUniverseCount: 100
  }));
  const clusters = bottomModelSectorClusters(opportunities, 2);
  assert.equal(clusters[0].sector, "Small Sector");
  assert.equal(clusters[0].count, 20);
  assert.equal(clusters[0].representationRatio, 4);
  assert.match(clusters[0].rationale, /versus its share of the full universe/);
});

test("rejects speculative or jargon-heavy Deeper Read cards", () => {
  const valid = {
    thesis: "Refinery outages have reduced diesel supply while transport demand remains firm.",
    whyItMatters: "Diesel is a direct input to freight and industrial costs.",
    marketReadThrough: "Monitor: compare diesel prices with crude, inflation expectations, and transport margins.",
    variantAngle: "Inference: persistent product-market tightness could matter even if broad crude prices ease."
  };
  assert.equal(deeperReadCardPassesGuardrails(valid), true);
  assert.equal(deeperReadCardPassesGuardrails({
    ...valid, thesis: "The source says refinery disruption could keep diesel supply tight."
  }), true);
  assert.equal(deeperReadCardPassesGuardrails({
    ...valid,
    thesis: "An export ribbon could cap real rates.",
    marketReadThrough: "Expect transport shares to outperform."
  }), false);
  assert.equal(deeperReadCardPassesGuardrails({
    ...valid,
    variantAngle: "Inference: diesel costs will stay elevated."
  }), false);
});
