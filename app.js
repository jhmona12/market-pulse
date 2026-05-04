const fallbackSnapshot = {
  generatedAt: new Date().toISOString(),
  model: {
    status: "fallback",
    scoredCount: 0,
    model: null,
    explainability: null,
    topRankings: []
  },
  note: {
    headline: "Fallback snapshot loaded; run the refresh script for current metrics.",
    body: "This fallback appears only when data/snapshot.json is unavailable. It is sample data and should not be treated as a current market read.",
    changed: [
      "No live snapshot was loaded.",
      "Run node scripts/update-data.mjs to rebuild the dashboard data.",
      "Current source, macro, and momentum metrics are unavailable in fallback mode."
    ],
    watch: [
      "Check that the local server is running from the project folder.",
      "Check that data/snapshot.json exists.",
      "Check the browser console if the dashboard continues to show fallback data."
    ]
  },
  marketStrip: [
    { symbol: "SPY", label: "S&P 500", price: 520.12, changePct: 0.42 },
    { symbol: "QQQ", label: "Nasdaq 100", price: 442.33, changePct: 0.68 },
    { symbol: "IWM", label: "Russell 2000", price: 205.71, changePct: -0.18 },
    { symbol: "TLT", label: "20Y Treasury", price: 91.52, changePct: 0.24 },
    { symbol: "GLD", label: "Gold", price: 218.44, changePct: 0.12 },
    { symbol: "HYG", label: "High Yield", price: 78.31, changePct: 0.09 }
  ],
  sectorPerformance: [
    {
      sector: "Technology",
      symbol: "XLK",
      change1d: 0.4,
      change5d: 1.8,
      change30d: 6.1,
      ytd: 14.2,
      relative30d: 2.4,
      above50: true,
      above200: true,
      rsi14: 63,
      history: [199, 201, 203, 204, 205, 207, 208, 210, 212, 214]
    }
  ],
  recommendations: [
    {
      label: "Metric Match",
      symbol: "NVDA",
      title: "Score 86; RSI 68",
      rationale: "NVDA: above 50-day and 200-day averages; 20-day return +12.40%; volume 1.42x 20-day average."
    },
    {
      label: "Near Threshold",
      symbol: "XLK",
      title: "Score 79; ETF",
      rationale: "XLK: above 50-day and 200-day averages; 20-day return +6.10%; volume 1.18x 20-day average."
    },
    {
      label: "RSI Above Limit",
      symbol: "GOOGL",
      title: "RSI above configured ceiling",
      rationale: "GOOGL: RSI is above the configured ceiling; review the generated snapshot for current return and relative-strength values."
    }
  ],
  aiRecommendations: {
    status: "disabled",
    model: null,
    usage: null,
    headline: "AI synthesis is ready once OPENAI_API_KEY is configured.",
    macroView: "AI synthesis is disabled until OPENAI_API_KEY is configured.",
    recommendations: [],
    portfolioNotes: [
      "No AI call was made for this snapshot.",
      "Rules-based metrics are still available in the Daily Read, Desk Calls, and Momentum Book."
    ],
    openQuestions: [
      "OPENAI_API_KEY is not configured.",
      "Run the refresh after configuring an API key to populate this section."
    ],
    sourceRefs: []
  },
  avoidList: {
    status: "fallback",
    summary: "Stay-away candidates are generated from the lowest-ranked model names once the daily snapshot is available.",
    sectors: [],
    companies: []
  },
  opportunities: [
    {
      symbol: "NVDA",
      name: "NVIDIA Corporation",
      type: "stock",
      sector: "Information Technology",
      score: 86,
      close: 934.12,
      rsi14: 68,
      volumeRatio: 1.42,
      return20: 12.4,
      above50: true,
      above100: true,
      above200: true,
      recentCross: true,
      relativeStrength: 8.8,
      history: [795, 802, 811, 836, 829, 852, 871, 881, 904, 934],
      tags: ["50/200 uptrend", "Volume support", "Leadership"]
    },
    {
      symbol: "XLK",
      name: "Technology Select Sector SPDR Fund",
      type: "etf",
      sector: "Sector",
      score: 79,
      close: 214.18,
      rsi14: 63,
      volumeRatio: 1.18,
      return20: 6.1,
      above50: true,
      above100: true,
      above200: true,
      recentCross: false,
      relativeStrength: 3.3,
      history: [199, 201, 203, 204, 205, 207, 208, 210, 212, 214],
      tags: ["Sector leadership", "Clean trend"]
    },
    {
      symbol: "JPM",
      name: "JPMorgan Chase & Co.",
      type: "stock",
      sector: "Financials",
      score: 71,
      close: 197.4,
      rsi14: 59,
      volumeRatio: 1.08,
      return20: 4.7,
      above50: true,
      above100: true,
      above200: true,
      recentCross: false,
      relativeStrength: 2.4,
      history: [184, 185, 186, 188, 190, 191, 193, 194, 196, 197],
      tags: ["Rates sensitive", "Financial leadership"]
    }
  ],
  macro: [
    { label: "10Y Treasury", value: "4.58%", delta: "+7 bps" },
    { label: "2Y Treasury", value: "4.94%", delta: "+5 bps" },
    { label: "Fed Funds", value: "5.33%", delta: "Flat" },
    { label: "Unemployment", value: "3.9%", delta: "+0.1 pp" },
    { label: "CPI YoY", value: "3.5%", delta: "+0.3 pp" },
    { label: "GDP QoQ", value: "1.6%", delta: "-1.8 pp" }
  ],
  calendar: [
    { date: "2026-05-08", time: "8:30 AM ET", event: "Employment Situation", source: "BLS", importance: "High" },
    { date: "2026-05-13", time: "8:30 AM ET", event: "Consumer Price Index", source: "BLS", importance: "High" },
    { date: "2026-05-29", time: "8:30 AM ET", event: "Personal Income and Outlays", source: "BEA", importance: "High" },
    { date: "2026-06-17", time: "2:00 PM ET", event: "FOMC Rate Decision", source: "Federal Reserve", importance: "High" }
  ],
  sources: []
};

