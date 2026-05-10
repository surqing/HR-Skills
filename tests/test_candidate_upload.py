from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "hr-recruiting-tracker" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import upload_to_smartsheet  # noqa: E402
import table_models  # noqa: E402


class CandidateUploadMappingTests(unittest.TestCase):
    def test_default_fields_avoid_select_types(self) -> None:
        field_types = {field["field_title"]: field["field_type"] for field in upload_to_smartsheet.CANDIDATE_FIELDS}

        self.assertEqual(field_types["最高学历"], "text")
        self.assertEqual(field_types["招聘阶段"], "text")
        self.assertEqual(field_types["解析质量"], "text")
        self.assertEqual(field_types["简历包路径"], "text")
        self.assertNotIn("singleSelect", field_types.values())
        self.assertNotIn("select", field_types.values())

    def test_number_fields_include_required_use_separate(self) -> None:
        number_fields = [
            field for field in upload_to_smartsheet.CANDIDATE_FIELDS
            if field["field_type"] == "number"
        ]

        self.assertTrue(number_fields)
        for field in number_fields:
            self.assertIn("use_separate", field["property_number"])

    def test_record_mapping_uses_sha_based_record_id(self) -> None:
        draft = {
            "source": {"file_name": "resume.pdf", "sha256": "abcdef1234567890"},
            "identity": {"name": "张三", "phone": "13800138000", "email": "zhangsan@example.com"},
            "profile": {
                "current_company": None,
                "years_of_experience": None,
                "education_evidence": ["某大学 计算机科学(本科) 2020.09 - 2024.06"],
                "skill_mentions": ["Python", "SQL"],
            },
        }
        report = {"quality": "fallback", "review_required": True}

        record = upload_to_smartsheet.map_candidate_to_record(draft, report, "/tmp/bundle")
        values = record["field_values"]
        by_field = {value["field"]: value for value in values}

        self.assertEqual(by_field["记录ID"]["text_value"]["items"][0]["text"], "张三-abcdef123456")
        self.assertEqual(by_field["解析质量"]["text_value"]["items"][0]["text"], "回退解析")
        self.assertEqual(by_field["招聘阶段"]["text_value"]["items"][0]["text"], "简历筛选")

    def test_redact_record_masks_sensitive_fields(self) -> None:
        draft = {
            "source": {"file_name": "张三-resume.pdf", "sha256": "abcdef1234567890"},
            "identity": {"name": "张三", "phone": "13800138000", "email": "zhangsan@example.com"},
            "profile": {
                "current_company": None,
                "years_of_experience": None,
                "education_evidence": [],
                "skill_mentions": [],
            },
        }
        report = {"quality": "high", "review_required": False}

        record = upload_to_smartsheet.map_candidate_to_record(draft, report, "/tmp/private-bundle")
        redacted = upload_to_smartsheet.redact_record(record)
        values = {value["field"]: value for value in redacted["field_values"]}

        self.assertEqual(values["姓名"]["text_value"]["items"][0]["text"], "张*")
        self.assertEqual(values["电话"]["string_value"], "138****8000")
        self.assertEqual(values["邮箱"]["string_value"], "z***@example.com")
        self.assertEqual(values["简历来源"]["text_value"]["items"][0]["text"], "***.pdf")
        self.assertEqual(values["简历包路径"]["text_value"]["items"][0]["text"], "file://<redacted-bundle>")
        self.assertEqual(values["记录ID"]["text_value"]["items"][0]["text"], "***-abcdef...")

    def test_dry_run_target_resolution_is_offline_by_default(self) -> None:
        def fail_remote(_: str):
            raise AssertionError("dry-run should not search Tencent Docs unless --probe-remote is set")

        original = upload_to_smartsheet.find_smartsheets_by_title
        try:
            upload_to_smartsheet.find_smartsheets_by_title = fail_remote
            file_id, created, action = upload_to_smartsheet.resolve_smartsheet_target(
                None,
                "HR候选人库",
                None,
                create_new=False,
                dry_run=True,
            )
        finally:
            upload_to_smartsheet.find_smartsheets_by_title = original

        self.assertIsNone(file_id)
        self.assertFalse(created)
        self.assertEqual(action, "dry_run_offline")

    def test_unreviewed_upload_gate_uses_report_flag(self) -> None:
        self.assertTrue(upload_to_smartsheet.should_block_unreviewed_upload({"quality": "fallback"}))
        self.assertTrue(upload_to_smartsheet.should_block_unreviewed_upload({"quality": "high", "review_required": True}))
        self.assertFalse(upload_to_smartsheet.should_block_unreviewed_upload({"quality": "high", "review_required": False}))

    def test_spaced_education_line_is_split_into_fields(self) -> None:
        degree, school, major, grad_year = upload_to_smartsheet.extract_education_highest([
            "长 沙 学 院 计 算 机科学与技 术(本科) 2017.09 - 2023.06",
        ])

        self.assertEqual(degree, "本科")
        self.assertEqual(school, "长沙学院")
        self.assertEqual(major, "计算机科学与技术")
        self.assertEqual(grad_year, "2023")

    def test_highest_education_is_selected_after_spacing_normalization(self) -> None:
        degree, school, major, grad_year = upload_to_smartsheet.extract_education_highest([
            "中 南 大学 人工 智 能(硕 士) 2024.09 - 2027.06",
            "长 沙 学 院 计 算 机科学与技 术(本科) 2017.09 - 2023.06",
        ])

        self.assertEqual(degree, "硕士")
        self.assertEqual(school, "中南大学")
        self.assertEqual(major, "人工智能")
        self.assertEqual(grad_year, "2027")

    def test_candidate_table_has_fixed_title(self) -> None:
        self.assertEqual(table_models.get_fixed_sheet_title("candidates"), "HR候选人库")

    def test_find_smartsheet_by_title_requires_exact_title(self) -> None:
        calls = []

        def fake_call(tool: str, args: dict) -> dict:
            calls.append((tool, args))
            return {
                "list": [
                    {"title": "HR候选人库-测试", "file_id": "wrong"},
                    {"title": "HR候选人库", "file_id": "right"},
                ]
            }

        original = upload_to_smartsheet.mcporter_call
        try:
            upload_to_smartsheet.mcporter_call = fake_call
            found = upload_to_smartsheet.find_smartsheet_by_title("HR候选人库")
        finally:
            upload_to_smartsheet.mcporter_call = original

        self.assertEqual(found["file_id"], "right")
        self.assertEqual(calls, [("manage.search_file", {"search_key": "HR候选人库"})])


if __name__ == "__main__":
    unittest.main()
