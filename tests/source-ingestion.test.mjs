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
