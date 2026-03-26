# Schema

Logical tables used by the skill:

- `tracks`
- `track_variants`
- `collections`
- `collection_items`
- `profile_snapshots`
- `search_history`
- `recommendation_runs`
- `recommendation_items`
- `playlists`
- `playlist_items`
- `download_jobs`
- `download_files`
- `push_queue`
- `push_history`

## Core semantics

- `likes` is the default system collection
- a playlist is local state, not a remote platform playlist
- recommendation runs are daily-cache aware
- push queue stores follow-up actions for the assistant

## Track identity

Tracks are deduplicated with a normalized key built from:

- title
- artists
- album

Provider IDs are stored separately so the same logical track can have multiple variants.
