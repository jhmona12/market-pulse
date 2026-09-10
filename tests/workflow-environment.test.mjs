import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

for (const [workflow, verifyCommand] of [
  ["pages.yml", "npm run verify"],
  ["refresh-data.yml", "node scripts/refresh/run-snapshot-refresh.mjs"]
]) {
  test(`${workflow} provisions Python dependencies before project verification`, () => {
    const source = readFileSync(new URL(`../.github/workflows/${workflow}`, import.meta.url), "utf8");
    const pythonSetup = source.indexOf("uses: actions/setup-python@");
    const dependencyInstall = source.indexOf("python -m pip install -r scripts/modeling/requirements.txt");
    const verification = source.indexOf(verifyCommand);
    assert.ok(pythonSetup >= 0, `${workflow} must explicitly provision Python`);
    assert.ok(dependencyInstall > pythonSetup, `${workflow} must install model-test dependencies after Python setup`);
    assert.ok(verification > dependencyInstall, `${workflow} must install dependencies before verification`);
  });
}
