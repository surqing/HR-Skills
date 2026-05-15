# 企业微信招聘协作工作流

使用官方 `@wecom/cli` 完成内部 HR/面试官协作：面试通知预览/发送、面评消息回收、面评落表和候选人阶段同步。

## 目录

- 范围
- 安装和授权
- wecom-notify
- wecom-feedback-sync
- 面评消息格式
- 候选人关联和阶段同步
- 使用边界
- 预期回复

## 范围

首版只处理内部协作：

- 给 HR 或面试官发送面试确认、提醒、补充材料通知
- 从当前账号可见的企业微信会话拉取 7 天内文本消息
- 将结构化面评写入独立 `HR面评记录表`
- 在候选人明确匹配时只同步 `HR候选人库` 的招聘阶段

不做候选人外联自动化，不创建企业微信日程或会议，不读取不可见私聊。

## 安装和授权

安装官方 CLI：

```bash
npm install -g @wecom/cli
```

安装官方 WeCom CLI skills：

```bash
npx skills add WeComTeam/wecom-cli -y -g
```

首次授权：

```bash
wecom-cli init
```

如果在无浏览器或远程环境中使用，可按 CLI 提示选择二维码授权；常见非交互方式：

```bash
wecom-cli init --noninteractive --no-open
```

检查本 skill 依赖：

```bash
python3 {baseDir}/scripts/dependency_check.py --workflow wecom-notify
python3 {baseDir}/scripts/dependency_check.py --workflow wecom-feedback-sync
python3 {baseDir}/scripts/dependency_check.py --workflow wecom-feedback-sync --probe-remote
```

`wecom-feedback-sync` 还需要腾讯文档写入能力，参考 `dependency_contracts.md` 中的 `tencent_docs.upsert_record`。

## wecom-notify

`wecom-notify` 负责发送内部文本消息。默认只预览，不实际发送。

预览面试确认：

```bash
python3 {baseDir}/scripts/wecom_notify.py \
  --chat-type 1 \
  --chatid "interviewer_userid" \
  --kind interview-confirmation \
  --candidate-name "张三" \
  --job-title "Agent 开发工程师" \
  --interviewer-name "李面试官" \
  --interview-time "2026-05-12 15:00" \
  --interview-mode "视频" \
  --interview-round "技术一面"
```

实际发送：

```bash
python3 {baseDir}/scripts/wecom_notify.py ... --send
```

模板类型：

- `interview-confirmation`
- `interview-reminder`
- `material-request`

发送规则：

- `--chat-type 1` 表示单聊，`--chatid` 为成员 userid
- `--chat-type 2` 表示群聊，`--chatid` 为群 ID
- 文本消息必须控制在企业微信 2048 字节以内
- 没有 `--send` 时永远不调用企业微信发送接口

## wecom-feedback-sync

`wecom-feedback-sync` 读取消息、解析面评、写入面评表，并在明确匹配时同步候选人主表。默认 dry-run。

离线预览：

```bash
python3 {baseDir}/scripts/wecom_feedback_sync.py --messages-json "/path/to/messages.json"
```

实时拉取可见会话：

```bash
python3 {baseDir}/scripts/wecom_feedback_sync.py \
  --chat-type 2 \
  --chatid "group_chat_id" \
  --begin-time "2026-05-09 00:00:00" \
  --end-time "2026-05-10 23:59:59"
```

实际写入和同步：

```bash
python3 {baseDir}/scripts/wecom_feedback_sync.py \
  --messages-json "/path/to/messages.json" \
  --apply
```

目标表：

- 候选人主表：固定表名 `HR候选人库`
- 面评记录表：固定表名 `HR面评记录表`

`HR面评记录表` 不存在时，实际 `--apply` 会创建；候选人主表不会由该工作流创建，必须已有。

## 面评消息格式

建议面试官在可见会话中发送带标签的文本：

```text
候选人：张三
候选人记录ID：张三-abcdef123456
岗位ID：JOB-001
面试官：李四
面试时间：2026-05-12 15:00
面试方式：视频
轮次：技术一面
面评：基础扎实，项目表达清楚，Agent 工具调用经验符合岗位要求。
评分：4.5
备注：需要复核系统设计深度。
结论：需复试
下一步：安排技术二面
```

可识别标签包括：

- 候选人/面试者
- 候选人记录ID/记录ID
- 岗位ID
- 面试官
- 面试时间
- 面试方式/方式
- 面试轮次/轮次
- 面评/面试官面评/评价
- 评分/面试官评分
- 备注/面试官备注
- 结论/面试结论
- 下一步/建议动作
- 电话
- 邮箱

无法解析候选人姓名或候选人记录ID的文本不会进入面评记录。

## 候选人关联和阶段同步

候选人关联优先级：

1. `candidate_record_id`
2. 电话
3. 邮箱
4. 姓名 + 岗位
5. 姓名

如果匹配结果不唯一，面评记录写入 `sync_status = 需人工确认`，不更新候选人主表。

阶段同步规则：

- `decision = 不通过`：招聘阶段更新为 `不合适`
- `decision = 待定`：保留当前阶段
- `decision = 通过`：按面试轮次对应阶段推进；无法判断时保留当前阶段
- `decision = 需复试`：按面试轮次或当前阶段推进一档

候选人主表只保留当前阶段；面评正文、评分、结论和建议动作保存在 `HR面评记录表`。

## 使用边界

- 企业微信消息接口仅支持拉取当前账号可见的会话。
- 消息记录通常只支持最近 7 天。
- 如果私聊不可见，请人工转发面评到可见群或可见会话后再同步。
- 默认输出为排障安全视图；完整候选人信息、原始记录 ID、来源会话/消息 ID 和面评正文必须显式传入 `--show-sensitive` 才会输出。
- 当前脚本只处理文本面评，不解析语音、图片或附件。

## 预期回复

`wecom-notify` 成功时说明：

- 是否 dry-run
- 发送对象 `chat_type` 和 `chatid`
- 消息模板类型
- 消息正文

`wecom-feedback-sync` 成功时说明：

- 读取消息数
- 识别面评数
- 候选人关联状态
- 写入的面评记录 ID
- 更新的候选人记录 ID
- 需要人工确认的记录
