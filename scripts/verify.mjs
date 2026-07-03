import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { validateDashboardSchemas } from "./snapshot/schemas.mjs";
import { sortedSourceArticles } from "../src/dashboard/source-refs.js";

const root = process.cwd();
const node = process.execPath;

function fail(message) {
  console.error(message);
  process.exitCode = 1;
}

function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, encoding: "utf8" });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.status !== 0) fail(`${command} ${args.join(" ")} failed with exit code ${result.status}`);
}

function filesIn(directory, predicate) {
  const results = [];
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry);
    const info = statSync(path);
    if (info.isDirectory()) results.push(...filesIn(path, predicate));
    else if (predicate(path)) results.push(path);
  }
  return results;
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(join(root, path), "utf8"));
  } catch (error) {
    fail(`${path} is not valid JSON: ${error.message}`);
    return null;
  }
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function collectSourceRefs(value, refs = []) {
  if (!value || typeof value !== "object") return refs;
  if (Array.isArray(value.sourceRefs)) refs.push(...value.sourceRefs);
  if (value.sourceRef) refs.push(value.sourceRef);
  Object.values(value).forEach((child) => collectSourceRefs(child, refs));
  return refs;
}

function collectStrings(value, strings = []) {
  if (typeof value === "string") strings.push(value);
  else if (Array.isArray(value)) value.forEach((child) => collectStrings(child, strings));
  else if (value && typeof value === "object") Object.values(value).forEach((child) => collectStrings(child, strings));
  return strings;
}

function hasRepeatedTickerList(value) {
  if (typeof value !== "string") return false;
  const matches = value.match(/\b[A-Z][A-Z0-9.]{0,5}(?:,\s*[A-Z][A-Z0-9.]{0,5}){2,}\b/g) || [];
  return matches.some((match) => {
    const tickers = match.split(",").map((item) => item.trim()).filter(Boolean);
    return new Set(tickers).size < tickers.length;
  });
}

run(node, ["--check", "app.js"]);
run(node, ["--check", "scripts/verify.mjs"]);
run(node, ["--check", "scripts/update-data.mjs"]);
run(node, ["--check", "scripts/update-macro-calendar.mjs"]);
run(node, ["--check", "scripts/local-dashboard-server.mjs"]);
run(node, ["--check", "scripts/configure-ticker-backend.mjs"]);
run(node, ["--check", "scripts/write-refresh-status.mjs"]);
run(node, ["--check", "scripts/check-refresh-window.mjs"]);
run(node, ["--check", "scripts/monitor-refreshes.mjs"]);
run(node, ["--check", "scripts/refresh/confirm-live-pages.mjs"]);
run(node, ["--check", "scripts/ingest/sources.mjs"]);
run(node, ["--check", "scripts/snapshot/ai-memo.mjs"]);
run(node, ["--check", "scripts/snapshot/schemas.mjs"]);
filesIn(join(root, "src"), (path) => path.endsWith(".js")).forEach((path) => run(node, ["--check", path]));
run(node, ["--test", ...filesIn(join(root, "tests"), (path) => path.endsWith(".test.mjs"))]);

const python = process.env.PYTHON || "python3";
const pythonFiles = [
  ...filesIn(join(root, "scripts/modeling"), (path) => path.endsWith(".py")),
  "analysis/model-monitoring/run_recent_decile_backtest.py"
];
run(python, ["-m", "py_compile", ...pythonFiles]);

const requiredJsonFiles = [
  "config/runtime.json",
  "data/snapshot.json",
  "data/model-scorebook.json",
  "data/model-monitoring.json",
  "data/long-horizon-research.json",
  "data/macro-calendar.json",
  "data/refresh-status.json",
  "data/refresh-ledger.json"
];

for (const file of requiredJsonFiles) readJson(file);

