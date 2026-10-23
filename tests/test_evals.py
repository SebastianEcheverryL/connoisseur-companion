"""Evaluation helpers and the grounding check, without any LLM."""
import pytest

from connoisseur.agents import grounding
from connoisseur.agents.grounding import GroundingCheck
from connoisseur.schemas import UserContext
from evals import judge, metrics
from evals.run import check_e2e, check_profile


def test_ranking_metrics():
    assert metrics.first_relevant_rank(["a", "b", "c"], {"c"}) == 3
    assert metrics.first_relevant_rank(["a"], {"z"}) is None
    assert metrics.hit_at_k(3, 5) == 1.0 and metrics.hit_at_k(None, 5) == 0.0
    assert metrics.reciprocal_rank(4) == 0.25
    assert metrics.mean([]) is None  # reported as n/a, never as a misleading 0


def test_profile_checks_fold_accents_and_handle_missing_location():
    ctx = UserContext(intent="restaurant", language="Spanish", location="Ciudad de México", near_me=False,
                      cuisines=["tacos"], search_query="tacos al pastor")
    checks = check_profile(ctx, {"intent": "restaurant", "language": "spanish", "location_contains": "mexico",
                                 "near_me": False, "cuisines_any": ["taco"]})
    assert all(checks.values())
    assert check_profile(UserContext(intent="restaurant", location="Paris"),
                         {"intent": "restaurant", "location_contains": None})["location"] is False


def test_e2e_checks():
    result = {"route": ["intent:lookup", "tool:get_review", "guard:output:ok"], "answer": "Sakura is calm.",
              "cards": [{"type": "restaurant", "source": "osm"}]}
    checks = check_e2e(result, {"tools_any": ["get_review"], "route_any_prefix": ["guard:output"],
                                "answer_regex": ["(?i)sakura"], "answer_not_regex": ["reservations"],
                                "cards_min": 1, "cards_source": "osm"})
    assert all(checks.values())
    assert check_e2e(result, {"tools_any": ["semantic_search"]}) == {"tools": False}


@pytest.mark.parametrize(
    ("quote", "found"),
    [
        ('diet_tags: ["vegan"]', True),                       # judge dropped the key quotes
        ("{'name': 'Iron Griddle', 'rating': 4.3}", True),     # single quotes instead of double
        ('location: "Little Tokyo"', True),                    # compact JSON in the evidence, spaced quote
        ("rooftop terrace", False),                            # not in the evidence
        ("", False),
    ],
)
def test_judge_quotes_must_exist_in_evidence(quote, found):
    evidence = '{"name": "Iron Griddle", "rating": 4.3, "diet_tags": ["vegan"]} {"location":"Little Tokyo"}'
    assert judge._quote_found(quote, evidence) is found


async def test_judge_counts_a_supported_claim_with_a_fake_quote_as_unsupported(monkeypatch):
    async def fake_ask(schema, system, user, model):
        return judge.GroundednessVerdict(claims=[
            judge.ClaimVerdict(claim="rating 4.3", verdict="supported", evidence_quote='"rating": 4.3'),
            judge.ClaimVerdict(claim="open late", verdict="supported", evidence_quote="open until midnight"),
        ])

    monkeypatch.setattr(judge, "_ask", fake_ask)
    result = await judge.groundedness("q", "a", ['{"rating": 4.3}'])
    assert result == {"score": 0.5, "grounded": False, "claims": 2, "unsupported_claims": ["open late"]}


async def test_grounding_check_replaces_answer_only_when_claims_are_unsupported(monkeypatch):
    async def unsupported(question, answer, evidence):
        return GroundingCheck(unsupported_claims=["takes reservations"],
                              revised_answer="I don't have reservation information for Sakura Garden.")

    monkeypatch.setattr(grounding, "check", unsupported)
    answer, revised = await grounding.enforce("q", "Yes, it takes reservations.", ["{}"])
    assert revised and answer.startswith("I don't have")

    async def supported(question, answer, evidence):
        return GroundingCheck(unsupported_claims=[], revised_answer=answer)

    monkeypatch.setattr(grounding, "check", supported)
    assert await grounding.enforce("q", "Fine answer.", ["{}"]) == ("Fine answer.", False)

    async def broken(question, answer, evidence):
        return None

    monkeypatch.setattr(grounding, "check", broken)
    assert await grounding.enforce("q", "Fine answer.", ["{}"]) == ("Fine answer.", False)


def test_judge_drops_claims_that_restate_an_unknown():
    answer = "Sakura Garden is in Little Tokyo. I don't have information on whether Sakura Garden takes reservations."
    assert judge._restates_unknown("Sakura Garden takes reservations", answer)
    assert not judge._restates_unknown("Sakura Garden is in Little Tokyo", answer)
