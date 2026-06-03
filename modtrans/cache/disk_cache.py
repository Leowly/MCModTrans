"""SQLite-backed disk cache to avoid re-parsing unchanged JARs.

Cache key: SHA-256 hex digest of the JAR file.
Cache value: JSON-serialized ModAssets.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from ..models import GameVersion, ModAssets, ModMetadata

logger = logging.getLogger(__name__)

# Current schema version — bump when ModAssets structure changes
_SCHEMA_VERSION = 1

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS parsed_jars (
    jar_hash TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    mod_assets_json TEXT NOT NULL,
    jar_name TEXT NOT NULL,
    jar_size INTEGER NOT NULL,
    parsed_at REAL NOT NULL
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_parsed_at
ON parsed_jars(parsed_at);
"""


class DiskCache:
    """SQLite-based JAR parse result cache.

    Usage::

        cache = DiskCache(Path(".cache/modtrans"))
        assets = cache.get("abc123...")  # Returns ModAssets or None
        cache.put("abc123...", assets)
    """

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._cache_dir / "cache.db"
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "DiskCache":
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open (or create) the cache database."""
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.execute(_CREATE_INDEX_SQL)
        self._conn.commit()

    def close(self) -> None:
        """Close the cache database."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("DiskCache 未打开。请使用上下文管理器或调用 .open()")
        return self._conn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, jar_hash: str) -> Optional[ModAssets]:
        """Retrieve cached ModAssets by JAR SHA-256 hash.

        Args:
            jar_hash: SHA-256 hex digest of the JAR file.

        Returns:
            ModAssets if found and schema matches, None otherwise.
        """
        row = self.conn.execute(
            "SELECT schema_version, mod_assets_json FROM parsed_jars WHERE jar_hash = ?",
            (jar_hash,),
        ).fetchone()

        if row is None:
            return None

        schema_version, json_str = row
        if schema_version != _SCHEMA_VERSION:
            logger.debug("缓存结构版本不匹配 %s, 作废旧缓存", jar_hash)
            self.conn.execute(
                "DELETE FROM parsed_jars WHERE jar_hash = ?", (jar_hash,)
            )
            self.conn.commit()
            return None

        try:
            return _deserialize_mod_assets(json.loads(json_str))
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("缓存条目损坏 %s: %s", jar_hash, e)
            return None

    def put(self, jar_hash: str, mod_assets: ModAssets) -> None:
        """Store ModAssets in the cache.

        Uses INSERT OR REPLACE to handle duplicate hashes (e.g., if the
        same JAR is re-parsed with a newer version of the tool).
        """
        jar_name = mod_assets.jar_path.name if mod_assets.jar_path else "unknown"
        jar_size = (
            mod_assets.jar_path.stat().st_size if mod_assets.jar_path else 0
        )

        json_data = _serialize_mod_assets(mod_assets)
        json_str = json.dumps(json_data, ensure_ascii=False)

        self.conn.execute(
            """INSERT OR REPLACE INTO parsed_jars
               (jar_hash, schema_version, mod_assets_json, jar_name, jar_size, parsed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (jar_hash, _SCHEMA_VERSION, json_str, jar_name, jar_size, time.time()),
        )
        self.conn.commit()

    def clear(self) -> int:
        """Clear all cached entries.

        Returns:
            Number of entries removed.
        """
        cursor = self.conn.execute("DELETE FROM parsed_jars")
        self.conn.commit()
        return cursor.rowcount

    def stats(self) -> dict:
        """Return cache statistics."""
        row = self.conn.execute(
            "SELECT COUNT(*), SUM(jar_size) FROM parsed_jars"
        ).fetchone()
        return {
            "entries": row[0] or 0,
            "total_size_bytes": row[1] or 0,
            "db_path": str(self._db_path),
        }

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def hash_jar(jar_path: Path) -> str:
        """Compute SHA-256 hex digest of a JAR file.

        Reads the file in chunks to support large JARs.
        """
        sha256 = hashlib.sha256()
        with open(jar_path, "rb") as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        return sha256.hexdigest()


# ---------------------------------------------------------------------------
# Serialization helpers (ModAssets ↔ JSON-safe dict)
# ---------------------------------------------------------------------------

def _serialize_mod_assets(assets: ModAssets) -> dict:
    """Convert ModAssets to a JSON-serializable dictionary."""
    return {
        "modid": assets.modid,
        "game_version": assets.game_version.value,
        "english_entries": assets.english_entries,
        "chinese_entries": assets.chinese_entries,
        "existing_chinese": assets.existing_chinese,
        "metadata": {
            "modid": assets.metadata.modid,
            "name": assets.metadata.name,
            "version": assets.metadata.version,
            "author": assets.metadata.author,
            "description": assets.metadata.description,
            "game_version": assets.metadata.game_version,
            "credits": assets.metadata.credits,
            "url": assets.metadata.url,
        },
        "jar_path": str(assets.jar_path) if assets.jar_path else None,
        "source_encoding": assets.source_encoding,
    }


def _deserialize_mod_assets(data: dict) -> ModAssets:
    """Reconstruct ModAssets from a deserialized JSON dict."""
    meta = data.get("metadata", {})
    jar_path_str = data.get("jar_path")
    return ModAssets(
        modid=data["modid"],
        game_version=GameVersion(data["game_version"]),
        english_entries=data.get("english_entries", {}),
        chinese_entries=data.get("chinese_entries", {}),
        existing_chinese=data.get("existing_chinese", {}),
        metadata=ModMetadata(
            modid=meta.get("modid", ""),
            name=meta.get("name", ""),
            version=meta.get("version", ""),
            author=meta.get("author", ""),
            description=meta.get("description", ""),
            game_version=meta.get("game_version", ""),
            credits=meta.get("credits", ""),
            url=meta.get("url", ""),
        ),
        jar_path=Path(jar_path_str) if jar_path_str else None,
        source_encoding=data.get("source_encoding", "utf-8"),
    )
