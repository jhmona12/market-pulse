import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const outputPath = process.env.MACRO_CALENDAR_OUTPUT || "data/macro-calendar.json";
const monthsAhead = Number.parseInt(process.env.MACRO_CALENDAR_MONTHS || "6", 10);
const lookbackDays = Number.parseInt(process.env.MACRO_CALENDAR_LOOKBACK_DAYS || "7", 10);
const userAgent = "MarketPulseMacroCalendar/0.1 personal research dashboard";

const monthIndex = new Map([
  ["january", 0],
  ["february", 1],
  ["march", 2],
  ["april", 3],
  ["may", 4],
  ["june", 5],
  ["july", 6],
  ["august", 7],
  ["september", 8],
  ["october", 9],
  ["november", 10],
  ["december", 11]
]);

const fredBlsReleases = [
  {
    rid: 50,
    event: "Employment Situation",
    source: "BLS",
    importance: "High",
    sourceUrl: "https://fred.stlouisfed.org/releases/calendar?rid=50",
    note: "FRED calendar for the BLS Employment Situation release. Direct BLS schedule pages can block automated requests."
  },
  {
    rid: 10,
    event: "Consumer Price Index",
    source: "BLS",
    importance: "High",
    sourceUrl: "https://fred.stlouisfed.org/releases/calendar?rid=10",
    note: "FRED calendar for the BLS CPI release. Direct BLS schedule pages can block automated requests."
  },
  {
    rid: 46,
    event: "Producer Price Index",
    source: "BLS",
    importance: "High",
    sourceUrl: "https://fred.stlouisfed.org/releases/calendar?rid=46",
    note: "FRED calendar for the BLS PPI release. Direct BLS schedule pages can block automated requests."
  }
];

function dateKeyInZone(date = new Date(), timeZone = "America/New_York") {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).formatToParts(date);
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${byType.year}-${byType.month}-${byType.day}`;
}

function dateFromKey(dateKey) {
  const [year, month, day] = dateKey.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

function addDays(date, days) {
  const next = new Date(date);
  next.setUTCDate(next.getUTCDate() + days);
  return next;
}

function addMonths(date, months) {
  const next = new Date(date);
  next.setUTCMonth(next.getUTCMonth() + months);
  return next;
}

function toDateKey(date) {
  return date.toISOString().slice(0, 10);
}

function decodeHtml(value) {
  return String(value || "")
    .replace(/&amp;/g, "&")
    .replace(/&#039;/g, "'")
    .replace(/&quot;/g, "\"")
    .replace(/&nbsp;/g, " ")
    .replace(/&ndash;/g, "-")
    .replace(/&mdash;/g, "-")
    .replace(/\s+/g, " ")
    .trim();
}

function stripTags(value) {
  return decodeHtml(String(value || "").replace(/<[^>]+>/g, " "));
}

function parseMonthDayYear(value, fallbackYear = null) {
  const clean = decodeHtml(value).replace(/\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b,?\s*/i, "");
  const match = clean.match(/\b([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?\b/);
  if (!match) return null;
  const month = monthIndex.get(match[1].toLowerCase());
  const day = Number.parseInt(match[2], 10);
  const year = Number.parseInt(match[3] || fallbackYear, 10);
  if (month == null || !Number.isInteger(day) || !Number.isInteger(year)) return null;
  return toDateKey(new Date(Date.UTC(year, month, day)));
}

function displayEasternFromCentral(timeText) {
  const match = String(timeText || "").trim().match(/^(\d{1,2}):(\d{2})\s*([ap])m$/i);
  if (!match) return "8:30 AM ET";
  let hour = Number.parseInt(match[1], 10);
  const minute = match[2];
  const period = match[3].toUpperCase();
  if (period === "P" && hour !== 12) hour += 12;
  if (period === "A" && hour === 12) hour = 0;
  hour = (hour + 1) % 24;
  const displayPeriod = hour >= 12 ? "PM" : "AM";
  const displayHour = hour % 12 || 12;
  return `${displayHour}:${minute} ${displayPeriod} ET`;
}

function dateWithinWindow(dateKey, windowStartKey, windowEndKey) {
  return dateKey >= windowStartKey && dateKey <= windowEndKey;
}

async function fetchText(url) {
  const response = await fetch(url, {
    headers: {
      "user-agent": userAgent,
      accept: "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8"
    }
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.text();
}

function dedupeAndSort(events) {
  const seen = new Set();
  return events
    .filter((event) => event.date && event.event && event.source)
    .filter((event) => {
      const key = `${event.date}|${event.time}|${event.source}|${event.event}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((a, b) => {
      if (a.date !== b.date) return a.date.localeCompare(b.date);
      if (a.time !== b.time) return a.time.localeCompare(b.time);
      return a.event.localeCompare(b.event);
    });
}

