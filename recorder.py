"""
録画管理モジュール
EDCBへの1時間ごとのプログラム予約登録・管理を行う
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

from edcb import CtrlCmdUtil
from database import Database, JST

logger = logging.getLogger(__name__)


class Recorder:
    """EDCB録画予約管理クラス"""

    # 予約タイトルのプレフィックス（このシステムが登録した予約を識別するために使用）
    TITLE_PREFIX = "24h自動録画_"

    def __init__(self, config: dict, db: Database):
        self.config = config
        self.db = db
        self.edcb = CtrlCmdUtil()
        self.edcb.setConnectTimeOutSec(10)

        # EDCB接続先の設定
        edcb_conf = config.get("edcb", {})
        host = edcb_conf.get("host", "127.0.0.1")
        port = edcb_conf.get("port", 4510)
        self.edcb.setNWSetting(host, port)

        # チャンネル設定
        ch = config.get("channel", {})
        self.onid = ch.get("onid", 0)
        self.tsid = ch.get("tsid", 0)
        self.sid = ch.get("sid", 0)
        self.station_name = ch.get("name", "")
        self.tuner_id = ch.get("tuner_id", 0)

        # 録画設定
        rec_conf = config.get("recording", {})
        self.interval_minutes = rec_conf.get("interval_minutes", 60)
        self.advance_count = rec_conf.get("advance_reserve_count", 2)

    def _make_title(self, start_time: datetime) -> str:
        """予約タイトルを生成"""
        return f"{self.TITLE_PREFIX}{start_time.strftime('%Y%m%d_%H%M')}"

    def _make_reserve_data(self, start_time: datetime) -> dict:
        """予約データ（ReserveData辞書）を生成"""
        return {
            'title': self._make_title(start_time),
            'start_time': start_time,
            'duration_second': self.interval_minutes * 60,
            'station_name': self.station_name,
            'onid': self.onid,
            'tsid': self.tsid,
            'sid': self.sid,
            'eid': 0xFFFF,  # プログラム予約（EPG不使用）
            'rec_setting': {
                'rec_mode': 1,  # 指定サービス
                'priority': 2,
                'tuijyuu_flag': False,
                'service_mode': 0,
                'pittari_flag': False,
                'continue_rec_flag': False,
                'partial_rec_flag': 0,
                'tuner_id': self.tuner_id,
            }
        }

    def _get_next_slot_times(self, now: datetime) -> list[datetime]:
        """次に予約すべき時間枠のリストを返す（advance_count分）"""
        # 現在の時間枠の開始時刻（毎時00分）
        current_slot = now.replace(minute=0, second=0, microsecond=0)
        if now.minute >= 55:
            # 55分を過ぎていたら次の枠から
            current_slot += timedelta(hours=1)

        slots = []
        for i in range(self.advance_count + 1):
            slots.append(current_slot + timedelta(hours=i))
        return slots

    async def ensure_reservations(self):
        """次の予約枠が確保されているか確認し、不足分を追加する"""
        now = datetime.now(JST)
        needed_slots = self._get_next_slot_times(now)

        try:
            # 現在の予約一覧を取得
            existing_reserves = await self.edcb.sendEnumReserve()
            if existing_reserves is None:
                logger.error("予約一覧の取得に失敗しました（EDCB接続エラー）")
                return

            # このシステムが登録した予約のタイトルを抽出
            our_titles = set()
            for r in existing_reserves:
                title = r.get('title', '')
                if title.startswith(self.TITLE_PREFIX):
                    our_titles.add(title)

            # 不足している予約を追加
            new_reserves = []
            for slot_time in needed_slots:
                title = self._make_title(slot_time)
                if title not in our_titles:
                    reserve = self._make_reserve_data(slot_time)
                    new_reserves.append(reserve)
                    logger.info("予約を追加予定: %s (%s)", title, slot_time.isoformat())

            if new_reserves:
                success = await self.edcb.sendAddReserve(new_reserves)
                if success:
                    logger.info("%d件の予約を追加しました", len(new_reserves))
                else:
                    logger.error("予約の追加に失敗しました")
            else:
                logger.debug("追加が必要な予約はありません")

        except Exception as e:
            logger.error("予約確認中にエラー: %s", e)

    async def check_completed_recordings(self):
        """録画完了したファイルの情報をDBに記録する"""
        try:
            rec_info_list = await self.edcb.sendEnumRecInfoBasic()
            if rec_info_list is None:
                logger.error("録画済み情報の取得に失敗しました")
                return

            for rec_info in rec_info_list:
                title = rec_info.get('title', '')
                if not title.startswith(self.TITLE_PREFIX):
                    continue

                file_path = rec_info.get('rec_file_path', '')
                if not file_path:
                    continue

                start_time = rec_info.get('start_time')
                duration = rec_info.get('duration_sec', 3600)
                if start_time:
                    end_time = start_time + timedelta(seconds=duration)
                    self.db.add_recording_file(
                        file_path=file_path,
                        start_time=start_time.isoformat(),
                        end_time=end_time.isoformat(),
                        reserve_id=rec_info.get('id', 0)
                    )

        except Exception as e:
            logger.error("録画完了チェック中にエラー: %s", e)

    async def cleanup_old_reserves(self):
        """終了済みの古い予約を削除する"""
        try:
            existing_reserves = await self.edcb.sendEnumReserve()
            if existing_reserves is None:
                return

            now = datetime.now(JST)
            old_ids = []
            for r in existing_reserves:
                title = r.get('title', '')
                if not title.startswith(self.TITLE_PREFIX):
                    continue
                start = r.get('start_time')
                duration = r.get('duration_second', 3600)
                if start and start + timedelta(seconds=duration) < now - timedelta(hours=2):
                    old_ids.append(r.get('reserve_id', 0))

            if old_ids:
                success = await self.edcb.sendDelReserve(old_ids)
                if success:
                    logger.info("%d件の古い予約を削除しました", len(old_ids))

        except Exception as e:
            logger.error("古い予約の削除中にエラー: %s", e)

    def update_config(self, config: dict):
        """設定を更新する"""
        self.config = config
        
        # EDCB接続先の設定
        edcb_conf = config.get("edcb", {})
        host = edcb_conf.get("host", "127.0.0.1")
        port = edcb_conf.get("port", 4510)
        self.edcb.setNWSetting(host, port)

        # チャンネル設定
        ch = config.get("channel", {})
        self.onid = ch.get("onid", 0)
        self.tsid = ch.get("tsid", 0)
        self.sid = ch.get("sid", 0)
        self.station_name = ch.get("name", "")
        self.tuner_id = ch.get("tuner_id", 0)

        # 録画設定
        rec_conf = config.get("recording", {})
        self.interval_minutes = rec_conf.get("interval_minutes", 60)
        self.advance_count = rec_conf.get("advance_reserve_count", 2)

    async def run_cycle(self):
        """1サイクル分の処理を実行する"""
        logger.info("録画管理サイクルを開始")
        
        # チャンネル設定とチューナー設定が正しく行われているか確認
        if not self.station_name or self.onid == 0 or self.tsid == 0 or self.sid == 0 or self.tuner_id == 0:
            logger.warning("チャンネルまたはチューナーの初期設定が未完了のため、予約処理をスキップします。Web UIから設定を行ってください。")
            return
            
        await self.ensure_reservations()
        await self.check_completed_recordings()
        await self.cleanup_old_reserves()
        logger.info("録画管理サイクルを完了")
