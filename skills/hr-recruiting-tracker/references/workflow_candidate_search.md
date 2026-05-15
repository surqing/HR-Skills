# 候选人搜索工作流 (candidate-search)

在 `HR候选人库` 中按结构化条件搜索、去重并汇总已有候选人记录。

## 目录

- 目标
- 前置条件
- 搜索条件
- 输出格式
- 使用示例
- 注意事项

## 目标

此工作流用于只读检索候选人库，输出命中记录与结构化汇总，帮助招聘人员快速盘点、筛选和识别重复候选人。

## 前置条件

与 `candidate-upload` 相同：

1. 已安装 `tencent-docs` skill
2. 已安装 `mcporter`
3. 已完成腾讯文档授权

检查：

```bash
python3 {baseDir}/scripts/dependency_check.py --workflow candidate-search
python3 {baseDir}/scripts/dependency_check.py --workflow candidate-search --probe-remote
```

## 搜索条件

支持按以下字段筛选：

- 姓名
- 电话
- 邮箱
- 当前公司
- 最高学历
- 毕业院校
- 专业
- 技能标签
- 求职意向
- 招聘阶段
- 简历来源
- 解析质量

支持额外的 `contains` 关键词检索，用于跨字段包含匹配。

## 输出格式

默认输出 JSON，包含两部分：

- `records`：命中的候选人明细，默认脱敏
- `summary`：命中数、阶段分布、技能分布、解析质量分布、缺失字段统计、疑似重复候选人分组

默认输出为排障安全视图；如需查看候选人姓名、联系方式、原始记录 ID 或简历来源，必须显式传入 `--show-sensitive`。

## 使用示例

按姓名和技能检索：

```bash
python3 {baseDir}/scripts/candidate_search.py --name "张三" --skills "Python"
```

按阶段和公司盘点：

```bash
python3 {baseDir}/scripts/candidate_search.py --recruiting-stage "HR初筛" --current-company "OpenAI" --include-all
```

查看明文：

```bash
python3 {baseDir}/scripts/candidate_search.py --email "zhangsan@example.com" --show-sensitive
```

## 注意事项

- 默认只读，不写回腾讯文档。
- 默认输出脱敏后的排障安全视图；完整敏感值只在显式传入 `--show-sensitive` 时输出。
- 默认去重；如需保留重复命中可传 `--with-duplicates`。
- 默认返回前 50 条记录；需要完整结果可传 `--include-all`。
- “分析”仅指结构化统计，不包含生成式长摘要。
