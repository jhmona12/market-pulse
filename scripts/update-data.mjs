import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const maxTickers = Number.parseInt(process.env.MAX_TICKERS || "0", 10);
const articlesPerSource = Number.parseInt(process.env.SOURCE_ARTICLES_PER_SOURCE || "3", 10);
const maxArticleCandidates = Number.parseInt(process.env.SOURCE_ARTICLE_CANDIDATES || "10", 10);
const companyContextCount = Number.parseInt(process.env.COMPANY_CONTEXT_COUNT || "12", 10);
const snapshotOutput = process.env.SNAPSHOT_OUTPUT || "data/snapshot.json";
const today = new Date();
const startDate = new Date(today);
startDate.setDate(startDate.getDate() - 430);
const defaultAiPrompt = `Write a concise hedge-fund-style morning strategy memo. Use only the supplied macro indicators, source summaries, current-event drivers, upcoming events, XGBoost model rankings, rules-based desk calls, momentum metrics, and lowest-ranked avoid candidates. Treat every output as a research recommendation, not financial advice.`;
const defaultOpenAiModel = "gpt-5-nano";
const modelPricingPerMillion = {
  "gpt-5": { input: 1.25, cachedInput: 0.125, output: 10 },
  "gpt-5-mini": { input: 0.25, cachedInput: 0.025, output: 2 },
  "gpt-5-nano": { input: 0.05, cachedInput: 0.005, output: 0.4 },
  "gpt-4.1": { input: 2, cachedInput: 0.5, output: 8 },
  "gpt-4.1-mini": { input: 0.4, cachedInput: 0.1, output: 1.6 },
  "gpt-4.1-nano": { input: 0.1, cachedInput: 0.025, output: 0.4 },
  "gpt-4o-mini": { input: 0.15, cachedInput: 0.075, output: 0.6 }
};

const macroSeries = [
  { id: "DGS10", label: "10Y Treasury", suffix: "%", deltaKind: "bps" },
  { id: "DGS2", label: "2Y Treasury", suffix: "%", deltaKind: "bps" },
  { id: "FEDFUNDS", label: "Fed Funds", suffix: "%", deltaKind: "rate" },
  { id: "UNRATE", label: "Unemployment", suffix: "%", deltaKind: "pp" },
  { id: "CPIAUCSL", label: "CPI Index", suffix: "", deltaKind: "pct" },
  { id: "GDP", label: "GDP", suffix: "B", deltaKind: "pct" }
];

const calendar = [
  { date: "2026-05-08", time: "8:30 AM ET", event: "Employment Situation", source: "BLS", importance: "High" },
  { date: "2026-05-13", time: "8:30 AM ET", event: "Consumer Price Index", source: "BLS", importance: "High" },
  { date: "2026-05-14", time: "8:30 AM ET", event: "Producer Price Index", source: "BLS", importance: "High" },
  { date: "2026-05-28", time: "8:30 AM ET", event: "GDP Second Estimate", source: "BEA", importance: "High" },
  { date: "2026-05-29", time: "8:30 AM ET", event: "Personal Income and Outlays", source: "BEA", importance: "High" },
  { date: "2026-06-05", time: "8:30 AM ET", event: "Employment Situation", source: "BLS", importance: "High" },
  { date: "2026-06-17", time: "2:00 PM ET", event: "FOMC Rate Decision", source: "Federal Reserve", importance: "High" },
  { date: "2026-07-29", time: "2:00 PM ET", event: "FOMC Rate Decision", source: "Federal Reserve", importance: "High" }
];

