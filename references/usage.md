# Usage

## What This Skill Does

This skill gives OpenClaw a local music workflow with persistent state.

Main capabilities:

- search songs, albums, and playlists
- save and manage local playlists
- analyze listening taste from a local collection
- generate daily recommendations
- push recommendations into conversation and local queue
- download tracks, playlists, and albums

## Setup

Install dependencies:

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/install.sh
```

Initialize state:

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl init
```

After creating or updating the skill, refresh OpenClaw skills or restart the gateway so the new skill is indexed.

Optional environment variables:

```bash
export MUSIC_ORCH_DB=/custom/path/music.db
export MUSIC_ORCH_DOWNLOADS=/custom/path/downloads
export MUSIC_ORCH_SOURCES="JBSouMusicClient,MyFreeMP3MusicClient,MP3JuiceMusicClient"
export MUSIC_ORCH_MODEL_MODE="native"
```

## Common Prompts

- 帮我搜 10 首适合深夜听的中文流行歌
- 分析一下我本地 likes 集合的偏好
- 用 likes 和 “通勤” 歌单一起给我做推荐
- 给我今天的每日推荐，尽量别和昨天重复
- 把刚推荐的前 5 首推送出来
- 先查一下当前有哪些下载渠道，再选一个帮我下载
- 先给我推荐计划，不要直接推荐结果
- 先把推荐候选列出来，我挑完再正式提交
- 看一下这首歌有哪些下载变体和来源
- 建一个叫“今晚循环”的歌单，把刚才推荐里能下载的都加进去
- 下载刚推荐里的第 2 首
- 下载“今晚循环”这个歌单
- 下载这个专辑：`<album id>`
- 只推荐当前可以下载的结果

## CLI Examples

Search:

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channels
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channels-health
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channels-health --refresh
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channels-health --provider JBSouMusicClient
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl channels-health --provider JBSouMusicClient --refresh
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl search-preview --query "city pop 夜晚" --type mixed --limit 15

~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl search \
  --query "city pop 夜晚" \
  --type mixed \
  --limit 15

~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl search-variants \
  --query "city pop 夜晚" \
  --type mixed \
  --limit 15
```

Analyze:

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl analyze --collection likes
```

Recommend:

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl recommend-plan \
  --collection likes \
  --limit 12

~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl recommend-candidates \
  --collection likes \
  --limit 12

~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl recommend-commit \
  --candidate-set-id CANDIDATE_SET_ID

~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl recommend \
  --collection likes \
  --limit 12
```

Daily recommendation:

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl daily
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl daily --refresh
```

Playlist management:

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl playlist create --name "今晚循环"
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl playlist list
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl playlist show --playlist "今晚循环"
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl playlist add --playlist "今晚循环" --track-id TRACK_ID
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl playlist remove --playlist "今晚循环" --track-id TRACK_ID
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl playlist rename --playlist "今晚循环" --to "夜间循环"
```

Collections:

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl collection list
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl collection show --collection likes
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl collection create --collection 通勤
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl collection merge --collection 综合偏好 --sources likes,通勤
```

Download:

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl variants --track-id TRACK_ID
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl track-show --track-id TRACK_ID
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download preview --track-id TRACK_ID
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download choose --track-id TRACK_ID --dry-run
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download choose --track-id TRACK_ID --refresh-health
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download track --track-id TRACK_ID
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download track --track-id TRACK_ID --provider JBSouMusicClient
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download playlist --playlist "今晚循环"
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download album --album-id ALBUM_ID
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download queue --limit 10
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download status --job-id JOB_ID
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl download files --limit 10
```

Push:

```bash
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl push latest
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl push-list --limit 10
~/.openclaw/workspace/skills/music-orchestrator/scripts/musicctl push-show --id PUSH_ID
```

## Suggested Conversation Pattern

1. Search or analyze a collection.
2. Ask for recommendations.
3. Review pushed candidates.
4. Create or update a playlist.
5. Download selected content.

## Common Failure Cases

- `musicdl is not installed`
  Run `scripts/install.sh`.
- `No collection found`
  Create a playlist or save some tracks first.
- `No downloadable variant`
  The candidate may be searchable but not currently downloadable.
- `fallback_only_results`
  The search fell back to `ITunesFallback` or another search-only source, so metadata exists but no direct download source was found.
- `Batch job queued`
  This is expected for playlist and album downloads.
