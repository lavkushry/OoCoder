"""M4: review thread classification including flaky-test case."""

from __future__ import annotations

from oss_agent.review.classify import classify_comment
from oss_agent.review.loop import handle_review_thread


def test_classify_categories() -> None:
    assert classify_comment("nit: trailing whitespace") == "nit"
    assert classify_comment("This introduces a bug in error handling") == "bug"
    assert classify_comment("Can we rethink the design here?") == "design"
    assert classify_comment("flaky test on CI") == "flake"
    assert classify_comment("Why this constant?") == "question"


def test_review_thread_with_flake() -> None:
    handling = handle_review_thread(
        [
            "nit: naming",
            "Looks like a flaky failure under load",
            "Could we use a different design?",
        ],
        ci_log="intermittent failure: tests/test_add.py",
        known_flakes=["tests/test_add.py"],
    )
    assert handling.flake_detected is True
    assert handling.escalate is True
    classes = {i["class"] for i in handling.items}
    assert "nit" in classes
    assert "flake" in classes
    assert "design" in classes
    assert all(i["status"] == "pending_approval" for i in handling.items)
