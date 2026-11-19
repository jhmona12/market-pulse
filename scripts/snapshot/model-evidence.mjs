import { readFile } from "node:fs/promises";
import { resolve, sep } from "node:path";

export function validateModelEvidence(evidence, metadata) {
  if (evidence.modelMetadata?.modelSha256 !== metadata.model_sha256 ||
      evidence.evaluation?.modelSha256 !== metadata.model_sha256 ||
      evidence.modelMetadata?.featureVersion !== metadata.feature_version) {
    throw new Error("Long-horizon research evidence belongs to different model weights");
  }
  return evidence;
}

export async function loadModelEvidence(root, metadata) {
  const directory = resolve(root, "models") + sep;
  const path = resolve(root, metadata.dashboard_evidence_path || "");
  if (!path.startsWith(directory)) throw new Error("Model evidence must be inside models/");
  return validateModelEvidence(JSON.parse(await readFile(path, "utf8")), metadata);
}
