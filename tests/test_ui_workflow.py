from scheduler.views import _workflow_steps


def _states(**overrides: bool) -> tuple[list[str], str]:
    values = {
        "has_term": False,
        "has_import": False,
        "has_prepared_data": False,
        "has_checked_data": False,
        "has_run": False,
        "has_successful_run": False,
        "has_timetable": False,
        "has_review": False,
        "has_approval": False,
    }
    values.update(overrides)
    steps, next_step = _workflow_steps(**values)
    return [step.state for step in steps], next_step.title


def test_empty_workflow_starts_with_term_setup() -> None:
    states, next_title = _states()
    assert states == ["Not started"] * 5
    assert next_title == "Set up the academic term"


def test_prepared_data_makes_generation_ready() -> None:
    states, next_title = _states(has_term=True, has_prepared_data=True)
    assert states == ["Complete", "Complete", "Ready", "Not started", "Not started"]
    assert next_title == "Generate a schedule"


def test_checked_or_running_data_marks_generation_in_progress() -> None:
    states, _ = _states(has_term=True, has_prepared_data=True, has_checked_data=True)
    assert states[2] == "In progress"


def test_generated_timetable_makes_review_ready() -> None:
    states, next_title = _states(
        has_term=True,
        has_prepared_data=True,
        has_successful_run=True,
        has_timetable=True,
    )
    assert states == ["Complete", "Complete", "Complete", "Complete", "Ready"]
    assert next_title == "Review and approve"


def test_review_and_approval_progress() -> None:
    common = {
        "has_term": True,
        "has_prepared_data": True,
        "has_successful_run": True,
        "has_timetable": True,
    }
    reviewing, _ = _states(**common, has_review=True)
    approved, next_title = _states(**common, has_review=True, has_approval=True)
    assert reviewing[-1] == "In progress"
    assert approved == ["Complete"] * 5
    assert next_title == "Review and approve"