async function scrapeFredBlsCalendar(release, windowStartKey, windowEndKey) {
  const startYear = Number.parseInt(windowStartKey.slice(0, 4), 10);
  const endYear = Number.parseInt(windowEndKey.slice(0, 4), 10);
  const events = [];

  for (let year = startYear; year <= endYear; year += 1) {
    const url = `https://fred.stlouisfed.org/releases/calendar?od=asc&rid=${release.rid}&ve=${year}-12-31&view=year&vs=${year}-01-01`;
    const html = await fetchText(url);
    const pattern = /<span style="font-weight:\s*bold;">([^<]+)<\/span>[\s\S]*?<td[^>]*>\s*([0-9]{1,2}:[0-9]{2}\s*[ap]m)\s*<\/td>[\s\S]*?<a href="\/release\?rid=\d+">([^<]+)<\/a>/gi;
    for (const match of html.matchAll(pattern)) {
      const date = parseMonthDayYear(match[1], year);
      if (!date || !dateWithinWindow(date, windowStartKey, windowEndKey)) continue;
      events.push({
        date,
        time: displayEasternFromCentral(match[2]),
        event: release.event,
        title: stripTags(match[3]),
        source: release.source,
        importance: release.importance,
        sourceUrl: url,
        sourceName: "FRED release calendar",
        upstreamSource: "BLS",
        scraper: "fred-release-calendar",
        note: release.note
      });
    }
  }

  return events;
}

function normalizeBeaEvent(title) {
  if (/Personal Income and Outlays/i.test(title)) return "Personal Income and Outlays";
  if (/GDP \(Advance Estimate\)/i.test(title)) return "GDP Advance Estimate";
  if (/GDP \(Second Estimate\)/i.test(title)) return "GDP Second Estimate";
  if (/GDP \(Third Estimate\)/i.test(title)) return "GDP Third Estimate";
  if (/GDP/i.test(title)) return "Gross Domestic Product";
  return title;
}

async function scrapeBeaCalendar(windowStartKey, windowEndKey) {
  const url = "https://www.bea.gov/news/schedule";
  const html = await fetchText(url);
  const year = html.match(/<th[^>]*>\s*Year\s+(\d{4})\s*<\/th>/i)?.[1] || windowStartKey.slice(0, 4);
  const events = [];
  const rowPattern = /<tr[^>]*>[\s\S]*?<div class="release-date">([^<]+)<\/div>\s*<small class="text-muted">([^<]+)<\/small>[\s\S]*?<td class="release-title[^"]*"[^>]*>([\s\S]*?)<\/td>[\s\S]*?<\/tr>/gi;

  for (const match of html.matchAll(rowPattern)) {
    const title = stripTags(match[3]);
    if (!/(GDP|Personal Income and Outlays)/i.test(title)) continue;
    const date = parseMonthDayYear(match[1], year);
    if (!date || !dateWithinWindow(date, windowStartKey, windowEndKey)) continue;
    events.push({
      date,
      time: `${stripTags(match[2]).replace(/\s+/g, " ")} ET`,
      event: normalizeBeaEvent(title),
      title,
      source: "BEA",
      importance: "High",
      sourceUrl: url,
      sourceName: "BEA release schedule",
      scraper: "bea-release-schedule"
    });
  }

  return events;
}

function endDayFromRange(value) {
  const clean = String(value || "").replace(/\*/g, "").trim();
  const match = clean.match(/(\d{1,2})(?:\s*-\s*(\d{1,2}))?/);
  return match ? Number.parseInt(match[2] || match[1], 10) : null;
}

