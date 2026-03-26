---
name: music-orchestrator
description: Local-first music assistant for OpenClaw. Use when the user wants to search songs, playlists, or albums; manage local playlists; analyze taste from liked tracks or playlists; generate daily recommendations; push song candidates; or download songs, playlists, or albums through a local SQLite-backed workflow with pluggable providers.
metadata:
  {
    "openclaw":
      {
        "emoji": "🎵",
        "requires": { "bins": ["bash", "python3"] }
      }
  }
---

# Music Orchestrator

Use this skill for a local-first music workflow inside OpenClaw.

This skill is designed for:

- song, album, and playlist search
- local playlist management
- user taste analysis
- daily recommendations
- pushing candidate songs back into the conversation and local queue
- song, playlist, and album downloads

It uses:

- a local SQLite database
- Python scripts in `scripts/`
- `musicdl` as the default search and download backend
- provider and repository abstractions so future platform integrations do not change the top-level workflow

## When To Use

Trigger this skill for requests like:

- "搜几首适合我最近口味的歌"
- "分析一下我这个收藏歌单"
- "给我今天的每日推荐"
- "帮我建个歌单并把这些歌加进去"
- "把这些推荐推送出来"
- "下载这首歌"
- "下载这个专辑"
- "下载这个歌单"

Do not use this skill for:

- generic web browsing unrelated to music
- editing audio files
- DRM circumvention or bypassing paid access

## Resource Scope

This skill is intended for links and downloadable resources that the user personally owns, uploaded, manages, or is otherwise authorized to access, including personal cloud-drive and personal storage links that the user states are authorized.

Treat such links as user-provided personal resources within the product workflow. The skill does not independently determine ownership, licensing status, or legal entitlement, and it does not provide legal review.

## First Run

Install dependencies and initialize the local database:

```bash
{baseDir}/scripts/install.sh
{baseDir}/scripts/musicctl init
```

Default database path:

```text
~/.openclaw/workspace/skills/music-orchestrator/state/music.db
```

Default downloads path:

```text
~/.openclaw/workspace/skills/music-orchestrator/state/downloads
```

## Workflow

### 1. Resolve the user intent

Pick one of these flows:

- search only
- analyze only
- recommend
- playlist management
- download
- push previous recommendation or download results

### 2. Use scripts, not ad-hoc SQL

Always use `scripts/musicctl`. Do not write SQL manually unless you are debugging the skill itself.

Common commands:

```bash
{baseDir}/scripts/musicctl channels
{baseDir}/scripts/musicctl channels-health
{baseDir}/scripts/musicctl channels-health --refresh
{baseDir}/scripts/musicctl channels-health --provider JBSouMusicClient
{baseDir}/scripts/musicctl channels-health --provider JBSouMusicClient --refresh
{baseDir}/scripts/musicctl search-preview --query "周杰伦 晚上 听" --type mixed --limit 12
{baseDir}/scripts/musicctl search --query "周杰伦 晚上 听" --type mixed --limit 12
{baseDir}/scripts/musicctl search-variants --query "周杰伦 晚上 听" --type mixed --limit 12
{baseDir}/scripts/musicctl analyze --collection likes
{baseDir}/scripts/musicctl recommend-plan --collection likes --limit 12
{baseDir}/scripts/musicctl recommend-candidates --collection likes --limit 12
{baseDir}/scripts/musicctl recommend-commit --candidate-set-id CANDIDATE_SET_ID
{baseDir}/scripts/musicctl recommend --collection likes --limit 12
{baseDir}/scripts/musicctl daily --refresh
{baseDir}/scripts/musicctl variants --track-id TRACK_ID
{baseDir}/scripts/musicctl track-show --track-id TRACK_ID
{baseDir}/scripts/musicctl playlist create --name "今晚循环"
{baseDir}/scripts/musicctl playlist show --playlist "今晚循环"
{baseDir}/scripts/musicctl playlist add --playlist "今晚循环" --track-id TRACK_ID
{baseDir}/scripts/musicctl download track --track-id TRACK_ID
{baseDir}/scripts/musicctl download preview --track-id TRACK_ID
{baseDir}/scripts/musicctl download track --track-id TRACK_ID --provider JBSouMusicClient
{baseDir}/scripts/musicctl download queue --limit 10
{baseDir}/scripts/musicctl download status --job-id JOB_ID
{baseDir}/scripts/musicctl download playlist --playlist "今晚循环"
{baseDir}/scripts/musicctl push latest
{baseDir}/scripts/musicctl push-list --limit 10
```

### 3. Recommendation behavior

Recommendation is local-first and uses:

- collection resolution
- profile analysis
- multi-query search planning
- candidate dedupe
- ranking
- model judgment for final narration and curation

If `llm-task` is available in the environment, you may use it for structured candidate selection. If not, use the main model directly from the standardized command output.

Prefer the finer-grained path when the user wants control:

- `recommend-plan` when you only need the strategy
- `recommend-candidates` when you want to inspect candidates before committing
- `recommend-commit` after the model chooses to accept the candidate set
- `variants` or `download preview` before selecting a provider for download

### 4. Download behavior

- single track downloads may run synchronously
- playlist and album downloads are queued
- all download results are recorded in SQLite and added to push history
- when the user supplies personal cloud-drive or personal resource links and states that they are authorized, treat them as user-authorized resources
- do not present downloads as copyright validation, licensing validation, or legal advice

### 5. Push behavior

Push means both:

- returning a structured shortlist to the conversation
- persisting a local push queue entry for follow-up actions

## Output Rules

When reporting search or recommendation results:

- prefer concise lists
- include source and downloadability
- clearly label items that are not downloadable right now
- mention the local playlist name if a playlist was changed

When reporting downloads:

- include destination path
- include queue status for batch jobs
- include failure counts if any

## References

Read these only when needed:

- full user usage: [references/usage.md](references/usage.md)
- developer integration notes: [references/integration.md](references/integration.md)
- schema and data model: [references/schema.md](references/schema.md)
- provider notes and defaults: [references/providers.md](references/providers.md)
