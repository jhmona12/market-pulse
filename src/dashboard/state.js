export function createInitialState(fallbackSnapshot) {
  return {
    snapshot: fallbackSnapshot,
    activeView: "briefing",
    scorebook: {
      status: "loading",
      generatedAt: null,
      asOfDate: null,
      rows: [],
      rowCount: 0,
      returnNotes: "",
      query: "",
      sector: "",
      sortKey: "modelScore",
      sortDirection: "desc"
    },
    monitoring: {
      status: "loading",
      generatedAt: null,
      asOfDate: null,
      rowCount: 0,
      topDecileCutoff: 0,
      topDecileCount: 0,
      recentEntrantCount: 0,
      trailingReturnStatus: "",
      marketDataStatus: null,
      currentTopDecile: [],
      recentEntrants: [],
      methodology: []
    },
    longHorizon: {
      status: "loading",
      generatedAt: null,
      sourceGeneratedAt: null,
      asOfDate: null,
      modelMetadata: {},
      labelSummaries: [],
      labelComparison: [],
      rows: [],
      rowCount: 0,
      topCandidates: [],
      trends: {},
      baselineComparison: {},
      shapTopFeatures: [],
      methodology: [],
      query: "",
      sector: "",
      sortKey: "longModelRank",
      sortDirection: "asc"
    },
    tickerLab: {
      enabled: true,
      apiReady: false,
      apiBaseUrl: "",
      requiresAccessCode: false,
      loading: false,
      status: "",
      result: null,
      error: null,
      maxTickers: 25
    }
  };
}