const snapshot = readJson("data/snapshot.json");
const scorebook = readJson("data/model-scorebook.json");
const monitoring = readJson("data/model-monitoring.json");
const longHorizonResearch = readJson("data/long-horizon-research.json");
const macroCalendar = readJson("data/macro-calendar.json");
const refreshLedger = readJson("data/refresh-ledger.json");
const schemaErrors = validateDashboardSchemas(root);
if (schemaErrors.length) fail(`dashboard schema validation failed:\n${schemaErrors.slice(0, 25).join("\n")}`);
if (!Array.isArray(snapshot?.opportunities)) fail("data/snapshot.json is missing opportunities[]");
if (!Array.isArray(scorebook?.rows)) fail("data/model-scorebook.json is missing rows[]");
if (!Array.isArray(monitoring?.currentTopDecile)) fail("data/model-monitoring.json is missing currentTopDecile[]");
if (!Array.isArray(longHorizonResearch?.topCandidates)) fail("data/long-horizon-research.json is missing topCandidates[]");
if (!Array.isArray(longHorizonResearch?.rows)) fail("data/long-horizon-research.json is missing rows[]");
if (!Array.isArray(longHorizonResearch?.labelSummaries) || !longHorizonResearch.labelSummaries.length) {
  fail("data/long-horizon-research.json is missing labelSummaries[]");
}
if (!Array.isArray(macroCalendar?.events) || !macroCalendar.events.length) fail("data/macro-calendar.json is missing events[]");
if (refreshLedger?.version !== 1 || !Array.isArray(refreshLedger?.successfulTargets)) {
  fail("data/refresh-ledger.json is missing version 1 successfulTargets[]");
}
const badLedgerEntries = refreshLedger.successfulTargets.filter((entry) => {
  if (typeof entry === "string") return !/^\d{4}-\d{2}-\d{2}-(morning|evening)$/.test(entry);
  return !/^\d{4}-\d{2}-\d{2}-(morning|evening)$/.test(String(entry?.targetKey || ""));
});
if (badLedgerEntries.length) fail("data/refresh-ledger.json contains malformed target keys");

if (snapshot.marketDataStatus?.status !== "fresh") fail(`snapshot marketDataStatus is ${snapshot.marketDataStatus?.status || "missing"}`);
if (scorebook.marketDataStatus?.status !== "fresh") fail(`scorebook marketDataStatus is ${scorebook.marketDataStatus?.status || "missing"}`);
if (snapshot.model?.status !== "ready") fail(`snapshot model status is ${snapshot.model?.status || "missing"}`);
if (scorebook.status !== "ready") fail(`scorebook status is ${scorebook.status || "missing"}`);
if (longHorizonResearch.status !== "ready") fail(`long-horizon status is ${longHorizonResearch.status || "missing"}`);
if (snapshot.model?.expectedAsOfDate && snapshot.marketDataStatus?.asOfDate !== snapshot.model.expectedAsOfDate) {
  fail(`snapshot market data is stale: ${snapshot.marketDataStatus?.asOfDate || "missing"} vs expected ${snapshot.model.expectedAsOfDate}`);
}

const ranks = scorebook.rows.map((row) => numberOrNull(row.modelRank)).filter((rank) => rank !== null).sort((a, b) => a - b);
if (ranks.length !== scorebook.rows.length) fail("scorebook contains rows without numeric modelRank");
if (ranks.length && (ranks[0] !== 1 || ranks.at(-1) !== scorebook.rows.length)) {
  fail(`scorebook rank range is ${ranks[0]}-${ranks.at(-1)} for ${scorebook.rows.length} rows`);
}
for (let index = 0; index < ranks.length; index += 1) {
  if (ranks[index] !== index + 1) {
    fail(`scorebook rank sequence breaks at position ${index + 1}`);
    break;
  }
}

const longRanks = longHorizonResearch.rows.map((row) => numberOrNull(row.longModelRank)).filter((rank) => rank !== null).sort((a, b) => a - b);
if (longRanks.length !== longHorizonResearch.rows.length) fail("long-horizon rows contain entries without numeric longModelRank");
if (longRanks.length && (longRanks[0] !== 1 || longRanks.at(-1) !== longHorizonResearch.rows.length)) {
  fail(`long-horizon rank range is ${longRanks[0]}-${longRanks.at(-1)} for ${longHorizonResearch.rows.length} rows`);
}
if (scorebook.asOfDate && longHorizonResearch.asOfDate && scorebook.asOfDate !== longHorizonResearch.asOfDate) {
  fail(`long-horizon asOfDate ${longHorizonResearch.asOfDate} does not match scorebook ${scorebook.asOfDate}`);
}

const topDecileCutoff = Math.ceil(scorebook.rows.length * 0.1);
const expectedTopDecile = scorebook.rows
  .filter((row) => Number(row.modelRank) <= topDecileCutoff)
  .map((row) => row.symbol)
  .sort();
const actualTopDecile = monitoring.currentTopDecile.map((row) => row.symbol).sort();
if (monitoring.topDecileCutoff !== topDecileCutoff) fail(`model-monitoring topDecileCutoff is ${monitoring.topDecileCutoff}, expected ${topDecileCutoff}`);
if (JSON.stringify(expectedTopDecile) !== JSON.stringify(actualTopDecile)) fail("model-monitoring currentTopDecile does not match scorebook model ranks");

const stopRequiredSetups = new Set(["momentum_confirmed", "model_rebound_watch", "model_ranked_not_momentum_confirmed"]);
const missingStops = scorebook.rows.filter((row) => {
  const setupRequiresStop = stopRequiredSetups.has(row.setupType);
  const topDecileRequiresStop = Number(row.modelRank) <= topDecileCutoff;
  return (setupRequiresStop || topDecileRequiresStop) && numberOrNull(row.stopSellPrice) === null;
});
if (missingStops.length) fail(`${missingStops.length} scorebook rows that should show stop values are missing stopSellPrice`);

