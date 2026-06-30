import { readFileSync } from "node:fs";
import { join } from "node:path";

const artifactSchemas = [
  { artifact: "data/snapshot.json", schema: "schemas/snapshot.schema.json" },
  { artifact: "data/model-scorebook.json", schema: "schemas/model-scorebook.schema.json" },
  { artifact: "data/long-horizon-research.json", schema: "schemas/long-horizon-research.schema.json" }
];

function readJson(root, path) {
  return JSON.parse(readFileSync(join(root, path), "utf8"));
}

function typeName(value) {
  if (Array.isArray(value)) return "array";
  if (value === null) return "null";
  return typeof value;
}

function typeMatches(value, expectedType) {
  const expected = Array.isArray(expectedType) ? expectedType : [expectedType];
  return expected.includes(typeName(value));
}

function validateValue(value, schema, path = "$", errors = []) {
  if (!schema || typeof schema !== "object") return errors;
  if (schema.type && !typeMatches(value, schema.type)) {
    errors.push(`${path} expected ${Array.isArray(schema.type) ? schema.type.join("|") : schema.type}, got ${typeName(value)}`);
    return errors;
  }
  if (schema.enum && !schema.enum.includes(value)) errors.push(`${path} is not in enum`);
  if (schema.type === "object" && schema.required) {
    for (const key of schema.required) {
      if (!Object.prototype.hasOwnProperty.call(value || {}, key)) errors.push(`${path}.${key} is required`);
    }
  }
  if (schema.properties && value && typeof value === "object" && !Array.isArray(value)) {
    for (const [key, childSchema] of Object.entries(schema.properties)) {
      if (Object.prototype.hasOwnProperty.call(value, key)) {
        validateValue(value[key], childSchema, `${path}.${key}`, errors);
      }
    }
  }
  if (schema.items && Array.isArray(value)) {
    if (schema.minItems != null && value.length < schema.minItems) errors.push(`${path} has fewer than ${schema.minItems} items`);
    if (schema.maxItems != null && value.length > schema.maxItems) errors.push(`${path} has more than ${schema.maxItems} items`);
    value.forEach((item, index) => validateValue(item, schema.items, `${path}[${index}]`, errors));
  }
  return errors;
}

function validateJsonSchema(value, schema) {
  return validateValue(value, schema);
}

function validateDashboardSchemas(root = process.cwd()) {
  return artifactSchemas.flatMap(({ artifact, schema: schemaPath }) => {
    const data = readJson(root, artifact);
    const schema = readJson(root, schemaPath);
    return validateJsonSchema(data, schema).map((error) => `${artifact}: ${error}`);
  });
}

export { artifactSchemas, validateDashboardSchemas, validateJsonSchema };
