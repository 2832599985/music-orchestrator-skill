#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from embedded_music_backend import EmbeddedMusicBackend, MYFREEJUICES_PROVIDER, ProviderAuthStore


DEFAULT_SOURCES = [
    MYFREEJUICES_PROVIDER,
    "JBSouMusicClient",
    "MyFreeMP3MusicClient",
    "MP3JuiceMusicClient",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value


def slug_key(title: str, artists: str, album: str) -> str:
    return " | ".join([normalize_text(title), normalize_text(artists), normalize_text(album)])


@dataclass
class CandidateTrack:
    title: str
    artists: str
    album: str
    duration: str
    source: str
    source_id: str
    download_url: str
    cover_url: str
    lyric: str
    downloadable_now: bool
    ext: str
    download_headers_json: str = ""


class Repository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    artists TEXT NOT NULL,
                    album TEXT NOT NULL DEFAULT '',
                    language TEXT NOT NULL DEFAULT '',
                    duration TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS track_variants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    provider_track_id TEXT NOT NULL,
                    download_url TEXT NOT NULL DEFAULT '',
                    cover_url TEXT NOT NULL DEFAULT '',
                    lyric TEXT NOT NULL DEFAULT '',
                    ext TEXT NOT NULL DEFAULT '',
                    downloadable_now INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(provider, provider_track_id),
                    FOREIGN KEY(track_id) REFERENCES tracks(id)
                );
                CREATE TABLE IF NOT EXISTS collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    is_system INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_id INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    added_at TEXT NOT NULL,
                    UNIQUE(collection_id, track_id),
                    FOREIGN KEY(collection_id) REFERENCES collections(id),
                    FOREIGN KEY(track_id) REFERENCES tracks(id)
                );
                CREATE TABLE IF NOT EXISTS profile_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_name TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    search_type TEXT NOT NULL,
                    result_count INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recommendation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_key TEXT NOT NULL UNIQUE,
                    collection_name TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    search_plan_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recommendation_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    rank_index INTEGER NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    downloadable_now INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES recommendation_runs(id),
                    FOREIGN KEY(track_id) REFERENCES tracks(id)
                );
                CREATE TABLE IF NOT EXISTS recommendation_candidate_sets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_name TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    search_plan_json TEXT NOT NULL,
                    candidates_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS playlist_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    added_at TEXT NOT NULL,
                    UNIQUE(playlist_id, track_id),
                    FOREIGN KEY(playlist_id) REFERENCES playlists(id),
                    FOREIGN KEY(track_id) REFERENCES tracks(id)
                );
                CREATE TABLE IF NOT EXISTS download_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_kind TEXT NOT NULL,
                    target_ref TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS download_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER,
                    track_id INTEGER,
                    path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES download_jobs(id),
                    FOREIGN KEY(track_id) REFERENCES tracks(id)
                );
                CREATE TABLE IF NOT EXISTS push_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS push_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_health (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    probe_query TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    result_count INTEGER NOT NULL DEFAULT 0,
                    downloadable_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )
            self.ensure_system_collection(conn, "likes")
            conn.commit()

    def ensure_system_collection(self, conn: sqlite3.Connection, name: str) -> None:
        now = utc_now()
        conn.execute(
            """
            INSERT INTO collections(name, kind, description, is_system, created_at, updated_at)
            VALUES(?, 'likes', 'Default local likes collection', 1, ?, ?)
            ON CONFLICT(name) DO NOTHING
            """,
            (name, now, now),
        )

    def save_candidates(self, candidates: Iterable[CandidateTrack]) -> list[dict[str, Any]]:
        saved: list[dict[str, Any]] = []
        now = utc_now()
        with self.connect() as conn:
            for item in candidates:
                key = slug_key(item.title, item.artists, item.album)
                conn.execute(
                    """
                    INSERT INTO tracks(dedupe_key, title, artists, album, language, duration, metadata_json, created_at, updated_at)
                    VALUES(?, ?, ?, ?, '', ?, '{}', ?, ?)
                    ON CONFLICT(dedupe_key) DO UPDATE SET
                        duration=excluded.duration,
                        updated_at=excluded.updated_at
                    """,
                    (key, item.title, item.artists, item.album, item.duration, now, now),
                )
                row = conn.execute("SELECT * FROM tracks WHERE dedupe_key = ?", (key,)).fetchone()
                assert row is not None
                conn.execute(
                    """
                    INSERT INTO track_variants(
                        track_id, provider, provider_track_id, download_url, cover_url, lyric, ext,
                        downloadable_now, raw_json, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, provider_track_id) DO UPDATE SET
                        download_url=excluded.download_url,
                        cover_url=excluded.cover_url,
                        lyric=excluded.lyric,
                        ext=excluded.ext,
                        downloadable_now=excluded.downloadable_now,
                        raw_json=excluded.raw_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        row["id"],
                        item.source,
                        item.source_id,
                        item.download_url,
                        item.cover_url,
                        item.lyric,
                        item.ext,
                        1 if item.downloadable_now else 0,
                        json.dumps(asdict(item), ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                saved.append(dict(row))
            conn.commit()
        return saved

    def log_search(self, query: str, search_type: str, payload: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO search_history(query, search_type, result_count, payload_json, created_at) VALUES(?, ?, ?, ?, ?)",
                (query, search_type, len(payload), json.dumps(payload, ensure_ascii=False), utc_now()),
            )
            conn.commit()

    def get_collection_tracks(self, collection_name: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT t.*
                FROM collections c
                JOIN collection_items ci ON ci.collection_id = c.id
                JOIN tracks t ON t.id = ci.track_id
                WHERE c.name = ?
                ORDER BY ci.added_at DESC
                """,
                (collection_name,),
            ).fetchall()

    def create_playlist(self, name: str, description: str = "") -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO playlists(name, description, created_at, updated_at) VALUES(?, ?, ?, ?) ON CONFLICT(name) DO NOTHING",
                (name, description, now, now),
            )
            conn.commit()

    def rename_playlist(self, old_name: str, new_name: str) -> None:
        with self.connect() as conn:
            updated = conn.execute(
                "UPDATE playlists SET name = ?, updated_at = ? WHERE name = ?",
                (new_name, utc_now(), old_name),
            ).rowcount
            if updated == 0:
                raise SystemExit(f"Playlist not found: {old_name}")
            conn.commit()

    def delete_playlist(self, name: str) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM playlists WHERE name = ?", (name,)).fetchone()
            if row is None:
                raise SystemExit(f"Playlist not found: {name}")
            conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (row["id"],))
            conn.execute("DELETE FROM playlists WHERE id = ?", (row["id"],))
            conn.commit()

    def list_playlists(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.name, p.description, COUNT(pi.id) AS track_count
                FROM playlists p
                LEFT JOIN playlist_items pi ON pi.playlist_id = p.id
                GROUP BY p.id
                ORDER BY p.name
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def show_playlist(self, name: str) -> dict[str, Any]:
        with self.connect() as conn:
            meta = conn.execute(
                "SELECT id, name, description, created_at, updated_at FROM playlists WHERE name = ?",
                (name,),
            ).fetchone()
            if meta is None:
                raise SystemExit(f"Playlist not found: {name}")
            items = conn.execute(
                """
                SELECT t.id, t.title, t.artists, t.album, t.duration, pi.added_at
                FROM playlist_items pi
                JOIN tracks t ON t.id = pi.track_id
                WHERE pi.playlist_id = ?
                ORDER BY pi.added_at DESC
                """,
                (meta["id"],),
            ).fetchall()
            return {"playlist": dict(meta), "items": [dict(row) for row in items]}

    def add_track_to_playlist(self, playlist_name: str, track_id: int) -> None:
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM playlists WHERE name = ?", (playlist_name,)).fetchone()
            if row is None:
                raise SystemExit(f"Playlist not found: {playlist_name}")
            conn.execute(
                "INSERT INTO playlist_items(playlist_id, track_id, added_at) VALUES(?, ?, ?) ON CONFLICT(playlist_id, track_id) DO NOTHING",
                (row["id"], track_id, now),
            )
            conn.commit()

    def remove_track_from_playlist(self, playlist_name: str, track_id: int) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM playlists WHERE name = ?", (playlist_name,)).fetchone()
            if row is None:
                raise SystemExit(f"Playlist not found: {playlist_name}")
            conn.execute(
                "DELETE FROM playlist_items WHERE playlist_id = ? AND track_id = ?",
                (row["id"], track_id),
            )
            conn.commit()

    def create_collection(self, name: str, kind: str = "custom", description: str = "") -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO collections(name, kind, description, is_system, created_at, updated_at)
                VALUES(?, ?, ?, 0, ?, ?)
                ON CONFLICT(name) DO NOTHING
                """,
                (name, kind, description, now, now),
            )
            conn.commit()

    def list_collections(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.name, c.kind, c.description, c.is_system, COUNT(ci.id) AS track_count
                FROM collections c
                LEFT JOIN collection_items ci ON ci.collection_id = c.id
                GROUP BY c.id
                ORDER BY c.is_system DESC, c.name
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def show_collection(self, name: str) -> dict[str, Any]:
        with self.connect() as conn:
            meta = conn.execute(
                "SELECT id, name, kind, description, is_system, created_at, updated_at FROM collections WHERE name = ?",
                (name,),
            ).fetchone()
            if meta is None:
                raise SystemExit(f"Collection not found: {name}")
            items = conn.execute(
                """
                SELECT t.id, t.title, t.artists, t.album, t.duration, ci.added_at
                FROM collection_items ci
                JOIN tracks t ON t.id = ci.track_id
                WHERE ci.collection_id = ?
                ORDER BY ci.added_at DESC
                """,
                (meta["id"],),
            ).fetchall()
            return {"collection": dict(meta), "items": [dict(row) for row in items]}

    def add_track_to_collection(self, collection_name: str, track_id: int) -> None:
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM collections WHERE name = ?", (collection_name,)).fetchone()
            if row is None:
                raise SystemExit(f"Collection not found: {collection_name}")
            conn.execute(
                "INSERT INTO collection_items(collection_id, track_id, weight, added_at) VALUES(?, ?, 1.0, ?) ON CONFLICT(collection_id, track_id) DO NOTHING",
                (row["id"], track_id, now),
            )
            conn.commit()

    def remove_track_from_collection(self, collection_name: str, track_id: int) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM collections WHERE name = ?", (collection_name,)).fetchone()
            if row is None:
                raise SystemExit(f"Collection not found: {collection_name}")
            conn.execute(
                "DELETE FROM collection_items WHERE collection_id = ? AND track_id = ?",
                (row["id"], track_id),
            )
            conn.commit()

    def merge_collections(self, source_names: list[str], target_name: str) -> dict[str, Any]:
        self.create_collection(target_name, kind="merged", description="Merged collection")
        with self.connect() as conn:
            target = conn.execute("SELECT id FROM collections WHERE name = ?", (target_name,)).fetchone()
            assert target is not None
            inserted = 0
            for source_name in source_names:
                source = conn.execute("SELECT id FROM collections WHERE name = ?", (source_name,)).fetchone()
                if source is None:
                    continue
                rows = conn.execute(
                    "SELECT track_id FROM collection_items WHERE collection_id = ?",
                    (source["id"],),
                ).fetchall()
                for row in rows:
                    inserted += conn.execute(
                        """
                        INSERT INTO collection_items(collection_id, track_id, weight, added_at)
                        VALUES(?, ?, 1.0, ?)
                        ON CONFLICT(collection_id, track_id) DO NOTHING
                        """,
                        (target["id"], row["track_id"], utc_now()),
                    ).rowcount
            conn.commit()
            return {"collection": target_name, "added_count": inserted, "sources": source_names}

    def get_track(self, track_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    def get_track_variants(self, track_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM track_variants WHERE track_id = ? ORDER BY downloadable_now DESC, provider",
                (track_id,),
            ).fetchall()

    def get_variant(self, variant_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM track_variants WHERE id = ?", (variant_id,)).fetchone()

    def search_rows(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, query, search_type, result_count, created_at
                FROM search_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def record_provider_health(
        self,
        provider: str,
        probe_query: str,
        status: str,
        latency_ms: int,
        result_count: int,
        downloadable_count: int,
        error: str,
        payload: dict[str, Any],
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_health(
                    provider, probe_query, status, latency_ms, result_count,
                    downloadable_count, error, payload_json, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    probe_query,
                    status,
                    latency_ms,
                    result_count,
                    downloadable_count,
                    error,
                    json.dumps(payload, ensure_ascii=False),
                    utc_now(),
                ),
            )
            conn.commit()

    def latest_provider_health(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT ph.*
                FROM provider_health ph
                JOIN (
                    SELECT provider, MAX(id) AS max_id
                    FROM provider_health
                    GROUP BY provider
                ) latest ON latest.max_id = ph.id
                ORDER BY ph.provider
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [
                (
                    lambda data: (
                        data.pop("payload_json"),
                        {**data, "payload": json.loads(row["payload_json"])}
                    )[1]
                )(dict(row))
                for row in rows
            ]

    def recommendation_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, run_key, collection_name, created_at
                FROM recommendation_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_recommendation_run(self, run_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, run_key, collection_name, profile_json, search_plan_json, summary_json, created_at
                FROM recommendation_runs WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise SystemExit(f"Recommendation run not found: {run_id}")
            data = dict(row)
            data["profile"] = json.loads(data.pop("profile_json"))
            data["search_plan"] = json.loads(data.pop("search_plan_json"))
            data["summary"] = json.loads(data.pop("summary_json"))
            data["items"] = self.recommendation_items(run_id)
            return data

    def save_profile_snapshot(self, collection_name: str, profile: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO profile_snapshots(collection_name, profile_json, created_at) VALUES(?, ?, ?)",
                (collection_name, json.dumps(profile, ensure_ascii=False), utc_now()),
            )
            conn.commit()

    def save_recommendation_run(
        self,
        run_key: str,
        collection_name: str,
        profile: dict[str, Any],
        search_plan: dict[str, Any],
        summary: dict[str, Any],
        track_ids: list[int],
    ) -> int:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO recommendation_runs(run_key, collection_name, profile_json, search_plan_json, summary_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    run_key,
                    collection_name,
                    json.dumps(profile, ensure_ascii=False),
                    json.dumps(search_plan, ensure_ascii=False),
                    json.dumps(summary, ensure_ascii=False),
                    now,
                ),
            )
            run_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            for idx, track_id in enumerate(track_ids):
                variants = self.get_track_variants(track_id)
                downloadable_now = 1 if any(v["downloadable_now"] for v in variants) else 0
                conn.execute(
                    """
                    INSERT INTO recommendation_items(run_id, track_id, rank_index, reason, downloadable_now, created_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, track_id, idx + 1, summary.get("reason", ""), downloadable_now, now),
                )
            conn.commit()
            return run_id

    def latest_recommendation_for_day(self, collection_name: str, day_key: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM recommendation_runs
                WHERE collection_name = ? AND run_key = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (collection_name, day_key),
            ).fetchone()

    def recommendation_items(self, run_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT t.id, t.title, t.artists, t.album, t.duration, ri.rank_index, ri.downloadable_now
                FROM recommendation_items ri
                JOIN tracks t ON t.id = ri.track_id
                WHERE ri.run_id = ?
                ORDER BY ri.rank_index
                """,
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def push(self, kind: str, payload: dict[str, Any]) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO push_queue(kind, payload_json, created_at) VALUES(?, ?, ?)",
                (kind, json.dumps(payload, ensure_ascii=False), now),
            )
            conn.execute(
                "INSERT INTO push_history(kind, payload_json, created_at) VALUES(?, ?, ?)",
                (kind, json.dumps(payload, ensure_ascii=False), now),
            )
            conn.commit()

    def latest_push(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM push_history ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            return {
                "id": row["id"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }

    def list_pushes(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, kind, payload_json, created_at FROM push_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "payload": json.loads(row["payload_json"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def show_push(self, push_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, kind, payload_json, created_at FROM push_history WHERE id = ?",
                (push_id,),
            ).fetchone()
            if row is None:
                raise SystemExit(f"Push not found: {push_id}")
            return {
                "id": row["id"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }

    def mark_push_consumed(self, push_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE push_queue SET consumed_at = ? WHERE id = ?",
                (utc_now(), push_id),
            )
            conn.commit()

    def create_download_job(self, target_kind: str, target_ref: str, payload: dict[str, Any], status: str = "queued") -> int:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO download_jobs(target_kind, target_ref, status, payload_json, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?)",
                (target_kind, target_ref, status, json.dumps(payload, ensure_ascii=False), now, now),
            )
            job_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.commit()
            return job_id

    def get_download_job(self, job_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM download_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise SystemExit(f"Download job not found: {job_id}")
            data = dict(row)
            data["payload"] = json.loads(data.pop("payload_json"))
            data["files"] = [
                dict(file_row)
                for file_row in conn.execute(
                    "SELECT id, track_id, path, status, detail_json, created_at FROM download_files WHERE job_id = ? ORDER BY id",
                    (job_id,),
                ).fetchall()
            ]
            return data

    def list_download_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, target_kind, target_ref, status, payload_json, created_at, updated_at FROM download_jobs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {
                    **dict(row),
                    "payload": json.loads(row["payload_json"]),
                }
                for row in rows
            ]

    def list_download_files(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, job_id, track_id, path, status, detail_json, created_at FROM download_files ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {
                    **dict(row),
                    "detail": json.loads(row["detail_json"]),
                }
                for row in rows
            ]

    def mark_download_job(self, job_id: int, status: str, payload: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE download_jobs SET status = ?, payload_json = ?, updated_at = ? WHERE id = ?",
                (status, json.dumps(payload or {}, ensure_ascii=False), utc_now(), job_id),
            )
            conn.commit()

    def log_download_file(self, job_id: int | None, track_id: int | None, path: str, status: str, detail: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO download_files(job_id, track_id, path, status, detail_json, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (job_id, track_id, path, status, json.dumps(detail, ensure_ascii=False), utc_now()),
            )
            conn.commit()

    def save_candidate_set(
        self,
        collection_name: str,
        profile: dict[str, Any],
        search_plan: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO recommendation_candidate_sets(collection_name, profile_json, search_plan_json, candidates_json, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    collection_name,
                    json.dumps(profile, ensure_ascii=False),
                    json.dumps(search_plan, ensure_ascii=False),
                    json.dumps(candidates, ensure_ascii=False),
                    utc_now(),
                ),
            )
            candidate_set_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.commit()
            return candidate_set_id

    def get_candidate_set(self, candidate_set_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, collection_name, profile_json, search_plan_json, candidates_json, created_at
                FROM recommendation_candidate_sets
                WHERE id = ?
                """,
                (candidate_set_id,),
            ).fetchone()
            if row is None:
                raise SystemExit(f"Recommendation candidate set not found: {candidate_set_id}")
            return {
                "id": row["id"],
                "collection_name": row["collection_name"],
                "profile": json.loads(row["profile_json"]),
                "search_plan": json.loads(row["search_plan_json"]),
                "candidates": json.loads(row["candidates_json"]),
                "created_at": row["created_at"],
            }


