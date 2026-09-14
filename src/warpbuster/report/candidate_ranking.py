"""Stable JSON and console presentation for advisory candidate ranking."""

from __future__ import annotations

import json

from warpbuster.models.reconstruction import CandidateRankingResult


def candidate_ranking_report(result: CandidateRankingResult) -> dict[str, object]:
    return {
        "policy_version": result.policy_version,
        "application_allowed": result.application_allowed,
        "config": json.loads(result.config_json),
        "rankings": [
            {
                "gap_id": ranking.gap_id,
                "status": ranking.status.value,
                "recommended_candidate_id": ranking.recommended_candidate_id,
                "best_score": ranking.best_score,
                "score_margin": ranking.score_margin,
                "near_best_candidate_ids": list(ranking.near_best_candidate_ids),
                "search_complete": ranking.search_complete,
                "reasons": list(ranking.reasons),
                "candidates": [
                    {
                        "candidate_id": candidate.candidate_id,
                        "provider": candidate.provider.value,
                        "eligible": candidate.eligible,
                        "score": candidate.score,
                        "observed_components": candidate.observed_components,
                        "component_scores": dict(candidate.component_scores),
                        "evidence": candidate.evidence(),
                        "reasons": list(candidate.reasons),
                        "source_candidate_id": candidate.source_candidate_id,
                        "source_role": candidate.source_role,
                        "coordinates": [
                            [point.latitude, point.longitude] for point in candidate.coordinates
                        ],
                        "application_allowed": False,
                    }
                    for candidate in ranking.candidates
                ],
            }
            for ranking in result.rankings
        ],
    }


def candidate_ranking_console(result: CandidateRankingResult) -> list[str]:
    lines = ["", "Gap candidate ranking", "Application: DISABLED (recommendation only)"]
    for ranking in result.rankings:
        recommended = ranking.recommended_candidate_id or "none"
        lines.append(
            f"  {ranking.gap_id}: {ranking.status.value.upper()}; "
            f"best={ranking.best_score}; margin={ranking.score_margin}; "
            f"recommended={recommended}; search_complete={ranking.search_complete}"
        )
        for candidate in sorted(
            ranking.candidates,
            key=lambda item: (
                item.score is None,
                item.score if item.score is not None else 0,
                item.candidate_id,
            ),
        ):
            lines.append(
                f"    {candidate.provider.value} {candidate.candidate_id}: "
                f"eligible={candidate.eligible}; score={candidate.score}; "
                f"components={dict(candidate.component_scores)}"
            )
    return lines
