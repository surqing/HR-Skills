from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "hr-recruiting-tracker" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import manage_jobs  # noqa: E402
import table_models  # noqa: E402


class JobManagementTests(unittest.TestCase):
    def test_jobs_table_has_required_fields(self) -> None:
        titles = set(table_models.get_field_titles("jobs"))
        required = {
            "job_title",
            "department",
            "hiring_manager",
            "must_have",
            "nice_to_have",
            "responsibilities",
            "level",
            "location",
            "salary_range",
            "interview_process",
            "status",
        }

        self.assertTrue(required.issubset(titles))
        self.assertEqual(table_models.get_fixed_sheet_title("jobs"), "HR岗位信息库")

    def test_jobs_table_avoids_select_types(self) -> None:
        field_types = {field["field_title"]: field["field_type"] for field in table_models.get_field_definitions("jobs")}

        self.assertEqual(field_types["status"], "text")
        self.assertNotIn("singleSelect", field_types.values())
        self.assertNotIn("select", field_types.values())

    def sample_job(self) -> dict:
        return {
            "job_id": "JOB-001",
            "job_title": "Agent 开发工程师",
            "department": "AI平台部",
            "hiring_manager": "王经理",
            "must_have": "Python；LLM 应用开发；工具调用经验",
            "nice_to_have": "MCP、RAG、OpenClaw 经验",
            "responsibilities": "开发和维护企业内部 Agent 工作流",
            "level": "P5-P6",
            "location": "深圳",
            "salary_range": "20k-35k * 14",
            "interview_process": "HR初筛 -> 技术一面 -> 主管面",
            "status": "开放",
        }

    def test_jobs_schema_does_not_embed_mock_records(self) -> None:
        jobs_table = table_models.get_table("jobs")

        self.assertNotIn("mock_records", jobs_table)

    def test_job_record_is_valid_and_mapped(self) -> None:
        job = self.sample_job()

        manage_jobs.validate_job(job)

        record = manage_jobs.map_job_to_record(job)
        values = {value["field"]: value for value in record["field_values"]}

        self.assertIn("job_id", values)
        self.assertIn("job_title", values)
        self.assertIn("status", values)
        self.assertIn("updated_at", values)
        self.assertEqual(values["status"]["text_value"]["items"][0]["text"], "开放")

    def test_load_job_records_requires_explicit_file(self) -> None:
        self.assertEqual(manage_jobs.load_job_records(None), [])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobs.json"
            path.write_text(json.dumps({"jobs": [self.sample_job()]}, ensure_ascii=False), encoding="utf-8")

            jobs = manage_jobs.load_job_records(str(path))

        self.assertEqual(jobs[0]["job_id"], "JOB-001")


if __name__ == "__main__":
    unittest.main()
