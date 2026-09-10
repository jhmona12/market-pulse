import { appendFileSync, readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

function positiveInteger(value, fallback) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : fallback;
}

function normalizePageUrl(pageUrl) {
  if (!pageUrl) throw new Error("PAGE_URL is required for live Pages confirmation.");
  return pageUrl.endsWith("/") ? pageUrl : `${pageUrl}/`;
}

function writeOutput(outputPath, values) {
  if (!outputPath) return;
  appendFileSync(
    outputPath,
    Object.entries(values)
      .map(([key, value]) => `${key}=${value ?? ""}`)
      .join("\n") + "\n"
  );
}

function appendSummary(summaryPath, result) {
  if (!summaryPath) return;
  const icon = result.confirmed ? "OK" : "Warning";
  appendFileSync(
    summaryPath,
    [
      `### Live Pages confirmation: ${icon}`,
      "",
      `- Status URL: ${result.statusUrl || "unavailable"}`,
      `- Expected run id: \`${result.expectedRunId || "unknown"}\``,
      `- Confirmed: \`${result.confirmed ? "true" : "false"}\``,
      result.lastError ? `- Last observed issue: ${result.lastError}` : null,
      ""
    ].filter(Boolean).join("\n")
  );
}

async function defaultSleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function confirmLivePagesStatus({
  pageUrl,
  expectedRunId,
  expectedArtifacts,
  attempts = 12,
  delayMs = 10000,
  fetchImpl = fetch,
  sleep = defaultSleep,
  logger = console,
  outputPath,
  summaryPath
} = {}) {
  const normalizedPageUrl = normalizePageUrl(pageUrl);
  const expected = String(expectedRunId || "");
  if (!expected) throw new Error("EXPECTED_RUN_ID is required for live Pages confirmation.");
  if (!expectedArtifacts || !Object.keys(expectedArtifacts).length) throw new Error("Expected dashboard artifact hashes are required.");
  const statusUrlObject = new URL("data/refresh-status.json", normalizedPageUrl);
  if (expected) statusUrlObject.searchParams.set("run", expected);
  const statusUrl = statusUrlObject.toString();
  let lastError = null;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const probeUrl = new URL(statusUrl);
      probeUrl.searchParams.set("attempt", String(attempt));
      const response = await fetchImpl(probeUrl.toString(), { cache: "no-store", signal: AbortSignal.timeout(15000) });
      if (!response.ok) {
        await response.body?.cancel?.();
        throw new Error(`HTTP ${response.status}`);
      }
      const status = await response.json();
      if (
        String(status.runId || "") === expected
        && status.status === "success"
        && status.publishStatus === "published"
      ) {
        for (const [path, digest] of Object.entries(expectedArtifacts)) {
          const artifactUrl = new URL(path, normalizedPageUrl);
          artifactUrl.search = probeUrl.search;
          const artifactResponse = await fetchImpl(artifactUrl.toString(), { cache: "no-store", signal: AbortSignal.timeout(15000) });
          if (!artifactResponse.ok) {
            await artifactResponse.body?.cancel?.();
            throw new Error(`HTTP ${artifactResponse.status} for ${path}`);
          }
          const servedDigest = createHash("sha256").update(await artifactResponse.text()).digest("hex");
          if (servedDigest !== digest) throw new Error(`Live artifact does not match verified output: ${path}`);
        }
        const result = { confirmed: true, statusUrl, expectedRunId: expected, attemptsUsed: attempt, artifactCount: Object.keys(expectedArtifacts).length };
        logger.log?.(`Confirmed live Pages status for run ${expected} at ${statusUrl}`);
        writeOutput(outputPath, {
          live_confirmed: "true",
          live_status_url: statusUrl,
          live_confirmation_error: ""
        });
        appendSummary(summaryPath, result);
        return result;
      }
      lastError = `live status has runId=${status.runId || "missing"} status=${status.status || "missing"} publishStatus=${status.publishStatus || "missing"}`;
    } catch (error) {
      lastError = error.message;
    }

    logger.log?.(`Live Pages status not confirmed (${attempt}/${attempts}): ${lastError}`);
    if (attempt < attempts) await sleep(delayMs);
  }

  const result = {
    confirmed: false,
    statusUrl,
    expectedRunId: expected,
    attemptsUsed: attempts,
    lastError
  };
  logger.warn?.(`Live Pages status did not confirm run ${expected}; the scheduled target must remain retryable until publication is observed. Last issue: ${lastError || "unknown"}`);
  writeOutput(outputPath, {
    live_confirmed: "false",
    live_status_url: statusUrl,
    live_confirmation_error: lastError || "unknown"
  });
  appendSummary(summaryPath, result);
  return result;
}

async function main() {
  try {
    const result = await confirmLivePagesStatus({
      pageUrl: process.env.PAGE_URL,
      expectedRunId: process.env.EXPECTED_RUN_ID,
      expectedArtifacts: Object.fromEntries([
        "data/snapshot.json", "data/model-scorebook.json",
        "data/long-horizon-research.json", "data/model-monitoring.json"
      ].map((path) => [path, createHash("sha256").update(readFileSync(path)).digest("hex")])),
      attempts: positiveInteger(process.env.LIVE_CONFIRM_ATTEMPTS, 12),
      delayMs: positiveInteger(process.env.LIVE_CONFIRM_DELAY_MS, 10000),
      outputPath: process.env.GITHUB_OUTPUT,
      summaryPath: process.env.GITHUB_STEP_SUMMARY
    });
    if (!result.confirmed && process.env.LIVE_CONFIRM_STRICT === "1") process.exitCode = 1;
  } catch (error) {
    console.warn(`Live Pages confirmation could not run: ${error.message}`);
    writeOutput(process.env.GITHUB_OUTPUT, {
      live_confirmed: "false",
      live_status_url: "",
      live_confirmation_error: error.message
    });
    if (process.env.LIVE_CONFIRM_STRICT === "1") process.exitCode = 1;
  }
}

export { confirmLivePagesStatus };

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main();
}
