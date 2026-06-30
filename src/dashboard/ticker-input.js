function normalizeTickerToken(token) {
  const cleaned = token.trim().toUpperCase().replace(/^\$/, "");
  const exchangePrefix = cleaned.match(/^[A-Z]{1,8}:([A-Z0-9][A-Z0-9.^=\-]{0,18})$/);
  return exchangePrefix ? exchangePrefix[1] : cleaned;
}

export function parseTickerInput(value, maxTickers = 25) {
  return [
    ...new Set(
      String(value || "")
        .replace(/[,\n\r\t;]+/g, " ")
        .split(/\s+/)
        .map(normalizeTickerToken)
        .filter(Boolean)
        .filter((token) => /^[A-Z0-9][A-Z0-9.^=\-]{0,18}$/.test(token))
    )
  ].slice(0, maxTickers);
}

export function normalizeApiBaseUrl(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}
