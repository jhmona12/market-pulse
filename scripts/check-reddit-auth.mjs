import { checkRedditAccess, RedditAccessError } from "./ingest/reddit-auth.mjs";

try {
  const result = await checkRedditAccess();
  console.log(`Reddit OAuth check passed: ${result.postsReturned} posts returned; ${result.postsWithEngagementMetrics} have vote/comment counts.`);
  console.log("Authenticated access verified. No post content, tokens, or credentials were logged or saved. Dashboard data was not refreshed.");
} catch (error) {
  // Only our fixed diagnostic messages are safe for public Actions logs.
  console.error(error instanceof RedditAccessError ? error.message : "Reddit OAuth check failed unexpectedly; no credentials or response bodies logged.");
  process.exitCode = 1;
}
