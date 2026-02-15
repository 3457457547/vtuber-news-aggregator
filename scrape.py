#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VTuber News Aggregator - Scraper & HTML Generator
取得先: https://vtuber.atodeyo.com/
対象サイト: にじホロ速, Vtuberまとめるよ～ん, Vtuberまとめ部！, VTuberNews, やらおん！
"""

import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timezone
from pathlib import Path
import time
import sys

# ==================== 設定 ====================

BASE_URL = "https://vtuber.atodeyo.com/"
OUTPUT_DIR = Path("public")
CACHE_FILE = Path("cache/scraped_data.json")

# 取得対象サイト（クラス名 → サイト名のマッピング）
ALLOWED_SITES = {
    "nhkecr27": "にじホロ速",
    "vemogu23": "Vtuberまとめるよ～ん",
    "vemgco19": "Vtuberまとめ部！",
    "vejwsu12": "VTuberNews",
    "yocgca13": "やらおん！"
}

# 除外するクラス（PR記事など）
EXCLUDED_CLASSES = ["pr"]

# 記事保持数・ページネーション
MAX_ITEMS = 100
ITEMS_PER_PAGE = 50

# リトライ設定
MAX_RETRIES = 3
RETRY_DELAY = 5

# ==================== ユーティリティ関数 ====================

def ensure_dirs():
    """必要なディレクトリを作成"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    print("✅ ディレクトリ確認完了")

def load_cache():
    """キャッシュファイルから過去の記事データを読み込み"""
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            print(f"📦 キャッシュ読み込み: {len(data)}件")
            return data
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"⚠️ キャッシュ破損（{e}）、新規作成します")
            return []
    else:
        print("📦 キャッシュなし、新規作成します")
        return []

def save_cache(items):
    """キャッシュファイルに記事データを保存（原子的書き込み）"""
    temp_file = CACHE_FILE.with_suffix('.tmp')
    try:
        temp_file.write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        temp_file.replace(CACHE_FILE)
        print(f"💾 キャッシュ保存: {len(items)}件")
    except Exception as e:
        print(f"❌ キャッシュ保存失敗: {e}")
        if temp_file.exists():
            temp_file.unlink()

