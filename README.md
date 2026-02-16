# 🔍 新人VTuber発掘所

YouTube Data API v3 を使って新人VTuberを自動発掘し、静的HTMLサイトとして公開するシステム。

## 🚀 セットアップ

### 1. GitHub Secrets に登録
- `YOUTUBE_API_KEY` — YouTube Data API v3 のAPIキー（必須）
- `OPENAI_API_KEY` — ChatGPT API キー（任意、未設定でもフォールバック紹介文を生成）

### 2. ファイル配置
```
リポジトリ/
├── scrape.py                  # メインスクリプト
├── requirements.txt
├── CNAME                      # vtuber-matome.net
├── .github/workflows/
│   └── update.yml             # GitHub Actions
├── cache/
│   ├── candidates.json        # 候補リスト（自動生成）
│   └── approved.json          # 承認済みリスト
└── public/                    # GitHub Pages 公開ディレクトリ
    ├── index.html
    ├── style.css
    ├── robots.txt
    ├── sitemap.xml
    └── vtuber/                # 個別紹介ページ
        └── xxxxxxxx.html
```

## 📋 使い方

### 自動実行（毎日）
GitHub Actions が毎日 9:00 JST に自動実行し、新人VTuber候補を収集します。

### 手動: 候補の承認（週1回・15分）

#### 方法A: CLI（ローカル実行）
```bash
export YOUTUBE_API_KEY="your-key"
export OPENAI_API_KEY="your-key"  # オプション

# 候補を収集
python scrape.py collect

# 候補を確認・承認
python scrape.py approve

# HTMLだけ再生成
python scrape.py generate

# ステータス確認
python scrape.py status
```

#### 方法B: GitHub Actions手動実行
1. リポジトリ → Actions → 「新人VTuber発掘」
2. "Run workflow" → mode を選択 → 実行

#### 方法C: Google Spreadsheet（将来実装予定）
スプレッドシートからチェックを入れるだけで承認できる仕組み。

## ⚙️ カスタマイズ

### フィルタリング条件を変更
`scrape.py` の設定セクションを編集:
```python
MAX_SUBSCRIBERS = 1000        # 登録者数上限
MAX_CHANNEL_AGE_DAYS = 90     # チャンネル開設日数上限
MIN_VIDEOS = 3                # 最低動画数
```

### 検索キーワードを追加
```python
SEARCH_QUERIES = [
    "新人VTuber",
    "VTuberデビュー",
    # ここに追加
]
```

### VTuber判定キーワードを追加
```python
VTUBER_KEYWORDS = [
    "vtuber", "バーチャル",
    # ここに追加
]
```

## 📊 APIクォータ使用量

| APIコール | コスト/回 | 日次使用 | 日次合計 |
|-----------|----------|---------|---------|
| search.list | 100 | 5クエリ | 500 |
| channels.list | 1 | ~50ch | 50 |
| videos.list (承認時) | 1 | ~10 | 10 |
| **合計** | | | **~560 / 10,000** |

## 🔧 トラブルシューティング

### APIクォータ超過
- エラー: `403 Forbidden`
- 対策: 翌日まで待つか、Google Cloud Console でクォータ確認

### 候補が見つからない
- 検索キーワードを増やす
- `MAX_CHANNEL_AGE_DAYS` を増やす（例: 180日）
- `MAX_SUBSCRIBERS` を増やす（例: 3000人）
