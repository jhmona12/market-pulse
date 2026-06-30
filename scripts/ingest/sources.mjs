const currentYear = new Date().getFullYear();
const articlesPerSource = Number.parseInt(process.env.SOURCE_ARTICLES_PER_SOURCE || "3", 10);
const maxArticleCandidates = Number.parseInt(process.env.SOURCE_ARTICLE_CANDIDATES || "6", 10);

function parseMarkdownSources(markdown) {
  return String(markdown || "")
    .split("\n")
    .filter((line) => line.trim().startsWith("|") && !line.includes("---"))
    .slice(1)
    .map((line) => line.split("|").slice(1, -1).map((cell) => cell.trim()))
    .filter((cells) => cells.length >= 6 && cells[1]?.startsWith("http"))
    .map(([name, url, category, cadence, trust, notes]) => ({ name, url, category, cadence, trust, notes }));
}

function decodeHtml(value) {
  let decoded = String(value || "");
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
  return decodeHtml(String(value || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim());
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
    const match = String(block || "").match(new RegExp(`<${name}\\b[^>]*>([\\s\\S]*?)<\\/${name}>`, "i"));
    if (match) return stripTags(stripCdata(match[1]));
  }
  return "";
}

function xmlRaw(block, tagNames) {
  const names = Array.isArray(tagNames) ? tagNames : [tagNames];
  for (const name of names) {
    const match = String(block || "").match(new RegExp(`<${name}\\b[^>]*>([\\s\\S]*?)<\\/${name}>`, "i"));
    if (match) return stripCdata(match[1]);
  }
  return "";
}

function xmlLink(block) {
  const atomLink = String(block || "").match(/<link\b[^>]*href=["']([^"']+)["'][^>]*>/i)?.[1];
  if (atomLink) return decodeHtml(atomLink);
  return xmlText(block, "link");
}

function attrValue(tag, name) {
  return String(tag || "").match(new RegExp(`${name}\\s*=\\s*["']([^"']+)["']`, "i"))?.[1] || "";
}

function metaContent(html, names) {
  const lowered = names.map((name) => name.toLowerCase());
  for (const match of String(html || "").matchAll(/<meta\b[^>]*>/gi)) {
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
      String(html || "").match(/<h1[^>]*>([\s\S]*?)<\/h1>/i)?.[1] ||
      String(html || "").match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] ||
      fallback
  ).slice(0, 160);
}

function summaryFromHtml(html, fallback) {
  return stripTags(
    metaContent(html, ["description", "og:description", "twitter:description"]) ||
      String(html || "").match(/<p[^>]*>([\s\S]*?)<\/p>/i)?.[1] ||
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

function normalizeDate(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  if (date.getFullYear() < 2000 || date.getFullYear() > currentYear + 1) return null;
  return date.toISOString();
}

function extractFeedItems(feed, source) {
  if (!looksLikeFeed(feed)) return [];
  const blocks = [
    ...[...String(feed || "").matchAll(/<item\b[\s\S]*?<\/item>/gi)].map((match) => match[0]),
    ...[...String(feed || "").matchAll(/<entry\b[\s\S]*?<\/entry>/gi)].map((match) => match[0])
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
  for (const match of String(html || "").matchAll(/<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)) {
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

function publishedDateFromHtml(html, fallbackText = "") {
  const source = String(html || "");
  const candidates = [
    metaContent(source, [
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
    source.match(/<time[^>]+datetime=["']([^"']+)["'][^>]*>/i)?.[1],
    ...extractJsonLdDates(source)
  ];

  const textDate = String(fallbackText || "").match(/\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},\s+20\d{2}\b/i)?.[0];
  if (textDate) candidates.push(textDate);

  for (const candidate of candidates) {
    const normalized = normalizeDate(candidate);
    if (normalized) return normalized;
  }
  return null;
}

function visibleTextFromHtml(html) {
  const withoutNoise = String(html || "")
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
  if (haystackIncludesSource(haystack, source.name)) score += 1;
  if (url.pathname.split("/").filter(Boolean).length >= 2) score += 2;
  if (url.pathname === "/" || url.hash) score -= 3;
  return score;
}

function haystackIncludesSource(haystack, sourceName) {
  return haystack.includes(String(sourceName || "").toLowerCase().split(" ")[0]);
}

function extractArticleCandidates(html, source) {
  const sourceUrl = new URL(source.url);
  const candidates = new Map();
  for (const match of String(html || "").matchAll(/<a\b([^>]*?)>([\s\S]*?)<\/a>/gi)) {
    const attrs = match[1];
    const href = attrValue(attrs, "href");
    const linkText = stripTags(match[2]);
    if (!href || !linkText || linkText.length < 8) continue;
    let url;
    try {
      url = new URL(decodeHtml(href), sourceUrl);
    } catch {
      continue;
    }
    if (!["http:", "https:"].includes(url.protocol)) continue;
    if (!sameHostOrSubdomain(sourceUrl.hostname.replace(/^www\./, ""), url.hostname.replace(/^www\./, ""))) continue;
    url.hash = "";
    const cleanUrl = url.href;
    const score = articleScore(url, linkText, source);
    if (score < 4) continue;
    const prior = candidates.get(cleanUrl);
    if (!prior || score > prior.score) {
      candidates.set(cleanUrl, { url: cleanUrl, linkText: linkText.slice(0, 180), score });
    }
  }
  return [...candidates.values()]
    .sort((a, b) => b.score - a.score)
    .slice(0, Math.max(articlesPerSource, maxArticleCandidates));
}

function sortArticlesNewestFirst(articles) {
  return [...(articles || [])].sort((a, b) => {
    const aTime = a.publishedAt ? new Date(a.publishedAt).getTime() : 0;
    const bTime = b.publishedAt ? new Date(b.publishedAt).getTime() : 0;
    if (aTime !== bTime) return bTime - aTime;
    return String(a.title || "").localeCompare(String(b.title || ""));
  });
}

export {
  attrValue,
  decodeHtml,
  extractArticleCandidates,
  extractFeedItems,
  looksLikeFeed,
  metaContent,
  normalizeDate,
  parseMarkdownSources,
  publishedDateFromHtml,
  sortArticlesNewestFirst,
  stripTags,
  summaryFromFeed,
  summaryFromHtml,
  titleFromFeed,
  titleFromHtml,
  visibleTextFromHtml
};
