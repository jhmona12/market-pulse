function defaultSleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function retryableHttpStatus(status) {
  return [408, 429, 500, 502, 503, 504].includes(status);
}

function retryDelayMs(attempt) {
  return 2500 * 2 ** attempt + Math.round(Math.random() * 700);
}

function isRedirectStatus(status) {
  return [301, 302, 303, 307, 308].includes(status);
}

async function cancelResponseBody(response) {
  try {
    await response?.body?.cancel?.();
  } catch {
    // The connection is already closed; there is nothing else to release.
  }
}

async function fetchWithBoundedRedirects(url, {
  fetchImpl,
  signal,
  headers,
  maxRedirects
}) {
  let currentUrl = String(url);
  let currentHeaders = new Headers(headers);
  for (let redirectCount = 0; redirectCount <= maxRedirects; redirectCount += 1) {
    const response = await fetchImpl(currentUrl, {
      signal,
      headers: currentHeaders,
      redirect: "manual"
    });
    if (!isRedirectStatus(response.status)) return response;

    const location = response.headers?.get?.("location");
    await cancelResponseBody(response);
    if (!location) {
      const error = new Error(`${response.status} redirect response did not include Location`);
      error.retryable = false;
      throw error;
    }
    if (redirectCount >= maxRedirects) {
      const error = new Error(`Redirect limit exceeded after ${maxRedirects} hop(s): ${currentUrl}`);
      error.retryable = false;
      throw error;
    }
    const nextUrl = new URL(location, currentUrl);
    if (nextUrl.origin !== new URL(currentUrl).origin) {
      currentHeaders = new Headers(currentHeaders);
      for (const name of ["authorization", "cookie", "proxy-authorization"]) currentHeaders.delete(name);
    }
    currentUrl = nextUrl.toString();
  }
  throw new Error(`Redirect limit exceeded: ${url}`);
}

async function fetchWithRetry(url, {
  responseType = "text",
  retries = 5,
  timeout = 14000,
  headers = {},
  fetchImpl = fetch,
  sleep = defaultSleep,
  delayForAttempt = retryDelayMs,
  maxRedirects = 6
} = {}) {
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetchWithBoundedRedirects(url, {
        fetchImpl,
        signal: controller.signal,
        headers,
        maxRedirects
      });
      if (!response.ok) {
        await cancelResponseBody(response);
        const error = new Error(`${response.status} ${response.statusText || "HTTP error"}`);
        error.retryable = retryableHttpStatus(response.status);
        throw error;
      }
      return responseType === "json" ? await response.json() : await response.text();
    } catch (error) {
      lastError = error;
      const canRetry = error?.retryable !== false && attempt < retries;
      if (!canRetry) throw error;
      await sleep(delayForAttempt(attempt));
    } finally {
      clearTimeout(timeoutId);
    }
  }
  throw lastError;
}

function fetchTextWithRetry(url, options = {}) {
  return fetchWithRetry(url, { ...options, responseType: "text" });
}

function fetchJsonWithRetry(url, options = {}) {
  return fetchWithRetry(url, { ...options, responseType: "json" });
}

export {
  cancelResponseBody,
  fetchJsonWithRetry,
  fetchTextWithRetry,
  fetchWithRetry,
  isRedirectStatus,
  retryableHttpStatus
};