const badActivations = scorebook.rows.filter((row) => row.setupType !== "model_rebound_watch" && numberOrNull(row.reboundActivationPrice) !== null);
if (badActivations.length) fail(`${badActivations.length} non-rebound rows expose reboundActivationPrice`);
const missingActivations = scorebook.rows.filter((row) => row.setupType === "model_rebound_watch" && numberOrNull(row.reboundActivationPrice) === null);
if (missingActivations.length) fail(`${missingActivations.length} Model Rebound Watch rows are missing reboundActivationPrice`);

const serializedDashboard = JSON.stringify({ snapshot, scorebook, monitoring, longHorizonResearch });
if (/[\u3400-\u9FFF]/.test(serializedDashboard)) {
  fail("dashboard data contains non-English CJK characters; AI/prompt output should be English-only");
}
[
  { token: "NaN", pattern: /(^|[^A-Za-z0-9])NaN([^A-Za-z0-9]|$)/ },
  { token: "undefined", pattern: /(^|[^A-Za-z0-9])undefined([^A-Za-z0-9]|$)/ },
  { token: "Infinity", pattern: /(^|[^A-Za-z0-9])Infinity([^A-Za-z0-9]|$)/ },
  { token: "Activation $0.00", pattern: /Activation \$0\.00/ },
  { token: "Activation 0.00", pattern: /Activation 0\.00/ }
].forEach(({ token, pattern }) => {
  if (pattern.test(serializedDashboard)) fail(`dashboard data contains bad placeholder token: ${token}`);
});

const aiMemoText = collectStrings(snapshot.aiRecommendations || {}).join(" ");
if (/\bstop(?:-loss)?(?:\s+(?:level|price))?\s*(?:of|at|near|below|under|:)?\s*\$?\d+(?:\.\d+)?\b/i.test(aiMemoText)) {
  fail("AI Strategy Memo contains a stop-loss metric that should be rendered only as a deterministic dashboard label");
}
if (/\bstopSell\b/i.test(aiMemoText)) {
  fail("AI Strategy Memo contains internal stopSell terminology");
}
if (/fed\s*\/\s*yields noise|dominant arbiter|above-avg|coming up in late july|drives tech sentiment|sets the market tone|supports the whole sector|mega-cap reporters|high-profile earnings|activation signals.*\?/i.test(aiMemoText)) {
  fail("AI Strategy Memo contains low-quality shorthand or unsupported broad-read-through language");
}
if (hasRepeatedTickerList(aiMemoText)) {
  fail("AI Strategy Memo contains a repeated ticker list");
}

const validSourceRefs = new Set(sortedSourceArticles(snapshot.sources).map((_, index) => `S${index + 1}`));
(snapshot.marketIntelligence?.officialMacro?.releases || []).forEach((release) => {
  if (release.id) validSourceRefs.add(release.id);
});
const badRefs = collectSourceRefs(snapshot.aiRecommendations || {}).filter((ref) => {
  if (validSourceRefs.has(ref)) return false;
  return !/^C\d+-(N\d+|IR|MARKETCAP|EARNINGS)$/.test(String(ref));
});
if (badRefs.length) fail(`AI Strategy Memo contains unresolved source refs: ${[...new Set(badRefs)].slice(0, 12).join(", ")}`);

const pagesWorkflow = readFileSync(join(root, ".github/workflows/pages.yml"), "utf8");
if (!pagesWorkflow.includes("cp -R src public/src")) {
  fail(".github/workflows/pages.yml does not publish browser modules from src/");
}
if (!pagesWorkflow.includes("data/model-monitoring.json public/data/model-monitoring.json")) {
  fail(".github/workflows/pages.yml does not publish data/model-monitoring.json");
}
if (!pagesWorkflow.includes("data/long-horizon-research.json public/data/long-horizon-research.json")) {
  fail(".github/workflows/pages.yml does not publish data/long-horizon-research.json");
}
if (!pagesWorkflow.includes("data/refresh-ledger.json public/data/refresh-ledger.json")) {
  fail(".github/workflows/pages.yml does not publish data/refresh-ledger.json");
}

