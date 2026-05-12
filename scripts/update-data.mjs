import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const maxTickers = Number.parseInt(process.env.MAX_TICKERS || "0", 10);
const articlesPerSource = Number.parseInt(process.env.SOURCE_ARTICLES_PER_SOURCE || "3", 10);
const maxArticleCandidates = Number.parseInt(process.env.SOURCE_ARTICLE_CANDIDATES || "6", 10);
const sourceMaxArticleAgeDays = Number.parseFloat(process.env.SOURCE_MAX_ARTICLE_AGE_DAYS || "30");
const companyContextCount = Number.parseInt(process.env.COMPANY_CONTEXT_COUNT || "12", 10);
const marketIntelArticleLimit = Number.parseInt(process.env.MARKET_INTEL_ARTICLE_LIMIT || "48", 10);
const marketIntelFreshHours = Number.parseFloat(process.env.MARKET_INTEL_FRESH_HOURS || "24");
const marketIntelImportantHours = Number.parseFloat(process.env.MARKET_INTEL_IMPORTANT_HOURS || "96");
const redditPostLimit = Number.parseInt(process.env.REDDIT_POST_LIMIT || "40", 10);
const marketCapFetchLimit = Number.parseInt(process.env.MARKET_CAP_FETCH_LIMIT || "60", 10);
const openAiTimeoutMs = Number.parseInt(process.env.OPENAI_TIMEOUT_MS || "180000", 10);
const allowStaleModelData = process.env.ALLOW_STALE_MODEL_DATA === "1";
const expectedMarketDataDate = process.env.EXPECTED_MARKET_DATA_DATE || "";
const skipReddit = process.env.SKIP_REDDIT === "1";
const snapshotOutput = process.env.SNAPSHOT_OUTPUT || "data/snapshot.json";
const scorebookOutput = process.env.SCOREBOOK_OUTPUT || "data/model-scorebook.json";
const monitoringOutput = process.env.MONITORING_OUTPUT || "data/model-monitoring.json";
const marketCapCacheMaxAgeHours = Number.parseFloat(process.env.MARKET_CAP_CACHE_MAX_AGE_HOURS || "18");
const deeperReadLookbackDays = Number.parseFloat(process.env.DEEPER_READ_LOOKBACK_DAYS || "7");
const deeperReadCandidateLimit = Number.parseInt(process.env.DEEPER_READ_CANDIDATE_LIMIT || "14", 10);
const macroReleaseLookbackHours = Number.parseFloat(process.env.MACRO_RELEASE_LOOKBACK_HOURS || "96");
const deeperReadHistoryPath = "data/deeper-read-history.json";
const today = new Date();
const startDate = new Date(today);
startDate.setDate(startDate.getDate() - 430);
const defaultAiPrompt = `Write a concise hedge-fund-style morning strategy memo. Use only the supplied macro indicators, source summaries, current-event drivers, upcoming events, XGBoost model rankings, deterministic desk calls, momentum metrics, and lowest-ranked avoid candidates. Treat every output as a research recommendation, not financial advice.`;
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
  { date: "2026-05-12", time: "8:30 AM ET", event: "Consumer Price Index", source: "BLS", importance: "High" },
  { date: "2026-05-13", time: "8:30 AM ET", event: "Producer Price Index", source: "BLS", importance: "High" },
  { date: "2026-05-28", time: "8:30 AM ET", event: "GDP Second Estimate", source: "BEA", importance: "High" },
  { date: "2026-05-28", time: "8:30 AM ET", event: "Personal Income and Outlays", source: "BEA", importance: "High" },
  { date: "2026-06-05", time: "8:30 AM ET", event: "Employment Situation", source: "BLS", importance: "High" },
  { date: "2026-06-17", time: "2:00 PM ET", event: "FOMC Rate Decision", source: "Federal Reserve", importance: "High" },
  { date: "2026-07-29", time: "2:00 PM ET", event: "FOMC Rate Decision", source: "Federal Reserve", importance: "High" }
];

const officialMacroReleaseSpecs = [
  {
    source: "BLS",
    eventPattern: /Employment Situation/i,
    releaseName: "Employment Situation",
    sourceName: "BLS Employment Situation",
    sourceUrl: "https://www.bls.gov/news.release/empsit.nr0.htm",
    parser: parseBlsEmploymentSituationRelease
  },
  {
    source: "BLS",
    eventPattern: /Consumer Price Index/i,
    releaseName: "Consumer Price Index",
    sourceName: "BLS Consumer Price Index",
    sourceUrl: "https://www.bls.gov/news.release/cpi.nr0.htm",
    parser: parseBlsCpiRelease
  },
  {
    source: "BLS",
    eventPattern: /Producer Price Index/i,
    releaseName: "Producer Price Index",
    sourceName: "BLS Producer Price Index",
    sourceUrl: "https://www.bls.gov/news.release/ppi.nr0.htm",
    parser: parseBlsPpiRelease
  },
  {
    source: "BEA",
    eventPattern: /GDP/i,
    releaseName: "Gross Domestic Product",
    sourceName: "BEA Gross Domestic Product",
    sourceUrl: "https://www.bea.gov/data/gdp/gross-domestic-product",
    parser: parseGenericOfficialMacroRelease
  },
  {
    source: "BEA",
    eventPattern: /Personal Income|Outlays|PCE/i,
    releaseName: "Personal Income and Outlays",
    sourceName: "BEA Personal Income and Outlays",
    sourceUrl: "https://www.bea.gov/products/personal-income-outlays",
    parser: parseGenericOfficialMacroRelease
  }
];

const redditSources = [
  { subreddit: "wallstreetbets", segment: "Retail momentum" },
  { subreddit: "stocks", segment: "Retail investing" },
  { subreddit: "investing", segment: "Retail investing" },
  { subreddit: "SecurityAnalysis", segment: "Fundamental research" },
  { subreddit: "options", segment: "Options sentiment" }
];

const tickerStopWords = new Set([
  "A",
  "AI",
  "ALL",
  "AM",
  "ATH",
  "CEO",
  "CFO",
  "CPI",
  "DD",
  "EPS",
  "ETF",
  "FBI",
  "FDA",
  "FOMC",
  "GDP",
  "IPO",
  "IRS",
  "IT",
  "JOB",
  "LOL",
  "M",
  "MAGA",
  "NYSE",
  "OP",
  "OTM",
  "PCE",
  "PM",
  "PUT",
  "SEC",
  "USA",
  "USD",
  "YOLO"
]);

function ymd(date) {
  return date.toISOString().slice(0, 10).replaceAll("-", "");
}

function dateKeyInTimeZone(date = new Date(), timeZone = "America/Los_Angeles") {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).formatToParts(date);
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${byType.year}-${byType.month}-${byType.day}`;
}

function offsetDateKey(dateKey, offsetDays) {
  const date = new Date(`${dateKey}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + offsetDays);
  return date.toISOString().slice(0, 10);
}

function parseClockTime(timeText) {
  const match = String(timeText || "").match(/\b(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\b/i);
  if (!match) return null;
  let hour = Number.parseInt(match[1], 10);
  const minute = Number.parseInt(match[2] || "0", 10);
  const meridiem = match[3].toUpperCase();
  if (meridiem === "PM" && hour !== 12) hour += 12;
  if (meridiem === "AM" && hour === 12) hour = 0;
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
  return { hour, minute };
}

function timeZoneOffsetMinutes(date, timeZone) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    timeZoneName: "shortOffset",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23"
  }).formatToParts(date);
  const offsetText = parts.find((part) => part.type === "timeZoneName")?.value || "GMT";
  if (offsetText === "GMT") return 0;
  const match = offsetText.match(/GMT([+-])(\d{1,2})(?::?(\d{2}))?/i);
  if (!match) return 0;
  const sign = match[1] === "-" ? -1 : 1;
  const hours = Number.parseInt(match[2], 10);
  const minutes = Number.parseInt(match[3] || "0", 10);
  return sign * (hours * 60 + minutes);
}

function zonedDateTimeToUtc(dateKey, timeText, timeZone = "America/New_York") {
  const clock = parseClockTime(timeText);
  const dateMatch = String(dateKey || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!clock || !dateMatch) return null;
  const [, year, month, day] = dateMatch.map(Number);
  let utc = new Date(Date.UTC(year, month - 1, day, clock.hour, clock.minute));
  for (let index = 0; index < 3; index += 1) {
    const offset = timeZoneOffsetMinutes(utc, timeZone);
    utc = new Date(Date.UTC(year, month - 1, day, clock.hour, clock.minute) - offset * 60 * 1000);
  }
  return utc;
}

function calendarEventReleaseAt(event) {
  if (!event?.date || !event?.time) return null;
  return zonedDateTimeToUtc(event.date, event.time, "America/New_York");
}

function calendarEventAgeHours(event, now = new Date()) {
  const releaseAt = calendarEventReleaseAt(event);
  if (!releaseAt) return null;
  return (now.getTime() - releaseAt.getTime()) / (60 * 60 * 1000);
}

function isCalendarEventDue(event, now = new Date()) {
  const ageHours = calendarEventAgeHours(event, now);
  return ageHours != null && ageHours >= 0;
}

function upcomingMacroEvents(events, now = new Date()) {
  return (events || [])
    .filter((event) => {
      const releaseAt = calendarEventReleaseAt(event);
      if (releaseAt) return releaseAt.getTime() > now.getTime();
      return new Date(`${event.date}T23:59:59`).getTime() >= now.getTime();
    })
    .slice(0, 8);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function retryableHttpStatus(status) {
  return [408, 429, 500, 502, 503, 504].includes(status);
}

function retryDelayMs(attempt) {
  return 2500 * 2 ** attempt + Math.round(Math.random() * 700);
}

async function fetchText(url, options = {}) {
  const retries = options.retries ?? 5;
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeout || 14000);
    try {
      const response = await fetch(url, {
        signal: controller.signal,
        headers: {
          "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36 MarketPulse/0.1",
          accept: "text/html,application/xhtml+xml,application/xml,text/csv,text/plain;q=0.9,*/*;q=0.8",
          "accept-language": "en-US,en;q=0.9"
        }
      });
      if (!response.ok) {
        lastError = new Error(`${response.status} ${response.statusText}`);
        if (retryableHttpStatus(response.status) && attempt < retries) {
          clearTimeout(timeout);
          await sleep(retryDelayMs(attempt));
          continue;
        }
        throw lastError;
      }
      return await response.text();
    } catch (error) {
      lastError = error;
      if (attempt < retries) {
        clearTimeout(timeout);
        await sleep(retryDelayMs(attempt));
        continue;
      }
      throw lastError;
    } finally {
      clearTimeout(timeout);
    }
  }
  throw lastError;
}

async function fetchJson(url, options = {}) {
  const retries = options.retries ?? 5;
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
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
      if (!response.ok) {
        lastError = new Error(`${response.status} ${response.statusText}`);
        if (retryableHttpStatus(response.status) && attempt < retries) {
          clearTimeout(timeout);
          await sleep(retryDelayMs(attempt));
          continue;
        }
        throw lastError;
      }
      return await response.json();
    } catch (error) {
      lastError = error;
      if (attempt < retries) {
        clearTimeout(timeout);
        await sleep(retryDelayMs(attempt));
        continue;
      }
      throw lastError;
    } finally {
      clearTimeout(timeout);
    }
  }
  throw lastError;
}

async function fetchPublicJson(url, options = {}) {
  const retries = options.retries ?? 5;
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeout || 14000);
    try {
      const response = await fetch(url, {
        signal: controller.signal,
        headers: {
          "user-agent": options.userAgent || "Mozilla/5.0 MarketPulse/0.1 personal research dashboard",
          accept: "application/json,text/plain,*/*",
          ...(options.headers || {})
        }
      });
      if (!response.ok) {
        lastError = new Error(`${response.status} ${response.statusText}`);
        if (retryableHttpStatus(response.status) && attempt < retries) {
          clearTimeout(timeout);
          await sleep(retryDelayMs(attempt));
          continue;
        }
        throw lastError;
      }
      return await response.json();
    } catch (error) {
      lastError = error;
      if (attempt < retries) {
        clearTimeout(timeout);
        await sleep(retryDelayMs(attempt));
        continue;
      }
      throw lastError;
    } finally {
      clearTimeout(timeout);
    }
  }
  throw lastError;
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

function optionalNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  return finiteNumber(value);
}

function optionalRoundedNumber(value, digits = 2) {
  const number = optionalNumber(value);
  return number == null ? null : Number(number.toFixed(digits));
}

function shouldSurfaceStopSell(item = {}) {
  return ["momentum_confirmed", "model_rebound_watch", "model_ranked_not_momentum_confirmed"].includes(item.setupType);
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

function pacificDateParts(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Los_Angeles",
    weekday: "short",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    hourCycle: "h23"
  }).formatToParts(date);
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return {
    isoDate: `${byType.year}-${byType.month}-${byType.day}`,
    weekday: byType.weekday,
    hour: Number(byType.hour)
  };
}

function previousBusinessDate(isoDate) {
  const date = new Date(`${isoDate}T12:00:00Z`);
  do {
    date.setUTCDate(date.getUTCDate() - 1);
  } while ([0, 6].includes(date.getUTCDay()));
  return date.toISOString().slice(0, 10);
}

function latestExpectedMarketDataDate(date = new Date()) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(expectedMarketDataDate)) return expectedMarketDataDate;
  const pacific = pacificDateParts(date);
  if (pacific.weekday === "Sat" || pacific.weekday === "Sun") return previousBusinessDate(pacific.isoDate);
  return pacific.hour >= 14 ? pacific.isoDate : previousBusinessDate(pacific.isoDate);
}