function ymd(date) {
  return date.toISOString().slice(0, 10).replaceAll("-", "");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchText(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || 14000);
  try {
    const response = await fetch(url, {
      signal: controller.signal,
      headers: {
        "user-agent": "MorningDesk/0.1 personal research dashboard; contact=local",
        accept: "text/html,application/xhtml+xml,application/xml,text/csv,text/plain;q=0.9,*/*;q=0.8"
      }
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return await response.text();
  } finally {
    clearTimeout(timeout);
  }
}

async function fetchJson(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || 14000);
  try {
    const response = await fetch(url, {
      signal: controller.signal,
      headers: {
        "user-agent": "Mozilla/5.0 MarketPulse/0.1 personal research dashboard",
        accept: "application/json,text/plain,*/*",
        origin: "https://www.nasdaq.com",
        referer: "https://www.nasdaq.com/"
      }
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

async function loadLocalEnv() {
  try {
    const text = await readFile(join(root, ".env"), "utf8");
    text.split(/\r?\n/).forEach((line) => {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) return;
      const [key, ...rest] = trimmed.split("=");
      if (!process.env[key]) process.env[key] = rest.join("=").trim().replace(/^["']|["']$/g, "");
    });
  } catch {
    // .env is optional for this local-first prototype.
  }
}

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function roundedNumber(value, digits = 2) {
  const number = finiteNumber(value);
  return number == null ? null : Number(number.toFixed(digits));
}

function parseLargeNumber(value) {
  if (value == null) return null;
  const number = Number(String(value).replace(/[$,\s]/g, ""));
  return Number.isFinite(number) ? number : null;
}

function formatMarketCap(value) {
  const number = parseLargeNumber(value);
  if (number == null) return null;
  if (number >= 1_000_000_000_000) return `$${(number / 1_000_000_000_000).toFixed(2)}T`;
  if (number >= 1_000_000_000) return `$${(number / 1_000_000_000).toFixed(1)}B`;
  if (number >= 1_000_000) return `$${(number / 1_000_000).toFixed(1)}M`;
  return `$${number.toLocaleString("en-US")}`;
}

function hasModelRank(item) {
  return Number.isFinite(Number(item?.modelRank));
}

async function loadModelRankings() {
  try {
    const text = await readFile(join(root, "data/model-rank-scores.json"), "utf8");
    const payload = JSON.parse(text);
    const rankings = (payload.rankings || []).filter((item) => item?.symbol);
    return {
      status: payload.status || "ready",
      generatedAt: payload.generatedAt || null,
      asOfDate: payload.asOfDate || null,
      model: payload.model || null,
      scoredCount: payload.scoredCount || rankings.length,
      requestedSymbolCount: payload.requestedSymbolCount || rankings.length,
      failedSymbolCount: payload.failedSymbolCount || 0,
      unscoredSymbolCount: payload.unscoredSymbolCount || 0,
      unscoredSymbols: payload.unscoredSymbols || [],
      rankings,
      bySymbol: new Map(rankings.map((item) => [item.symbol, item]))
    };
  } catch (error) {
    return {
      status: "missing",
      error: error.message,
      generatedAt: null,
      asOfDate: null,
      model: null,
      scoredCount: 0,
      requestedSymbolCount: 0,
      failedSymbolCount: 0,
      unscoredSymbolCount: 0,
      unscoredSymbols: [],
      rankings: [],
      bySymbol: new Map()
    };
  }
}

async function loadModelExplainability() {
  try {
    const text = await readFile(join(root, "models/rank/xgboost_rank_sector14_tuned_explainability.json"), "utf8");
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function publicModelSummary(modelRankings, explainability) {
  return {
    status: modelRankings.status,
    generatedAt: modelRankings.generatedAt,
    asOfDate: modelRankings.asOfDate,
    model: modelRankings.model,
    scoredCount: modelRankings.scoredCount,
    requestedSymbolCount: modelRankings.requestedSymbolCount,
    failedSymbolCount: modelRankings.failedSymbolCount,
    unscoredSymbolCount: modelRankings.unscoredSymbolCount,
    unscoredSymbols: modelRankings.unscoredSymbols.slice(0, 25),
    unavailableReason: modelRankings.status === "ready" ? null : modelRankings.error || null,
    topRankings: modelRankings.rankings.slice(0, 12),
    explainability: explainability
      ? {
          generatedAt: explainability.generatedAt,
          sourceModel: explainability.sourceModel,
          method: explainability.method,
          notes: explainability.notes,
          topFeatures: (explainability.topFeatures || []).slice(0, 10)
        }
      : null
  };
}

function parseMarkdownSources(markdown) {
  return markdown
    .split("\n")
    .filter((line) => line.trim().startsWith("|") && !line.includes("---"))
    .slice(1)
    .map((line) => line.split("|").slice(1, -1).map((cell) => cell.trim()))
    .filter((cells) => cells.length >= 6 && cells[1]?.startsWith("http"))
    .map(([name, url, category, cadence, trust, notes]) => ({ name, url, category, cadence, trust, notes }));
}

function decodeHtml(value) {
  let decoded = value;
  for (let index = 0; index < 3; index += 1) {
    const next = decoded
      .replaceAll("&amp;", "&")
      .replaceAll("&quot;", "\"")
      .replaceAll("&#39;", "'")
      .replaceAll("&nbsp;", " ")
      .replaceAll("&lt;", "<")
      .replaceAll("&gt;", ">");
    if (next === decoded) break;
    decoded = next;
  }
  return decoded;
}

function stripTags(value) {
  return decodeHtml(value.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim());
}

function attrValue(tag, name) {
  return tag.match(new RegExp(`${name}\\s*=\\s*["']([^"']+)["']`, "i"))?.[1] || "";
}

function metaContent(html, names) {
  const lowered = names.map((name) => name.toLowerCase());
  for (const match of html.matchAll(/<meta\b[^>]*>/gi)) {
    const tag = match[0];
    const property = attrValue(tag, "property").toLowerCase();
    const name = attrValue(tag, "name").toLowerCase();
    const itemprop = attrValue(tag, "itemprop").toLowerCase();
    if (lowered.includes(property) || lowered.includes(name) || lowered.includes(itemprop)) {
      return attrValue(tag, "content");
    }
  }
  return "";
}

function titleFromHtml(html, fallback) {
  return stripTags(
    metaContent(html, ["og:title", "twitter:title", "headline"]) ||
      html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i)?.[1] ||
      html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] ||
      fallback
  ).slice(0, 160);
}

function summaryFromHtml(html, fallback) {
  return stripTags(
    metaContent(html, ["description", "og:description", "twitter:description"]) ||
      html.match(/<p[^>]*>([\s\S]*?)<\/p>/i)?.[1] ||
      fallback
  ).slice(0, 320);
}

function extractJsonLdDates(html) {
  const dates = [];
  for (const match of html.matchAll(/<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)) {
    try {
      const parsed = JSON.parse(stripTags(match[1]));
      const nodes = Array.isArray(parsed) ? parsed : [parsed, ...(parsed["@graph"] || [])];
      nodes.forEach((node) => {
        const value = node?.datePublished || node?.dateModified || node?.uploadDate;
        if (value) dates.push(value);
      });
    } catch {
      // Some publishers include invalid JSON-LD; date meta tags still cover those pages.
    }
  }
  return dates;
}

function normalizeDate(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  if (date.getFullYear() < 2000 || date.getFullYear() > today.getFullYear() + 1) return null;
  return date.toISOString();
}

function publishedDateFromHtml(html, fallbackText = "") {
  const candidates = [
    metaContent(html, [
      "article:published_time",
      "datePublished",
      "date",
      "publishdate",
      "pubdate",
      "sailthru.date",
      "parsely-pub-date",
      "dc.date",
      "dc.date.issued"
    ]),
    html.match(/<time[^>]+datetime=["']([^"']+)["'][^>]*>/i)?.[1],
    ...extractJsonLdDates(html)
  ];

  const textDate = fallbackText.match(/\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},\s+20\d{2}\b/i)?.[0];
  if (textDate) candidates.push(textDate);

  for (const candidate of candidates) {
    const normalized = normalizeDate(candidate);
    if (normalized) return normalized;
  }
  return null;
}

function visibleTextFromHtml(html) {
  const withoutNoise = html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<nav[\s\S]*?<\/nav>/gi, " ")
    .replace(/<footer[\s\S]*?<\/footer>/gi, " ")
    .replace(/<header[\s\S]*?<\/header>/gi, " ");
  return stripTags(withoutNoise).replace(/\s+/g, " ").trim();
}

function sameHostOrSubdomain(sourceHost, candidateHost) {
  return candidateHost === sourceHost || candidateHost.endsWith(`.${sourceHost}`) || sourceHost.endsWith(`.${candidateHost}`);
}

function articleScore(url, text, source) {
  const haystack = `${url.pathname} ${url.search} ${text}`.toLowerCase();
  let score = 0;
  if (/(commentary|insight|research|market|outlook|weekly|capital-market|strategy|economic|macro|article|blog)/i.test(haystack)) score += 8;
  if (/(login|sign-in|privacy|terms|careers|contact|subscribe|podcast|webinar|event|video|pdf|mailto|javascript|archive|award|recognition|about|account|solution|product|fund|529|college|advisor|client|why-|alternative-investments|benefits-of|financial-plan|personal-finance|retirement|estate-planning)/i.test(haystack)) score -= 10;
  if (/20\d{2}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]20\d{2}/.test(haystack)) score += 5;
  if (haystack.includes(source.name.toLowerCase().split(" ")[0])) score += 1;
  if (url.pathname.split("/").filter(Boolean).length >= 2) score += 2;
  if (url.pathname === "/" || url.hash) score -= 3;
  return score;
}

function extractArticleCandidates(html, source) {
  const sourceUrl = new URL(source.url);
  const seen = new Set();
  const candidates = [];

  for (const match of html.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/gi)) {
    const href = attrValue(match[1], "href");
    if (!href || href.startsWith("#") || href.startsWith("mailto:") || href.startsWith("tel:") || href.startsWith("javascript:")) continue;

    let url;
    try {
      url = new URL(decodeHtml(href), sourceUrl);
    } catch {
      continue;
    }

    url.hash = "";
    if (!["http:", "https:"].includes(url.protocol)) continue;
    if (!sameHostOrSubdomain(sourceUrl.hostname.replace(/^www\./, ""), url.hostname.replace(/^www\./, ""))) continue;
    if (seen.has(url.href)) continue;

    const text = stripTags(match[2]).slice(0, 180);
    const score = articleScore(url, text, source);
    if (score < 4) continue;

    seen.add(url.href);
    candidates.push({ url: url.href, linkText: text, score });
  }

  return candidates
    .sort((a, b) => b.score - a.score)
    .slice(0, Math.max(articlesPerSource, maxArticleCandidates));
}

async function fetchArticle(source, candidate) {
  const html = await fetchText(candidate.url, { timeout: 14000 });
  const text = visibleTextFromHtml(html);
  const title = titleFromHtml(html, candidate.linkText || source.name);
  const summary = summaryFromHtml(html, source.notes);
  const publishedAt = publishedDateFromHtml(html, `${candidate.linkText} ${text.slice(0, 800)}`);

  return {
    sourceName: source.name,
    title,
    url: candidate.url,
    publishedAt,
    summary,
    excerpt: text.slice(0, 1800),
    discoveredFrom: source.url
  };
}

function sortArticlesNewestFirst(articles) {
  return articles.sort((a, b) => {
    const aTime = a.publishedAt ? new Date(a.publishedAt).getTime() : 0;
    const bTime = b.publishedAt ? new Date(b.publishedAt).getTime() : 0;
    if (aTime !== bTime) return bTime - aTime;
    return a.title.localeCompare(b.title);
  });
}

async function checkSource(source) {
  try {
    const html = await fetchText(source.url, { timeout: 12000 });
    const title = titleFromHtml(html, source.name);
    const summary = summaryFromHtml(html, source.notes);
    const landingArticle = {
      sourceName: source.name,
      title,
      url: source.url,
      publishedAt: publishedDateFromHtml(html),
      summary,
      excerpt: visibleTextFromHtml(html).slice(0, 1800),
      discoveredFrom: source.url
    };
    const candidates = extractArticleCandidates(html, source);
    const candidateArticles = await mapLimit(candidates, 3, async (candidate) => {
      try {
        return await fetchArticle(source, candidate);
      } catch {
        return null;
      }
    });
    const articlesByUrl = new Map();
    [landingArticle, ...candidateArticles.filter(Boolean)].forEach((article) => {
      if (!article.url || articlesByUrl.has(article.url)) return;
      articlesByUrl.set(article.url, article);
    });
    const sortedArticles = sortArticlesNewestFirst([...articlesByUrl.values()]);
    const datedArticles = sortedArticles.filter((article) => article.publishedAt);
    const articles = (
      datedArticles.length
        ? [...datedArticles, ...sortedArticles.filter((article) => !article.publishedAt && article.url === source.url)]
        : sortedArticles
    ).slice(0, articlesPerSource);
    return {
      ...source,
      title,
      summary,
      articles,
      articleCount: articles.length,
      ok: true
    };
  } catch (error) {
    return {
      ...source,
      title: source.name,
      summary: `Source check failed: ${error.message}`,
      articles: [],
      articleCount: 0,
      ok: false
    };
  }
}

async function fetchSp500Constituents() {
  const url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies";
  const html = await fetchText(url, { timeout: 16000 });
  const table = html.match(/<table[^>]+id=["']constituents["'][\s\S]*?<\/table>/i)?.[0];
  if (!table) throw new Error("Could not find constituents table");

  const rows = [...table.matchAll(/<tr[\s\S]*?<\/tr>/gi)].slice(1);
  return rows
    .map((row) => {
      const cells = [...row[0].matchAll(/<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/gi)].map((cell) => stripTags(cell[1]));
      return {
        symbol: cells[0],
        name: cells[1],
        sector: cells[2],
        type: "stock"
      };
    })
    .filter((item) => item.symbol && item.name);
}

function yahooSymbol(symbol) {
  return symbol.toUpperCase().replaceAll(".", "-");
}

function parseCsv(csv) {
  const lines = csv.trim().split(/\r?\n/);
  const headers = lines.shift()?.split(",") || [];
  return lines
    .map((line) => {
      const values = line.split(",");
      return Object.fromEntries(headers.map((header, index) => [header, values[index]]));
    })
    .filter((row) => row.Date && row.Close && row.Close !== "N/D")
    .map((row) => ({
      date: row.Date,
      open: Number(row.Open),
      high: Number(row.High),
      low: Number(row.Low),
      close: Number(row.Close),
      volume: Number(row.Volume)
    }))
    .filter((row) => Number.isFinite(row.close));
}

async function fetchHistory(symbol) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(yahooSymbol(symbol))}?range=18mo&interval=1d&events=history`;
  const text = await fetchText(url, { timeout: 12000 });
  const payload = JSON.parse(text);
  const result = payload.chart?.result?.[0];
  const timestamps = result?.timestamp || [];
  const quote = result?.indicators?.quote?.[0] || {};
  const adjusted = result?.indicators?.adjclose?.[0]?.adjclose || quote.close || [];
  const rows = timestamps
    .map((timestamp, index) => ({
      date: new Date(timestamp * 1000).toISOString().slice(0, 10),
      open: quote.open?.[index],
      high: quote.high?.[index],
      low: quote.low?.[index],
      close: adjusted[index] ?? quote.close?.[index],
      volume: quote.volume?.[index]
    }))
    .filter((row) => Number.isFinite(row.close));
  if (rows.length < 205) throw new Error(`Not enough history for ${symbol}`);
  return rows;
}

function sma(values, period, end = values.length) {
  if (end < period) return null;
  const slice = values.slice(end - period, end);
  return slice.reduce((sum, value) => sum + value, 0) / period;
}

function percentChange(now, before) {
  if (!before) return 0;
  return ((now - before) / before) * 100;
}

function formatPercent(value) {
  const number = Number(value || 0);
  return `${number >= 0 ? "+" : ""}${number.toFixed(2)}%`;
}

function formatNumber(value, decimals = 1) {
  return Number(value || 0).toFixed(decimals);
}

function rsi(values, period = 14) {
  if (values.length < period + 1) return 50;
  const slice = values.slice(-period - 1);
  let gains = 0;
  let losses = 0;
  for (let index = 1; index < slice.length; index += 1) {
    const delta = slice[index] - slice[index - 1];
    if (delta >= 0) gains += delta;
    else losses += Math.abs(delta);
  }
  if (losses === 0) return 100;
  const rs = gains / losses;
  return 100 - 100 / (1 + rs);
}

function crossedAbove(values, shortPeriod, longPeriod, lookback = 12) {
  for (let offset = 0; offset < lookback; offset += 1) {
    const end = values.length - offset;
    const prevEnd = end - 1;
    const shortNow = sma(values, shortPeriod, end);
    const longNow = sma(values, longPeriod, end);
    const shortPrev = sma(values, shortPeriod, prevEnd);
    const longPrev = sma(values, longPeriod, prevEnd);
    if ([shortNow, longNow, shortPrev, longPrev].some((value) => value == null)) continue;
    if (shortPrev <= longPrev && shortNow > longNow) return true;
  }
  return false;
}

function computeSignal(item, history, spyReturn20 = 0) {
  const closes = history.map((row) => row.close);
  const volumes = history.map((row) => row.volume || 0);
  const close = closes.at(-1);
  const prevClose = closes.at(-2);
  const ma20 = sma(closes, 20);
  const ma50 = sma(closes, 50);
  const ma100 = sma(closes, 100);
  const ma200 = sma(closes, 200);
  const rsi14 = rsi(closes);
  const return20 = percentChange(close, closes.at(-21));
  const return60 = percentChange(close, closes.at(-61));
  const relativeStrength = return20 - spyReturn20;
  const avgVolume20 = sma(volumes, 20) || 1;
  const volumeRatio = (volumes.at(-1) || avgVolume20) / avgVolume20;
  const above50 = close > ma50;
  const above100 = close > ma100;
  const above200 = close > ma200;
  const recentCross = crossedAbove(closes, 20, 50) || crossedAbove(closes, 50, 200, 20);
  const nearHigh = close / Math.max(...closes.slice(-252)) > 0.92;

  let score = 0;
  if (close > ma20) score += 12;
  if (above50) score += 14;
  if (above100) score += 10;
  if (above200) score += 16;
  if (recentCross) score += 12;
  if (return20 > 0) score += Math.min(12, return20);
  if (return60 > 0) score += Math.min(10, return60 / 2);
  if (relativeStrength > 0) score += Math.min(10, relativeStrength);
  if (volumeRatio > 1.1) score += Math.min(8, (volumeRatio - 1) * 10);
  if (rsi14 >= 50 && rsi14 <= 76) score += 8;
  if (nearHigh) score += 8;

  const tags = [];
  if (recentCross) tags.push("Fresh cross");
  if (above50 && above200) tags.push("Major trend");
  if (relativeStrength > 2) tags.push("Beating SPY");
  if (volumeRatio > 1.1) tags.push("Volume support");
  if (nearHigh) tags.push("Near highs");
  if (rsi14 > 76) tags.push("Extended RSI");

  return {
    symbol: item.symbol,
    name: item.name,
    type: item.type || "stock",
    sector: item.sector || item.assetClass || "Unclassified",
    score: Math.max(0, Math.min(100, (score / 112) * 100)),
    close,
    changePct: percentChange(close, prevClose),
    rsi14,
    volumeRatio,
    return20,
    return60,
    above50,
    above100,
    above200,
    recentCross,
    relativeStrength,
    history: closes.slice(-36),
    tags
  };
}

function mergeModelScores(signals, modelRankings) {
  if (modelRankings.status !== "ready" || !modelRankings.bySymbol.size) {
    return signals.map((item) => ({ ...item, rulesScore: item.score })).sort((a, b) => b.score - a.score);
  }

  return signals
    .map((item) => {
      const model = modelRankings.bySymbol.get(item.symbol);
      if (!model) return { ...item, rulesScore: item.score };

      const modelPercentile = finiteNumber(model.modelPercentile);
      const modelTags = [model.modelBucket, ...(model.modelReasons || []).slice(0, 2)].filter(Boolean);
      return {
        ...item,
        rulesScore: item.score,
        score: modelPercentile ?? item.score,
        modelRank: finiteNumber(model.modelRank),
        modelUniverseCount: finiteNumber(model.modelUniverseCount),
        modelScore: finiteNumber(model.modelScore),
        modelPercentile,
        modelBucket: model.modelBucket || "Ranked",
        modelReasons: model.modelReasons || [],
        riskFlags: model.riskFlags || [],
        modelAsOfDate: model.asOfDate || modelRankings.asOfDate,
        return120: finiteNumber(model.return120),
        relativeReturn60VsSpy: finiteNumber(model.relativeReturn60VsSpy),
        sectorReturn60: finiteNumber(model.sectorReturn60),
        volatility60d: finiteNumber(model.volatility60d),
        volatility60dVsSector: finiteNumber(model.volatility60dVsSector),
        distanceTo52wHigh: finiteNumber(model.distanceTo52wHigh),
        tags: [...new Set([...(item.tags || []), ...modelTags])]
      };
    })
    .sort((a, b) => {
      const aModel = hasModelRank(a);
      const bModel = hasModelRank(b);
      if (aModel && bModel) return Number(a.modelRank) - Number(b.modelRank);
      if (aModel !== bModel) return aModel ? -1 : 1;
      return b.score - a.score;
    });
}

function computeSectorPerformance(item, history, spyHistory) {
  const closes = history.map((row) => row.close);
  const spyCloses = spyHistory.map((row) => row.close);
  const close = closes.at(-1);
  const date = history.at(-1)?.date;
  const ytdIndex = history.findIndex((row) => row.date >= `${date.slice(0, 4)}-01-01`);
  const ytdBase = ytdIndex >= 0 ? closes[ytdIndex] : closes[0];
  const return30 = percentChange(close, closes.at(-31));
  const spyReturn30 = percentChange(spyCloses.at(-1), spyCloses.at(-31));
  const ma50 = sma(closes, 50);
  const ma200 = sma(closes, 200);

  return {
    sector: item.sector,
    symbol: item.symbol,
    name: item.name,
    date,
    close,
    change1d: percentChange(close, closes.at(-2)),
    change5d: percentChange(close, closes.at(-6)),
    change30d: return30,
    ytd: percentChange(close, ytdBase),
    relative30d: return30 - spyReturn30,
    above50: close > ma50,
    above200: close > ma200,
    rsi14: rsi(closes),
    history: closes.slice(-36)
  };
}

async function mapLimit(items, limit, worker) {
  const results = [];
  let index = 0;
  async function next() {
    while (index < items.length) {
      const current = index;
      index += 1;
      results[current] = await worker(items[current], current);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, next));
  return results;
}

async function fetchFredSeries(series) {
  try {
    const csv = await fetchText(`https://fred.stlouisfed.org/graph/fredgraph.csv?id=${series.id}`, { timeout: 12000 });
    const [, ...lines] = csv.trim().split(/\r?\n/);
    const valid = lines
      .map((line) => {
        const [date, value] = line.split(",");
        return { date, value: Number(value) };
      })
      .filter((row) => Number.isFinite(row.value));
    const last = valid.at(-1)?.value;
    const prev = valid.at(-2)?.value;
    if (!Number.isFinite(last)) throw new Error("No values");
    const delta = last - prev;
    let deltaText = "Flat";
    if (series.deltaKind === "bps") deltaText = `${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(0)} bps`;
    else if (series.deltaKind === "pp") deltaText = `${delta >= 0 ? "+" : ""}${delta.toFixed(1)} pp`;
    else if (series.deltaKind === "pct") deltaText = formatPercent(percentChange(last, prev));
    else deltaText = `${delta >= 0 ? "+" : ""}${delta.toFixed(2)}`;
    return {
      label: series.label,
      value: `${last.toFixed(series.suffix === "B" ? 0 : 2)}${series.suffix === "B" ? "B" : series.suffix}`,
      delta: deltaText
    };
  } catch (error) {
    return { label: series.label, value: "n/a", delta: error.message };
  }
}

function firstSentence(text, maxLength = 240) {
  if (!text) return "";
  const clean = String(text).replace(/\s+/g, " ").trim();
  const sentence = clean.match(/^.*?[.!?](?:\s|$)/)?.[0]?.trim() || clean;
  return sentence.length > maxLength ? `${sentence.slice(0, maxLength - 3).trim()}...` : sentence;
}

function articleBriefs(sources, limit = 3) {
  return sortArticlesNewestFirst(
    sources
      .flatMap((source) => (source.articles || []).map((article) => ({ ...article, sourceName: article.sourceName || source.name })))
      .filter((article) => article.title && article.url)
      .filter((article) => !/(pardon our interruption|privacy|terms|sign in|login|subscribe)/i.test(`${article.title} ${article.summary || ""}`))
  )
    .slice(0, limit)
    .map((article) => `${article.sourceName}: ${article.title}`);
}

function topSectorText(sectorPerformance) {
  const leaders = (sectorPerformance || []).slice(0, 3);
  if (!leaders.length) return "sector confirmation is unavailable";
  return leaders
    .map((item) => `${item.sector} ${formatPercent(item.change30d)} over 30D${Number(item.relative30d) >= 0 ? `, ${formatPercent(item.relative30d)} vs SPY` : ""}`)
    .join("; ");
}

function dominantModelSectorText(modelLeaders) {
  const counts = new Map();
  modelLeaders.slice(0, 25).forEach((item) => counts.set(item.sector, (counts.get(item.sector) || 0) + 1));
  const [sector, count] = [...counts.entries()].sort((a, b) => b[1] - a[1])[0] || [];
  return sector ? `${count} of the top 25 model-ranked stocks are in ${sector}` : "model leadership is not concentrated enough to call a dominant sector";
}

function boundedList(items, fallback, limit = 6) {
  const clean = (items || []).map((item) => String(item || "").trim()).filter(Boolean);
  return clean.length ? clean.slice(0, limit) : fallback.slice(0, limit);
}

function cleanDailyReadText(value) {
  return String(value || "")
    .replace(/[‑–—]/g, "-")
    .replaceAll("XGBoost_ranked", "XGBoost-ranked")
    .replaceAll("modelRank", "model rank")
    .replaceAll("14d", "14-day")
    .replace(/AI-dense/gi, "AI-focused")
    .replace(/duration\/breathing risk/gi, "duration-sensitive risk assets")
    .replace(/\b([A-Z]{2,5})\/\1-linked\b/g, "$1-linked")
    .replace(/high breadth in XLK/gi, "strength in XLK")
    .replace(/model relevance degrades/gi, "top-ranked names lose price or volume confirmation")
    .replace(/sharp macro turn lower in growth \/ inflation data/gi, "weaker growth data, hotter inflation, or higher yields")
    .replace(/Earnings context solidify through/gi, "Earnings context is supported by")
    .replace(/a weaker growth data/gi, "weaker growth data")
    .replace(/hotter inflation, or higher yields or/gi, "hotter inflation, higher yields, or")
    .replace(/volume deteriorations/gi, "volume deterioration")
    .replace(/Volume weakness/g, "volume weakness")
    .replace(/\bSatS\b/g, "SATS")
    .replace(/\(\s+/g, "(")
    .replace(/\s*\/\s*/g, " / ")
    .replace(/RSI \/ volatility/gi, "RSI and volatility")
    .replace(/volume \/ trend/gi, "volume or trend")
    .replace(/weak-mauge/gi, "weak")
    .replace(/drawdowns risk/gi, "drawdown risk")
    .replace(/\b([A-Z]{2,5}) \/ ([A-Z]{2,5})\b/g, "$1 and $2")
    .replace(/\s+/g, " ")
    .trim();
}

function cleanMemoText(value) {
  return cleanDailyReadText(value)
    .replace(/\bnot provided\b/gi, "")
    .replace(/\branked low\b/gi, "ranked near the bottom of the model book")
    .replace(/\s+/g, " ")
    .trim();
}

function usableDailyReadItem(value) {
  return !/(no names lack|no data gaps|all data complete|no .* unavailable)/i.test(value);
}

function cleanDailyRead(dailyRead) {
  if (!dailyRead) return null;
  return {
    headline: firstSentence(cleanDailyReadText(dailyRead.headline), 150),
    body: cleanDailyReadText(dailyRead.body),
    keyTakeaways: (dailyRead.keyTakeaways || []).map(cleanDailyReadText).filter(Boolean).filter(usableDailyReadItem),
    watchItems: (dailyRead.watchItems || []).map(cleanDailyReadText).filter(Boolean).filter(usableDailyReadItem)
  };
}

function buildNote({ opportunities, macro, sources, model, sectorPerformance, rulesRecommendations, aiRecommendations }) {
  const modelReady = model?.status === "ready" && model.scoredCount > 0;
  const modelLeaders = opportunities.filter(hasModelRank);
  const leaders = (modelReady ? modelLeaders : opportunities).slice(0, 5);
  const etfLeaders = opportunities.filter((item) => item.type === "etf").slice(0, 3);
  const broadTrend = opportunities.filter((item) => item.above50 && item.above200).length;
  const above50 = opportunities.filter((item) => item.above50).length;
  const above200 = opportunities.filter((item) => item.above200).length;
  const topDecileLimit = modelReady ? Math.max(1, Math.ceil(model.scoredCount * 0.1)) : null;
  const extended = modelReady
    ? modelLeaders.filter((item) => item.modelRank <= topDecileLimit && item.rsi14 > 76).length
    : opportunities.filter((item) => item.score >= 70 && item.rsi14 > 76).length;
  const cleanCandidates = modelReady
    ? modelLeaders.filter((item) => item.modelRank <= topDecileLimit && item.above50 && item.above200 && item.rsi14 <= 76).length
    : opportunities.filter((item) => item.score >= 72 && item.above50 && item.above200 && item.rsi14 <= 76).length;
  const universeCount = opportunities.length || 1;
  const breadth = Math.round((broadTrend / universeCount) * 100);
  const above50Pct = Math.round((above50 / universeCount) * 100);
  const above200Pct = Math.round((above200 / universeCount) * 100);
  const sourceHits = sources.filter((source) => source.ok).length;
  const failedSources = sources.length - sourceHits;
  const articleHits = sources.reduce((total, source) => total + (source.articles?.length || 0), 0);
  const upcomingEvents = calendar.filter((event) => new Date(`${event.date}T23:59:59`) >= new Date()).slice(0, 3);
  const leaderText = leaders.map((item) => item.symbol).join(", ") || "none";
  const etfText = etfLeaders.map((item) => item.symbol).join(", ") || "none";
  const topSector = sectorPerformance?.[0];
  const modelSectorText = dominantModelSectorText(modelLeaders);
  const sourceBriefs = articleBriefs(sources, 3);
  const aiFocus = aiRecommendations?.status === "ready"
    ? firstSentence(aiRecommendations.headline || aiRecommendations.macroView)
    : "";
  const aiSymbols = (aiRecommendations?.recommendations || []).slice(0, 4).map((item) => item.symbol).filter(Boolean).join(", ");
  const modelText = modelReady
    ? `${model.scoredCount} S&P 500 names were scored by the XGBoost rank model as of ${model.asOfDate || "the latest available close"}`
    : "The XGBoost rank model was not available for this refresh";
  const regime = breadth >= 55
    ? "Risk appetite is broad"
    : breadth >= 40
      ? "Risk appetite is constructive but selective"
      : "Risk appetite is narrow";
  const fallbackHeadline = modelReady
    ? `${regime}; model leadership is clustered in ${leaderText}, with ${topSector?.sector || "sector"} confirmation.`
    : `${regime}; rules-based momentum leaders are ${leaderText}.`;
  const fallbackBody = `${regime}: ${above50Pct}% of the screened universe is above the 50-day average, ${above200Pct}% is above the 200-day, and ${breadth}% clears both trend lines. ${modelText}; the cleanest top-decile setup count is ${cleanCandidates}, while ${extended} top-ranked names are already RSI-extended. Sector confirmation is led by ${topSectorText(sectorPerformance)}. The AI memo is focused on ${aiSymbols || leaderText}${aiFocus ? `, with the note that ${aiFocus.charAt(0).toLowerCase()}${aiFocus.slice(1)}` : ""}. Source tape coverage is ${sourceHits}/${sources.length} pages live with ${articleHits} recent articles, so treat source conclusions as useful but incomplete when a publisher blocks scraping.`;
  const fallbackChanged = [
    modelReady
      ? `Model book: ${leaderText} lead ${model.scoredCount} scored S&P 500 names; ${modelSectorText}.`
      : "Model rankings were unavailable, so the read used the rules-based momentum score.",
    `Market breadth: ${above50Pct}% above the 50-day, ${above200Pct}% above the 200-day, and ${breadth}% above both, which keeps the setup ${breadth >= 40 ? "tradable" : "fragile"}.`,
    `Sector tape: ${topSectorText(sectorPerformance)}.`,
    rulesRecommendations?.length
      ? `Desk call summary: ${rulesRecommendations.slice(0, 4).map((item) => `${item.symbol} (${item.label})`).join(", ")}.`
      : "Desk call summary was unavailable in this snapshot.",
    aiFocus ? `AI memo: ${aiFocus}` : "AI memo was unavailable, so this read is based on deterministic model, macro, sector, and source data.",
    sourceBriefs.length ? `Research tape: ${sourceBriefs.join(" | ")}.` : "Research tape: no high-quality article briefs were extracted from the configured source pages."
  ];
  const fallbackWatch = [
    upcomingEvents.length ? `Macro risk: ${upcomingEvents.map((event) => `${event.event} on ${event.date}`).join("; ")}.` : "No upcoming macro events are currently listed in the local calendar.",
    modelReady
      ? `Momentum risk: ${extended} top-decile model names have RSI above 76; chase risk is highest where model rank is strong but volume/trend confirmation is weak.`
      : `Momentum risk: ${extended} screened names have score >= 70 and RSI above 76.`,
    `Confirmation check: ETF leaders are ${etfText}; if they roll over while single-name ranks stay high, reduce confidence in the long book.`,
    `${failedSources} of ${sources.length} configured source pages failed the latest check; blocked or stale sources should not drive the call.`
  ];
  const aiDailyRead = aiRecommendations?.status === "ready" ? cleanDailyRead(aiRecommendations.dailyRead) : null;

  return {
    headline: aiDailyRead?.headline || fallbackHeadline,
    body: aiDailyRead?.body || fallbackBody,
    changed: boundedList([...(aiDailyRead?.keyTakeaways || []).slice(0, 4), ...fallbackChanged], fallbackChanged, 6),
    watch: boundedList([...(aiDailyRead?.watchItems || []).slice(0, 3), ...fallbackWatch], fallbackWatch, 5),
    generatedBy: aiDailyRead ? "ai_with_fact_guardrails" : "deterministic"
  };
}

function buildRecommendations(opportunities) {
  const modelCandidates = opportunities.filter(hasModelRank);
  if (modelCandidates.length) {
    const cleanModelCandidates = modelCandidates.filter((item) => item.above50 && item.above200 && item.rsi14 <= 76);
    const watch = modelCandidates.find((item) => item.modelRank <= Math.ceil(modelCandidates.length * 0.2) && (!item.above50 || !item.above200 || item.rsi14 > 76));
    const calls = cleanModelCandidates.slice(0, 4).map((item) => ({
      label: item.modelBucket || "Model Ranked",
      symbol: item.symbol,
      title: `Rank #${item.modelRank} of ${item.modelUniverseCount}; ${formatNumber(item.modelPercentile)} percentile`,
      rationale: `${item.symbol}: model score ${formatNumber(item.modelScore, 3)}; rules score ${formatNumber(item.rulesScore)}; ${item.above50 && item.above200 ? "above 50-day and 200-day averages" : "mixed trend alignment"}; 60-day relative return vs SPY ${formatPercent(item.relativeReturn60VsSpy)}; evidence: ${(item.modelReasons || []).join("; ") || "model rank and technical inputs"}.`
    }));

    if (watch) {
      calls.push({
        label: "Model Risk Check",
        symbol: watch.symbol,
        title: `Rank #${watch.modelRank}; ${watch.riskFlags?.[0] || "review setup"}`,
        rationale: `${watch.symbol}: still ranks highly, but flags include ${(watch.riskFlags || []).join("; ") || "trend or RSI review"}; RSI ${Math.round(watch.rsi14)}; rules score ${formatNumber(watch.rulesScore)}.`
      });
    }

    return calls.slice(0, 6);
  }

  const cleanCandidates = opportunities.filter((item) => item.score >= 72 && item.above50 && item.above200 && item.rsi14 <= 76);
  const extended = opportunities.find((item) => item.score >= 72 && item.rsi14 > 76);
  const watch = opportunities.find((item) => item.score >= 62 && item.score < 72 && item.above50 && item.above200);
  const calls = cleanCandidates.slice(0, 4).map((item) => ({
    label: "Metric Match",
    symbol: item.symbol,
    title: `Score ${formatNumber(item.score)}; RSI ${Math.round(item.rsi14)}`,
    rationale: `${item.symbol}: above 50-day and 200-day averages; 20-day return ${formatPercent(item.return20)}; relative strength vs SPY ${formatPercent(item.relativeStrength)}; volume ${item.volumeRatio.toFixed(2)}x 20-day average.`
  }));

  if (watch) {
    calls.push({
      label: "Near Threshold",
      symbol: watch.symbol,
      title: `Score ${formatNumber(watch.score)}; threshold 72.0`,
      rationale: `${watch.symbol}: above 50-day and 200-day averages; score is ${formatNumber(watch.score)}, below the 72.0 clean-candidate threshold; 20-day return ${formatPercent(watch.return20)}; volume ${watch.volumeRatio.toFixed(2)}x 20-day average.`
    });
  }

  if (extended) {
    calls.push({
      label: "RSI Above Limit",
      symbol: extended.symbol,
      title: `RSI ${Math.round(extended.rsi14)}; limit 76`,
      rationale: `${extended.symbol}: score ${formatNumber(extended.score)}; RSI is ${Math.round(extended.rsi14)}, above the configured 76 ceiling; 20-day return ${formatPercent(extended.return20)}; relative strength vs SPY ${formatPercent(extended.relativeStrength)}.`
    });
  }

  return calls.slice(0, 6);
}

function bottomModelCandidates(opportunities, limit = 12) {
  return opportunities
    .filter((item) => item.type === "stock" && hasModelRank(item))
    .sort((a, b) => Number(b.modelRank) - Number(a.modelRank))
    .slice(0, limit);
}

function bottomModelSectorClusters(opportunities, limit = 4) {
  const ranked = opportunities
    .filter((item) => item.type === "stock" && hasModelRank(item))
    .sort((a, b) => Number(b.modelRank) - Number(a.modelRank));
  const bottomWindow = ranked.slice(0, Math.max(25, Math.ceil(ranked.length * 0.15)));
  const sectors = new Map();
  bottomWindow.forEach((item) => {
    const sector = item.sector || "Unknown";
    const entry = sectors.get(sector) || { sector, count: 0, examples: [] };
    entry.count += 1;
    if (entry.examples.length < 4) entry.examples.push(item.symbol);
    sectors.set(sector, entry);
  });
  return [...sectors.values()]
    .sort((a, b) => b.count - a.count)
    .slice(0, limit)
    .map((entry) => ({
      ...entry,
      rationale: `${entry.count} of the weakest model-ranked names are in ${entry.sector}; examples include ${entry.examples.join(", ")}.`
    }));
}

function buildAvoidList(opportunities, aiRecommendations) {
  const bottomCompanies = bottomModelCandidates(opportunities, 10);
  if (!bottomCompanies.length) {
    return {
      status: "missing_model",
      summary: "Stay-away candidates require a current model-ranking file; none was available for this snapshot.",
      sectors: [],
      companies: []
    };
  }

  const sectors = bottomModelSectorClusters(opportunities, 4);
  const worst = bottomCompanies.slice(0, 8).map((item) => ({
    symbol: item.symbol,
    name: item.name,
    sector: item.sector,
    modelRank: finiteNumber(item.modelRank),
    modelUniverseCount: finiteNumber(item.modelUniverseCount),
    modelPercentile: roundedNumber(item.modelPercentile, 1),
    rulesScore: roundedNumber(item.rulesScore, 1),
    relativeReturn60VsSpy: roundedNumber(item.relativeReturn60VsSpy, 2),
    rsi14: roundedNumber(item.rsi14, 0),
    above50: item.above50,
    above200: item.above200,
    riskFlags: item.riskFlags || [],
    modelEvidence: `${item.symbol} ranks #${item.modelRank} of ${item.modelUniverseCount || "the scored universe"} with ${formatPercent(item.relativeReturn60VsSpy)} 60-day relative return versus SPY, rules score ${formatNumber(item.rulesScore)}, and ${item.above50 && item.above200 ? "positive" : "weak or mixed"} trend alignment.`,
    rationale: `${item.symbol} sits near the bottom of the model book; avoid fresh long exposure unless the trend and relative-strength setup materially improves.`
  }));

  const aiAvoid = aiRecommendations?.status === "ready" ? aiRecommendations.avoidList : null;
  const aiSectors = new Map((aiAvoid?.sectors || []).map((item) => [item.sector, item]));
  const aiCompanies = new Map((aiAvoid?.companies || []).map((item) => [item.symbol, item]));
  const aiSummary = cleanMemoText(aiAvoid?.summary);

  return {
    status: "ready",
    summary:
      aiSummary ||
      `Avoid list is driven by the bottom of the XGBoost rank model: ${worst.map((item) => item.symbol).slice(0, 5).join(", ")} are the weakest current long candidates, with sector pressure most visible in ${sectors.map((item) => item.sector).slice(0, 2).join(" and ")}.`,
    sectors: sectors.map((item) => ({
      ...item,
      rationale: cleanMemoText(aiSectors.get(item.sector)?.rationale) || item.rationale
    })),
    companies: worst.map((item) => {
      const aiItem = aiCompanies.get(item.symbol);
      return {
        ...item,
        rationale: cleanMemoText(aiItem?.rationale) || item.rationale,
        modelEvidence: item.modelEvidence
      };
    })
  };
}

function fallbackAiRecommendations(reason) {
  return {
    status: reason,
    model: null,
    headline: "AI macro and model synthesis is not configured yet.",
    macroView: "The dashboard can still show model-ranked and rules-based recommendations. Add OPENAI_API_KEY to .env and rerun the refresh to generate AI recommendations that connect macro context, public source summaries, and the XGBoost single-name rank model.",
    dailyRead: null,
    recommendations: [],
    portfolioNotes: [
      "Model-ranked and rules-based screening remain available without an API key.",
      "The AI layer is designed to explain why a model candidate matters now, what macro backdrop supports it, and what would invalidate it."
    ],
    openQuestions: [
      "Which publications should be weighted most heavily?",
      "Should the AI favor the model's highest-ranked names or require cleaner trend confirmation?"
    ],
    avoidList: {
      summary: "The stay-away list is generated deterministically from the lowest-ranked model names when the model snapshot is available.",
      sectors: [],
      companies: []
    },
    sourceRefs: []
  };
}

function responseText(payload) {
  if (payload.output_text) return payload.output_text;
  return (payload.output || [])
    .flatMap((item) => item.content || [])
    .map((content) => content.text || "")
    .join("\n")
    .trim();
}

function estimateOpenAiCost(model, usage = {}) {
  const pricing = modelPricingPerMillion[model];
  const inputTokens = usage.input_tokens || 0;
  const cachedInputTokens = usage.input_tokens_details?.cached_tokens || 0;
  const billableInputTokens = Math.max(0, inputTokens - cachedInputTokens);
  const outputTokens = usage.output_tokens || 0;
  const totalTokens = usage.total_tokens || inputTokens + outputTokens;
  const reasoningTokens = usage.output_tokens_details?.reasoning_tokens || 0;

  if (!pricing) {
    return {
      model,
      inputTokens,
      cachedInputTokens,
      outputTokens,
      reasoningTokens,
      totalTokens,
      estimatedCostUsd: null,
      note: "No local pricing table entry for this model."
    };
  }

  const estimatedCostUsd =
    (billableInputTokens / 1_000_000) * pricing.input +
    (cachedInputTokens / 1_000_000) * pricing.cachedInput +
    (outputTokens / 1_000_000) * pricing.output;

  return {
    model,
    inputTokens,
    cachedInputTokens,
    outputTokens,
    reasoningTokens,
    totalTokens,
    estimatedCostUsd: Number(estimatedCostUsd.toFixed(6)),
    pricingPerMillion: pricing
  };
}

async function appendUsageLog(entry) {
  await mkdir(join(root, "data"), { recursive: true });
  await appendFile(join(root, "data/usage-log.jsonl"), `${JSON.stringify(entry)}\n`);
}

function compactCandidate(item) {
  return {
    symbol: item.symbol,
    name: item.name,
    type: item.type,
    sector: item.sector,
    score: roundedNumber(item.score, 1),
    rulesScore: roundedNumber(item.rulesScore, 1),
    modelRank: finiteNumber(item.modelRank),
    modelUniverseCount: finiteNumber(item.modelUniverseCount),
    modelScore: roundedNumber(item.modelScore, 6),
    modelPercentile: roundedNumber(item.modelPercentile, 1),
    modelBucket: item.modelBucket || null,
    modelReasons: item.modelReasons || [],
    riskFlags: item.riskFlags || [],
    modelAsOfDate: item.modelAsOfDate || null,
    close: roundedNumber(item.close, 2),
    changePct: roundedNumber(item.changePct, 2),
    return20: roundedNumber(item.return20, 2),
    return60: roundedNumber(item.return60, 2),
    return120: roundedNumber(item.return120, 2),
    relativeStrength: roundedNumber(item.relativeStrength, 2),
    relativeReturn60VsSpy: roundedNumber(item.relativeReturn60VsSpy, 2),
    sectorReturn60: roundedNumber(item.sectorReturn60, 2),
    volatility60d: roundedNumber(item.volatility60d, 4),
    distanceTo52wHigh: roundedNumber(item.distanceTo52wHigh, 2),
    rsi14: roundedNumber(item.rsi14, 0),
    volumeRatio: roundedNumber(item.volumeRatio, 2),
    above50: item.above50,
    above100: item.above100,
    above200: item.above200,
    recentCross: item.recentCross,
    tags: item.tags
  };
}

function nasdaqValue(node) {
  return node && typeof node === "object" && "value" in node ? node.value : node;
}

function parseYahooRssItems(xml, symbol) {
  return [...xml.matchAll(/<item\b[\s\S]*?<\/item>/gi)]
    .slice(0, 8)
    .map((match, index) => {
      const item = match[0];
      const title = stripTags(item.match(/<title>([\s\S]*?)<\/title>/i)?.[1] || "");
      const url = decodeHtml(item.match(/<link>([\s\S]*?)<\/link>/i)?.[1] || "");
      const publishedAt = normalizeDate(item.match(/<pubDate>([\s\S]*?)<\/pubDate>/i)?.[1] || "");
      const summary = stripTags(item.match(/<description>([\s\S]*?)<\/description>/i)?.[1] || "").slice(0, 280);
      return {
        id: `${symbol}-N${index + 1}`,
        sourceName: "Yahoo Finance News RSS",
        title,
        url,
        publishedAt,
        summary
      };
    })
    .filter((item) => item.title && item.url);
}

function cleanCompanyUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value.startsWith("http") ? value : `https://${value}`);
    url.hash = "";
    return url.href.replace(/\/$/, "");
  } catch {
    return null;
  }
}

