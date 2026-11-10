"""Default OSS contribution workflow — n8n-style node graph over factory apps."""

from __future__ import annotations

from oss_agent.workflow.graph import Connection, WorkflowDefinition, WorkflowNode

# Node ids are stable; names match factory step names for board UX.
OSS_CONTRIBUTION_WORKFLOW = WorkflowDefinition(
    id="wf_oss_contribution",
    name="oss-contribution",
    nodes=(
        WorkflowNode(id="n_trigger", name="manual_trigger", type="trigger"),
        WorkflowNode(
            id="n_research",
            name="project_research",
            type="app",
            parameters={"app": "khoj_last30days_research"},
        ),
        WorkflowNode(
            id="n_if_research",
            name="if_research_passed",
            type="if",
            parameters={"boolean_key": "passed", "equals": True},
        ),
        WorkflowNode(
            id="n_analyze",
            name="analyze",
            type="app",
            parameters={"app": "paperclip_analysis"},
        ),
        WorkflowNode(
            id="n_if_go",
            name="if_recommendation_go",
            type="if",
            parameters={"boolean_key": "passed", "equals": True},
        ),
        WorkflowNode(
            id="n_await_analysis",
            name="await_analysis_approval",
            type="wait",
            parameters={"app": "governance_gate"},
        ),
        WorkflowNode(
            id="n_intake",
            name="intake",
            type="app",
            parameters={"app": "scout"},
        ),
        WorkflowNode(
            id="n_discuss",
            name="discuss",
            type="app",
            parameters={"app": "discussion_draft"},
        ),
        WorkflowNode(
            id="n_await_discuss",
            name="await_discuss_approval",
            type="wait",
            parameters={"app": "gateway_comment"},
        ),
        WorkflowNode(
            id="n_reproduce",
            name="reproduce",
            type="app",
            parameters={"app": "sandbox"},
        ),
        WorkflowNode(
            id="n_implement",
            name="implement",
            type="app",
            parameters={"app": "patch"},
        ),
        WorkflowNode(
            id="n_verify",
            name="verify",
            type="app",
            parameters={"app": "verify"},
        ),
        WorkflowNode(
            id="n_quality",
            name="quality_analysis",
            type="app",
            parameters={"app": "strix_quality"},
        ),
        WorkflowNode(
            id="n_if_quality",
            name="if_quality_passed",
            type="if",
            parameters={"boolean_key": "passed", "equals": True},
        ),
        WorkflowNode(
            id="n_await_pr",
            name="await_approval",
            type="wait",
            parameters={"app": "gateway_draft"},
        ),
        WorkflowNode(
            id="n_review",
            name="review_iterate",
            type="app",
            parameters={"app": "review_ci"},
        ),
        WorkflowNode(
            id="n_if_review",
            name="if_review_ready",
            type="if",
            parameters={"boolean_key": "passed", "equals": True},
        ),
        WorkflowNode(
            id="n_await_merge",
            name="await_merge",
            type="wait",
            parameters={"app": "sponsor_merge"},
        ),
        WorkflowNode(
            id="n_done",
            name="done",
            type="noop",
            parameters={},
        ),
        WorkflowNode(
            id="n_blocked",
            name="blocked",
            type="noop",
            parameters={"terminal": "blocked"},
        ),
    ),
    connections=(
        Connection("n_trigger", "n_research"),
        Connection("n_research", "n_if_research"),
        Connection("n_if_research", "n_analyze", source_output="true"),
        Connection("n_if_research", "n_blocked", source_output="false"),
        Connection("n_analyze", "n_if_go"),
        Connection("n_if_go", "n_await_analysis", source_output="true"),
        Connection("n_if_go", "n_blocked", source_output="false"),
        Connection("n_await_analysis", "n_intake"),
        Connection("n_intake", "n_discuss"),
        Connection("n_discuss", "n_await_discuss"),
        Connection("n_await_discuss", "n_reproduce"),
        Connection("n_reproduce", "n_implement"),
        Connection("n_implement", "n_verify"),
        Connection("n_verify", "n_quality"),
        Connection("n_quality", "n_if_quality"),
        Connection("n_if_quality", "n_await_pr", source_output="true"),
        Connection("n_if_quality", "n_blocked", source_output="false"),
        Connection("n_await_pr", "n_review"),
        Connection("n_review", "n_if_review"),
        Connection("n_if_review", "n_await_merge", source_output="true"),
        Connection("n_if_review", "n_blocked", source_output="false"),
        Connection("n_await_merge", "n_done"),
    ),
)

OSS_CONTRIBUTION_WORKFLOW.validate()
