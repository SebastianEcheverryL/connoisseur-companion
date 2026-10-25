"use client";

import { useState } from "react";

import { ThumbIcon } from "@/components/icons";

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
    <div className="flex items-center gap-2">
      {([1, 0] as const).map((value) => (
        <button
          key={value}
          onClick={() => rate(value)}
          disabled={sent !== null}
          aria-label={value ? "Helpful" : "Not helpful"}
          aria-pressed={sent === value}
          className={`grid h-[34px] w-[34px] place-items-center rounded-full border disabled:cursor-default ${
            sent === value
              ? "border-accent bg-accent-soft text-accent-text"
              : "border-border bg-background text-muted hover:text-foreground"
          }`}
        >
          <ThumbIcon size={15} down={value === 0} />
        </button>
      ))}
      {sent !== null && <span className="text-xs text-muted">Thanks!</span>}
    </div>
  );
}
