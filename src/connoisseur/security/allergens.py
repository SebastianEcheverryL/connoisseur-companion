"""Deterministic allergy safety: a prompt rule alone was not enough.

In the red team, llama3.1 ignored the instruction "never claim a dish is allergen-free" and called a
Pad Thai "peanut-free" for a user with a severe peanut allergy. So, in code:
1. detect allergies mentioned by the user (English and Spanish),
2. drop recipes whose ingredient list contains the allergen before the LLM sees them,
3. strip sentences that claim something is free of / safe for an allergen,
4. append a fixed note to confirm ingredients.
"""
import re

# Allergy -> words that reveal it in a user message (en/es)
ALLERGY_MENTIONS: dict[str, tuple[str, ...]] = {
    "peanut": ("peanut", "maní", "mani", "cacahuate", "cacahuete"),
    "tree_nut": ("tree nut", "nuts", "almond", "cashew", "walnut", "frutos secos", "nueces", "almendra"),
    "shellfish": ("shellfish", "shrimp", "crustacean", "mariscos", "camarón", "camaron", "langostino"),
    "fish": ("fish allergy", "allergic to fish", "pescado"),
    "dairy": ("dairy", "lactose", "milk allergy", "lácteos", "lacteos", "lactosa"),
    "egg": ("egg allergy", "allergic to egg", "huevo"),
    # Not bare "soy": in Spanish it is the verb "to be" ("soy alérgico al maní")
    "soy": ("soy allergy", "allergic to soy", "soy-free", "soya", "soja"),
    "gluten": ("gluten", "celiac", "coeliac", "celíaco", "celiaco", "wheat allergy", "trigo"),
    "sesame": ("sesame", "sésamo", "sesamo", "ajonjolí", "ajonjoli"),
}

# Allergy -> ingredient words in the (English) recipe dataset
ALLERGEN_INGREDIENTS: dict[str, tuple[str, ...]] = {
    "peanut": ("peanut",),
    "tree_nut": ("almond", "cashew", "walnut", "pecan", "pistachio", "hazelnut", "macadamia", "pine nut"),
    "shellfish": ("shrimp", "prawn", "crab", "lobster", "scallop", "clam", "mussel", "oyster"),
    "fish": ("fish", "salmon", "tuna", "cod", "anchov", "sardine", "tilapia"),
    "dairy": ("milk", "cheese", "butter", "cream", "yogurt", "yoghurt", "ghee", "paneer", "parmesan", "mozzarella"),
    "egg": ("egg",),
    "soy": ("soy", "tofu", "edamame", "miso", "tempeh"),
    "gluten": ("flour", "wheat", "bread", "pasta", "spaghetti", "noodle", "soy sauce", "barley", "couscous",
               "tortilla", "breadcrumb", "pizza dough", "naan"),
    "sesame": ("sesame", "tahini"),
}

ALLERGY_CONTEXT = re.compile(r"allerg|al[eé]rgi|intoleran|celiac|cel[ií]ac|sin gluten|gluten[- ]free", re.IGNORECASE)

_CLAIM = re.compile(
    r"[^.!?\n]*\b("
    r"(is|are|it's|it is)\s+(completely\s+|totally\s+|100%\s+)?[\w-]*[- ]free"
    r"|safe for (you|your|a|people with)[^.!?\n]*allerg"
    r"|free (of|from) [\w\s-]*(allergen|peanut|nut|gluten|dairy|shellfish)"
    r"|(es|son|está|están)\s+(totalmente\s+|completamente\s+)?(libre|libres) de"
    r"|segur[oa]s? para (tu|tus|personas con)[^.!?\n]*alergi"
    r")[^.!?\n]*[.!?]?",
    re.IGNORECASE,
)


def detect_allergies(text: str, dietary: list[str] | None = None) -> set[str]:
    """Allergies the user mentioned. Requires an allergy context word, so "peanut sauce" alone is not one."""
    haystack = " ".join([text, *(dietary or [])]).lower()
    if not ALLERGY_CONTEXT.search(haystack):
        return set()
    return {
        allergy
        for allergy, words in ALLERGY_MENTIONS.items()
        if any(re.search(rf"\b{re.escape(w)}\b", haystack) for w in words)
    }


def contains_allergen(ingredients: list[str], allergies: set[str]) -> bool:
    text = " ".join(ingredients).lower()
    return any(word in text for allergy in allergies for word in ALLERGEN_INGREDIENTS.get(allergy, ()))


def strip_safety_claims(text: str) -> str:
    """Remove sentences that guarantee something is allergen-free or safe for an allergy."""
    return re.sub(r"\s{2,}", " ", _CLAIM.sub("", text)).strip()
