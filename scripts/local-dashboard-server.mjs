import { createReadStream } from "node:fs";
import { mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, extname, join, normalize, relative, resolve } from "node:path";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const port = Number.parseInt(process.env.PORT || "4173", 10);
const python = process.env.MODEL_PYTHON || join(root, ".venv-model/bin/python");
const outputDir = join(root, "data/ticker-lab");
const referenceCachePath = join(root, "data/model-reference-cache.json");
const maxTickers = Number.parseInt(process.env.TICKER_LAB_MAX_TICKERS || "25", 10);
const accessCode = process.env.TICKER_LAB_ACCESS_CODE || "";

const mimeTypes = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".ico": "image/x-icon"
};

function corsHeaders() {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type,x-ticker-lab-token",
    "access-control-max-age": "86400"
  };
}

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    ...corsHeaders()
  });
  response.end(`${JSON.stringify(payload, null, 2)}\n`);
}

function authorized(request) {
  if (!accessCode) return true;
  return request.headers["x-ticker-lab-token"] === accessCode;
}

function cleanTickerInput(value) {
  const text = Array.isArray(value) ? value.join(" ") : String(value || "");
  const tokens = text
    .replace(/[,\n\r\t;]+/g, " ")
    .split(/\s+/)
    .map((token) => token.trim().toUpperCase().replace(/^\$/, ""))
    .filter(Boolean)
    .filter((token) => /^[A-Z0-9][A-Z0-9.^=\-]{0,18}$/.test(token));
  return [...new Set(tokens)].slice(0, maxTickers);
}

async function readRequestBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 32_000) throw new Error("Ticker request is too large.");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function runModelScore(tickers, outputPath) {
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(
      python,
      [
        "scripts/modeling/score_live_rank_model.py",
        "--focus-symbols",
        tickers.join(","),
        "--output",
        relative(root, outputPath),
        "--reference-cache",
        relative(root, referenceCachePath)
      ],
      { cwd: root, env: process.env }
    );
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", rejectPromise);
    child.on("close", (code) => {
      if (code === 0) {
        resolvePromise({ stdout, stderr });
      } else {
        rejectPromise(new Error(stderr || stdout || `Model scoring failed with exit code ${code}`));
      }
    });
  });
}

async function scoreTickers(request, response) {
  try {
    if (!authorized(request)) {
      sendJson(response, 401, {
        status: "error",
        error: "Ticker Lab access code is required."
      });
      return;
    }

    const rawBody = await readRequestBody(request);
    const body = rawBody ? JSON.parse(rawBody) : {};
    const tickers = cleanTickerInput(body.tickers || body.text);
    if (!tickers.length) {
      sendJson(response, 400, { status: "error", error: "Paste at least one ticker symbol." });
      return;
    }

    await mkdir(outputDir, { recursive: true });
    const runId = randomUUID();
    const outputPath = join(outputDir, `${runId}.json`);
    const startedAt = new Date().toISOString();
    const run = await runModelScore(tickers, outputPath);
    const payload = JSON.parse(await readFile(outputPath, "utf8"));
    await writeFile(join(outputDir, "latest.json"), `${JSON.stringify(payload, null, 2)}\n`);
    await rm(outputPath, { force: true });

    sendJson(response, 200, {
      status: "ready",
      startedAt,
      completedAt: new Date().toISOString(),
      stdout: run.stdout.trim(),
      stderr: run.stderr.trim(),
      requestedTickers: tickers,
      asOfDate: payload.asOfDate,
      referenceUniverse: payload.referenceUniverse,
      referenceUniverseCount: payload.referenceUniverseCount,
      results: payload.focusRankings || [],
      failures: payload.focusFailures || {},
      unscoredSymbols: payload.focusUnscoredSymbols || [],
      sectorDiagnostics: payload.sectorDiagnostics || {},
      methodologyNotes: payload.methodologyNotes || []
    });
  } catch (error) {
    sendJson(response, 500, {
      status: "error",
      error: error.message
    });
  }
}

async function serveStatic(request, response) {
  const url = new URL(request.url || "/", `http://localhost:${port}`);
  const pathname = decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname);
  const filePath = normalize(join(root, pathname));
  if (!filePath.startsWith(root)) {
    response.writeHead(403);
    response.end("Forbidden");
    return;
  }
  try {
    const info = await stat(filePath);
    if (!info.isFile()) throw new Error("Not a file");
    response.writeHead(200, {
      "content-type": mimeTypes[extname(filePath)] || "application/octet-stream",
      "cache-control": "no-store"
    });
    createReadStream(filePath).pipe(response);
  } catch {
    response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    response.end("Not found");
  }
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url || "/", `http://localhost:${port}`);
  if (request.method === "OPTIONS") {
    response.writeHead(204, corsHeaders());
    response.end();
    return;
  }
  if (request.method === "GET" && url.pathname === "/api/ticker-lab/status") {
    let referenceCache = { ready: false };
    try {
      const payload = JSON.parse(await readFile(referenceCachePath, "utf8"));
      referenceCache = {
        ready: payload.status === "ready",
        generatedAt: payload.generatedAt,
        asOfDate: payload.asOfDate,
        referenceUniverseCount: payload.referenceUniverseCount
      };
    } catch {
      referenceCache = { ready: false };
    }
    sendJson(response, 200, {
      enabled: true,
      privacy: accessCode ? "access_code_required" : "open_endpoint",
      requiresAccessCode: Boolean(accessCode),
      maxTickers,
      referenceUniverse: "Current S&P 500",
      referenceCache
    });
    return;
  }
  if (request.method === "POST" && url.pathname === "/api/ticker-lab/score") {
    await scoreTickers(request, response);
    return;
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    response.writeHead(405);
    response.end("Method not allowed");
    return;
  }
  await serveStatic(request, response);
});

server.listen(port, () => {
  console.log(`Market Pulse local server running at http://localhost:${port}`);
  console.log("Ticker Lab API is available at /api/ticker-lab/score.");
});
