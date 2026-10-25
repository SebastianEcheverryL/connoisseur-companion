/**
 * The recommender's text answer repeats every pick as a numbered Markdown list ("**Restaurants**\n1. ...").
 * When the picks are shown as cards, keep only the rest: the summary sentence and the closing notes
 * (OpenStreetMap credit, tips, allergy warning).
 */
export function answerWithoutPickLists(text: string): string {
  return text
    .split(/\n\s*\n/)
    .filter((block) => {
      const lines = block.trim().split("\n");
      const isHeading = /^\*\*[^*]+\*\*$/.test(lines[0]?.trim() ?? "");
      return !(isHeading && lines.slice(1).some((line) => /^\s*\d+\.\s/.test(line)));
    })
    .join("\n\n")
    .trim();
}

/** "Laureles, Comuna 11, Medellín, Antioquia, Colombia" -> "Laureles, Medellín"-ish: the first two parts. */
export function shortPlace(displayName: string): string {
  return displayName
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .slice(0, 2)
    .join(", ");
}

/** True when the user writes in Spanish, per the profile agent (drives the few fixed UI strings in a reply). */
export function isSpanish(language: string | null | undefined): boolean {
  return /^(spanish|español|espanol|es)\b/i.test(language ?? "");
}