class MusicdlAdapter:
    def __init__(self, work_dir: Path, sources: list[str], auth_store: ProviderAuthStore | None = None) -> None:
        self.work_dir = work_dir
        self.sources = sources
        self.auth_store = auth_store
        self.backend = EmbeddedMusicBackend(sources, auth_store=auth_store)

    def fallback_search(self, query: str, limit: int = 10) -> list[CandidateTrack]:
        url = (
            "https://itunes.apple.com/search?"
            + urllib.parse.urlencode({"term": query, "entity": "song", "limit": limit})
        )
        with urllib.request.urlopen(url, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        results: list[CandidateTrack] = []
        for item in payload.get("results", []):
            results.append(
                CandidateTrack(
                    title=item.get("trackName", "") or "",
                    artists=item.get("artistName", "") or "",
                    album=item.get("collectionName", "") or "",
                    duration="",
                    source="ITunesFallback",
                    source_id=str(item.get("trackId", "") or ""),
                    download_url="",
                    cover_url=item.get("artworkUrl100", "") or "",
                    lyric="",
                    downloadable_now=False,
                    ext="m4a",
                    download_headers_json="",
                )
            )
        return results

    def search(self, query: str) -> list[CandidateTrack]:
        def run_embedded() -> list[CandidateTrack]:
            raw = self.backend.search(query, limit=12)
            return [CandidateTrack(**item) for item in raw]

        try:
            pool = ThreadPoolExecutor(max_workers=1)
            future = pool.submit(run_embedded)
            try:
                return future.result(timeout=30)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        except (FuturesTimeoutError, Exception):
            return self.fallback_search(query)

    def download_variant(self, variant: sqlite3.Row, destination: Path) -> dict[str, Any]:
        url = variant["download_url"]
        if not url:
            raise SystemExit("Selected variant has no download URL")
        ext = variant["ext"] or "mp3"
        filename = f"{variant['provider']}-{variant['provider_track_id']}.{ext}"
        payload = {}
        try:
            payload = json.loads(variant["raw_json"])
        except Exception:
            payload = {}
        result = self.backend.download(url, destination, filename, payload.get("download_headers_json", ""))
        return {"path": result["path"], "provider": variant["provider"], "status": result["status"]}

    def list_channels(self) -> dict[str, Any]:
        return self.backend.list_channels()

    def probe_provider(self, source: str, query: str = "Jay Chou") -> dict[str, Any]:
        return self.backend.probe_provider(source, query)

    def search_provider(self, source: str, query: str, limit: int = 12, allow_fallback: bool = False) -> list[CandidateTrack]:
        raw = self.backend.search_provider(source, query, limit=limit, allow_fallback=allow_fallback)
        return [CandidateTrack(**item) for item in raw]


class ProfileAnalyzer:
    def analyze(self, rows: list[sqlite3.Row]) -> dict[str, Any]:
        artists: dict[str, int] = {}
        albums: dict[str, int] = {}
        for row in rows:
            artist_key = row["artists"] or "Unknown"
            album_key = row["album"] or "Unknown"
            artists[artist_key] = artists.get(artist_key, 0) + 1
            albums[album_key] = albums.get(album_key, 0) + 1
        top_artists = sorted(artists.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        top_albums = sorted(albums.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        return {
            "track_count": len(rows),
            "top_artists": top_artists,
            "top_albums": top_albums,
            "language_hint": "unknown",
            "mood_hint": "derived_from_local_collection",
            "updated_at": utc_now(),
        }


class RecommendationPlanner:
    def plan(self, profile: dict[str, Any], collection_name: str, limit: int) -> dict[str, Any]:
        seeds = [artist for artist, _ in profile.get("top_artists", [])[:3]]
        if not seeds:
            seeds = [collection_name]
        queries = seeds[: max(2, min(4, len(seeds)))]
        if len(queries) < 2:
            queries.append(f"{queries[0]} 推荐")
        return {
            "collection": collection_name,
            "queries": queries[:4],
            "search_modes": ["song", "mixed"],
            "limit": limit,
            "strategy": "content_profile_plus_multi_query",
        }


class RecommendationEngine:
    def __init__(self, repo: Repository, adapter: MusicdlAdapter) -> None:
        self.repo = repo
        self.adapter = adapter

    def recommend(self, collection_name: str, limit: int) -> dict[str, Any]:
        rows = self.repo.get_collection_tracks(collection_name)
        analyzer = ProfileAnalyzer()
        profile = analyzer.analyze(rows)
        self.repo.save_profile_snapshot(collection_name, profile)
        plan = RecommendationPlanner().plan(profile, collection_name, limit)
        seen_keys = {row["dedupe_key"] for row in rows}
        merged: dict[str, CandidateTrack] = {}
        for query in plan["queries"]:
            for candidate in self.adapter.search(query):
                key = slug_key(candidate.title, candidate.artists, candidate.album)
                if key in seen_keys or key in merged:
                    continue
                merged[key] = candidate
                if len(merged) >= limit:
                    break
            if len(merged) >= limit:
                break
        saved = self.repo.save_candidates(merged.values())
        summary = {
            "reason": "multi_query_profile_recommendation",
            "candidate_count": len(saved),
            "queries": plan["queries"],
        }
        run_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        run_id = self.repo.save_recommendation_run(
            run_key,
            collection_name,
            profile,
            plan,
            summary,
            [row["id"] for row in saved],
        )
        items = self.repo.recommendation_items(run_id)
        payload = {"run_id": run_id, "collection": collection_name, "items": items, "summary": summary}
        self.repo.push("recommendation_batch", payload)
        return payload


class DownloadQueueWorker:
    def __init__(self, repo: Repository, adapter: MusicdlAdapter, download_dir: Path) -> None:
        self.repo = repo
        self.adapter = adapter
        self.download_dir = download_dir
        self.jobs: queue.Queue[tuple[int, list[int]]] = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def enqueue(self, job_id: int, track_ids: list[int]) -> None:
        self.jobs.put((job_id, track_ids))

    def _run(self) -> None:
        while True:
            job_id, track_ids = self.jobs.get()
            try:
                job = self.repo.get_download_job(job_id)
                if job["status"] == "cancelled":
                    continue
                for track_id in track_ids:
                    track = self.repo.get_track(track_id)
                    if track is None:
                        continue
                    variants = self.repo.get_track_variants(track_id)
                    if not variants:
                        self.repo.log_download_file(job_id, track_id, "", "missing_variant", {})
                        continue
                    chosen = next((v for v in variants if v["downloadable_now"] and v["download_url"]), None)
                    if chosen is None:
                        self.repo.log_download_file(job_id, track_id, "", "missing_variant", {"reason": "no_downloadable_variant"})
                        continue
                    result = self.adapter.download_variant(chosen, self.download_dir)
                    self.repo.log_download_file(job_id, track_id, result["path"], result["status"], result)
                self.repo.mark_download_job(job_id, "completed", {"track_count": len(track_ids)})
            except Exception as exc:  # noqa: BLE001
                self.repo.mark_download_job(job_id, "failed", {"error": str(exc)})


def build_paths() -> tuple[Path, Path, Path, Path]:
    base_dir = Path(__file__).resolve().parent.parent
    state_dir = base_dir / "state"
    db_path = Path(os.environ.get("MUSIC_ORCH_DB", state_dir / "music.db"))
    download_dir = Path(os.environ.get("MUSIC_ORCH_DOWNLOADS", state_dir / "downloads"))
    provider_work_dir = state_dir / "provider-work"
    auth_store_path = state_dir / "provider_auth.json"
    return db_path, download_dir, provider_work_dir, auth_store_path


def build_sources() -> list[str]:
    raw = os.environ.get("MUSIC_ORCH_SOURCES", ",".join(DEFAULT_SOURCES))
    return [item.strip() for item in raw.split(",") if item.strip()]


def redact_secret(value: str, visible: int = 6) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= visible * 2:
        return "*" * len(text)
    return f"{text[:visible]}...{text[-visible:]}"


def auth_summary(provider: str, auth_store: ProviderAuthStore) -> dict[str, Any]:
    record = auth_store.get(provider)
    clearance = str(record.get("cf_clearance", "") or "").strip()
    return {
        "provider": provider,
        "configured": bool(clearance),
        "state_file": str(auth_store.path),
        "updated_at": record.get("updated_at", ""),
        "music_lang": record.get("music_lang", "en") or "en",
        "user_agent_present": bool(str(record.get("user_agent", "") or "").strip()),
        "cf_clearance_preview": redact_secret(clearance),
        "source": record.get("source", "refresh" if clearance else ""),
    }


def detect_visible_browser_runtime() -> dict[str, Any]:
    display = (os.environ.get("DISPLAY") or "").strip()
    wayland = (os.environ.get("WAYLAND_DISPLAY") or "").strip()
    if not (display or wayland):
        return {
            "can_refresh": False,
            "reason": "gui_unavailable",
            "message": "gui_unavailable: Visible browser refresh requires DISPLAY or WAYLAND_DISPLAY. Use 'channel-auth set' on headless systems.",
            "xvfb_run_present": bool(shutil.which("xvfb-run")),
        }
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return {
            "can_refresh": False,
            "reason": "playwright_missing",
            "message": "playwright_missing: Install Playwright with 'pip install playwright' and 'python3 -m playwright install chromium'.",
            "xvfb_run_present": bool(shutil.which("xvfb-run")),
        }
    return {
        "can_refresh": True,
        "reason": "",
        "message": "",
        "xvfb_run_present": bool(shutil.which("xvfb-run")),
    }


def candidate_to_dict(candidate: CandidateTrack) -> dict[str, Any]:
    return {
        "title": candidate.title,
        "artists": candidate.artists,
        "album": candidate.album,
        "duration": candidate.duration,
        "source": candidate.source,
        "source_id": candidate.source_id,
        "downloadable_now": candidate.downloadable_now,
        "cover_url": candidate.cover_url,
        "ext": candidate.ext,
        "has_download_url": bool(candidate.download_url),
    }


def variant_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "provider": row["provider"],
        "provider_track_id": row["provider_track_id"],
        "downloadable_now": bool(row["downloadable_now"]),
        "download_url_present": bool(row["download_url"]),
        "cover_url_present": bool(row["cover_url"]),
        "lyric_present": bool(row["lyric"]),
        "ext": row["ext"],
    }


def classify_provider_health(row: dict[str, Any]) -> dict[str, Any]:
    latency_ms = int(row.get("latency_ms") or 0)
    status = row.get("status", "")
    if status == "ok":
        if latency_ms < 4000:
            severity = "healthy"
        elif latency_ms < 10000:
            severity = "degraded"
        else:
            severity = "slow"
    elif status == "empty":
        severity = "degraded"
    elif status:
        severity = "unhealthy"
    else:
        severity = "unknown"
    return {**row, "severity": severity}


def severity_rank(value: str) -> int:
    order = {
        "healthy": 0,
        "degraded": 1,
        "slow": 2,
        "unhealthy": 3,
        "unknown": 4,
    }
    return order.get(value, 4)


def candidate_match_score(track: sqlite3.Row | dict[str, Any], candidate: CandidateTrack) -> tuple[int, int, int]:
    track_title = normalize_text(track["title"])
    track_artists = normalize_text(track["artists"])
    candidate_title = normalize_text(candidate.title)
    candidate_artists = normalize_text(candidate.artists)
    title_score = 0
    if track_title and candidate_title:
        if track_title == candidate_title:
            title_score = 4
        elif track_title in candidate_title or candidate_title in track_title:
            title_score = 3
        elif any(token and token in candidate_title for token in track_title.split(" ")):
            title_score = 2
    artist_score = 0
    if track_artists and candidate_artists:
        if track_artists == candidate_artists:
            artist_score = 3
        elif any(token and token in candidate_artists for token in track_artists.split(" ")):
            artist_score = 1
    downloadable_score = 1 if candidate.downloadable_now and candidate.download_url else 0
    return (title_score, artist_score, downloadable_score)


def listen_item_score(query: str, item: dict[str, Any]) -> tuple[int, int, int]:
    normalized_query = normalize_text(query)
    title = normalize_text(item.get("title", ""))
    artists = normalize_text(item.get("artists", ""))
    title_score = 0
    if normalized_query and title:
        if normalized_query == title:
            title_score = 5
        elif normalized_query in title or title in normalized_query:
            title_score = 4
        elif any(token and token in title for token in normalized_query.split(" ")):
            title_score = 2
    artist_score = 0
    if normalized_query and artists:
        if artists in normalized_query:
            artist_score = 2
        elif any(token and token in artists for token in normalized_query.split(" ")):
            artist_score = 1
    downloadable_score = 1 if any(v.get("downloadable_now") and v.get("download_url_present") for v in item.get("variants", [])) else 0
    return (title_score, artist_score, downloadable_score)


def load_provider_health(
    repo: Repository,
    adapter: MusicdlAdapter,
    providers: list[str],
    refresh: bool,
    probe_query: str,
) -> list[dict[str, Any]]:
    latest = {row["provider"]: classify_provider_health(row) for row in repo.latest_provider_health(max(50, len(providers) + 5))}
    providers_to_probe = list(providers) if refresh else [provider for provider in providers if provider not in latest]
    for provider in providers_to_probe:
        probe = adapter.probe_provider(provider, probe_query)
        repo.record_provider_health(
            provider=probe["provider"],
            probe_query=probe["probe_query"],
            status=probe["status"],
            latency_ms=probe["latency_ms"],
            result_count=probe["result_count"],
            downloadable_count=probe["downloadable_count"],
            error=probe["error"],
            payload=probe,
        )
        latest[provider] = classify_provider_health({**probe, "payload": probe, "created_at": utc_now()})
    result: list[dict[str, Any]] = []
    for provider in providers:
        result.append(
            latest.get(
                provider,
                classify_provider_health(
                    {
                        "provider": provider,
                        "status": "",
                        "latency_ms": 0,
                        "result_count": 0,
                        "downloadable_count": 0,
                        "error": "",
                        "payload": {},
                    }
                ),
            )
        )
    return result


def choose_download_variant(
    repo: Repository,
    adapter: MusicdlAdapter,
    track_id: int,
    provider: str | None = None,
    refresh_health: bool = False,
) -> dict[str, Any]:
    track = repo.get_track(track_id)
    if track is None:
        raise SystemExit(f"Track not found: {track_id}")
    variants = repo.get_track_variants(track_id)
    if not variants:
        raise SystemExit("No variants found for track")

    channels = cmd_channels(adapter)
    active_sources = list(adapter.sources)
    if provider and provider not in active_sources:
        raise SystemExit(f"Provider not active: {provider}")
    fallback_only = set(channels.get("fallback_search_only", []))
    probe_query = " ".join(part for part in [track["artists"], track["title"]] if part).strip() or "Jay Chou"
    health = load_provider_health(repo, adapter, active_sources, refresh_health, probe_query)
    health_by_provider = {row["provider"]: row for row in health}
    provider_priority = {name: idx for idx, name in enumerate(DEFAULT_SOURCES)}

    eligible_variants = [variant for variant in variants if variant["provider"] in active_sources and variant["provider"] not in fallback_only]
    downloadable_variants = [variant for variant in eligible_variants if bool(variant["downloadable_now"]) and bool(variant["download_url"])]

    def sort_key(variant: sqlite3.Row) -> tuple[int, int, int, int, int]:
        health_row = health_by_provider.get(variant["provider"], {})
        return (
            severity_rank(health_row.get("severity", "unknown")),
            int(health_row.get("latency_ms") or 999999),
            -int(health_row.get("downloadable_count") or 0),
            -int(health_row.get("result_count") or 0),
            provider_priority.get(variant["provider"], len(provider_priority)),
        )

    chosen: sqlite3.Row | None = None
    decision_reason = ""
    failure_reason = ""

    if provider:
        chosen = next((variant for variant in variants if variant["provider"] == provider), None)
        if chosen is None:
            failure_reason = "provider_not_available_for_track"
        elif chosen["provider"] in fallback_only:
            failure_reason = "provider_is_search_only"
            chosen = None
        elif not chosen["downloadable_now"] or not chosen["download_url"]:
            failure_reason = "provider_variant_not_downloadable"
            chosen = None
        else:
            decision_reason = "user_selected_provider"
    else:
        if downloadable_variants:
            chosen = sorted(downloadable_variants, key=sort_key)[0]
            decision_reason = "best_downloadable_variant_by_health_and_provider_priority"
        elif not eligible_variants and any(variant["provider"] in fallback_only for variant in variants):
            fresh_candidates: list[CandidateTrack] = []
            search_queries = [
                " ".join(part for part in [track["artists"], track["title"]] if part).strip(),
                track["title"],
            ]
            for query in search_queries:
                if not query:
                    continue
                try:
                    fresh_candidates.extend(adapter.search(query))
                except Exception:
                    continue
            fresh_candidates = [
                candidate
                for candidate in fresh_candidates
                if candidate.source in active_sources and candidate.source not in fallback_only
            ]
            fresh_candidates = sorted(
                fresh_candidates,
                key=lambda candidate: candidate_match_score(track, candidate),
                reverse=True,
            )
            if fresh_candidates and candidate_match_score(track, fresh_candidates[0])[0] > 0:
                saved_rows = repo.save_candidates(fresh_candidates[:5])
                matched_row = saved_rows[0]
                matched_variants = repo.get_track_variants(matched_row["id"])
                matched_downloadable = [variant for variant in matched_variants if bool(variant["downloadable_now"]) and bool(variant["download_url"])]
                if matched_downloadable:
                    chosen = sorted(matched_downloadable, key=sort_key)[0]
                    decision_reason = "best_downloadable_variant_after_refresh_search"
                else:
                    failure_reason = "no_downloadable_variant"
            else:
                failure_reason = "fallback_only_results"
        else:
            failure_reason = "no_downloadable_variant"

    return {
        "track": dict(track),
        "channels": channels,
        "provider_health": health,
        "checked_providers": active_sources,
        "variant_count": len(variants),
        "variants": [variant_to_dict(variant) for variant in variants],
        "chosen_provider": chosen["provider"] if chosen is not None else provider,
        "chosen_variant": variant_to_dict(chosen) if chosen is not None else None,
        "decision_reason": decision_reason,
        "failure_reason": failure_reason,
    }


def cmd_init(repo: Repository) -> dict[str, Any]:
    repo.init()
    return {"status": "ok", "db": str(repo.db_path)}


def cmd_search_preview(adapter: MusicdlAdapter, query: str, search_type: str, limit: int) -> dict[str, Any]:
    candidates = adapter.search(query)[:limit]
    return {
        "query": query,
        "type": search_type,
        "count": len(candidates),
        "items": [candidate_to_dict(candidate) for candidate in candidates],
    }


def cmd_channel_search_preview(adapter: MusicdlAdapter, provider: str, query: str, limit: int) -> dict[str, Any]:
    candidates = adapter.search_provider(provider, query, limit=limit, allow_fallback=False)
    return {
        "query": query,
        "provider_scope": provider,
        "count": len(candidates),
        "items": [candidate_to_dict(candidate) for candidate in candidates],
    }


def cmd_search(repo: Repository, adapter: MusicdlAdapter, query: str, search_type: str, limit: int) -> dict[str, Any]:
    candidates = adapter.search(query)[:limit]
    saved = repo.save_candidates(candidates)
    repo.log_search(query, search_type, [asdict(c) for c in candidates])
    return {
        "query": query,
        "type": search_type,
        "count": len(saved),
        "items": [
            {
                "track_id": row["id"],
                "title": row["title"],
                "artists": row["artists"],
                "album": row["album"],
                "duration": row["duration"],
            }
            for row in saved
        ],
    }


def cmd_channel_search(repo: Repository, adapter: MusicdlAdapter, provider: str, query: str, limit: int) -> dict[str, Any]:
    try:
        candidates = adapter.search_provider(provider, query, limit=limit, allow_fallback=False)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    saved = repo.save_candidates(candidates)
    repo.log_search(query, f"provider:{provider}", [asdict(c) for c in candidates])
    return {
        "query": query,
        "provider_scope": provider,
        "count": len(saved),
        "items": [
            {
                "track_id": row["id"],
                "title": row["title"],
                "artists": row["artists"],
                "album": row["album"],
                "duration": row["duration"],
            }
            for row in saved
        ],
    }


def cmd_search_variants(repo: Repository, adapter: MusicdlAdapter, query: str, search_type: str, limit: int) -> dict[str, Any]:
    saved = cmd_search(repo, adapter, query, search_type, limit)
    items: list[dict[str, Any]] = []
    for item in saved["items"]:
        variants = repo.get_track_variants(item["track_id"])
        items.append({**item, "variants": [variant_to_dict(v) for v in variants]})
    return {
        "query": saved["query"],
        "type": saved["type"],
        "count": len(items),
        "items": items,
    }


def cmd_channel_search_variants(repo: Repository, adapter: MusicdlAdapter, provider: str, query: str, limit: int) -> dict[str, Any]:
    saved = cmd_channel_search(repo, adapter, provider, query, limit)
    items: list[dict[str, Any]] = []
    for item in saved["items"]:
        variants = [v for v in repo.get_track_variants(item["track_id"]) if v["provider"] == provider]
        items.append({**item, "variants": [variant_to_dict(v) for v in variants]})
    return {
        "query": saved["query"],
        "provider_scope": provider,
        "count": len(items),
        "items": items,
    }


def cmd_analyze(repo: Repository, collection: str) -> dict[str, Any]:
    rows = repo.get_collection_tracks(collection)
    profile = ProfileAnalyzer().analyze(rows)
    repo.save_profile_snapshot(collection, profile)
    return {"collection": collection, "profile": profile}


def build_recommendation_candidate_set(
    repo: Repository,
    adapter: MusicdlAdapter,
    collection: str,
    limit: int,
) -> dict[str, Any]:
    rows = repo.get_collection_tracks(collection)
    analyzer = ProfileAnalyzer()
    profile = analyzer.analyze(rows)
    repo.save_profile_snapshot(collection, profile)
    plan = RecommendationPlanner().plan(profile, collection, limit)
    seen_keys = {row["dedupe_key"] for row in rows}
    merged: dict[str, CandidateTrack] = {}
    for query in plan["queries"]:
        for candidate in adapter.search(query):
            key = slug_key(candidate.title, candidate.artists, candidate.album)
            if key in seen_keys or key in merged:
                continue
            merged[key] = candidate
            if len(merged) >= limit:
                break
        if len(merged) >= limit:
            break
    saved = repo.save_candidates(merged.values())
    items: list[dict[str, Any]] = []
    for row in saved:
        variants = repo.get_track_variants(row["id"])
        items.append(
            {
                "track_id": row["id"],
                "title": row["title"],
                "artists": row["artists"],
                "album": row["album"],
                "duration": row["duration"],
                "variants": [variant_to_dict(v) for v in variants],
                "downloadable_now": any(v["downloadable_now"] for v in variants),
            }
        )
    candidate_set_id = repo.save_candidate_set(collection, profile, plan, items)
    return {
        "candidate_set_id": candidate_set_id,
        "collection": collection,
        "profile": profile,
        "search_plan": plan,
        "items": items,
    }


def cmd_recommend_plan(repo: Repository, collection: str, limit: int) -> dict[str, Any]:
    rows = repo.get_collection_tracks(collection)
    profile = ProfileAnalyzer().analyze(rows)
    repo.save_profile_snapshot(collection, profile)
    return {
        "collection": collection,
        "profile": profile,
        "search_plan": RecommendationPlanner().plan(profile, collection, limit),
    }


def cmd_recommend_candidates(repo: Repository, adapter: MusicdlAdapter, collection: str, limit: int) -> dict[str, Any]:
    return build_recommendation_candidate_set(repo, adapter, collection, limit)


def cmd_recommend_commit(repo: Repository, candidate_set_id: int) -> dict[str, Any]:
    candidate_set = repo.get_candidate_set(candidate_set_id)
    track_ids = [item["track_id"] for item in candidate_set["candidates"]]
    summary = {
        "reason": "candidate_set_commit",
        "candidate_set_id": candidate_set_id,
        "candidate_count": len(track_ids),
        "queries": candidate_set["search_plan"].get("queries", []),
    }
    run_key = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}:set:{candidate_set_id}"
    run_id = repo.save_recommendation_run(
        run_key,
        candidate_set["collection_name"],
        candidate_set["profile"],
        candidate_set["search_plan"],
        summary,
        track_ids,
    )
    items = repo.recommendation_items(run_id)
    payload = {"run_id": run_id, "collection": candidate_set["collection_name"], "items": items, "summary": summary}
    repo.push("recommendation_batch", payload)
    return payload


def cmd_recommend_show(repo: Repository, run_id: int) -> dict[str, Any]:
    return repo.get_recommendation_run(run_id)


def cmd_recommend(repo: Repository, adapter: MusicdlAdapter, collection: str, limit: int) -> dict[str, Any]:
    candidate_set = build_recommendation_candidate_set(repo, adapter, collection, limit)
    return cmd_recommend_commit(repo, candidate_set["candidate_set_id"])


def cmd_daily(repo: Repository, adapter: MusicdlAdapter, refresh: bool) -> dict[str, Any]:
    day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    latest = None if refresh else repo.latest_recommendation_for_day("likes", day_key)
    if latest is not None:
        items = repo.recommendation_items(latest["id"])
        payload = {"run_id": latest["id"], "collection": "likes", "items": items, "cached": True}
        repo.push("recommendation_batch", payload)
        return payload
    return RecommendationEngine(repo, adapter).recommend("likes", 12)


def cmd_push(repo: Repository) -> dict[str, Any]:
    payload = repo.latest_push()
    if payload is None:
        return {"status": "empty"}
    return payload


def cmd_push_list(repo: Repository, limit: int) -> dict[str, Any]:
    return {"items": repo.list_pushes(limit)}


def cmd_push_show(repo: Repository, push_id: int) -> dict[str, Any]:
    return repo.show_push(push_id)


def cmd_push_mark_consumed(repo: Repository, push_id: int) -> dict[str, Any]:
    repo.mark_push_consumed(push_id)
    return {"status": "consumed", "push_id": push_id}


def cmd_playlist(repo: Repository, action: str, name: str | None, track_id: int | None, description: str | None) -> dict[str, Any]:
    if action == "create":
        assert name is not None
        repo.create_playlist(name, description or "")
        return {"status": "created", "playlist": name}
    if action == "list":
        return {"items": repo.list_playlists()}
    if action == "show":
        assert name is not None
        return repo.show_playlist(name)
    if action == "add":
        assert name is not None and track_id is not None
        repo.add_track_to_playlist(name, track_id)
        return {"status": "added", "playlist": name, "track_id": track_id}
    if action == "remove":
        assert name is not None and track_id is not None
        repo.remove_track_from_playlist(name, track_id)
        return {"status": "removed", "playlist": name, "track_id": track_id}
    if action == "delete":
        assert name is not None
        repo.delete_playlist(name)
        return {"status": "deleted", "playlist": name}
    if action == "rename":
        assert name is not None and description is not None
        repo.rename_playlist(name, description)
        return {"status": "renamed", "playlist": name, "new_name": description}
    raise SystemExit(f"Unsupported playlist action: {action}")


def cmd_collection(
    repo: Repository,
    action: str,
    collection: str | None,
    track_id: int | None,
    description: str | None = None,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    if action == "list":
        return {"items": repo.list_collections()}
    if action == "show":
        assert collection is not None
        return repo.show_collection(collection)
    if action == "create":
        assert collection is not None
        repo.create_collection(collection, description or "custom", "")
        return {"status": "created", "collection": collection}
    if action == "add":
        assert collection is not None and track_id is not None
        repo.add_track_to_collection(collection, track_id)
        return {"status": "added", "collection": collection, "track_id": track_id}
    if action == "remove":
        assert collection is not None and track_id is not None
        repo.remove_track_from_collection(collection, track_id)
        return {"status": "removed", "collection": collection, "track_id": track_id}
    if action == "merge":
        assert collection is not None and sources
        return repo.merge_collections(sources, collection)
    raise SystemExit(f"Unsupported collection action: {action}")


def cmd_track_show(repo: Repository, track_id: int) -> dict[str, Any]:
    track = repo.get_track(track_id)
    if track is None:
        raise SystemExit(f"Track not found: {track_id}")
    return {
        "track": dict(track),
        "variants": [variant_to_dict(v) for v in repo.get_track_variants(track_id)],
    }


def cmd_variants(repo: Repository, track_id: int) -> dict[str, Any]:
    track = repo.get_track(track_id)
    if track is None:
        raise SystemExit(f"Track not found: {track_id}")
    return {
        "track_id": track_id,
        "title": track["title"],
        "artists": track["artists"],
        "album": track["album"],
        "variant_count": len(repo.get_track_variants(track_id)),
        "variants": [variant_to_dict(v) for v in repo.get_track_variants(track_id)],
    }


def cmd_history(repo: Repository, kind: str, limit: int) -> dict[str, Any]:
    if kind == "search":
        return {"items": repo.search_rows(limit)}
    if kind == "recommend":
        return {"items": repo.recommendation_runs(limit)}
    raise SystemExit(f"Unsupported history kind: {kind}")


def cmd_download(
    repo: Repository,
    adapter: MusicdlAdapter,
    worker: DownloadQueueWorker,
    target_kind: str,
    track_id: int | None,
    playlist: str | None,
    album_id: int | None,
    provider: str | None,
) -> dict[str, Any]:
    if target_kind == "track":
        assert track_id is not None
        variants = repo.get_track_variants(track_id)
        if not variants:
            raise SystemExit("No variants found for track")
        if provider:
            chosen = next((v for v in variants if v["provider"] == provider), None)
            if chosen is None:
                raise SystemExit(f"Provider not available for track: {provider}")
            if not chosen["downloadable_now"] or not chosen["download_url"]:
                raise SystemExit(f"Provider has no downloadable variant for track: {provider}")
        else:
            chosen = next((v for v in variants if v["downloadable_now"] and v["download_url"]), None)
            if chosen is None:
                raise SystemExit("No downloadable variant available for track")
        result = adapter.download_variant(chosen, worker.download_dir)
        repo.log_download_file(None, track_id, result["path"], result["status"], result)
        repo.push("download_result", {"target_kind": "track", "track_id": track_id, "result": result})
        return {"status": "completed", "result": result}
    if target_kind == "playlist":
        assert playlist is not None
        with repo.connect() as conn:
            rows = conn.execute(
                """
                SELECT t.id
                FROM playlists p
                JOIN playlist_items pi ON pi.playlist_id = p.id
                JOIN tracks t ON t.id = pi.track_id
                WHERE p.name = ?
                ORDER BY t.id
                """,
                (playlist,),
            ).fetchall()
        track_ids = [int(row["id"]) for row in rows]
        job_id = repo.create_download_job("playlist", playlist, {"track_ids": track_ids})
        worker.enqueue(job_id, track_ids)
        repo.push("download_job", {"job_id": job_id, "target_kind": "playlist", "playlist": playlist})
        return {"status": "queued", "job_id": job_id, "track_count": len(track_ids)}
    if target_kind == "album":
        assert album_id is not None
        with repo.connect() as conn:
            row = conn.execute("SELECT album FROM tracks WHERE id = ?", (album_id,)).fetchone()
            if row is None:
                raise SystemExit(f"Track not found: {album_id}")
            album = row["album"]
            rows = conn.execute("SELECT id FROM tracks WHERE album = ? ORDER BY id", (album,)).fetchall()
        track_ids = [int(row["id"]) for row in rows]
        job_id = repo.create_download_job("album", album, {"track_ids": track_ids})
        worker.enqueue(job_id, track_ids)
        repo.push("download_job", {"job_id": job_id, "target_kind": "album", "album": album})
        return {"status": "queued", "job_id": job_id, "track_count": len(track_ids), "album": album}
    raise SystemExit(f"Unsupported download target: {target_kind}")


def cmd_download_preview(repo: Repository, track_id: int) -> dict[str, Any]:
    track = repo.get_track(track_id)
    if track is None:
        raise SystemExit(f"Track not found: {track_id}")
    variants = repo.get_track_variants(track_id)
    chosen = next((v for v in variants if v["downloadable_now"] and v["download_url"]), variants[0] if variants else None)
    return {
        "track": dict(track),
        "variant_count": len(variants),
        "default_provider": chosen["provider"] if chosen is not None else None,
        "variants": [variant_to_dict(v) for v in variants],
    }


def cmd_download_choose(
    repo: Repository,
    adapter: MusicdlAdapter,
    worker: DownloadQueueWorker,
    track_id: int,
    provider: str | None,
    dry_run: bool,
    refresh_health: bool,
) -> dict[str, Any]:
    decision = choose_download_variant(repo, adapter, track_id, provider, refresh_health)
    chosen_variant = decision["chosen_variant"]
    if chosen_variant is None:
        return {**decision, "status": "unavailable"}
    if dry_run:
        return {**decision, "status": "planned"}
    chosen = repo.get_variant(chosen_variant["id"])
    if chosen is None:
        raise SystemExit(f"Variant not found: {chosen_variant['id']}")
    result = adapter.download_variant(chosen, worker.download_dir)
    repo.log_download_file(None, track_id, result["path"], result["status"], result)
    repo.push(
        "download_result",
        {
            "target_kind": "track",
            "track_id": track_id,
            "provider": decision["chosen_provider"],
            "decision_reason": decision["decision_reason"],
            "result": result,
        },
    )
    return {**decision, "status": "completed", "result": result}


def cmd_download_queue(repo: Repository, limit: int) -> dict[str, Any]:
    return {"items": repo.list_download_jobs(limit)}


def cmd_download_status(repo: Repository, job_id: int) -> dict[str, Any]:
    return repo.get_download_job(job_id)


def cmd_download_files(repo: Repository, limit: int) -> dict[str, Any]:
    return {"items": repo.list_download_files(limit)}


def cmd_download_retry(repo: Repository, worker: DownloadQueueWorker, job_id: int) -> dict[str, Any]:
    job = repo.get_download_job(job_id)
    track_ids = [int(tid) for tid in job["payload"].get("track_ids", [])]
    new_job_id = repo.create_download_job(job["target_kind"], job["target_ref"], {"track_ids": track_ids})
    worker.enqueue(new_job_id, track_ids)
    return {"status": "queued", "retried_from": job_id, "job_id": new_job_id}


def cmd_download_cancel(repo: Repository, job_id: int) -> dict[str, Any]:
    job = repo.get_download_job(job_id)
    if job["status"] != "queued":
        return {"status": "not_cancelled", "job_id": job_id, "reason": "job is not queued"}
    repo.mark_download_job(job_id, "cancelled", job["payload"])
    return {"status": "cancelled", "job_id": job_id}


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_channels(adapter: MusicdlAdapter) -> dict[str, Any]:
    return adapter.list_channels()


def cmd_channels_health(
    repo: Repository,
    adapter: MusicdlAdapter,
    limit: int,
    refresh: bool = False,
    provider: str | None = None,
) -> dict[str, Any]:
    selected_sources = [provider] if provider else list(adapter.sources)
    if provider and provider not in adapter.sources:
        raise SystemExit(f"Provider not active: {provider}")
    health_rows = load_provider_health(repo, adapter, selected_sources, refresh, "Jay Chou")
    if provider:
        health_rows = [row for row in health_rows if row["provider"] == provider]
    return {
        "channels": cmd_channels(adapter),
        "selected_provider": provider,
        "health": health_rows,
        "recent_searches": repo.search_rows(limit),
    }


def cmd_channels_refresh(
    provider: str,
    auth_store: ProviderAuthStore,
    timeout: int,
    music_lang: str,
) -> dict[str, Any]:
    if provider != MYFREEJUICES_PROVIDER:
        raise SystemExit(f"Unsupported provider for channels-refresh: {provider}")
    runtime = detect_visible_browser_runtime()
    if not runtime["can_refresh"]:
        raise SystemExit(str(runtime["message"]))
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("playwright_missing: Install Playwright with 'pip install playwright' and 'python3 -m playwright install chromium'.") from exc

    site_url = "https://2024.myfreemp3juices.cc/"
    deadline_ms = max(30, timeout) * 1000
    cf_clearance = ""
    detected_lang = music_lang or "en"
    detected_user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
    )

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=False)
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                "chromium_missing: Install Chromium with 'python3 -m playwright install chromium', then retry."
            ) from exc
        context = browser.new_context(user_agent=detected_user_agent)
        context.add_cookies(
            [
                {
                    "name": "musicLang",
                    "value": detected_lang,
                    "domain": "2024.myfreemp3juices.cc",
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "Lax",
                }
            ]
        )
        page = context.new_page()
        page.goto(site_url, wait_until="domcontentloaded", timeout=deadline_ms)
        end_time = time.time() + max(30, timeout)
        while time.time() < end_time:
            cookies = context.cookies(site_url)
            for cookie in cookies:
                if cookie.get("name") == "cf_clearance" and cookie.get("value"):
                    cf_clearance = str(cookie["value"])
                if cookie.get("name") == "musicLang" and cookie.get("value"):
                    detected_lang = str(cookie["value"])
            if cf_clearance:
                break
            page.wait_for_timeout(1000)
        browser.close()

    if not cf_clearance:
        raise SystemExit("timeout_waiting_for_cf_clearance: Complete the Cloudflare challenge in the opened browser and retry.")

    record = auth_store.set(
        provider,
        {
            "cf_clearance": cf_clearance,
            "music_lang": detected_lang or "en",
            "user_agent": detected_user_agent,
            "search_headers": {
                "accept": "text/javascript, application/javascript, */*; q=0.01",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": site_url.rstrip("/"),
                "referer": site_url,
                "x-requested-with": "XMLHttpRequest",
            },
            "updated_at": utc_now(),
            "site": site_url,
        },
    )
    return {
        "provider": provider,
        "status": "refreshed",
        "state_file": str(auth_store.path),
        "updated_at": record["updated_at"],
        "cookie_found": True,
        "cf_clearance_preview": redact_secret(cf_clearance),
        "music_lang": record["music_lang"],
    }


