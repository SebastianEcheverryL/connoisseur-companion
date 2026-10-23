"use client";

import { useState } from "react";

/** 👍 / 👎 on an answer: stored as a Langfuse score on that answer's trace (through the backend). */
export default function Feedback({ traceId }: { traceId: string }) {
  const [sent, setSent] = useState<0 | 1 | null>(null);

  async function rate(value: 0 | 1) {
    setSent(value);
    await fetch("/api/feedback", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ traceId, value }),
    }).catch(() => setSent(null));
  }

  return (
    <div className="flex items-center gap-1 text-xs text-muted">
      <span>Was this helpful?</span>
      {([1, 0] as const).map((value) => (
        <button
          key={value}
          onClick={() => rate(value)}
          disabled={sent !== null}
          aria-label={value ? "Helpful" : "Not helpful"}
          className={`rounded-md px-1.5 py-0.5 hover:bg-accent-soft disabled:cursor-default ${sent === value ? "bg-accent-soft" : ""}`}
        >
          {value ? "👍" : "👎"}
        </button>
      ))}
      {sent !== null && <span>Thanks!</span>}
    </div>
  );
}
