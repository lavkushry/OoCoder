"""Property tests: illegal state transitions are rejected."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from oss_agent.orchestrator.states import TaskState, TransitionTable


@given(src=st.sampled_from(list(TaskState)), dst=st.sampled_from(list(TaskState)))
def test_transition_table_consistent(src: TaskState, dst: TaskState) -> None:
    allowed = TransitionTable.can_transition(src, dst)
    if allowed:
        assert dst in TransitionTable.legal_targets(src) or dst == TaskState.ABANDONED
        TransitionTable.assert_transition(src, dst)
    else:
        try:
            TransitionTable.assert_transition(src, dst)
        except ValueError:
            return
        raise AssertionError(f"expected illegal: {src} -> {dst}")