def cmd_channel_auth_refresh(provider: str, auth_store: ProviderAuthStore, timeout: int, music_lang: str) -> dict[str, Any]:
    return cmd_channels_refresh(provider, auth_store, timeout, music_lang)


def cmd_channel_auth_set(
    provider: str,
    auth_store: ProviderAuthStore,
    cf_clearance: str,
    music_lang: str,
    user_agent: str,
) -> dict[str, Any]:
    if provider != MYFREEJUICES_PROVIDER:
        raise SystemExit(f"Unsupported provider for channel-auth set: {provider}")
    clearance = (cf_clearance or "").strip()
    if not clearance:
        raise SystemExit("cf_clearance is required")
    resolved_user_agent = (
        (user_agent or "").strip()
        or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
    )
    record = auth_store.set(
        provider,
        {
            "cf_clearance": clearance,
            "music_lang": (music_lang or "en").strip() or "en",
            "user_agent": resolved_user_agent,
            "search_headers": {
                "accept": "text/javascript, application/javascript, */*; q=0.01",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": "https://2024.myfreemp3juices.cc",
                "referer": "https://2024.myfreemp3juices.cc/",
                "x-requested-with": "XMLHttpRequest",
            },
            "updated_at": utc_now(),
            "site": "https://2024.myfreemp3juices.cc/",
            "source": "manual_set",
        },
    )
    return {
        "provider": provider,
        "status": "stored",
        "state_file": str(auth_store.path),
        "updated_at": record["updated_at"],
        "cf_clearance_preview": redact_secret(clearance),
        "music_lang": record["music_lang"],
        "source": "manual_set",
    }


