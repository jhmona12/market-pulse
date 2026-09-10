import { appendFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const tacticalArgs = [
  "scripts/modeling/score_live_rank_model.py",
  "--output",
  "data/model-rank-scores.json",
  "--max-workers",
  "4"
];

const strategicArgs = [
  "scripts/modeling/score_live_rank_model.py",
  "--model-dir",
  "models/long-horizon",
  "--model-name",
  "xgboost_rank_sector252_15y_monthly_tuned_research",
  "--output",
  "data/model-rank-scores-long-horizon.json",
  "--reference-cache",
  "data/cache/model-reference-cache.json",
  "--score-reference-cache"
];

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

async function runModelScoring({
  attempts = 2,
  retryDelayMs = 90000,
  python = process.env.PYTHON || "python",
  runCommand = defaultRunCommand,
  sleep = defaultSleep,
  logger = console,
  cwd = process.cwd()
} = {}) {
  let tacticalOutcome = "failure";
  let strategicOutcome = "skipped";
  let lastPhase = "tactical";
  let lastStatus = 1;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    logger.log?.(`Model scoring attempt ${attempt}/${attempts}`);

    if (tacticalOutcome !== "success") {
      lastPhase = "tactical";
      lastStatus = await runCommand(python, tacticalArgs, { cwd });
      tacticalOutcome = lastStatus === 0 ? "success" : "failure";
      if (tacticalOutcome !== "success") {
        logger.warn?.(`Tactical model scoring failed on attempt ${attempt} with exit code ${lastStatus}.`);
      }
    }

    if (tacticalOutcome === "success" && strategicOutcome !== "success") {
      lastPhase = "strategic";
      lastStatus = await runCommand(python, strategicArgs, { cwd });
      strategicOutcome = lastStatus === 0 ? "success" : "failure";
      if (strategicOutcome !== "success") {
        logger.warn?.(`Long-horizon model scoring failed on attempt ${attempt} with exit code ${lastStatus}.`);
      }
    }

    if (tacticalOutcome === "success" && strategicOutcome === "success") {
      logger.log?.(`Both model scorers passed on attempt ${attempt}.`);
      return { ok: true, attemptsUsed: attempt, tacticalOutcome, strategicOutcome, lastPhase, lastStatus: 0 };
    }

    if (attempt < attempts) {
      logger.warn?.(`Retrying the failed model phase after ${retryDelayMs}ms.`);
      await sleep(retryDelayMs);
    }
  }

  logger.error?.(`Model scoring failed after ${attempts} attempt(s); last failed phase: ${lastPhase}; exit code: ${lastStatus}.`);
  return { ok: false, attemptsUsed: attempts, tacticalOutcome, strategicOutcome, lastPhase, lastStatus };
}

function writeGithubOutputs(result) {
  if (!process.env.GITHUB_OUTPUT) return;
  appendFileSync(
    process.env.GITHUB_OUTPUT,
    [
      `tactical_outcome=${result.tacticalOutcome}`,
      `strategic_outcome=${result.strategicOutcome}`,
      `last_phase=${result.lastPhase}`,
      `attempts_used=${result.attemptsUsed}`
    ].join("\n") + "\n"
  );
}

async function main() {
  const result = await runModelScoring({
    attempts: positiveInteger(process.env.MODEL_SCORING_ATTEMPTS, 2),
    retryDelayMs: positiveInteger(process.env.MODEL_SCORING_RETRY_DELAY_MS, 90000)
  });
  writeGithubOutputs(result);
  if (!result.ok) process.exit(result.lastStatus || 1);
}

export { runModelScoring, strategicArgs, tacticalArgs };

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main();
}
