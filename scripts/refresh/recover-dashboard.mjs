import { appendFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const dashboardArtifacts = [
  "data/snapshot.json", "data/model-scorebook.json", "data/model-monitoring.json",
  "data/long-horizon-research.json", "data/macro-calendar.json",
  "data/refresh-status.json", "data/refresh-ledger.json"
];

function recoverDashboard({ cwd = process.cwd(), branch = process.env.GITHUB_REF_NAME, liveConfirmed = false } = {}) {
  if (liveConfirmed) return { recovered: false, reason: "The new dashboard is confirmed live; preserve its published data and diagnose repository synchronization." };
  if (!branch) throw new Error("A branch is required for failure recovery.");
  const git = (args) => execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
  git(["fetch", "origin", branch]);
  if (git(["rev-parse", "HEAD"]) !== git(["rev-parse", "FETCH_HEAD"])) {
    return { recovered: false, reason: "The remote branch advanced; this older job must not publish failure data over newer output." };
  }
  git(["restore", "--source=HEAD", "--", ...dashboardArtifacts]);
  return { recovered: true, reason: "Restored the checked-in dashboard after a failed refresh." };
}

export { dashboardArtifacts, recoverDashboard };

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const result = recoverDashboard({ liveConfirmed: process.env.LIVE_PAGES_CONFIRMED === "true" });
  console.log(result.reason);
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, `recovered=${result.recovered}\n`);
}
