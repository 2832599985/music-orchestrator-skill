from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


VALID_AUDIO_EXTS = {"mp3", "m4a", "flac", "wav", "ogg", "aac", "opus"}
MYFREEJUICES_PROVIDER = "MyFreeMP3JuicesMusicClient"


def _request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 12,
) -> tuple[bytes, str, dict[str, str]]:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
        final_url = response.geturl()
        response_headers = {key.lower(): value for key, value in response.headers.items()}
    return payload, final_url, response_headers


def _request_text(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 12,
) -> tuple[str, str, dict[str, str]]:
    payload, final_url, response_headers = _request(url, method=method, headers=headers, data=data, timeout=timeout)
    return payload.decode("utf-8", errors="ignore"), final_url, response_headers


def _request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 12,
) -> tuple[dict[str, Any] | list[Any], str, dict[str, str]]:
    text, final_url, response_headers = _request_text(url, method=method, headers=headers, data=data, timeout=timeout)
    return json.loads(text), final_url, response_headers


def _resolve_url(url: str, *, headers: dict[str, str] | None = None, timeout: int = 12) -> str:
    request = urllib.request.Request(url, headers=headers or {}, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.geturl()
    except Exception:
        try:
            _, final_url, _ = _request(url, headers=headers, timeout=timeout)
            return final_url
        except Exception:
            return url


def _probe_audio_url(url: str, *, headers: dict[str, str] | None = None, timeout: int = 12) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers or {}, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            content_type = response_headers.get("content-type", "").lower()
            ext = final_url.split("?")[0].split(".")[-1].lower()
            if ext not in VALID_AUDIO_EXTS:
                ext = _guess_ext(content_type, ext)
            valid = ext in VALID_AUDIO_EXTS or content_type.startswith("audio/")
            return {
                "valid": valid,
                "final_url": final_url,
                "file_size": response_headers.get("content-length"),
                "ext": ext,
            }
    except Exception:
        range_headers = dict(headers or {})
        range_headers["Range"] = "bytes=0-0"
        try:
            request = urllib.request.Request(url, headers=range_headers, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final_url = response.geturl()
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                content_type = response_headers.get("content-type", "").lower()
                ext = final_url.split("?")[0].split(".")[-1].lower()
                if ext not in VALID_AUDIO_EXTS:
                    ext = _guess_ext(content_type, ext)
                valid = ext in VALID_AUDIO_EXTS or content_type.startswith("audio/")
                return {
                    "valid": valid,
                    "final_url": final_url,
                    "file_size": response_headers.get("content-length"),
                    "ext": ext,
                }
        except Exception:
            return {"valid": False, "final_url": url, "file_size": None, "ext": ""}


def _guess_ext(content_type: str, current_ext: str) -> str:
    if current_ext in VALID_AUDIO_EXTS:
        return current_ext
    mapping = {
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/mp4": "m4a",
        "audio/x-m4a": "m4a",
        "audio/aac": "aac",
        "audio/flac": "flac",
        "audio/wav": "wav",
        "audio/ogg": "ogg",
        "audio/opus": "opus",
    }
    return mapping.get(content_type.split(";")[0].strip(), current_ext)


def _clean_lrc(text: str) -> str:
    return (text or "").replace("\r\n", "\n").strip()


def _duration_from_lrc(text: str) -> str:
    matches = re.findall(r"\[(\d+):(\d+)(?:\.(\d+))?\]", text or "")
    if not matches:
        return ""
    minute, second, _ = matches[-1]
    total_seconds = int(minute) * 60 + int(second)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _duration_from_seconds(seconds: Any) -> str:
    try:
        total_seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    remainder = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{remainder:02d}"


def _parse_jsonp_payload(text: str) -> Any:
    payload = (text or "").strip()
    start = payload.find("(")
    end = payload.rfind(")")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("invalid_jsonp_response")
    return json.loads(payload[start + 1 : end])


class ProviderAuthStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def get(self, provider: str) -> dict[str, Any]:
        payload = self.load()
        value = payload.get(provider)
        if isinstance(value, dict):
            return value
        return {}

    def set(self, provider: str, config: dict[str, Any]) -> dict[str, Any]:
        payload = self.load()
        payload[provider] = config
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return config


class EmbeddedProvider:
    name = "EmbeddedProvider"

    def __init__(self, auth_store: ProviderAuthStore | None = None) -> None:
        self.auth_store = auth_store

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError

    def probe(self, query: str) -> dict[str, Any]:
        started = time.time()
        try:
            items = self.search(query, limit=2)
            elapsed_ms = int((time.time() - started) * 1000)
            return {
                "provider": self.name,
                "probe_query": query,
                "status": "ok" if items else "empty",
                "latency_ms": elapsed_ms,
                "result_count": len(items),
                "downloadable_count": sum(1 for item in items if item.get("downloadable_now")),
                "error": "",
                "sample_titles": [item.get("title", "") for item in items[:3]],
            }
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.time() - started) * 1000)
            return {
                "provider": self.name,
                "probe_query": query,
                "status": "search_failed",
                "latency_ms": elapsed_ms,
                "result_count": 0,
                "downloadable_count": 0,
                "error": str(exc),
                "sample_titles": [],
            }


