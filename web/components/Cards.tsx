import type { Card } from "@/lib/types";

const SOURCE: Record<string, { label: string; className: string }> = {
  dataset: { label: "California guide", className: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200" },
  osm: { label: "OpenStreetMap", className: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200" },
  web: { label: "Web", className: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200" },
};

/** Structured cards next to the text answer: every pick shows its source and, if any, where its rating comes from. */
export default function Cards({ cards }: { cards: Card[] }) {
  return (
    <ul className="grid gap-3 sm:grid-cols-2">
      {cards.map((card) => (
        <li key={card.id} className="rounded-xl border border-border bg-surface p-3 text-sm">
          <div className="flex items-start justify-between gap-2">
            <h3 className="font-medium">{card.name}</h3>
            {card.type === "restaurant" && card.source && (
              <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${SOURCE[card.source]?.className ?? ""}`}>
                {SOURCE[card.source]?.label ?? card.source}
              </span>
            )}
            {card.type === "recipe" && (
              <span className="shrink-0 rounded-full bg-violet-100 px-2 py-0.5 text-[11px] text-violet-900 dark:bg-violet-950 dark:text-violet-200">
                Recipe
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-muted">
            {[card.cuisine, card.location, card.price_range ? "$".repeat(card.price_range) : null]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {card.rating != null && (
            <p className="mt-1 text-xs">
              ⭐ {card.rating}/5{" "}
              {card.rating_source &&
                (card.review_url ? (
                  <a href={card.review_url} target="_blank" rel="noopener noreferrer" className="text-accent underline">
                    {card.rating_source}
                  </a>
                ) : (
                  card.rating_source
                ))}
              {card.review_count ? ` · ${card.review_count.toLocaleString()} reviews` : ""}
            </p>
          )}
          {card.url && card.source === "osm" && (
            <a href={card.url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block text-xs text-accent underline">
              View on map / website
            </a>
          )}
        </li>
      ))}
    </ul>
  );
}
