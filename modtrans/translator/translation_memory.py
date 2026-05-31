"""翻译记忆库 — 持久化翻译结果，避免重复调用 AI。

双层存储：
1. SQLite — 快速查询，键为 en_text 的 SHA-256 哈希
2. JSON — 纯 ``{英文: 中文}`` 字典，方便人工查看和编辑

翻译流程中的位置：
    mods → 解析 en_us → [翻译记忆库查重] → AI 翻译 → [写入记忆库] → 输出
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memory (
    en_hash TEXT PRIMARY KEY,
    en_text TEXT NOT NULL,
    zh_text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'ai',
    created_at REAL NOT NULL
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_memory_source
ON memory(source);
"""

_CREATE_META_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class TranslationMemory:
    """翻译记忆库 — 查询已有翻译，自动积累翻译成果。

    Usage::

        memory = TranslationMemory()
        memory.open()

        # 翻译前查重
        hits = memory.lookup_batch(english_entries)
        # hits = {key: zh_text, ...}  命中的直接复用

        # 翻译后写入
        memory.remember_batch(translations, source="ai")

        # 导出 JSON 供人工查看
        memory.export_json()

        memory.close()
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is None:
            data_dir = Path.home() / ".config" / "modtrans"
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._data_dir / "translation_memory.db"
        self._json_path = self._data_dir / "translation_memory.json"
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # 上下文管理器
    # ------------------------------------------------------------------

    def __enter__(self) -> "TranslationMemory":
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def open(self) -> None:
        """打开翻译记忆数据库。

        如果数据库文件不存在，延迟创建——只有首次写入时才建表。
        避免在未使用 AI 翻译时凭空出现空数据库文件。
        """
        db_exists = self._db_path.is_file()
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        if db_exists:
            # 已有数据库，确保表结构完整
            self._conn.execute(_CREATE_TABLE_SQL)
            self._conn.execute(_CREATE_INDEX_SQL)
            self._conn.execute(_CREATE_META_SQL)
            self._conn.commit()

    def close(self) -> None:
        """关闭数据库连接。"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_tables(self) -> None:
        """确保数据库表存在（延迟创建，首次写入时调用）。"""
        self.conn.execute(_CREATE_TABLE_SQL)
        self.conn.execute(_CREATE_INDEX_SQL)
        self.conn.execute(_CREATE_META_SQL)
        self.conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("TranslationMemory 未打开。请使用上下文管理器或调用 .open()")
        return self._conn

    # ------------------------------------------------------------------
    # JSON 同步检测
    # ------------------------------------------------------------------

    @property
    def json_path(self) -> Path:
        """JSON 记忆文件路径。"""
        return self._json_path

    def check_json_sync(self) -> bool:
        """检测 JSON 文件是否比数据库更新（用户手动编辑过）。

        Returns:
            True 表示 JSON 有新内容需要同步。
        """
        if not self._json_path.is_file():
            return False

        self._ensure_tables()

        json_mtime = self._json_path.stat().st_mtime
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'json_sync_mtime'"
        ).fetchone()
        last_sync = float(row[0]) if row else 0.0

        return json_mtime > last_sync + 1.0  # 1秒容差

    def _record_json_sync(self) -> None:
        """记录 JSON 同步时间。"""
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('json_sync_mtime', ?)",
            (str(time.time()),),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def lookup(self, en_text: str) -> Optional[str]:
        """查单个英文文本的翻译。

        Returns:
            中文翻译，如果没找到返回 None。
        """
        self._ensure_tables()
        key = _hash(en_text)
        row = self.conn.execute(
            "SELECT zh_text FROM memory WHERE en_hash = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def lookup_batch(self, entries: dict[str, str]) -> dict[str, str]:
        """批量查找翻译。

        Args:
            entries: {lang_key: en_text} 待查找的条目。

        Returns:
            {lang_key: zh_text} 已命中的翻译，未命中的不在结果中。
        """
        if not entries:
            return {}

        self._ensure_tables()

        # 收集所有 en_text 并计算哈希
        en_texts = list(entries.values())
        hashes = [_hash(t) for t in en_texts]

        # 批量查询
        placeholders = ",".join("?" * len(hashes))
        rows = self.conn.execute(
            f"SELECT en_hash, zh_text FROM memory WHERE en_hash IN ({placeholders})",
            hashes,
        ).fetchall()

        # 构建 en_hash → zh_text 映射
        hash_to_zh = {row[0]: row[1] for row in rows}

        # 反查：en_text → lang_key
        text_to_keys: dict[str, list[str]] = {}
        for key, text in entries.items():
            text_to_keys.setdefault(text, []).append(key)

        # 构建结果
        result: dict[str, str] = {}
        for en_text, lang_keys in text_to_keys.items():
            zh = hash_to_zh.get(_hash(en_text))
            if zh is not None:
                for k in lang_keys:
                    result[k] = zh

        return result

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def remember(self, en_text: str, zh_text: str, source: str = "ai") -> None:
        """记住一条翻译。如果已存在则更新。"""
        self._ensure_tables()
        self.conn.execute(
            """INSERT OR REPLACE INTO memory
               (en_hash, en_text, zh_text, source, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (_hash(en_text), en_text, zh_text, source, time.time()),
        )
        self.conn.commit()

    def remember_batch(
        self, translations: dict[str, str], source: str = "ai"
    ) -> int:
        """批量写入翻译记忆。

        Args:
            translations: {en_text: zh_text} 或 {lang_key: zh_text}。
            source: 来源标识 (ai, i18n, manual)。

        Returns:
            实际写入的条目数。
        """
        if not translations:
            return 0

        self._ensure_tables()

        now = time.time()
        data = [
            (_hash(en_text), en_text, zh_text, source, now)
            for en_text, zh_text in translations.items()
            if en_text and zh_text
        ]

        if not data:
            return 0

        self.conn.executemany(
            """INSERT OR REPLACE INTO memory
               (en_hash, en_text, zh_text, source, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            data,
        )
        self.conn.commit()
        return len(data)

    # ------------------------------------------------------------------
    # JSON 导出 / 导入
    # ------------------------------------------------------------------

    def export_json(self) -> Path:
        """将全部记忆导出为 JSON 文件，方便人工查看和编辑。

        Returns:
            导出的 JSON 文件路径。
        """
        self._ensure_tables()
        rows = self.conn.execute(
            "SELECT en_text, zh_text FROM memory ORDER BY en_text"
        ).fetchall()

        data = {en_text: zh_text for en_text, zh_text in rows}

        self._json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._record_json_sync()
        logger.info("翻译记忆已导出 %d 条到 %s", len(data), self._json_path)
        return self._json_path

    def import_json(self) -> int:
        """从 JSON 文件导入翻译记忆（人工编辑后重新导入）。

        只添加数据库中不存在的条目，不会覆盖已有翻译。

        Returns:
            新导入的条目数。
        """
        if not self._json_path.is_file():
            logger.info("JSON 记忆文件不存在: %s", self._json_path)
            return 0

        self._ensure_tables()

        try:
            data = json.loads(self._json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("JSON 记忆文件解析失败: %s", e)
            return 0

        if not isinstance(data, dict):
            logger.warning("JSON 记忆文件格式错误: 应为对象")
            return 0

        # 只导入不存在的条目
        existing_hashes = set()
        all_hashes = [_hash(en) for en in data.keys()]
        # 分批查询
        batch_size = 500
        for i in range(0, len(all_hashes), batch_size):
            batch = all_hashes[i : i + batch_size]
            placeholders = ",".join("?" * len(batch))
            rows = self.conn.execute(
                f"SELECT en_hash FROM memory WHERE en_hash IN ({placeholders})",
                batch,
            ).fetchall()
            existing_hashes.update(row[0] for row in rows)

        new_count = 0
        now = time.time()
        for en_text, zh_text in data.items():
            if not en_text or not zh_text:
                continue
            h = _hash(en_text)
            if h not in existing_hashes:
                self.conn.execute(
                    """INSERT OR IGNORE INTO memory
                       (en_hash, en_text, zh_text, source, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (h, en_text, str(zh_text), "manual", now),
                )
                new_count += 1

        self.conn.commit()
        self._record_json_sync()
        logger.info("从 JSON 导入 %d 条新记忆", new_count)
        return new_count

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """返回记忆库统计信息。"""
        try:
            total = self.conn.execute(
                "SELECT COUNT(*) FROM memory"
            ).fetchone()[0]
            by_source = self.conn.execute(
                "SELECT source, COUNT(*) FROM memory GROUP BY source"
            ).fetchall()
        except sqlite3.OperationalError:
            # 表尚未创建（从未写入过）
            total = 0
            by_source = []

        return {
            "total": total,
            "by_source": dict(by_source),
            "db_path": str(self._db_path),
            "json_path": str(self._json_path),
        }


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _hash(text: str) -> str:
    """计算文本的 SHA-256 哈希（用于主键）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