const refreshWorkflow = readFileSync(join(root, ".github/workflows/refresh-data.yml"), "utf8");
if (!refreshWorkflow.includes("REDDIT_CLIENT_ID")) {
  fail(".github/workflows/refresh-data.yml does not pass Reddit OAuth secrets to the refresh script");
}
if (!refreshWorkflow.includes("actions/cache/restore@v4") || !refreshWorkflow.includes("actions/cache/save@v4")) {
  fail(".github/workflows/refresh-data.yml does not restore/save data/cache runtime state");
}
if (!refreshWorkflow.includes("timezone: \"America/Los_Angeles\"")) {
  fail(".github/workflows/refresh-data.yml does not use Pacific timezone-aware schedules");
}
if (!refreshWorkflow.includes("node scripts/check-refresh-window.mjs")) {
  fail(".github/workflows/refresh-data.yml does not use scripts/check-refresh-window.mjs");
}
if (!refreshWorkflow.includes("data/refresh-ledger.json")) {
  fail(".github/workflows/refresh-data.yml does not preserve or publish data/refresh-ledger.json");
}
const deployIndex = refreshWorkflow.indexOf("name: Deploy Pages");
const liveConfirmIndex = refreshWorkflow.indexOf("name: Probe live Pages status");
const commitPublishedIndex = refreshWorkflow.indexOf("name: Commit published refresh outputs");
if (deployIndex === -1 || liveConfirmIndex === -1 || commitPublishedIndex === -1) {
  fail(".github/workflows/refresh-data.yml must deploy, probe the live Pages status, then commit published refresh outputs");
} else if (!(deployIndex < liveConfirmIndex && liveConfirmIndex < commitPublishedIndex)) {
  fail(".github/workflows/refresh-data.yml must not commit the successful refresh ledger before Pages deploy and live probing");
}
if (!refreshWorkflow.includes("node scripts/refresh/confirm-live-pages.mjs")) {
  fail(".github/workflows/refresh-data.yml must use the tested non-blocking live Pages probe");
}
if (!refreshWorkflow.includes("PAGES_PUBLISH_STATUS: published") || !refreshWorkflow.includes("PAGES_PUBLISH_STATUS: not_published")) {
  fail(".github/workflows/refresh-data.yml does not record published/not_published refresh status states");
}
if (!refreshWorkflow.includes("group: pages")) {
  fail(".github/workflows/refresh-data.yml must share the Pages concurrency group with the deploy workflow");
}
if (refreshWorkflow.includes("data/model-reference-cache.json") || refreshWorkflow.includes("data/market-cap-cache.json") || refreshWorkflow.includes("data/reddit-tape-cache.json") || refreshWorkflow.includes("data/deeper-read-history.json")) {
  fail(".github/workflows/refresh-data.yml should not commit legacy runtime cache files");
}
if (!refreshWorkflow.includes("cp -R src public/src")) {
  fail(".github/workflows/refresh-data.yml does not publish browser modules from src/");
}

const monitorWorkflow = readFileSync(join(root, ".github/workflows/monitor-refresh.yml"), "utf8");
if (!monitorWorkflow.includes("node scripts/monitor-refreshes.mjs")) {
  fail(".github/workflows/monitor-refresh.yml does not run scripts/monitor-refreshes.mjs");
}
if (!monitorWorkflow.includes("LIVE_DASHBOARD_BASE_URL")) {
  fail(".github/workflows/monitor-refresh.yml does not check the live GitHub Pages dashboard");
}

const updateDataSource = readFileSync(join(root, "scripts/update-data.mjs"), "utf8");
if (/query1\.finance\.yahoo\.com\/v8\/finance\/chart|technicalRowScore/.test(updateDataSource)) {
  fail("scripts/update-data.mjs is reintroducing price/technical ownership that belongs in Python");
}

const localServer = readFileSync(join(root, "scripts/local-dashboard-server.mjs"), "utf8");
if (!localServer.includes('"data/model-monitoring.json"')) {
  fail("scripts/local-dashboard-server.mjs does not allow data/model-monitoring.json");
}
if (!localServer.includes('"data/long-horizon-research.json"')) {
  fail("scripts/local-dashboard-server.mjs does not allow data/long-horizon-research.json");
}
if (!localServer.includes('"data/macro-calendar.json"')) {
  fail("scripts/local-dashboard-server.mjs does not allow data/macro-calendar.json");
}
if (!localServer.includes('"data/refresh-ledger.json"')) {
  fail("scripts/local-dashboard-server.mjs does not allow data/refresh-ledger.json");
}
if (!/src\\\/dashboard|src\/dashboard/.test(localServer)) {
  fail("scripts/local-dashboard-server.mjs does not allow browser modules under src/dashboard/");
}

const macroEvents = macroCalendar.events.filter((event) => event?.date && event?.time && event?.event && event?.source);
if (macroEvents.length !== macroCalendar.events.length) fail("data/macro-calendar.json contains incomplete macro events");
const requiredMacroFamilies = ["Employment Situation", "Consumer Price Index", "Producer Price Index", "FOMC Rate Decision"];
requiredMacroFamilies.forEach((family) => {
  if (!macroEvents.some((event) => event.event === family)) fail(`data/macro-calendar.json is missing ${family}`);
});

if (process.exitCode) process.exit(process.exitCode);
console.log("Project verification passed.");
