import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const root = process.cwd();

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

function sortedSourceArticles(sources) {
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

run("node", ["--check", "app.js"]);
run("node", ["--check", "scripts/verify.mjs"]);
run("node", ["--check", "scripts/update-data.mjs"]);
run("node", ["--check", "scripts/update-macro-calendar.mjs"]);
run("node", ["--check", "scripts/local-dashboard-server.mjs"]);
run("node", ["--check", "scripts/configure-ticker-backend.mjs"]);
run("node", ["--check", "scripts/write-refresh-status.mjs"]);

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
  "data/model-reference-cache.json",
  "data/macro-calendar.json",
  "data/refresh-status.json"
];

for (const file of requiredJsonFiles) readJson(file);

const snapshot = readJson("data/snapshot.json");
const scorebook = readJson("data/model-scorebook.json");
const monitoring = readJson("data/model-monitoring.json");
const macroCalendar = readJson("data/macro-calendar.json");
if (!Array.isArray(snapshot?.opportunities)) fail("data/snapshot.json is missing opportunities[]");
if (!Array.isArray(scorebook?.rows)) fail("data/model-scorebook.json is missing rows[]");
if (!Array.isArray(monitoring?.currentTopDecile)) fail("data/model-monitoring.json is missing currentTopDecile[]");
if (!Array.isArray(macroCalendar?.events) || !macroCalendar.events.length) fail("data/macro-calendar.json is missing events[]");

if (snapshot.marketDataStatus?.status !== "fresh") fail(`snapshot marketDataStatus is ${snapshot.marketDataStatus?.status || "missing"}`);
if (scorebook.marketDataStatus?.status !== "fresh") fail(`scorebook marketDataStatus is ${scorebook.marketDataStatus?.status || "missing"}`);
if (snapshot.model?.status !== "ready") fail(`snapshot model status is ${snapshot.model?.status || "missing"}`);
if (scorebook.status !== "ready") fail(`scorebook status is ${scorebook.status || "missing"}`);
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

const topDecileCutoff = Math.ceil(scorebook.rows.length * 0.1);
const expectedTopDecile = scorebook.rows
  .filter((row) => Number(row.modelRank) <= topDecileCutoff)
  .map((row) => row.symbol)
  .sort();
const actualTopDecile = monitoring.currentTopDecile.map((row) => row.symbol).sort();
if (monitoring.topDecileCutoff !== topDecileCutoff) fail(`model-monitoring topDecileCutoff is ${monitoring.topDecileCutoff}, expected ${topDecileCutoff}`);
if (JSON.stringify(expectedTopDecile) !== JSON.stringify(actualTopDecile)) fail("model-monitoring currentTopDecile does not match scorebook model ranks");

const stopRequiredSetups = new Set(["momentum_confirmed", "model_rebound_watch", "model_ranked_not_momentum_confirmed"]);
const missingStops = scorebook.rows.filter((row) => stopRequiredSetups.has(row.setupType) && numberOrNull(row.stopSellPrice) === null);
if (missingStops.length) fail(`${missingStops.length} scorebook rows that should show stop values are missing stopSellPrice`);

const badActivations = scorebook.rows.filter((row) => row.setupType !== "model_rebound_watch" && numberOrNull(row.reboundActivationPrice) !== null);
if (badActivations.length) fail(`${badActivations.length} non-rebound rows expose reboundActivationPrice`);
const missingActivations = scorebook.rows.filter((row) => row.setupType === "model_rebound_watch" && numberOrNull(row.reboundActivationPrice) === null);
if (missingActivations.length) fail(`${missingActivations.length} Model Rebound Watch rows are missing reboundActivationPrice`);

const serializedDashboard = JSON.stringify({ snapshot, scorebook, monitoring });
["NaN", "undefined", "Infinity", "Activation $0.00", "Activation 0.00"].forEach((token) => {
  if (serializedDashboard.includes(token)) fail(`dashboard data contains bad placeholder token: ${token}`);
});

const aiMemoText = collectStrings(snapshot.aiRecommendations || {}).join(" ");
if (/\bstop(?:-loss)?(?:\s+(?:level|price))?\s*(?:of|at|near|below|under|:)?\s*\$?\d+(?:\.\d+)?\b/i.test(aiMemoText)) {
  fail("AI Strategy Memo contains a stop-loss metric that should be rendered only as a deterministic dashboard label");
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
if (!pagesWorkflow.includes("data/model-monitoring.json public/data/model-monitoring.json")) {
  fail(".github/workflows/pages.yml does not publish data/model-monitoring.json");
}

const localServer = readFileSync(join(root, "scripts/local-dashboard-server.mjs"), "utf8");
if (!localServer.includes('"data/model-monitoring.json"')) {
  fail("scripts/local-dashboard-server.mjs does not allow data/model-monitoring.json");
}
if (!localServer.includes('"data/macro-calendar.json"')) {
  fail("scripts/local-dashboard-server.mjs does not allow data/macro-calendar.json");
}

const macroEvents = macroCalendar.events.filter((event) => event?.date && event?.time && event?.event && event?.source);
if (macroEvents.length !== macroCalendar.events.length) fail("data/macro-calendar.json contains incomplete macro events");
const requiredMacroFamilies = ["Employment Situation", "Consumer Price Index", "Producer Price Index", "FOMC Rate Decision"];
requiredMacroFamilies.forEach((family) => {
  if (!macroEvents.some((event) => event.event === family)) fail(`data/macro-calendar.json is missing ${family}`);
});

if (process.exitCode) process.exit(process.exitCode);
console.log("Project verification passed.");
