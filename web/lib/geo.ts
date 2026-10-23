import type { Coordinates } from "./types";

/** Round to 2 decimals (~1 km) in the browser, before the position leaves the page. */
export function roundCoordinates(lat: number, lon: number): Coordinates {
  return { lat: Math.round(lat * 100) / 100, lon: Math.round(lon * 100) / 100 };
}

/** Validate coordinates coming from a request body. */
export function parseCoordinates(value: unknown): Coordinates | null {
  if (!value || typeof value !== "object") return null;
  const { lat, lon } = value as Record<string, unknown>;
  if (typeof lat !== "number" || typeof lon !== "number") return null;
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return roundCoordinates(lat, lon);
}
