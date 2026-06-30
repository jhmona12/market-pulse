export function sortedSourceArticles(sources = []) {
  return (sources || [])
    .flatMap((source) => (source.articles || []).map((article) => ({ ...article, sourceName: article.sourceName || source.name })))
    .filter((article) => article.title && article.url)
    .filter((article) => !/(pardon our interruption|privacy|terms|sign in|login|subscribe)/i.test(`${article.title} ${article.summary || ""}`))
    .sort((a, b) => {
      const aTime = a.publishedAt ? new Date(a.publishedAt).getTime() : 0;
      const bTime = b.publishedAt ? new Date(b.publishedAt).getTime() : 0;
      if (aTime !== bTime) return bTime - aTime;
      return String(a.title || "").localeCompare(String(b.title || ""));
    })
    .slice(0, 36);
}

export function compactSourceName(value) {
  return String(value || "")
    .replace(/\s*\|\s*.*/, "")
    .replace(/\s*-\s*.*$/, "")
    .replace(/\s+Research$/, "")
    .trim();
}

export function buildSourceRefMap(snapshot = {}) {
  const map = new Map(sortedSourceArticles(snapshot.sources).map((article, index) => [`S${index + 1}`, article]));
  (snapshot.marketIntelligence?.officialMacro?.releases || []).forEach((release) => {
    if (!release.id) return;
    map.set(release.id, {
      sourceName: release.sourceName,
      title: release.title || release.event,
      url: release.sourceUrl,
      publishedAt: release.publishedAt || release.releaseAt,
      summary: release.marketRead
    });
  });
  return map;
}

export function sourceRefLabel(ref, symbol, researchSources) {
  const research = String(ref).match(/^S(\d+)$/);
  if (research) {
    const article = researchSources.get(ref);
    return compactSourceName(article?.sourceName) || `Research Source ${research[1]}`;
  }

  const officialMacro = String(ref).match(/^O(\d+)$/);
  if (officialMacro) {
    const release = researchSources.get(ref);
    return compactSourceName(release?.sourceName) || `Official Macro ${officialMacro[1]}`;
  }

  const companyRef = String(ref).match(/^C\d+-(N\d+|IR|MARKETCAP|EARNINGS)$/);
  if (!companyRef) return ref;

  const prefix = symbol ? `${symbol} ` : "";
  if (companyRef[1].startsWith("N")) return `${prefix}News`;
  if (companyRef[1] === "IR") return `${prefix}Investor Relations`;
  if (companyRef[1] === "MARKETCAP") return `${prefix}Market Cap`;
  if (companyRef[1] === "EARNINGS") return `${prefix}Earnings`;
  return ref;
}

export function sourceRefLabels(refs, symbol, researchSources) {
  return [...new Set((refs || []).map((ref) => sourceRefLabel(ref, symbol, researchSources)).filter(Boolean))];
}
