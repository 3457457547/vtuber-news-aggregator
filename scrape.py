#!/usr/bin/env python3
"""
新人VTuber発掘サイト - メインスクリプト
YouTube Data API v3 を使って新人VTuberを自動発掘し、
静的HTMLサイトを生成する。

フロー:
  1. YouTube API で新人VTuber候補を検索
  2. フィルタリング（登録者数・開設日・アクティブ度）
  3. 候補リストをJSONキャッシュに保存
  4. 承認済みVTuberの紹介ページをHTML生成
  5. Git push → GitHub Pages で自動デプロイ
"""

import os
import sys
import json
import re
import math
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote

# ============================================================
# 設定
# ============================================================

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# 検索キーワード
SEARCH_QUERIES = [
    "新人VTuber",
    "VTuberデビュー",
    "初配信 VTuber",
    "個人勢VTuber デビュー",
    "新人Vtuber 自己紹介",
]

# フィルタリング条件
MAX_SUBSCRIBERS = 1000        # 登録者数上限
MAX_CHANNEL_AGE_DAYS = 90     # チャンネル開設からの日数上限
MIN_VIDEOS = 3                # 最低動画数
MAX_DAYS_SINCE_LAST_VIDEO = 30  # 最終投稿からの日数上限

# VTuber判定キーワード（チャンネル名 or 説明文に含まれるか）
VTUBER_KEYWORDS = [
    "vtuber", "ブイチューバー", "Vチューバー",
    "バーチャル", "virtual", "ママ", "パパ",
    "Live2D", "live2d", "配信者", "ゲーム実況",
    "歌ってみた", "初配信", "デビュー",
]

# 出力設定
SITE_NAME = "新人VTuber発掘所"
SITE_TAGLINE = "あなたの推しになる新人、ここで見つかる"
SITE_URL = "https://vtuber-matome.net"
ITEMS_PER_PAGE = 20

# ディレクトリ
CACHE_DIR = Path("cache")
PUBLIC_DIR = Path("docs")
CANDIDATES_FILE = CACHE_DIR / "candidates.json"
APPROVED_FILE = CACHE_DIR / "approved.json"

# ChatGPT API（オプション、未設定ならスキップ）
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = "gpt-4o-mini"

# ============================================================
# ユーティリティ
# ============================================================

def ensure_dirs():
    """必要なディレクトリを作成"""
    CACHE_DIR.mkdir(exist_ok=True)
    PUBLIC_DIR.mkdir(exist_ok=True)


def load_json(path: Path, default=None):
    """JSONファイルを読み込む"""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else []


