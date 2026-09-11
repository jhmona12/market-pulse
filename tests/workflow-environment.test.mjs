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

test("price diagnostics survive scoring failure without becoming a deploy dependency", () => {
  const source = readFileSync(new URL("../.github/workflows/refresh-data.yml", import.meta.url), "utf8");
  const section = source.split("- name: Retain price readiness diagnostics")[1]?.split("- name:")[0] || "";
  assert.match(section, /if: always\(\)/);
  assert.match(section, /continue-on-error: true/);
  assert.match(section, /data\/diagnostics\/price-readiness-\*\.json/);
  assert.match(section, /github.run_attempt/);
  assert.match(section, /retention-days: 14/);
  assert.ok(source.indexOf("Retain price readiness diagnostics") < source.indexOf("Stop if model scoring failed"));
});
