"""
EEW監視モジュール
気象庁の「緊急地震速報（警報）発表状況」ページを定期的にスクレイピングし、
新しいEEWイベントをデータベースに登録する
"""

import re
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Optional

from database import Database, JST

logger = logging.getLogger(__name__)

# 気象庁EEW発表状況ページURL
JMA_EEW_URL = "https://www.data.jma.go.jp/eew/data/nc/pub_hist/index.html"

# EEW詳細URL内の日時パターン: YYYYMMDDHHmmSS
EEW_DATETIME_PATTERN = re.compile(r'/(\d{14})/')


class EEWMonitor:
    """緊急地震速報監視クラス"""

    def __init__(self, config: dict, db: Database):
        self.config = config
        self.db = db
        eew_conf = config.get("eew", {})
        self.default_retention_hours = eew_conf.get("default_retention_hours", 5.0)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) 24hTSRecord/1.0'
        })

    def _parse_eew_page(self, html: str) -> list[dict]:
        """
        JMAのEEW発表状況ページをパースし、EEWイベントのリストを返す

        テーブル構造:
        <table id="event_list">
          <tr>
            <td>発生日時</td>
            <td>震央地名</td>
            <td>M</td>
            <td>最大震度</td>
            <td><a href="...reachtime.html">〇</a></td>
          </tr>
        </table>
        """
        soup = BeautifulSoup(html, 'html.parser')
        events = []

        # テーブルを探す（id="event_list" を優先、なければ最初のテーブル）
        table = soup.find('table', id='event_list')
        if not table:
            # テーブルが見つからない場合、リンクからEEW情報を抽出する
            # JMAのページはリンク形式で情報を提供している場合がある
            events = self._parse_from_links(soup)
            return events

        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 4:
                continue

            try:
                occurrence_time_str = cells[0].get_text(strip=True)
                epicenter = cells[1].get_text(strip=True)
                magnitude_str = cells[2].get_text(strip=True)
                max_intensity = cells[3].get_text(strip=True)

                # 詳細リンクを取得
                link_cell = cells[-1] if len(cells) > 4 else cells[3]
                link = link_cell.find('a')
                detail_url = link['href'] if link else ''

                # マグニチュードをパース
                magnitude = self._parse_magnitude(magnitude_str)

                # 発生日時をパース
                occurrence_time = self._parse_datetime(occurrence_time_str, detail_url)

                if occurrence_time and detail_url:
                    events.append({
                        'occurrence_time': occurrence_time,
                        'epicenter': epicenter,
                        'magnitude': magnitude,
                        'max_intensity': max_intensity,
                        'detail_url': detail_url,
                    })

            except (IndexError, ValueError, KeyError) as e:
                logger.warning("EEW行のパースに失敗: %s", e)
                continue

        return events

    def _parse_from_links(self, soup: BeautifulSoup) -> list[dict]:
        """
        リンクベースでEEW情報を抽出する（テーブルがない場合のフォールバック）
        URLパターン: /YYYYMMDDHHMMSS/reachtime/reachtime.html
        """
        events = []
        seen_urls = set()

        for link in soup.find_all('a', href=True):
            url = link['href']
            if 'reachtime/reachtime.html' not in url:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)

            match = EEW_DATETIME_PATTERN.search(url)
            if match:
                dt_str = match.group(1)
                try:
                    occurrence_time = datetime.strptime(dt_str, '%Y%m%d%H%M%S')
                    occurrence_time = occurrence_time.replace(tzinfo=JST)

                    # 完全なURLに変換
                    if not url.startswith('http'):
                        url = f"https://www.data.jma.go.jp{url}" if url.startswith('/') else \
                              f"https://www.data.jma.go.jp/eew/data/nc/pub_hist/{url}"

                    events.append({
                        'occurrence_time': occurrence_time.isoformat(),
                        'epicenter': '不明',  # リンクのみの場合は不明
                        'magnitude': None,
                        'max_intensity': None,
                        'detail_url': url,
                    })
                except ValueError:
                    continue

        return events

    def _parse_magnitude(self, mag_str: str) -> Optional[float]:
        """マグニチュード文字列をfloatに変換"""
        try:
            cleaned = mag_str.replace('M', '').strip()
            if cleaned and cleaned != '-' and cleaned != '---':
                return float(cleaned)
        except ValueError:
            pass
        return None

    def _parse_datetime(self, dt_str: str, detail_url: str = '') -> Optional[str]:
        """
        発生日時文字列をISO8601形式に変換する
        フォーマット例: "2026/01/11 13:15"
        フォールバック: URLから抽出
        """
        # まず日時文字列からパースを試みる
        for fmt in ['%Y/%m/%d %H:%M', '%Y/%m/%d %H:%M:%S',
                     '%Y年%m月%d日 %H時%M分', '%Y-%m-%d %H:%M']:
            try:
                dt = datetime.strptime(dt_str, fmt)
                dt = dt.replace(tzinfo=JST)
                return dt.isoformat()
            except ValueError:
                continue

        # URLから日時を抽出（フォールバック）
        if detail_url:
            match = EEW_DATETIME_PATTERN.search(detail_url)
            if match:
                try:
                    dt = datetime.strptime(match.group(1), '%Y%m%d%H%M%S')
                    dt = dt.replace(tzinfo=JST)
                    return dt.isoformat()
                except ValueError:
                    pass

        return None

    def fetch_and_parse(self) -> list[dict]:
        """JMAのEEWページを取得してパースする"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.session.get(JMA_EEW_URL, timeout=30)
                response.raise_for_status()
                response.encoding = response.apparent_encoding or 'utf-8'
                events = self._parse_eew_page(response.text)
                logger.info("JMAページから %d 件のEEWイベントを取得", len(events))
                return events

            except requests.RequestException as e:
                logger.warning("JMAページの取得に失敗 (試行 %d/%d): %s",
                               attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    import time
                    time.sleep(60)

        logger.error("JMAページの取得に %d回失敗しました", max_retries)
        return []

    def check_new_events(self) -> list[dict]:
        """新しいEEWイベントを確認し、DBに登録する"""
        events = self.fetch_and_parse()
        new_events = []

        for event in events:
            result = self.db.add_eew_event(
                occurrence_time=event['occurrence_time'],
                epicenter=event['epicenter'],
                magnitude=event.get('magnitude'),
                max_intensity=event.get('max_intensity'),
                detail_url=event['detail_url'],
                retention_hours=self.default_retention_hours
            )
            if result is not None:
                new_events.append(event)
                logger.info("新規EEW検出: %s %s",
                            event['occurrence_time'], event['epicenter'])

        if new_events:
            logger.info("新しいEEWイベント %d 件をDBに登録しました", len(new_events))
        else:
            logger.debug("新しいEEWイベントはありません")

        return new_events

    def run_cycle(self):
        """1サイクル分の処理を実行"""
        logger.info("EEW監視サイクルを開始")
        new_events = self.check_new_events()
        logger.info("EEW監視サイクルを完了 (新規: %d件)", len(new_events))
        return new_events
