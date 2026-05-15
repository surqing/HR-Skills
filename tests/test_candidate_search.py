from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "hr-recruiting-tracker" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import candidate_search  # noqa: E402
import upload_to_smartsheet  # noqa: E402


class CandidateSearchTests(unittest.TestCase):
    def make_record(self, **fields):
        values = []
        for field, value in fields.items():
            if value is None:
                continue
            if field in {"工作年限", "毕业年份"}:
                values.append({"field": field, "number_value": value})
            elif field == "需HR审核":
                values.append({"field": field, "bool_value": value})
            else:
                values.append({"field": field, "text_value": {"items": [{"text": str(value), "type": "text"}]}})
        return {"field_values": values, "record_id": fields.get("record_id", "rid")}

    def test_record_field_text_map_handles_common_value_types(self) -> None:
        record = {
            "field_values": [
                {"field": "姓名", "text_value": {"items": [{"text": "张三", "type": "text"}]}},
                {"field": "工作年限", "number_value": 5.0},
                {"field": "需HR审核", "bool_value": True},
                {"field": "录入时间", "string_value": "123"},
            ]
        }

        mapped = upload_to_smartsheet.record_field_text_map(record)

        self.assertEqual(mapped["姓名"], "张三")
        self.assertEqual(mapped["工作年限"], "5")
        self.assertEqual(mapped["需HR审核"], "true")
        self.assertEqual(mapped["录入时间"], "123")

    def test_record_matches_filters_and_contains_keywords(self) -> None:
        candidate = {
            "name": "张三",
            "phone": "13800138000",
            "email": "zhangsan@example.com",
            "current_company": "OpenAI",
            "highest_degree": "本科",
            "school": "清华大学",
            "major": "计算机科学与技术",
            "skills": "Python、SQL",
            "job_intent": "Agent 开发",
            "recruiting_stage": "HR初筛",
            "resume_source": "resume.pdf",
            "parse_quality": "高保真",
            "record_id": "rid-1",
        }

        self.assertTrue(candidate_search.record_matches(candidate, {"name": ["张"]}, []))
        self.assertTrue(candidate_search.record_matches(candidate, {"skills": ["Python"]}, []))
        self.assertFalse(candidate_search.record_matches(candidate, {"recruiting_stage": ["Offer"]}, []))
        self.assertTrue(candidate_search.record_matches(candidate, {}, ["清华"]))
        self.assertFalse(candidate_search.record_matches(candidate, {}, ["不存在"]))

    def test_build_summary_counts_duplicates_and_missing_fields(self) -> None:
        candidates = [
            {
                "name": "张三",
                "phone": "13800138000",
                "email": "zhangsan@example.com",
                "current_company": "OpenAI",
                "highest_degree": "本科",
                "school": "清华大学",
                "major": "计算机科学与技术",
                "skills": "Python、SQL",
                "job_intent": "Agent 开发",
                "recruiting_stage": "HR初筛",
                "resume_source": "a.pdf",
                "parse_quality": "高保真",
                "record_id": "rid-1",
            },
            {
                "name": "张三",
                "phone": "13800138000",
                "email": "zhangsan@example.com",
                "current_company": "OpenAI",
                "highest_degree": "本科",
                "school": "清华大学",
                "major": "计算机科学与技术",
                "skills": "Python、SQL",
                "job_intent": "Agent 开发",
                "recruiting_stage": "HR初筛",
                "resume_source": "b.pdf",
                "parse_quality": "高保真",
                "record_id": "rid-2",
            },
        ]

        summary = candidate_search.build_summary(candidates)
        sensitive_summary = candidate_search.build_summary(candidates, show_sensitive=True)

        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["stage_distribution"]["HR初筛"], 2)
        self.assertEqual(summary["skill_distribution"]["Python"], 2)
        self.assertTrue(summary["duplicate_groups"])
        self.assertEqual(summary["duplicate_groups"][0]["group_key"], "张*")
        self.assertEqual(summary["duplicate_groups"][0]["names"], ["张*", "张*"])
        self.assertNotIn("张三", json.dumps(summary["duplicate_groups"], ensure_ascii=False))
        self.assertEqual(sensitive_summary["duplicate_groups"][0]["group_key"], "张三")
        self.assertEqual(sensitive_summary["duplicate_groups"][0]["names"], ["张三", "张三"])

    def test_redact_candidate_masks_sensitive_values(self) -> None:
        candidate = {
            "name": "张三",
            "phone": "13800138000",
            "email": "zhangsan@example.com",
            "resume_source": "resume.pdf",
            "record_id": "张三-abcdef123456",
            "skills": "Python",
        }

        redacted = candidate_search.redact_candidate(candidate)

        self.assertEqual(redacted["name"], "张*")
        self.assertEqual(redacted["phone"], "138****8000")
        self.assertEqual(redacted["email"], "z***@example.com")
        self.assertEqual(redacted["resume_source"], "***.pdf")
        self.assertEqual(redacted["record_id"], "***-abcdef...")

    def test_candidate_summary_view_masks_record_id(self) -> None:
        candidate = {
            "name": "张三",
            "phone": "13800138000",
            "email": "zhangsan@example.com",
            "resume_source": "resume.pdf",
            "record_id": "张三-abcdef123456",
        }

        view = candidate_search.candidate_summary_view(candidate)

        self.assertEqual(view["record_id"], "***-abcdef...")
        self.assertEqual(view["name"], "张*")

    def test_main_reads_and_filters_records(self) -> None:
        records = [
            self.make_record(
                姓名="张三",
                电话="13800138000",
                邮箱="zhangsan@example.com",
                当前公司="OpenAI",
                最高学历="本科",
                毕业院校="清华大学",
                专业="计算机科学与技术",
                技能标签="Python、SQL",
                求职意向="Agent 开发",
                招聘阶段="HR初筛",
                简历来源="a.pdf",
                解析质量="高保真",
                记录ID="张三-aaaa1111",
            ),
            self.make_record(
                姓名="李四",
                电话="13900139000",
                邮箱="lisi@example.com",
                当前公司="Other",
                最高学历="硕士",
                毕业院校="北京大学",
                专业="软件工程",
                技能标签="Go、K8s",
                求职意向="后端",
                招聘阶段="Offer",
                简历来源="b.pdf",
                解析质量="回退解析",
                记录ID="李四-bbbb2222",
            ),
        ]

        with patch.object(candidate_search, "find_smartsheet_by_title", return_value={"file_id": "file-1"}), \
            patch.object(candidate_search.smartsheet, "list_tables", return_value=[{"sheet_id": "sheet-1", "title": "表"}]), \
            patch.object(candidate_search.smartsheet, "list_records", return_value=records), \
            patch.object(sys, "argv", ["candidate_search.py", "--name", "张三"]):
            rc = candidate_search.main()

        self.assertEqual(rc, 0)

    def test_main_outputs_json(self) -> None:
        records = [
            self.make_record(姓名="张三", 电话="13800138000", 邮箱="zhangsan@example.com", 记录ID="张三-aaaa1111"),
            self.make_record(姓名="张三", 电话="13800138000", 邮箱="zhangsan@example.com", 记录ID="张三-bbbb2222"),
        ]

        with patch.object(candidate_search, "find_smartsheet_by_title", return_value={"file_id": "file-1"}), \
            patch.object(candidate_search.smartsheet, "list_tables", return_value=[{"sheet_id": "sheet-1", "title": "表"}]), \
            patch.object(candidate_search.smartsheet, "list_records", return_value=records), \
            patch.object(sys, "argv", ["candidate_search.py", "--include-all"]):
            with patch("builtins.print") as mock_print:
                rc = candidate_search.main()

        self.assertEqual(rc, 0)
        payload = json.loads(mock_print.call_args.args[0])
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["summary"]["count"], 2)
        self.assertEqual(payload["records"][0]["record_id"], "***-aaaa11...")
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("张三", serialized)
        self.assertNotIn("张三-aaaa1111", serialized)
        self.assertNotIn("13800138000", serialized)

    def test_main_show_sensitive_keeps_raw_values(self) -> None:
        records = [self.make_record(姓名="张三", 电话="13800138000", 邮箱="zhangsan@example.com", 记录ID="张三-aaaa1111")]

        with patch.object(candidate_search, "find_smartsheet_by_title", return_value={"file_id": "file-1"}), \
            patch.object(candidate_search.smartsheet, "list_tables", return_value=[{"sheet_id": "sheet-1", "title": "表"}]), \
            patch.object(candidate_search.smartsheet, "list_records", return_value=records), \
            patch.object(sys, "argv", ["candidate_search.py", "--include-all", "--show-sensitive"]):
            with patch("builtins.print") as mock_print:
                rc = candidate_search.main()

        self.assertEqual(rc, 0)
        payload = json.loads(mock_print.call_args.args[0])
        self.assertFalse(payload["sensitive_values_redacted"])
        self.assertEqual(payload["records"][0]["name"], "张三")
        self.assertEqual(payload["records"][0]["record_id"], "张三-aaaa1111")


if __name__ == "__main__":
    unittest.main()
