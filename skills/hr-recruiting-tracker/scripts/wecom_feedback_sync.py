#!/usr/bin/env python3
"""Sync interview feedback from visible WeCom messages into Tencent Docs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import table_models
import upload_to_smartsheet as smartsheet
import wecom_cli


CANDIDATE_TABLE = "candidates"
FEEDBACK_TABLE = "interview_feedback"
CANDIDATE_FIXED_TITLE = table_models.get_fixed_sheet_title(CANDIDATE_TABLE)
FEEDBACK_FIXED_TITLE = table_models.get_fixed_sheet_title(FEEDBACK_TABLE)
CANDIDATE_KEY_TO_TITLE = table_models.get_field_key_to_title(CANDIDATE_TABLE)
FEEDBACK_KEY_TO_TITLE = table_models.get_field_key_to_title(FEEDBACK_TABLE)
CANDIDATE_TITLE_TO_KEY = {title: key for key, title in CANDIDATE_KEY_TO_TITLE.items()}
FEEDBACK_FIELDS = table_models.get_field_definitions(FEEDBACK_TABLE)
STAGE_ORDER = ["简历筛选", "HR初筛", "技术一面", "技术二面", "HR面", "Offer", "入职"]
ROUND_TO_STAGE = {
    "HR初筛": "HR初筛",
    "技术一面": "技术一面",
    "一面": "技术一面",
    "技术二面": "技术二面",
    "二面": "技术二面",
    "HR面": "HR面",
    "终面": "HR面",
}
DECISIONS = ("不通过", "需复试", "通过", "待定")
SYNC_MANUAL = "需人工确认"
SYNC_PREVIEW = "已预览"
SYNC_RECORDED = "已记录"
SYNC_SYNCED = "已同步"
REDACTED_TEXT = "<redacted>"
FREE_TEXT_FIELDS = {"interviewer_feedback", "interviewer_notes", "next_action"}
MASKED_TEXT_FIELDS = {
    "candidate_name",
    "candidate_record_id",
    "interviewer_name",
    "feedback_id",
    "source_message_id",
    "source_conversation_id",
}


def normalize(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    return re.sub(r"\s+", "", text)


def now_ms() -> str:
    return str(int(time.time() * 1000))


def datetime_to_ms(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{12,}", text):
        return text
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return str(int(parsed.timestamp() * 1000))
        except ValueError:
            continue
    return text


def text_value(text: Any) -> list[dict[str, str]]:
    return [{"type": "text", "text": str(text)}]


def add_text(field_values: list[dict[str, Any]], field: str, value: Any) -> None:
    if value is None or value == "":
        return
    field_values.append({"field": field, "text_value": {"items": text_value(value)}})


def add_string(field_values: list[dict[str, Any]], field: str, value: Any) -> None:
    if value is None or value == "":
        return
    field_values.append({"field": field, "string_value": str(value)})


def add_number(field_values: list[dict[str, Any]], field: str, value: Any) -> None:
    if value is None or value == "":
        return
    try:
        field_values.append({"field": field, "number_value": float(value)})
    except (TypeError, ValueError):
        return


def parse_labelled_feedback(content: str) -> dict[str, Any]:
    """Parse simple labelled interview feedback text into table keys."""
    aliases = {
        "候选人": "candidate_name",
        "面试者": "candidate_name",
        "candidate_name": "candidate_name",
        "候选人记录ID": "candidate_record_id",
        "记录ID": "candidate_record_id",
        "candidate_record_id": "candidate_record_id",
        "岗位ID": "job_id",
        "job_id": "job_id",
        "面试官": "interviewer_name",
        "interviewer_name": "interviewer_name",
        "面试时间": "interview_time",
        "interview_time": "interview_time",
        "面试方式": "interview_mode",
        "方式": "interview_mode",
        "interview_mode": "interview_mode",
        "面试轮次": "interview_round",
        "轮次": "interview_round",
        "interview_round": "interview_round",
        "面评": "interviewer_feedback",
        "面试官面评": "interviewer_feedback",
        "评价": "interviewer_feedback",
        "interviewer_feedback": "interviewer_feedback",
        "评分": "interviewer_score",
        "面试官评分": "interviewer_score",
        "interviewer_score": "interviewer_score",
        "备注": "interviewer_notes",
        "面试官备注": "interviewer_notes",
        "interviewer_notes": "interviewer_notes",
        "结论": "decision",
        "面试结论": "decision",
        "decision": "decision",
        "下一步": "next_action",
        "建议动作": "next_action",
        "next_action": "next_action",
        "电话": "phone",
        "phone": "phone",
        "邮箱": "email",
        "email": "email",
    }
    parsed: dict[str, Any] = {}
    feedback_lines: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.strip(" \t-")
        if not line:
            continue
        match = re.match(r"^([^:：]{1,24})[:：]\s*(.+)$", line)
        if match:
            label = match.group(1).strip()
            value = match.group(2).strip()
            key = aliases.get(label)
            if key:
                parsed[key] = value
                continue
        feedback_lines.append(line)

    if "interviewer_feedback" not in parsed and feedback_lines:
        parsed["interviewer_feedback"] = "\n".join(feedback_lines)

    if "decision" not in parsed:
        for decision in DECISIONS:
            if decision in content:
                parsed["decision"] = decision
                break

    if "interviewer_score" not in parsed:
        score_match = re.search(r"(?:评分|score)\D*([1-5](?:\.\d)?)", content, flags=re.IGNORECASE)
        if score_match:
            parsed["interviewer_score"] = score_match.group(1)

    return parsed


def stable_feedback_id(parsed: dict[str, Any], message: dict[str, Any]) -> str:
    explicit = message.get("msgid") or message.get("message_id") or message.get("id")
    if explicit:
        return f"wecom-{explicit}"
    seed = "|".join(
        str(part or "")
        for part in (
            message.get("userid"),
            message.get("send_time"),
            message.get("chatid"),
            parsed.get("candidate_name"),
            parsed.get("interviewer_feedback"),
        )
    )
    return "wecom-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def message_text(message: dict[str, Any]) -> str:
    text = message.get("text")
    if isinstance(text, dict):
        return str(text.get("content") or "")
    if isinstance(text, str):
        return text
    return str(message.get("content") or "")


def parse_feedback_message(
    message: dict[str, Any],
    chatid: str | None = None,
    chat_type: int | None = None,
) -> dict[str, Any] | None:
    if message.get("msgtype", "text") != "text":
        return None
    content = message_text(message)
    if not content.strip():
        return None
    parsed = parse_labelled_feedback(content)
    if not parsed.get("candidate_name") and not parsed.get("candidate_record_id"):
        return None

    source_message_id = message.get("msgid") or message.get("message_id") or message.get("id")
    source_conversation_id = chatid or message.get("chatid") or message.get("chat_id")
    if chat_type and source_conversation_id:
        source_conversation_id = f"{chat_type}:{source_conversation_id}"

    parsed.update(
        {
            "feedback_id": stable_feedback_id(parsed, {**message, "chatid": source_conversation_id}),
            "interviewer_name": parsed.get("interviewer_name") or message.get("name") or message.get("userid"),
            "source_message_id": source_message_id or stable_feedback_id(parsed, message),
            "source_conversation_id": source_conversation_id,
            "source_message_time": message.get("send_time"),
        }
    )
    return parsed


def parse_feedback_messages(messages: Iterable[dict[str, Any]], chatid: str | None = None, chat_type: int | None = None) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for message in messages:
        item = parse_feedback_message(message, chatid=chatid, chat_type=chat_type)
        if item:
            parsed.append(item)
    return parsed


def feedback_to_record(feedback: dict[str, Any], sync_status: str) -> dict[str, Any]:
    field_values: list[dict[str, Any]] = []
    now = now_ms()
    values = {**feedback, "sync_status": sync_status, "created_at": now, "updated_at": now}
    field_types = {field["field_title"]: field["field_type"] for field in FEEDBACK_FIELDS}

    for key, title in FEEDBACK_KEY_TO_TITLE.items():
        value = values.get(key)
        field_type = field_types.get(title)
        if field_type == "number":
            add_number(field_values, title, value)
        elif field_type == "dateTime":
            add_string(field_values, title, datetime_to_ms(value))
        else:
            add_text(field_values, title, value)
    return {"field_values": field_values}


def mask_identifier(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return "***"


def redact_feedback_value(field: str, value: Any) -> Any:
    if field == "candidate_name":
        return smartsheet.mask_name(value)
    if field == "candidate_record_id":
        return smartsheet.mask_record_id(value)
    if field == "interviewer_name":
        return smartsheet.mask_name(value)
    if field in {"feedback_id", "source_message_id", "source_conversation_id"}:
        return mask_identifier(value)
    if field in FREE_TEXT_FIELDS:
        return REDACTED_TEXT
    return value


def replace_field_value(field_value: dict[str, Any], value: Any) -> None:
    replacement = "" if value is None else str(value)
    if "text_value" in field_value:
        field_value["text_value"] = {"items": [{"type": "text", "text": replacement}]}
    elif "string_value" in field_value:
        field_value["string_value"] = replacement
    else:
        field_value["text_value"] = {"items": [{"type": "text", "text": replacement}]}


def redact_feedback_record(record: dict[str, Any]) -> dict[str, Any]:
    redacted = json.loads(json.dumps(record, ensure_ascii=False))
    for field_value in redacted.get("field_values", []):
        field = field_value.get("field")
        if field in MASKED_TEXT_FIELDS or field in FREE_TEXT_FIELDS:
            original = smartsheet.field_value_to_text(field_value)
            replace_field_value(field_value, redact_feedback_value(field, original))
    return redacted


def redact_candidate_update(update: dict[str, Any]) -> dict[str, Any]:
    redacted = json.loads(json.dumps(update, ensure_ascii=False))
    redacted["record_id"] = smartsheet.mask_record_id(redacted.get("record_id"))
    return redacted


def candidate_from_record(record: dict[str, Any]) -> dict[str, Any]:
    mapped = smartsheet.record_field_text_map(record)
    candidate = {key: None for key in CANDIDATE_KEY_TO_TITLE}
    for title, value in mapped.items():
        key = CANDIDATE_TITLE_TO_KEY.get(title)
        if key:
            candidate[key] = value
    candidate["smartsheet_record_id"] = record.get("record_id")
    candidate["raw"] = mapped
    return candidate


def association_key(candidate: dict[str, Any], key: str) -> str:
    return normalize(candidate.get(key))


def associate_candidate(feedback: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    checks = [
        ("candidate_record_id", "record_id"),
        ("phone", "phone"),
        ("email", "email"),
    ]
    for feedback_key, candidate_key in checks:
        value = normalize(feedback.get(feedback_key))
        if not value:
            continue
        matches = [candidate for candidate in candidates if association_key(candidate, candidate_key) == value]
        if len(matches) == 1:
            return {"status": "matched", "method": feedback_key, "candidate": matches[0]}
        if len(matches) > 1:
            return {"status": "ambiguous", "method": feedback_key, "candidates": matches}

    name = normalize(feedback.get("candidate_name"))
    job_id = normalize(feedback.get("job_id"))
    if name and job_id:
        matches = [
            candidate for candidate in candidates
            if association_key(candidate, "name") == name
            and (
                association_key(candidate, "job_intent") == job_id
                or job_id in association_key(candidate, "job_intent")
            )
        ]
        if len(matches) == 1:
            return {"status": "matched", "method": "name+job", "candidate": matches[0]}
        if len(matches) > 1:
            return {"status": "ambiguous", "method": "name+job", "candidates": matches}

    if name:
        matches = [candidate for candidate in candidates if association_key(candidate, "name") == name]
        if len(matches) == 1:
            return {"status": "matched", "method": "name", "candidate": matches[0]}
        if len(matches) > 1:
            return {"status": "ambiguous", "method": "name", "candidates": matches}

    return {"status": "unmatched", "method": None, "candidates": []}


def stage_index(stage: str | None) -> int:
    if stage in STAGE_ORDER:
        return STAGE_ORDER.index(stage)
    return -1


def stage_for_round(round_name: str | None) -> str | None:
    text = round_name or ""
    for key, stage in ROUND_TO_STAGE.items():
        if key in text:
            return stage
    return None


def next_stage_after(stage: str | None) -> str | None:
    if stage not in STAGE_ORDER:
        return "HR初筛"
    idx = STAGE_ORDER.index(stage)
    if idx + 1 < len(STAGE_ORDER):
        return STAGE_ORDER[idx + 1]
    return stage


def decide_candidate_stage(current_stage: str | None, decision: str | None, interview_round: str | None) -> str | None:
    if decision == "不通过":
        return "不合适"
    if decision in {"待定", None, ""}:
        return current_stage
    if decision in {"通过", "需复试"}:
        round_stage = stage_for_round(interview_round)
        if round_stage and stage_index(round_stage) > stage_index(current_stage):
            return round_stage
        if decision == "需复试":
            return next_stage_after(current_stage)
        return round_stage or current_stage
    return current_stage


def build_candidate_update(candidate: dict[str, Any], feedback: dict[str, Any]) -> dict[str, Any] | None:
    record_id = candidate.get("smartsheet_record_id")
    if not record_id:
        return None
    current_stage = candidate.get("recruiting_stage")
    next_stage = decide_candidate_stage(current_stage, feedback.get("decision"), feedback.get("interview_round"))
    values: list[dict[str, Any]] = []
    if next_stage and next_stage != current_stage:
        add_text(values, CANDIDATE_KEY_TO_TITLE["recruiting_stage"], next_stage)
    if not values:
        return None
    return {"record_id": record_id, "field_values": values}


def ensure_fields(file_id: str, sheet_id: str, fields: list[dict[str, Any]], table_label: str) -> list[dict[str, Any]]:
    existing_fields = smartsheet.list_fields(file_id, sheet_id)
    existing_titles = {field["field_title"] for field in existing_fields}
    expected_types = {field["field_title"]: field["field_type"] for field in fields}
    conflicts = [
        f"{field['field_title']}({field.get('field_type')} != {expected_types[field['field_title']]})"
        for field in existing_fields
        if field.get("field_title") in expected_types
        and field.get("field_type") != expected_types[field["field_title"]]
    ]
    if conflicts:
        raise RuntimeError(f"{table_label}字段类型冲突，请手动处理后重试: " + ", ".join(conflicts))
    missing = [field for field in fields if field["field_title"] not in existing_titles]
    if missing:
        smartsheet.add_fields(file_id, sheet_id, missing)
        existing_fields = smartsheet.list_fields(file_id, sheet_id)
    return existing_fields


def resolve_table(file_id: str | None, title: str, create_new: bool, space_id: str | None, dry_run: bool) -> tuple[str | None, bool, str]:
    return smartsheet.resolve_smartsheet_target(
        file_id,
        title,
        space_id,
        create_new,
        dry_run=dry_run,
        probe_remote=not dry_run,
    )


def resolve_existing_table(file_id: str | None, title: str) -> str:
    if file_id:
        return file_id
    found = smartsheet.find_smartsheet_by_title(title)
    if not found:
        raise RuntimeError(f"未找到必需的智能表格: {title}")
    return found["file_id"]


def first_sheet_id(file_id: str) -> str:
    tables = smartsheet.list_tables(file_id)
    if not tables:
        raise RuntimeError("智能表格中没有工作表")
    return tables[0]["sheet_id"]


def load_messages_from_json(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if isinstance(data.get("messages"), list):
            return data["messages"]
        if isinstance(data.get("message"), dict):
            return [data["message"]]
    if isinstance(data, list):
        return data
    raise ValueError("消息 JSON 必须是数组，或包含 messages 数组。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages-json", help="离线消息 JSON 文件；用于预览或测试。")
    parser.add_argument("--chat-type", type=int, choices=[1, 2], help="企业微信会话类型：1 单聊，2 群聊。")
    parser.add_argument("--chatid", help="企业微信会话 ID；不传 --messages-json 时必填。")
    parser.add_argument("--begin-time", help="拉取消息开始时间，格式 YYYY-MM-DD HH:mm:ss。")
    parser.add_argument("--end-time", help="拉取消息结束时间，格式 YYYY-MM-DD HH:mm:ss。")
    parser.add_argument("--candidate-file-id", help="候选人库 file_id；默认按固定表名 HR候选人库 搜索。")
    parser.add_argument("--feedback-file-id", help="面评表 file_id；默认按固定表名 HR面评记录表 搜索或创建。")
    parser.add_argument("--space-id", help="创建新面评表时使用的知识库空间 ID。")
    parser.add_argument("--create-feedback-table", action="store_true", help="忽略同名搜索结果，强制创建新的面评表。")
    parser.add_argument("--apply", action="store_true", help="实际写入面评表，并在明确匹配时更新候选人阶段。默认只预览。")
    parser.add_argument("--show-sensitive", action="store_true", help="输出明文候选人匹配信息。默认脱敏。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dry_run = not args.apply

    if args.messages_json:
        messages = load_messages_from_json(args.messages_json)
    else:
        if not (args.chat_type and args.chatid and args.begin_time and args.end_time):
            print("实时拉取需同时提供 --chat-type、--chatid、--begin-time、--end-time。", file=sys.stderr)
            return 2
        try:
            messages = wecom_cli.get_messages(args.chat_type, args.chatid, args.begin_time, args.end_time)
        except wecom_cli.WeComCliError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    feedback_items = parse_feedback_messages(messages, chatid=args.chatid, chat_type=args.chat_type)

    candidate_file_id = None
    candidate_sheet_id = None
    candidates: list[dict[str, Any]] = []
    candidate_updates: list[dict[str, Any]] = []
    associations: list[dict[str, Any]] = []

    if feedback_items and args.apply:
        candidate_file_id = resolve_existing_table(
            args.candidate_file_id,
            CANDIDATE_FIXED_TITLE,
        )
        candidate_sheet_id = first_sheet_id(candidate_file_id)
        candidates = [candidate_from_record(record) for record in smartsheet.list_records(candidate_file_id, candidate_sheet_id)]
    elif feedback_items and args.candidate_file_id:
        candidate_file_id = args.candidate_file_id

    for feedback in feedback_items:
        association = associate_candidate(feedback, candidates) if candidates else {"status": "not_loaded", "method": None}
        if association.get("status") == "matched":
            candidate = association["candidate"]
            feedback["candidate_record_id"] = feedback.get("candidate_record_id") or candidate.get("record_id")
            update = build_candidate_update(candidate, feedback)
            if update:
                candidate_updates.append(update)
            feedback["sync_status"] = SYNC_SYNCED if update else SYNC_RECORDED
        elif association.get("status") in {"ambiguous", "unmatched"}:
            feedback["sync_status"] = SYNC_MANUAL
        else:
            feedback["sync_status"] = SYNC_PREVIEW
        associations.append({
            "feedback_id": feedback.get("feedback_id") if args.show_sensitive else mask_identifier(feedback.get("feedback_id")),
            "candidate_name": smartsheet.mask_name(feedback.get("candidate_name")) if not args.show_sensitive else feedback.get("candidate_name"),
            "status": association.get("status"),
            "method": association.get("method"),
            "matched_record_id": (
                association.get("candidate", {}).get("record_id")
                if association.get("status") == "matched" and args.show_sensitive
                else None
            ),
        })

    feedback_records = [
        feedback_to_record(item, item.get("sync_status") or (SYNC_PREVIEW if dry_run else SYNC_RECORDED))
        for item in feedback_items
    ]
    output_feedback_records = feedback_records if args.show_sensitive else [
        redact_feedback_record(record)
        for record in feedback_records
    ]
    output_candidate_updates = candidate_updates if args.show_sensitive else [
        redact_candidate_update(update)
        for update in candidate_updates
    ]

    output: dict[str, Any] = {
        "status": "dry-run" if dry_run else "success",
        "workflow": "wecom-feedback-sync",
        "messages_seen": len(messages),
        "feedback_detected": len(feedback_items),
        "associations": associations,
        "feedback_records": output_feedback_records,
        "candidate_updates": output_candidate_updates,
        "sensitive_values_redacted": not args.show_sensitive,
    }

    if dry_run:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    feedback_file_id, created_new, _ = resolve_table(
        args.feedback_file_id,
        FEEDBACK_FIXED_TITLE,
        create_new=args.create_feedback_table,
        space_id=args.space_id,
        dry_run=False,
    )
    if not feedback_file_id:
        raise RuntimeError("未能解析面评表 file_id")
    feedback_sheet_id = first_sheet_id(feedback_file_id)
    ensure_fields(feedback_file_id, feedback_sheet_id, FEEDBACK_FIELDS, "面评表")
    if created_new:
        smartsheet.clean_default_rows_and_cols(feedback_file_id, feedback_sheet_id, FEEDBACK_FIELDS)

    inserted = smartsheet.add_records(feedback_file_id, feedback_sheet_id, feedback_records) if feedback_records else []
    updated = []
    if candidate_updates and candidate_file_id and candidate_sheet_id:
        updated = smartsheet.update_records(candidate_file_id, candidate_sheet_id, candidate_updates)
    inserted_record_ids = [record.get("record_id") for record in inserted]
    updated_record_ids = [record.get("record_id") for record in updated]
    output_inserted_ids = inserted_record_ids if args.show_sensitive else [
        mask_identifier(record_id)
        for record_id in inserted_record_ids
    ]
    output_updated_ids = updated_record_ids if args.show_sensitive else [
        smartsheet.mask_record_id(record_id)
        for record_id in updated_record_ids
    ]

    output.update({
        "feedback_file_id": feedback_file_id,
        "feedback_sheet_id": feedback_sheet_id,
        "candidate_file_id": candidate_file_id,
        "candidate_sheet_id": candidate_sheet_id,
        "inserted_feedback_record_ids": output_inserted_ids,
        "updated_candidate_record_ids": output_updated_ids,
        "url": f"https://docs.qq.com/smartsheet/{feedback_file_id}",
    })
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