function shortDateFromIso(value) {
  const normalized = normalizeDate(value);
  if (!normalized) return null;
  return new Date(normalized).toISOString().slice(0, 10);
}

function buildEarningsSummary(earnings) {
  if (!earnings) return "Earnings timing was unavailable from the free sources checked.";
  const pieces = [];
  const lastDate = shortDateFromIso(earnings.lastReportedDate) || earnings.lastReportedDateText;
  if (lastDate) {
    pieces.push(
      `Last reported ${lastDate}${earnings.lastFiscalQuarter ? ` for the ${earnings.lastFiscalQuarter} fiscal quarter` : ""}` +
        `${earnings.lastEps != null ? `; EPS ${earnings.lastEps}` : ""}` +
        `${earnings.lastConsensus != null ? ` versus consensus ${earnings.lastConsensus}` : ""}` +
        `${earnings.lastSurprisePct != null ? `; surprise ${earnings.lastSurprisePct}%` : ""}.`
    );
  } else {
    pieces.push("Last earnings report date was not available from the free sources checked.");
  }

  const nextDate = shortDateFromIso(earnings.nextReportDate) || earnings.nextReportDateText;
  if (nextDate) {
    pieces.push(`Next earnings report date: ${nextDate}.`);
  } else if (earnings.nextFiscalPeriod) {
    pieces.push(`Exact next report date was not available; Nasdaq lists the next forecast fiscal period as ${earnings.nextFiscalPeriod}${earnings.nextConsensus != null ? ` with consensus ${earnings.nextConsensus}` : ""}.`);
  } else {
    pieces.push("Exact next earnings report date was not available.");
  }
  return pieces.join(" ");
}

