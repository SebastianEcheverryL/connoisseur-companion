"use client";

import { useState } from "react";

import { roundCoordinates } from "@/lib/geo";
import type { Coordinates } from "@/lib/types";

/**
 * Optional browser geolocation. The position is rounded to ~1 km here, before it is ever sent,
 * and only travels with the next messages while sharing is on.
 */
export default function LocationButton({
  value,
  onChange,
}: {
  value: Coordinates | null;
  onChange: (value: Coordinates | null) => void;
}) {
  const [state, setState] = useState<"idle" | "asking" | "denied">("idle");

  function share() {
    if (!navigator.geolocation) return setState("denied");
    setState("asking");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        onChange(roundCoordinates(pos.coords.latitude, pos.coords.longitude));
        setState("idle");
      },
      () => setState("denied"),
      { timeout: 10_000, maximumAge: 600_000 },
    );
  }

  if (value) {
    return (
      <p className="flex items-center gap-2 text-xs text-muted">
        📍 Sharing your location (rounded to ~1 km)
        <button type="button" onClick={() => onChange(null)} className="underline">
          Stop sharing
        </button>
      </p>
    );
  }
  return (
    <p className="flex items-center gap-2 text-xs text-muted">
      <button type="button" onClick={share} disabled={state === "asking"} className="underline">
        📍 Use my location
      </button>
      {state === "asking" && <span>Waiting for permission…</span>}
      {state === "denied" && <span>Location not available. You can also type your city.</span>}
    </p>
  );
}
