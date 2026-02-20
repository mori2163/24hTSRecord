# 24hTSRecord

24時間録画・緊急地震速報連動システム

EDCB (EpgDataCap_Bon) のAPIを利用して特定チャンネルの24時間連続録画を行い、気象庁の「緊急地震速報（警報）発表状況」ページを定期的に監視するシステムです。
緊急地震速報（EEW）が発表された場合は、発表時刻の前後（デフォルトで10分前から5時間後まで）の録画ファイルを自動的に保護し、発表がなかった時間帯の録画ファイルは自動削除します。

## 主な機能

- **24時間連続録画**: 1時間ごとにEDCBへ録画予約を自動登録し、指定チャンネルを24時間録画し続けます。
- **EEW連動保護**: 気象庁のWebページを定期的にスクレイピングし、EEW（警報）発表を検知すると、該当時間帯の録画ファイルを自動で保護対象に設定します。
- **自動クリーンアップ**: 保護対象外の古い録画ファイルは自動的に削除され、ディスク容量の圧迫を防ぎます。
- **Web GUI**: ブラウザからEEWイベントの履歴確認や、保護対象ファイルの保存期間延長などの操作が可能です。

## 動作環境

- **OS**: Windows
- **Python**: 3.11以上
- **EDCB**: [tkntrec/EDCB](https://github.com/tkntrec/EDCB) または xtne6f版 (EpgTimerSrvがTCP/IPまたはパイプ経由で外部からAPI制御可能な状態であること)

## インストール

1. リポジトリをクローンします。
   ```bash
   git clone https://github.com/yourusername/24hTSRecord.git
   cd 24hTSRecord
   ```

2. 依存パッケージをインストールします。
   ```bash
   pip install -r requirements.txt
   ```

3. 設定ファイルのテンプレートをコピーして、環境に合わせて編集します。
   ```bash
   cp config.example.json config.json
   ```

## 設定 (`config.json`)

`config.json` を編集して、EDCBの接続先や録画対象チャンネル、保存先ディレクトリなどを設定します。

```json
{
    "edcb": {
        "host": "192.168.11.100",
        "port": 4510
    },
    "channel": {
        "name": "NHK総合１",
        "onid": 0,
        "tsid": 0,
        "sid": 0,
        "tuner_id": 0
    },
    "recording": {
        "interval_minutes": 60,
        "output_directory": "C:\\Videos",
        "advance_reserve_count": 2
    },
    "eew": {
        "poll_interval_minutes": 60,
        "default_retention_hours": 5.0,
        "pre_buffer_minutes": 10
    },
    "web": {
        "host": "127.0.0.1",
        "port": 8080
    }
}
```

## 使い方

1. EDCB (EpgTimerSrv) が起動していることを確認します。
2. 本システムを起動します。
   ```bash
   python main.py
   ```
3. ブラウザで `http://127.0.0.1:8080` (設定したIPとポート) にアクセスすると、Web GUIが表示されます。

## 謝礼
xtne6f様の[EDCB.py](https://github.com/xtne6f/edcb.py/)(MIT License)を使わさせていただいています。ありがとうございます。
## ライセンス

MIT License