function companyOverviewSummary(context) {
  const description = context?.companyDescription || "";
  if (!description) return "";
  return description.length > 320 ? `${description.slice(0, 317).trim()}...` : description;
}

function marketCapFromSummary(summary, symbol) {
  const raw = nasdaqValue(summary?.data?.summaryData?.MarketCap);
  const value = parseLargeNumber(raw);
  const text = formatMarketCap(value);
  if (!text) return null;
  return {
    value,
    text,
    sourceName: "Nasdaq quote summary",
    sourceUrl: `https://www.nasdaq.com/market-activity/stocks/${symbol.toLowerCase()}`
  };
}

function normalizeAiRecommendationContext(parsed, companyContexts, validSourceRefs) {
  const bySymbol = new Map(companyContexts.map((context) => [context.symbol, context]));
  const validRefSet = new Set(validSourceRefs);
  const recommendations = (parsed.recommendations || []).map((recommendation) => {
    const context = bySymbol.get(recommendation.symbol);
    if (!context) return recommendation;

    const sourceRefs = [
      ...(recommendation.sourceRefs || []),
      context.investorRelations?.id,
      context.marketCap?.sourceId,
      context.earnings?.sourceId
    ].filter((ref, index, refs) => ref && validRefSet.has(ref) && refs.indexOf(ref) === index);

    return {
      ...recommendation,
      companyOverview: companyOverviewSummary(context) || recommendation.companyOverview || "",
      marketCap: context.marketCap?.text || recommendation.marketCap || "",
      earningsContext: context.earnings?.summary || recommendation.earningsContext || "",
      sourceRefs
    };
  });
  return { ...parsed, recommendations };
}

