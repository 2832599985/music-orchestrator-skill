# Providers

Default backend is the embedded downloader shipped inside this skill.

Default provider list:

- `JBSouMusicClient`
- `MyFreeMP3MusicClient`
- `MP3JuiceMusicClient`

Optional expansion candidates:

- `TuneHubMusicClient`
- `GDStudioMusicClient`
- `FangpiMusicClient`

Notes:

- some sources are search-only in practice depending on upstream availability
- some candidates may be recommendable but not currently downloadable
- keep source health in mind and prefer graceful degradation over hard failure
- sources that require extra cookies or quark-only download flows are intentionally excluded from the default set
- if the embedded provider search does not return usable results, the skill falls back to a public iTunes search feed for non-downloadable candidate recall

If a source becomes unstable, remove it from `MUSIC_ORCH_SOURCES` without changing the higher-level workflow.