def save_json(path: Path, data):
    """JSONファイルに保存"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def api_request(endpoint: str, params: dict) -> dict:
    """YouTube Data API にリクエストを送る"""
    params["key"] = YOUTUBE_API_KEY
    url = f"{YOUTUBE_API_BASE}/{endpoint}?{urlencode(params)}"
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        print(f"[ERROR] YouTube API {endpoint}: {e.code} {e.reason}")
        if e.code == 403:
            print("[ERROR] APIクォータ超過の可能性があります")
        return {}
    except URLError as e:
        print(f"[ERROR] Network error: {e}")
        return {}


def parse_iso8601(date_str: str) -> datetime:
    """ISO8601日付をパース"""
    # Python 3.11+ の fromisoformat で対応
    date_str = date_str.replace("Z", "+00:00")
    return datetime.fromisoformat(date_str)


def days_ago(date_str: str) -> int:
    """指定日付から今日までの日数"""
    dt = parse_iso8601(date_str)
    now = datetime.now(timezone.utc)
    return (now - dt).days


def format_subscriber_count(count: int) -> str:
    """登録者数を読みやすい形式に"""
    if count >= 10000:
        return f"{count / 10000:.1f}万人"
    elif count >= 1000:
        return f"{count / 1000:.1f}千人"
    return f"{count}人"


def format_date_jp(date_str: str) -> str:
    """日付を日本語形式に"""
    dt = parse_iso8601(date_str)
    return dt.strftime("%Y年%m月%d日")


def channel_id_hash(channel_id: str) -> str:
    """チャンネルIDから短いハッシュを生成（ファイル名用）"""
    return hashlib.md5(channel_id.encode()).hexdigest()[:8]


# ============================================================
# YouTube API: 新人VTuber検索
# ============================================================

def search_channels(query: str, max_results: int = 20) -> list:
    """
    YouTube検索APIでチャンネルを検索
    クォータコスト: 100/リクエスト
    """
    published_after = (datetime.now(timezone.utc) - timedelta(days=MAX_CHANNEL_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    data = api_request("search", {
        "part": "snippet",
        "q": query,
        "type": "channel",
        "maxResults": max_results,
        "publishedAfter": published_after,
        "order": "date",
        "regionCode": "JP",
        "relevanceLanguage": "ja",
    })

    channels = []
    for item in data.get("items", []):
        channels.append({
            "channel_id": item["snippet"]["channelId"],
            "title": item["snippet"]["title"],
            "description": item["snippet"]["description"],
            "thumbnail": item["snippet"]["thumbnails"].get("medium", {}).get("url", ""),
            "published_at": item["snippet"]["publishedAt"],
        })

    return channels


def get_channel_details(channel_ids: list) -> dict:
    """
    チャンネルの詳細情報（登録者数、動画数など）を取得
    クォータコスト: 1/リクエスト（最大50チャンネル/リクエスト）
    """
    if not channel_ids:
        return {}

    # 50件ずつ分割
    results = {}
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i+50]
        data = api_request("channels", {
            "part": "snippet,statistics,brandingSettings",
            "id": ",".join(batch),
        })

        for item in data.get("items", []):
            cid = item["id"]
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})
            branding = item.get("brandingSettings", {}).get("channel", {})

            # 登録者数が非公開の場合
            sub_count = int(stats.get("subscriberCount", 0))
            if stats.get("hiddenSubscriberCount", False):
                sub_count = -1  # 非公開

            results[cid] = {
                "channel_id": cid,
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "custom_url": snippet.get("customUrl", ""),
                "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                "published_at": snippet.get("publishedAt", ""),
                "subscriber_count": sub_count,
                "video_count": int(stats.get("videoCount", 0)),
                "view_count": int(stats.get("viewCount", 0)),
                "keywords": branding.get("keywords", ""),
            }

    return results


def get_latest_videos(channel_id: str, max_results: int = 5) -> list:
    """
    チャンネルの最新動画を取得
    クォータコスト: 100/リクエスト（search APIを使用）
    ※ クォータ節約のため、候補確定後のみ呼ぶ
    """
    data = api_request("search", {
        "part": "snippet",
        "channelId": channel_id,
        "type": "video",
        "maxResults": max_results,
        "order": "date",
    })

    videos = []
    for item in data.get("items", []):
        videos.append({
            "video_id": item["id"]["videoId"],
            "title": item["snippet"]["title"],
            "thumbnail": item["snippet"]["thumbnails"].get("medium", {}).get("url", ""),
            "published_at": item["snippet"]["publishedAt"],
        })

    return videos


def get_video_details(video_ids: list) -> dict:
    """
    動画の詳細情報（再生回数など）を取得
    クォータコスト: 1/リクエスト
    """
    if not video_ids:
        return {}

    results = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        data = api_request("videos", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch),
        })

        for item in data.get("items", []):
            vid = item["id"]
            stats = item.get("statistics", {})
            results[vid] = {
                "video_id": vid,
                "title": item["snippet"]["title"],
                "view_count": int(stats.get("viewCount", 0)),
                "like_count": int(stats.get("likeCount", 0)),
                "duration": item.get("contentDetails", {}).get("duration", ""),
            }

    return results


# ============================================================
# フィルタリング
# ============================================================

def is_likely_vtuber(channel: dict) -> bool:
    """VTuberの可能性が高いか判定"""
    text = (channel.get("title", "") + " " +
            channel.get("description", "") + " " +
            channel.get("keywords", "")).lower()

    return any(kw.lower() in text for kw in VTUBER_KEYWORDS)


def passes_filters(channel: dict) -> tuple:
    """
    フィルタリング条件をチェック
    Returns: (passes: bool, reason: str)
    """
    # 登録者数チェック（非公開は通す）
    sub_count = channel.get("subscriber_count", 0)
    if sub_count > MAX_SUBSCRIBERS and sub_count != -1:
        return False, f"登録者数が{MAX_SUBSCRIBERS}人を超えている（{sub_count}人）"

    # チャンネル年齢チェック
    pub_date = channel.get("published_at", "")
    if pub_date:
        age = days_ago(pub_date)
        if age > MAX_CHANNEL_AGE_DAYS:
            return False, f"チャンネル開設から{age}日経過（上限{MAX_CHANNEL_AGE_DAYS}日）"

    # 動画数チェック
    video_count = channel.get("video_count", 0)
    if video_count < MIN_VIDEOS:
        return False, f"動画数が{video_count}本（最低{MIN_VIDEOS}本必要）"

    # VTuber判定
    if not is_likely_vtuber(channel):
        return False, "VTuber関連キーワードが見つからない"

    return True, "OK"


# ============================================================
# ChatGPT API: 紹介文生成（オプション）
# ============================================================

def generate_introduction(channel: dict, videos: list) -> str:
    """ChatGPT APIで紹介文を自動生成"""
    if not OPENAI_API_KEY:
        return generate_fallback_introduction(channel)

    video_titles = "\n".join([f"- {v['title']}" for v in videos[:5]])

    prompt = f"""以下のVTuberチャンネル情報をもとに、応援する気持ちを込めた紹介文を3行で書いてください。
