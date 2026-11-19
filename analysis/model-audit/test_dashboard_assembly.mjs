import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { buildModelScorebook, buildLongHorizonResearch, buildModelMonitoring } from "../../scripts/update-data.mjs";
import { validateJsonSchema } from "../../scripts/snapshot/schemas.mjs";

const root = process.cwd();
const experiment = resolve(process.argv[2] || "data/modeling/experiments/2026-11-18");
const folder = join(experiment, "deployment");
const readJson = async (path) => JSON.parse(await readFile(path, "utf8"));
const tactical = await readJson(join(folder, "tactical-scores.json"));
const strategic = await readJson(join(folder, "strategic-scores.json"));
const tuning = await readJson(join(experiment, "tuning_252d/results.json"));
const explanation = await readJson(join(experiment, "tuning_252d/xgboost_rank_sector252_v2_candidate_explainability.json"));
const metadata = tuning.candidate_metadata;
assert.equal(explanation.modelSha256, metadata.model_sha256);
assert.equal(strategic.model.artifactSha256, metadata.model_sha256);
assert.equal(strategic.model.featureVersion, 2);
assert.equal(tactical.asOfDate, strategic.asOfDate);

const stockMetadata = new Map(tactical.rankings.map((row) => [row.symbol, { name: row.name, sector: row.sector }]));
// This is a model-integration test, not a market-cap/news refresh. Unknown fields stay null.
const marketCapCache = { bySymbol: new Map() };
const marketDataStatus = { status: "fresh", asOfDate: tactical.asOfDate, expectedAsOfDate: tactical.asOfDate };
const scorebook = buildModelScorebook({ modelRankings: tactical, stockMetadata, marketCapCache,
  signalsBySymbol: new Map(tactical.rankings.map((row) => [row.symbol, row])), marketDataStatus });
const base = { labelSummaries: [{ label: "Sector-neutral 252D return", description: "Development-window results; not expected returns.",
  testStart: tuning.test_start, testEnd: tuning.test_end, completeCohorts: tuning.common_complete_test_cohorts,
  testCohorts: tuning.test_cohorts, summary: tuning.test_summary }],
  modelMetadata: { featureCount: metadata.feature_count, trainingEndDate: metadata.training_end_date,
    numBoostRound: metadata.num_boost_round, featureVersion: 2, modelSha256: metadata.model_sha256 },
  shapTopFeatures: explanation.topFeatures, methodology: tuning.limitations };
const longHorizon = buildLongHorizonResearch({ base, longHorizonRankings: strategic, modelScorebook: scorebook, stockMetadata, marketCapCache });
const monitoring = buildModelMonitoring({ modelScorebook: scorebook, previousMonitoring: {} });
for (const [artifact, schema] of [[scorebook, "model-scorebook"], [longHorizon, "long-horizon-research"]]) {
  const errors = validateJsonSchema(artifact, await readJson(join(root, `schemas/${schema}.schema.json`)));
  assert.deepEqual(errors, []);
}
assert.equal(scorebook.rowCount, tactical.rankings.length);
assert.ok(scorebook.rowCount >= 450);
assert.equal(longHorizon.rowCount, scorebook.rowCount);
const bySymbol = new Map(scorebook.rows.map((row) => [row.symbol, row]));
for (const [index, row] of scorebook.rows.entries()) {
  assert.equal(row.modelRank, index + 1);
  assert.ok(Number.isFinite(row.modelScore));
  if (row.setupType !== "model_rebound_watch") assert.equal(row.reboundActivationPrice, null);
  else assert.ok(row.reboundActivationPrice > 0);
  if (index < Math.ceil(scorebook.rowCount / 10)) assert.ok(row.stopSellPrice > 0);
}
for (const [index, row] of longHorizon.rows.entries()) {
  assert.equal(row.longModelRank, index + 1);
  const tacticalRow = bySymbol.get(row.symbol);
  assert.equal(row.tacticalModelRank, tacticalRow.modelRank);
  for (const key of ["beta60d", "return7", "return14", "return30", "return60", "return90", "ytdReturn"]) {
    assert.equal(row[key], tacticalRow[key]);
  }
}
assert.equal(monitoring.topDecileCount, Math.ceil(scorebook.rowCount / 10));
await writeFile(join(folder, "model-scorebook.json"), JSON.stringify(scorebook, null, 2) + "\n");
await writeFile(join(folder, "long-horizon-research.json"), JSON.stringify(longHorizon, null, 2) + "\n");
await writeFile(join(folder, "model-monitoring.json"), JSON.stringify(monitoring, null, 2) + "\n");
const result = { status: "passed", asOfDate: tactical.asOfDate, scorebookRows: scorebook.rowCount,
  strategicRows: longHorizon.rowCount, monitorRows: monitoring.topDecileCount,
  candidateExplanationHashMatches: true, schemaValidation: "passed", publicArtifactsWritten: false,
  scope: "Actual pure JS assembly functions and schemas; no news, LLM, market-cap enrichment or GitHub deployment." };
await writeFile(join(folder, "dashboard-assembly-results.json"), JSON.stringify(result, null, 2) + "\n");
console.log(JSON.stringify(result, null, 2));