async function loadModelRankings() {
  try {
    const text = await readFile(join(root, "data/model-rank-scores.json"), "utf8");
    const payload = JSON.parse(text);
    const rankings = (payload.rankings || []).filter((item) => item?.symbol);
    const expectedAsOfDate = latestExpectedMarketDataDate();
    if (
      payload.status === "ready"
      && payload.asOfDate
      && payload.asOfDate < expectedAsOfDate
      && !allowStaleModelData
    ) {
      return {
        status: "stale",
        error: `Model rankings are stale: as-of ${payload.asOfDate}, expected ${expectedAsOfDate}. Stale model data was not used.`,
        generatedAt: payload.generatedAt || null,
        asOfDate: payload.asOfDate || null,
        expectedAsOfDate,
        model: payload.model || null,
        scoredCount: 0,
        requestedSymbolCount: payload.requestedSymbolCount || rankings.length,
        failedSymbolCount: payload.failedSymbolCount || 0,
        unscoredSymbolCount: payload.unscoredSymbolCount || 0,
        unscoredSymbols: payload.unscoredSymbols || [],
        technicalTape: null,
        marketRows: [],
        sectorRows: [],
        rankings: [],
        bySymbol: new Map()
      };
    }
    return {
      status: payload.status || "ready",
      generatedAt: payload.generatedAt || null,
      asOfDate: payload.asOfDate || null,
      expectedAsOfDate,
      model: payload.model || null,
      scoredCount: payload.scoredCount || rankings.length,
      requestedSymbolCount: payload.requestedSymbolCount || rankings.length,
      failedSymbolCount: payload.failedSymbolCount || 0,
      unscoredSymbolCount: payload.unscoredSymbolCount || 0,
      unscoredSymbols: payload.unscoredSymbols || [],
      technicalTape: payload.technicalTape || null,
      marketRows: payload.marketRows || [],
      sectorRows: payload.sectorRows || [],
      rankings,
      bySymbol: new Map(rankings.map((item) => [item.symbol, item]))
    };
  } catch (error) {
    return {
      status: "missing",
      error: error.message,
      generatedAt: null,
      asOfDate: null,
      expectedAsOfDate: latestExpectedMarketDataDate(),
      model: null,
      scoredCount: 0,
      requestedSymbolCount: 0,
      failedSymbolCount: 0,
      unscoredSymbolCount: 0,
      unscoredSymbols: [],
      technicalTape: null,
      marketRows: [],
      sectorRows: [],
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

async function loadPreviousRefreshStatus() {
  try {
    const text = await readFile(join(root, "data/refresh-status.json"), "utf8");
    return JSON.parse(text);
  } catch {
    return null;
  }
}

async function loadPreviousModelMonitoring() {
  try {
    const text = await readFile(join(root, monitoringOutput), "utf8");
    return JSON.parse(text);
  } catch {
    return { generatedAt: null, history: [] };
  }
}

function publicModelSummary(modelRankings, explainability) {
  return {
    status: modelRankings.status,
    generatedAt: modelRankings.generatedAt,
    asOfDate: modelRankings.asOfDate,
    expectedAsOfDate: modelRankings.expectedAsOfDate,
    isStale: modelRankings.status === "stale",
    model: modelRankings.model,
    scoredCount: modelRankings.scoredCount,
    requestedSymbolCount: modelRankings.requestedSymbolCount,
    failedSymbolCount: modelRankings.failedSymbolCount,
    unscoredSymbolCount: modelRankings.unscoredSymbolCount,
    unscoredSymbols: modelRankings.unscoredSymbols.slice(0, 25),
    technicalTape: modelRankings.technicalTape,
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

function constituentMetadataBySymbol(stocks) {
  return new Map(
    (stocks || []).map((item) => [
      item.symbol,
      {
        name: item.name,
        sector: item.sector,
        industry: item.subIndustry || item.sub_industry || item.industry || null
      }
    ])
  );
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
      .replaceAll("&gt;", ">")
      .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(Number.parseInt(hex, 16)))
      .replace(/&#(\d+);/g, (_, value) => String.fromCodePoint(Number.parseInt(value, 10)));
    if (next === decoded) break;
    decoded = next;
  }
  return decoded;
}

function stripTags(value) {
  return decodeHtml(value.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim());
}

function stripCdata(value) {
  return String(value || "")
    .replace(/^<!\[CDATA\[/, "")
    .replace(/\]\]>$/, "")
    .trim();
}

function xmlText(block, tagNames) {
  const names = Array.isArray(tagNames) ? tagNames : [tagNames];
  for (const name of names) {
    const match = block.match(new RegExp(`<${name}\\b[^>]*>([\\s\\S]*?)<\\/${name}>`, "i"));
    if (match) return stripTags(stripCdata(match[1]));
  }
  return "";
}

function xmlRaw(block, tagNames) {
  const names = Array.isArray(tagNames) ? tagNames : [tagNames];
  for (const name of names) {
    const match = block.match(new RegExp(`<${name}\\b[^>]*>([\\s\\S]*?)<\\/${name}>`, "i"));
    if (match) return stripCdata(match[1]);
  }
  return "";
}

function xmlLink(block) {
  const atomLink = block.match(/<link\b[^>]*href=["']([^"']+)["'][^>]*>/i)?.[1];
  if (atomLink) return decodeHtml(atomLink);
  return xmlText(block, "link");
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

function looksLikeFeed(text) {
  return /<(rss|feed)\b/i.test(text) || /<item\b[\s\S]*<\/item>/i.test(text) || /<entry\b[\s\S]*<\/entry>/i.test(text);
}

function titleFromFeed(feed, fallback) {
  return xmlText(feed, "title") || fallback;
}

function summaryFromFeed(feed, fallback) {
  return xmlText(feed, ["description", "subtitle", "summary"])?.slice(0, 320) || fallback;
}

function extractFeedItems(feed, source) {
  if (!looksLikeFeed(feed)) return [];
  const blocks = [
    ...[...feed.matchAll(/<item\b[\s\S]*?<\/item>/gi)].map((match) => match[0]),
    ...[...feed.matchAll(/<entry\b[\s\S]*?<\/entry>/gi)].map((match) => match[0])
  ];

  return blocks
    .map((block) => {
      let url = xmlLink(block) || source.url;
      try {
        url = new URL(url, source.url).href;
      } catch {
        url = source.url;
      }
      const rawSummary = xmlRaw(block, ["description", "summary", "content:encoded", "content"]);
      const summary = stripTags(decodeHtml(rawSummary || source.notes)).slice(0, 420);
      const excerpt = stripTags(decodeHtml(rawSummary || block)).slice(0, 1800);
      return {
        sourceName: source.name,
        title: xmlText(block, "title").slice(0, 180),
        url,
        publishedAt: normalizeDate(xmlText(block, ["pubDate", "published", "updated", "dc:date"])),
        summary,
        excerpt,
        discoveredFrom: source.url
      };
    })
    .filter((article) => article.title && article.url);
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

function cleanReleaseSentence(value) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .replace(/\s+\(See table[^)]*\)/gi, "")
    .trim();
}

function sentenceMatch(text, regex) {
  return cleanReleaseSentence(text.match(regex)?.[0] || "");
}

function releaseSentenceStarting(text, startRegex, maxLength = 360) {
  const match = text.match(startRegex);
  if (!match || match.index == null) return "";
  const slice = text.slice(match.index, match.index + maxLength);
  const boundary = slice.search(/\.(?=\s+[A-Z(]|$)/);
  return cleanReleaseSentence(boundary >= 0 ? slice.slice(0, boundary + 1) : slice);
}

function releasedPeriodFromText(text, fallback = "") {
  return (
    text.match(/\bTHE EMPLOYMENT SITUATION\s+--\s+([A-Z]+\s+20\d{2})/i)?.[1] ||
    text.match(/\bCONSUMER PRICE INDEX\s+-\s+([A-Z]+\s+20\d{2})/i)?.[1] ||
    text.match(/\bPRODUCER PRICE INDEXES?\s+-\s+([A-Z]+\s+20\d{2})/i)?.[1] ||
    text.match(/\b(Consumer Price Index|Producer Price Index|Personal Income and Outlays|GDP)[^\n.]*?,?\s+((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|First|Second|Third|Fourth|1st|2nd|3rd|4th)[^,.]{0,40}20\d{2})/i)?.[2] ||
    fallback
  );
}

function parseBlsEmploymentSituationRelease(html, event, spec) {
  const text = visibleTextFromHtml(html);
  const title = "Employment Situation";
  const period = releasedPeriodFromText(text, event?.date || "");
  const payrollSentence =
    sentenceMatch(text, /Total nonfarm payroll employment[\s\S]{0,360}?reported today\./i) ||
    releaseSentenceStarting(text, /Total nonfarm payroll employment/i, 360);
  const householdSentence =
    sentenceMatch(text, /The unemployment rate[\s\S]{0,320}?over the year\./i) ||
    releaseSentenceStarting(text, /The unemployment rate/i, 300);
  const participationSentence = releaseSentenceStarting(text, /Both the labor force participation rate/i, 320);
  const partTimeSentence = releaseSentenceStarting(text, /The number of people employed part time for economic reasons/i, 300);
  const earningsSentence =
    sentenceMatch(text, /In April, average hourly earnings[\s\S]{0,360}?Over the year, average hourly earnings have increased by\s+[\d.]+\s+percent\./i) ||
    releaseSentenceStarting(text, /In April, average hourly earnings/i, 260) ||
    releaseSentenceStarting(text, /average hourly earnings/i, 260);
  const workweekSentence = releaseSentenceStarting(text, /The average workweek/i, 220);
  const revisionSentence = sentenceMatch(text, /The change in total nonfarm payroll employment[\s\S]{0,420}?previously reported\./i);
  const industrySentences = [
    sentenceMatch(text, /In April, health care[\s\S]{0,180}?\./i),
    sentenceMatch(text, /Transportation and warehousing employment[\s\S]{0,220}?\./i),
    sentenceMatch(text, /Retail trade added[\s\S]{0,220}?\./i),
    sentenceMatch(text, /Federal government employment[\s\S]{0,220}?\./i),
    sentenceMatch(text, /Employment in information[\s\S]{0,220}?\./i)
  ].filter(Boolean);

  const bullets = [
    payrollSentence,
    householdSentence,
    participationSentence,
    partTimeSentence,
    earningsSentence,
    revisionSentence
  ].filter(Boolean);
  const payrollValue = text.match(/employment edged up by ([\d,]+) in April/i)?.[1] || text.match(/employment increased by ([\d,]+) in/i)?.[1] || null;
  const unemploymentRate = text.match(/unemployment rate was\s+(?:unchanged\s+)?at\s+([\d.]+)\s+percent/i)?.[1] || null;
  const participationRate = text.match(/labor force participation rate,\s+at\s+([\d.]+)\s+percent/i)?.[1] || null;
  const averageHourlyEarningsMoM = text.match(/average hourly earnings[\s\S]{0,80}?rose by [^,]+,\s+or\s+([\d.]+)\s+percent/i)?.[1] || null;
  const averageHourlyEarningsYoY = text.match(/Over the year, average hourly earnings have increased by\s+([\d.]+)\s+percent/i)?.[1] || null;

  const payrollClause = payrollValue ? `Payrolls rose ${payrollValue}` : firstSentence(payrollSentence, 110);
  const unemploymentClause = unemploymentRate ? `unemployment held at ${unemploymentRate}%` : firstSentence(householdSentence, 110);
  const earningsClause = averageHourlyEarningsMoM && averageHourlyEarningsYoY
    ? `wages rose ${averageHourlyEarningsMoM}% month over month and ${averageHourlyEarningsYoY}% year over year`
    : firstSentence(earningsSentence, 130);

  return {
    title,
    event: event.event,
    period,
    sourceName: spec.sourceName,
    sourceUrl: spec.sourceUrl,
    publishedAt: calendarEventReleaseAt(event)?.toISOString() || new Date().toISOString(),
    summary: [payrollSentence, householdSentence, earningsSentence].filter(Boolean).join(" "),
    marketRead: `${payrollClause}; ${unemploymentClause}; ${earningsClause}.`,
    metrics: {
      payrollsChange: payrollValue ? Number(payrollValue.replace(/,/g, "")) : null,
      unemploymentRate: unemploymentRate == null ? null : Number(unemploymentRate),
      laborForceParticipationRate: participationRate == null ? null : Number(participationRate),
      averageHourlyEarningsMoM: averageHourlyEarningsMoM == null ? null : Number(averageHourlyEarningsMoM),
      averageHourlyEarningsYoY: averageHourlyEarningsYoY == null ? null : Number(averageHourlyEarningsYoY)
    },
    bullets,
    industryDetails: industrySentences,
    revisions: revisionSentence,
    themes: ["Macro and growth", "Rates and central banks"],
    importance: "High"
  };
}

function numberFromMatch(text, regex) {
  const value = text.match(regex)?.[1];
  if (value == null) return null;
  const number = Number.parseFloat(String(value).replace(/,/g, ""));
  return Number.isFinite(number) ? number : null;
}

function releaseBodyAfter(text, startRegex) {
  const match = text.match(startRegex);
  if (!match || match.index == null) return text;
  return text.slice(match.index);
}

function parseBlsCpiRelease(html, event, spec) {
  const text = visibleTextFromHtml(html);
  const body = releaseBodyAfter(text, /CONSUMER PRICE INDEX\s+-\s+[A-Z]+\s+20\d{2}/i);
  const title = titleFromHtml(html, "Consumer Price Index Summary");
  const period = releasedPeriodFromText(text, event?.date || "");
  const headlineSentence =
    sentenceMatch(body, /The Consumer Price Index for All Urban Consumers[\s\S]{0,420}?reported today\./i) ||
    releaseSentenceStarting(body, /The Consumer Price Index for All Urban Consumers/i, 420);
  const energySentence = releaseSentenceStarting(body, /The index for energy rose/i, 260);
  const foodSentence = releaseSentenceStarting(body, /The index for food increased/i, 260);
  const coreSentence = releaseSentenceStarting(body, /The index for all items less food and energy rose/i, 360);
  const yearSentence = releaseSentenceStarting(body, /The all items index rose/i, 320);
  const bullets = [headlineSentence, energySentence, foodSentence, coreSentence, yearSentence].filter(Boolean);

  const headlineMoM = numberFromMatch(body, /CPI-U\)\s+increased\s+([\d.]+)\s+percent/i);
  const priorHeadlineMoM = numberFromMatch(body, /after rising\s+([\d.]+)\s+percent\s+in\s+[A-Z][a-z]+/i);
  const headlineYoY = numberFromMatch(body, /all items index increased\s+([\d.]+)\s+percent\s+before seasonal adjustment/i);
  const coreMoM = numberFromMatch(body, /all items less food and energy rose\s+([\d.]+)\s+percent\s+in\s+[A-Z][a-z]+/i);
  const coreYoY = numberFromMatch(body, /all items less food and energy index rose\s+([\d.]+)\s+percent\s+over the year/i);
  const energyMoM = numberFromMatch(body, /The index for energy rose\s+([\d.]+)\s+percent/i);
  const shelterMoM = numberFromMatch(body, /shelter index also increased[\s\S]{0,80}?rising\s+([\d.]+)\s+percent/i);
  const foodMoM = numberFromMatch(body, /The index for food increased\s+([\d.]+)\s+percent/i);

  const marketPieces = [];
  if (headlineMoM != null && headlineYoY != null) {
    marketPieces.push(`CPI-U rose ${headlineMoM}% month over month in ${period || "the latest release"} and ${headlineYoY}% year over year`);
  } else if (headlineSentence) {
    marketPieces.push(firstSentence(headlineSentence, 180));
  }
  if (coreMoM != null || coreYoY != null) {
    marketPieces.push(`core CPI was ${coreMoM ?? "n/a"}% month over month and ${coreYoY ?? "n/a"}% year over year`);
  }
  if (energyMoM != null || shelterMoM != null || foodMoM != null) {
    marketPieces.push(`energy ${energyMoM ?? "n/a"}%, shelter ${shelterMoM ?? "n/a"}%, and food ${foodMoM ?? "n/a"}% were key internals`);
  }

  return {
    title,
    event: event.event,
    period,
    sourceName: spec.sourceName,
    sourceUrl: spec.sourceUrl,
    publishedAt: calendarEventReleaseAt(event)?.toISOString() || new Date().toISOString(),
    summary: bullets.slice(0, 3).join(" "),
    marketRead: `${marketPieces.filter(Boolean).join("; ")}.`,
    metrics: {
      headlineCpiMoM: headlineMoM,
      priorHeadlineCpiMoM: priorHeadlineMoM,
      headlineCpiYoY: headlineYoY,
      coreCpiMoM: coreMoM,
      coreCpiYoY: coreYoY,
      energyMoM,
      shelterMoM,
      foodMoM
    },
    bullets,
    industryDetails: [energySentence, foodSentence, coreSentence].filter(Boolean),
    revisions: null,
    themes: ["Macro and growth", "Rates and central banks", "Energy"],
    importance: "High"
  };
}

function parseBlsPpiRelease(html, event, spec) {
  const text = visibleTextFromHtml(html);
  const body = releaseBodyAfter(text, /PRODUCER PRICE INDEXES?\s+-\s+[A-Z]+\s+20\d{2}/i);
  const title = titleFromHtml(html, "Producer Price Index");
  const period = releasedPeriodFromText(text, event?.date || "");
  const headlineSentence = releaseSentenceStarting(body, /The Producer Price Index for final demand/i, 420);
  const goodsSentence = releaseSentenceStarting(body, /The index for final demand goods/i, 300);
  const servicesSentence = releaseSentenceStarting(body, /Prices for final demand services/i, 300);
  const releaseScheduleSentence = releaseSentenceStarting(body, /The Producer Price Index for/i, 260);
  const bullets = [headlineSentence, goodsSentence, servicesSentence].filter(Boolean);

  const finalDemandMoM = numberFromMatch(body, /final demand (?:increased|advanced|rose)\s+([\d.]+)\s+percent/i);
  const priorFinalDemandMoM = numberFromMatch(body, /Final demand prices moved up\s+([\d.]+)\s+percent\s+in\s+[A-Z][a-z]+/i);
  const finalDemandYoY = numberFromMatch(body, /final demand rose\s+([\d.]+)\s+percent\s+for the 12 months/i);
  const goodsMoM = numberFromMatch(body, /final demand goods (?:advanced|rose|increased)\s+([\d.]+)\s+percent/i);
  const servicesMoM = numberFromMatch(body, /final demand services (?:increased|advanced|rose|decreased|declined|were unchanged)\s+([\d.]+)?\s*percent?/i);
  const marketPieces = [];
  if (finalDemandMoM != null || finalDemandYoY != null) {
    marketPieces.push(`PPI final demand was ${finalDemandMoM ?? "n/a"}% month over month and ${finalDemandYoY ?? "n/a"}% year over year`);
  } else if (headlineSentence) {
    marketPieces.push(firstSentence(headlineSentence, 180));
  }
  if (goodsMoM != null || servicesMoM != null) {
    marketPieces.push(`goods ${goodsMoM ?? "n/a"}% and services ${servicesMoM ?? "n/a"}% drove the mix`);
  }

  return {
    title,
    event: event.event,
    period,
    sourceName: spec.sourceName,
    sourceUrl: spec.sourceUrl,
    publishedAt: calendarEventReleaseAt(event)?.toISOString() || publishedDateFromHtml(html) || new Date().toISOString(),
    summary: bullets.length ? bullets.slice(0, 3).join(" ") : releaseScheduleSentence,
    marketRead: marketPieces.length ? `${marketPieces.join("; ")}.` : firstSentence(releaseScheduleSentence || headlineSentence, 260),
    metrics: {
      finalDemandPpiMoM: finalDemandMoM,
      priorFinalDemandPpiMoM: priorFinalDemandMoM,
      finalDemandPpiYoY: finalDemandYoY,
      goodsMoM,
      servicesMoM
    },
    bullets,
    industryDetails: [goodsSentence, servicesSentence].filter(Boolean),
    revisions: null,
    themes: ["Macro and growth", "Rates and central banks"],
    importance: event.importance || "High"
  };
}

function parseGenericOfficialMacroRelease(html, event, spec) {
  const text = visibleTextFromHtml(html);
  const title = titleFromHtml(html, spec.releaseName);
  const period = releasedPeriodFromText(text, event?.date || "");
  const releaseAnchor = text.search(new RegExp(spec.releaseName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"));
  const body = releaseAnchor >= 0 ? text.slice(releaseAnchor) : text;
  const sentences = (body.match(/[^.!?]+[.!?]/g) || [])
    .map(cleanReleaseSentence)
    .filter((sentence) => sentence.length > 45)
    .filter((sentence) => !/(subscribe|release calendar|technical information|media contact|table of contents)/i.test(sentence))
    .slice(0, 6);
  return {
    title,
    event: event.event,
    period,
    sourceName: spec.sourceName,
    sourceUrl: spec.sourceUrl,
    publishedAt: calendarEventReleaseAt(event)?.toISOString() || publishedDateFromHtml(html) || new Date().toISOString(),
    summary: sentences.slice(0, 3).join(" "),
    marketRead: firstSentence(sentences.join(" "), 260),
    metrics: {},
    bullets: sentences.slice(0, 5),
    industryDetails: [],
    revisions: null,
    themes: classifyMarketThemes(`${title} ${sentences.join(" ")}`),
    importance: event.importance || "High"
  };
}

function officialReleaseSpecForEvent(event) {
  return officialMacroReleaseSpecs.find((spec) => {
    if (spec.source && event.source && spec.source.toLowerCase() !== event.source.toLowerCase()) return false;
    return spec.eventPattern.test(event.event || "");
  });
}

async function fetchOfficialMacroRelease(event, index) {
  const spec = officialReleaseSpecForEvent(event);
  if (!spec) return null;
  try {
    const html = await fetchText(spec.sourceUrl, { timeout: 12000, retries: 2 });
    const release = spec.parser(html, event, spec);
    return {
      id: `O${index + 1}`,
      status: "ready",
      date: event.date,
      releaseTime: event.time,
      releaseAt: calendarEventReleaseAt(event)?.toISOString() || null,
      ageHours: roundedNumber(calendarEventAgeHours(event), 1),
      source: event.source,
      ...release
    };
  } catch (error) {
    return {
      id: `O${index + 1}`,
      status: "error",
      date: event.date,
      releaseTime: event.time,
      releaseAt: calendarEventReleaseAt(event)?.toISOString() || null,
      source: event.source,
      event: event.event,
      sourceName: spec.sourceName,
      sourceUrl: spec.sourceUrl,
      error: error.message,
      marketRead: `${event.event} was due, but the official release could not be fetched: ${error.message}`,
      bullets: [],
      themes: ["Macro and growth"],
      importance: event.importance || "High"
    };
  }
}

async function fetchOfficialMacroReleases(calendarItems, now = new Date()) {
  const dueEvents = (calendarItems || [])
    .filter((event) => officialReleaseSpecForEvent(event))
    .map((event) => ({ event, ageHours: calendarEventAgeHours(event, now) }))
    .filter((item) => item.ageHours != null && item.ageHours >= 0 && item.ageHours <= macroReleaseLookbackHours)
    .sort((a, b) => a.ageHours - b.ageHours)
    .map((item) => item.event);

  const releases = (await mapLimit(dueEvents, 3, fetchOfficialMacroRelease)).filter(Boolean);
  return {
    generatedAt: new Date().toISOString(),
    lookbackHours: macroReleaseLookbackHours,
    releases
  };
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
  const html = await fetchText(candidate.url, { timeout: 9000, retries: 1 });
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

function isUsableSourceArticle(article, maxAgeDays = sourceMaxArticleAgeDays) {
  if (!article?.title || !article?.url || !article?.publishedAt) return false;
  const text = `${article.title} ${article.summary || ""}`;
  if (/(pardon our interruption|access denied|privacy|terms|sign in|login|subscribe)/i.test(text)) return false;
  if (isGenericArticleText(article.title) || isGenericArticleText(article.summary)) return false;
  if (/^(papers|browse research papers|financial market infrastructure supervision|economic news releases|press releases)$/i.test(article.title.trim())) return false;
  const publishedAt = new Date(article.publishedAt).getTime();
  if (!Number.isFinite(publishedAt)) return false;
  if (!Number.isFinite(maxAgeDays) || maxAgeDays <= 0) return true;
  if (Date.now() - publishedAt > maxAgeDays * 24 * 60 * 60 * 1000) return false;
  const themes = classifyMarketThemes(text);
  if (themes.some((theme) => theme !== "Market color")) return true;
  return /\b(market|markets|stock|stocks|bond|bonds|yield|yields|rate|rates|fed|fomc|inflation|cpi|pce|gdp|payroll|jobs|oil|energy|earnings|outlook|commentary|economy|economic|consumer|credit|tariff|trade|semiconductor|ai)\b/i.test(text);
}

function recentSourceArticles(articles, limit = articlesPerSource) {
  return sortArticlesNewestFirst((articles || []).filter((article) => isUsableSourceArticle(article))).slice(0, limit);
}

async function checkSource(source) {
  try {
    const html = await fetchText(source.url, { timeout: 9000, retries: 1 });
    if (looksLikeFeed(html)) {
      const feedArticles = recentSourceArticles(extractFeedItems(html, source), articlesPerSource);
      return {
        ...source,
        title: titleFromFeed(html, source.name),
        summary: feedArticles.length
          ? summaryFromFeed(html, source.notes)
          : `No recent dated articles found in the last ${sourceMaxArticleAgeDays} days.`,
        articles: feedArticles,
        articleCount: feedArticles.length,
        ok: true
      };
    }

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
    const articles = recentSourceArticles([...articlesByUrl.values()], articlesPerSource);
    return {
      ...source,
      title,
      summary: articles.length ? summary : `No recent dated articles found in the last ${sourceMaxArticleAgeDays} days.`,
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
        subIndustry: cells[3],
        type: "stock"
      };
    })
    .filter((item) => item.symbol && item.name);
}

async function loadMarketCapCache() {
  try {
    const text = await readFile(join(root, "data/market-cap-cache.json"), "utf8");
    const payload = JSON.parse(text);
    return {
      generatedAt: payload.generatedAt || null,
      bySymbol: new Map(Object.entries(payload.bySymbol || {}))
    };
  } catch {
    return { generatedAt: null, bySymbol: new Map() };
  }
}

async function writeMarketCapCache(cache) {
  const bySymbol = Object.fromEntries([...cache.bySymbol.entries()].sort(([a], [b]) => a.localeCompare(b)));
  await writeFile(
    join(root, "data/market-cap-cache.json"),
    `${JSON.stringify({ generatedAt: new Date().toISOString(), bySymbol }, null, 2)}\n`
  );
}

async function fetchMarketCap(symbol) {
  const summary = await fetchJson(`https://api.nasdaq.com/api/quote/${encodeURIComponent(symbol)}/summary?assetclass=stocks`, {
    timeout: 8000,
    retries: 1
  });
  const marketCap = marketCapFromSummary(summary, symbol);
  if (!marketCap) throw new Error("market cap unavailable");
  return {
    value: marketCap.value,
    text: marketCap.text,
    sourceName: marketCap.sourceName,
    sourceUrl: marketCap.sourceUrl,
    fetchedAt: new Date().toISOString()
  };
}

function isMarketCapCacheFresh(entry) {
  if (!entry?.fetchedAt || !Number.isFinite(marketCapCacheMaxAgeHours) || marketCapCacheMaxAgeHours <= 0) return false;
  const fetchedAt = new Date(entry.fetchedAt).getTime();
  if (!Number.isFinite(fetchedAt)) return false;
  return Date.now() - fetchedAt < marketCapCacheMaxAgeHours * 60 * 60 * 1000;
}

async function enrichMarketCaps(symbols, existingCache) {
  const cache = {
    generatedAt: existingCache.generatedAt,
    bySymbol: new Map(existingCache.bySymbol)
  };
  const uniqueSymbols = [...new Set(symbols.filter(Boolean))];
  const missingSymbols = uniqueSymbols.filter((symbol) => !cache.bySymbol.has(symbol) || cache.bySymbol.get(symbol)?.text == null);
  const staleExistingSymbols = uniqueSymbols.filter(
    (symbol) => cache.bySymbol.has(symbol) && cache.bySymbol.get(symbol)?.text != null && !isMarketCapCacheFresh(cache.bySymbol.get(symbol))
  );
  const staleSymbols = [...missingSymbols, ...staleExistingSymbols].slice(0, Math.max(0, marketCapFetchLimit));
  await mapLimit(staleSymbols, 4, async (symbol) => {
    try {
      const marketCap = await fetchMarketCap(symbol);
      cache.bySymbol.set(symbol, marketCap);
      await sleep(15);
    } catch {
      if (!cache.bySymbol.has(symbol)) {
        cache.bySymbol.set(symbol, {
          value: null,
          text: null,
          sourceName: "Unavailable",
          sourceUrl: null,
          fetchedAt: null
        });
      }
    }
  });
  return cache;
}

async function fetchYahooScreener(scrId, label, count = 25) {
  const url = `https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=${encodeURIComponent(scrId)}&count=${count}`;
  try {
    const payload = await fetchPublicJson(url, { timeout: 9000, retries: 1 });
    const quotes = payload.finance?.result?.[0]?.quotes || [];
    return {
      status: "ready",
      label,
      scrId,
      rows: quotes.map((quote) => ({
        symbol: quote.symbol,
        name: quote.shortName || quote.longName || quote.displayName || quote.symbol,
        price: finiteNumber(quote.regularMarketPrice),
        changePct: roundedNumber(quote.regularMarketChangePercent, 2),
        change: roundedNumber(quote.regularMarketChange, 2),
        volume: finiteNumber(quote.regularMarketVolume),
        marketCap: finiteNumber(quote.marketCap),
        sourceName: "Yahoo Finance screener",
        sourceUrl: `https://finance.yahoo.com/markets/stocks/${scrId.replaceAll("_", "-").toLowerCase()}/`
      }))
    };
  } catch (error) {
    return { status: "error", label, scrId, error: error.message, rows: [] };
  }
}

async function fetchMarketMovers() {
  const [gainers, losers, mostActive] = await Promise.all([
    fetchYahooScreener("day_gainers", "Day gainers", 30),
    fetchYahooScreener("day_losers", "Day losers", 30),
    fetchYahooScreener("most_actives", "Most active", 30)
  ]);
  return {
    generatedAt: new Date().toISOString(),
    source: "Yahoo Finance predefined screeners",
    gainers: gainers.rows,
    losers: losers.rows,
    mostActive: mostActive.rows,
    status: [gainers, losers, mostActive].some((item) => item.status === "ready") ? "ready" : "error",
    errors: [gainers, losers, mostActive].filter((item) => item.status !== "ready").map((item) => `${item.label}: ${item.error}`)
  };
}

async function fetchNasdaqEarningsCalendar(dateKey) {
  const url = `https://api.nasdaq.com/api/calendar/earnings?date=${dateKey}`;
  try {
    const payload = await fetchJson(url, { timeout: 9000, retries: 1 });
    const rows = (payload.data?.rows || []).map((row) => ({
      date: dateKey,
      symbol: row.symbol,
      name: row.name,
      time: row.time,
      marketCap: row.marketCap,
      fiscalQuarterEnding: row.fiscalQuarterEnding,
      epsForecast: row.epsForecast,
      noOfEsts: row.noOfEsts,
      lastYearReportDate: row.lastYearRptDt,
      lastYearEps: row.lastYearEPS,
      sourceName: "Nasdaq earnings calendar",
      sourceUrl: `https://www.nasdaq.com/market-activity/earnings?date=${dateKey}`
    }));
    return {
      status: "ready",
      date: dateKey,
      asOf: payload.data?.asOf || dateKey,
      rows
    };
  } catch (error) {
    return { status: "error", date: dateKey, error: error.message, rows: [] };
  }
}

function normalizeTickerSymbol(symbol) {
  return String(symbol || "").toUpperCase().replaceAll("-", ".").trim();
}

function mentionKey(symbol) {
  return normalizeTickerSymbol(symbol).replace(/[^A-Z0-9.]/g, "");
}

function extractTickersFromText(text, knownSymbols = new Set()) {
  const clean = String(text || "");
  const tickers = new Set();
  for (const match of clean.matchAll(/\$([A-Z][A-Z0-9.-]{0,7})\b/g)) {
    const symbol = mentionKey(match[1]);
    if (symbol.length >= 1 && symbol.length <= 6 && !tickerStopWords.has(symbol)) tickers.add(symbol);
  }
  for (const match of clean.matchAll(/\b[A-Z]{2,5}(?:\.[A-Z])?\b/g)) {
    const symbol = mentionKey(match[0]);
    if (tickerStopWords.has(symbol)) continue;
    if (knownSymbols.has(symbol)) tickers.add(symbol);
  }
  return [...tickers];
}

async function buildEarningsTape({ sources, marketMovers, knownSymbols }) {
  const pacificToday = dateKeyInTimeZone(new Date(), "America/Los_Angeles");
  const dates = [offsetDateKey(pacificToday, -1), pacificToday, offsetDateKey(pacificToday, 1)];
  const calendars = await mapLimit(dates, 2, fetchNasdaqEarningsCalendar);
  const calendarRows = calendars.flatMap((calendarItem) => calendarItem.rows || []);
  const earningsSymbols = new Set(calendarRows.map((row) => normalizeTickerSymbol(row.symbol)).filter(Boolean));
  const moverRows = [
    ...(marketMovers.gainers || []).map((row) => ({ ...row, moverBucket: "gainer" })),
    ...(marketMovers.losers || []).map((row) => ({ ...row, moverBucket: "loser" })),
    ...(marketMovers.mostActive || []).map((row) => ({ ...row, moverBucket: "most_active" }))
  ];
  const uniqueMoverRows = [...new Map(moverRows.map((row) => [normalizeTickerSymbol(row.symbol), row])).values()];
  const earningsMovers = uniqueMoverRows
    .filter((row) => earningsSymbols.has(normalizeTickerSymbol(row.symbol)))
    .sort((a, b) => Math.abs(Number(b.changePct || 0)) - Math.abs(Number(a.changePct || 0)))
    .slice(0, 12);

  const articleHeadlines = sortArticlesNewestFirst(
    sources
      .flatMap((source) => (source.articles || []).map((article) => ({ ...article, sourceName: article.sourceName || source.name })))
      .filter((article) => /(earnings|results|revenue|guidance|profit|eps|quarter|reports?)/i.test(`${article.title} ${article.summary || ""} ${article.excerpt || ""}`))
  )
    .slice(0, 12)
    .map((article, index) => ({
      id: `E${index + 1}`,
      title: article.title,
      sourceName: article.sourceName,
      url: article.url,
      publishedAt: article.publishedAt,
      summary: article.summary,
      tickers: extractTickersFromText(`${article.title} ${article.summary || ""}`, knownSymbols).slice(0, 8)
    }));

  return {
    status: calendars.some((item) => item.status === "ready") ? "ready" : "error",
    generatedAt: new Date().toISOString(),
    dates,
    calendars: calendars.map(({ date, asOf, status, error, rows }) => ({
      date,
      asOf,
      status,
      error: error || null,
      rowCount: rows?.length || 0,
      rows: (rows || []).slice(0, 40)
    })),
    earningsMovers,
    articleHeadlines,
    sourceNote: "Nasdaq earnings calendar plus Yahoo Finance daily mover screeners and earnings-related article headlines."
  };
}

async function fetchRedditListing(subreddit, sort = "hot", limit = redditPostLimit) {
  const url = `https://www.reddit.com/r/${encodeURIComponent(subreddit)}/${sort}.json?limit=${limit}`;
  const payload = await fetchPublicJson(url, {
    timeout: 12000,
    retries: 1,
    userAgent: "MarketPulse/0.1 personal research dashboard reddit sentiment reader"
  });
  return (payload.data?.children || []).map((child) => child.data).filter(Boolean);
}

async function fetchRedditTape(knownSymbols) {
  if (skipReddit) {
    return {
      status: "skipped",
      generatedAt: new Date().toISOString(),
      sourceNote: "Reddit ingestion skipped by SKIP_REDDIT=1.",
      topTickers: [],
      topPosts: [],
      subreddits: []
    };
  }

  const results = await mapLimit(redditSources, 2, async (source) => {
    try {
      const posts = await fetchRedditListing(source.subreddit, "hot", redditPostLimit);
      return {
        ...source,
        status: "ready",
        posts: posts.map((post) => {
          const title = post.title || "";
          const selftext = post.selftext || "";
          return {
            id: post.id,
            subreddit: source.subreddit,
            segment: source.segment,
            title,
            url: post.url_overridden_by_dest || `https://www.reddit.com${post.permalink}`,
            permalink: `https://www.reddit.com${post.permalink}`,
            createdAt: post.created_utc ? new Date(post.created_utc * 1000).toISOString() : null,
            score: finiteNumber(post.score) || 0,
            comments: finiteNumber(post.num_comments) || 0,
            upvoteRatio: finiteNumber(post.upvote_ratio),
            flair: post.link_flair_text || null,
            tickers: extractTickersFromText(`${title} ${selftext.slice(0, 500)}`, knownSymbols)
          };
        })
      };
    } catch (error) {
      return { ...source, status: "error", error: error.message, posts: [] };
    }
  });

  const posts = results.flatMap((result) => result.posts || []);
  const tickerScores = new Map();
  posts.forEach((post) => {
    post.tickers.forEach((symbol) => {
      const existing = tickerScores.get(symbol) || {
        symbol,
        mentions: 0,
        score: 0,
        comments: 0,
        subreddits: new Set(),
        posts: []
      };
      existing.mentions += 1;
      existing.score += post.score || 0;
      existing.comments += post.comments || 0;
      existing.subreddits.add(post.subreddit);
      existing.posts.push({
        title: post.title,
        subreddit: post.subreddit,
        score: post.score,
        comments: post.comments,
        permalink: post.permalink
      });
      tickerScores.set(symbol, existing);
    });
  });

  const topTickers = [...tickerScores.values()]
    .map((item) => ({
      ...item,
      subreddits: [...item.subreddits],
      posts: item.posts.sort((a, b) => (b.score + b.comments) - (a.score + a.comments)).slice(0, 3),
      attentionScore: item.mentions * 10 + item.score * 0.02 + item.comments * 0.05
    }))
    .sort((a, b) => b.attentionScore - a.attentionScore)
    .slice(0, 15);

  const topPosts = posts
    .sort((a, b) => (b.score + b.comments * 2) - (a.score + a.comments * 2))
    .slice(0, 16);

  return {
    status: results.some((result) => result.status === "ready") ? "ready" : "error",
    generatedAt: new Date().toISOString(),
    sourceNote: "Public Reddit JSON endpoints; use as sentiment and attention only, not verified news.",
    subreddits: results.map(({ subreddit, segment, status, error, posts }) => ({
      subreddit,
      segment,
      status,
      error: error || null,
      postCount: posts?.length || 0
    })),
    topTickers,
    topPosts
  };
}

function buildModelScorebook({ modelRankings, stockMetadata, marketCapCache, signalsBySymbol, marketDataStatus }) {
  const rankings = modelRankings.status === "ready" ? modelRankings.rankings : [];
  const returnStatus = marketDataStatus?.status === "fresh" ? "fresh" : "unavailable";
  const useTechnicalReturns = returnStatus === "fresh";
  const rows = rankings
    .map((item) => {
      const metadata = stockMetadata.get(item.symbol) || {};
      const marketCap = marketCapCache.bySymbol.get(item.symbol) || null;
      const signal = signalsBySymbol.get(item.symbol) || null;
      const showActivation = item.setupType === "model_rebound_watch";
      return {
        symbol: item.symbol,
        name: item.name || metadata.name || item.symbol,
        sector: item.sector || metadata.sector || null,
        industry: metadata.industry || null,
        marketCap: marketCap?.text || null,
        marketCapValue: finiteNumber(marketCap?.value),
        marketCapSource: marketCap?.sourceName || null,
        marketCapFetchedAt: marketCap?.fetchedAt || null,
        modelRank: finiteNumber(item.modelRank),
        modelUniverseCount: finiteNumber(item.modelUniverseCount),
        modelScore: finiteNumber(item.modelScore),
        modelPercentile: roundedNumber(item.modelPercentile, 1),
        setupType: item.setupType || null,
        setupTags: item.setupTags || [],
        reboundActivationPrice: showActivation ? finiteNumber(item.reboundActivationPrice) : null,
        reboundActivationPct: showActivation ? roundedNumber(item.reboundActivationPct, 2) : null,
        reboundActivationWindowDays: showActivation ? finiteNumber(item.reboundActivationWindowDays) : null,
        stopSellPrice: shouldSurfaceStopSell(item) ? optionalNumber(item.stopSellPrice) : null,
        stopSellDistancePct: shouldSurfaceStopSell(item) ? optionalRoundedNumber(item.stopSellDistancePct, 2) : null,
        stopSellRule: shouldSurfaceStopSell(item) ? item.stopSellRule || null : null,
        stopSellBasis: shouldSurfaceStopSell(item) ? item.stopSellBasis || null : null,
        beta60d: useTechnicalReturns ? roundedNumber(signal?.beta60d ?? item.beta60d, 2) : null,
        return7: useTechnicalReturns ? roundedNumber(signal?.return7 ?? item.return7, 2) : null,
        return14: useTechnicalReturns ? roundedNumber(signal?.return14 ?? item.return14, 2) : null,
        return30: useTechnicalReturns ? roundedNumber(signal?.return30 ?? item.return30, 2) : null,
        return60: useTechnicalReturns ? roundedNumber(signal?.return60 ?? item.return60, 2) : null,
        return90: useTechnicalReturns ? roundedNumber(signal?.return90 ?? item.return90, 2) : null,
        ytdReturn: useTechnicalReturns ? roundedNumber(signal?.ytdReturn ?? item.ytdReturn, 2) : null,
        asOfDate: item.asOfDate || modelRankings.asOfDate || null
      };
    })
    .sort((a, b) => Number(b.modelScore ?? -Infinity) - Number(a.modelScore ?? -Infinity));

  return {
    generatedAt: new Date().toISOString(),
    asOfDate: modelRankings.asOfDate || null,
    status: modelRankings.status,
    marketDataStatus: marketDataStatus || null,
    trailingReturnStatus: returnStatus,
    rowCount: rows.length,
    model: modelRankings.model,
    columns: [
      "symbol",
      "name",
      "sector",
      "industry",
      "marketCap",
      "modelScore",
      "modelPercentile",
      "setupType",
      "setupTags",
      "reboundActivationPrice",
      "stopSellPrice",
      "beta60d",
      "return7",
      "return14",
      "return30",
      "return60",
      "return90",
      "ytdReturn"
    ],
    returnNotes: returnStatus === "fresh"
      ? "Return columns use fresh adjusted-close price history and calendar lookbacks: 7D, 14D, 30D, 60D, 90D, then YTD. If the exact calendar date was not a trading day, the prior available trading close is used."
      : "Fresh price history was unavailable; stale cached returns were not reused, so trailing return columns are intentionally blank until the next successful price refresh.",
    rows
  };
}

function monitoringNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function modelDecile(rank, rowCount) {
  const numericRank = monitoringNumber(rank);
  const numericCount = monitoringNumber(rowCount);
  if (!Number.isFinite(numericRank) || !Number.isFinite(numericCount) || numericCount <= 0) return null;
  return Math.max(1, Math.min(10, Math.ceil((numericRank / numericCount) * 10)));
}

function compactMonitoringRow(row) {
  return {
    symbol: row.symbol,
    name: row.name || row.symbol,
    sector: row.sector || null,
    industry: row.industry || null,
    marketCap: row.marketCap || null,
    marketCapValue: monitoringNumber(row.marketCapValue),
    modelRank: monitoringNumber(row.modelRank),
    modelScore: monitoringNumber(row.modelScore),
    modelPercentile: roundedNumber(row.modelPercentile, 1),
    setupType: row.setupType || null,
    setupTags: row.setupTags || [],
    beta60d: monitoringNumber(row.beta60d),
    return7: monitoringNumber(row.return7),
    return14: monitoringNumber(row.return14),
    return30: monitoringNumber(row.return30),
    return60: monitoringNumber(row.return60),
    return90: monitoringNumber(row.return90),
    ytdReturn: monitoringNumber(row.ytdReturn),
    asOfDate: row.asOfDate || null
  };
}

function compactMonitoringHistoryRow(row) {
  return {
    symbol: row.symbol,
    name: row.name || row.symbol,
    sector: row.sector || null,
    industry: row.industry || null,
    modelRank: monitoringNumber(row.modelRank),
    modelScore: monitoringNumber(row.modelScore),
    modelPercentile: roundedNumber(row.modelPercentile, 1),
    setupType: row.setupType || null,
    setupTags: row.setupTags || [],
    modelDecile: monitoringNumber(row.modelDecile),
    asOfDate: row.asOfDate || null
  };
}

function monitoringRankMap(snapshot) {
  return new Map((snapshot?.rows || []).map((row) => [row.symbol, row]));
}

function rankChange(previousRank, currentRank) {
  const previous = monitoringNumber(previousRank);
  const current = monitoringNumber(currentRank);
  if (!Number.isFinite(previous) || !Number.isFinite(current)) return null;
  return previous - current;
}

function topDecileStatus({ row, priorRow, lookbackRow, priorCutoff, hasPriorSnapshot }) {
  if (!priorRow) return hasPriorSnapshot ? "New Coverage" : "Baseline Top Decile";
  const priorRank = monitoringNumber(priorRow.modelRank);
  const currentRank = monitoringNumber(row.modelRank);
  const oneRunChange = rankChange(priorRank, currentRank);
  const lookbackChange = rankChange(lookbackRow?.modelRank, currentRank);
  if (!Number.isFinite(priorRank)) return "New Coverage";
  if (priorRank > priorCutoff) return "New Top-Decile Entrant";
  if (Number.isFinite(lookbackChange) && lookbackChange >= 50) return "Major Rank Upgrade";
  if (Number.isFinite(oneRunChange) && oneRunChange >= 25) return "Rank Upgrade";
  return "Held Top Decile";
}

function buildModelMonitoring({ modelScorebook, previousMonitoring }) {
  const rows = modelScorebook.status === "ready" ? modelScorebook.rows || [] : [];
  const rowCount = rows.length;
  const topDecileCutoff = rowCount ? Math.ceil(rowCount * 0.1) : 0;
  const generatedAt = new Date().toISOString();
  const asOfDate = modelScorebook.asOfDate || null;
  const currentRows = rows
    .map(compactMonitoringRow)
    .map((row) => ({
      ...row,
      modelDecile: modelDecile(row.modelRank, rowCount)
    }))
    .sort((a, b) => Number(a.modelRank || 9999) - Number(b.modelRank || 9999));

  const currentSnapshot = {
    generatedAt,
    asOfDate,
    rowCount,
    topDecileCutoff,
    rows: currentRows.map(compactMonitoringHistoryRow)
  };
  const priorHistory = Array.isArray(previousMonitoring?.history) ? previousMonitoring.history : [];
  const distinctPriorHistory = priorHistory.filter((snapshot) => snapshot?.asOfDate && snapshot.asOfDate !== asOfDate);
  const priorSnapshot = distinctPriorHistory[0] || null;
  const lookbackSnapshot = distinctPriorHistory[4] || distinctPriorHistory[distinctPriorHistory.length - 1] || null;
  const priorRows = monitoringRankMap(priorSnapshot);
  const lookbackRows = monitoringRankMap(lookbackSnapshot);
  const priorCutoff = priorSnapshot?.topDecileCutoff || Math.ceil((priorSnapshot?.rowCount || rowCount || 0) * 0.1);

  const currentTopDecile = currentRows
    .filter((row) => Number.isFinite(Number(row.modelRank)) && Number(row.modelRank) <= topDecileCutoff)
    .map((row) => {
      const priorRow = priorRows.get(row.symbol);
      const lookbackRow = lookbackRows.get(row.symbol);
      const previousRank = monitoringNumber(priorRow?.modelRank);
      const lookbackRank = monitoringNumber(lookbackRow?.modelRank);
      const oneRunRankChange = rankChange(previousRank, row.modelRank);
      const lookbackRankChange = rankChange(lookbackRank, row.modelRank);
      const status = topDecileStatus({ row, priorRow, lookbackRow, priorCutoff, hasPriorSnapshot: Boolean(priorSnapshot) });
      return {
        ...row,
        previousRank,
        previousDecile: modelDecile(previousRank, priorSnapshot?.rowCount || rowCount),
        oneRunRankChange,
        lookbackRank,
        lookbackRankChange,
        status
      };
    });

  const recentEntrants = priorSnapshot
    ? currentTopDecile
        .filter((row) => row.status !== "Held Top Decile")
        .sort((a, b) => {
          const aJump = Number(a.lookbackRankChange ?? a.oneRunRankChange ?? -Infinity);
          const bJump = Number(b.lookbackRankChange ?? b.oneRunRankChange ?? -Infinity);
          if (a.status === "New Top-Decile Entrant" && b.status !== "New Top-Decile Entrant") return -1;
          if (b.status === "New Top-Decile Entrant" && a.status !== "New Top-Decile Entrant") return 1;
          return bJump - aJump;
        })
        .slice(0, 20)
    : [];

  const history = [currentSnapshot, ...priorHistory.filter((snapshot) => snapshot?.asOfDate !== asOfDate)].slice(0, 30);

  return {
    generatedAt,
    asOfDate,
    status: modelScorebook.status,
    source: scorebookOutput,
    model: modelScorebook.model || null,
    rowCount,
    topDecileCutoff,
    topDecileCount: currentTopDecile.length,
    recentEntrantCount: recentEntrants.length,
    trailingReturnStatus: modelScorebook.trailingReturnStatus || "unavailable",
    marketDataStatus: modelScorebook.marketDataStatus || null,
    currentTopDecile,
    recentEntrants,
    history,
    methodology: [
      "Top decile means model rank is inside the best 10% of the current scored S&P 500 reference universe.",
      "A new entrant is a current top-decile name that was outside the top decile in the prior distinct refresh.",
      "Rank upgrades flag names that stayed in the top decile but improved at least 25 rank slots since the prior distinct refresh or 50 rank slots over the available multi-refresh lookback.",
      "The first generated monitoring snapshot is a baseline; entrant tracking begins once at least one prior distinct refresh exists."
    ]
  };
}

function percentChange(now, before) {
  if (!before) return 0;
  return ((now - before) / before) * 100;
}

function formatPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "n/a";
  return `${number >= 0 ? "+" : ""}${number.toFixed(2)}%`;
}

function formatNumber(value, decimals = 1) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(decimals) : "n/a";
}


function modelOnlyOpportunities(modelRankings, stockMetadata = new Map()) {
  if (modelRankings.status !== "ready" || !modelRankings.rankings.length) return [];
  return modelRankings.rankings.map((item) => {
    const metadata = stockMetadata.get(item.symbol) || {};
    const modelPercentile = finiteNumber(item.modelPercentile);
    const showActivation = item.setupType === "model_rebound_watch";
    const modelTags = [
      ...(item.setupTags || []),
      item.modelBucket,
      ...(item.modelReasons || []).slice(0, 2)
    ].filter(Boolean);
    return {
      symbol: item.symbol,
      name: item.name || metadata.name || item.symbol,
      type: "stock",
      sector: item.sector || metadata.sector || "Unclassified",
      score: modelPercentile ?? 0,
      close: finiteNumber(item.close),
      changePct: finiteNumber(item.changePct),
      rsi14: finiteNumber(item.rsi14),
      volumeRatio: finiteNumber(item.volumeRatio),
      return7: finiteNumber(item.return7),
      return14: finiteNumber(item.return14),
      return20: finiteNumber(item.return20),
      return30: finiteNumber(item.return30),
      return60: finiteNumber(item.return60),
      return90: finiteNumber(item.return90),
      ytdReturn: finiteNumber(item.ytdReturn),
      beta60d: finiteNumber(item.beta60d),
      above50: typeof item.above50 === "boolean" ? item.above50 : null,
      above100: typeof item.above100 === "boolean" ? item.above100 : null,
      above200: typeof item.above200 === "boolean" ? item.above200 : null,
      recentCross: false,
      relativeStrength: finiteNumber(item.relativeReturn20VsSpy),
      history: item.history || [],
      tags: [...new Set(modelTags)],
      rulesScore: null,
      modelRank: finiteNumber(item.modelRank),
      modelUniverseCount: finiteNumber(item.modelUniverseCount),
      modelScore: finiteNumber(item.modelScore),
      modelPercentile,
      modelBucket: item.modelBucket || "Ranked",
      modelReasons: item.modelReasons || [],
      riskFlags: item.riskFlags || [],
      setupType: item.setupType || null,
      setupTags: item.setupTags || [],
      reboundActivationPrice: showActivation ? finiteNumber(item.reboundActivationPrice) : null,
      reboundActivationPct: showActivation ? finiteNumber(item.reboundActivationPct) : null,
      reboundActivationVolMultiple: showActivation ? finiteNumber(item.reboundActivationVolMultiple) : null,
      reboundActivationWindowDays: showActivation ? finiteNumber(item.reboundActivationWindowDays) : null,
      reboundActivationRule: showActivation ? item.reboundActivationRule || null : null,
      stopSellPrice: finiteNumber(item.stopSellPrice),
      stopSellDistancePct: finiteNumber(item.stopSellDistancePct),
      stopSellAtr20: finiteNumber(item.stopSellAtr20),
      stopSellRule: item.stopSellRule || null,
      stopSellBasis: item.stopSellBasis || null,
      stopSellComponents: item.stopSellComponents || null,
      modelAsOfDate: item.asOfDate || modelRankings.asOfDate,
      return120: finiteNumber(item.return120),
      relativeReturn60VsSpy: finiteNumber(item.relativeReturn60VsSpy),
      sectorReturn60: finiteNumber(item.sectorReturn60),
      volatility60d: finiteNumber(item.volatility60d),
      volatility60dVsSector: finiteNumber(item.volatility60dVsSector),
      distanceTo52wHigh: finiteNumber(item.distanceTo52wHigh)
    };
  });
}

function hasFreshModelTechnicalTape(modelRankings, expectedAsOfDate) {
  return (
    modelRankings.status === "ready"
    && modelRankings.technicalTape?.status === "ready"
    && modelRankings.technicalTape?.asOfDate
    && modelRankings.technicalTape.asOfDate >= expectedAsOfDate
  );
}

function technicalRowScore(row) {
  let score = 42;
  const return30 = finiteNumber(row.return30 ?? row.change30d);
  const return60 = finiteNumber(row.return60 ?? row.change60d);
  const rsi14 = finiteNumber(row.rsi14);
  if (row.above50) score += 12;
  if (row.above200) score += 14;
  if (return30 != null && return30 > 0) score += Math.min(14, return30);
  if (return60 != null && return60 > 0) score += Math.min(10, return60 / 2);
  if (rsi14 != null && rsi14 >= 50 && rsi14 <= 76) score += 8;
  return Math.max(0, Math.min(100, score));
}

function technicalRowOpportunity(row, etfMetadata = new Map()) {
  const metadata = etfMetadata.get(row.symbol) || {};
  return {
    symbol: row.symbol,
    name: row.name || metadata.name || row.label || row.symbol,
    type: "etf",
    sector: row.sector || metadata.sector || metadata.assetClass || "ETF",
    score: technicalRowScore(row),
    close: finiteNumber(row.close ?? row.price),
    changePct: finiteNumber(row.changePct ?? row.change1d),
    rsi14: finiteNumber(row.rsi14),
    volumeRatio: null,
    return7: finiteNumber(row.return7 ?? row.change5d),
    return14: finiteNumber(row.return14),
    return20: finiteNumber(row.return20 ?? row.change30d),
    return30: finiteNumber(row.return30 ?? row.change30d),
    return60: finiteNumber(row.return60 ?? row.change60d),
    return90: finiteNumber(row.return90 ?? row.change90d),
    ytdReturn: finiteNumber(row.ytdReturn ?? row.ytd),
    beta60d: finiteNumber(row.beta60d),
    above50: row.above50,
    above100: row.above100,
    above200: row.above200,
    recentCross: false,
    relativeStrength: finiteNumber(row.relative30d),
    history: row.history || [],
    tags: [
      row.above50 && row.above200 ? "ETF trend" : "ETF watch",
      Number(row.relative30d) >= 0 ? "Beating SPY" : null
    ].filter(Boolean)
  };
}

function modelTechnicalOpportunities(modelRankings, stockMetadata, etfs) {
  const opportunities = modelOnlyOpportunities(modelRankings, stockMetadata);
  const seen = new Set(opportunities.map((item) => item.symbol));
  const etfMetadata = new Map((etfs || []).map((item) => [item.symbol, item]));
  [...(modelRankings.marketRows || []), ...(modelRankings.sectorRows || [])].forEach((row) => {
    if (!row?.symbol || seen.has(row.symbol)) return;
    opportunities.push(technicalRowOpportunity(row, etfMetadata));
    seen.add(row.symbol);
  });
  return opportunities;
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
  const parts = clean.match(/.*?[.!?](?:\s|$)/g) || [];
  let sentence = "";
  for (const part of parts) {
    sentence = `${sentence} ${part.trim()}`.trim();
    if (sentence.length >= 60 && !/\b(?:U\.S|U\.K|E\.U|Inc|Ltd|Co|Corp)\.$/.test(sentence)) break;
  }
  if (!sentence) sentence = clean;
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

function isGenericArticleText(value) {
  const text = String(value || "").trim();
  return /^(read our latest market commentary|stock market news, commentary|bond market updates|learn about the bond market|public .* research|markets and economy|stock market news|bond market commentary)\b/i.test(text)
    || /\bwhere thought finds leadership\b/i.test(text)
    || /\buncovers powerful insights that move business and society forward\b/i.test(text);
}

function cleanArticleConclusion(value, title = "") {
  let text = cleanDailyReadText(value)
    .replace(/Yes A checkmark with a circle around it close/gi, " ")
    .replace(/\bclose\s+(?=[A-Z])/g, "")
    .replace(/\b(data|war|rally|update)\s+(Over in|Stock futures|Earnings season|Investors|The oil)\b/g, "$1. $2")
    .replace(/\s+/g, " ")
    .trim();
  const cleanTitle = cleanDailyReadText(title);
  if (cleanTitle && text.toLowerCase().startsWith(cleanTitle.toLowerCase())) {
    text = text.slice(cleanTitle.length).trim();
  }
  return text;
}

function excerptConclusion(excerpt, title = "", maxLength = 240) {
  const text = cleanArticleConclusion(excerpt, title);
  if (!text) return "";
  const keyPoints = text.match(/\bKey Points\s+(.{40,700})/i)?.[1];
  if (keyPoints) return firstSentence(keyPoints, maxLength);

  const headlineMatch = text.match(/\b(Headlines Take a Backseat to Fundamentals in Rally.{20,400})/i)?.[1];
  if (headlineMatch) return firstSentence(headlineMatch, maxLength);

  const dateMatch = text.match(/\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},\s+20\d{2}\b/i);
  if (dateMatch) {
    let tail = text.slice(dateMatch.index + dateMatch[0].length).trim();
    tail = tail.replace(/^DJIA:.*?(?=\b(?:Stocks|Stock futures|Treasury|Yields|Oil|Markets|Investors)\b)/i, "");
    tail = tail.replace(/^Article\s+\|\s+/i, "");
    if (tail.length > 40) return firstSentence(tail, maxLength);
  }

  return firstSentence(text, maxLength);
}

function articleConclusion(article, maxLength = 240) {
  const title = article?.title || "";
  const summary = cleanArticleConclusion(article?.summary || "", title);
  const excerpt = cleanArticleConclusion(article?.excerpt || "", title);
  const extracted = excerptConclusion(article?.excerpt || "", title, maxLength);
  const summaryUsable = summary && !isGenericArticleText(summary) && summary.length >= 45;
  const candidates = [summary, excerpt, title]
    .map((item) => cleanArticleConclusion(item, title))
    .filter(Boolean);
  const preferred = summaryUsable ? summary : extracted || candidates.find((item) => !isGenericArticleText(item)) || candidates[0] || title;
  return firstSentence(preferred, maxLength);
}

function deeperReadInterestingness(article) {
  const text = `${article.title || ""} ${article.summary || ""} ${article.excerpt || ""}`;
  const sourceName = String(article.sourceName || "");
  let score = 0;
  score += sourceTrustScore(article.trust) * 6;
  if (/\b(why it matters|bottom line|between the lines|zoom out|big picture|variant|contrarian|surprising|overlooked|underappreciated|second-order|structural|regime|cycle|risk map|trade[- ]off)\b/i.test(text)) score += 18;
  if (/\b(debt to gdp|k-shaped|productivity|supply chain|ai trade|tariff|tariffs|energy crisis|inflation expectations|credit|liquidity|consumer stress|capital spending|capex|hard assets|cultural headwaters|baby boomers|millennials|volatility strategies)\b/i.test(text)) score += 18;
  if (/\b(how|why|could|might|isn't|is not|what to watch|read-through|implication)\b/i.test(article.title || "")) score += 8;
  if (/\b(Axios Macro|Axios Markets|Raymond James|Fidelity|Morgan Stanley|J\\.P\\. Morgan|Bank of America Institute|State Street|Merrill|Schwab)\b/i.test(sourceName)) score += 14;
  if (/\b(daily open|stock market news|market update|top stories|earnings rss|before the bell|opening comment|live updates|stock futures|oil futures rise|asia-pacific markets fall)\b/i.test(`${article.title || ""} ${sourceName}`)) score -= 34;
  if (/\b(CNBC Top News RSS|CNBC Markets|MarketWatch Top Stories RSS)\b/i.test(sourceName)) score -= 18;
  const age = articleAgeHours(article.publishedAt);
  if (age != null) score += Math.max(0, 10 - age / 24);
  return roundedNumber(score, 1);
}

function isDeeperReadAnalyticalCandidate(article) {
  const text = `${article.sourceName || ""} ${article.title || ""} ${article.summary || ""} ${article.excerpt || ""}`;
  if (/\b(daily open|stock market news|market update|top stories|earnings rss|before the bell|opening comment|live updates|stock futures|oil futures rise|asia-pacific markets fall)\b/i.test(text)) return false;
  if (/\b(Axios Macro|Axios Markets|Raymond James|Fidelity|Morgan Stanley|J\\.P\\. Morgan|Bank of America Institute|State Street|Merrill|Schwab)\b/i.test(article.sourceName || "")) return true;
  return /\b(why it matters|bottom line|between the lines|zoom out|big picture|what is|what isn't|how|why|k-shaped|debt to gdp|productivity|supply chain|ai trade|tariff|energy crisis|consumer stress|capital spending|capex|hard assets|cultural headwaters|volatility strategies|underappreciated|overlooked)\b/i.test(text);
}

function buildDeeperReadCandidates(sourceTape, history = {}) {
  const recentSources = new Set((history.lastCards || []).map((card) => card.sourceName).filter(Boolean));
  const recentUrls = new Set((history.lastCards || []).map((card) => card.url).filter(Boolean));
  const maxAgeHours = deeperReadLookbackDays * 24;
  const scored = sourceTape
    .map((article) => {
      const ageHours = articleAgeHours(article.publishedAt);
      return {
        sourceRef: article.id,
        sourceName: article.sourceName,
        title: article.title,
        url: article.url,
        publishedAt: article.publishedAt,
        summary: article.summary,
        themes: article.themes || classifyMarketThemes(`${article.title || ""} ${article.summary || ""} ${article.excerpt || ""}`),
        ageHours: ageHours == null ? null : roundedNumber(ageHours, 1),
        recentlyUsedSource: recentSources.has(article.sourceName),
        recentlyUsedUrl: recentUrls.has(article.url),
        qualityScore: deeperReadInterestingness(article)
      };
    })
    .filter((item) => item.publishedAt && item.ageHours != null && item.ageHours <= maxAgeHours)
    .filter((item) => !/(pardon our interruption|privacy|terms|sign in|login|subscribe)/i.test(`${item.title || ""} ${item.summary || ""}`))
    .filter(isDeeperReadAnalyticalCandidate)
    .sort((a, b) => b.qualityScore - a.qualityScore);

  const bySource = new Map();
  scored.forEach((item) => {
    if (!bySource.has(item.sourceName)) bySource.set(item.sourceName, item);
  });
  const uniqueBySource = [...bySource.values()];
  const rotated = uniqueBySource.filter((item) => !item.recentlyUsedSource && !item.recentlyUsedUrl);
  const selectedPool = rotated.length >= 3 ? rotated : uniqueBySource;
  return selectedPool.slice(0, deeperReadCandidateLimit);
}

function deterministicDeeperRead(candidates = [], reason = "AI Deeper Read was unavailable for this refresh.") {
  const cards = candidates.slice(0, 4).map((candidate) => {
    const summary = articleBrief({ title: candidate.title, summary: candidate.summary }, 260);
    const themes = (candidate.themes || []).slice(0, 2).join(" and ") || "market structure";
    return {
      sourceRef: candidate.sourceRef || null,
      sourceName: candidate.sourceName || "Source",
      title: candidate.title || "Research angle",
      url: candidate.url || null,
      publishedAt: candidate.publishedAt || null,
      thesis: summary || candidate.title || "This source raises a differentiated market question worth reviewing.",
      whyItMatters: `The angle is tied to ${themes}, so it can affect sector leadership, factor rotation, or risk appetite beyond the headline move.`,
      marketReadThrough: (candidate.themes || []).length ? `Primary read-through: ${candidate.themes.slice(0, 3).join(", ")}.` : "Review the source for second-order market read-throughs.",
      variantAngle: "Selected by the deterministic source-quality filter because the AI layer was unavailable.",
      confidence: Number(candidate.qualityScore) >= 40 ? "High" : "Medium"
    };
  });
  return {
    status: cards.length ? "ready" : "thin",
    lookbackDays: deeperReadLookbackDays,
    summary: cards.length
      ? `AI Deeper Read was unavailable, so these are the highest-quality differentiated source angles selected deterministically. ${reason}`
      : `No differentiated source candidates cleared the Deeper Read filter. ${reason}`,
    cards
  };
}

function sourceTrustScore(trust) {
  const value = String(trust || "").toLowerCase();
  if (value.includes("very high")) return 5;
  if (value.includes("high")) return 4;
  if (value.includes("medium high")) return 3.5;
  if (value.includes("medium")) return 3;
  return 2;
}

function articleAgeHours(publishedAt, now = new Date()) {
  const time = new Date(publishedAt || 0).getTime();
  if (!Number.isFinite(time) || time <= 0) return null;
  return (now.getTime() - time) / (60 * 60 * 1000);
}

function classifyMarketThemes(text) {
  const haystack = String(text || "").toLowerCase();
  const themes = [];
  const tests = [
    ["Earnings", /\b(earnings|results|revenue|guidance|profit|eps|quarter|margin|beat|miss)\b/],
    ["Rates and central banks", /\b(fed|fomc|powell|rate|rates|yield|treasury|bond|ecb|lagarde|bank of england|boe|boj|central bank|monetary policy|inflation|cpi|pce)\b/],
    ["Geopolitics and policy", /\b(iran|israel|ukraine|russia|china|taiwan|war|conflict|geopolitical|sanction|tariff|trade|election|congress|white house|hormuz)\b/],
    ["Commodities and energy", /\b(oil|crude|brent|wti|gasoline|opec|energy|natural gas|gold|copper|commodity|commodities)\b/],
    ["Macro and growth", /\b(gdp|payroll|jobs|unemployment|claims|retail sales|consumer|pmi|ism|growth|recession|soft landing|hard landing)\b/],
    ["Credit and liquidity", /\b(credit|spread|spreads|liquidity|funding|default|high yield|investment grade|bank stress|refunding|treasury borrowing)\b/],
    ["AI and semis", /\b(ai|artificial intelligence|semiconductor|semis|chips|gpu|data center|datacenter)\b/]
  ];
  tests.forEach(([theme, regex]) => {
    if (regex.test(haystack)) themes.push(theme);
  });
  return themes.length ? themes : ["Market color"];
}

function articleImportanceScore(article, source, previousRefreshAt, now = new Date()) {
  const text = `${article.title} ${article.summary || ""} ${article.excerpt || ""}`;
  const age = articleAgeHours(article.publishedAt, now);
  const sincePrevious = previousRefreshAt && article.publishedAt && new Date(article.publishedAt) > new Date(previousRefreshAt);
  const themes = classifyMarketThemes(text);
  let score = sourceTrustScore(source.trust) * 8;
  if (sincePrevious) score += 30;
  if (age != null && age <= marketIntelFreshHours) score += 24;
  else if (age != null && age <= marketIntelImportantHours) score += 8;
  if (themes.some((theme) => ["Earnings", "Rates and central banks", "Geopolitics and policy", "Commodities and energy"].includes(theme))) score += 14;
  if (/breaking|daily open|market pulse|stock market today|before the bell|after hours|wall street|yields?|oil|earnings|central bank/i.test(text)) score += 8;
  if (!article.publishedAt) score -= 12;
  if (/(pardon our interruption|privacy|terms|sign in|login|subscribe)/i.test(text)) score -= 40;
  return { score, themes, sincePrevious, ageHours: age == null ? null : roundedNumber(age, 1) };
}

function flattenSourceArticles(sources) {
  return sources.flatMap((source) =>
    (source.articles || []).map((article) => ({
      ...article,
      sourceName: article.sourceName || source.name,
      sourceCategory: source.category,
      trust: source.trust,
      sourceOk: source.ok
    }))
  );
}

function buildProfessionalDrivers({ sources, previousRefreshAt, knownSymbols }) {
  const now = new Date();
  const byUrl = new Map();
  sources.forEach((source) => {
    (source.articles || []).forEach((article) => {
      if (!article.title || !article.url) return;
      const ranked = articleImportanceScore(article, source, previousRefreshAt, now);
      const keepFresh = ranked.sincePrevious || (ranked.ageHours != null && ranked.ageHours <= marketIntelFreshHours);
      const keepImportant = ranked.ageHours != null && ranked.ageHours <= marketIntelImportantHours && ranked.score >= 44;
      if (!keepFresh && !keepImportant) return;
      const existing = byUrl.get(article.url);
      const candidate = {
        id: null,
        title: article.title,
        sourceName: article.sourceName || source.name,
        sourceCategory: source.category,
        trust: source.trust,
        url: article.url,
        publishedAt: article.publishedAt,
        summary: articleConclusion(article, 320),
        excerpt: article.excerpt || "",
        themes: ranked.themes,
        freshness: ranked.sincePrevious ? "Since prior refresh" : ranked.ageHours <= marketIntelFreshHours ? "Last 24 hours" : "Important older item",
        ageHours: ranked.ageHours,
        score: roundedNumber(ranked.score, 1),
        tickers: extractTickersFromText(`${article.title} ${article.summary || ""}`, knownSymbols).slice(0, 8)
      };
      if (!existing || candidate.score > existing.score) byUrl.set(article.url, candidate);
    });
  });

  return [...byUrl.values()]
    .sort((a, b) => b.score - a.score)
    .slice(0, marketIntelArticleLimit)
    .map((item, index) => ({ ...item, id: `M${index + 1}` }));
}

function buildMarketDriverSummary(drivers, earningsTape, redditTape, officialMacroReleases = []) {
  const pieces = [];
  const used = new Set();
  officialMacroReleases
    .filter((release) => release.status === "ready")
    .slice(0, 3)
    .forEach((release) => {
      pieces.push(`Macro release: ${release.marketRead}${release.id ? ` (${release.id})` : ""}`);
    });
  const priorityThemes = ["Geopolitics and policy", "Rates and central banks", "Commodities and energy", "Macro and growth", "Earnings", "AI and semis", "Credit and liquidity"];
  priorityThemes.forEach((theme) => {
    const driver = drivers
      .filter((item) => !used.has(item.id) && (item.themes || []).includes(theme))
      .sort((a, b) => driverThemeFit(b, theme) - driverThemeFit(a, theme))[0];
    if (!driver) return;
    used.add(driver.id);
    pieces.push(`${theme}: ${driver.summary}${driver.id ? ` (${driver.id})` : ""}`);
  });
  if (earningsTape.earningsMovers?.length) {
    pieces.push(`Earnings movers: ${earningsTape.earningsMovers.slice(0, 3).map((mover) => `${mover.symbol} ${formatPercent(mover.changePct)}`).join(", ")} show where post-results dispersion is largest.`);
  }
  if (redditTape.topTickers?.length) {
    pieces.push(`Retail attention: ${redditTape.topTickers.slice(0, 4).map((ticker) => ticker.symbol).join(", ")} are drawing the most Reddit ticker concentration; treat this as sentiment, not verified news.`);
  }
  return pieces.slice(0, 10);
}

function driverThemeFit(driver, theme) {
  const title = String(driver.title || "");
  const summary = String(driver.summary || "");
  const text = `${title} ${summary} ${driver.excerpt || ""}`.toLowerCase();
  const themeTests = {
    "Geopolitics and policy": /\b(iran|israel|ukraine|russia|china|taiwan|war|conflict|hormuz|sanction|tariff|trade|ceasefire)\b/i,
    "Rates and central banks": /\b(yield|treasury|fed|fomc|rate|rates|powell|ecb|boe|boj|inflation|cpi|pce)\b/i,
    "Commodities and energy": /\b(oil|crude|brent|wti|hormuz|energy|natural gas|gold|silver|copper)\b/i,
    "Macro and growth": /\b(gdp|jobs|payroll|claims|unemployment|pmi|ism|growth|consumer|productivity)\b/i,
    Earnings: /\b(earnings|results|revenue|guidance|eps|margin|beat|miss)\b/i,
    "AI and semis": /\b(ai|semiconductor|semis|chip|gpu|data center)\b/i,
    "Credit and liquidity": /\b(credit|spread|liquidity|funding|default|high yield)\b/i
  };
  let score = Number(driver.score) || 0;
  if (themeTests[theme]?.test(text)) score += 20;
  if (themeTests[theme]?.test(`${title} ${summary}`)) score += 35;
  if (!isGenericArticleText(driver.summary)) score += 8;
  if (isGenericArticleText(driver.title)) score -= 10;
  if (isGenericArticleText(driver.summary)) score -= 12;
  if (driver.freshness === "Since prior refresh") score += 6;
  return score;
}

function primaryTheme(themes = []) {
  const priorityThemes = ["Geopolitics and policy", "Rates and central banks", "Commodities and energy", "Macro and growth", "Earnings", "AI and semis", "Credit and liquidity", "Market color"];
  return priorityThemes.find((theme) => themes.includes(theme)) || themes[0] || "Market drivers";
}

function headlineTheme(theme) {
  if (theme === "Geopolitics and policy") return "Geopolitical and policy risk";
  if (theme === "Commodities and energy") return "Energy and commodity risk";
  if (theme === "Rates and central banks") return "Rates and central banks";
  return theme || "Market drivers";
}

function buildMarketIntelligence({ sources, earningsTape, redditTape, marketMovers, previousRefreshStatus, knownSymbols, marketDataStatus, officialMacro }) {
  const previousRefreshAt = previousRefreshStatus?.snapshotGeneratedAt || previousRefreshStatus?.generatedAt || null;
  const drivers = buildProfessionalDrivers({ sources, previousRefreshAt, knownSymbols });
  const officialMacroReleases = officialMacro?.releases || [];
  const themeCounts = new Map();
  drivers.forEach((driver) => driver.themes.forEach((theme) => themeCounts.set(theme, (themeCounts.get(theme) || 0) + 1)));
  officialMacroReleases.forEach((release) => (release.themes || []).forEach((theme) => themeCounts.set(theme, (themeCounts.get(theme) || 0) + 1)));
  const topThemes = [...themeCounts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map(([theme, count]) => ({ theme, count }));
  const sourceHealth = {
    checked: sources.length,
    live: sources.filter((source) => source.ok).length,
    blockedOrFailed: sources.filter((source) => !source.ok).map((source) => ({ name: source.name, reason: source.summary }))
  };

  return {
    generatedAt: new Date().toISOString(),
    window: {
      mode: "rolling_24h_with_important_older_context",
      freshHours: marketIntelFreshHours,
      importantLookbackHours: marketIntelImportantHours,
      previousRefreshAt
    },
    marketDataStatus,
    officialMacro: {
      generatedAt: officialMacro?.generatedAt || null,
      lookbackHours: officialMacro?.lookbackHours || macroReleaseLookbackHours,
      releases: officialMacroReleases
    },
    sourceHealth,
    professionalDrivers: drivers.slice(0, 18),
    topThemes,
    earnings: earningsTape,
    reddit: redditTape,
    marketMovers: {
      source: marketMovers.source,
      status: marketMovers.status,
      gainers: (marketMovers.gainers || []).slice(0, 12),
      losers: (marketMovers.losers || []).slice(0, 12),
      mostActive: (marketMovers.mostActive || []).slice(0, 12),
      errors: marketMovers.errors || []
    },
    briefingBullets: buildMarketDriverSummary(drivers, earningsTape, redditTape, officialMacroReleases)
  };
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
    .replace(/^\s*[-•]\s+/, "")
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

function removeAiStopMetricText(value) {
  const text = cleanMemoText(value);
  if (!text) return "";
  return text
    .replace(/\b(?:if\s+)?(?:the\s+)?close\s+(?:fails|falls|breaks|closes)\s+(?:below|under)\s+(?:the\s+)?stop(?:-loss)?(?:\s+(?:level|price))?\s*\$?\d+(?:\.\d+)?\b[^.;]*/gi, "if price loses trend confirmation")
    .replace(/\bstop(?:-loss)?(?:\s+(?:level|price))?\s*(?:of|at|near|below|under|:)?\s*\$?\d+(?:\.\d+)?\b/gi, "risk-control trigger")
    .replace(/\b(?:below|under)\s+(?:the\s+)?stop(?:-loss)?(?:\s+(?:level|price))?\b/gi, "below trend confirmation")
    .replace(/\bstop(?:-loss)?(?:\s+(?:level|price))?\b/gi, "risk-control trigger")
    .replace(/\s+/g, " ")
    .trim();
}

function sanitizeAiRecommendationText(field, value) {
  const text = removeAiStopMetricText(value);
  if (!text) return text;
  if (field === "invalidation" && /risk-control trigger/i.test(text)) {
    return "Downgrade if price loses trend confirmation, volume deteriorates, or the model rank falls materially.";
  }
  return text;
}

function sanitizeAiRecommendation(recommendation) {
  const cleaned = { ...recommendation };
  [
    "setup",
    "whyNow",
    "rationale",
    "companyOverview",
    "earningsContext",
    "recentNews",
    "macroLink",
    "macroEvidence",
    "modelEvidence",
    "technicalEvidence",
    "momentumEvidence",
    "risk",
    "invalidation"
  ].forEach((field) => {
    if (typeof cleaned[field] === "string") cleaned[field] = sanitizeAiRecommendationText(field, cleaned[field]);
  });
  return cleaned;
}

function usableDailyReadItem(value) {
  return !/(no names lack|no data gaps|all data complete|no .* unavailable)/i.test(value);
}

function dailyReadPassesFactGuardrails(dailyRead) {
  const text = [
    dailyRead?.headline,
    dailyRead?.body,
    ...(dailyRead?.keyTakeaways || []),
    ...(dailyRead?.watchItems || [])
  ]
    .join(" ")
    .trim();
  if (!text) return false;
  if (/forward return|SHAP|cross-asset liquidity|sector monolith|risk-on bid|свеж/i.test(text)) return false;
  if (/[\u0400-\u04FF]/.test(text)) return false;
  return true;
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

function buildNote({ opportunities, macro, calendar, sources, model, sectorPerformance, deskRecommendations, aiRecommendations, marketIntelligence }) {
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
    ? modelLeaders.filter((item) => item.setupType === "momentum_confirmed").length
    : opportunities.filter((item) => item.score >= 72 && item.above50 && item.above200 && item.rsi14 <= 76).length;
  const universeCount = opportunities.length || 1;
  const breadth = Math.round((broadTrend / universeCount) * 100);
  const above50Pct = Math.round((above50 / universeCount) * 100);
  const above200Pct = Math.round((above200 / universeCount) * 100);
  const sourceHits = sources.filter((source) => source.ok).length;
  const failedSources = sources.length - sourceHits;
  const articleHits = sources.reduce((total, source) => total + (source.articles?.length || 0), 0);
  const upcomingEvents = (calendar || []).slice(0, 3);
  const leaderText = leaders.map((item) => item.symbol).join(", ") || "none";
  const etfText = etfLeaders.map((item) => item.symbol).join(", ") || "none";
  const topSector = sectorPerformance?.[0];
  const modelSectorText = dominantModelSectorText(modelLeaders);
  const sourceBriefs = articleBriefs(sources, 3);
  const marketBullets = marketIntelligence?.briefingBullets || [];
  const topDrivers = marketIntelligence?.professionalDrivers || [];
  const officialMacroReleases = marketIntelligence?.officialMacro?.releases || [];
  const latestOfficialMacro = officialMacroReleases.find((release) => release.status === "ready");
  const earningsMovers = marketIntelligence?.earnings?.earningsMovers || [];
  const redditTickers = marketIntelligence?.reddit?.topTickers || [];
  const technicalsFresh = marketIntelligence?.marketDataStatus?.status === "fresh";
  const technicalStatusMessage = marketIntelligence?.marketDataStatus?.message || "Fresh price and technical data is unavailable.";
  const aiFocus = aiRecommendations?.status === "ready"
    ? firstSentence(aiRecommendations.headline || aiRecommendations.macroView)
    : "";
  const aiSymbols = (aiRecommendations?.recommendations || []).slice(0, 4).map((item) => item.symbol).filter(Boolean).join(", ");
  const modelText = modelReady
    ? `${model.scoredCount} S&P 500 names were scored by the XGBoost rank model as of ${model.asOfDate || "the latest available close"}`
    : "The XGBoost rank model was not available for this refresh";
  const regime = !technicalsFresh
    ? "Fresh technical data is unavailable"
    : breadth >= 55
      ? "Risk appetite is broad"
      : breadth >= 40
        ? "Risk appetite is constructive but selective"
        : "Risk appetite is narrow";
  const fallbackHeadline = !technicalsFresh
    ? "Fresh technical data is unavailable; use macro, news, and model context with caution."
    : modelReady
    ? `${regime}; model leadership is clustered in ${leaderText}, with ${topSector?.sector || "sector"} confirmation.`
    : `${regime}; model-ranked momentum leaders are unavailable.`;
  const intelligenceHeadline = latestOfficialMacro
    ? `${latestOfficialMacro.title || latestOfficialMacro.event} frames today's market read.`
    : topDrivers.length
      ? `${headlineTheme(primaryTheme(topDrivers[0].themes))} frames today's market read.`
      : fallbackHeadline;
  const macroReleaseLead = latestOfficialMacro
    ? `The primary macro release is ${latestOfficialMacro.title || latestOfficialMacro.event}: ${latestOfficialMacro.marketRead}`
    : "";
  const driverLead = macroReleaseLead
    ? macroReleaseLead
    : topDrivers.length
      ? `The most relevant market drivers are ${[...new Set(topDrivers.slice(0, 5).map((driver) => headlineTheme(primaryTheme(driver.themes)).toLowerCase()))].slice(0, 3).join(", ")}.`
      : "The current market-driver read is thin because few configured sources yielded current dated articles.";
  const earningsLead = earningsMovers.length
    ? `Earnings dispersion is visible in ${earningsMovers.slice(0, 3).map((item) => `${item.symbol} ${formatPercent(item.changePct)}`).join(", ")}.`
    : "No large Yahoo mover matched the Nasdaq earnings calendar in this refresh.";
  const redditLead = redditTickers.length
    ? `Retail attention is concentrated in ${redditTickers.slice(0, 4).map((item) => item.symbol).join(", ")}; treat that as sentiment, not verified news.`
    : "Reddit attention data was unavailable or did not produce clean ticker concentration.";
  const fallbackBody = !technicalsFresh
    ? `${driverLead} ${earningsLead} ${redditLead} ${technicalStatusMessage} ${modelReady ? `The model file reports ${model.scoredCount} scored names as of ${model.asOfDate || "the latest available close"}, but price-derived fields should not be treated as current until the model scorer restores the technical tape.` : "Model-ranked single-name context is unavailable, so do not rely on stale ranks."}`
    : `${regime}. ${driverLead} ${earningsLead} ${redditLead} The model read is secondary confirmation, with leadership concentrated in ${leaderText}.`;
  const macroReleaseBullets = officialMacroReleases
    .filter((release) => release.status === "ready")
    .slice(0, 3)
    .map((release) => `Macro release: ${release.marketRead}${release.id ? ` (${release.id})` : ""}`);
  const earningsBullet = marketBullets.find((item) => item.startsWith("Earnings movers:"));
  const retailBullet = marketBullets.find((item) => item.startsWith("Retail attention:"));
  const driverBullets = marketBullets
    .filter((item) => !item.startsWith("Macro release:") && !item.startsWith("Earnings movers:") && !item.startsWith("Retail attention:"))
    .slice(0, 2);
  const fallbackChanged = [
    ...macroReleaseBullets,
    ...driverBullets,
    earningsBullet,
    retailBullet,
    technicalsFresh
      ? `Breadth: ${above50Pct}% of the screened universe is above the 50-day average, ${above200Pct}% is above the 200-day, and ${breadth}% clears both trend lines.`
      : `Data freshness: ${technicalStatusMessage}`,
    modelReady && technicalsFresh
      ? `Model read: ${leaderText} lead ${model.scoredCount} scored S&P 500 names; ${modelSectorText}; sector confirmation is ${topSectorText(sectorPerformance)}.`
      : modelReady
        ? `Model read: ${model.scoredCount} names were scored as of ${model.asOfDate || "the latest available close"}, but technical tape is unavailable so leadership and sector confirmation are suppressed.`
      : "Model rankings were unavailable, so no single-name model read was used.",
    deskRecommendations?.length
      ? `Desk call summary: ${deskRecommendations.slice(0, 4).map((item) => `${item.symbol} (${item.label})`).join(", ")}.`
      : "Desk call summary was unavailable in this snapshot.",
    aiFocus ? `AI memo: ${aiFocus}` : "AI memo was unavailable, so this read is based on deterministic model, macro, sector, and source data.",
    sourceBriefs.length ? `Research tape: ${sourceBriefs.join(" | ")}.` : "Research tape: no high-quality article briefs were extracted from the configured source pages."
  ];
  const fallbackWatch = [
    upcomingEvents.length ? `Macro risk: ${upcomingEvents.map((event) => `${event.event} on ${event.date}`).join("; ")}.` : "No upcoming macro events are currently listed in the local calendar.",
    modelReady && technicalsFresh
      ? `Momentum risk: ${extended} top-decile model names have RSI above 76; chase risk is highest where model rank is strong but volume/trend confirmation is weak.`
      : "Momentum risk: fresh technical tape is unavailable, so RSI/chase-risk counts are suppressed.",
    technicalsFresh
      ? `Confirmation check: ETF leaders are ${etfText}; if they roll over while single-name ranks stay high, reduce confidence in the long book.`
      : "Data check: rerun the Python model scorer before relying on trailing returns, sector tiles, breadth, or moving-average status.",
    `${failedSources} of ${sources.length} configured source pages failed the latest check; blocked or stale sources should not drive the call.`
  ];
  const cleanedAiDailyRead = process.env.AI_DAILY_READ === "1" && aiRecommendations?.status === "ready" ? cleanDailyRead(aiRecommendations.dailyRead) : null;
  const aiDailyRead = process.env.AI_DAILY_READ === "1" && dailyReadPassesFactGuardrails(cleanedAiDailyRead) ? cleanedAiDailyRead : null;

  return {
    headline: aiDailyRead?.headline || intelligenceHeadline,
    body: aiDailyRead?.body || fallbackBody,
    changed: boundedList([...(aiDailyRead?.keyTakeaways || []).slice(0, 4), ...fallbackChanged], fallbackChanged, 6),
    watch: boundedList([...(aiDailyRead?.watchItems || []).slice(0, 3), ...fallbackWatch], fallbackWatch, 5),
    generatedBy: aiDailyRead ? "ai_with_fact_guardrails" : cleanedAiDailyRead ? "deterministic_ai_daily_read_rejected" : "deterministic"
  };
}

function buildRecommendations(opportunities) {
  const modelCandidates = opportunities.filter(hasModelRank);
  if (modelCandidates.length) {
    const cleanModelCandidates = modelCandidates.filter((item) => item.setupType === "momentum_confirmed");
    const watch = modelCandidates.find((item) => item.modelRank <= Math.ceil(modelCandidates.length * 0.2) && item.setupType !== "momentum_confirmed");
    const calls = cleanModelCandidates.slice(0, 4).map((item) => ({
      label: item.modelBucket || "Model Ranked",
      symbol: item.symbol,
      title: `Rank #${item.modelRank} of ${item.modelUniverseCount}; ${formatNumber(item.modelPercentile)} percentile`,
      rationale: `${item.symbol}: model score ${formatNumber(item.modelScore, 3)}; rules score ${formatNumber(item.rulesScore)}; ${item.above50 && item.above200 ? "above 50-day and 200-day averages" : "mixed trend alignment"}; 60-day relative return vs SPY ${formatPercent(item.relativeReturn60VsSpy)}; evidence: ${(item.modelReasons || []).join("; ") || "model rank and technical inputs"}.`,
    }));

    if (watch) {
      const activation = watch.reboundActivationPrice
        ? `; rebound activation requires a close above $${Number(watch.reboundActivationPrice).toFixed(2)} within ${watch.reboundActivationWindowDays || 5} trading days`
        : "";
      calls.push({
        label: watch.setupType === "model_rebound_watch" ? "Model Rebound Watch" : "Model Risk Check",
        symbol: watch.symbol,
        title: `Rank #${watch.modelRank}; ${watch.riskFlags?.[0] || "review setup"}`,
        rationale: `${watch.symbol}: still ranks highly, but it is not momentum-confirmed. Flags include ${(watch.riskFlags || []).join("; ") || "trend or RSI review"}; RSI ${Math.round(watch.rsi14)}; rules score ${formatNumber(watch.rulesScore)}${activation}.`
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
    macroView: "The dashboard can still show model-ranked recommendations and deterministic desk context. Add OPENAI_API_KEY to .env and rerun the refresh to generate AI recommendations that connect macro context, public source summaries, and the XGBoost single-name rank model.",
    dailyRead: null,
    deeperRead: {
      status: "unavailable",
      lookbackDays: deeperReadLookbackDays,
      summary: "Deeper Read is generated by the AI layer when OPENAI_API_KEY is configured.",
      cards: []
    },
    recommendations: [],
    portfolioNotes: [
      "Model-ranked screening and deterministic dashboard context remain available without an API key.",
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

function deterministicRecommendationFallback(modelCandidates = [], deskRecommendations = [], marketIntelligence = null) {
  const deskCallBySymbol = new Map((deskRecommendations || []).map((item) => [item.symbol, item]));
  const driver = (marketIntelligence?.briefingBullets || [])[0] || (marketIntelligence?.professionalDrivers || [])[0]?.summary || "Current macro and source context is available in the Daily Read.";
  const candidates = modelCandidates.length
    ? modelCandidates.slice(0, 4)
    : (deskRecommendations || []).slice(0, 4).map((item) => ({ symbol: item.symbol, name: item.symbol, score: null, modelRank: null, modelPercentile: null, modelReasons: [], riskFlags: [], setupTags: [] }));
  return candidates.map((candidate) => {
    const deskCall = deskCallBySymbol.get(candidate.symbol);
    const setupTags = candidate.setupTags || [];
    return {
      symbol: candidate.symbol,
      action: candidate.setupType === "model_rebound_watch" ? "Watch for activation" : "Research long setup",
      conviction: candidate.setupType === "momentum_confirmed" ? "Medium" : "Review",
      setup: deskCall?.rationale || `${candidate.symbol} ranks highly in the model book and should be reviewed against the macro tape before any trade decision.`,
      whyNow: firstSentence(driver, 220),
      rationale: deskCall?.rationale || `${candidate.symbol} is a top model candidate with ${formatPercent(candidate.relativeReturn60VsSpy)} 60-day relative return versus SPY.`,
      companyOverview: "",
      marketCap: candidate.marketCap || "",
      earningsContext: "",
      recentNews: "No AI company-news synthesis was available during this refresh.",
      macroLink: firstSentence(driver, 220),
      macroEvidence: firstSentence(driver, 220),
      modelEvidence: `Rank ${candidate.modelRank || "n/a"} of ${candidate.modelUniverseCount || "the scored universe"}; percentile ${formatNumber(candidate.modelPercentile)}; reasons: ${(candidate.modelReasons || []).join("; ") || "model score and technical inputs"}.`,
      technicalEvidence: `Close ${formatNumber(candidate.close, 2)}; 7D ${formatPercent(candidate.return7)}; 30D ${formatPercent(candidate.return30)}; 60D vs SPY ${formatPercent(candidate.relativeReturn60VsSpy)}; RSI ${formatNumber(candidate.rsi14, 0)}.`,
      momentumEvidence: `${setupTags.length ? setupTags.join("; ") : "Review trend confirmation"}; risk flags: ${(candidate.riskFlags || []).join("; ") || "none supplied"}.`,
      risk: (candidate.riskFlags || []).join("; ") || "Requires confirmation from source tape and risk controls.",
      invalidation: "Downgrade if the name loses trend confirmation, the model rank falls materially, or the macro/source backdrop turns against the setup.",
      sourceRefs: []
    };
  });
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

async function loadDeeperReadHistory() {
  try {
    return JSON.parse(await readFile(join(root, deeperReadHistoryPath), "utf8"));
  } catch {
    return { generatedAt: null, lastCards: [], runs: [] };
  }
}

async function writeDeeperReadHistory(deeperRead, previousHistory = {}) {
  const cards = (deeperRead?.cards || []).filter((card) => card.sourceRef || card.url || card.sourceName);
  if (!cards.length) return;
  await mkdir(join(root, "data"), { recursive: true });
  const run = {
    generatedAt: new Date().toISOString(),
    cards: cards.map((card) => ({
      sourceRef: card.sourceRef || null,
      sourceName: card.sourceName || null,
      title: card.title || null,
      url: card.url || null
    }))
  };
  const runs = [run, ...(previousHistory.runs || [])].slice(0, 12);
  await writeFile(
    join(root, deeperReadHistoryPath),
    `${JSON.stringify({ generatedAt: run.generatedAt, lastCards: run.cards, runs }, null, 2)}\n`
  );
}

function compactCandidate(item) {
  const showActivation = item.setupType === "model_rebound_watch";
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
    setupType: item.setupType || null,
    setupTags: item.setupTags || [],
    reboundActivationPrice: showActivation ? roundedNumber(item.reboundActivationPrice, 2) : null,
    reboundActivationPct: showActivation ? roundedNumber(item.reboundActivationPct, 2) : null,
    reboundActivationVolMultiple: showActivation ? roundedNumber(item.reboundActivationVolMultiple, 2) : null,
    reboundActivationWindowDays: showActivation ? finiteNumber(item.reboundActivationWindowDays) : null,
    reboundActivationRule: showActivation ? item.reboundActivationRule || null : null,
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

function normalizeDeeperReadPayload(deeperRead, sourceTape = [], validSourceRefs = []) {
  const validRefSet = new Set(validSourceRefs);
  const sourceRefsById = new Map(sourceTape.map((source) => [source.id, source]));
  const deeperCards = (deeperRead?.cards || [])
    .map((card) => {
      const source = sourceRefsById.get(card.sourceRef);
      const sourceName = source?.sourceName || card.sourceName || "";
      return {
        ...card,
        sourceName,
        title: source?.title || card.title || "",
        url: source?.url || card.url || "",
        publishedAt: source?.publishedAt || card.publishedAt || null,
        thesis: cleanMemoText(card.thesis),
        whyItMatters: cleanMemoText(card.whyItMatters),
        marketReadThrough: cleanMemoText(card.marketReadThrough),
        variantAngle: cleanMemoText(card.variantAngle),
        sourceRefs: card.sourceRef ? [card.sourceRef] : []
      };
    })
    .filter((card) => card.sourceRef && validRefSet.has(card.sourceRef));
  const seenSources = new Set();
  const uniqueDeeperCards = [];
  deeperCards.forEach((card) => {
    const key = card.sourceName || card.sourceRef;
    if (seenSources.has(key)) return;
    seenSources.add(key);
    uniqueDeeperCards.push(card);
  });
  return {
    status: uniqueDeeperCards.length ? deeperRead?.status || "ready" : "thin",
    lookbackDays: deeperReadLookbackDays,
    summary: cleanMemoText(deeperRead?.summary) || "No differentiated source analysis cleared the quality filter in this refresh.",
    cards: uniqueDeeperCards.slice(0, 5)
  };
}

function normalizeAiRecommendationContext(parsed, companyContexts, validSourceRefs, sourceTape = []) {
  const bySymbol = new Map(companyContexts.map((context) => [context.symbol, context]));
  const validRefSet = new Set(validSourceRefs);
  const recommendations = (parsed.recommendations || []).map((recommendation) => {
    const context = bySymbol.get(recommendation.symbol);
    if (!context) return sanitizeAiRecommendation(recommendation);

    const sourceRefs = [
      ...(recommendation.sourceRefs || []),
      context.investorRelations?.id,
      context.marketCap?.sourceId,
      context.earnings?.sourceId
    ].filter((ref, index, refs) => ref && validRefSet.has(ref) && refs.indexOf(ref) === index);

    return sanitizeAiRecommendation({
      ...recommendation,
      companyOverview: companyOverviewSummary(context) || recommendation.companyOverview || "",
      marketCap: context.marketCap?.text || recommendation.marketCap || "",
      earningsContext: context.earnings?.summary || recommendation.earningsContext || "",
      sourceRefs
    });
  });
  const deeperRead = normalizeDeeperReadPayload(parsed.deeperRead, sourceTape, validSourceRefs);
  return { ...parsed, deeperRead, recommendations };
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
    const html = await fetchText(base.href, { timeout: 7000, retries: 1 });
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
    const html = await fetchText(investorUrl, { timeout: 7000, retries: 1 });
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
    fetchJson(`https://api.nasdaq.com/api/company/${encodeURIComponent(symbol)}/company-profile`, { timeout: 8000, retries: 1 }).catch(() => null),
    fetchJson(`https://api.nasdaq.com/api/company/${encodeURIComponent(symbol)}/earnings-surprise`, { timeout: 8000, retries: 1 }).catch(() => null),
    fetchJson(`https://api.nasdaq.com/api/quote/${encodeURIComponent(symbol)}/eps?assetclass=stocks`, { timeout: 8000, retries: 1 }).catch(() => null),
    fetchJson(`https://api.nasdaq.com/api/quote/${encodeURIComponent(symbol)}/summary?assetclass=stocks`, { timeout: 8000, retries: 1 }).catch(() => null)
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
    const xml = await fetchText(`https://feeds.finance.yahoo.com/rss/2.0/headline?s=${encodeURIComponent(symbol)}&region=US&lang=en-US`, { timeout: 8000, retries: 1 });
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

async function buildAiDeeperRead({ apiKey, aiModel, candidates, sourceTape, sourceRefIds, history }) {
  if (!apiKey || !candidates.length) {
    return deterministicDeeperRead(candidates, !apiKey ? "OPENAI_API_KEY was not configured." : "No differentiated source candidates were available.");
  }

  const candidateRefs = candidates.map((candidate) => candidate.sourceRef).filter(Boolean);
  const requestBody = {
    model: aiModel,
    instructions: [
      "You write the Deeper Read section for a hedge-fund-style market dashboard.",
      "Use only the supplied article candidates from the last 7 days.",
      "Do not mention the stock model, XGBoost, model ranks, or momentum leaders. This section is source analysis only.",
      "Choose the most differentiated analytical angles, not generic recaps of stocks, oil, or futures moving.",
      "Avoid repeating sources marked recentlyUsedSource unless there are too few good alternatives.",
      "Return concise but thoughtful cards with thesis, why it matters, market read-through, and the variant angle."
    ].join(" "),
    input: [
      {
        role: "user",
        content: [
          {
            type: "input_text",
            text: `Create Deeper Read from these candidates only:\n${JSON.stringify({
              lookbackDays: deeperReadLookbackDays,
              previousRefreshSources: (history.lastCards || []).map((card) => card.sourceName).filter(Boolean),
              candidates
            }, null, 2)}`
          }
        ]
      }
    ],
    text: {
      format: {
        type: "json_schema",
        name: "deeper_read",
        strict: true,
        schema: {
          type: "object",
          additionalProperties: false,
          required: ["status", "lookbackDays", "summary", "cards"],
          properties: {
            status: { type: "string", enum: ["ready", "thin", "unavailable"] },
            lookbackDays: { type: "number" },
            summary: { type: "string" },
            cards: {
              type: "array",
              minItems: 0,
              maxItems: 5,
              items: {
                type: "object",
                additionalProperties: false,
                required: ["sourceRef", "sourceName", "title", "thesis", "whyItMatters", "marketReadThrough", "variantAngle", "confidence"],
                properties: {
                  sourceRef: candidateRefs.length ? { type: "string", enum: candidateRefs } : { type: "string" },
                  sourceName: { type: "string" },
                  title: { type: "string" },
                  thesis: { type: "string" },
                  whyItMatters: { type: "string" },
                  marketReadThrough: { type: "string" },
                  variantAngle: { type: "string" },
                  confidence: { type: "string", enum: ["High", "Medium", "Low"] }
                }
              }
            }
          }
        }
      }
    },
    reasoning: { effort: "minimal" },
    max_output_tokens: 2500
  };

  try {
    console.log(`Calling OpenAI ${aiModel} for Deeper Read...`);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), openAiTimeoutMs);
    const response = await fetch("https://api.openai.com/v1/responses", {
      method: "POST",
      signal: controller.signal,
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${apiKey}`
      },
      body: JSON.stringify(requestBody)
    });
    try {
      const json = await response.json();
      if (!response.ok) throw new Error(json.error?.message || `${response.status} ${response.statusText}`);
      const usage = estimateOpenAiCost(aiModel, json.usage);
      const text = responseText(json);
      if (!text) throw new Error("OpenAI response did not include Deeper Read text.");
      await appendUsageLog({
        generatedAt: new Date().toISOString(),
        status: "deeper_read_ready",
        model: aiModel,
        usage
      });
      const parsed = normalizeDeeperReadPayload(JSON.parse(text), sourceTape, sourceRefIds);
      console.log(`Deeper Read generated with ${parsed.cards.length} cards.`);
      return parsed;
    } finally {
      clearTimeout(timeout);
    }
  } catch (error) {
    console.warn(`Deeper Read unavailable: ${error.message}`);
    await appendUsageLog({
      generatedAt: new Date().toISOString(),
      status: "deeper_read_error",
      model: aiModel,
      error: error.message
    });
    return deterministicDeeperRead(candidates, `Deeper Read AI call failed: ${error.message}`);
  }
}

async function buildAiRecommendations({ opportunities, macro, calendar, sources, deskRecommendations, sectorPerformance, model: modelSummary, marketIntelligence, promptText }) {
  const apiKey = process.env.OPENAI_API_KEY;
  if (process.env.SKIP_AI === "1") return fallbackAiRecommendations("skipped_by_env");
  if (!apiKey) return fallbackAiRecommendations("missing_api_key");

  console.log("Building AI recommendation context...");
  const aiModel = process.env.OPENAI_MODEL || defaultOpenAiModel;
  const deeperReadHistory = await loadDeeperReadHistory();
  const sourceArticles = sortArticlesNewestFirst(
    sources
      .flatMap((source) =>
        (source.articles || []).map((article) => ({
          ...article,
          sourceName: article.sourceName || source.name,
          sourceCategory: source.category,
          trust: source.trust
        }))
      )
      .filter((article) => article.title && article.url)
  ).slice(0, 36);
  const sourceTape = sourceArticles.map(({ sourceName, sourceCategory, trust, title, url, publishedAt, summary, excerpt }, index) => ({
    id: `S${index + 1}`,
    sourceName,
    sourceCategory,
    trust,
    title,
    url,
    publishedAt,
    summary,
    excerpt: excerpt?.slice(0, 900) || ""
  }));
  const deeperReadCandidates = buildDeeperReadCandidates(sourceTape, deeperReadHistory);
  const modelCandidates = opportunities.filter(hasModelRank).slice(0, 30).map(compactCandidate);
  const avoidCandidates = bottomModelCandidates(opportunities, 16).map(compactCandidate);
  const avoidSectors = bottomModelSectorClusters(opportunities, 5);
  const companyContexts = await buildCompanyContexts(modelCandidates);
  console.log(`Built ${companyContexts.length} company contexts for AI memo.`);
  const validSymbols = [
    ...new Set(
      companyContexts.length
        ? companyContexts.map((item) => item.symbol)
        : modelCandidates.length
          ? modelCandidates.map((item) => item.symbol)
        : [
            ...opportunities.slice(0, 40).map((item) => item.symbol),
            ...opportunities.filter((item) => item.type === "etf").slice(0, 15).map((item) => item.symbol),
            ...deskRecommendations.map((item) => item.symbol)
          ]
    )
  ].filter(Boolean);
  const companySourceRefs = companyContexts.flatMap((context) => context.sources || []);
  const officialMacroSourceRefs = (marketIntelligence?.officialMacro?.releases || []).map((release) => ({
    id: release.id,
    title: release.title || release.event,
    url: release.sourceUrl,
    sourceName: release.sourceName,
    publishedAt: release.publishedAt || release.releaseAt,
    summary: release.marketRead
  }));
  const sourceRefIds = [
    ...sourceTape.map((source) => source.id),
    ...officialMacroSourceRefs.map((source) => source.id),
    ...companySourceRefs.map((source) => source.id)
  ].filter(Boolean);
  const sourceRefSchema = sourceRefIds.length
    ? { type: "string", enum: sourceRefIds }
    : { type: "string" };
  const validAvoidSymbols = avoidCandidates.map((item) => item.symbol).filter(Boolean);
  const payload = {
    generatedAt: new Date().toISOString(),
    model: modelSummary,
    macro,
    upcomingEvents: calendar.slice(0, 8),
    marketIntelligence: {
      window: marketIntelligence?.window || null,
      marketDataStatus: marketIntelligence?.marketDataStatus || null,
      sourceHealth: marketIntelligence?.sourceHealth || null,
      topThemes: marketIntelligence?.topThemes || [],
      professionalDrivers: (marketIntelligence?.professionalDrivers || []).slice(0, 12),
      officialMacro: {
        generatedAt: marketIntelligence?.officialMacro?.generatedAt || null,
        lookbackHours: marketIntelligence?.officialMacro?.lookbackHours || macroReleaseLookbackHours,
        releases: (marketIntelligence?.officialMacro?.releases || []).slice(0, 6)
      },
      earnings: {
        dates: marketIntelligence?.earnings?.dates || [],
        calendars: (marketIntelligence?.earnings?.calendars || []).map((item) => ({
          date: item.date,
          asOf: item.asOf,
          status: item.status,
          rowCount: item.rowCount,
          rows: (item.rows || []).slice(0, 18)
        })),
        earningsMovers: (marketIntelligence?.earnings?.earningsMovers || []).slice(0, 10),
        articleHeadlines: (marketIntelligence?.earnings?.articleHeadlines || []).slice(0, 10)
      },
      reddit: {
        status: marketIntelligence?.reddit?.status || "missing",
        sourceNote: marketIntelligence?.reddit?.sourceNote || "",
        subreddits: marketIntelligence?.reddit?.subreddits || [],
        topTickers: (marketIntelligence?.reddit?.topTickers || []).slice(0, 12),
        topPosts: (marketIntelligence?.reddit?.topPosts || []).slice(0, 10)
      },
      marketMovers: {
        gainers: (marketIntelligence?.marketMovers?.gainers || []).slice(0, 8),
        losers: (marketIntelligence?.marketMovers?.losers || []).slice(0, 8),
        mostActive: (marketIntelligence?.marketMovers?.mostActive || []).slice(0, 8)
      }
    },
    sourceTape,
    officialMacroSourceRefs,
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
    deskRecommendations,
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
    max_output_tokens: 7600
  };

  try {
    console.log(`Calling OpenAI ${aiModel} for strategy memo...`);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), openAiTimeoutMs);
    const response = await fetch("https://api.openai.com/v1/responses", {
      method: "POST",
      signal: controller.signal,
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${apiKey}`
      },
      body: JSON.stringify(requestBody)
    });
    try {
      const json = await response.json();
      if (!response.ok) throw new Error(json.error?.message || `${response.status} ${response.statusText}`);
      const usage = estimateOpenAiCost(aiModel, json.usage);
      const text = responseText(json);
      if (!text) throw new Error("OpenAI response did not include final text; try a larger max_output_tokens value or a lower reasoning effort.");
      const parsed = normalizeAiRecommendationContext(JSON.parse(text), companyContexts, sourceRefIds, sourceTape);
      const deeperRead = await buildAiDeeperRead({
        apiKey,
        aiModel,
        candidates: deeperReadCandidates,
        sourceTape,
        sourceRefIds,
        history: deeperReadHistory
      });
      parsed.deeperRead = deeperRead.cards.length ? deeperRead : parsed.deeperRead;
      await writeDeeperReadHistory(parsed.deeperRead, deeperReadHistory);
      await appendUsageLog({
        generatedAt: new Date().toISOString(),
        status: "ready",
        model: aiModel,
        usage
      });
      console.log("AI strategy memo generated.");
      return {
        status: "ready",
        model: aiModel,
        usage,
        companyContextCount: companyContexts.length,
        ...parsed
      };
    } finally {
      clearTimeout(timeout);
    }
  } catch (error) {
    console.warn(`AI strategy memo unavailable: ${error.message}`);
    await appendUsageLog({
      generatedAt: new Date().toISOString(),
      status: "ai_error",
      model: aiModel,
      error: error.message
    });
    return {
      ...fallbackAiRecommendations("ai_error"),
      model: aiModel,
      headline: "Deterministic strategy fallback is showing because AI synthesis failed.",
      macroView: `The deterministic dashboard refreshed with current model, macro, source, earnings, and Reddit inputs. The AI call failed: ${error.message}`,
      recommendations: deterministicRecommendationFallback(modelCandidates, deskRecommendations, marketIntelligence),
      deeperRead: deterministicDeeperRead(deeperReadCandidates, `Strategy memo AI call failed: ${error.message}`),
      portfolioNotes: [
        "Use the Daily Read, Desk Calls, Momentum Book, and Model Scoreboard as the primary refreshed outputs for this run.",
        "AI narrative synthesis failed, so company-specific recommendations are deterministic summaries of model-ranked candidates."
      ],
      openQuestions: [
        "Retry the AI call if a narrative recommendation layer is required for this refresh.",
        "Review the model candidates against the market-driver and source-tape sections before acting."
      ]
    };
  }
}

async function main() {
  await loadLocalEnv();

  const [sourceMarkdown, universeConfigText, aiPromptText, modelRankings, modelExplainability, previousRefreshStatus, previousModelMonitoring] = await Promise.all([
    readFile(join(root, "config/news-sources.md"), "utf8"),
    readFile(join(root, "config/universe.json"), "utf8"),
    readFile(join(root, "config/ai-recommendation-prompt.md"), "utf8").catch(() => defaultAiPrompt),
    loadModelRankings(),
    loadModelExplainability(),
    loadPreviousRefreshStatus(),
    loadPreviousModelMonitoring()
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
  const knownSymbols = new Set(
    [
      ...universe.map((item) => item.symbol),
      ...modelRankings.rankings.map((item) => item.symbol),
      "SPY",
      "QQQ",
      "IWM",
      "TLT",
      "GLD",
      "HYG",
      "GME",
      "OKLO",
      "CRCL",
      "SOXX",
      "URA",
      "SLV",
      "ASML"
    ]
      .map(mentionKey)
      .filter(Boolean)
  );
  const redditTapePromise = fetchRedditTape(knownSymbols);
  const marketMoversPromise = fetchMarketMovers();
  const officialMacroPromise = fetchOfficialMacroReleases(calendar);

  console.log(`Screening ${universe.length} instruments...`);

  const stockMetadata = constituentMetadataBySymbol(stocks);
  const expectedTechnicalAsOfDate = latestExpectedMarketDataDate();
  let opportunities;
  let signalsBySymbol;
  let marketStrip;
  let sectorPerformance;
  let marketDataStatus = {
    status: "fresh",
    message: "Fresh end-of-day chart history was fetched for this refresh.",
    expectedAsOfDate: expectedTechnicalAsOfDate,
    staleDataReused: false
  };

  if (hasFreshModelTechnicalTape(modelRankings, expectedTechnicalAsOfDate)) {
    opportunities = modelTechnicalOpportunities(modelRankings, stockMetadata, etfs);
    signalsBySymbol = new Map(opportunities.map((item) => [item.symbol, item]));
    marketStrip = (modelRankings.marketRows || []).slice(0, 6).map((item) => ({
      symbol: item.symbol,
      label: item.label || item.name || item.symbol,
      price: item.price ?? item.close,
      changePct: item.changePct
    }));
    sectorPerformance = modelRankings.sectorRows || [];
    marketDataStatus = {
      status: "fresh",
      source: modelRankings.technicalTape.source || "model_scorer_yahoo_history",
      message: `Fresh end-of-day technical tape was reused from the model scoring pass through ${modelRankings.technicalTape.asOfDate}.`,
      expectedAsOfDate: expectedTechnicalAsOfDate,
      asOfDate: modelRankings.technicalTape.asOfDate,
      staleDataReused: false
    };
  } else {
    const reason = modelRankings.error
      || (modelRankings.technicalTape?.asOfDate
        ? `model technical tape is stale: latest ${modelRankings.technicalTape.asOfDate}, expected ${expectedTechnicalAsOfDate}`
        : "fresh model technical tape is missing");
    console.warn(`Fresh model technical tape unavailable; stale cached technicals will not be reused: ${reason}`);
    opportunities = modelTechnicalOpportunities(modelRankings, stockMetadata, etfs);
    signalsBySymbol = new Map();
    marketStrip = (modelRankings.marketRows || []).slice(0, 6).map((item) => ({
      symbol: item.symbol,
      label: item.label || item.name || item.symbol,
      price: item.price ?? item.close,
      changePct: item.changePct
    }));
    sectorPerformance = modelRankings.sectorRows || [];
    marketDataStatus = {
      status: "unavailable",
      source: modelRankings.technicalTape?.source || null,
      message: `Fresh model technical tape is unavailable (${reason}); stale cached technical values were not reused. Run scripts/modeling/score_live_rank_model.py before the briefing refresh to restore trailing returns, sector tiles, breadth, and scorebook technical columns.`,
      expectedAsOfDate: expectedTechnicalAsOfDate,
      asOfDate: modelRankings.technicalTape?.asOfDate || modelRankings.asOfDate || null,
      staleDataReused: false
    };
  }

  const marketCapCachePromise = loadMarketCapCache().then((cache) =>
    modelRankings.status === "ready" ? enrichMarketCaps(modelRankings.rankings.map((item) => item.symbol), cache) : cache
  );

  console.log("Refreshing macro, source, Reddit, and market-mover tapes...");
  const [macro, sources, redditTape, marketMovers, officialMacro] = await Promise.all([
    mapLimit(macroSeries, 4, fetchFredSeries),
    checkedSourcesPromise,
    redditTapePromise,
    marketMoversPromise,
    officialMacroPromise
  ]);
  console.log(`Source tape checked: ${sources.filter((source) => source.ok).length}/${sources.length} live.`);
  console.log(`Official macro releases captured: ${(officialMacro.releases || []).filter((release) => release.status === "ready").length}/${(officialMacro.releases || []).length}.`);
  const earningsTape = await buildEarningsTape({ sources, marketMovers, knownSymbols });
  console.log(`Earnings tape built: ${(earningsTape.earningsMovers || []).length} earnings-linked movers.`);
  const marketIntelligence = buildMarketIntelligence({
    sources,
    earningsTape,
    redditTape,
    marketMovers,
    previousRefreshStatus,
    knownSymbols,
    marketDataStatus,
    officialMacro
  });
  console.log(`Market intelligence built: ${(marketIntelligence.professionalDrivers || []).length} professional drivers.`);
  const upcomingCalendar = upcomingMacroEvents(calendar);
  const deskRecommendations = buildRecommendations(opportunities);
  const aiRecommendations = await buildAiRecommendations({
    opportunities,
    macro,
    calendar: upcomingCalendar,
    sources,
    deskRecommendations,
    sectorPerformance,
    model: modelSummary,
    marketIntelligence,
    promptText: aiPromptText
  });
  const avoidList = buildAvoidList(opportunities, aiRecommendations);
  console.log("Waiting for market-cap cache refresh...");
  const marketCapCache = await marketCapCachePromise;
  console.log("Market-cap cache ready.");
  if (modelRankings.status === "ready") await writeMarketCapCache(marketCapCache);
  const modelScorebook = buildModelScorebook({
    modelRankings,
    stockMetadata,
    marketCapCache,
    signalsBySymbol,
    marketDataStatus
  });
  const modelMonitoring = buildModelMonitoring({
    modelScorebook,
    previousMonitoring: previousModelMonitoring
  });

  const snapshot = {
    generatedAt: new Date().toISOString(),
    model: modelSummary,
    note: buildNote({
      opportunities,
      macro,
      calendar: upcomingCalendar,
      sources,
      model: modelSummary,
      sectorPerformance,
      deskRecommendations,
      aiRecommendations,
      marketIntelligence
    }),
    aiRecommendations,
    deeperRead: aiRecommendations.deeperRead || fallbackAiRecommendations("missing_deeper_read").deeperRead,
    marketDataStatus,
    marketIntelligence,
    avoidList,
    recommendations: deskRecommendations,
    sectorPerformance,
    marketStrip,
    opportunities,
    macro,
    calendar: upcomingCalendar,
    sources
  };

  await Promise.all([
    writeFile(join(root, snapshotOutput), `${JSON.stringify(snapshot, null, 2)}\n`),
    writeFile(join(root, scorebookOutput), `${JSON.stringify(modelScorebook, null, 2)}\n`),
    writeFile(join(root, monitoringOutput), `${JSON.stringify(modelMonitoring, null, 2)}\n`)
  ]);
  console.log(`Wrote ${snapshotOutput} with ${opportunities.length} ranked instruments.`);
  console.log(`Wrote ${scorebookOutput} with ${modelScorebook.rowCount} model-scored rows.`);
  console.log(`Wrote ${monitoringOutput} with ${modelMonitoring.topDecileCount} top-decile rows.`);
}

export { buildModelMonitoring, checkSource, extractArticleCandidates, fetchOfficialMacroReleases, parseMarkdownSources, publishedDateFromHtml, sortArticlesNewestFirst };

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
