import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { dashboardArtifacts, recoverDashboard } from "../scripts/refresh/recover-dashboard.mjs";

function fixture(t) {
  const directory = mkdtempSync(join(tmpdir(), "market-pulse-recovery-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const cwd = join(directory, "work");
  const remote = join(directory, "remote.git");
  const git = (args, at = directory) => execFileSync("git", args, { cwd: at, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
  git(["init", "--bare", "-b", "main", remote]);
  git(["init", "-b", "main", cwd]);
  git(["config", "user.email", "test@example.invalid"], cwd);
  git(["config", "user.name", "Test"], cwd);
  mkdirSync(join(cwd, "data"));
  for (const path of dashboardArtifacts) writeFileSync(join(cwd, path), "{\"version\":\"last-good\"}\n");
  git(["add", "data"], cwd);
  git(["commit", "-m", "Initial fixture"], cwd);
  git(["remote", "add", "origin", remote], cwd);
  git(["push", "origin", "main"], cwd);
  for (const path of dashboardArtifacts) writeFileSync(join(cwd, path), "partial refresh\n");
  return { cwd, remote, directory, git };
}

test("failure recovery restores the complete dashboard bundle together", (t) => {
  const { cwd } = fixture(t);
  assert.equal(recoverDashboard({ cwd, branch: "main" }).recovered, true);
  for (const path of dashboardArtifacts) assert.equal(JSON.parse(readFileSync(join(cwd, path))).version, "last-good");
});

test("failure recovery cannot roll back a newer remote commit", (t) => {
  const { cwd, remote, directory, git } = fixture(t);
  const newer = join(directory, "newer");
  git(["clone", remote, newer]);
  git(["config", "user.email", "test@example.invalid"], newer);
  git(["config", "user.name", "Test"], newer);
  git(["commit", "--allow-empty", "-m", "Newer published change"], newer);
  git(["push", "origin", "main"], newer);
  assert.equal(recoverDashboard({ cwd, branch: "main" }).recovered, false);
  assert.equal(readFileSync(join(cwd, "data/snapshot.json"), "utf8"), "partial refresh\n");
});

test("a commit failure after confirmed publication does not restore old data", (t) => {
  const { cwd } = fixture(t);
  assert.equal(recoverDashboard({ cwd, branch: "main", liveConfirmed: true }).recovered, false);
  assert.equal(readFileSync(join(cwd, "data/snapshot.json"), "utf8"), "partial refresh\n");
});