async function scrapeFedFomcCalendar(windowStartKey, windowEndKey) {
  const url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm";
  const html = await fetchText(url);
  const events = [];
  const panelPattern = /<h4><a[^>]*>\s*(\d{4})\s+FOMC Meetings\s*<\/a><\/h4><\/div>([\s\S]*?)(?=<div class="panel panel-default"><div class="panel-heading"><h4>|$)/gi;

  for (const panel of html.matchAll(panelPattern)) {
    const year = Number.parseInt(panel[1], 10);
    const panelHtml = panel[2];
    const rowPattern = /fomc-meeting__month[\s\S]*?<strong>([^<]+)<\/strong>[\s\S]*?fomc-meeting__date[^>]*>([^<]+)<\/div>/gi;
    for (const row of panelHtml.matchAll(rowPattern)) {
      const month = monthIndex.get(stripTags(row[1]).toLowerCase());
      const day = endDayFromRange(row[2]);
      if (month == null || !day) continue;
      const date = toDateKey(new Date(Date.UTC(year, month, day)));
      if (!dateWithinWindow(date, windowStartKey, windowEndKey)) continue;
      events.push({
        date,
        time: "2:00 PM ET",
        event: "FOMC Rate Decision",
        title: `FOMC meeting decision (${stripTags(row[1])} ${stripTags(row[2])}, ${year})`,
        source: "Federal Reserve",
        importance: "High",
        sourceUrl: url,
        sourceName: "Federal Reserve FOMC calendar",
        scraper: "federal-reserve-fomc-calendar",
        projectionMaterialsExpected: /\*/.test(row[2])
      });
    }
  }

  return events;
}

async function main() {
  const todayKey = process.env.MACRO_CALENDAR_TODAY || dateKeyInZone(new Date(), "America/New_York");
  const today = dateFromKey(todayKey);
  const windowStart = addDays(today, -Math.max(0, lookbackDays));
  const windowEnd = addMonths(today, monthsAhead);
  const windowStartKey = toDateKey(windowStart);
  const windowEndKey = toDateKey(windowEnd);

  const sourceResults = [];
  const allEvents = [];

  for (const release of fredBlsReleases) {
    try {
      const events = await scrapeFredBlsCalendar(release, windowStartKey, windowEndKey);
      allEvents.push(...events);
      sourceResults.push({ source: `${release.source} ${release.event}`, status: "ready", eventCount: events.length, scraper: "fred-release-calendar" });
    } catch (error) {
      sourceResults.push({ source: `${release.source} ${release.event}`, status: "error", error: error.message, scraper: "fred-release-calendar" });
    }
  }

  try {
    const events = await scrapeBeaCalendar(windowStartKey, windowEndKey);
    allEvents.push(...events);
    sourceResults.push({ source: "BEA release schedule", status: "ready", eventCount: events.length, scraper: "bea-release-schedule" });
  } catch (error) {
    sourceResults.push({ source: "BEA release schedule", status: "error", error: error.message, scraper: "bea-release-schedule" });
  }

  try {
    const events = await scrapeFedFomcCalendar(windowStartKey, windowEndKey);
    allEvents.push(...events);
    sourceResults.push({ source: "Federal Reserve FOMC calendar", status: "ready", eventCount: events.length, scraper: "federal-reserve-fomc-calendar" });
  } catch (error) {
    sourceResults.push({ source: "Federal Reserve FOMC calendar", status: "error", error: error.message, scraper: "federal-reserve-fomc-calendar" });
  }

  const events = dedupeAndSort(allEvents);
  if (!events.length) {
    throw new Error("No macro release dates were scraped; refusing to overwrite the macro calendar.");
  }

  const payload = {
    generatedAt: new Date().toISOString(),
    window: {
      today: todayKey,
      lookbackDays,
      monthsAhead,
      startDate: windowStartKey,
      endDate: windowEndKey
    },
    sources: sourceResults,
    events
  };

  await mkdir(dirname(join(root, outputPath)), { recursive: true });
  await writeFile(join(root, outputPath), `${JSON.stringify(payload, null, 2)}\n`);
  console.log(`Wrote ${outputPath} with ${events.length} macro release dates through ${windowEndKey}.`);
  sourceResults.forEach((source) => {
    const detail = source.status === "ready" ? `${source.eventCount} events` : source.error;
    console.log(`${source.status.toUpperCase()}: ${source.source} (${detail})`);
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
