import { writeFile } from "node:fs/promises";

const rawUrl = process.argv[2] || "";
const url = rawUrl.trim().replace(/\/+$/, "");

if (!url) {
  console.error("Usage: node scripts/configure-ticker-backend.mjs https://your-backend.example.com");
  process.exit(1);
}

try {
  const parsed = new URL(url);
  if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("URL must start with http:// or https://");
} catch (error) {
  console.error(`Invalid backend URL: ${error.message}`);
  process.exit(1);
}

const payload = {
  tickerLabApiBaseUrl: url
};

await writeFile("config/runtime.json", `${JSON.stringify(payload, null, 2)}\n`, "utf8");
console.log(`Configured Ticker Lab backend: ${url}`);