フレンドリーで明るいトーンで、視聴者が「見てみたい」と思うような紹介にしてください。

チャンネル名: {channel['title']}
チャンネル説明: {channel.get('description', 'なし')[:200]}
最近の動画:
{video_titles}
登録者数: {format_subscriber_count(channel.get('subscriber_count', 0))}

ルール:
- 3行以内
- 絵文字は1〜2個まで
- 「応援しています」的な前向きな締め
- マークダウンは使わない"""

    try:
        req_body = json.dumps({
            "model": OPENAI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 200,
            "temperature": 0.7,
        }).encode("utf-8")

        req = Request(
            "https://api.openai.com/v1/chat/completions",
            data=req_body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            },
        )
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()

    except Exception as e:
        print(f"[WARN] ChatGPT API error: {e}")
        return generate_fallback_introduction(channel)


def generate_fallback_introduction(channel: dict) -> str:
    """ChatGPT APIが使えない場合のフォールバック紹介文"""
    title = channel.get("title", "名前不明")
    desc = channel.get("description", "")[:100]
    if desc:
        return f"{title}さんがVTuberとしてデビュー！ {desc.split(chr(10))[0]}"
    return f"{title}さんがVTuberとしてデビュー！ ぜひチャンネルをチェックしてみてください。"


# ============================================================
# メインロジック: 候補収集
# ============================================================

def collect_candidates() -> list:
    """
    全検索クエリで新人VTuber候補を収集し、フィルタリング
    """
    print("=" * 60)
    print("新人VTuber候補を収集中...")
    print("=" * 60)

    # 既存の候補・承認済みリストを読み込み
    existing_candidates = load_json(CANDIDATES_FILE, [])
    approved = load_json(APPROVED_FILE, [])

    existing_ids = {c["channel_id"] for c in existing_candidates}
    approved_ids = {a["channel_id"] for a in approved}

    all_channel_ids = []
    channel_snippets = {}  # channel_id -> search snippet

    # 各クエリで検索
    for query in SEARCH_QUERIES:
        print(f"\n検索中: 「{query}」")
        results = search_channels(query, max_results=10)
        print(f"  → {len(results)}件ヒット")

        for ch in results:
            cid = ch["channel_id"]
            if cid not in existing_ids and cid not in approved_ids:
                if cid not in channel_snippets:
                    all_channel_ids.append(cid)
                    channel_snippets[cid] = ch

    if not all_channel_ids:
        print("\n新しい候補はありませんでした。")
        return existing_candidates

    # 重複を除去した新規チャンネルの詳細を取得
    unique_ids = list(set(all_channel_ids))
    print(f"\n新規チャンネル {len(unique_ids)}件の詳細を取得中...")
    details = get_channel_details(unique_ids)

    # フィルタリング
    new_candidates = []
    for cid, detail in details.items():
        # スニペット情報をマージ
        snippet = channel_snippets.get(cid, {})
        detail["thumbnail"] = detail.get("thumbnail") or snippet.get("thumbnail", "")

        passes, reason = passes_filters(detail)
        if passes:
            detail["discovered_at"] = datetime.now(timezone.utc).isoformat()
            detail["status"] = "pending"  # pending / approved / rejected
            new_candidates.append(detail)
            print(f"  ✅ {detail['title']}（{format_subscriber_count(detail.get('subscriber_count', 0))}）")
        else:
            print(f"  ❌ {detail.get('title', cid)}: {reason}")

    # 既存候補とマージ
    merged = existing_candidates + new_candidates
    print(f"\n候補合計: {len(merged)}件（新規 {len(new_candidates)}件）")

    return merged


# ============================================================
# 承認処理
# ============================================================

def approve_candidate(candidates: list, channel_id: str) -> tuple:
    """
    候補を承認して承認リストに移動
    Returns: (updated_candidates, approved_entry)
    """
    approved = load_json(APPROVED_FILE, [])

    target = None
    remaining = []
    for c in candidates:
        if c["channel_id"] == channel_id:
            target = c
        else:
            remaining.append(c)

    if not target:
        print(f"[WARN] チャンネル {channel_id} が候補リストに見つかりません")
        return candidates, None

    # 最新動画を取得
    print(f"「{target['title']}」の最新動画を取得中...")
    videos = get_latest_videos(channel_id, max_results=5)
    target["latest_videos"] = videos

    # 紹介文を生成
    print(f"紹介文を生成中...")
    target["introduction"] = generate_introduction(target, videos)

    # 承認
    target["status"] = "approved"
    target["approved_at"] = datetime.now(timezone.utc).isoformat()
    approved.append(target)

    save_json(APPROVED_FILE, approved)
    print(f"✅ {target['title']} を承認しました")

    return remaining, target


def auto_approve_from_spreadsheet():
    """
    Google Spreadsheet経由の承認をチェック
    （Apps Scriptが approved.json に直接書き込む想定）
    ※ 将来実装。現在はCLIから approve コマンドで代用
    """
    pass


# ============================================================
# HTML生成
# ============================================================

def render_css() -> str:
    """CSSを生成"""
    return """