function likelyIrLink(anchorText, href) {
  const haystack = `${anchorText} ${href}`.toLowerCase();
  return /\binvestor(s)?\b|investor-relations|\bir\b/.test(haystack) && !/(careers|privacy|terms|cookie|linkedin|facebook|twitter|youtube|instagram)/.test(haystack);
}

async function discoverInvestorRelationsUrl(companyUrl) {
  const cleaned = cleanCompanyUrl(companyUrl);
  if (!cleaned) return null;

  try {
    const base = new URL(cleaned);
    const html = await fetchText(base.href, { timeout: 9000 });
    for (const match of html.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/gi)) {
      const href = attrValue(match[1], "href");
      const label = stripTags(match[2]);
      if (!href || !likelyIrLink(label, href)) continue;
      try {
        const url = new URL(decodeHtml(href), base);
        if (!["http:", "https:"].includes(url.protocol)) continue;
        return url.href;
      } catch {
        // Keep looking for a usable investor relations link.
      }
    }
  } catch {
    // Some homepages block automated requests; deterministic URL guesses still cover many IR sites.
  }

  const base = new URL(cleaned);
  const host = base.hostname.replace(/^www\./, "");
  return `${base.protocol}//investors.${host}`;
}

function compactIrPage(html, fallbackUrl) {
  if (!html) return null;
  const text = visibleTextFromHtml(html).slice(0, 2200);
  return {
    title: titleFromHtml(html, "Investor Relations"),
    summary: summaryFromHtml(html, "Investor relations page"),
    excerpt: text,
    url: fallbackUrl
  };
}

