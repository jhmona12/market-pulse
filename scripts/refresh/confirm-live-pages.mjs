import { appendFileSync } from "node:fs";
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
  attempts = 12,
  delayMs = 10000,
  fetchImpl = fetch,
  sleep = defaultSleep,
  logger = console,
  outputPath,
  summaryPath
} = {}) {
  const normalizedPageUrl = normalizePageUrl(pageUrl);
  const statusUrl = new URL("data/refresh-status.json", normalizedPageUrl).toString();
  const expected = String(expectedRunId || "");
  let lastError = null;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await fetchImpl(statusUrl, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const status = await response.json();
      if (
        String(status.runId || "") === expected
        && status.status === "success"
        && status.publishStatus === "published"
      ) {
        const result = { confirmed: true, statusUrl, expectedRunId: expected, attemptsUsed: attempt };
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
  logger.warn?.(`Live Pages status did not confirm run ${expected}; continuing because deploy-pages already succeeded. Monitor Refresh Health will catch a stale live site. Last issue: ${lastError || "unknown"}`);
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
    await confirmLivePagesStatus({
      pageUrl: process.env.PAGE_URL,
      expectedRunId: process.env.EXPECTED_RUN_ID,
      attempts: positiveInteger(process.env.LIVE_CONFIRM_ATTEMPTS, 12),
      delayMs: positiveInteger(process.env.LIVE_CONFIRM_DELAY_MS, 10000),
      outputPath: process.env.GITHUB_OUTPUT,
      summaryPath: process.env.GITHUB_STEP_SUMMARY
    });
  } catch (error) {
    console.warn(`Live Pages confirmation could not run: ${error.message}`);
    writeOutput(process.env.GITHUB_OUTPUT, {
      live_confirmed: "false",
      live_status_url: "",
      live_confirmation_error: error.message
    });
    if (process.env.LIVE_CONFIRM_STRICT === "1") process.exit(1);
  }
}

export { confirmLivePagesStatus };

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main();
}
