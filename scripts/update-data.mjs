import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const maxTickers = Number.parseInt(process.env.MAX_TICKERS || "0", 10);
const articlesPerSource = Number.parseInt(process.env.SOURCE_ARTICLES_PER_SOURCE || "3", 10);
const maxArticleCandidates = Number.parseInt(process.env.SOURCE_ARTICLE_CANDIDATES || "10", 10);
const snapshotOutput = process.env.SNAPSHOT_OUTPUT || "data/snapshot.json";
const today = new Date();
const startDate = new Date(today);
startDate.setDate(startDate.getDate() - 430);
const defaultAiPrompt = `Write a concise hedge-fund-style morning strategy memo. Use only the supplied macro indicators, source summaries, upcoming events, XGBoost model rankings, rules-based desk calls, and momentum metrics. Treat every output as a research recommendation, not financial advice.`;
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

function buildNote({ opportunities, macro, sources, model }) {
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
  const modelText = modelReady
    ? `${model.scoredCount} S&P 500 names were scored by the XGBoost rank model as of ${model.asOfDate || "the latest available close"}`
    : "The XGBoost rank model was not available for this refresh";
  const headline = modelReady
    ? `${modelText}; top model setups: ${leaderText}.`
    : `${breadth}% of screened names are above both 50-day and 200-day averages; top-ranked setups: ${leaderText}.`;
  const body = `The daily setup is based on end-of-day data across ${universeCount} S&P 500 constituents and major ETFs. ${modelText}. ${above50Pct}% are above the 50-day average, ${above200Pct}% are above the 200-day average, and ${breadth}% are above both. The highest-ranked opportunities are ${leaderText}. ETF confirmation leaders are ${etfText}. ${cleanCandidates} names pass the clean-candidate filter, while ${extended} ranked names have RSI above 76. ${sourceHits} public source pages were checked and ${articleHits} recent articles were ingested for the source tape.`;

  return {
    headline,
    body,
    changed: [
      modelReady
        ? `Model leader set: ${leaderText}; top-decile clean candidates: ${cleanCandidates}.`
        : "Model rankings were unavailable, so the note used the rules-based momentum score.",
      `${breadth}% of screened instruments sit above both 50-day and 200-day moving averages.`,
      `${above50Pct}% are above the 50-day average and ${above200Pct}% are above the 200-day average.`,
      leaders.length && modelReady
        ? `Top model rank: ${leaders[0].symbol} at #${leaders[0].modelRank} with ${(leaders[0].modelReasons || []).join("; ") || "available model evidence"}.`
        : leaders.length
          ? `Top score: ${leaders[0].symbol} at ${Math.round(leaders[0].score)} with ${leaders[0].tags.join(", ").toLowerCase() || "trend confirmation"}.`
          : "No ranked leaders were available.",
      `${cleanCandidates} names pass the clean-candidate filter; ${extended} ranked names are above the RSI ceiling.`,
      `${macro.filter((item) => item.value !== "n/a").length} macro series refreshed from free FRED endpoints.`,
      `${articleHits} recent article summaries were extracted from configured research and commentary sources.`
    ],
    watch: [
      modelReady
        ? `${extended} top-decile model names have RSI above 76.`
        : `${extended} screened names have score >= 70 and RSI above 76.`,
      upcomingEvents.length ? `Next scheduled macro events: ${upcomingEvents.map((event) => `${event.event} on ${event.date}`).join("; ")}.` : "No upcoming macro events are currently listed in the local calendar.",
      `${failedSources} of ${sources.length} configured source pages failed the latest source check.`
    ]
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

function fallbackAiRecommendations(reason) {
  return {
    status: reason,
    model: null,
    headline: "AI macro and model synthesis is not configured yet.",
    macroView: "The dashboard can still show model-ranked and rules-based recommendations. Add OPENAI_API_KEY to .env and rerun the refresh to generate AI recommendations that connect macro context, public source summaries, and the XGBoost single-name rank model.",
    recommendations: [],
    portfolioNotes: [
      "Model-ranked and rules-based screening remain available without an API key.",
      "The AI layer is designed to explain why a model candidate matters now, what macro backdrop supports it, and what would invalidate it."
    ],
    openQuestions: [
      "Which publications should be weighted most heavily?",
      "Should the AI favor the model's highest-ranked names or require cleaner trend confirmation?"
    ],
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
  const validSymbols = [
    ...new Set(
      modelCandidates.length
        ? modelCandidates.map((item) => item.symbol)
        : [
            ...opportunities.slice(0, 40).map((item) => item.symbol),
            ...opportunities.filter((item) => item.type === "etf").slice(0, 15).map((item) => item.symbol),
            ...rulesRecommendations.map((item) => item.symbol)
          ]
    )
  ].filter(Boolean);
  const sourceRefSchema = sourceTape.length
    ? { type: "string", enum: sourceTape.map((source) => source.id) }
    : { type: "string" };
  const payload = {
    generatedAt: new Date().toISOString(),
    model: modelSummary,
    macro,
    upcomingEvents: calendar.slice(0, 8),
    sourceTape,
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
          required: ["headline", "macroView", "recommendations", "portfolioNotes", "openQuestions", "sourceRefs"],
          properties: {
            headline: { type: "string" },
            macroView: { type: "string" },
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
                  macroLink: { type: "string" },
                  macroEvidence: { type: "string" },
                  modelEvidence: { type: "string" },
                  technicalEvidence: { type: "string" },
                  momentumEvidence: { type: "string" },
                  risk: { type: "string" },
                  invalidation: { type: "string" },
                  sourceRefs: {
                    type: "array",
                    minItems: sourceTape.length ? 1 : 0,
                    maxItems: 4,
                    items: sourceRefSchema
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
              maxItems: 8,
              items: sourceRefSchema
            }
          }
        }
      }
    },
    reasoning: { effort: "minimal" },
    max_output_tokens: 6000
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
    const parsed = JSON.parse(text);
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

  const snapshot = {
    generatedAt: new Date().toISOString(),
    model: modelSummary,
    note: buildNote({ opportunities, macro, sources, model: modelSummary }),
    aiRecommendations,
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
