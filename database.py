"""
データベース操作モジュール
SQLiteを使用してEEWイベント・録画ファイル・システム設定を管理する
"""

import sqlite3
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 日本標準時
JST = timezone(timedelta(hours=9))


class Database:
    """SQLiteデータベース操作クラス"""

    def __init__(self, db_path: str = "data/24htsrecord.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """データベース接続を取得"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        """テーブルを初期化"""
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS eew_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurrence_time TEXT NOT NULL,
                    epicenter TEXT NOT NULL,
                    magnitude REAL,
                    max_intensity TEXT,
                    detail_url TEXT UNIQUE NOT NULL,
                    retention_hours REAL NOT NULL DEFAULT 5.0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                );

                CREATE TABLE IF NOT EXISTS recording_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT UNIQUE NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    reserve_id INTEGER,
                    is_protected INTEGER NOT NULL DEFAULT 0,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                );

                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            logger.info("データベースを初期化しました: %s", self.db_path)

    # ===== EEWイベント操作 =====

    def add_eew_event(self, occurrence_time: str, epicenter: str,
                      magnitude: Optional[float], max_intensity: Optional[str],
                      detail_url: str, retention_hours: float = 5.0) -> Optional[int]:
        """EEWイベントを追加する (重複はスキップ)"""
        try:
            with self._get_conn() as conn:
                cursor = conn.execute("""
                    INSERT OR IGNORE INTO eew_events
                    (occurrence_time, epicenter, magnitude, max_intensity, detail_url, retention_hours)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (occurrence_time, epicenter, magnitude, max_intensity, detail_url, retention_hours))
                if cursor.rowcount > 0:
                    logger.info("EEWイベントを登録: %s %s M%.1f 最大震度%s",
                                occurrence_time, epicenter,
                                magnitude or 0, max_intensity or "不明")
                    return cursor.lastrowid
                return None
        except sqlite3.Error as e:
            logger.error("EEWイベントの追加に失敗: %s", e)
            raise

    def get_all_eew_events(self) -> list[dict]:
        """全EEWイベントを取得"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM eew_events ORDER BY occurrence_time DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def get_active_eew_events(self) -> list[dict]:
        """保護期間がまだ有効なEEWイベントを取得"""
        with self._get_conn() as conn:
            # occurrence_time はタイムゾーン付ISO8601なので、Python側でフィルタリングする
            rows = conn.execute("SELECT * FROM eew_events ORDER BY occurrence_time DESC").fetchall()
            
            active_events = []
            now = datetime.now(JST)
            for row in rows:
                r = dict(row)
                try:
                    dt = datetime.fromisoformat(r['occurrence_time'])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=JST)
                    
                    retention = r.get('retention_hours', 5.0)
                    if dt + timedelta(hours=retention) > now:
                        active_events.append(r)
                except ValueError:
                    pass
            
            return active_events

    def extend_retention(self, event_id: int, hours: float) -> bool:
        """EEWイベントの保存時間を延長する"""
        try:
            with self._get_conn() as conn:
                cursor = conn.execute("""
                    UPDATE eew_events SET retention_hours = ? WHERE id = ?
                """, (hours, event_id))
                if cursor.rowcount > 0:
                    logger.info("EEWイベントID=%d の保存時間を %.1f 時間に変更", event_id, hours)
                    return True
                return False
        except sqlite3.Error as e:
            logger.error("保存時間の延長に失敗: %s", e)
            raise

    # ===== 録画ファイル操作 =====

    def add_recording_file(self, file_path: str, start_time: str,
                           end_time: str, reserve_id: int = 0) -> Optional[int]:
        """録画ファイルをDBに登録"""
        try:
            with self._get_conn() as conn:
                cursor = conn.execute("""
                    INSERT OR IGNORE INTO recording_files
                    (file_path, start_time, end_time, reserve_id)
                    VALUES (?, ?, ?, ?)
                """, (file_path, start_time, end_time, reserve_id))
                if cursor.rowcount > 0:
                    logger.info("録画ファイルを登録: %s", file_path)
                    return cursor.lastrowid
                return None
        except sqlite3.Error as e:
            logger.error("録画ファイルの登録に失敗: %s", e)
            raise

    def get_all_recording_files(self, include_deleted: bool = False) -> list[dict]:
        """録画ファイル一覧を取得"""
        with self._get_conn() as conn:
            if include_deleted:
                query = "SELECT * FROM recording_files ORDER BY start_time DESC"
            else:
                query = "SELECT * FROM recording_files WHERE deleted = 0 ORDER BY start_time DESC"
            rows = conn.execute(query).fetchall()
            return [dict(row) for row in rows]

    def mark_file_deleted(self, file_id: int) -> bool:
        """録画ファイルを削除済みとしてマーク"""
        try:
            with self._get_conn() as conn:
                cursor = conn.execute("""
                    UPDATE recording_files SET deleted = 1 WHERE id = ?
                """, (file_id,))
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error("ファイル削除マークに失敗: %s", e)
            raise

    def update_protection_status(self, file_id: int, is_protected: bool) -> bool:
        """録画ファイルの保護状態を更新"""
        try:
            with self._get_conn() as conn:
                cursor = conn.execute("""
                    UPDATE recording_files SET is_protected = ? WHERE id = ?
                """, (1 if is_protected else 0, file_id))
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error("保護状態の更新に失敗: %s", e)
            raise

    # ===== システム設定操作 =====

    def get_config(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """システム設定を取得"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key = ?", (key,)
            ).fetchone()
            return row['value'] if row else default

    def set_config(self, key: str, value: str):
        """システム設定を保存"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)
            """, (key, value))
