import assert from "node:assert/strict";
import { test } from "node:test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { buildModelMonitoring, buildLongHorizonResearch } from "../scripts/update-data.mjs";
import { validateModelEvidence, loadModelEvidence } from "../scripts/snapshot/model-evidence.mjs";

test("model changes reset rank-change comparisons without deleting historical snapshots", () => {
  const scorebook = { status: "ready", asOfDate: "2026-11-18", model: { artifactSha256: "new" }, rows: [
    { symbol: "ABC", modelRank: 1, modelScore: 0.5, modelPercentile: 100 }
  ] };
  const previous = { history: [{ asOfDate: "2026-11-17", modelSha256: "old", rows: [{symbol: "ABC", modelRank: 100}] }] };
  const first = buildModelMonitoring({ modelScorebook: scorebook, previousMonitoring: previous });
  assert.equal(first.recentEntrantCount, 0);
  assert.equal(first.currentTopDecile[0].previousRank, null);
  assert.equal(first.history.length, 2);
  assert.equal(first.history[0].modelSha256, "new");
  const next = buildModelMonitoring({ modelScorebook: { ...scorebook, asOfDate: "2026-11-19" }, previousMonitoring: first });
  assert.equal(next.currentTopDecile[0].previousRank, 1);
});

test("replacement evidence cannot reuse prior-model statistics or mislabeled hashes", async () => {
  const metadata = { model_sha256: "new", feature_version: 2 };
  const evidence = { modelMetadata: {modelSha256: "new", featureVersion: 2}, evaluation: {modelSha256: "new"} };
  assert.equal(validateModelEvidence(evidence, metadata), evidence);
  assert.throws(() => validateModelEvidence(evidence, {...metadata, model_sha256: "old"}), /different model/);
  await assert.rejects(loadModelEvidence(process.cwd(), {dashboard_evidence_path: "../outside.json"}), /inside models/);
  assert.throws(() => buildLongHorizonResearch({ base: evidence,
    longHorizonRankings: { model: {featureVersion: 2, artifactSha256: "old"} } }), /different models/);
});

test("release push bypasses scheduled-window deduplication without creating a refresh loop", () => {
  const output = execFileSync(process.execPath, ["scripts/check-refresh-window.mjs"], {
    encoding: "utf8", env: { ...process.env, GITHUB_EVENT_NAME: "push", SCHEDULE: "",
      GITHUB_OUTPUT: "", GITHUB_ENV: "" }
  });
  assert.match(output, /run_refresh=true/);
  assert.match(output, /refresh_target_key=manual/);
  const workflow = readFileSync(new URL("../.github/workflows/refresh-data.yml", import.meta.url), "utf8");
  const push = workflow.split("  push:\n")[1].split("  schedule:")[0];
  assert.match(push, /branches: \[main\]/);
  assert.match(push, /models\/releases\/\*\*\/manifest\.json/);
  assert.doesNotMatch(push, /data\/|\*\.json/);
  const pages = readFileSync(new URL("../.github/workflows/pages.yml", import.meta.url), "utf8");
  assert.ok(pages.indexOf("git pull --ff-only") < pages.indexOf("npm run verify"));
});

test("strategic release notes cannot force an implicit oversized grid column", () => {
  const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
  for (const selector of ["long-horizon-panel", "long-horizon-section"]) {
    const rule = css.split(`.${selector} {`)[1].split("}")[0];
    assert.match(rule, /grid-template-columns: minmax\(0, 1fr\)/);
  }
});
