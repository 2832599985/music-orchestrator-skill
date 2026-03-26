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

下载和搜索后端已经内置在仓库里。只要安装这个 skill 就能运行，不需要额外安装 `musicdl`。

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
- `channels-refresh`
- `channel-auth refresh`
- `channel-search`
- `channel-search-variants`
- `channels-health`
- `playlist create|list|show|add|remove|delete|rename`
- `collection list|show|create|add|remove|merge`
- `history search|recommend`
- `push latest`
- `push-list`
- `push-show`
- `push-mark-consumed`
- `download choose|track|playlist|album|preview|queue|status|files|retry|cancel`
- `listen`

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

`install.sh` 现在只是一个轻量自检脚本。实际运行链路是 `scripts/musicctl -> python3 -> skill 内置后端`。

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
        ├── embedded_music_backend.py
        ├── install.sh
        ├── musicctl
        └── music_orchestrator.py
```

## 常见用法

示例：

```bash
bash ~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channels
bash ~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-auth show --provider MyFreeMP3JuicesMusicClient
bash ~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-auth validate --provider MyFreeMP3JuicesMusicClient
bash ~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-auth refresh --provider MyFreeMP3JuicesMusicClient
bash ~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-auth set --provider MyFreeMP3JuicesMusicClient --cf-clearance "COOKIE_VALUE"
bash ~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-search --provider MyFreeMP3JuicesMusicClient --query "Minami Kawakiwoameku" --limit 8
bash ~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl listen --query "家有女友 主题曲"
bash ~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download choose --track-id TRACK_ID --dry-run
```

推荐听歌/下载流程：

1. 用户说“我要听歌”时，优先直接用 `musicctl listen --query "..."`
2. skill 应先尝试 `MyFreeMP3JuicesMusicClient`
3. 先用 `channel-auth show` 或 `channel-auth validate` 看当前 auth 是否可用
4. 在无图形环境里，优先执行 `channel-auth set --provider MyFreeMP3JuicesMusicClient --cf-clearance "COOKIE_VALUE"`
5. 只有在有可见浏览器时，再执行 `channel-auth refresh --provider MyFreeMP3JuicesMusicClient`
6. 需要只查这个默认渠道时，用 `channel-search` 或 `channel-search-variants`
7. 只有当你想手动控制 provider 时，再用 `download choose` 或 `download track --provider ...`

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

## 可选受保护 Provider

`MyFreeMP3JuicesMusicClient` 是一个默认 provider，对应 `https://2024.myfreemp3juices.cc/`。

- 默认已经包含在 source 集合里
- 需要有效的 `cf_clearance`
- cookie 会保存到 `state/provider_auth.json`
- 环境变量可以覆盖本地状态：
  - `MUSIC_ORCH_MYFREEJUICES_CF_CLEARANCE`
  - `MUSIC_ORCH_MYFREEJUICES_LANG`
- 对 Codex 安装版，建议统一用 `bash scripts/musicctl ...`，因为安装器可能不会保留可执行位
- 可以先查看当前 auth：
  `bash ~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-auth show --provider MyFreeMP3JuicesMusicClient`
- 可以校验当前 auth：
  `bash ~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-auth validate --provider MyFreeMP3JuicesMusicClient`
- 可以通过下面的命令刷新本地 auth：
  `bash ~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-auth refresh --provider MyFreeMP3JuicesMusicClient`
- 也可以手动设置：
  `bash ~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-auth set --provider MyFreeMP3JuicesMusicClient --cf-clearance "COOKIE_VALUE"`
- 也可以清除本地保存的 auth：
  `bash ~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-auth clear --provider MyFreeMP3JuicesMusicClient`
- `channel-auth refresh` 依赖本机 Playwright 和 Chromium：
  `pip install playwright`
  `python3 -m playwright install chromium`
- 在无图形界面的 Ubuntu 上，优先用 `channel-auth set`，因为可见浏览器刷新需要 GUI 运行环境

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
