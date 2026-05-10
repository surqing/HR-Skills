from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "hr-recruiting-tracker" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import dependency_check  # noqa: E402
import resume_extract  # noqa: E402


class ResumeExtractTests(unittest.TestCase):
    def test_plain_text_resume_extracts_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resume = Path(tmp) / "resume.txt"
            resume.write_text("姓名：张三\nEmail: zhangsan@example.com\nPython\n", encoding="utf-8")

            result = resume_extract.extract_resume(resume, parser="auto")
            draft = resume_extract.build_candidate_draft(result.markdown, resume.name, "hash")
            stats = resume_extract.compute_text_stats(result.markdown, result.raw, resume.suffix)

        self.assertEqual(result.parser, "plain-text")
        self.assertEqual(result.quality, "source")
        self.assertEqual(draft["identity"]["name"], "张三")
        self.assertFalse(stats.suspected_scanned)

    def test_spaced_chinese_name_is_normalized(self) -> None:
        text = "\n".join(["教育背景", "中 南 大学", "李 四", "17600000000"])

        self.assertEqual(resume_extract.detect_name(text), "李四")

    def test_docling_failure_falls_back_and_records_attempts(self) -> None:
        def failing_docling(_: Path) -> resume_extract.ExtractionResult:
            raise resume_extract.ExtractionError("docling conversion failed")

        def successful_local(_: Path) -> resume_extract.ExtractionResult:
            return resume_extract.ExtractionResult(
                markdown="# 简历\n\n吴 树 青\n",
                raw={"format": "pdf-text", "pages": [{"page": 1, "text": "吴 树 青"}]},
                parser="pypdf",
                quality="fallback",
                warnings=["fallback"],
                parser_attempts=[],
            )

        with tempfile.TemporaryDirectory() as tmp:
            resume = Path(tmp) / "resume.pdf"
            resume.write_bytes(b"%PDF fake")
            with patch.object(resume_extract, "docling_attempts", return_value=[failing_docling]):
                with patch.object(resume_extract, "local_attempts_for_suffix", return_value=[successful_local]):
                    result = resume_extract.extract_resume(resume, parser="docling")

        self.assertEqual(result.parser, "pypdf")
        self.assertEqual(result.quality, "fallback")
        self.assertEqual([attempt.status for attempt in result.parser_attempts], ["failed", "succeeded"])


class DependencyCheckTests(unittest.TestCase):
    def test_no_docling_does_not_report_high_quality(self) -> None:
        def fake_has_module(name: str) -> bool:
            return False

        with patch.object(dependency_check, "command_runs", return_value=(False, "missing")):
            with patch.object(dependency_check, "has_module", side_effect=fake_has_module):
                with patch.object(dependency_check, "find_command", return_value=None):
                    payload = dependency_check.build_payload("resume-ingest")

        self.assertTrue(payload["ready"])
        self.assertEqual(payload["quality"], "text-only")

    def test_probe_file_reports_parser_and_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resume = Path(tmp) / "resume.md"
            resume.write_text("# 简历\n\n姓名：李四\n", encoding="utf-8")
            payload = dependency_check.build_payload("resume-ingest", probe_path=resume)

        self.assertEqual(payload["probe"]["status"], "succeeded")
        self.assertEqual(payload["probe"]["parser"], "plain-text")
        self.assertIn("text_stats", payload["probe"])

    def test_candidate_upload_dependency_check_is_local_by_default(self) -> None:
        with patch.object(dependency_check, "find_tencent_docs_skill_dir", return_value=Path("/tmp/tencent-docs")):
            with patch.object(dependency_check.shutil, "which", return_value="/usr/bin/mcporter"):
                with patch.object(dependency_check.subprocess, "run", side_effect=AssertionError("unexpected remote probe")):
                    payload = dependency_check.build_payload("candidate-upload")

        self.assertTrue(payload["ready"])
        self.assertEqual(payload["quality"], "not-applicable")
        by_name = {check["name"]: check for check in payload["checks"]}
        self.assertFalse(by_name["tencent_docs.mcp"]["required"])
        self.assertFalse(by_name["tencent_docs.auth"]["required"])


if __name__ == "__main__":
    unittest.main()