class ITunesFallbackProvider(EmbeddedProvider):
    name = "ITunesFallback"

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        url = "https://itunes.apple.com/search?" + urllib.parse.urlencode({"term": query, "entity": "song", "limit": limit})
        payload, _, _ = _request_json(url, timeout=12)
        items: list[dict[str, Any]] = []
        for item in payload.get("results", []):
            items.append(
                {
                    "title": item.get("trackName", "") or "",
                    "artists": item.get("artistName", "") or "",
                    "album": item.get("collectionName", "") or "",
                    "duration": "",
                    "source": self.name,
                    "source_id": str(item.get("trackId", "") or ""),
                    "download_url": "",
                    "cover_url": item.get("artworkUrl100", "") or "",
                    "lyric": "",
                    "downloadable_now": False,
                    "ext": "m4a",
                    "download_headers_json": "",
                }
            )
        return items


class JBSouProvider(EmbeddedProvider):
    name = "JBSouMusicClient"
    root_sources = ("netease", "qq", "kugou", "kuwo")
    search_headers = {
        "user-agent": "Mozilla/5.0",
        "origin": "https://www.jbsou.cn",
        "x-requested-with": "XMLHttpRequest",
        "accept": "application/json, text/javascript, */*; q=0.01",
        "referer": "https://www.jbsou.cn/",
    }
    download_headers = {"user-agent": "Mozilla/5.0"}

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        base_url = "https://www.jbsou.cn/"
        for root_source in self.root_sources:
            if len(items) >= limit:
                break
            body = urllib.parse.urlencode({"input": query, "filter": "name", "type": root_source, "page": 1}).encode("utf-8")
            payload, _, _ = _request_json(base_url, method="POST", headers=self.search_headers, data=body, timeout=10)
            for result in payload.get("data", []):
                if len(items) >= limit:
                    break
                song_id = str(result.get("songid", "") or "")
                download_path = result.get("url", "") or ""
                if not song_id or not download_path:
                    continue
                download_url = _resolve_url(urllib.parse.urljoin(base_url, download_path), headers=self.download_headers, timeout=10)
                probe = _probe_audio_url(download_url, headers=self.download_headers, timeout=10)
                lyric_text = ""
                duration = ""
                lyric_path = result.get("lrc", "") or ""
                if lyric_path:
                    try:
                        lyric_text, _, _ = _request_text(urllib.parse.urljoin(base_url, lyric_path), timeout=10)
                        lyric_text = _clean_lrc(lyric_text)
                        duration = _duration_from_lrc(lyric_text)
                    except Exception:
                        lyric_text = ""
                items.append(
                    {
                        "title": result.get("name", "") or "",
                        "artists": (result.get("artist", "") or "").replace("/", ", "),
                        "album": result.get("album", "") or "",
                        "duration": duration,
                        "source": self.name,
                        "source_id": song_id,
                        "download_url": probe["final_url"] if probe["valid"] else "",
                        "cover_url": urllib.parse.urljoin(base_url, result.get("cover", "") or ""),
                        "lyric": lyric_text,
                        "downloadable_now": bool(probe["valid"]),
                        "ext": probe["ext"] or "mp3",
                        "download_headers_json": json.dumps(self.download_headers, ensure_ascii=False),
                    }
                )
        return items