def fetch_html(url):
    """HTMLを取得（リトライ付き）"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }
    
    for attempt in range(MAX_RETRIES):
        try:
            print(f"🌐 取得中 (試行 {attempt + 1}/{MAX_RETRIES}): {url}")
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            # 文字エンコーディングを明示的にUTF-8に設定
            response.encoding = 'utf-8'
            
            print(f"✅ 取得成功: {len(response.text)} bytes")
            return response.text
        except requests.exceptions.Timeout:
            print(f"⏱️ タイムアウト (試行 {attempt + 1}/{MAX_RETRIES})")
        except requests.exceptions.RequestException as e:
            print(f"⚠️ 取得エラー: {e}")
        
        if attempt < MAX_RETRIES - 1:
            wait_time = RETRY_DELAY * (attempt + 1)
            print(f"⏳ {wait_time}秒待機してリトライ...")
            time.sleep(wait_time)
    
    print("❌ すべてのリトライが失敗しました")
    return None

def parse_timeline(html):
    """HTMLから記事情報を抽出"""
    if not html:
        return []
    
    try:
        soup = BeautifulSoup(html, "html.parser")
        timeline = soup.select_one(".timeline")
        
        if not timeline:
            print("⚠️ .timeline要素が見つかりません（HTML構造が変更された可能性）")
            return []
        
        items = []
        children = timeline.find_all('div', recursive=False)
        print(f"🔍 {len(children)}件の要素を検出")
        
        for idx, child in enumerate(children):
            try:
                classes = child.get('class', [])
                if not classes:
                    continue
                
                site_class = classes[0]
                
                if site_class in EXCLUDED_CLASSES:
                    continue
                
                if site_class not in ALLOWED_SITES:
                    continue
                
                time_p = child.find('p', class_='time')
                article_p = child.find('p', class_='article')
                site_p = child.find('p', class_='site')
                
                if not article_p:
                    continue
                
                article_a = article_p.find('a')
                if not article_a:
                    continue
                
                title = article_a.get_text(strip=True)
                href = article_a.get('href', '')
                time_text = time_p.get_text(strip=True) if time_p else ''
                
                site_name = ALLOWED_SITES.get(site_class, site_class)
                
                if not href or not title:
                    continue
                
                if href.startswith('/'):
                    href = BASE_URL.rstrip('/') + href
                elif not href.startswith('http'):
                    continue
                
                item = {
                    "title": title[:200],
                    "url": href,
                    "site": site_name,
                    "site_class": site_class,
                    "time_text": time_text[:50],
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "id": abs(hash(href))
                }
                
                items.append(item)
                
            except Exception as e:
                print(f"⚠️ 要素{idx}のパースエラー: {e}")
                continue
        
        print(f"✅ {len(items)}件の記事を抽出（対象サイトのみ）")
        
        site_counts = {}
        for item in items:
            site = item['site']
            site_counts[site] = site_counts.get(site, 0) + 1
        
        for site, count in site_counts.items():
            print(f"   - {site}: {count}件")
        
        return items
        
    except Exception as e:
        print(f"❌ HTML解析エラー: {e}")
        import traceback
        traceback.print_exc()
        return []

def dedupe_and_merge(old_items, new_items):
    """新旧記事をマージし、重複を削除"""
    existing_ids = {item.get("id") for item in old_items if "id" in item}
    
    unique_new = [
        item for item in new_items 
        if item.get("id") not in existing_ids
    ]
    
    print(f"🆕 新規記事: {len(unique_new)}件")
    
    merged = unique_new + old_items
    trimmed = merged[:MAX_ITEMS]
    
    if len(merged) > MAX_ITEMS:
        print(f"✂️ {len(merged) - MAX_ITEMS}件を削除（上限{MAX_ITEMS}件）")
    
    return trimmed

# ==================== HTML生成 ====================

def render_header(page_title="VTuberまとめのまとめ | 最新ニュース一覧"):
    """HTMLヘッダー（共通）"""
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="VTuberまとめサイトの最新情報を一括チェック。にじホロ速、やらおん等の人気サイトから2時間ごとに自動収集。">
<meta name="keywords" content="VTuber,まとめ,にじさんじ,ホロライブ,最新ニュース,やらおん,にじホロ速">
<title>{page_title}</title>
<link rel="stylesheet" href="/style.css">
<link rel="canonical" href="https://3457457547.github.io/vtuber-news-aggregator/">
<meta property="og:title" content="VTuberまとめのまとめ">
<meta property="og:description" content="VTuber関連の最新まとめ記事を一括チェック">
<meta property="og:type" content="website">
<meta property="og:url" content="https://3457457547.github.io/vtuber-news-aggregator/">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="VTuberまとめのまとめ">
</head>
<body>
<header>
<div class="container">
<h1>📰 VTuberまとめのまとめ</h1>
<p class="subtitle">人気VTuberまとめサイトの最新情報を2時間ごとに更新</p>
</div>
</header>
<main class="container">"""

def render_footer():
    """HTMLフッター（共通）"""
    now = datetime.now(timezone.utc)
    return f"""
</main>
<footer class="site-footer">
<div class="container">
<p>&copy; 2024 VTuberまとめのまとめ | 最終更新: {now.strftime('%Y-%m-%d %H:%M')} UTC</p>
<p class="sources">情報元: {", ".join(ALLOWED_SITES.values())}</p>
</div>
</footer>
</body>
</html>"""

def render_article(item):
    """記事カードHTML"""
    return f"""<article class="post">
<time>{item.get('time_text', '')}</time>
<h2><a href="{item['url']}" target="_blank" rel="noopener noreferrer">{item['title']}</a></h2>
<p class="source">{item['site']}</p>
</article>
"""