def cmd_channel_auth_show(provider: str, auth_store: ProviderAuthStore) -> dict[str, Any]:
    if provider != MYFREEJUICES_PROVIDER:
        raise SystemExit(f"Unsupported provider for channel-auth show: {provider}")
    return auth_summary(provider, auth_store)


def cmd_channel_auth_clear(provider: str, auth_store: ProviderAuthStore) -> dict[str, Any]:
    if provider != MYFREEJUICES_PROVIDER:
        raise SystemExit(f"Unsupported provider for channel-auth clear: {provider}")
    cleared = auth_store.clear(provider)
    return {
        "provider": provider,
        "status": "cleared" if cleared else "not_configured",
        "state_file": str(auth_store.path),
        "updated_at": utc_now(),
    }


def cmd_channel_auth_validate(adapter: MusicdlAdapter, provider: str, auth_store: ProviderAuthStore) -> dict[str, Any]:
    if provider != MYFREEJUICES_PROVIDER:
        raise SystemExit(f"Unsupported provider for channel-auth validate: {provider}")
    summary = auth_summary(provider, auth_store)
    if not summary["configured"]:
        return {
            **summary,
            "status": "invalid",
            "error": "missing_cf_clearance",
            "validated_at": utc_now(),
            "sample_result_count": 0,
        }
    try:
        results = adapter.search_provider(provider, "周杰伦 稻香", limit=3, allow_fallback=False)
    except Exception as exc:  # noqa: BLE001
        return {
            **summary,
            "status": "invalid",
            "error": str(exc),
            "validated_at": utc_now(),
            "sample_result_count": 0,
        }
    return {
        **summary,
        "status": "valid",
        "error": "",
        "validated_at": utc_now(),
        "sample_result_count": len(results),
        "sample_titles": [item.title for item in results[:3]],
    }


