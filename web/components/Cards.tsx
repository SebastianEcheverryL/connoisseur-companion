import type { Card } from "@/lib/types";

const SOURCE_LABEL: Record<string, string> = {
  dataset: "California guide",
  osm: "OpenStreetMap",
  web: "Web",
};

/** Structured picks next to the text answer: every card shows its source and, if any, where its rating comes from. */
export default function Cards({ cards }: { cards: Card[] }) {
  const restaurants = cards.filter((c) => c.type === "restaurant");
  const recipes = cards.filter((c) => c.type === "recipe");
  return (
    <>
      {restaurants.length > 0 && (
        <ul className="grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
          {restaurants.map((card) => (
            <RestaurantCard key={card.id} card={card} />
          ))}
        </ul>
      )}
      {recipes.length > 0 && (
        <ul className="flex flex-col gap-3">
          {recipes.map((card) => (
            <RecipeCard key={card.id} card={card} />
          ))}
        </ul>
      )}
    </>
  );
}

function RestaurantCard({ card }: { card: Card }) {
  const meta = [card.cuisine, card.location, card.price_range ? "$".repeat(card.price_range) : null].filter(Boolean);
  return (
    <li className="flex flex-col gap-1.5 rounded-[20px] border border-border bg-surface px-4 pt-3.5 pb-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[.06em] text-accent">
          {SOURCE_LABEL[card.source ?? ""] ?? card.source ?? "Guide"}
        </span>
        {card.rating != null && <span className="text-[13px] font-semibold">★ {card.rating}</span>}
      </div>
      <h3 className="font-display text-[19px] leading-[1.15]">{card.name}</h3>
      {meta.length > 0 && <p className="text-[13px] text-muted">{meta.join(" · ")}</p>}
      {card.reasoning && <p className="text-[13px] leading-[1.45] text-pretty">{card.reasoning}</p>}
      {card.rating != null && card.rating_source && (
        <p className="text-xs text-muted">
          Rating from{" "}
          {card.review_url ? (
            <a href={card.review_url} target="_blank" rel="noopener noreferrer" className="text-accent underline">
              {card.rating_source}
            </a>
          ) : (
            card.rating_source
          )}
          {card.review_count ? ` · ${card.review_count.toLocaleString()} reviews` : ""}
        </p>
      )}
      {card.url && card.source === "osm" && (
        <a
          href={card.url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-auto pt-1 text-xs font-semibold text-accent underline"
        >
          View on map / website
        </a>
      )}
    </li>
  );
}

function RecipeCard({ card }: { card: Card }) {
  const ingredients = card.ingredients ?? [];
  const shown = ingredients.slice(0, 6);
  const scores = [
    card.text_score != null ? { label: "Text match", value: card.text_score } : null,
    card.image_score != null ? { label: "Image match", value: card.image_score } : null,
  ].filter((s): s is { label: string; value: number } => s !== null);
  if (scores.length === 0 && card.score != null) scores.push({ label: "Match", value: card.score });

  return (
    <li className="grid items-center gap-5 rounded-[22px] border border-border bg-surface px-5 py-4 md:grid-cols-[minmax(0,1fr)_190px]">
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="font-display text-[19px] leading-[1.15]">{card.name}</h3>
          {card.cuisine && (
            <span className="rounded-full bg-accent-soft px-[9px] py-[3px] text-[11px] font-semibold text-accent-text">
              {card.cuisine}
            </span>
          )}
        </div>
        {card.reasoning && <p className="text-[13px] leading-[1.45] text-pretty">{card.reasoning}</p>}
        {shown.length > 0 && (
          <ul aria-label="Ingredients" className="flex flex-wrap gap-[5px]">
            {shown.map((item) => (
              <li key={item} className="rounded-full border border-border px-[9px] py-[3px] text-xs text-muted">
                {item}
              </li>
            ))}
            {ingredients.length > shown.length && (
              <li className="px-1 py-[3px] text-xs text-muted">+{ingredients.length - shown.length} more</li>
            )}
          </ul>
        )}
      </div>
      {scores.length > 0 && (
        <div className="flex flex-col gap-2 text-xs text-muted">
          {scores.map((score) => (
            <ScoreBar key={score.label} {...score} />
          ))}
        </div>
      )}
    </li>
  );
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between">
        <span>{label}</span>
        <span className="font-semibold text-foreground">{value.toFixed(2)}</span>
      </div>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={value}
        className="h-1.5 rounded-full bg-surface-2"
      >
        <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
