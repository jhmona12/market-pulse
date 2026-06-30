export function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

export function formatDate(value) {
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date(value));
}

export function formatShortDate(value) {
  if (!value) return "Date unavailable";
  const date = /^\d{4}-\d{2}-\d{2}$/.test(String(value)) ? new Date(`${value}T12:00:00`) : new Date(value);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(date);
}

export function pct(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "n/a";
  return `${number >= 0 ? "+" : ""}${number.toFixed(2)}%`;
}

export function displayPct(value) {
  if (!Number.isFinite(Number(value))) return "n/a";
  return pct(value);
}

export function displayReturnDecimal(value) {
  if (!Number.isFinite(Number(value))) return "n/a";
  return pct(Number(value) * 100);
}

export function displayPercentile(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "n/a";
  return `${(number * 100).toFixed(1)}%`;
}

export function displayScore(value) {
  if (!Number.isFinite(Number(value))) return "n/a";
  return Number(value).toFixed(6);
}

export function displayMarketCap(value, fallbackText) {
  if (fallbackText) return fallbackText;
  const number = Number(value);
  if (!Number.isFinite(number)) return "n/a";
  if (number >= 1_000_000_000_000) return `$${(number / 1_000_000_000_000).toFixed(2)}T`;
  if (number >= 1_000_000_000) return `$${(number / 1_000_000_000).toFixed(1)}B`;
  if (number >= 1_000_000) return `$${(number / 1_000_000).toFixed(1)}M`;
  return `$${number.toLocaleString("en-US")}`;
}

export function money(value) {
  if (!Number.isFinite(Number(value))) return "n/a";
  return Number(value).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2
  });
}

export function hasNumericValue(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

export function returnClass(value) {
  if (!Number.isFinite(Number(value))) return "";
  return Number(value) < 0 ? "negative" : "positive";
}

export function percentilePoint(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return number <= 1 ? number * 100 : number;
}

export function displayPercentilePoint(value) {
  const number = percentilePoint(value);
  if (!Number.isFinite(number)) return "n/a";
  return `${number.toFixed(0)}%`;
}
