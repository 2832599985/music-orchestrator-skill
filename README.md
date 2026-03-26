# Music Orchestrator Skill

[中文说明](README.zh-CN.md)

Local-first music skill for OpenClaw.

It is designed as an orchestrator rather than a thin search wrapper, so the model can control the full workflow end to end:

- search songs, albums, and playlists
- analyze local taste from saved tracks and collections
- generate recommendations and daily recommendations
- manage local playlists and collections
- download tracks, playlists, and albums
- inspect available channels and choose a download provider explicitly

The download/search backend is embedded in this repository. Installing the skill is enough to run it; no separate `musicdl` package install is required.

## What It Includes

- `SKILL.md`: runtime behavior and operating rules for the model
- `scripts/musicctl`: CLI entrypoint
- `scripts/music_orchestrator.py`: orchestration and persistence logic
- `references/usage.md`: user-facing examples
- `references/integration.md`: architecture and extension notes
- `references/schema.md`: SQLite schema notes
- `references/providers.md`: provider defaults and notes

## Design Goals

- strong decoupling between providers, repositories, recommendation flow, playlist flow, download flow, push flow, and history flow
- local-first workflow
- SQLite persistence
- provider abstraction layer
- predictable command surface for LLM-driven orchestration

## Resource Scope

This skill is intended to operate on music resources that the user personally owns, uploaded, manages, or is otherwise authorized to access, including personal cloud-drive and personal storage links that the user states are authorized.

Within this workflow, those links are treated as user-provided personal resources. The project does not independently verify ownership, licensing status, or legal entitlement, and it is not a legal review tool.

## Implemented Capabilities

Current command surface includes:

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

## Install

Clone the repository into your OpenClaw skill workspace:

```bash
git clone https://github.com/2832599985/music-orchestrator-skill.git \
  ~/.openclaw/workspace/skills/music-orchestrator
```

Then run:

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/install.sh
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl init
```

`install.sh` is only a lightweight environment check. The runtime path is `scripts/musicctl -> python3 -> embedded skill backend`.

Expected layout after install:

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

## Typical Usage

Examples:

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channels
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-auth refresh --provider MyFreeMP3JuicesMusicClient
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-auth set --provider MyFreeMP3JuicesMusicClient --cf-clearance "COOKIE_VALUE"
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-search --provider MyFreeMP3JuicesMusicClient --query "Minami Kawakiwoameku" --limit 8
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channels-health --refresh
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl search --query "city pop 夜晚" --type mixed --limit 10
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl analyze --collection likes
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl recommend --collection likes --limit 12
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl playlist create --name "今晚循环"
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl variants --track-id TRACK_ID
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download choose --track-id TRACK_ID --refresh-health
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download track --track-id TRACK_ID --provider JBSouMusicClient
```

Recommended listen/download flow:

1. Use `musicctl listen --query "..."` when the user wants to hear a song now
2. The skill should first try `MyFreeMP3JuicesMusicClient`
3. If auth is missing, refresh it with `musicctl channel-auth refresh --provider MyFreeMP3JuicesMusicClient`
4. If you already have a fresh cookie value, set it directly with `musicctl channel-auth set --provider MyFreeMP3JuicesMusicClient --cf-clearance "COOKIE_VALUE"`
5. Use `channel-search` or `channel-search-variants` when you need provider-only inspection
6. Use `download choose` or `download track --provider ...` only when you want manual provider control

## Provider Health

`channels-health` is a real provider probe, not a placeholder. It supports:

- probing all providers or a single provider
- reading the latest cached result
- forcing a live probe with `--refresh`

Returned fields include:

- `status`
- `latency_ms`
- `result_count`
- `downloadable_count`
- `error`
- `sample_titles`
- `severity`

Probe results are persisted into `provider_health`.

## Optional Protected Provider

`MyFreeMP3JuicesMusicClient` is a default provider for `https://2024.myfreemp3juices.cc/`.

- It is included in the default source set.
- It requires a valid `cf_clearance`.
- The cookie can be stored in `state/provider_auth.json`.
- Environment variables override the saved state:
  - `MUSIC_ORCH_MYFREEJUICES_CF_CLEARANCE`
  - `MUSIC_ORCH_MYFREEJUICES_LANG`
- You can refresh local auth state with:
  `~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-auth refresh --provider MyFreeMP3JuicesMusicClient`
- You can also set it manually:
  `~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channel-auth set --provider MyFreeMP3JuicesMusicClient --cf-clearance "COOKIE_VALUE"`
- `channel-auth refresh` requires local Playwright and Chromium:
  `pip install playwright`
  `python3 -m playwright install chromium`

## Documentation

- Usage guide: [references/usage.md](references/usage.md)
- Integration notes: [references/integration.md](references/integration.md)
- Schema notes: [references/schema.md](references/schema.md)
- Provider notes: [references/providers.md](references/providers.md)

## Status

Verified locally:

- initialization
- search and variant lookup
- taste analysis
- recommendation planning and commit
- playlist and collection operations
- single-track download
- provider health probing
- `python3 -m py_compile`

Not yet heavily stress-tested:

- long-running background download workers
- complex persistent job scheduling
- heavier embedding-based recommenders
- deep `llm-task` integration
