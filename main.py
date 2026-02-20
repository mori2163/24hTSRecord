"""
24時間録画・緊急地震速報連動システム
エントリーポイント - 全モジュールを起動し、asyncioタスクループで並行実行する
"""

import asyncio
import json
import sys
import os
import logging
import logging.handlers
from pathlib import Path

import uvicorn

from database import Database
from recorder import Recorder
from eew_monitor import EEWMonitor
from file_manager import FileManager
from web.api import create_app


def setup_logging():
    """ロギングの設定"""
    # dataディレクトリの作成
    os.makedirs("data", exist_ok=True)

    # ルートロガーの設定
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # フォーマット
    formatter = logging.Formatter(
        '[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # コンソール出力
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # ファイル出力（ローテーション: 5MB × 5ファイル）
    file_handler = logging.handlers.RotatingFileHandler(
        "data/app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # uvicornのログレベルを調整
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def load_config() -> dict:
    """設定ファイルを読み込む"""
    config_path = Path("config.json")
    if not config_path.exists():
        logging.error("config.json が見つかりません")
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    logging.info("設定ファイルを読み込みました: %s", config_path)
    return config


async def recording_loop(recorder: Recorder, interval_minutes: int):
    """録画管理の定期実行ループ"""
    logger = logging.getLogger("recording_loop")
    while True:
        try:
            await recorder.run_cycle()
        except Exception as e:
            logger.error("録画管理サイクルでエラー: %s", e, exc_info=True)

        await asyncio.sleep(interval_minutes * 60)


async def eew_monitor_loop(eew_monitor: EEWMonitor, interval_minutes: int):
    """EEW監視の定期実行ループ"""
    logger = logging.getLogger("eew_monitor_loop")
    while True:
        try:
            # EEWMonitorは同期処理なのでスレッドで実行
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, eew_monitor.run_cycle)
        except Exception as e:
            logger.error("EEW監視サイクルでエラー: %s", e, exc_info=True)

        await asyncio.sleep(interval_minutes * 60)


async def file_manager_loop(file_manager: FileManager, interval_minutes: int):
    """ファイル管理の定期実行ループ"""
    logger = logging.getLogger("file_manager_loop")
    # 最初のサイクルは少し遅らせる（録画管理が先に動いてから）
    await asyncio.sleep(30)

    while True:
        try:
            file_manager.run_cycle()
        except Exception as e:
            logger.error("ファイル管理サイクルでエラー: %s", e, exc_info=True)

        await asyncio.sleep(interval_minutes * 60)


async def main():
    """メインエントリーポイント"""
    # ロギング設定
    setup_logging()
    logger = logging.getLogger("main")
    logger.info("=" * 60)
    logger.info("24hTSRecord — 24時間録画・緊急地震速報連動システム")
    logger.info("=" * 60)

    # 設定読み込み
    config = load_config()

    # データベース初期化
    db = Database()

    # 各モジュールの初期化
    recorder = Recorder(config, db)
    eew_monitor = EEWMonitor(config, db)
    file_manager = FileManager(config, db)

    # FastAPIアプリの作成
    app = create_app(config, db, recorder)

    # Web設定
    web_conf = config.get("web", {})
    host = web_conf.get("host", "127.0.0.1")
    port = web_conf.get("port", 8080)

    # 録画設定
    rec_interval = config.get("recording", {}).get("interval_minutes", 60)
    eew_interval = config.get("eew", {}).get("poll_interval_minutes", 60)
    file_interval = rec_interval  # ファイル管理は録画間隔と同じ

    logger.info("対象チャンネル: %s (ONID=%d, TSID=%d, SID=%d)",
                config.get("channel", {}).get("name", "不明"),
                config.get("channel", {}).get("onid", 0),
                config.get("channel", {}).get("tsid", 0),
                config.get("channel", {}).get("sid", 0))
    logger.info("録画間隔: %d分, EEW監視間隔: %d分", rec_interval, eew_interval)
    
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    logger.info("Web GUI: http://%s:%d", display_host, port)

    # Uvicornサーバーの設定
    uvicorn_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(uvicorn_config)

    # 全タスクを並行実行
    logger.info("全モジュールを起動します...")
    await asyncio.gather(
        server.serve(),
        recording_loop(recorder, rec_interval),
        eew_monitor_loop(eew_monitor, eew_interval),
        file_manager_loop(file_manager, file_interval),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("システムを終了します")
    except Exception as e:
        logging.error("致命的なエラー: %s", e, exc_info=True)
        sys.exit(1)