def cmd_listen(
    repo: Repository,
    adapter: MusicdlAdapter,
    worker: DownloadQueueWorker,
    auth_store: ProviderAuthStore,
    query: str,
    provider: str | None,
    dry_run: bool,
    refresh_auth: bool,
) -> dict[str, Any]:
    target_provider = provider or MYFREEJUICES_PROVIDER
    checked_steps: list[str] = []
    auth_guidance: dict[str, Any] = {}

    if target_provider == MYFREEJUICES_PROVIDER:
        checked_steps.append("protected_provider_health")
        probe = adapter.probe_provider(MYFREEJUICES_PROVIDER, query)
        if probe.get("error") in {"missing_cf_clearance", "invalid_cf_clearance"} and refresh_auth:
            runtime = detect_visible_browser_runtime()
            auth_guidance = {
                "auth_action_required": True,
                "auth_provider": MYFREEJUICES_PROVIDER,
                "auth_reason": probe.get("error"),
                "recommended_command": "bash scripts/musicctl channel-auth set --provider MyFreeMP3JuicesMusicClient --cf-clearance \"COOKIE_VALUE\"",
                "refresh_available": runtime["can_refresh"],
            }
            if runtime["can_refresh"]:
                checked_steps.append("protected_provider_refresh")
                cmd_channel_auth_refresh(MYFREEJUICES_PROVIDER, auth_store, 180, "en")
                probe = adapter.probe_provider(MYFREEJUICES_PROVIDER, query)
                if probe.get("error") not in {"missing_cf_clearance", "invalid_cf_clearance"}:
                    auth_guidance = {}
            else:
                checked_steps.append("protected_provider_refresh_skipped")
                auth_guidance["auth_runtime_error"] = runtime["reason"]
                auth_guidance["auth_runtime_message"] = runtime["message"]

    if target_provider:
        checked_steps.append("provider_scoped_search")
        try:
            scoped = cmd_channel_search_variants(repo, adapter, target_provider, query, 8)
        except SystemExit:
            scoped = {"query": query, "provider_scope": target_provider, "count": 0, "items": []}
        items = scoped["items"]
        if items:
            chosen_item = sorted(items, key=lambda item: listen_item_score(query, item), reverse=True)[0]
            decision = cmd_download_choose(
                repo,
                adapter,
                worker,
                chosen_item["track_id"],
                target_provider,
                dry_run,
                refresh_health=False,
            )
            return {
                "query": query,
                "intent": "listen",
                "checked_steps": checked_steps,
                "provider_scope": target_provider,
                "selected_track": next((item for item in items if item["track_id"] == chosen_item["track_id"]), chosen_item),
                **auth_guidance,
                **decision,
            }

    checked_steps.append("default_provider_search")
    broad = cmd_search_variants(repo, adapter, query, "mixed", 8)
    if not broad["items"]:
        return {
            "query": query,
            "intent": "listen",
            "checked_steps": checked_steps,
            "status": "unavailable",
            "failure_reason": "no_search_results",
        }
    chosen_item = sorted(broad["items"], key=lambda item: listen_item_score(query, item), reverse=True)[0]
    decision = cmd_download_choose(
        repo,
        adapter,
        worker,
        chosen_item["track_id"],
        None if provider == MYFREEJUICES_PROVIDER else provider,
        dry_run,
        refresh_health=False,
    )
    return {
        "query": query,
        "intent": "listen",
        "checked_steps": checked_steps,
        "provider_scope": provider or "default_sources",
        "selected_track": next((item for item in broad["items"] if item["track_id"] == chosen_item["track_id"]), chosen_item),
        **auth_guidance,
        **decision,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local music orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")

    search = sub.add_parser("search")
    search.add_argument("--query", required=True)
    search.add_argument("--type", default="mixed")
    search.add_argument("--limit", type=int, default=12)

    search_preview = sub.add_parser("search-preview")
    search_preview.add_argument("--query", required=True)
    search_preview.add_argument("--type", default="mixed")
    search_preview.add_argument("--limit", type=int, default=12)

    search_variants = sub.add_parser("search-variants")
    search_variants.add_argument("--query", required=True)
    search_variants.add_argument("--type", default="mixed")
    search_variants.add_argument("--limit", type=int, default=12)

    channel_search = sub.add_parser("channel-search")
    channel_search.add_argument("--provider", required=True)
    channel_search.add_argument("--query", required=True)
    channel_search.add_argument("--limit", type=int, default=12)

    channel_search_variants = sub.add_parser("channel-search-variants")
    channel_search_variants.add_argument("--provider", required=True)
    channel_search_variants.add_argument("--query", required=True)
    channel_search_variants.add_argument("--limit", type=int, default=12)

    sub.add_parser("channels")

    channels_refresh = sub.add_parser("channels-refresh")
    channels_refresh.add_argument("--provider", required=True)
    channels_refresh.add_argument("--timeout", type=int, default=180)
    channels_refresh.add_argument("--lang", default="en")

    channel_auth = sub.add_parser("channel-auth")
    channel_auth_sub = channel_auth.add_subparsers(dest="channel_auth_action", required=True)
    channel_auth_refresh = channel_auth_sub.add_parser("refresh")
    channel_auth_refresh.add_argument("--provider", required=True)
    channel_auth_refresh.add_argument("--timeout", type=int, default=180)
    channel_auth_refresh.add_argument("--lang", default="en")
    channel_auth_set = channel_auth_sub.add_parser("set")
    channel_auth_set.add_argument("--provider", required=True)
    channel_auth_set.add_argument("--cf-clearance", required=True)
    channel_auth_set.add_argument("--lang", default="en")
    channel_auth_set.add_argument("--user-agent", default="")
    channel_auth_show = channel_auth_sub.add_parser("show")
    channel_auth_show.add_argument("--provider", required=True)
    channel_auth_validate = channel_auth_sub.add_parser("validate")
    channel_auth_validate.add_argument("--provider", required=True)
    channel_auth_clear = channel_auth_sub.add_parser("clear")
    channel_auth_clear.add_argument("--provider", required=True)

    channels_health = sub.add_parser("channels-health")
    channels_health.add_argument("--limit", type=int, default=20)
    channels_health.add_argument("--refresh", action="store_true")
    channels_health.add_argument("--provider")

    analyze = sub.add_parser("analyze")
    analyze.add_argument("--collection", default="likes")

    recommend = sub.add_parser("recommend")
    recommend.add_argument("--collection", default="likes")
    recommend.add_argument("--limit", type=int, default=12)

    recommend_plan = sub.add_parser("recommend-plan")
    recommend_plan.add_argument("--collection", default="likes")
    recommend_plan.add_argument("--limit", type=int, default=12)

    recommend_candidates = sub.add_parser("recommend-candidates")
    recommend_candidates.add_argument("--collection", default="likes")
    recommend_candidates.add_argument("--limit", type=int, default=12)

    recommend_commit = sub.add_parser("recommend-commit")
    recommend_commit.add_argument("--candidate-set-id", type=int, required=True)

    recommend_show = sub.add_parser("recommend-show")
    recommend_show.add_argument("--run-id", type=int, required=True)

    daily = sub.add_parser("daily")
    daily.add_argument("--refresh", action="store_true")

    push = sub.add_parser("push")
    push.add_argument("which", choices=["latest"])

    push_list = sub.add_parser("push-list")
    push_list.add_argument("--limit", type=int, default=20)

    push_show = sub.add_parser("push-show")
    push_show.add_argument("--id", type=int, required=True)

    push_mark = sub.add_parser("push-mark-consumed")
    push_mark.add_argument("--id", type=int, required=True)

    history = sub.add_parser("history")
    history.add_argument("kind", choices=["search", "recommend"])
    history.add_argument("--limit", type=int, default=20)

    listen = sub.add_parser("listen")
    listen.add_argument("--query", required=True)
    listen.add_argument("--provider")
    listen.add_argument("--dry-run", action="store_true")
    listen.add_argument("--refresh-auth", action="store_true", default=True)

    track_show = sub.add_parser("track-show")
    track_show.add_argument("--track-id", type=int, required=True)

    variants = sub.add_parser("variants")
    variants.add_argument("--track-id", type=int, required=True)

    playlist = sub.add_parser("playlist")
    psub = playlist.add_subparsers(dest="playlist_action", required=True)
    pcreate = psub.add_parser("create")
    pcreate.add_argument("--name", required=True)
    pcreate.add_argument("--description", default="")
    psub.add_parser("list")
    pshow = psub.add_parser("show")
    pshow.add_argument("--playlist", required=True)
    padd = psub.add_parser("add")
    padd.add_argument("--playlist", required=True)
    padd.add_argument("--track-id", type=int, required=True)
    premove = psub.add_parser("remove")
    premove.add_argument("--playlist", required=True)
    premove.add_argument("--track-id", type=int, required=True)
    pdelete = psub.add_parser("delete")
    pdelete.add_argument("--playlist", required=True)
    prename = psub.add_parser("rename")
    prename.add_argument("--playlist", required=True)
    prename.add_argument("--to", required=True)

    collection = sub.add_parser("collection")
    csub = collection.add_subparsers(dest="collection_action", required=True)
    csub.add_parser("list")
    cshow = csub.add_parser("show")
    cshow.add_argument("--collection", required=True)
    ccreate = csub.add_parser("create")
    ccreate.add_argument("--collection", required=True)
    cadd = csub.add_parser("add")
    cadd.add_argument("--collection", default="likes")
    cadd.add_argument("--track-id", type=int, required=True)
    crem = csub.add_parser("remove")
    crem.add_argument("--collection", required=True)
    crem.add_argument("--track-id", type=int, required=True)
    cmerge = csub.add_parser("merge")
    cmerge.add_argument("--collection", required=True)
    cmerge.add_argument("--sources", required=True)

    download = sub.add_parser("download")
    dsub = download.add_subparsers(dest="download_target", required=True)
    dtrack = dsub.add_parser("track")
    dtrack.add_argument("--track-id", type=int, required=True)
    dtrack.add_argument("--provider")
    dchoose = dsub.add_parser("choose")
    dchoose.add_argument("--track-id", type=int, required=True)
    dchoose.add_argument("--provider")
    dchoose.add_argument("--dry-run", action="store_true")
    dchoose.add_argument("--refresh-health", action="store_true")
    dplaylist = dsub.add_parser("playlist")
    dplaylist.add_argument("--playlist", required=True)
    dalbum = dsub.add_parser("album")
    dalbum.add_argument("--album-id", type=int, required=True)
    dpreview = dsub.add_parser("preview")
    dpreview.add_argument("--track-id", type=int, required=True)
    dqueue = dsub.add_parser("queue")
    dqueue.add_argument("--limit", type=int, default=20)
    dstatus = dsub.add_parser("status")
    dstatus.add_argument("--job-id", type=int, required=True)
    dfiles = dsub.add_parser("files")
    dfiles.add_argument("--limit", type=int, default=20)
    dretry = dsub.add_parser("retry")
    dretry.add_argument("--job-id", type=int, required=True)
    dcancel = dsub.add_parser("cancel")
    dcancel.add_argument("--job-id", type=int, required=True)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    db_path, download_dir, provider_work_dir, auth_store_path = build_paths()
    repo = Repository(db_path)
    repo.init()
    auth_store = ProviderAuthStore(auth_store_path)
    adapter = MusicdlAdapter(provider_work_dir, build_sources(), auth_store=auth_store)
    worker = DownloadQueueWorker(repo, adapter, download_dir)

    if args.command == "init":
        print_json(cmd_init(repo))
    elif args.command == "search":
        print_json(cmd_search(repo, adapter, args.query, args.type, args.limit))
    elif args.command == "search-preview":
        print_json(cmd_search_preview(adapter, args.query, args.type, args.limit))
    elif args.command == "search-variants":
        print_json(cmd_search_variants(repo, adapter, args.query, args.type, args.limit))
    elif args.command == "channel-search":
        print_json(cmd_channel_search(repo, adapter, args.provider, args.query, args.limit))
    elif args.command == "channel-search-variants":
        print_json(cmd_channel_search_variants(repo, adapter, args.provider, args.query, args.limit))
    elif args.command == "channels":
        print_json(cmd_channels(adapter))
    elif args.command == "channels-refresh":
        print_json(cmd_channels_refresh(args.provider, auth_store, args.timeout, args.lang))
    elif args.command == "channel-auth":
        if args.channel_auth_action == "refresh":
            print_json(cmd_channel_auth_refresh(args.provider, auth_store, args.timeout, args.lang))
        elif args.channel_auth_action == "set":
            print_json(cmd_channel_auth_set(args.provider, auth_store, args.cf_clearance, args.lang, args.user_agent))
        elif args.channel_auth_action == "show":
            print_json(cmd_channel_auth_show(args.provider, auth_store))
        elif args.channel_auth_action == "validate":
            print_json(cmd_channel_auth_validate(adapter, args.provider, auth_store))
        elif args.channel_auth_action == "clear":
            print_json(cmd_channel_auth_clear(args.provider, auth_store))
    elif args.command == "channels-health":
        print_json(cmd_channels_health(repo, adapter, args.limit, args.refresh, args.provider))
    elif args.command == "analyze":
        print_json(cmd_analyze(repo, args.collection))
    elif args.command == "recommend":
        print_json(cmd_recommend(repo, adapter, args.collection, args.limit))
    elif args.command == "recommend-plan":
        print_json(cmd_recommend_plan(repo, args.collection, args.limit))
    elif args.command == "recommend-candidates":
        print_json(cmd_recommend_candidates(repo, adapter, args.collection, args.limit))
    elif args.command == "recommend-commit":
        print_json(cmd_recommend_commit(repo, args.candidate_set_id))
    elif args.command == "recommend-show":
        print_json(cmd_recommend_show(repo, args.run_id))
    elif args.command == "daily":
        print_json(cmd_daily(repo, adapter, args.refresh))
    elif args.command == "push":
        print_json(cmd_push(repo))
    elif args.command == "push-list":
        print_json(cmd_push_list(repo, args.limit))
    elif args.command == "push-show":
        print_json(cmd_push_show(repo, args.id))
    elif args.command == "push-mark-consumed":
        print_json(cmd_push_mark_consumed(repo, args.id))
    elif args.command == "history":
        print_json(cmd_history(repo, args.kind, args.limit))
    elif args.command == "listen":
        print_json(cmd_listen(repo, adapter, worker, auth_store, args.query, args.provider, args.dry_run, args.refresh_auth))
    elif args.command == "track-show":
        print_json(cmd_track_show(repo, args.track_id))
    elif args.command == "variants":
        print_json(cmd_variants(repo, args.track_id))
    elif args.command == "playlist":
        if args.playlist_action == "create":
            print_json(cmd_playlist(repo, "create", args.name, None, args.description))
        elif args.playlist_action == "list":
            print_json(cmd_playlist(repo, "list", None, None, None))
        elif args.playlist_action == "show":
            print_json(cmd_playlist(repo, "show", args.playlist, None, None))
        elif args.playlist_action == "add":
            print_json(cmd_playlist(repo, "add", args.playlist, args.track_id, None))
        elif args.playlist_action == "remove":
            print_json(cmd_playlist(repo, "remove", args.playlist, args.track_id, None))
        elif args.playlist_action == "delete":
            print_json(cmd_playlist(repo, "delete", args.playlist, None, None))
        elif args.playlist_action == "rename":
            print_json(cmd_playlist(repo, "rename", args.playlist, None, args.to))
    elif args.command == "collection":
        if args.collection_action == "list":
            print_json(cmd_collection(repo, "list", None, None))
        elif args.collection_action == "show":
            print_json(cmd_collection(repo, "show", args.collection, None))
        elif args.collection_action == "create":
            print_json(cmd_collection(repo, "create", args.collection, None))
        elif args.collection_action == "add":
            print_json(cmd_collection(repo, "add", args.collection, args.track_id))
        elif args.collection_action == "remove":
            print_json(cmd_collection(repo, "remove", args.collection, args.track_id))
        elif args.collection_action == "merge":
            print_json(cmd_collection(repo, "merge", args.collection, None, sources=[part.strip() for part in args.sources.split(",") if part.strip()]))
    elif args.command == "download":
        if args.download_target in {"track", "playlist", "album"}:
            print_json(
                cmd_download(
                    repo,
                    adapter,
                    worker,
                    args.download_target,
                    getattr(args, "track_id", None),
                    getattr(args, "playlist", None),
                    getattr(args, "album_id", None),
                    getattr(args, "provider", None),
                )
            )
            if args.download_target != "track":
                time.sleep(0.1)
        elif args.download_target == "choose":
            print_json(
                cmd_download_choose(
                    repo,
                    adapter,
                    worker,
                    args.track_id,
                    args.provider,
                    args.dry_run,
                    args.refresh_health,
                )
            )
        elif args.download_target == "preview":
            print_json(cmd_download_preview(repo, args.track_id))
        elif args.download_target == "queue":
            print_json(cmd_download_queue(repo, args.limit))
        elif args.download_target == "status":
            print_json(cmd_download_status(repo, args.job_id))
        elif args.download_target == "files":
            print_json(cmd_download_files(repo, args.limit))
        elif args.download_target == "retry":
            print_json(cmd_download_retry(repo, worker, args.job_id))
        elif args.download_target == "cancel":
            print_json(cmd_download_cancel(repo, args.job_id))


if __name__ == "__main__":
    main()
