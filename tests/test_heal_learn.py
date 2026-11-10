"""Self-heal from sandbox evidence, then self-learn on the next run."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from oss_agent.audit.log import FileAndDbAuditLog
from oss_agent.config import AppConfig
from oss_agent.db.models import Action, Artifact, Lesson, Project
from oss_agent.db.session import create_all, make_engine
from oss_agent.gateway.service import GatewayError, GitHubGatewayService
from oss_agent.heal.diagnose import diagnose_failure
from oss_agent.heal.loop import heal_until_green, stand_down_comment
from oss_agent.heal.proof import run_heal_learn_proof, run_recorded_attempt
from oss_agent.learn.apply import implementation_blocked, title_from_lessons
from oss_agent.learn.models import LessonRecord
from oss_agent.learn.store import LessonStore
from oss_agent.loop.offline import load_contribution_fixture
from oss_agent.orchestrator.engine import Orchestrator
from oss_agent.orchestrator.states import TaskState
from oss_agent.protocols import OutwardActionDraft
from oss_agent.sandbox.fake import FakeSandboxRunner
from oss_agent.scout.choose import choose_allowlisted_issue
from oss_agent.scout.ranking import rank_issues

ROOT = Path(__file__).resolve().parents[1]
REPO = "fixtures/friendly_tiny"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    engine = make_engine(f"sqlite:///{tmp_path / 'lessons.db'}")
    create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(
        Project(id="proj-friendly", repo=REPO, policy_json={}, ai_allowed=True, rate_limits={})
    )
    session.commit()
    yield session
    session.close()


def _write_pkg(root: Path, *, func: str, expr: str, delta: int) -> None:
    src = root / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text(
        f"def {func}(a: int, b: int) -> int:\n    return {expr} + {delta}\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_pkg.py").write_text(
        "from pkg import " + func + "\n\n" + f"def test_{func}():\n    assert {func}(2, 2) == 4\n",
        encoding="utf-8",
    )


def _log(func: str, actual: int, expected: int) -> str:
    return (
        f"def test_{func}():\n"
        f">       assert {func}(2, 2) == {expected}\n"
        f"E       assert {actual} == {expected}\n"
        f"E        +  where {actual} = {func}(2, 2)\n"
        f"FAILED tests/test_pkg.py::test_{func} - assert {actual} == {expected}\n"
    )


def test_repairs_follow_each_log_not_one_patch(tmp_path: Path) -> None:
    plus_one = tmp_path / "plus-one"
    plus_three = tmp_path / "plus-three"
    _write_pkg(plus_one, func="add", expr="a + b", delta=1)
    _write_pkg(plus_three, func="mul", expr="a * b", delta=3)
    left = diagnose_failure(_log("add", 5, 4), plus_one)
    right = diagnose_failure(_log("mul", 7, 4), plus_three)
    assert left is not None and right is not None
    assert (left.old, left.new) != (right.old, right.new)
    assert left.signature != right.signature
    assert "+ 1" in left.old and "+ 1" not in left.new
    assert "+ 3" in right.old and "+ 3" not in right.new
    prose = "The model says replace the body with return a + b."
    assert diagnose_failure(prose, plus_one) is None


def test_docstring_repair_uses_the_flagged_phrase(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    fixture_path = root / "fixtures" / "loop" / "friendly_tiny_contribution.json"
    fixture = load_contribution_fixture(fixture_path)
    tree = tmp_path / "docs"
    src = tree / "src" / "friendly_tiny"
    src.mkdir(parents=True)
    module = root / "fixtures" / "projects" / "friendly_tiny" / "src" / "friendly_tiny"
    source = (module / "__init__.py").read_text(encoding="utf-8")
    (src / "__init__.py").write_text(source, encoding="utf-8")
    other = tmp_path / "other" / "src" / "other"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text(
        "def sub(a: int, b: int) -> int:\n"
        '    """Obsolete comment: do not ship this helper."""\n'
        "    return a - b\n",
        encoding="utf-8",
    )
    ci_log = "FAILED tests/test_add.py - docstring still says Intentionally wrong"
    add_plan = diagnose_failure(ci_log, tree)
    sub_plan = diagnose_failure(
        "FAILED tests/test_sub.py - docstring still says Obsolete comment",
        tmp_path / "other",
    )
    assert add_plan is not None and sub_plan is not None
    assert add_plan.new != sub_plan.new
    assert "add" in add_plan.new
    assert "sub" in sub_plan.new
    assert fixture.follow_up_fix is not None
    assert add_plan.new != fixture.follow_up_fix.new
    assert "Intentionally wrong" not in add_plan.new


@pytest.mark.asyncio
async def test_heal_caps_at_two_and_third_failure_stands_down(tmp_path: Path) -> None:
    root = tmp_path / "triple"
    src = root / "src" / "sample_math"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b + 1\n"
        "def mul(a: int, b: int) -> int:\n"
        "    return a * b + 3\n"
        "def sub(a: int, b: int) -> int:\n"
        "    return a - b + 2\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_ops.py").write_text(
        "from sample_math import add, mul, sub\n\n"
        "def test_add():\n    assert add(2, 2) == 4\n"
        "def test_mul():\n    assert mul(2, 2) == 4\n"
        "def test_sub():\n    assert sub(3, 1) == 2\n",
        encoding="utf-8",
    )
    runner = FakeSandboxRunner()
    outcome = await heal_until_green(
        runner,
        task_id="cap",
        workdir=root,
        repo="fixtures/triple",
        max_repairs=2,
        issue_title="three bugs",
    )
    text = (src / "__init__.py").read_text(encoding="utf-8")
    assert outcome.ok is False
    assert outcome.reason == "attempt_cap"
    assert outcome.repair_attempts == 2
    assert outcome.real_failures == 3
    assert "return a + b + 1" not in text
    assert "return a * b + 3" not in text
    assert "return a - b + 2" in text
    assert outcome.stand_down_body is not None
    assert AppConfig().policy.ai_disclosure not in outcome.stand_down_body
    assert any(lesson.kind == "stand_down" for lesson in outcome.lessons)
    assert "GITHUB_TOKEN" not in runner.last_env
    assert "OPENAI_API_KEY" not in runner.last_env


@pytest.mark.asyncio
async def test_flake_is_not_retried_and_typo_is_not_learned(tmp_path: Path, db: Session) -> None:
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    original = "def add(a: int, b: int) -> int:\n    return a + b + 1\n"
    (src / "__init__.py").write_text(original, encoding="utf-8")
    flake = await heal_until_green(
        FakeSandboxRunner(),
        task_id="flake",
        workdir=tmp_path,
        repo=REPO,
        evidence_log="intermittent failure: tests/test_timing.py",
        known_flakes=["tests/test_timing.py"],
    )
    assert flake.reason == "flake_no_retry"
    assert flake.repair_attempts == 0
    assert flake.lessons == ()
    assert (src / "__init__.py").read_text(encoding="utf-8") == original
    typo = await heal_until_green(
        FakeSandboxRunner(),
        task_id="typo",
        workdir=tmp_path,
        repo=REPO,
        evidence_log="nit: typo in the README spelling",
    )
    assert typo.reason == "typo_skipped"
    assert typo.lessons == ()
    store = LessonStore(db)
    assert store.record_maintainer(REPO, "nit: typo in README", "nit") is None
    kept = store.record_maintainer(
        REPO,
        "bug: don't touch src/friendly_tiny/legacy.py on this repo",
        "bug",
    )
    assert kept is not None
    assert kept.kind == "maintainer_correction"
    assert "src/friendly_tiny/legacy.py" in kept.avoid_files
    assert implementation_blocked("src/friendly_tiny/legacy.py", store.for_repo(REPO)) is True


@pytest.mark.asyncio
async def test_repair_uses_stored_lesson_pattern(tmp_path: Path) -> None:
    _write_pkg(tmp_path, func="add", expr="a + b", delta=1)
    log = _log("add", 5, 4)
    plan = diagnose_failure(log, tmp_path)
    assert plan is not None
    lesson = LessonRecord(
        id="les_paren",
        repo=REPO,
        signature=plan.signature,
        kind="failure",
        rule=plan.rule,
        avoid_files=(),
        failed_pattern="return a + b + 1",
        preferred_pattern="return (a + b)",
        module_relpath=plan.module_relpath,
        title_block="",
        title_hint="fix: add from lesson",
        evidence_hash="abc",
        verified=True,
    )
    outcome = await heal_until_green(
        FakeSandboxRunner(),
        task_id="lesson-repair",
        workdir=tmp_path,
        repo=REPO,
        evidence_log=log,
        lessons=[lesson],
    )
    text = (tmp_path / plan.module_relpath).read_text(encoding="utf-8")
    assert outcome.ok is True
    assert outcome.from_lesson is True
    assert "return (a + b)" in text
    assert title_from_lessons("fallback", [lesson]) == "fix: add from lesson"


@pytest.mark.asyncio
async def test_second_run_follows_stored_lesson_not_the_prompt(db: Session, tmp_path: Path) -> None:
    proof = await run_heal_learn_proof(
        db,
        source_repo=ROOT / "fixtures" / "projects" / "friendly_tiny",
        issues_path=ROOT / "fixtures" / "issues" / "friendly_tiny_issues.json",
        work=tmp_path / "proof",
    )
    assert proof.run_a.first_verify_ok is False
    assert proof.run_a.healed is True
    assert proof.run_a.repair_attempts == 1
    assert proof.run_a.chosen_issue == 3
    run_a_text = (proof.run_a.workdir / "src" / "friendly_tiny" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "return a + b + 1" not in run_a_text
    assert "return a + b + 9" not in run_a_text
    hostile = (proof.run_b.workdir / ".oss_agent_issue.txt").read_text(encoding="utf-8")
    assert "return a + b + 9" in hostile
    assert proof.run_b.first_verify_ok is True
    assert proof.run_b.repair_attempts == 0
    assert proof.run_b.chosen_issue == 3
    assert proof.lesson_id in proof.run_b.applied_lesson_ids
    rows = list(
        db.execute(
            select(Lesson).where(
                Lesson.repo == REPO,
                Lesson.signature == proof.lesson_signature,
            )
        ).scalars()
    )
    assert len(rows) == 1
    assert rows[0].verified is True
    assert rows[0].evidence_hash == hashlib.sha256(proof.run_a.failure_log.encode()).hexdigest()
    ignored = await run_recorded_attempt(
        db,
        source_repo=ROOT / "fixtures" / "projects" / "friendly_tiny",
        issues_path=ROOT / "fixtures" / "issues" / "friendly_tiny_issues.json",
        workdir=tmp_path / "ignore",
        repo_name=REPO,
        use_lessons=False,
        task_id="ignore-lesson",
    )
    assert ignored.first_verify_ok is False
    assert ignored.repair_attempts == 1


def test_choose_skips_title_blocked_by_stand_down() -> None:
    issues = json.loads((ROOT / "fixtures" / "issues" / "friendly_tiny_issues.json").read_text())
    ranked = rank_issues("proj-friendly", issues)
    blocked = LessonRecord(
        id="les_block",
        repo=REPO,
        signature="assert:add:delta:+1",
        kind="stand_down",
        rule="Stand down. Do not retry this title.",
        avoid_files=(),
        failed_pattern="",
        preferred_pattern="",
        module_relpath="",
        title_block="add failing test for off-by-one",
        title_hint="",
        evidence_hash="",
        verified=False,
    )
    chosen = choose_allowlisted_issue(
        ranked,
        allowlisted_repos={REPO},
        repo=REPO,
        min_score=1.0,
        lessons=[blocked],
    )
    assert chosen.number != 3


def test_gateway_refuses_force_push_and_protected_branches(db: Session, tmp_path: Path) -> None:
    cfg = AppConfig()
    cfg.research.require_before_action = False
    audit = FileAndDbAuditLog(tmp_path / "audit.jsonl", db)
    orch = Orchestrator(db, cfg, audit)
    gw = GitHubGatewayService(db, cfg, audit)
    task = orch.create_task(project_id="proj-friendly", issue_id=None, state=TaskState.IN_REVIEW)
    art = Artifact(
        id="art_push",
        task_id=task.id,
        type="patch",
        path=str(tmp_path / "patch"),
        content_hash="abc",
        from_sandbox=True,
    )
    db.add(art)
    db.flush()
    body = stand_down_comment(repo=REPO, signature="assert:add:delta:+1", attempts=2)
    assert cfg.policy.ai_disclosure not in body
    comment_id = gw.enqueue(
        OutwardActionDraft(kind="comment", task_id=task.id, payload={"body": body, "issue": 3})
    )
    gw._mark_and_maybe_send(comment_id, approved_by="sponsor")
    action = db.get(Action, comment_id)
    assert action is not None
    assert cfg.policy.ai_disclosure in str(action.draft.get("body", ""))
    with pytest.raises(GatewayError, match="force_push_refused"):
        gw.enqueue(
            OutwardActionDraft(
                kind="push",
                task_id=task.id,
                payload={"body": "push", "branch": "oss-agent/issue-3", "force": True},
                evidence_artifact_ids=["art_push"],
            )
        )
    with pytest.raises(GatewayError, match="protected_branch"):
        gw.enqueue(
            OutwardActionDraft(
                kind="push",
                task_id=task.id,
                payload={"body": "push", "branch": "refs/heads/main", "force": False},
                evidence_artifact_ids=["art_push"],
            )
        )
    draft_id = gw.enqueue(
        OutwardActionDraft(
            kind="push",
            task_id=task.id,
            payload={"body": "push", "branch": "oss-agent/issue-3", "force": False},
            evidence_artifact_ids=["art_push"],
        )
    )
    sent = gw._mark_and_maybe_send(draft_id, approved_by="sponsor")
    assert sent.ok
    assert sent.remote_id is not None and sent.remote_id.startswith("dryrun_")