const state = {
  snapshot: fallbackSnapshot
};

const $ = (selector) => document.querySelector(selector);

function on(selector, eventName, handler) {
  const element = $(selector);
  if (element) element.addEventListener(eventName, handler);
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

function formatDate(value) {
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date(value));
}

function formatShortDate(value) {
  if (!value) return "Date unavailable";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(new Date(value));
}

function pct(value) {
  const number = Number(value || 0);
  return `${number >= 0 ? "+" : ""}${number.toFixed(2)}%`;
}

function hasModelRank(item) {
  return Number.isFinite(Number(item?.modelRank));
}

function sortedSourceArticles() {
  return (state.snapshot.sources || [])
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

function sourceRefMap() {
  return new Map(sortedSourceArticles().map((article, index) => [`S${index + 1}`, article]));
}

function compactSourceName(value) {
  return String(value || "")
    .replace(/\s*\|\s*.*/, "")
    .replace(/\s*-\s*.*$/, "")
    .replace(/\s+Research$/, "")
    .trim();
}

function sourceRefLabel(ref, symbol, researchSources) {
  const research = String(ref).match(/^S(\d+)$/);
  if (research) {
    const article = researchSources.get(ref);
    return compactSourceName(article?.sourceName) || `Research Source ${research[1]}`;
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

function sourceRefLabels(refs, symbol, researchSources) {
  return [...new Set((refs || []).map((ref) => sourceRefLabel(ref, symbol, researchSources)).filter(Boolean))];
}

function money(value) {
  if (!Number.isFinite(Number(value))) return "n/a";
  return Number(value).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: Number(value) > 100 ? 2 : 2
  });
}

function renderList(target, items) {
  target.innerHTML = "";
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    target.appendChild(li);
  });
}

