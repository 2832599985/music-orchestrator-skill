# Music Orchestrator Skill

Local-first music skill for OpenClaw.

It is designed as an orchestrator rather than a thin search wrapper, so the model can control the full workflow end to end:

- search songs, albums, and playlists
- analyze local taste from saved tracks and collections
- generate recommendations and daily recommendations
- manage local playlists and collections
- download tracks, playlists, and albums
- inspect available channels and choose a download provider explicitly

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
- `channels-health`
- `playlist create|list|show|add|remove|delete|rename`
- `collection list|show|create|add|remove|merge`
- `history search|recommend`
- `push latest`
- `push-list`
- `push-show`
- `push-mark-consumed`
- `download track|playlist|album|preview|queue|status|files|retry|cancel`

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

## Typical Usage

Examples:

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channels
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channels-health --refresh
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl search --query "city pop 夜晚" --type mixed --limit 10
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl analyze --collection likes
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl recommend --collection likes --limit 12
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl playlist create --name "今晚循环"
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl variants --track-id TRACK_ID
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download track --track-id TRACK_ID --provider JBSouMusicClient
```

Recommended download flow:

1. Inspect channels with `musicctl channels`
2. Probe health with `musicctl channels-health --refresh`
3. Check source variants with `musicctl variants --track-id ...`
4. Download with `musicctl download track --track-id ... --provider ...`

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