:root {
  --primary: #6C5CE7;
  --primary-light: #A29BFE;
  --accent: #FD79A8;
  --accent-light: #FDCB6E;
  --bg: #F8F9FA;
  --card-bg: #FFFFFF;
  --text: #2D3436;
  --text-light: #636E72;
  --border: #E9ECEF;
  --shadow: 0 2px 12px rgba(0,0,0,0.08);
  --shadow-hover: 0 8px 25px rgba(108,92,231,0.15);
  --radius: 12px;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Segoe UI", sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.7;
  min-height: 100vh;
}

/* ヘッダー */
.header {
  background: linear-gradient(135deg, var(--primary), #4834D4);
  color: white;
  padding: 2rem 1rem;
  text-align: center;
  position: relative;
  overflow: hidden;
}
.header::before {
  content: '';
  position: absolute;
  top: -50%;
  left: -50%;
  width: 200%;
  height: 200%;
  background: radial-gradient(circle, rgba(255,255,255,0.1) 0%, transparent 70%);
  animation: pulse 4s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { transform: scale(1); opacity: 0.5; }
  50% { transform: scale(1.1); opacity: 1; }
}
.header h1 {
  font-size: 1.8rem;
  position: relative;
  z-index: 1;
  text-shadow: 0 2px 4px rgba(0,0,0,0.2);
}
.header p {
  font-size: 0.95rem;
  opacity: 0.9;
  margin-top: 0.5rem;
  position: relative;
  z-index: 1;
}

/* メインコンテンツ */
.container {
  max-width: 800px;
  margin: 0 auto;
  padding: 1.5rem 1rem;
}

/* セクションタイトル */
.section-title {
  font-size: 1.2rem;
  color: var(--primary);
  margin: 2rem 0 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--primary-light);
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

/* VTuberカード */
.vtuber-card {
  background: var(--card-bg);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1.2rem;
  margin-bottom: 1rem;
  transition: all 0.3s ease;
  border: 1px solid var(--border);
  animation: fadeInUp 0.5s ease both;
}
.vtuber-card:hover {
  box-shadow: var(--shadow-hover);
  transform: translateY(-2px);
  border-color: var(--primary-light);
}

@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

.card-header {
  display: flex;
  gap: 1rem;
  align-items: flex-start;
}
.card-thumbnail {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  object-fit: cover;
  border: 3px solid var(--primary-light);
  flex-shrink: 0;
}
.card-info {
  flex: 1;
  min-width: 0;
}
.card-name {
  font-size: 1.1rem;
  font-weight: bold;
  color: var(--text);
  margin-bottom: 0.25rem;
}
.card-name a {
  color: inherit;
  text-decoration: none;
}
.card-name a:hover {
  color: var(--primary);
}
.card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  font-size: 0.8rem;
  color: var(--text-light);
  margin-bottom: 0.5rem;
}
.card-meta span {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
}
.card-intro {
  font-size: 0.9rem;
  color: var(--text);
  line-height: 1.8;
  margin-top: 0.75rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border);
}