def render_ad_block():
    """広告ブロック"""
    return """<div class="ad-block">
<!-- nend広告コードをここに挿入 -->
<p style="color:#999;font-size:0.9rem;">広告エリア（nend審査通過後に表示）</p>
</div>
"""

def render_index(items):
    """トップページHTML生成"""
    html = render_header()
    html += '<div class="list">\n'
    
    for i, item in enumerate(items[:ITEMS_PER_PAGE]):
        html += render_article(item)
        if (i + 1) % 10 == 0 and i < ITEMS_PER_PAGE - 1:
            html += render_ad_block()
    
    html += '</div>\n'
    
    if len(items) > ITEMS_PER_PAGE:
        html += '<nav class="pager"><a href="/page2.html">過去の記事 →</a></nav>\n'
    
    html += render_footer()
    return html

def render_page2(items):
    """2ページ目HTML生成"""
    html = render_header(page_title="過去の記事 - VTuberまとめのまとめ")
    html += '<div class="list">\n'
    
    page2_items = items[ITEMS_PER_PAGE:ITEMS_PER_PAGE * 2]
    
    for i, item in enumerate(page2_items):
        html += render_article(item)
        if (i + 1) % 10 == 0 and i < len(page2_items) - 1:
            html += render_ad_block()
    
    html += '</div>\n'
    html += '<nav class="pager"><a href="/index.html">← 最新記事へ</a></nav>\n'
    html += render_footer()
    return html

def generate_css():
    """スタイルシートを生成"""
    return """:root {
  --primary: #0066cc;
  --text: #1a1a1a;
  --text-light: #666;
  --border: #e5e5e5;
  --bg: #fafafa;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  line-height: 1.6;
  color: var(--text);
  background: var(--bg);
}

header {
  background: #fff;
  border-bottom: 1px solid var(--border);
  padding: 1.5rem 0;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  position: sticky;
  top: 0;
  z-index: 100;
}

.container {
  max-width: 900px;
  margin: 0 auto;
  padding: 0 1.5rem;
}

h1 {
  font-size: 1.75rem;
  font-weight: 700;
  color: var(--primary);
  margin-bottom: 0.25rem;
}

.subtitle {
  color: var(--text-light);
  font-size: 0.95rem;
}

main {
  padding: 2rem 0;
  min-height: 60vh;
}

.list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

article.post {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.25rem;
  transition: all 0.2s ease;
}

article.post:hover {
  box-shadow: 0 4px 12px rgba(0,0,0,0.08);
  transform: translateY(-2px);
}

time {
  font-size: 0.85rem;
  color: var(--text-light);
  display: block;
  margin-bottom: 0.5rem;
}

article.post h2 {
  font-size: 1.1rem;
  font-weight: 500;
  line-height: 1.5;
  margin-bottom: 0.5rem;
}

article.post h2 a {
  color: var(--text);
  text-decoration: none;
}

article.post h2 a:hover {
  color: var(--primary);
  text-decoration: underline;
}

.source {
  font-size: 0.85rem;
  color: var(--text-light);
  background: var(--bg);
  display: inline-block;
  padding: 0.25rem 0.75rem;
  border-radius: 4px;
  font-weight: 500;
}

.ad-block {
  margin: 1.5rem 0;
  padding: 1.5rem;
  background: #f9f9f9;
  border: 1px dashed var(--border);
  border-radius: 8px;
  text-align: center;
  min-height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
}

nav.pager {
  text-align: center;
  margin: 3rem 0;
}

nav.pager a {
  display: inline-block;
  padding: 0.75rem 2rem;
  background: var(--primary);
  color: #fff;
  text-decoration: none;
  border-radius: 6px;
  font-weight: 500;
  transition: all 0.2s ease;
}

nav.pager a:hover {
  background: #0052a3;
  transform: translateY(-2px);
  box-shadow: 0 4px 8px rgba(0,102,204,0.3);
}

.site-footer {
  background: #fff;
  border-top: 1px solid var(--border);
  padding: 2rem 0;
  margin-top: 4rem;
  text-align: center;
  color: var(--text-light);
  font-size: 0.9rem;
}

.site-footer .sources {
  margin-top: 0.5rem;
  font-size: 0.85rem;
}

@media (max-width: 768px) {
  .container {
    padding: 0 1rem;
  }
  h1 {
    font-size: 1.4rem;
  }
  .subtitle {
    font-size: 0.85rem;
  }
  article.post {
    padding: 1rem;
  }
  article.post h2 {
    font-size: 1rem;
  }
  nav.pager a {
    padding: 0.6rem 1.5rem;
    font-size: 0.95rem;
  }
  .ad-block {
    min-height: 100px;
    padding: 1rem;
  }
}
"""

