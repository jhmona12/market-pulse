import { cancelResponseBody } from "./http.mjs";
import { redditListingPostsFromJson } from "./reddit.mjs";

export class RedditAccessError extends Error {}

async function requestRedditJson(url, stage, options, { fetchImpl = fetch, timeoutMs = 12000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    // Never forward credentials through redirects or expose upstream response bodies.
    const response = await fetchImpl(url, { ...options, redirect: "error", signal: controller.signal });
    if (!response.ok) {
      await cancelResponseBody(response);
      const hint = response.status === 401 ? "Check the client ID and secret belong to the same app."
        : response.status === 403 ? "Access was denied; check Reddit API approval and app permissions."
          : response.status === 429 ? "Rate limited; wait before rerunning this check."
            : "Reddit did not accept the request.";
      throw new RedditAccessError(`${stage}: HTTP ${response.status}. ${hint}`);
    }
    try {
      return await response.json();
    } catch {
      throw new RedditAccessError(`${stage}: ${controller.signal.aborted ? "request timed out" : "response was not valid JSON"}.`);
    }
  } catch (error) {
    if (error instanceof RedditAccessError) throw error;
    throw new RedditAccessError(`${stage}: ${controller.signal.aborted ? "request timed out" : "network request failed"}.`);
  } finally {
    clearTimeout(timer);
  }
}

export async function fetchRedditAccessToken({ clientId, clientSecret, userAgent, ...requestOptions }) {
  clientId = String(clientId || "").trim();
  clientSecret = String(clientSecret || "").trim();
  if (!clientId || !clientSecret) {
    return { token: null, mode: "public", error: "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not configured" };
  }
  const payload = await requestRedditJson("https://www.reddit.com/api/v1/access_token", "OAuth token", {
    method: "POST",
    headers: {
      authorization: `Basic ${Buffer.from(`${clientId}:${clientSecret}`).toString("base64")}`,
      "content-type": "application/x-www-form-urlencoded",
      "user-agent": userAgent,
      accept: "application/json"
    },
    body: new URLSearchParams({ grant_type: "client_credentials" })
  }, requestOptions);
  if (payload?.error || typeof payload?.access_token !== "string" || !payload.access_token.trim()
      || String(payload.token_type || "").toLowerCase() !== "bearer") {
    throw new RedditAccessError("OAuth token: response did not include a valid bearer token. Check the app type and API access.");
  }
  return { token: payload.access_token, mode: "oauth" };
}

export async function checkRedditAccess(env = process.env, requestOptions = {}) {
  const names = ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"];
  const missing = names.filter((name) => !String(env[name] || "").trim());
  if (missing.length) throw new RedditAccessError(`Missing configuration: ${missing.join(", ")}.`);
  const userAgent = env.REDDIT_USER_AGENT.trim();
  const { token } = await fetchRedditAccessToken({
    clientId: env.REDDIT_CLIENT_ID, clientSecret: env.REDDIT_CLIENT_SECRET, userAgent, ...requestOptions
  });
  const payload = await requestRedditJson("https://oauth.reddit.com/r/wallstreetbets/hot?limit=5", "Authenticated listing", {
    headers: { authorization: `Bearer ${token}`, "user-agent": userAgent, accept: "application/json" }
  }, requestOptions);
  if (payload?.kind !== "Listing" || !Array.isArray(payload?.data?.children)
      || payload.data.children.some((child) => child?.kind !== "t3" || !child?.data || typeof child.data !== "object")) {
    throw new RedditAccessError("Authenticated listing: response was not a Reddit post listing.");
  }
  const posts = redditListingPostsFromJson(payload);
  return {
    status: "passed",
    authMode: "oauth",
    subreddit: "wallstreetbets",
    postsReturned: posts.length,
    postsWithEngagementMetrics: posts.filter((post) => Number.isFinite(post.score) && Number.isFinite(post.num_comments)).length
  };
}