async function fetchInvestorRelationsContext(symbol, companyUrl) {
  const investorUrl = await discoverInvestorRelationsUrl(companyUrl);
  if (!investorUrl) return null;

  try {
    const html = await fetchText(investorUrl, { timeout: 12000 });
    return {
      id: `${symbol}-IR`,
      type: "investor_relations",
      sourceName: "Company investor relations",
      ...compactIrPage(html, investorUrl)
    };
  } catch {
    return {
      id: `${symbol}-IR`,
      type: "investor_relations",
      sourceName: "Company investor relations",
      title: "Investor Relations",
      summary: "Investor relations URL discovered, but the page could not be fetched during this refresh.",
      excerpt: "",
      url: investorUrl
    };
  }
}

async function fetchNasdaqCompanyContext(symbol) {
  const [profile, surprise, eps, summary] = await Promise.all([
    fetchJson(`https://api.nasdaq.com/api/company/${encodeURIComponent(symbol)}/company-profile`, { timeout: 12000 }).catch(() => null),
    fetchJson(`https://api.nasdaq.com/api/company/${encodeURIComponent(symbol)}/earnings-surprise`, { timeout: 12000 }).catch(() => null),
    fetchJson(`https://api.nasdaq.com/api/quote/${encodeURIComponent(symbol)}/eps?assetclass=stocks`, { timeout: 12000 }).catch(() => null),
    fetchJson(`https://api.nasdaq.com/api/quote/${encodeURIComponent(symbol)}/summary?assetclass=stocks`, { timeout: 12000 }).catch(() => null)
  ]);

  const profileData = profile?.data || {};
  const surpriseRows = surprise?.data?.earningsSurpriseTable?.rows || [];
  const epsRows = eps?.data?.earningsPerShare || [];
  const lastReport = surpriseRows[0] || null;
  const nextQuarter = epsRows.find((row) => String(row.type || "").toLowerCase().includes("upcoming")) || null;

  return {
    companyName: nasdaqValue(profileData.CompanyName),
    companyDescription: stripTags(String(nasdaqValue(profileData.CompanyDescription) || "")).slice(0, 800),
    companyUrl: cleanCompanyUrl(nasdaqValue(profileData.CompanyUrl)),
    industry: nasdaqValue(profileData.Industry),
    sector: nasdaqValue(profileData.Sector),
    marketCap: marketCapFromSummary(summary, symbol),
    earnings: {
      lastReportedDate: normalizeDate(lastReport?.dateReported),
      lastReportedDateText: lastReport?.dateReported || null,
      lastFiscalQuarter: lastReport?.fiscalQtrEnd || null,
      lastEps: lastReport?.eps ?? null,
      lastConsensus: lastReport?.consensusForecast ?? null,
      lastSurprisePct: lastReport?.percentageSurprise ?? null,
      nextReportDate: null,
      nextReportDateText: null,
      nextFiscalPeriod: nextQuarter?.period || null,
      nextConsensus: nextQuarter?.consensus ?? null,
      sourceName: "Nasdaq earnings data",
      sourceUrl: `https://www.nasdaq.com/market-activity/stocks/${symbol.toLowerCase()}/earnings`
    }
  };
}