def generate_robots_txt():
    """robots.txt生成"""
    return """User-agent: *
Allow: /

Sitemap: https://3457457547.github.io/vtuber-news-aggregator/sitemap.xml
"""

def generate_sitemap(items):
    """sitemap.xml生成"""
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sitemap += '  <url>\n'
    sitemap += '    <loc>https://3457457547.github.io/vtuber-news-aggregator/</loc>\n'
    sitemap += '    <changefreq>hourly</changefreq>\n'
    sitemap += '    <priority>1.0</priority>\n'
    sitemap += '  </url>\n'
    
    if len(items) > ITEMS_PER_PAGE:
        sitemap += '  <url>\n'
        sitemap += '    <loc>https://3457457547.github.io/vtuber-news-aggregator/page2.html</loc>\n'
        sitemap += '    <changefreq>daily</changefreq>\n'
        sitemap += '    <priority>0.8</priority>\n'
        sitemap += '  </url>\n'
    
    sitemap += '</urlset>'
    return sitemap

def write_files(items):
    """ファイル書き込み"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    print("📄 index.html 生成中...")
    (OUTPUT_DIR / "index.html").write_text(render_index(items), encoding="utf-8")
    
    print("📄 page2.html 生成中...")
    (OUTPUT_DIR / "page2.html").write_text(render_page2(items), encoding="utf-8")
    
    print("🎨 style.css 生成中...")
    (OUTPUT_DIR / "style.css").write_text(generate_css(), encoding="utf-8")
    
    print("🔍 robots.txt 生成中...")
    (OUTPUT_DIR / "robots.txt").write_text(generate_robots_txt(), encoding="utf-8")
    
    print("🗺️ sitemap.xml 生成中...")
    (OUTPUT_DIR / "sitemap.xml").write_text(generate_sitemap(items), encoding="utf-8")
    
    print("✅ すべてのファイル生成完了")

def main():
    """メイン処理"""
    print("=" * 60)
    print("VTuber News Aggregator - 起動")
    print("=" * 60)
    
    ensure_dirs()
    old_items = load_cache()
    html = fetch_html(BASE_URL)
    
    if not html:
        print("⚠️ HTML取得失敗 - キャッシュから生成します")
        if old_items:
            write_files(old_items)
            print("✅ キャッシュから生成完了")
        else:
            print("❌ キャッシュも存在しません")
            sys.exit(1)
        return
    
    new_items = parse_timeline(html)
    
    if not new_items:
        print("⚠️ 記事が抽出できませんでした")
        if old_items:
            print("📦 キャッシュから生成します")
            write_files(old_items)
            print("✅ キャッシュから生成完了")
        else:
            print("❌ 生成するデータがありません")
            sys.exit(1)
        return
    
    merged_items = dedupe_and_merge(old_items, new_items)
    save_cache(merged_items)
    write_files(merged_items)
    
    print("=" * 60)
    print("✅ 処理完了")
    print(f"📊 総記事数: {len(merged_items)}件")
    print("=" * 60)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ エラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