class MyFreeMP3Provider(EmbeddedProvider):
    name = "MyFreeMP3MusicClient"
    search_headers = {
        "accept": "*/*",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "x-requested-with": "XMLHttpRequest",
        "origin": "https://www.myfreemp3.com.cn",
        "referer": "https://www.myfreemp3.com.cn/",
        "user-agent": "Mozilla/5.0",
    }

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        body = urllib.parse.urlencode({"type": "netease", "filter": "name", "page": 1, "input": query}).encode("utf-8")
        payload, _, _ = _request_json("https://www.myfreemp3.com.cn/", method="POST", headers=self.search_headers, data=body, timeout=10)
        items: list[dict[str, Any]] = []
        for result in payload.get("data", {}).get("list", []):
            if len(items) >= limit:
                break
            source_id = str(result.get("id", "") or "")
            if not source_id:
                continue
            netease_url = f"http://music.163.com/song/media/outer/url?id={source_id}.mp3"
            download_url = _resolve_url(netease_url, timeout=10)
            probe = _probe_audio_url(download_url, timeout=10)
            lyric_text = _clean_lrc((result.get("lrc", "") or "").removeprefix("data:text/plain,"))
            items.append(
                {
                    "title": result.get("title", "") or "",
                    "artists": result.get("author", "") or "",
                    "album": "",
                    "duration": _duration_from_lrc(lyric_text),
                    "source": self.name,
                    "source_id": source_id,
                    "download_url": probe["final_url"] if probe["valid"] else "",
                    "cover_url": result.get("pic", "") or "",
                    "lyric": lyric_text,
                    "downloadable_now": bool(probe["valid"]),
                    "ext": "mp3",
                    "download_headers_json": "",
                }
            )
        return items


class MP3JuiceProvider(EmbeddedProvider):
    name = "MP3JuiceMusicClient"
    search_headers = {
        "user-agent": "Mozilla/5.0",
        "referer": "https://mp3juice.sc/",
        "origin": "https://mp3juice.sc",
    }

    def _get_dynamic_config(self) -> list[Any]:
        text, _, _ = _request_text(f"https://mp3juice.as/?t={int(time.time() * 1000)}", headers=self.search_headers, timeout=10)
        match = re.search(r"var\s+json\s*=\s*JSON\.parse\('(.+?)'\);", text)
        if match:
            encoded = match.group(1).encode("utf-8").decode("unicode_escape")
            return json.loads(encoded)
        match = re.search(r"var\s+json\s*=\s*(\[.+?\]);", text)
        if not match:
            raise ValueError("mp3juice dynamic config not found")
        return json.loads(match.group(1))

    def _calculate_auth(self, raw_data: list[Any]) -> str:
        data_arr, should_reverse, offset_arr = raw_data[0], raw_data[1], raw_data[2]
        result_chars = []
        for idx, value in enumerate(data_arr):
            result_chars.append(chr(value - offset_arr[len(offset_arr) - (idx + 1)]))
        if should_reverse:
            result_chars.reverse()
        return "".join(result_chars)[:32]

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        config = self._get_dynamic_config()
        auth_token = self._calculate_auth(config)
        encoded_query = base64.b64encode(urllib.parse.quote(query, safe="").encode("utf-8")).decode("utf-8")
        search_url = "https://mp3juice.sc/api/v1/search?" + urllib.parse.urlencode(
            {"k": auth_token, "y": "s", "q": encoded_query, "t": str(int(time.time()))}
        )
        payload, _, _ = _request_json(search_url, headers=self.search_headers, timeout=12)
        search_results = []
        for item in payload.get("yt", []) or []:
            item["root_source"] = "YouTube"
            search_results.append(item)
        for item in payload.get("sc", []) or []:
            item["root_source"] = "SoundCloud"
            search_results.append(item)

        items: list[dict[str, Any]] = []
        param_key = chr(config[6])
        for result in search_results:
            if len(items) >= limit:
                break
            source_id = str(result.get("id", "") or "")
            if not source_id:
                continue
            download_url = ""
            if result.get("root_source") == "SoundCloud":
                id_base64 = result.get("id_base64", "") or ""
                title_base64 = result.get("title_base64", "") or ""
                if id_base64 and title_base64:
                    download_url = f"https://thetacloud.org/s/{id_base64}/{title_base64}/"
            else:
                params = urllib.parse.urlencode({param_key: auth_token, "t": str(int(time.time()))})
                init_payload, _, _ = _request_json(f"https://theta.thetacloud.org/api/v1/init?{params}", headers=self.search_headers, timeout=10)
                convert_url = init_payload.get("convertURL", "") or ""
                if not convert_url:
                    continue
                convert_payload, _, _ = _request_json(
                    f"{convert_url}&v={source_id}&f=mp3&t={int(time.time())}",
                    headers=self.search_headers,
                    timeout=12,
                )
                redirect_url = convert_payload.get("redirectURL", "") or ""
                if not redirect_url:
                    continue
                redirect_payload, _, _ = _request_json(redirect_url, headers=self.search_headers, timeout=12)
                download_url = redirect_payload.get("downloadURL", "") or ""
            if not download_url:
                continue
            probe = _probe_audio_url(download_url, headers=self.search_headers, timeout=10)
            items.append(
                {
                    "title": result.get("title", "") or "",
                    "artists": "",
                    "album": "",
                    "duration": "",
                    "source": self.name,
                    "source_id": source_id,
                    "download_url": probe["final_url"] if probe["valid"] else download_url,
                    "cover_url": "",
                    "lyric": "",
                    "downloadable_now": bool(probe["valid"]) or bool(download_url),
                    "ext": probe["ext"] or "mp3",
                    "download_headers_json": json.dumps(self.search_headers, ensure_ascii=False),
                }
            )
        return items


