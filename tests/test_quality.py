"""Strix-inspired code quality / security analysis tests."""

from __future__ import annotations

from pathlib import Path

from oss_agent.config import AppConfig, QualityConfig
from oss_agent.quality.analyzer import analyze_patch_quality
from oss_agent.quality.findings import SEVERITY_TO_SARIF


def test_clean_minimal_fix_passes(tmp_path: Path) -> None:
    diff = """\
--- a/src/friendly_tiny/__init__.py
+++ b/src/friendly_tiny/__init__.py
@@ -1,3 +1,3 @@
 def add(a: int, b: int) -> int:
-    return a + b + 1
+    return a + b
"""
    report = analyze_patch_quality(
        workdir=tmp_path,
        diff=diff,
        changed_files=["src/friendly_tiny/__init__.py"],
        tests_verified=True,
    )
    assert report.passed is True
    assert report.highest_severity() is None or report.highest_severity() == "info"


def test_secret_in_diff_blocks(tmp_path: Path) -> None:
    diff = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1,1 +1,2 @@
+api_key = "sk-super-secret-token-value"
 print("hi")
"""
    report = analyze_patch_quality(
        workdir=tmp_path,
        diff=diff,
        changed_files=["src/app.py"],
        tests_verified=True,
    )
    assert report.passed is False
    assert any(f.severity == "critical" for f in report.findings)
    assert report.block_reason


def test_dangerous_eval_is_high(tmp_path: Path) -> None:
    diff = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1,0 +1,1 @@
+x = eval(user_input)
"""
    report = analyze_patch_quality(
        workdir=tmp_path,
        diff=diff,
        changed_files=["src/app.py"],
        config=AppConfig(quality=QualityConfig(block_on_severity="high")),
        tests_verified=True,
    )
    assert report.passed is False
    assert any(f.rule_id == "dangerous-eval" for f in report.findings)


def test_sarif_shape(tmp_path: Path) -> None:
    diff = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1,0 +1,1 @@
+password = "hunter2hunter2"
"""
    report = analyze_patch_quality(
        workdir=tmp_path,
        diff=diff,
        changed_files=["src/app.py"],
        tests_verified=True,
    )
    sarif = report.to_sarif()
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"]
    level = sarif["runs"][0]["results"][0]["level"]
    assert level in {"error", "warning", "note"}
    assert SEVERITY_TO_SARIF["critical"] == "error"


def test_missing_tests_without_verification(tmp_path: Path) -> None:
    diff = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1,0 +1,1 @@
+x = 1
"""
    report = analyze_patch_quality(
        workdir=tmp_path,
        diff=diff,
        changed_files=["src/app.py"],
        tests_verified=False,
        config=AppConfig(
            quality=QualityConfig(require_tests_with_code=True, block_on_severity="medium")
        ),
    )
    assert any(f.rule_id == "quality-missing-tests" for f in report.findings)
    assert report.passed is False
