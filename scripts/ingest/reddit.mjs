import { decodeHtml, normalizeDate, stripTags, xmlLink, xmlRaw, xmlText } from "./sources.mjs";

function normalizeRedditPostId(value) {
  return String(value || "")
    .toUpperCase()
    .replace(/[^A-Z0-9._-]/g, "")
    .slice(0, 80);
}

function redditListingPostsFromJson(payload) {
  return (payload?.data?.children || []).map((child) => child?.data).filter(Boolean);
}

function parseRedditRssListing(xml, subreddit, segment, sort = "hot") {
  const entries = [...String(xml || "").matchAll(/<entry\b[\s\S]*?<\/entry>/gi)].map((match) => match[0]);
  return entries
    .map((entry) => {
      const rawContent = xmlRaw(entry, ["content", "summary"]);
      const content = stripTags(decodeHtml(rawContent || "")).slice(0, 2000);
      const permalink = xmlLink(entry);
      const title = xmlText(entry, "title");
      return {
        id: normalizeRedditPostId(xmlText(entry, "id") || permalink || `${subreddit}-${sort}-${title}`),
        subreddit,
        segment,
        title,
        selftext: content,
        url: permalink,
        permalink,
        created_utc: null,
        createdAt: normalizeDate(xmlText(entry, ["published", "updated"])),
        score: 0,
        num_comments: 0,
        upvote_ratio: null,
        link_flair_text: null,
        fetchMethod: "rss"
      };
    })
    .filter((post) => post.title);
}

function redditSortsForMode(configuredSorts, oauthToken) {
  const sorts = [...new Set((configuredSorts || []).filter((sort) => ["hot", "new", "top"].includes(sort)))];
  return oauthToken ? (sorts.length ? sorts : ["hot", "new", "top"]) : ["hot"];
}

function redditSourcesForMode(configuredSources, oauthToken) {
  const sources = configuredSources || [];
  if (oauthToken) return sources;
  const wallStreetBets = sources.find((source) => String(source?.subreddit || "").toLowerCase() === "wallstreetbets");
  return wallStreetBets ? [wallStreetBets] : sources.slice(0, 1);
}

function recentRedditPosts(posts, now = new Date()) {
  return posts.filter((post) => {
    const published = post.createdAt || (post.created_utc ? new Date(post.created_utc * 1000).toISOString() : null);
    const ageMs = published ? now.getTime() - new Date(published).getTime() : NaN;
    return Number.isFinite(ageMs) && ageMs >= 0 && ageMs <= 24 * 60 * 60 * 1000;
  });
}

function redditMetricsFromPosts(posts) {
  const oauthPostCount = posts.filter((post) => post.fetchMethod === "oauth_json").length;
  const includesVoteAndCommentCounts = posts.length > 0 && oauthPostCount === posts.length;
  return {
    metricMode: includesVoteAndCommentCounts ? "ranked_attention" : "unranked_recent_mentions",
    includesVoteAndCommentCounts,
    oauthPostCount,
    rssPostCount: posts.length - oauthPostCount
  };
}

export { parseRedditRssListing, redditListingPostsFromJson, redditSortsForMode, redditSourcesForMode, recentRedditPosts, redditMetricsFromPosts };
