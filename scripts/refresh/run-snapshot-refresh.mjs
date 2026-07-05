import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

function positiveInteger(value, fallback) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : fallback;
}

function defaultRunCommand(command, args, options = {}) {
  return new Promise((resolve) => {
    const child = spawn(command, args, {
      cwd: options.cwd || process.cwd(),
      env: options.env || process.env,
      shell: false,
      stdio: "inherit"
    });
    child.on("error", () => resolve(1));
    child.on("close", (code) => resolve(code ?? 1));
  });
}

async function defaultSleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function runSnapshotRefresh({
  attempts = 2,
  retryDelayMs = 5000,
  runCommand = defaultRunCommand,
  sleep = defaultSleep,
  logger = console,
  cwd = process.cwd()
} = {}) {
  let lastStatus = 1;
  let lastPhase = "not_started";

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    logger.log?.(`Snapshot refresh attempt ${attempt}/${attempts}`);

    lastPhase = "refresh";
    const refreshStatus = await runCommand("node", ["scripts/update-data.mjs"], { cwd });
    if (refreshStatus !== 0) {
      lastStatus = refreshStatus;
      logger.warn?.(`Snapshot refresh failed on attempt ${attempt} with exit code ${refreshStatus}.`);
    } else {
      lastPhase = "verify";
      const verifyStatus = await runCommand("npm", ["run", "verify"], { cwd });
      if (verifyStatus === 0) {
        logger.log?.(`Snapshot refresh and verification passed on attempt ${attempt}.`);
        return { ok: true, attemptsUsed: attempt, lastPhase: "verify", lastStatus: 0 };
      }
      lastStatus = verifyStatus;
      logger.warn?.(`Generated dashboard verification failed on attempt ${attempt} with exit code ${verifyStatus}.`);
    }

    if (attempt < attempts) {
      logger.warn?.(`Retrying snapshot refresh after ${retryDelayMs}ms because ${lastPhase} did not pass.`);
      await sleep(retryDelayMs);
    }
  }

  logger.error?.(`Snapshot refresh failed after ${attempts} attempt(s); last failed phase: ${lastPhase}; exit code: ${lastStatus}.`);
  return { ok: false, attemptsUsed: attempts, lastPhase, lastStatus };
}

async function main() {
  const result = await runSnapshotRefresh({
    attempts: positiveInteger(process.env.SNAPSHOT_REFRESH_ATTEMPTS, 2),
    retryDelayMs: positiveInteger(process.env.SNAPSHOT_REFRESH_RETRY_DELAY_MS, 5000)
  });
  if (!result.ok) process.exit(result.lastStatus || 1);
}

export { runSnapshotRefresh };

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main();
}
