# Music Orchestrator Skill

[English](README.md)

这是一个面向 OpenClaw 的本地优先音乐 skill。

它不是简单的搜索壳子，而是一个完整的编排层，让模型可以细粒度控制整条音乐工作流：

- 搜索歌曲、专辑、歌单
- 基于本地保存的歌曲和集合分析喜好
- 生成推荐和每日推荐
- 管理本地歌单与集合
- 下载单曲、歌单、专辑
- 先检查可用渠道，再自主选择下载 provider

## 仓库内容

- `SKILL.md`：模型运行规则和操作约束
- `scripts/musicctl`：CLI 入口
- `scripts/music_orchestrator.py`：核心编排与持久化逻辑
- `references/usage.md`：使用说明和示例
- `references/integration.md`：架构与接入说明
- `references/schema.md`：SQLite schema 说明
- `references/providers.md`：provider 默认配置与说明

## 设计目标

- 强解耦，provider、repository、推荐、歌单、下载、推送、历史分层
- 本地优先
- SQLite 持久化
- provider 抽象层
- 为 LLM 编排提供稳定、可预测的命令接口

## 资源范围说明

这个 skill 的使用前提是：处理的音乐资源来自用户本人拥有、上传、管理，或已获授权访问的资源，包括用户明确说明已获授权的个人网盘和个人存储链接。

在产品工作流里，这类链接会被视为用户提供的个人资源。项目本身不独立核验资源权属、授权状态或法律属性，也不提供法律审查结论。

## 已实现能力

当前已实现的命令能力包括：

- `init`
- `search`
- `search-preview`
- `search-variants`
- `analyze`
- `recommend`
- `recommend-plan`
- `recommend-candidates`
- `recommend-commit`
- `recommend-show`
- `daily`
- `track-show`
- `variants`
- `channels`
- `channels-health`
- `playlist create|list|show|add|remove|delete|rename`
- `collection list|show|create|add|remove|merge`
- `history search|recommend`
- `push latest`
- `push-list`
- `push-show`
- `push-mark-consumed`
- `download choose|track|playlist|album|preview|queue|status|files|retry|cancel`

## 安装

将仓库克隆到 OpenClaw 的 skill 目录：

```bash
git clone https://github.com/2832599985/music-orchestrator-skill.git \
  ~/.openclaw/workspace/skills/music-orchestrator
```

然后执行：

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/install.sh
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl init
```

安装完成后的目录结构应类似：

```text
~/.openclaw/workspace/skills/
└── music-orchestrator/
    ├── SKILL.md
    ├── README.md
    ├── README.zh-CN.md
    ├── agents/
    │   └── openai.yaml
    ├── references/
    │   ├── usage.md
    │   ├── integration.md
    │   ├── schema.md
    │   └── providers.md
    └── scripts/
        ├── install.sh
        ├── musicctl
        └── music_orchestrator.py
```

## 常见用法

示例：

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channels
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channels-health --refresh
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl search --query "city pop 夜晚" --type mixed --limit 10
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl analyze --collection likes
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl recommend --collection likes --limit 12
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl playlist create --name "今晚循环"
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl variants --track-id TRACK_ID
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download choose --track-id TRACK_ID --refresh-health
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download track --track-id TRACK_ID --provider JBSouMusicClient
```

推荐下载流程：

1. 先用 `musicctl channels` 查看有哪些可用渠道
2. 再用 `musicctl channels-health --refresh` 查看实时健康状态
3. 用 `musicctl variants --track-id ...` 查看这首歌有哪些来源变体
4. 优先用 `musicctl download choose --track-id ... --refresh-health` 自动选源
5. 只有当你想手动控制 provider 时，再用 `musicctl download track --track-id ... --provider ...`

## Provider 健康探针

`channels-health` 不是占位命令，而是真正的 provider 健康探针。它支持：

- 全量探测所有 provider
- 仅探测单个 provider
- 读取最近一次缓存结果
- 通过 `--refresh` 强制实时探测

返回字段包括：

- `status`
- `latency_ms`
- `result_count`
- `downloadable_count`
- `error`
- `sample_titles`
- `severity`

探测结果会持久化到 `provider_health`。

## 文档入口

- 使用说明：[references/usage.md](references/usage.md)
- 接入说明：[references/integration.md](references/integration.md)
- Schema 说明：[references/schema.md](references/schema.md)
- Provider 说明：[references/providers.md](references/providers.md)

## 当前状态

已经本地验证过：

- 初始化
- 搜索和变体查询
- 喜好分析
- 推荐规划和推荐提交
- 歌单与集合操作
- 单曲下载
- provider 健康探测
- `python3 -m py_compile`

还没做重度压测的部分：

- 长时间后台下载 worker
- 更复杂的持久任务调度
- 更重的 embedding 推荐模型
- 更深的 `llm-task` 集成
