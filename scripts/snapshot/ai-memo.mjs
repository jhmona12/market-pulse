function fixedNumber(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? Number(number.toFixed(digits)) : null;
}

function buildAiMemoInputPayload({
  generatedAt = new Date().toISOString(),
  modelSummary,
  macro,
  calendar,
  marketIntelligence,
  sourceTape,
  officialMacroSourceRefs,
  companyContexts,
  companySourceRefs,
  sources,
  deskRecommendations,
  sectorPerformance,
  modelCandidates,
  longHorizonContext,
  avoidCandidates,
  avoidSectors,
  opportunities,
  compactCandidate,
  macroReleaseLookbackHours,
  validRecommendationSymbols
}) {
  return {
    generatedAt,
    model: modelSummary,
    macro,
    upcomingEvents: (calendar || []).slice(0, 8),
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
    sourceStatus: (sources || []).map(({ name, url, category, trust, ok, articleCount, summary }) => ({
      name,
      url,
      category,
      trust,
      ok,
      articleCount,
      summary
    })),
    deskRecommendations,
    sectorPerformance: (sectorPerformance || []).map(({ sector, symbol, change1d, change5d, change30d, ytd, relative30d, above50, above200, rsi14 }) => ({
      sector,
      symbol,
      change1d: fixedNumber(change1d, 2),
      change5d: fixedNumber(change5d, 2),
      change30d: fixedNumber(change30d, 2),
      ytd: fixedNumber(ytd, 2),
      relative30d: fixedNumber(relative30d, 2),
      above50,
      above200,
      rsi14: fixedNumber(rsi14, 0)
    })),
    modelCandidates,
    longHorizonContext,
    avoidCandidates,
    avoidSectors,
    topMomentum: (opportunities || []).slice(0, 18).map(compactCandidate),
    extendedMomentum: (opportunities || [])
      .filter((item) => item.score >= 70 && item.rsi14 > 76)
      .slice(0, 8)
      .map(compactCandidate),
    etfMomentum: (opportunities || [])
      .filter((item) => item.type === "etf")
      .slice(0, 10)
      .map(compactCandidate),
    validRecommendationSymbols: [...new Set((validRecommendationSymbols || (companyContexts || []).map((item) => item.symbol)).filter(Boolean))]
  };
}

function buildAiRecommendationResponseSchema({ validSymbols = [], validAvoidSymbols = [], sourceRefIds = [] } = {}) {
  const sourceRefSchema = sourceRefIds.length
    ? { type: "string", enum: sourceRefIds }
    : { type: "string" };

  return {
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
  };
}

export { buildAiMemoInputPayload, buildAiRecommendationResponseSchema };
