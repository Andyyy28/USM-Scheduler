from __future__ import annotations

from copy import deepcopy

import pytest
from django.utils import timezone

from scheduler import models
from scheduler.services import formal_studies
from scheduler.services.research_metrics import NO_FORMAL_CONCLUSION, analyze_experiment_study
from tests.integration.test_formal_studies import _ready_formal_study

pytestmark = pytest.mark.django_db


def _rehash(profile: dict) -> None:
    profile["profile_hash"] = models.canonical_sha256({
        key: value for key, value in profile.items() if key != "profile_hash"
    })


def test_self_consistent_fabricated_tuning_plan_cannot_create_formal_study() -> None:
    study, actor = _ready_formal_study()
    profiles = deepcopy(study.protocol_manifest["solver_profiles"])
    for profile in profiles.values():
        profile["plan_hash"] = "f" * 64
        _rehash(profile)

    with pytest.raises(formal_studies.FormalStudyError) as caught:
        formal_studies.create_formal_study(
            source_snapshot=study.source_snapshot, actor=actor, solver_profiles=profiles
        )

    assert "INCOMPLETE_PERSISTED_TUNING_PILOT" in {issue.code for issue in caught.value.issues}
    assert models.ExperimentStudy.objects.filter(mode=models.ExperimentMode.FORMAL).count() == 1


def test_changed_selection_and_arbitrary_mutation_are_rejected() -> None:
    study, _actor = _ready_formal_study()
    profiles = deepcopy(study.protocol_manifest["solver_profiles"])
    profiles["GA"]["configuration"]["mutation_rate"] = 0.123
    _rehash(profiles["GA"])
    with pytest.raises(formal_studies.FormalStudyError) as caught:
        formal_studies._normalize_solver_profiles(profiles)
    assert "INVALID_GA_MUTATION" in {issue.code for issue in caught.value.issues}

    profiles = deepcopy(study.protocol_manifest["solver_profiles"])
    profiles["CP_SAT"]["selection_metrics"]["feasibility_rate"] = 1.0
    _rehash(profiles["CP_SAT"])
    with pytest.raises(formal_studies.FormalStudyError) as caught:
        formal_studies._authenticate_persisted_tuning_profiles(profiles)
    assert "TUNING_SELECTION_MISMATCH" in {issue.code for issue in caught.value.issues}


def test_ga_mutation_formula_is_resolved_for_each_frozen_scale() -> None:
    study, _actor = _ready_formal_study()
    multiplier = study.protocol_manifest["solver_profiles"]["GA"]["tuning_parameters"]["mutation_multiplier"]
    for batch in study.batches.all():
        mutable_count = formal_studies._tuning_mutable_event_count(batch.snapshot)
        run = batch.runs.filter(algorithm="GA", purpose="MEASURED").first()
        expected = min(1.0, multiplier / mutable_count) if mutable_count else 0.0
        assert run.configuration["mutation_rate"] == expected
        assert run.configuration["mutable_event_count"] == mutable_count


@pytest.mark.parametrize(("field", "value", "code"), [
    ("event_count", 99, "FROZEN_EVENT_COUNT_MISMATCH"),
    ("candidate_count", 999, "FROZEN_CANDIDATE_COUNT_MISMATCH"),
    ("candidate_map", {}, "FROZEN_CANDIDATE_KEYS_MISMATCH"),
    ("meeting_headcounts", {}, "FROZEN_MEETING_KEYS_MISMATCH"),
    ("instructor_daily_load_evidence", [], "FROZEN_DAILY_LOAD_EVIDENCE_MISMATCH"),
])
def test_snapshot_redundant_evidence_must_agree(field: str, value: object, code: str) -> None:
    study, _actor = _ready_formal_study()
    snapshot = study.source_snapshot
    setattr(snapshot, field, value)
    assert code in {issue.code for issue in formal_studies.validate_source_snapshot(snapshot)}


def test_completed_rows_without_worker_provenance_cannot_claim_a_winner() -> None:
    study, _actor = _ready_formal_study()
    models.ScheduleRun.objects.filter(experiment_batch__study=study).update(
        status=models.RunStatus.NO_SOLUTION,
        finished_at=timezone.now(),
    )
    models.ExperimentStudy.objects.filter(pk=study.pk).update(status=models.StudyStatus.COMPLETED)
    study.refresh_from_db()

    report = analyze_experiment_study(study, protocol_valid=True, resamples=100)

    assert report["formal_conclusion"] == NO_FORMAL_CONCLUSION
    assert report["integrity"]["effective_protocol_valid"] is False
    assert "MISSING_TERMINAL_PROVENANCE" in {
        issue["code"] for issue in report["integrity"]["terminal_audit"]["issues"]
    }
