#!/usr/bin/env python3
"""Preview or send internal recruiting notifications through WeCom."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from typing import Any

import wecom_cli


MAX_WECOM_TEXT_BYTES = 2048


@dataclass
class NotificationContext:
    candidate_name: str
    job_title: str | None = None
    interviewer_name: str | None = None
    interview_time: str | None = None
    interview_mode: str | None = None
    interview_round: str | None = None
    extra_note: str | None = None


TEMPLATES = {
    "interview-confirmation": (
        "面试确认\n"
        "候选人：{candidate_name}\n"
        "岗位：{job_title}\n"
        "轮次：{interview_round}\n"
        "时间：{interview_time}\n"
        "方式：{interview_mode}\n"
        "面试官：{interviewer_name}\n"
        "{extra_note}"
    ),
    "interview-reminder": (
        "面试提醒\n"
        "候选人：{candidate_name}\n"
        "岗位：{job_title}\n"
        "轮次：{interview_round}\n"
        "时间：{interview_time}\n"
        "方式：{interview_mode}\n"
        "{extra_note}"
    ),
    "material-request": (
        "补充材料提醒\n"
        "候选人：{candidate_name}\n"
        "岗位：{job_title}\n"
        "{extra_note}"
    ),
}


def clean_line_value(value: str | None, placeholder: str = "待确认") -> str:
    text = (value or "").strip()
    return text or placeholder


def render_message(kind: str, context: NotificationContext) -> str:
    if kind not in TEMPLATES:
        raise ValueError(f"未知通知类型: {kind}")
    values = asdict(context)
    rendered_values = {
        key: clean_line_value(value, "" if key == "extra_note" else "待确认")
        for key, value in values.items()
    }
    content = TEMPLATES[kind].format(**rendered_values)
    lines = [line.rstrip() for line in content.splitlines()]
    compacted = "\n".join(line for line in lines if line.strip())
    if len(compacted.encode("utf-8")) > MAX_WECOM_TEXT_BYTES:
        raise ValueError("企业微信文本消息超过 2048 字节，请缩短补充说明。")
    return compacted


def build_preview(chat_type: int, chatid: str, kind: str, content: str, dry_run: bool) -> dict[str, Any]:
    return {
        "status": "dry-run" if dry_run else "ready",
        "workflow": "wecom-notify",
        "target": {"chat_type": chat_type, "chatid": chatid},
        "message_kind": kind,
        "content": content,
        "content_bytes": len(content.encode("utf-8")),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-type", type=int, choices=[1, 2], required=True, help="会话类型：1 单聊，2 群聊。")
    parser.add_argument("--chatid", required=True, help="企业微信会话 ID；单聊为 userid，群聊为群 ID。")
    parser.add_argument(
        "--kind",
        choices=sorted(TEMPLATES),
        default="interview-confirmation",
        help="通知模板类型。",
    )
    parser.add_argument("--candidate-name", required=True, help="候选人姓名。")
    parser.add_argument("--job-title", help="岗位名称。")
    parser.add_argument("--interviewer-name", help="面试官姓名。")
    parser.add_argument("--interview-time", help="面试时间。")
    parser.add_argument("--interview-mode", help="面试方式，例如视频/现场/电话。")
    parser.add_argument("--interview-round", help="面试轮次。")
    parser.add_argument("--extra-note", help="补充说明。")
    parser.add_argument("--dry-run", action="store_true", default=True, help="预览消息，不发送。默认开启。")
    parser.add_argument("--send", action="store_true", help="实际发送消息；必须显式传入。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    context = NotificationContext(
        candidate_name=args.candidate_name,
        job_title=args.job_title,
        interviewer_name=args.interviewer_name,
        interview_time=args.interview_time,
        interview_mode=args.interview_mode,
        interview_round=args.interview_round,
        extra_note=args.extra_note,
    )
    content = render_message(args.kind, context)
    dry_run = not args.send
    preview = build_preview(args.chat_type, args.chatid, args.kind, content, dry_run=dry_run)

    if dry_run:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0

    try:
        result = wecom_cli.send_text_message(args.chat_type, args.chatid, content)
    except wecom_cli.WeComCliError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps({
        **preview,
        "status": "sent",
        "wecom_result": result,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
