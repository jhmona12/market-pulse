import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

import {
  parseRedditRssListing,
  redditListingPostsFromJson,
  redditSourcesForMode,
  redditSortsForMode,
  recentRedditPosts,
  redditMetricsFromPosts
} from "../scripts/ingest/reddit.mjs";

const fixture = (name) => new URL(`./fixtures/${name}`, import.meta.url);

test("parses Reddit Atom feeds into normalized posts", async () => {
  const feed = await readFile(fixture("reddit-feed.xml"), "utf8");
  const posts = parseRedditRssListing(feed, "wallstreetbets", "Retail momentum", "hot");

  assert.equal(posts.length, 1);
  assert.equal(posts[0].id, "T3_EXAMPLE123");
  assert.equal(posts[0].title, "$NVDA demand looks strong after earnings");
  assert.equal(posts[0].createdAt, "2026-06-29T14:30:00.000Z");
  assert.match(posts[0].selftext, /Watching NVDA/);
  assert.equal(posts[0].fetchMethod, "rss");
});

test("normalizes Reddit JSON listings", () => {
  const posts = redditListingPostsFromJson({
    data: { children: [{ data: { id: "abc", title: "A post" } }, {}, { data: null }] }
  });
  assert.deepEqual(posts, [{ id: "abc", title: "A post" }]);
});

test("uses one low-volume RSS sort without OAuth", () => {
  assert.deepEqual(redditSortsForMode(["hot", "new", "top"], null), ["hot"]);
  assert.deepEqual(redditSortsForMode(["hot", "new", "top"], "token"), ["hot", "new", "top"]);
});

test("uses only WallStreetBets in unauthenticated fallback mode", () => {
  const sources = [{ subreddit: "stocks" }, { subreddit: "wallstreetbets" }, { subreddit: "investing" }];
  assert.deepEqual(redditSourcesForMode(sources, null), [{ subreddit: "wallstreetbets" }]);
  assert.deepEqual(redditSourcesForMode(sources, "token"), sources);
});

test("Reddit attention labels follow the received data rather than OAuth configuration", () => {
  const oauth = { fetchMethod: "oauth_json" };
  const rss = { fetchMethod: "rss" };
  assert.equal(redditMetricsFromPosts([oauth, oauth]).metricMode, "ranked_attention");
  for (const posts of [[rss], [oauth, rss], []]) {
    const result = redditMetricsFromPosts(posts);
    assert.equal(result.metricMode, "unranked_recent_mentions");
    assert.equal(result.includesVoteAndCommentCounts, false);
  }
});

test("hot feeds exclude posts older than 24 hours, future posts, and undated posts", () => {
  const posts = [
    { id: "recent", createdAt: "2026-09-10T01:00:00Z" },
    { id: "boundary", created_utc: Date.parse("2026-09-09T08:00:00Z") / 1000 },
    { id: "old", createdAt: "2026-09-08T12:00:00Z" },
    { id: "future", createdAt: "2026-09-11T01:00:00Z" },
    { id: "undated" }
  ];
  assert.deepEqual(recentRedditPosts(posts, new Date("2026-09-10T08:00:00Z")).map((post) => post.id), ["recent", "boundary"]);
});
