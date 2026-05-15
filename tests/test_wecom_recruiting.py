from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "hr-recruiting-tracker" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import table_models  # noqa: E402
import wecom_cli  # noqa: E402
import wecom_feedback_sync  # noqa: E402
import wecom_notify  # noqa: E402


def field_text_map(record: dict) -> dict[str, str]:
    return {
        value["field"]: wecom_feedback_sync.smartsheet.field_value_to_text(value)
        for value in record.get("field_values", [])
    }


class WeComRecruitingTests(unittest.TestCase):
    def test_interview_feedback_schema_is_independent(self) -> None:
        titles = set(table_models.get_field_titles("interview_feedback"))

        self.assertEqual(table_models.get_fixed_sheet_title("interview_feedback"), "HR面评记录表")
        self.assertEqual(table_models.get_fixed_sheet_title("candidates"), "HR候选人库")
        self.assertIn("interviewer_feedback", titles)
        self.assertIn("interviewer_score", titles)
        self.assertIn("source_message_id", titles)
        self.assertNotIn("interviewer_feedback", set(table_models.get_field_titles("candidates")))

    def test_wecom_notify_renders_dry_run_preview(self) -> None:
        context = wecom_notify.NotificationContext(
            candidate_name="张三",
            job_title="Agent 开发工程师",
            interviewer_name="李四",
            interview_time="2026-05-12 15:00",
            interview_mode="视频",
            interview_round="技术一面",
        )

        content = wecom_notify.render_message("interview-confirmation", context)
        preview = wecom_notify.build_preview(1, "lisi", "interview-confirmation", content, dry_run=True)

        self.assertIn("候选人：张三", content)
        self.assertIn("轮次：技术一面", content)
        self.assertEqual(preview["status"], "dry-run")
        self.assertEqual(preview["target"]["chatid"], "lisi")

    def test_wecom_cli_unwraps_json_rpc_result(self) -> None:
        wrapped = {
            "id": "mcp_rpc_1",
            "jsonrpc": "2.0",
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"errcode": 0, "errmsg": "ok", "chats": []}, ensure_ascii=False),
                    }
                ],
                "isError": False,
            },
        }

        unwrapped = wecom_cli.unwrap_wecom_cli_result(wrapped)

        self.assertEqual(unwrapped["errcode"], 0)
        self.assertEqual(unwrapped["chats"], [])

    def test_parse_feedback_message_maps_labels(self) -> None:
        message = {
            "userid": "lisi",
            "send_time": "2026-05-12 16:00:00",
            "msgtype": "text",
            "text": {
                "content": "\n".join(
                    [
                        "候选人：张三",
                        "候选人记录ID：张三-abcdef123456",
                        "岗位ID：JOB-001",
                        "面试官：李四",
                        "面试时间：2026-05-12 15:00",
                        "面试方式：视频",
                        "轮次：技术一面",
                        "面评：基础扎实",
                        "评分：4.5",
                        "备注：继续看系统设计",
                        "结论：需复试",
                        "下一步：安排技术二面",
                    ]
                )
            },
        }

        parsed = wecom_feedback_sync.parse_feedback_message(message, chatid="group-1", chat_type=2)

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["candidate_name"], "张三")
        self.assertEqual(parsed["candidate_record_id"], "张三-abcdef123456")
        self.assertEqual(parsed["job_id"], "JOB-001")
        self.assertEqual(parsed["interviewer_name"], "李四")
        self.assertEqual(parsed["interviewer_score"], "4.5")
        self.assertEqual(parsed["decision"], "需复试")
        self.assertEqual(parsed["source_conversation_id"], "2:group-1")
        self.assertTrue(parsed["feedback_id"].startswith("wecom-"))

    def test_candidate_association_prefers_record_id_then_contact(self) -> None:
        candidates = [
            {
                "record_id": "张三-aaa",
                "name": "张三",
                "phone": "13800138000",
                "email": "a@example.com",
                "job_intent": "JOB-001",
            },
            {
                "record_id": "张三-bbb",
                "name": "张三",
                "phone": "13900139000",
                "email": "b@example.com",
                "job_intent": "JOB-002",
            },
        ]

        by_id = wecom_feedback_sync.associate_candidate({"candidate_record_id": "张三-bbb", "phone": "13800138000"}, candidates)
        by_phone = wecom_feedback_sync.associate_candidate({"candidate_name": "张三", "phone": "13800138000"}, candidates)
        ambiguous = wecom_feedback_sync.associate_candidate({"candidate_name": "张三"}, candidates)

        self.assertEqual(by_id["status"], "matched")
        self.assertEqual(by_id["candidate"]["record_id"], "张三-bbb")
        self.assertEqual(by_phone["status"], "matched")
        self.assertEqual(by_phone["candidate"]["record_id"], "张三-aaa")
        self.assertEqual(ambiguous["status"], "ambiguous")

    def test_stage_sync_rules(self) -> None:
        self.assertEqual(wecom_feedback_sync.decide_candidate_stage("技术一面", "不通过", "技术一面"), "不合适")
        self.assertEqual(wecom_feedback_sync.decide_candidate_stage("技术一面", "待定", "技术一面"), "技术一面")
        self.assertEqual(wecom_feedback_sync.decide_candidate_stage("HR初筛", "需复试", "技术一面"), "技术一面")
        self.assertEqual(wecom_feedback_sync.decide_candidate_stage("技术一面", "需复试", "技术一面"), "技术二面")

    def test_feedback_record_uses_schema_titles_and_date_ms(self) -> None:
        record = wecom_feedback_sync.feedback_to_record(
            {
                "feedback_id": "wecom-1",
                "candidate_name": "张三",
                "interview_time": "2026-05-12 15:00",
                "source_message_time": "2026-05-12 16:00:00",
                "interviewer_score": "4.5",
                "decision": "通过",
            },
            "已预览",
        )
        values = {value["field"]: value for value in record["field_values"]}

        self.assertEqual(values["feedback_id"]["text_value"]["items"][0]["text"], "wecom-1")
        self.assertEqual(values["candidate_name"]["text_value"]["items"][0]["text"], "张三")
        self.assertIn("string_value", values["interview_time"])
        self.assertTrue(values["interview_time"]["string_value"].isdigit())
        self.assertEqual(values["interviewer_score"]["number_value"], 4.5)
        self.assertEqual(values["sync_status"]["text_value"]["items"][0]["text"], "已预览")

    def test_main_dry_run_reads_message_json_without_remote_calls(self) -> None:
        messages = [
            {
                "userid": "lisi",
                "send_time": "2026-05-12 16:00:00",
                "msgtype": "text",
                "text": {"content": "候选人：张三\n面评：不错\n结论：通过"},
            }
        ]
        path = ROOT / "tests" / "_tmp_wecom_messages.json"
        path.write_text(json.dumps({"messages": messages}, ensure_ascii=False), encoding="utf-8")
        try:
            with patch.object(sys, "argv", ["wecom_feedback_sync.py", "--messages-json", str(path)]):
                with patch("builtins.print") as mock_print:
                    rc = wecom_feedback_sync.main()
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(rc, 0)
        payload = json.loads(mock_print.call_args.args[0])
        self.assertEqual(payload["status"], "dry-run")
        self.assertEqual(payload["feedback_detected"], 1)
        self.assertEqual(payload["candidate_updates"], [])
        self.assertTrue(payload["sensitive_values_redacted"])
        values = field_text_map(payload["feedback_records"][0])
        self.assertEqual(values["candidate_name"], "张*")
        self.assertEqual(values["interviewer_feedback"], "<redacted>")
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("张三", serialized)
        self.assertNotIn("不错", serialized)

    def test_main_show_sensitive_outputs_raw_feedback_content(self) -> None:
        messages = [
            {
                "userid": "lisi",
                "send_time": "2026-05-12 16:00:00",
                "msgtype": "text",
                "text": {"content": "候选人：张三\n面评：不错\n结论：通过"},
            }
        ]
        path = ROOT / "tests" / "_tmp_wecom_messages.json"
        path.write_text(json.dumps({"messages": messages}, ensure_ascii=False), encoding="utf-8")
        try:
            with patch.object(sys, "argv", ["wecom_feedback_sync.py", "--messages-json", str(path), "--show-sensitive"]):
                with patch("builtins.print") as mock_print:
                    rc = wecom_feedback_sync.main()
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(rc, 0)
        payload = json.loads(mock_print.call_args.args[0])
        self.assertFalse(payload["sensitive_values_redacted"])
        values = field_text_map(payload["feedback_records"][0])
        self.assertEqual(values["candidate_name"], "张三")
        self.assertEqual(values["interviewer_feedback"], "不错")

    def test_main_apply_writes_raw_records_but_prints_redacted_output(self) -> None:
        messages = [
            {
                "userid": "lisi",
                "send_time": "2026-05-12 16:00:00",
                "msgtype": "text",
                "text": {
                    "content": "\n".join([
                        "候选人：张三",
                        "候选人记录ID：张三-aaaa1111",
                        "轮次：技术一面",
                        "面评：基础扎实",
                        "结论：需复试",
                    ])
                },
            }
        ]
        candidate_records = [
            {
                "record_id": "remote-row-id-1",
                "field_values": [
                    {"field": "记录ID", "text_value": {"items": [{"text": "张三-aaaa1111", "type": "text"}]}},
                    {"field": "姓名", "text_value": {"items": [{"text": "张三", "type": "text"}]}},
                    {"field": "招聘阶段", "text_value": {"items": [{"text": "HR初筛", "type": "text"}]}},
                ],
            }
        ]
        path = ROOT / "tests" / "_tmp_wecom_messages.json"
        path.write_text(json.dumps({"messages": messages}, ensure_ascii=False), encoding="utf-8")
        try:
            with patch.object(sys, "argv", [
                "wecom_feedback_sync.py",
                "--messages-json", str(path),
                "--apply",
                "--candidate-file-id", "candidate-file",
                "--feedback-file-id", "feedback-file",
            ]), \
                patch.object(wecom_feedback_sync.smartsheet, "list_tables", return_value=[{"sheet_id": "sheet-1", "title": "表"}]), \
                patch.object(wecom_feedback_sync.smartsheet, "list_records", return_value=candidate_records), \
                patch.object(wecom_feedback_sync, "ensure_fields", return_value=[]), \
                patch.object(wecom_feedback_sync.smartsheet, "add_records", return_value=[{"record_id": "feedback-row-1"}]) as add_records, \
                patch.object(wecom_feedback_sync.smartsheet, "update_records", return_value=[{"record_id": "remote-row-id-1"}]) as update_records:
                with patch("builtins.print") as mock_print:
                    rc = wecom_feedback_sync.main()
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(rc, 0)
        raw_feedback_values = field_text_map(add_records.call_args.args[2][0])
        self.assertEqual(raw_feedback_values["candidate_name"], "张三")
        self.assertEqual(raw_feedback_values["interviewer_feedback"], "基础扎实")
        self.assertEqual(update_records.call_args.args[2][0]["record_id"], "remote-row-id-1")
        payload = json.loads(mock_print.call_args.args[0])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertTrue(payload["sensitive_values_redacted"])
        self.assertNotIn("张三", serialized)
        self.assertNotIn("张三-aaaa1111", serialized)
        self.assertNotIn("基础扎实", serialized)
        self.assertNotIn("remote-row-id-1", serialized)
        self.assertNotIn("feedback-row-1", serialized)


if __name__ == "__main__":
    unittest.main()
