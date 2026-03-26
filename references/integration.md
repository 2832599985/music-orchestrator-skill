# Integration Notes

## Architecture

The skill is split into these layers:

- orchestrator instructions in `SKILL.md`
- command entrypoint in `scripts/musicctl`
- domain and persistence in `scripts/music_orchestrator.py`
- external provider adapter through `musicdl`

Top-level flow:

1. resolve collection
2. analyze profile
3. plan search
4. aggregate candidates
5. rank and dedupe
6. persist recommendation run
7. optionally push, build playlist, or download

## Stable Interfaces

Do not break these JSON shapes without updating the whole skill:

- `TrackRecord`
- `CandidateTrack`
- `UserProfile`
- `RecommendationRun`
- `DownloadJob`
- `PushItem`

The main command emits JSON for machine use. Keep field names backward compatible.

Channel discovery is also part of the stable interface:

- `musicctl channels`
- `musicctl channels-health`

This lets the model inspect active downloadable providers before choosing a source-specific download path.

`channels-health --refresh` is the active probe path. It performs a lightweight per-provider search probe, persists the latest result, and returns:

- provider status
- latency
- result count
- downloadable count
- sample titles
- severity classification

The current command surface is intentionally split into four groups:

- observation: `track-show`, `variants`, `history`, `push-list`, `download status`
- search: `search-preview`, `search`, `search-variants`
- recommendation: `recommend-plan`, `recommend-candidates`, `recommend-commit`, `recommend-show`
- execution: playlist, collection, push, and download mutations

Keep those boundaries stable so the model can orchestrate with finer granularity.

## Adding a Provider

Add a provider by extending the adapter layer in `scripts/music_orchestrator.py`.

Required methods:

- search songs
- search albums
- search playlists
- resolve downloadable variants

Provider code must not:

- write SQLite directly
- format final recommendation prose
- own playlist logic

## Replacing SQLite

Keep the repository method signatures stable and swap the backend implementation behind:

- track persistence
- collection persistence
- recommendation runs
- push queue
- download jobs

Do not let service or provider code talk raw SQL.

## Real Platform Integration

To add authenticated platforms later:

- keep the current `MusicdlAdapter` for anonymous and aggregate sources
- add a separate `AccountProvider` abstraction for user-bound APIs
- keep analyzer, planner, push, and playlist services unchanged

## llm-task Integration

This skill must work without `llm-task`.

If `llm-task` is available, use it only for structured narrowing of already standardized candidates.
Do not make it a hard dependency for basic search, recommendation, or download workflows.

## Download Extensions

If you add a different downloader later:

- keep `DownloadService` as the entry point
- return a standardized file record with path, status, provider, and timestamps
- preserve queue semantics for playlist and album downloads