function sparkline(values) {
  const width = 320;
  const height = 52;
  const clean = values.filter((value) => Number.isFinite(Number(value))).map(Number);
  if (clean.length < 2) return "";
  const min = Math.min(...clean);
  const max = Math.max(...clean);
  const span = max - min || 1;
  const step = width / (clean.length - 1);
  const points = clean
    .map((value, index) => {
      const x = index * step;
      const y = height - ((value - min) / span) * (height - 8) - 4;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return `
    <svg class="spark" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">
      <polyline points="${points}" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></polyline>
    </svg>
  `;
}

function renderMarketStrip() {
  const strip = $("#marketStrip");
  strip.innerHTML = "";
  state.snapshot.marketStrip.slice(0, 6).forEach((item) => {
    const tile = document.createElement("div");
    tile.className = "ticker-tile";
    tile.innerHTML = `
      <span>${esc(item.label || item.symbol)}</span>
      <strong>${esc(item.symbol)} ${money(item.price)}</strong>
      <em class="${Number(item.changePct) < 0 ? "negative" : ""}">${pct(item.changePct)}</em>
    `;
    strip.appendChild(tile);
  });
}

function renderNote() {
  const note = state.snapshot.note || fallbackSnapshot.note;
  $("#asOfDate").textContent = `As of ${formatDate(state.snapshot.generatedAt)}`;
  $("#noteHeadline").textContent = note.headline;
  $("#noteBody").textContent = note.body;
  renderList($("#changedList"), note.changed || []);
  renderList($("#watchList"), note.watch || []);
}

function renderRecommendations() {
  const list = $("#recommendationList");
  const recommendations = state.snapshot.recommendations || [];
  list.innerHTML = "";

  if (!recommendations.length) {
    list.innerHTML = `<div class="empty-state">No desk calls are available in this snapshot.</div>`;
    return;
  }

  recommendations.slice(0, 6).forEach((item) => {
    const labelClass = item.label === "RSI Above Limit" || item.label === "Model Risk Check" ? "risk" : item.label === "Near Threshold" ? "watch" : "";
    const card = document.createElement("article");
    card.className = "recommendation";
    card.innerHTML = `
      <span class="rec-label ${labelClass}">${esc(item.label)}</span>
      <strong>${esc(item.symbol)} · ${esc(item.title)}</strong>
      <p>${esc(item.rationale)}</p>
    `;
    list.appendChild(card);
  });
}

function renderAiRecommendations() {
  const ai = state.snapshot.aiRecommendations || fallbackSnapshot.aiRecommendations;
  const cost = ai.usage?.estimatedCostUsd;
  const modelSummary = state.snapshot.model;
  const modelText = modelSummary?.status === "ready" ? ` · ${modelSummary.scoredCount} model-scored` : "";
  const researchSources = sourceRefMap();
  $("#aiMeta").textContent =
    ai.status === "ready"
      ? `Generated by ${ai.model || "AI"}${Number.isFinite(cost) ? ` · ~$${cost.toFixed(4)}` : ""}${modelText}`
      : "Configure OPENAI_API_KEY";
  $("#aiHeadline").textContent = ai.headline || "AI synthesis unavailable";
  $("#aiMacroView").textContent = ai.macroView || "";

  const list = $("#aiRecommendationList");
  list.innerHTML = "";

  if (!ai.recommendations?.length) {
    list.innerHTML = `<div class="empty-state">Run the refresh with OPENAI_API_KEY to generate macro-aware AI calls.</div>`;
  } else {
    ai.recommendations.slice(0, 6).forEach((item) => {
      const card = document.createElement("article");
      card.className = "ai-rec";
      const setup = item.setup || item.rationale || "";
      const whyNow = item.whyNow || item.macroLink || "";
      const companyOverview = item.companyOverview || "";
      const marketCap = item.marketCap || item.marketCapText || "";
      const earningsContext = item.earningsContext || "";
      const recentNews = item.recentNews || "";
      const macroEvidence = item.macroEvidence || item.macroLink || "";
      const modelEvidence = item.modelEvidence || "";
      const technicalEvidence = item.technicalEvidence || item.momentumEvidence || "";
      const invalidation = item.invalidation || "";
      const refLabels = sourceRefLabels(item.sourceRefs, item.symbol, researchSources);
      card.innerHTML = `
        <header>
          <div>
            <strong>${esc(item.symbol)} · ${esc(item.action)}</strong>
            <small>${esc(whyNow)}</small>
          </div>
          <span class="conviction">${esc(item.conviction || "Review")}</span>
        </header>
        <p>${esc(setup)}</p>
        ${
          companyOverview || marketCap || earningsContext || recentNews
            ? `<div class="company-context">
                ${companyOverview ? `<small><strong>Company:</strong> ${esc(companyOverview)}</small>` : ""}
                ${marketCap ? `<small><strong>Market Cap:</strong> ${esc(marketCap)}</small>` : ""}
                ${earningsContext ? `<small><strong>Earnings:</strong> ${esc(earningsContext)}</small>` : ""}
                ${recentNews ? `<small><strong>News:</strong> ${esc(recentNews)}</small>` : ""}
              </div>`
            : ""
        }
        <div class="ai-evidence">
          <small><strong>Macro:</strong> ${esc(macroEvidence)}</small>
          ${modelEvidence ? `<small><strong>Model:</strong> ${esc(modelEvidence)}</small>` : ""}
          <small><strong>Technical:</strong> ${esc(technicalEvidence)}</small>
        </div>
        <small><strong>Risk:</strong> ${esc(item.risk || "")}</small>
        ${invalidation ? `<small><strong>Invalidation:</strong> ${esc(invalidation)}</small>` : ""}
        ${refLabels.length ? `<div class="source-ref-row">${refLabels.map((label) => `<span>${esc(label)}</span>`).join("")}</div>` : ""}
      `;
      list.appendChild(card);
    });
  }

  renderList($("#aiPortfolioNotes"), ai.portfolioNotes || []);
  renderList($("#aiOpenQuestions"), ai.openQuestions || []);
}

function renderAvoidList() {
  const avoid = state.snapshot.avoidList || fallbackSnapshot.avoidList;
  const sectorsTarget = $("#avoidSectors");
  const companiesTarget = $("#avoidList");
  $("#avoidSummary").textContent = avoid.summary || "Lowest-ranked model names are unavailable in this snapshot.";
  sectorsTarget.innerHTML = "";
  companiesTarget.innerHTML = "";

  const sectors = avoid.sectors || [];
  if (sectors.length) {
    sectors.slice(0, 4).forEach((item) => {
      const card = document.createElement("article");
      card.className = "avoid-sector";
      const examples = item.examples?.length ? `Examples: ${item.examples.join(", ")}` : "";
      card.innerHTML = `
        <span>${esc(item.count != null ? `${item.count} bottom names` : "Bottom-ranked cluster")}</span>
        <strong>${esc(item.sector || "Sector review")}</strong>
        <p>${esc(item.rationale || examples)}</p>
      `;
      sectorsTarget.appendChild(card);
    });
  }

  const companies = avoid.companies || [];
  if (!companies.length) {
    companiesTarget.innerHTML = `<div class="empty-state">Run the model refresh to populate lowest-ranked companies.</div>`;
    return;
  }

  companies.slice(0, 8).forEach((item) => {
    const card = document.createElement("article");
    card.className = "avoid-card";
    const meta = [item.sector, item.marketCap].filter(Boolean).join(" · ");
    const evidence = item.modelEvidence || item.rationale || item.reason || "Lowest-ranked by the model.";
    const risk = Array.isArray(item.riskFlags) ? item.riskFlags.join("; ") : item.risk || item.riskFlags || "";
    card.innerHTML = `
      <header>
        <div>
          <strong>${esc(item.symbol)}</strong>
          <span>${esc(item.name || meta || "Model-ranked company")}</span>
        </div>
        <span class="avoid-rank">#${esc(item.modelRank || "-")}</span>
      </header>
      ${meta ? `<small>${esc(meta)}</small>` : ""}
      <p>${esc(evidence)}</p>
      ${risk ? `<small><strong>Risk flags:</strong> ${esc(risk)}</small>` : ""}
    `;
    companiesTarget.appendChild(card);
  });
}

function renderSectorPerformance() {
  const grid = $("#sectorGrid");
  const sectors = state.snapshot.sectorPerformance || [];
  grid.innerHTML = "";

  if (!sectors.length) {
    grid.innerHTML = `<div class="empty-state">Run the refresh script to populate sector performance.</div>`;
    return;
  }

  sectors.forEach((item) => {
    const tile = document.createElement("article");
    tile.className = "sector-tile";
    const trendTags = [
      item.above50 ? "Above 50D" : "Below 50D",
      item.above200 ? "Above 200D" : "Below 200D",
      Number(item.relative30d) >= 0 ? "30D > SPY" : "30D < SPY",
      Number(item.rsi14) > 76 ? "RSI > 76" : `RSI ${Math.round(item.rsi14 || 0)}`
    ];
    tile.innerHTML = `
      <div class="sector-head">
        <div>
          <strong>${esc(item.sector)}</strong>
          <span>${esc(item.symbol)}</span>
        </div>
        <span class="conviction">${pct(item.change30d)}</span>
      </div>
      ${sparkline(item.history || [])}
      <div class="sector-main">
        <div><span>30D</span><strong>${pct(item.change30d)}</strong></div>
        <div><span>YTD</span><strong>${pct(item.ytd)}</strong></div>
      </div>
      <div class="sector-mini">
        <div><span>1D</span><strong>${pct(item.change1d)}</strong></div>
        <div><span>5D</span><strong>${pct(item.change5d)}</strong></div>
        <div><span>vs SPY 30D</span><strong>${pct(item.relative30d)}</strong></div>
      </div>
      <div class="sector-badges">${trendTags.map((tag) => `<span class="tag">${esc(tag)}</span>`).join("")}</div>
    `;
    grid.appendChild(tile);
  });
}

function renderOpportunities() {
  const list = $("#opportunityList");
  const matches = [...state.snapshot.opportunities]
    .sort((a, b) => {
      const aModel = hasModelRank(a);
      const bModel = hasModelRank(b);
      if (aModel && bModel) return Number(a.modelRank) - Number(b.modelRank);
      if (aModel !== bModel) return aModel ? -1 : 1;
      return b.score - a.score;
    })
    .slice(0, 18);

  $("#signalCount").textContent = `${matches.length} signal${matches.length === 1 ? "" : "s"}`;
  $("#bookMeta").textContent =
    state.snapshot.model?.status === "ready"
      ? `${state.snapshot.model.scoredCount} model-scored · ${state.snapshot.opportunities.length} screened`
      : `${state.snapshot.opportunities.length} screened`;
  list.innerHTML = "";

  if (!matches.length) {
    list.innerHTML = `<div class="empty-state">No momentum names are available in this snapshot.</div>`;
    return;
  }

  matches.forEach((item) => {
    const article = document.createElement("article");
    article.className = "opportunity";
    const modelRanked = hasModelRank(item);
    const badges = [
      ...(modelRanked ? item.modelReasons || [] : []),
      ...(item.tags || []),
      ...(item.riskFlags || [])
    ].slice(0, 6);
    article.innerHTML = `
      <div class="opp-head">
        <div>
          <strong>${esc(item.symbol)}</strong>
          <span>${esc(item.name || item.sector || item.type)}</span>
        </div>
        <div class="score ${modelRanked ? "model-score" : ""}">
          ${modelRanked ? `<span>#${esc(item.modelRank)}</span><small>${Number(item.modelPercentile || 0).toFixed(0)}%</small>` : Math.round(item.score)}
        </div>
      </div>
      ${sparkline(item.history || [])}
      <div class="signal-grid">
        <div><span>Close</span><strong>${money(item.close)}</strong></div>
        <div><span>${modelRanked ? "60D vs SPY" : "20D return"}</span><strong>${pct(modelRanked ? item.relativeReturn60VsSpy : item.return20)}</strong></div>
        <div><span>RSI 14</span><strong>${Number(item.rsi14 || 0).toFixed(0)}</strong></div>
        <div><span>${modelRanked ? "Rules" : "Volume"}</span><strong>${modelRanked ? Math.round(item.rulesScore || 0) : `${Number(item.volumeRatio || 0).toFixed(2)}x`}</strong></div>
      </div>
      <div class="tags">${badges.map((tag) => `<span class="tag">${esc(tag)}</span>`).join("")}</div>
    `;
    list.appendChild(article);
  });
}

function renderCalendar() {
  const target = $("#calendarList");
  target.innerHTML = "";
  state.snapshot.calendar.slice(0, 8).forEach((event) => {
    const item = document.createElement("div");
    item.className = "event";
    item.innerHTML = `
      <div>
        <strong>${new Date(`${event.date}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" })}</strong>
        <span>${esc(event.time || "")}</span>
      </div>
      <div>
        <strong>${esc(event.event)}</strong>
        <span>${esc(event.source || "")}</span>
        <em>${esc(event.importance || "Medium")}</em>
      </div>
    `;
    target.appendChild(item);
  });
}

function renderMacro() {
  const grid = $("#macroGrid");
  grid.innerHTML = "";
  state.snapshot.macro.slice(0, 9).forEach((item) => {
    const card = document.createElement("div");
    card.className = "macro-card";
    card.innerHTML = `<span>${esc(item.label)}</span><strong>${esc(item.value)}</strong><span>${esc(item.delta || "")}</span>`;
    grid.appendChild(card);
  });
}

function renderSources() {
  const sourceTape = $("#sourceTape");
  const sources = state.snapshot.sources || [];
  const articleCount = sources.reduce((total, source) => total + (source.articles?.length || 0), 0);
  $("#sourceCount").textContent = `${sources.length} sources · ${articleCount} articles`;
  sourceTape.innerHTML = "";

  if (!sources.length) {
    sourceTape.innerHTML = `<div class="empty-state">Run the refresh script to populate publication checks.</div>`;
    return;
  }

  sources.forEach((source) => {
    const item = document.createElement("div");
    item.className = "source-item";
    const articles = source.articles || [];
    item.innerHTML = `
      <div>
        <strong>${esc(source.category || "Source")}</strong>
        <span>${esc(source.trust || "")}</span>
      </div>
      <div>
        <a href="${esc(source.url)}" target="_blank" rel="noreferrer">${esc(source.title || source.name)}</a>
        <span>${esc(source.summary || source.notes || "")}</span>
        ${
          articles.length
            ? `<div class="source-articles">${articles
                .map(
                  (article) => `
                    <a class="source-article" href="${esc(article.url)}" target="_blank" rel="noreferrer">
                      <strong>${esc(article.title)}</strong>
                      <span>${esc(article.sourceName || source.name)} · ${esc(formatShortDate(article.publishedAt))}</span>
                    </a>
                  `
                )
                .join("")}</div>`
            : ""
        }
      </div>
    `;
    sourceTape.appendChild(item);
  });
}

function render() {
  renderNote();
  renderMarketStrip();
  renderAiRecommendations();
  renderAvoidList();
  renderSectorPerformance();
  renderRecommendations();
  renderOpportunities();
  renderCalendar();
  renderMacro();
  renderSources();
}

function wireControls() {
  on("#refreshView", "click", loadSnapshot);
}

async function loadSnapshot() {
  try {
    const response = await fetch(`data/snapshot.json?ts=${Date.now()}`);
    if (!response.ok) throw new Error(`Snapshot unavailable: ${response.status}`);
    state.snapshot = await response.json();
  } catch (error) {
    console.warn(error);
    state.snapshot = fallbackSnapshot;
  }
  render();
}

wireControls();
loadSnapshot();
