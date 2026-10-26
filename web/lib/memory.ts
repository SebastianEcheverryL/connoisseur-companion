import type { Memory } from "./types";

/** The backend's session id for a chat: the same cleanup the chat route applies before sending it. */
export function toSessionId(id: string): string {
  return id.replace(/[^\w-]/g, "-").slice(0, 64);
}

const ALLERGY_LABEL: Record<string, [string, string]> = {
  peanut: ["Peanut allergy", "Alergia al maní"],
  tree_nut: ["Tree nut allergy", "Alergia a frutos secos"],
  shellfish: ["Shellfish allergy", "Alergia a mariscos"],
  fish: ["Fish allergy", "Alergia al pescado"],
  dairy: ["Dairy allergy", "Alergia a lácteos"],
  egg: ["Egg allergy", "Alergia al huevo"],
  soy: ["Soy allergy", "Alergia a la soya"],
  gluten: ["Gluten intolerance", "Intolerancia al gluten"],
  sesame: ["Sesame allergy", "Alergia al sésamo"],
};

export type MemoryChip = { label: string; kind: "allergy" | "diet" | "place" };

/** Chips for "Remembered": allergies first (they matter most), then diet, then the saved city. */
export function memoryChips(memory: Memory | null | undefined, spanish: boolean): MemoryChip[] {
  if (!memory) return [];
  const allergies = memory.allergies.map((a) => ({
    label: ALLERGY_LABEL[a]?.[spanish ? 1 : 0] ?? a,
    kind: "allergy" as const,
  }));
  const diet = memory.dietary.map((d) => ({ label: d, kind: "diet" as const }));
  // The city is only news when it came from an earlier chat; otherwise "Understood" already shows it
  const place =
    memory.from_saved && memory.location
      ? [{ label: spanish ? `Ciudad guardada: ${memory.location}` : `Saved city: ${memory.location}`, kind: "place" as const }]
      : [];
  return [...allergies, ...diet, ...place];
}
