#!/usr/bin/env python3
"""只读搜索和汇总腾讯文档候选人库。"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from typing import Any

import table_models
import upload_to_smartsheet as smartsheet


CANDIDATE_TABLE = "candidates"
CANDIDATE_FIXED_TITLE = table_models.get_fixed_sheet_title(CANDIDATE_TABLE)
FIELD_TITLE_BY_KEY = table_models.get_field_key_to_title(CANDIDATE_TABLE)
FIELD_KEYS_BY_TITLE = {title: key for key, title in FIELD_TITLE_BY_KEY.items()}
SEARCHABLE_FIELDS = {
    "name",
    "phone",
    "email",
    "current_company",
    "highest_degree",
    "school",
    "major",
    "skills",
    "job_intent",
    "recruiting_stage",
    "resume_source",
    "parse_quality",
}
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file-id", help="已有候选人库 file_id；不填时按固定表名搜索。")
    parser.add_argument("--sheet-title", default=CANDIDATE_FIXED_TITLE, help=f"固定候选人库名称（默认：{CANDIDATE_FIXED_TITLE}）")
    parser.add_argument("--space-id", help="保留参数，仅用于输出目标定位信息。")
    parser.add_argument("--name", action="append", help="按姓名筛选，可重复传入。")
    parser.add_argument("--phone", action="append", help="按电话筛选，可重复传入。")
    parser.add_argument("--email", action="append", help="按邮箱筛选，可重复传入。")
    parser.add_argument("--current-company", action="append", help="按当前公司筛选，可重复传入。")
    parser.add_argument("--skills", action="append", help="按技能关键词筛选，可重复传入。")
    parser.add_argument("--job-intent", action="append", help="按求职意向筛选，可重复传入。")
    parser.add_argument("--recruiting-stage", action="append", help="按招聘阶段筛选，可重复传入。")
    parser.add_argument("--highest-degree", action="append", help="按最高学历筛选，可重复传入。")
    parser.add_argument("--school", action="append", help="按毕业院校筛选，可重复传入。")
    parser.add_argument("--major", action="append", help="按专业筛选，可重复传入。")
    parser.add_argument("--resume-source", action="append", help="按简历来源筛选，可重复传入。")
    parser.add_argument("--parse-quality", action="append", help="按解析质量筛选，可重复传入。")
    parser.add_argument("--contains", action="append", help="任意字段包含关键词，支持重复传入。")
    parser.add_argument("--limit", type=int, default=50, help="返回明细上限，默认 50。")
    parser.add_argument("--include-all", action="store_true", help="返回全部命中明细，不截断。")
    parser.add_argument("--show-sensitive", action="store_true", help="输出明文敏感字段。默认脱敏。")
    parser.add_argument("--with-duplicates", action="store_true", help="明细中保留疑似重复候选人，不在结果中折叠。")
    parser.add_argument("--list-only", action="store_true", help="只列出匹配记录，不输出汇总。")
    return parser.parse_args()


def normalize(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    return re.sub(r"\s+", "", text)


def split_keywords(values: list[str] | None) -> list[str]:
    items: list[str] = []
    for value in values or []:
        for part in re.split(r"[，,、;；\s]+", value):
            part = part.strip()
            if part:
                items.append(part)
    return items


def contains_any(haystack: str, needles: list[str]) -> bool:
    if not needles:
        return True
    normalized_haystack = normalize(haystack)
    return any(normalize(needle) in normalized_haystack for needle in needles)


def field_matches(value: str, expected_values: list[str]) -> bool:
    if not expected_values:
        return True
    normalized_value = normalize(value)
    return any(normalize(expected) in normalized_value for expected in expected_values)


def record_to_candidate(record: dict[str, Any]) -> dict[str, Any]:
    mapped = smartsheet.record_field_text_map(record)
    result: dict[str, Any] = {key: None for key in SEARCHABLE_FIELDS}
    for field_title, value in mapped.items():
        key = FIELD_KEYS_BY_TITLE.get(field_title)
        if key in SEARCHABLE_FIELDS:
            result[key] = value
    result["record_id"] = mapped.get("记录ID") or record.get("record_id")
    result["raw"] = mapped
    return result


def canonical_field_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def candidate_summary_view(candidate: dict[str, Any]) -> dict[str, Any]:
    resume_source = candidate.get("resume_source")
    return {
        "record_id": candidate.get("record_id"),
        "name": smartsheet.mask_name(candidate.get("name")),
        "phone": smartsheet.mask_phone(candidate.get("phone")),
        "email": smartsheet.mask_email(candidate.get("email")),
        "current_company": candidate.get("current_company"),
        "highest_degree": candidate.get("highest_degree"),
        "school": candidate.get("school"),
        "major": candidate.get("major"),
        "skills": candidate.get("skills"),
        "job_intent": candidate.get("job_intent"),
        "recruiting_stage": candidate.get("recruiting_stage"),
        "resume_source": smartsheet.redact_string_field("简历来源", resume_source) if resume_source else None,
        "parse_quality": candidate.get("parse_quality"),
    }


def find_smartsheet_by_title(title: str) -> dict[str, str] | None:
    result = smartsheet.mcporter_call("manage.search_file", {"search_key": title})
    for item in result.get("list", []):
        if item.get("title") == title and item.get("file_id"):
            return item
    return None


def resolve_target(file_id: str | None, sheet_title: str) -> tuple[str, bool]:
    if file_id:
        return file_id, False
    found = find_smartsheet_by_title(sheet_title)
    if found:
        return found["file_id"], False
    raise RuntimeError(f"未找到候选人库: {sheet_title}")


def record_matches(candidate: dict[str, Any], filters: dict[str, list[str]], contains_terms: list[str]) -> bool:
    checks = {key: canonical_field_value(candidate.get(key)) for key in SEARCHABLE_FIELDS}
    for key, expected_values in filters.items():
        if not field_matches(checks.get(key, ""), expected_values):
            return False
    if contains_terms:
        haystack = " ".join(str(value) for value in checks.values() if value)
        if not contains_any(haystack, contains_terms):
            return False
    return True


def candidate_identity_key(candidate: dict[str, Any]) -> str:
    parts = [
        normalize(candidate.get("name")),
        normalize(candidate.get("phone")),
        normalize(candidate.get("email")),
    ]
    for part in parts:
        if part:
            return part
    return normalize(candidate.get("record_id"))


def build_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    stage_counter = Counter()
    skill_counter = Counter()
    quality_counter = Counter()
    missing_counter = Counter()
    duplicate_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for candidate in candidates:
        stage = candidate.get("recruiting_stage") or "未填写"
        stage_counter[stage] += 1
        quality_counter[candidate.get("parse_quality") or "未填写"] += 1
        for skill in split_keywords([candidate.get("skills") or ""]):
            skill_counter[skill] += 1

        for key in ("name", "phone", "email", "current_company", "highest_degree", "school", "major", "skills", "job_intent", "recruiting_stage"):
            if not candidate.get(key):
                missing_counter[key] += 1

        duplicate_groups[candidate_identity_key(candidate)].append(candidate)

    duplicates = [
        {
            "group_key": key,
            "count": len(group),
            "record_ids": [item.get("record_id") for item in group if item.get("record_id")],
            "names": [item.get("name") for item in group if item.get("name")],
        }
        for key, group in duplicate_groups.items()
        if key and len(group) > 1
    ]

    return {
        "count": len(candidates),
        "stage_distribution": dict(stage_counter),
        "skill_distribution": dict(skill_counter.most_common(20)),
        "parse_quality_distribution": dict(quality_counter),
        "missing_field_distribution": dict(missing_counter),
        "duplicate_groups": duplicates,
    }


def redact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(candidate)
    redacted["name"] = smartsheet.mask_name(candidate.get("name"))
    redacted["phone"] = smartsheet.mask_phone(candidate.get("phone"))
    redacted["email"] = smartsheet.mask_email(candidate.get("email"))
    redacted["resume_source"] = smartsheet.redact_string_field("简历来源", candidate.get("resume_source"))
    redacted["record_id"] = smartsheet.mask_record_id(candidate.get("record_id"))
    return redacted


def main() -> int:
    args = parse_args()
    file_id, _created_new = resolve_target(args.file_id, args.sheet_title)

    tables = smartsheet.list_tables(file_id)
    if not tables:
        raise RuntimeError("候选人库中没有工作表")
    sheet_id = tables[0]["sheet_id"]

    records = smartsheet.list_records(file_id, sheet_id)
    candidates = [record_to_candidate(record) for record in records]

    filters = {
        "name": split_keywords(args.name),
        "phone": split_keywords(args.phone),
        "email": split_keywords(args.email),
        "current_company": split_keywords(args.current_company),
        "highest_degree": split_keywords(args.highest_degree),
        "school": split_keywords(args.school),
        "major": split_keywords(args.major),
        "skills": split_keywords(args.skills),
        "job_intent": split_keywords(args.job_intent),
        "recruiting_stage": split_keywords(args.recruiting_stage),
        "resume_source": split_keywords(args.resume_source),
        "parse_quality": split_keywords(args.parse_quality),
    }
    contains_terms = split_keywords(args.contains)

    matched = [candidate for candidate in candidates if record_matches(candidate, filters, contains_terms)]
    summary_candidates = list(matched)

    if not args.with_duplicates:
        deduped: list[dict[str, Any]] = []
        seen = set()
        for candidate in matched:
            key = candidate_identity_key(candidate)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            deduped.append(candidate)
        matched = deduped

    summary = build_summary(summary_candidates)
    total_count = len(summary_candidates)
    if not args.include_all and args.limit >= 0:
        matched = matched[: args.limit]

    summary.update({
        "matched_before_limit": total_count,
        "returned": len(matched),
        "file_id": file_id,
        "sheet_id": sheet_id,
        "sheet_title": args.sheet_title,
    })

    result = {
        "status": "success",
        "summary": summary,
        "records": [
            candidate_summary_view(candidate) if not args.show_sensitive else candidate
            for candidate in matched
        ],
        "sensitive_values_redacted": not args.show_sensitive,
    }

    if args.list_only:
        result.pop("summary", None)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