async function fetchCompanyNews(symbol) {
  try {
    const xml = await fetchText(`https://feeds.finance.yahoo.com/rss/2.0/headline?s=${encodeURIComponent(symbol)}&region=US&lang=en-US`, { timeout: 12000 });
    return parseYahooRssItems(xml, symbol).slice(0, 5);
  } catch {
    return [];
  }
}

async function fetchCompanyContext(candidate, index) {
  const symbol = candidate.symbol;
  const nasdaq = await fetchNasdaqCompanyContext(symbol);
  const [investorRelations, news] = await Promise.all([
    fetchInvestorRelationsContext(symbol, nasdaq.companyUrl),
    fetchCompanyNews(symbol)
  ]);
  const sourcePrefix = `C${index + 1}`;
  const sources = [];
  if (investorRelations?.url) {
    sources.push({
      id: `${sourcePrefix}-IR`,
      type: "investor_relations",
      title: investorRelations.title || `${symbol} Investor Relations`,
      url: investorRelations.url,
      sourceName: investorRelations.sourceName
    });
  }
  if (nasdaq.earnings?.sourceUrl) {
    sources.push({
      id: `${sourcePrefix}-EARNINGS`,
      type: "earnings",
      title: `${symbol} earnings history and forecast`,
      url: nasdaq.earnings.sourceUrl,
      sourceName: nasdaq.earnings.sourceName
    });
  }
  if (nasdaq.marketCap?.sourceUrl) {
    sources.push({
      id: `${sourcePrefix}-MARKETCAP`,
      type: "market_data",
      title: `${symbol} quote summary`,
      url: nasdaq.marketCap.sourceUrl,
      sourceName: nasdaq.marketCap.sourceName
    });
  }
  news.forEach((item, newsIndex) => {
    sources.push({
      id: `${sourcePrefix}-N${newsIndex + 1}`,
      type: "news",
      title: item.title,
      url: item.url,
      sourceName: item.sourceName,
      publishedAt: item.publishedAt
    });
  });

  return {
    id: sourcePrefix,
    symbol,
    name: candidate.name,
    modelRank: candidate.modelRank,
    modelPercentile: candidate.modelPercentile,
    recentReturns: {
      return20: candidate.return20,
      return60: candidate.return60,
      return120: candidate.return120,
      relativeReturn60VsSpy: candidate.relativeReturn60VsSpy
    },
    companyName: nasdaq.companyName || candidate.name,
    companyDescription: nasdaq.companyDescription,
    companyUrl: nasdaq.companyUrl,
    industry: nasdaq.industry,
    sector: candidate.sector || nasdaq.sector,
    marketCap: nasdaq.marketCap
      ? {
          ...nasdaq.marketCap,
          sourceId: `${sourcePrefix}-MARKETCAP`
        }
      : null,
    marketCapText: nasdaq.marketCap?.text || null,
    investorRelations: investorRelations
      ? {
          id: `${sourcePrefix}-IR`,
          url: investorRelations.url,
          title: investorRelations.title,
          summary: investorRelations.summary,
          excerpt: investorRelations.excerpt?.slice(0, 1200) || ""
        }
      : null,
    earnings: {
      ...nasdaq.earnings,
      sourceId: `${sourcePrefix}-EARNINGS`,
      summary: buildEarningsSummary(nasdaq.earnings)
    },
    news: news.map((item, newsIndex) => ({
      ...item,
      id: `${sourcePrefix}-N${newsIndex + 1}`
    })),
    sources
  };
}

async function buildCompanyContexts(candidates) {
  const selected = candidates.slice(0, Math.max(0, companyContextCount));
  const contexts = await mapLimit(selected, 3, async (candidate, index) => {
    try {
      return await fetchCompanyContext(candidate, index);
    } catch (error) {
      return {
        id: `C${index + 1}`,
        symbol: candidate.symbol,
        name: candidate.name,
        error: error.message,
        sources: []
      };
    }
  });
  return contexts.filter(Boolean);
}

