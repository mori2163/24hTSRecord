"""
ファイル管理モジュール
録画済みTSファイルの保護判定・自動削除を行う
"""

import os
import logging
from datetime import datetime, timedelta

from database import Database, JST

logger = logging.getLogger(__name__)


class FileManager:
    """録画ファイル管理クラス"""

    def __init__(self, config: dict, db: Database):
        self.config = config
        self.db = db
        eew_conf = config.get("eew", {})
        self.pre_buffer_minutes = eew_conf.get("pre_buffer_minutes", 10)

    def _parse_time(self, time_str: str) -> datetime:
        """ISO8601文字列をdatetimeに変換"""
        dt = datetime.fromisoformat(time_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt

    def _can_confirm_file_missing(self, file_path: str) -> bool:
        """ファイル不存在を確定できる状態かどうかを判定する"""
        parent_dir = os.path.dirname(file_path)
        if not parent_dir:
            return True
        return os.path.isdir(parent_dir)

    def _is_recent_buffer(self, file_start: datetime) -> bool:
        """最近のファイル（EEW取得遅れを考慮して3時間前まで）かどうかを判定"""
        now = datetime.now(JST)
        # 3時間前
        recent_threshold = now - timedelta(hours=3)
        return file_start >= recent_threshold

    def should_protect(self, file_start: datetime, file_end: datetime) -> bool:
        """
        ファイルが保護対象かどうかを判定する

        保護条件:
        1. 最近のファイル（EEW取得遅れを考慮して3時間前まで）
        2. EEWイベントの保護時間範囲と重なる
        """
        # 条件1: 最近のファイル（3時間前まで）
        if self._is_recent_buffer(file_start):
            return True

        # 条件2: EEWイベントの保護範囲と重なるか
        active_events = self.db.get_active_eew_events()
        for event in active_events:
            eew_time = self._parse_time(event['occurrence_time'])
            retention_hours = event.get('retention_hours', 5.0)

            protect_start = eew_time - timedelta(minutes=self.pre_buffer_minutes)
            protect_end = eew_time + timedelta(hours=retention_hours)

            # ファイルの時間範囲と保護範囲が重なるか
            if file_start < protect_end and file_end > protect_start:
                return True

        return False

    def update_protection_flags(self):
        """全ファイルの保護フラグを更新する"""
        files = self.db.get_all_recording_files(include_deleted=False)
        protected_count = 0
        unprotected_count = 0

        for f in files:
            try:
                file_start = self._parse_time(f['start_time'])
                file_end = self._parse_time(f['end_time'])
                is_protected = self.should_protect(file_start, file_end)

                if is_protected != bool(f['is_protected']):
                    self.db.update_protection_status(f['id'], is_protected)

                if is_protected:
                    protected_count += 1
                else:
                    unprotected_count += 1

            except (ValueError, KeyError) as e:
                logger.warning("ファイル保護判定エラー (id=%d): %s", f.get('id', 0), e)

        logger.info("保護状態を更新: 保護=%d件, 非保護=%d件", protected_count, unprotected_count)

    def recover_inconsistent_deleted_flags(self) -> int:
        """DBのdeletedフラグ不整合（deleted=1だが実ファイルあり）を復旧する"""
        files = self.db.get_all_recording_files(include_deleted=True)
        recovered_count = 0

        for f in files:
            if not bool(f.get('deleted')):
                continue

            file_path = f.get('file_path', '')
            if not file_path or not os.path.exists(file_path):
                continue

            file_id = f.get('id')
            if file_id is None:
                continue

            # 物理ファイルが存在するため、deletedフラグを戻して再評価対象にする
            self.db.set_file_deleted(file_id, False)

            try:
                file_start = self._parse_time(f['start_time'])
                file_end = self._parse_time(f['end_time'])
                is_protected = self.should_protect(file_start, file_end)
                self.db.update_protection_status(file_id, is_protected)
            except (ValueError, KeyError) as e:
                logger.warning("復旧時の保護判定エラー (id=%d): %s", file_id, e)

            recovered_count += 1
            logger.warning("deletedフラグ不整合を復旧: %s", file_path)

        if recovered_count > 0:
            logger.info("deletedフラグ不整合を %d 件復旧しました", recovered_count)

        return recovered_count

    def delete_unprotected_files(self) -> int:
        """保護対象外の古いファイルを削除する"""
        files = self.db.get_all_recording_files(include_deleted=False)
        deleted_count = 0

        for f in files:
            if f['is_protected']:
                continue

            file_path = f['file_path']
            try:
                file_start = self._parse_time(f['start_time'])
                file_end = self._parse_time(f['end_time'])

                # 再度保護判定（念のため）
                if self.should_protect(file_start, file_end):
                    self.db.update_protection_status(f['id'], True)
                    continue

                # ファイルを物理削除
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info("ファイルを削除: %s", file_path)
                else:
                    if not self._can_confirm_file_missing(file_path):
                        logger.warning(
                            "削除を保留: 保存先パスが未接続/未準備の可能性があります。次サイクルで再試行します: %s",
                            file_path,
                        )
                        continue

                    logger.debug("ファイルが見つからないため削除済みとして扱います: %s", file_path)

                # DBで削除済みマーク
                self.db.mark_file_deleted(f['id'])
                deleted_count += 1

            except OSError as e:
                logger.error("ファイル削除に失敗: %s - %s", file_path, e)
            except (ValueError, KeyError) as e:
                logger.warning("ファイル処理エラー (id=%d): %s", f.get('id', 0), e)

        if deleted_count > 0:
            logger.info("%d件のファイルを削除しました", deleted_count)
        else:
            logger.debug("削除対象のファイルはありません")

        return deleted_count

    def run_cycle(self) -> int:
        """1サイクル分の処理を実行"""
        logger.info("ファイル管理サイクルを開始")
        recovered = self.recover_inconsistent_deleted_flags()
        self.update_protection_flags()
        deleted = self.delete_unprotected_files()
        logger.info("ファイル管理サイクルを完了 (復旧: %d件, 削除: %d件)", recovered, deleted)
        return deleted
