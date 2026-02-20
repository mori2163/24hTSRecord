"""
Web API モジュール
FastAPIを使用したREST APIとWebフロントエンドのサーバー
"""

import json
import logging
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Any

from database import Database, JST
from recorder import Recorder

logger = logging.getLogger(__name__)

# Pydanticモデル
class ExtendRetentionRequest(BaseModel):
    hours: float

class ConfigUpdateRequest(BaseModel):
    key: str
    value: Any


def create_app(config: dict, db: Database, recorder: Recorder) -> FastAPI:
    """FastAPIアプリケーションを生成する"""

    app = FastAPI(title="24hTSRecord", version="1.0.0")

    # 静的ファイル配信
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # --- エンドポイント ---

    @app.get("/")
    async def index():
        """メインページを返す"""
        index_path = static_dir / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return JSONResponse({"error": "index.html not found"}, status_code=404)

    @app.get("/api/status")
    async def get_status():
        """システム状態を取得"""
        now = datetime.now(JST)
        files = db.get_all_recording_files(include_deleted=False)
        active_events = db.get_active_eew_events()

        # 現在録画中のファイルを特定
        current_recording = None
        for f in files:
            try:
                start = datetime.fromisoformat(f['start_time'])
                end = datetime.fromisoformat(f['end_time'])
                if start.tzinfo is None:
                    start = start.replace(tzinfo=JST)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=JST)
                if start <= now <= end:
                    current_recording = f
                    break
            except (ValueError, KeyError):
                continue

        return {
            "status": "recording" if current_recording else "waiting",
            "current_time": now.isoformat(),
            "current_recording": current_recording,
            "total_files": len(files),
            "protected_files": sum(1 for f in files if f.get('is_protected')),
            "active_eew_events": len(active_events),
            "channel": config.get("channel", {}),
        }

    @app.get("/api/eew_events")
    async def get_eew_events():
        """EEWイベント一覧を取得"""
        events = db.get_all_eew_events()
        return {"events": events, "total": len(events)}

    @app.post("/api/eew_events/{event_id}/extend")
    async def extend_retention(event_id: int, req: ExtendRetentionRequest):
        """EEWイベントの保存時間を延長する"""
        if req.hours <= 0:
            raise HTTPException(status_code=400, detail="延長時間は正の値で指定してください")

        success = db.extend_retention(event_id, req.hours)
        if not success:
            raise HTTPException(status_code=404, detail="指定されたEEWイベントが見つかりません")

        return {"success": True, "event_id": event_id, "new_retention_hours": req.hours}

    @app.get("/api/recordings")
    async def get_recordings():
        """録画ファイル一覧を取得"""
        files = db.get_all_recording_files(include_deleted=False)
        return {"recordings": files, "total": len(files)}

    @app.get("/api/config")
    async def get_config():
        """現在の設定を取得"""
        return config

    @app.put("/api/config")
    async def update_config(req: ConfigUpdateRequest):
        """設定を変更する"""
        # メモリ上の設定を更新
        config[req.key] = req.value
        
        # config.json に保存
        config_path = Path("config.json")
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error("設定ファイルの保存に失敗: %s", e)
            raise HTTPException(status_code=500, detail="設定ファイルの保存に失敗しました")

        # DBにも保存 (文字列として)
        db.set_config(req.key, json.dumps(req.value, ensure_ascii=False))
        
        # Recorderの設定も更新
        recorder.update_config(config)
        
        return {"success": True, "key": req.key, "value": req.value}

    @app.get("/api/edcb/services")
    async def get_services():
        """EDCBのサービス（チャンネル）一覧を取得"""
        try:
            services = await recorder.edcb.sendEnumService()
            if services is None:
                raise HTTPException(status_code=500, detail="サービス一覧の取得に失敗しました")
            # 必要な情報だけ抽出
            result = []
            for s in services:
                result.append({
                    "name": s.get("service_name"),
                    "onid": s.get("onid"),
                    "tsid": s.get("tsid"),
                    "sid": s.get("sid")
                })
            return {"services": result}
        except Exception as e:
            logger.error("Error getting services: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/edcb/tuners")
    async def get_tuners():
        """EDCBのチューナー一覧を取得"""
        try:
            tuners = await recorder.edcb.sendEnumTunerReserve()
            if tuners is None:
                raise HTTPException(status_code=500, detail="チューナー一覧の取得に失敗しました")
            result = []
            for t in tuners:
                result.append({
                    "id": t.get("tuner_id"),
                    "name": t.get("tuner_name")
                })
            return {"tuners": result}
        except Exception as e:
            logger.error("Error getting tuners: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    return app