class MyFreeMP3JuicesProvider(EmbeddedProvider):
    name = MYFREEJUICES_PROVIDER
    base_url = "https://2024.myfreemp3juices.cc"
    site_url = f"{base_url}/"
    search_path = "/api/api_search.php"
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
    )

    def _auth(self) -> tuple[str, str]:
        clearance = os.environ.get("MUSIC_ORCH_MYFREEJUICES_CF_CLEARANCE", "").strip()
        music_lang = os.environ.get("MUSIC_ORCH_MYFREEJUICES_LANG", "").strip() or "en"
        if not clearance and self.auth_store is not None:
            stored = self.auth_store.get(self.name)
            clearance = str(stored.get("cf_clearance", "") or "").strip()
            music_lang = str(stored.get("music_lang", "") or music_lang).strip() or "en"
        if not clearance:
            raise ValueError("missing_cf_clearance")
        return clearance, music_lang

    def _search_headers(self, clearance: str, music_lang: str) -> dict[str, str]:
        return {
            "accept": "text/javascript, application/javascript, */*; q=0.01",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": self.base_url,
            "referer": self.site_url,
            "user-agent": self.user_agent,
            "x-requested-with": "XMLHttpRequest",
            "cookie": f"cf_clearance={clearance}; musicLang={music_lang}",
        }

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        clearance, music_lang = self._auth()
        callback = f"jQuery_{int(time.time() * 1000)}"
        url = f"{self.base_url}{self.search_path}?" + urllib.parse.urlencode({"callback": callback})
        body = urllib.parse.urlencode({"q": query, "page": 0}).encode("utf-8")
        try:
            text, _, _ = _request_text(
                url,
                method="POST",
                headers=self._search_headers(clearance, music_lang),
                data=body,
                timeout=15,
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise ValueError("invalid_cf_clearance") from exc
            raise
        payload = _parse_jsonp_payload(text)
        response_items = payload.get("response", []) if isinstance(payload, dict) else []
        items: list[dict[str, Any]] = []
        for result in response_items:
            if isinstance(result, str):
                continue
            if len(items) >= limit:
                break
            raw_url = str(result.get("url", "") or "").strip()
            source_id = f"{result.get('owner_id', '')}_{result.get('id', '')}".strip("_")
            if not raw_url or not source_id:
                continue
            probe = _probe_audio_url(raw_url, headers={"user-agent": self.user_agent}, timeout=12)
            items.append(
                {
                    "title": str(result.get("title", "") or ""),
                    "artists": str(result.get("artist", "") or ""),
                    "album": "",
                    "duration": _duration_from_seconds(result.get("duration")),
                    "source": self.name,
                    "source_id": source_id,
                    "download_url": probe["final_url"] if probe["valid"] else raw_url,
                    "cover_url": "",
                    "lyric": "",
                    "downloadable_now": bool(probe["valid"]) or bool(raw_url),
                    "ext": probe["ext"] or "mp3",
                    "download_headers_json": json.dumps({"user-agent": self.user_agent}, ensure_ascii=False),
                }
            )
        return items


class EmbeddedMusicBackend:
    provider_classes = {
        "JBSouMusicClient": JBSouProvider,
        "MyFreeMP3MusicClient": MyFreeMP3Provider,
        "MP3JuiceMusicClient": MP3JuiceProvider,
        MYFREEJUICES_PROVIDER: MyFreeMP3JuicesProvider,
    }
    default_source_names = [
        "JBSouMusicClient",
        "MyFreeMP3MusicClient",
        "MP3JuiceMusicClient",
    ]
    optional_source_names = [MYFREEJUICES_PROVIDER]

    def __init__(self, sources: list[str], auth_store: ProviderAuthStore | None = None) -> None:
        self.sources = sources
        self.auth_store = auth_store
        self.providers = {
            name: self.provider_classes[name](auth_store=self.auth_store)
            for name in sources
            if name in self.provider_classes
        }
        self.fallback = ITunesFallbackProvider()

    def _query_candidates(self, query: str) -> list[str]:
        variants = [query.strip()]
        compact = " ".join(query.split())
        if compact and compact not in variants:
            variants.append(compact)
        if " " in compact:
            tail = compact.split(" ")[-1].strip()
            if tail and tail not in variants:
                variants.append(tail)
        return variants

    def search(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        provider_limit = min(max(3, limit), 4)
        items: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()

        def run_provider(name: str) -> list[dict[str, Any]]:
            provider = self.providers.get(name)
            if provider is None:
                return []
            for candidate_query in self._query_candidates(query):
                try:
                    results = provider.search(candidate_query, limit=provider_limit)
                except Exception:
                    continue
                if results:
                    return results
            return []

        with ThreadPoolExecutor(max_workers=max(1, min(4, len(self.providers)))) as pool:
            future_map = {pool.submit(run_provider, name): name for name in self.sources if name in self.providers}
            for future in as_completed(future_map):
                try:
                    results = future.result()
                except Exception:
                    continue
                for item in results:
                    key = (
                        item.get("source", ""),
                        item.get("source_id", ""),
                        item.get("title", ""),
                        item.get("artists", ""),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(item)
        if not items:
            return self.fallback.search(query, limit=limit)
        return items[:limit]

    def probe_provider(self, source: str, query: str) -> dict[str, Any]:
        provider = self.providers.get(source)
        if provider is None:
            return {
                "provider": source,
                "probe_query": query,
                "status": "search_failed",
                "latency_ms": 0,
                "result_count": 0,
                "downloadable_count": 0,
                "error": "provider_not_supported",
                "sample_titles": [],
            }
        return provider.probe(query)

    def list_channels(self) -> dict[str, Any]:
        return {
            "backend": "embedded",
            "default_sources": list(self.default_source_names),
            "optional_sources": list(self.optional_source_names),
            "active_sources": list(self.providers.keys()),
            "fallback_search_only": ["ITunesFallback"],
            "notes": [
                "active_sources are the embedded providers implemented inside this skill",
                "fallback_search_only providers can supply recommendation candidates but not direct downloads",
                "optional_sources may require local auth state before they can be searched or downloaded",
            ],
        }

    def download(self, url: str, destination: Path, filename: str, headers_json: str = "") -> dict[str, Any]:
        headers = json.loads(headers_json) if headers_json else {}
        destination.mkdir(parents=True, exist_ok=True)
        out = destination / filename
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=60) as response, out.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 128)
                if not chunk:
                    break
                handle.write(chunk)
        return {"path": str(out), "status": "downloaded"}
