export async function fetchJson(path, label = path) {
  const response = await fetch(`${path}?ts=${Date.now()}`);
  if (!response.ok) throw new Error(`${label} unavailable: ${response.status}`);
  return response.json();
}