/* 動画セクション */
.card-videos {
  margin-top: 0.75rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border);
}
.card-videos-title {
  font-size: 0.8rem;
  color: var(--text-light);
  margin-bottom: 0.5rem;
}
.video-link {
  display: block;
  font-size: 0.85rem;
  color: var(--primary);
  text-decoration: none;
  padding: 0.3rem 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.video-link:hover {
  text-decoration: underline;
}

/* CTAボタン */
.card-cta {
  display: inline-block;
  margin-top: 0.75rem;
  padding: 0.5rem 1.2rem;
  background: linear-gradient(135deg, var(--accent), #E84393);
  color: white;
  border-radius: 2rem;
  text-decoration: none;
  font-size: 0.85rem;
  font-weight: bold;
  transition: all 0.3s ease;
}
.card-cta:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(253,121,168,0.4);
}

/* 広告プレースホルダー */
.ad-space {
  background: linear-gradient(135deg, #FFF3E0, #FFE0B2);
  border: 1px dashed #FFB74D;
  border-radius: var(--radius);
  padding: 1.5rem;
  margin: 1.5rem 0;
  text-align: center;
  color: #F57C00;
  font-size: 0.8rem;
}

/* ページネーション */
.pagination {
  display: flex;
  justify-content: center;
  gap: 0.5rem;
  margin: 2rem 0;
}
.pagination a, .pagination span {
  display: inline-block;
  padding: 0.5rem 1rem;
  border-radius: var(--radius);
  text-decoration: none;
  font-size: 0.9rem;
  border: 1px solid var(--border);
}
.pagination a {
  color: var(--primary);
  background: white;
}
.pagination a:hover {
  background: var(--primary);
  color: white;
}
.pagination .current {
  background: var(--primary);
  color: white;
  border-color: var(--primary);
}

/* フッター */
.footer {
  background: linear-gradient(135deg, #2D3436, #636E72);
  color: white;
  text-align: center;
  padding: 1.5rem 1rem;
  margin-top: 3rem;
  font-size: 0.8rem;
}
.footer a { color: var(--primary-light); text-decoration: none; }
.footer a:hover { text-decoration: underline; }

/* 空状態 */
.empty-state {
  text-align: center;
  padding: 3rem 1rem;
  color: var(--text-light);
}
.empty-state .emoji { font-size: 3rem; margin-bottom: 1rem; }

/* レスポンシブ */
@media (max-width: 600px) {
  .header h1 { font-size: 1.4rem; }
  .card-thumbnail { width: 48px; height: 48px; }
  .card-meta { font-size: 0.75rem; }
}
""".strip()


def render_head(title: str, description: str, url: str = "", og_image: str = "") -> str:
    """HTMLのhead部分を生成"""
    og_image_tag = f'<meta property="og:image" content="{og_image}">' if og_image else ""
    canonical = f'<link rel="canonical" href="{url}">' if url else ""

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <meta name="description" content="{description}">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{description}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{url}">
  {og_image_tag}
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="{description}">
  {canonical}
  <link rel="stylesheet" href="style.css">
  <!-- Google Analytics -->
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-SJ6FD6ZGJE"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());
    gtag('config', 'G-SJ6FD6ZGJE');
  </script>
</head>"""


def render_header() -> str:
    """サイトヘッダーを生成"""
    return f"""
<header class="header">
  <h1>{SITE_NAME}</h1>
  <p>{SITE_TAGLINE}</p>
</header>"""


def render_vtuber_card(vtuber: dict, index: int = 0) -> str:
    """VTuberカードHTMLを生成"""
    name = vtuber.get("title", "名前不明")
    thumbnail = vtuber.get("thumbnail", "")
    sub_count = vtuber.get("subscriber_count", 0)
    intro = vtuber.get("introduction", "")
    channel_id = vtuber.get("channel_id", "")
    channel_url = f"https://www.youtube.com/channel/{channel_id}"
    pub_date = vtuber.get("published_at", "")
    videos = vtuber.get("latest_videos", [])

    # メタ情報
    meta_parts = []
    if sub_count > 0:
        meta_parts.append(f"<span>📊 {format_subscriber_count(sub_count)}</span>")
    elif sub_count == -1:
        meta_parts.append("<span>📊 非公開</span>")
    if pub_date:
        meta_parts.append(f"<span>📅 {format_date_jp(pub_date)}\u00A0開設</span>")

    meta_html = "\n          ".join(meta_parts)

    # 紹介文
    intro_html = ""
    if intro:
        intro_escaped = intro.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        intro_html = f'<div class="card-intro">{intro_escaped}</div>'

    # 動画リスト
    videos_html = ""
    if videos:
        video_links = ""
        for v in videos[:3]:
            vid = v.get("video_id", "")
            vtitle = v.get("title", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            video_links += f'<a href="https://www.youtube.com/watch?v={vid}" target="_blank" rel="noopener" class="video-link">▶ {vtitle}</a>\n'
        videos_html = f"""
      <div class="card-videos">
        <div class="card-videos-title">最近の動画</div>
        {video_links}
      </div>"""

    delay = index * 0.05

    return f"""
    <article class="vtuber-card" style="animation-delay: {delay}s">
      <div class="card-header">
        <img src="{thumbnail}" alt="{name}" class="card-thumbnail" loading="lazy"
             onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2264%22 height=%2264%22><rect fill=%22%236C5CE7%22 width=%2264%22 height=%2264%22 rx=%2232%22/><text x=%2232%22 y=%2240%22 fill=%22white%22 text-anchor=%22middle%22 font-size=%2224%22>?</text></svg>'">
        <div class="card-info">
          <div class="card-name">
            <a href="{channel_url}" target="_blank" rel="noopener">{name}</a>
          </div>
          <div class="card-meta">
            {meta_html}
          </div>
        </div>
      </div>
      {intro_html}
      {videos_html}
      <a href="{channel_url}" target="_blank" rel="noopener" class="card-cta">チャンネルを見る →</a>
    </article>"""


def render_ad_space() -> str:
    """広告プレースホルダーを生成"""
    return """
    <div class="ad-space">
      📢 広告スペース（nend審査通過後に表示）
    </div>"""


def render_pagination(current_page: int, total_pages: int) -> str:
    """ページネーションHTMLを生成"""
    if total_pages <= 1:
        return ""

    parts = ['<div class="pagination">']
    for i in range(1, total_pages + 1):
        filename = "index.html" if i == 1 else f"page{i}.html"
        if i == current_page:
            parts.append(f'  <span class="current">{i}</span>')
        else:
            parts.append(f'  <a href="{filename}">{i}</a>')
    parts.append("</div>")

    return "\n".join(parts)


def render_footer() -> str:
    """フッターHTMLを生成"""
    now = datetime.now(timezone.utc) + timedelta(hours=9)  # JST
    update_time = now.strftime("%Y/%m/%d %H:%M")

    return f"""
<footer class="footer">
  <p>{SITE_NAME} | 最終更新: {update_time} JST</p>
  <p>お問い合わせ・掲載削除依頼は<a href="mailto:contact@vtuber-matome.net">こちら</a></p>
  <p style="margin-top: 0.5rem; font-size: 0.7rem; opacity: 0.7;">
    当サイトはYouTubeの公開データをもとに新人VTuberを紹介しています。
  </p>
</footer>"""


def generate_index_page(approved: list, page: int, total_pages: int) -> str:
    """メインページのHTMLを生成"""
    start = (page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_items = approved[start:end]

    title = f"{SITE_NAME} - {SITE_TAGLINE}"
    description = "新人VTuberを毎日発掘・紹介！あなたの新しい推しが見つかるかも。"

    cards_html = ""
    for i, vtuber in enumerate(page_items):
        cards_html += render_vtuber_card(vtuber, i)
        # 5件ごとに広告
        if (i + 1) % 5 == 0 and i < len(page_items) - 1:
            cards_html += render_ad_space()

    if not page_items:
        cards_html = """
    <div class="empty-state">
      <div class="emoji">🔍</div>
      <p>まだ紹介済みのVTuberがいません。</p>
      <p>まもなく新人VTuberの紹介が始まります！</p>
    </div>"""

    page_url = SITE_URL if page == 1 else f"{SITE_URL}/page{page}.html"

    return f"""{render_head(title, description, page_url)}
<body>
  {render_header()}
  <main class="container">
    <div class="section-title">✨ 新人VTuber紹介（{len(approved)}人）</div>
    {cards_html}
    {render_pagination(page, total_pages)}
  </main>
  {render_footer()}
</body>
</html>"""


def generate_vtuber_page(vtuber: dict) -> str:
    """個別VTuber紹介ページを生成"""
    name = vtuber.get("title", "名前不明")
    channel_id = vtuber.get("channel_id", "")
    slug = channel_id_hash(channel_id)
    thumbnail = vtuber.get("thumbnail", "")
    intro = vtuber.get("introduction", "")
    channel_url = f"https://www.youtube.com/channel/{channel_id}"
    videos = vtuber.get("latest_videos", [])

    title = f"【新人VTuber】{name}さんがデビュー！ | {SITE_NAME}"
    description = intro[:120] if intro else f"{name}さんの紹介ページ"
    page_url = f"{SITE_URL}/vtuber/{slug}.html"

    # 動画埋め込み
    videos_html = ""
    if videos:
        videos_section = ""
        for v in videos[:3]:
            vid = v.get("video_id", "")
            vtitle = v.get("title", "")
            videos_section += f"""
      <div style="margin-bottom: 1rem;">
        <div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;border-radius:8px;">
          <iframe src="https://www.youtube.com/embed/{vid}" frameborder="0"
                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                  allowfullscreen
                  style="position:absolute;top:0;left:0;width:100%;height:100%;"
                  loading="lazy"></iframe>
        </div>
        <p style="font-size:0.85rem;color:var(--text-light);margin-top:0.5rem;">{vtitle}</p>
      </div>"""
        videos_html = f"""
    <div class="section-title">🎬 最近の動画</div>
    {videos_section}"""

    intro_escaped = ""
    if intro:
        intro_escaped = intro.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")

    return f"""{render_head(title, description, page_url, thumbnail)}
<body>
  {render_header()}
  <main class="container">
    <a href="/" style="display:inline-block;margin-bottom:1rem;color:var(--primary);text-decoration:none;">← トップに戻る</a>

    <article class="vtuber-card" style="animation-delay:0s">
      <div class="card-header">
        <img src="{thumbnail}" alt="{name}" class="card-thumbnail" loading="lazy">
        <div class="card-info">
          <div class="card-name" style="font-size:1.3rem;">{name}</div>
          <div class="card-meta">
            <span>📊 {format_subscriber_count(vtuber.get('subscriber_count', 0))}</span>
            <span>📅 {format_date_jp(vtuber.get('published_at', ''))}\u00A0開設</span>
          </div>
        </div>
      </div>
      <div class="card-intro" style="font-size:1rem;">
        {intro_escaped}
      </div>
      <a href="{channel_url}" target="_blank" rel="noopener" class="card-cta" style="margin-top:1rem;">
        チャンネルを見る →
      </a>
    </article>

    {videos_html}

    {render_ad_space()}
  </main>
  {render_footer()}
</body>
</html>"""


# ============================================================
# 静的ファイル生成
# ============================================================

def generate_rss(approved: list) -> str:
    """RSS 2.0フィードを生成"""
    now = datetime.now(timezone.utc)
    pub_date = now.strftime("%a, %d %b %Y %H:%M:%S +0000")

    items = ""
    sorted_list = sorted(
        approved,
        key=lambda x: x.get("approved_at", x.get("discovered_at", "")),
        reverse=True,
    )[:20]  # 最新20件

    for vtuber in sorted_list:
        name = vtuber.get("title", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        slug = channel_id_hash(vtuber.get("channel_id", ""))
        link = f"{SITE_URL}/vtuber/{slug}.html"
        intro = vtuber.get("introduction", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        channel_url = f"https://www.youtube.com/channel/{vtuber.get('channel_id', '')}"
        sub_count = format_subscriber_count(vtuber.get("subscriber_count", 0))
        approved_at = vtuber.get("approved_at", now.isoformat())
        dt = parse_iso8601(approved_at)
        item_date = dt.strftime("%a, %d %b %Y %H:%M:%S +0000")

        description = f"{intro} チャンネル登録者: {sub_count} #新人VTuber"

        items += f"""    <item>
      <title>【新人VTuber】{name}さんがデビュー！</title>
      <link>{link}</link>
      <description>{description}</description>
      <pubDate>{item_date}</pubDate>
      <guid isPermaLink="true">{link}</guid>
    </item>
"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{SITE_NAME}</title>
    <link>{SITE_URL}</link>
    <description>{SITE_TAGLINE}</description>
    <language>ja</language>
    <lastBuildDate>{pub_date}</lastBuildDate>
    <atom:link href="{SITE_URL}/feed.xml" rel="self" type="application/rss+xml"/>
{items}  </channel>
</rss>"""


def generate_robots_txt() -> str:
    return f"""User-agent: *
Allow: /
Sitemap: {SITE_URL}/sitemap.xml"""


def generate_sitemap(approved: list) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    urls = [f"""  <url>
    <loc>{SITE_URL}/</loc>
    <lastmod>{now}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>"""]

    for vtuber in approved:
        slug = channel_id_hash(vtuber.get("channel_id", ""))
        urls.append(f"""  <url>
    <loc>{SITE_URL}/vtuber/{slug}.html</loc>
    <lastmod>{now}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls)}
</urlset>"""


def write_all_files(approved: list):
    """全ファイルを書き出す"""
    print("\nHTMLファイルを生成中...")

    # CSS
    css_path = PUBLIC_DIR / "style.css"
    css_path.write_text(render_css(), encoding="utf-8")
    print(f"  ✅ style.css")

    # 承認済みを新しい順にソート
    sorted_approved = sorted(
        approved,
        key=lambda x: x.get("approved_at", x.get("discovered_at", "")),
        reverse=True,
    )

    # メインページ（ページネーション）
    total_pages = max(1, math.ceil(len(sorted_approved) / ITEMS_PER_PAGE))
    for page in range(1, total_pages + 1):
        filename = "index.html" if page == 1 else f"page{page}.html"
        html = generate_index_page(sorted_approved, page, total_pages)
        (PUBLIC_DIR / filename).write_text(html, encoding="utf-8")
        print(f"  ✅ {filename}")

    # 個別VTuberページ
    vtuber_dir = PUBLIC_DIR / "vtuber"
    vtuber_dir.mkdir(exist_ok=True)
    for vtuber in sorted_approved:
        slug = channel_id_hash(vtuber.get("channel_id", ""))
        html = generate_vtuber_page(vtuber)
        (vtuber_dir / f"{slug}.html").write_text(html, encoding="utf-8")
    print(f"  ✅ 個別ページ: {len(sorted_approved)}件")

    # robots.txt & sitemap.xml & CNAME & RSS
    (PUBLIC_DIR / "robots.txt").write_text(generate_robots_txt(), encoding="utf-8")
    (PUBLIC_DIR / "sitemap.xml").write_text(generate_sitemap(sorted_approved), encoding="utf-8")
    (PUBLIC_DIR / "feed.xml").write_text(generate_rss(sorted_approved), encoding="utf-8")
    (PUBLIC_DIR / "CNAME").write_text("vtuber-matome.net", encoding="utf-8")
    print(f"  ✅ robots.txt & sitemap.xml & feed.xml & CNAME")

    print(f"\n生成完了！ 合計 {total_pages + len(sorted_approved) + 3} ファイル")


# ============================================================
# CLI
# ============================================================

def print_candidates(candidates: list):
    """候補リストを表示"""
    pending = [c for c in candidates if c.get("status") == "pending"]
    if not pending:
        print("承認待ちの候補はありません。")
        return

    print(f"\n📋 承認待ちの候補: {len(pending)}件")
    print("-" * 60)
    for i, c in enumerate(pending, 1):
        sub = format_subscriber_count(c.get("subscriber_count", 0))
        age = days_ago(c.get("published_at", datetime.now(timezone.utc).isoformat()))
        print(f"  {i}. {c['title']}")
        print(f"     登録者: {sub} | 開設: {age}日前 | 動画: {c.get('video_count', 0)}本")
        print(f"     ID: {c['channel_id']}")
        print()


def cli_approve(candidates: list) -> list:
    """CLIから候補を承認する"""
    pending = [c for c in candidates if c.get("status") == "pending"]
    if not pending:
        print("承認待ちの候補はありません。")
        return candidates

    print_candidates(candidates)
    print("承認するチャンネル番号を入力（カンマ区切りで複数可、qで終了）:")

    while True:
        user_input = input("> ").strip()
        if user_input.lower() == "q":
            break

        try:
            indices = [int(x.strip()) for x in user_input.split(",")]
            for idx in indices:
                if 1 <= idx <= len(pending):
                    channel_id = pending[idx - 1]["channel_id"]
                    candidates, approved = approve_candidate(candidates, channel_id)
                    if approved:
                        print(f"  ✅ 承認: {approved['title']}")
                else:
                    print(f"  ❌ 無効な番号: {idx}")
        except ValueError:
            print("数字を入力してください")

        # リスト更新
        pending = [c for c in candidates if c.get("status") == "pending"]
        if not pending:
            print("全候補を処理しました。")
            break

    return candidates


# ============================================================
# メイン
# ============================================================

def main():
    """メイン処理"""
    ensure_dirs()

    if not YOUTUBE_API_KEY:
        print("[ERROR] YOUTUBE_API_KEY が設定されていません")
        print("環境変数 YOUTUBE_API_KEY を設定してください")
        sys.exit(1)

    # コマンドライン引数で動作を変える
    mode = sys.argv[1] if len(sys.argv) > 1 else "collect"

    if mode == "collect":
        # 候補収集（自動実行用）
        candidates = collect_candidates()
        save_json(CANDIDATES_FILE, candidates)
        print_candidates(candidates)

        # 承認済みのHTMLも再生成
        approved = load_json(APPROVED_FILE, [])
        write_all_files(approved)

    elif mode == "approve":
        # 承認処理（手動実行用）
        candidates = load_json(CANDIDATES_FILE, [])
        candidates = cli_approve(candidates)
        save_json(CANDIDATES_FILE, candidates)

    elif mode == "approve-all":
        # 全候補を一括承認（GitHub Actions用）
        candidates = load_json(CANDIDATES_FILE, [])
        pending = [c for c in candidates if c.get("status") == "pending"]
        print(f"\n全{len(pending)}件を一括承認します...")
        for c in pending:
            candidates, approved_entry = approve_candidate(candidates, c["channel_id"])
            if approved_entry:
                print(f"  ✅ {approved_entry['title']}")
        save_json(CANDIDATES_FILE, candidates)

        # HTML再生成
        approved = load_json(APPROVED_FILE, [])
        write_all_files(approved)

    elif mode == "generate":
        # HTML生成のみ
        approved = load_json(APPROVED_FILE, [])
        write_all_files(approved)

    elif mode == "status":
        # ステータス表示
        candidates = load_json(CANDIDATES_FILE, [])
        approved = load_json(APPROVED_FILE, [])
        pending = [c for c in candidates if c.get("status") == "pending"]
        print(f"\n📊 ステータス")
        print(f"  候補（未処理）: {len(pending)}件")
        print(f"  承認済み: {len(approved)}件")
        print(f"  合計候補: {len(candidates)}件")

    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python scrape.py [collect|approve|approve-all|generate|status]")
        sys.exit(1)


if __name__ == "__main__":
    main()