async function buildAiRecommendations({ opportunities, macro, calendar, sources, rulesRecommendations, sectorPerformance, model: modelSummary, promptText }) {
  const apiKey = process.env.OPENAI_API_KEY;
  if (process.env.SKIP_AI === "1") return fallbackAiRecommendations("skipped_by_env");
  if (!apiKey) return fallbackAiRecommendations("missing_api_key");

  const aiModel = process.env.OPENAI_MODEL || defaultOpenAiModel;
  const sourceArticles = sortArticlesNewestFirst(
    sources
      .flatMap((source) => source.articles || [])
      .filter((article) => article.title && article.url)
  ).slice(0, 36);
  const sourceTape = sourceArticles.map(({ sourceName, title, url, publishedAt, summary, excerpt }, index) => ({
    id: `S${index + 1}`,
    sourceName,
    title,
    url,
    publishedAt,
    summary,
    excerpt: excerpt?.slice(0, 1200) || ""
  }));
  const modelCandidates = opportunities.filter(hasModelRank).slice(0, 30).map(compactCandidate);
  const avoidCandidates = bottomModelCandidates(opportunities, 16).map(compactCandidate);
  const avoidSectors = bottomModelSectorClusters(opportunities, 5);
  const companyContexts = await buildCompanyContexts(modelCandidates);
  const validSymbols = [
    ...new Set(
      companyContexts.length
        ? companyContexts.map((item) => item.symbol)
        : modelCandidates.length
          ? modelCandidates.map((item) => item.symbol)
        : [
            ...opportunities.slice(0, 40).map((item) => item.symbol),
            ...opportunities.filter((item) => item.type === "etf").slice(0, 15).map((item) => item.symbol),
            ...rulesRecommendations.map((item) => item.symbol)
          ]
    )
  ].filter(Boolean);
  const companySourceRefs = companyContexts.flatMap((context) => context.sources || []);
  const sourceRefIds = [...sourceTape.map((source) => source.id), ...companySourceRefs.map((source) => source.id)];
  const sourceRefSchema = sourceRefIds.length
    ? { type: "string", enum: sourceRefIds }
    : { type: "string" };
  const validAvoidSymbols = avoidCandidates.map((item) => item.symbol).filter(Boolean);
  const payload = {
    generatedAt: new Date().toISOString(),
    model: modelSummary,
    macro,
    upcomingEvents: calendar.slice(0, 8),
    sourceTape,
    companyContexts,
    companySourceRefs,
    sourceStatus: sources.map(({ name, url, category, trust, ok, articleCount, summary }) => ({
      name,
      url,
      category,
      trust,
      ok,
      articleCount,
      summary
    })),
    rulesRecommendations,
    sectorPerformance: sectorPerformance.map(({ sector, symbol, change1d, change5d, change30d, ytd, relative30d, above50, above200, rsi14 }) => ({
      sector,
      symbol,
      change1d: Number(change1d.toFixed(2)),
      change5d: Number(change5d.toFixed(2)),
      change30d: Number(change30d.toFixed(2)),
      ytd: Number(ytd.toFixed(2)),
      relative30d: Number(relative30d.toFixed(2)),
      above50,
      above200,
      rsi14: Math.round(rsi14)
    })),
    modelCandidates,
    avoidCandidates,
    avoidSectors,
    topMomentum: opportunities.slice(0, 18).map(compactCandidate),
    extendedMomentum: opportunities
      .filter((item) => item.score >= 70 && item.rsi14 > 76)
      .slice(0, 8)
      .map(compactCandidate),
    etfMomentum: opportunities
      .filter((item) => item.type === "etf")
      .slice(0, 10)
      .map(compactCandidate),
    validRecommendationSymbols: validSymbols
  };

  const requestBody = {
    model: aiModel,
    instructions: promptText || defaultAiPrompt,
    input: [
      {
        role: "user",
        content: [
          {
            type: "input_text",
            text: `Create the AI strategy memo from this dashboard snapshot:\n${JSON.stringify(payload, null, 2)}`
          }
        ]
      }
    ],
    text: {
      format: {
        type: "json_schema",
        name: "morning_desk_ai_recommendations",
        strict: true,
        schema: {
          type: "object",
          additionalProperties: false,
          required: ["headline", "macroView", "dailyRead", "recommendations", "avoidList", "portfolioNotes", "openQuestions", "sourceRefs"],
          properties: {
            headline: { type: "string" },
            macroView: { type: "string" },
            dailyRead: {
              type: "object",
              additionalProperties: false,
              required: ["headline", "body", "keyTakeaways", "watchItems"],
              properties: {
                headline: { type: "string" },
                body: { type: "string" },
                keyTakeaways: {
                  type: "array",
                  minItems: 4,
                  maxItems: 6,
                  items: { type: "string" }
                },
                watchItems: {
                  type: "array",
                  minItems: 3,
                  maxItems: 5,
                  items: { type: "string" }
                }
              }
            },
            recommendations: {
              type: "array",
              minItems: 3,
              maxItems: 6,
              items: {
                type: "object",
                additionalProperties: false,
                required: [
                  "symbol",
                  "action",
                  "conviction",
                  "setup",
                  "whyNow",
                  "rationale",
                  "companyOverview",
                  "marketCap",
                  "earningsContext",
                  "recentNews",
                  "macroLink",
                  "macroEvidence",
                  "modelEvidence",
                  "technicalEvidence",
                  "momentumEvidence",
                  "risk",
                  "invalidation",
                  "sourceRefs"
                ],
                properties: {
                  symbol: validSymbols.length ? { type: "string", enum: validSymbols } : { type: "string" },
                  action: { type: "string" },
                  conviction: { type: "string", enum: ["High", "Medium", "Low", "Review"] },
                  setup: { type: "string" },
                  whyNow: { type: "string" },
                  rationale: { type: "string" },
                  companyOverview: { type: "string" },
                  marketCap: { type: "string" },
                  earningsContext: { type: "string" },
                  recentNews: { type: "string" },
                  macroLink: { type: "string" },
                  macroEvidence: { type: "string" },
                  modelEvidence: { type: "string" },
                  technicalEvidence: { type: "string" },
                  momentumEvidence: { type: "string" },
                  risk: { type: "string" },
                  invalidation: { type: "string" },
                  sourceRefs: {
                    type: "array",
                    minItems: sourceRefIds.length ? 1 : 0,
                    maxItems: 6,
                    items: sourceRefSchema
                  }
                }
              }
            },
            avoidList: {
              type: "object",
              additionalProperties: false,
              required: ["summary", "sectors", "companies"],
              properties: {
                summary: { type: "string" },
                sectors: {
                  type: "array",
                  maxItems: 4,
                  items: {
                    type: "object",
                    additionalProperties: false,
                    required: ["sector", "rationale"],
                    properties: {
                      sector: { type: "string" },
                      rationale: { type: "string" }
                    }
                  }
                },
                companies: {
                  type: "array",
                  maxItems: 8,
                  items: {
                    type: "object",
                    additionalProperties: false,
                    required: ["symbol", "rationale", "modelEvidence"],
                    properties: {
                      symbol: validAvoidSymbols.length ? { type: "string", enum: validAvoidSymbols } : { type: "string" },
                      rationale: { type: "string" },
                      modelEvidence: { type: "string" }
                    }
                  }
                }
              }
            },
            portfolioNotes: {
              type: "array",
              minItems: 2,
              maxItems: 5,
              items: { type: "string" }
            },
            openQuestions: {
              type: "array",
              minItems: 2,
              maxItems: 5,
              items: { type: "string" }
            },
            sourceRefs: {
              type: "array",
              maxItems: 12,
              items: sourceRefSchema
            }
          }
        }
      }
    },
    reasoning: { effort: "minimal" },
    max_output_tokens: 8000
  };

  try {
    const response = await fetch("https://api.openai.com/v1/responses", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${apiKey}`
      },
      body: JSON.stringify(requestBody)
    });
    const json = await response.json();
    if (!response.ok) throw new Error(json.error?.message || `${response.status} ${response.statusText}`);
    const usage = estimateOpenAiCost(aiModel, json.usage);
    const text = responseText(json);
    if (!text) throw new Error("OpenAI response did not include final text; try a larger max_output_tokens value or a lower reasoning effort.");
    const parsed = normalizeAiRecommendationContext(JSON.parse(text), companyContexts, sourceRefIds);
    await appendUsageLog({
      generatedAt: new Date().toISOString(),
      status: "ready",
      model: aiModel,
      usage
    });
    return {
      status: "ready",
      model: aiModel,
      usage,
      companyContextCount: companyContexts.length,
      ...parsed
    };
  } catch (error) {
    await appendUsageLog({
      generatedAt: new Date().toISOString(),
      status: "ai_error",
      model: aiModel,
      error: error.message
    });
    return {
      ...fallbackAiRecommendations("ai_error"),
      model: aiModel,
      headline: "AI recommendation generation failed.",
      macroView: `The deterministic dashboard still refreshed, but the AI call failed: ${error.message}`
    };
  }
}

async function main() {
  await loadLocalEnv();

  const [sourceMarkdown, universeConfigText, aiPromptText, modelRankings, modelExplainability] = await Promise.all([
    readFile(join(root, "config/news-sources.md"), "utf8"),
    readFile(join(root, "config/universe.json"), "utf8"),
    readFile(join(root, "config/ai-recommendation-prompt.md"), "utf8").catch(() => defaultAiPrompt),
    loadModelRankings(),
    loadModelExplainability()
  ]);
  const modelSummary = publicModelSummary(modelRankings, modelExplainability);

  const universeConfig = JSON.parse(universeConfigText);
  const markdownSources = parseMarkdownSources(sourceMarkdown);
  const checkedSourcesPromise = mapLimit(markdownSources, 5, checkSource);

  let stocks = universeConfig.fallbackStocks.map((item) => ({ ...item, type: "stock" }));
  try {
    stocks = await fetchSp500Constituents();
  } catch (error) {
    console.warn(`Using fallback stock universe: ${error.message}`);
  }

  const etfs = universeConfig.etfs.map((item) => ({ ...item, type: "etf", sector: item.assetClass }));
  let universe = [...stocks, ...etfs];
  if (maxTickers > 0) universe = universe.slice(0, maxTickers);

  console.log(`Screening ${universe.length} instruments...`);

  const spyHistory = await fetchHistory("SPY");
  const spyCloses = spyHistory.map((row) => row.close);
  const spyReturn20 = percentChange(spyCloses.at(-1), spyCloses.at(-21));

  const signals = await mapLimit(universe, 10, async (item, index) => {
    try {
      if (index > 0 && index % 50 === 0) console.log(`...${index}/${universe.length}`);
      const history = item.symbol === "SPY" ? spyHistory : await fetchHistory(item.symbol);
      await sleep(20);
      return computeSignal(item, history, spyReturn20);
    } catch (error) {
      return null;
    }
  });

  const opportunities = mergeModelScores(signals.filter(Boolean), modelRankings);

  const historyCache = new Map([["SPY", spyHistory]]);

  const marketSymbols = ["SPY", "QQQ", "IWM", "TLT", "GLD", "HYG"];
  const marketStrip = (
    await mapLimit(marketSymbols, 4, async (symbol) => {
      const existing = opportunities.find((item) => item.symbol === symbol);
      if (existing) return existing;
      const metadata = etfs.find((item) => item.symbol === symbol) || { symbol, name: symbol };
      const history = await fetchHistory(symbol);
      historyCache.set(symbol, history);
      return computeSignal(metadata, history, spyReturn20);
    })
  ).map((item) => ({
    symbol: item.symbol,
    label: item.name.replace(/ ETF| Trust| Fund/g, ""),
    price: item.close,
    changePct: item.changePct
  }));

  const sectorEtfs = universeConfig.sectorEtfs || [];
  const sectorPerformance = (
    await mapLimit(sectorEtfs, 4, async (item) => {
      const history = historyCache.get(item.symbol) || await fetchHistory(item.symbol);
      historyCache.set(item.symbol, history);
      return computeSectorPerformance(item, history, spyHistory);
    })
  ).sort((a, b) => b.change30d - a.change30d);

  const [macro, sources] = await Promise.all([
    mapLimit(macroSeries, 4, fetchFredSeries),
    checkedSourcesPromise
  ]);
  const upcomingCalendar = calendar.filter((event) => new Date(`${event.date}T23:59:59`) >= new Date()).slice(0, 8);
  const rulesRecommendations = buildRecommendations(opportunities);
  const aiRecommendations = await buildAiRecommendations({
    opportunities,
    macro,
    calendar: upcomingCalendar,
    sources,
    rulesRecommendations,
    sectorPerformance,
    model: modelSummary,
    promptText: aiPromptText
  });
  const avoidList = buildAvoidList(opportunities, aiRecommendations);

  const snapshot = {
    generatedAt: new Date().toISOString(),
    model: modelSummary,
    note: buildNote({
      opportunities,
      macro,
      sources,
      model: modelSummary,
      sectorPerformance,
      rulesRecommendations,
      aiRecommendations
    }),
    aiRecommendations,
    avoidList,
    recommendations: rulesRecommendations,
    sectorPerformance,
    marketStrip,
    opportunities,
    macro,
    calendar: upcomingCalendar,
    sources
  };

  await writeFile(join(root, snapshotOutput), `${JSON.stringify(snapshot, null, 2)}\n`);
  console.log(`Wrote ${snapshotOutput} with ${opportunities.length} ranked instruments.`);
}

export { checkSource, extractArticleCandidates, parseMarkdownSources, publishedDateFromHtml, sortArticlesNewestFirst };

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
