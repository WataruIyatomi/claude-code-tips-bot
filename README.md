# Claude Code Tips Bot

Claude Code の裏技・隠しコマンド・有効活用事例を毎朝自動収集し、Slack に日本語で投稿するボット。

毎日最大10件を個別のSlackメッセージとして投稿します。

## 動作フロー

1. `sources.json` に定義されたURLからtipsを収集
2. `seen_tips.json` と照合して未投稿のみ抽出（SHA256ハッシュで重複管理）
3. スコアリングで上位10件を選択
4. 1件ずつSlackに投稿
5. `seen_tips.json` を更新してリポジトリにコミット

## セットアップ

### 1. リポジトリを Fork / Clone

```bash
git clone https://github.com/<your-username>/claude-code-tips-bot.git
cd claude-code-tips-bot
```

### 2. Slack Incoming Webhook を作成

1. [Slack API](https://api.slack.com/apps) → "Create New App" → "From scratch"
2. "Incoming Webhooks" を有効化
3. "Add New Webhook to Workspace" でチャンネルを選択
4. 生成された Webhook URL をコピー

### 3. GitHub Secrets に登録

リポジトリの **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `SLACK_WEBHOOK_URL` | `https://hooks.slack.com/services/...` |

### 4. Actions を有効化

リポジトリの **Actions** タブ → "I understand my workflows, go ahead and enable them"

### 5. 手動テスト実行

**Actions → Daily Claude Code Tips → Run workflow**

## ローカルテスト

```bash
pip install -r requirements.txt
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
python bot.py
```

## ファイル構成

```
claude-code-tips-bot/
├── bot.py                        # メインスクリプト
├── sources.json                  # 収集ソース定義
├── seen_tips.json                # 重複排除キャッシュ（自動更新）
├── requirements.txt              # Pythonパッケージ
├── .github/workflows/daily.yml  # GitHub Actions（毎朝JST 06:00）
└── README.md
```

## 収集ソース

| ソース | URL | 形式 |
|---|---|---|
| DEV.to | `https://dev.to/t/claudecode` | HTML |
| Reddit r/ClaudeAI | `https://www.reddit.com/r/ClaudeAI/.json` | JSON |
| GitHub Cheatsheet | `https://github.com/Njengah/claude-code-cheat-sheet` | Markdown |
| Sabrina.dev | `https://www.sabrina.dev` | HTML |
| Anthropic Blog | `https://www.anthropic.com/news` | HTML |

`sources.json` を編集することで収集ソースを追加・変更できます。

## Slackメッセージ形式

```
🧠 Claude Code Tips — 今日の10本 (2026/05/16)  [1/10]
💡 タイトル
150字以内の日本語説明
📂 カテゴリ: ショートカット / スラッシュコマンド / CLI / CLAUDE.md / MCP / エージェント
🔗 ソース: https://...
```

## エラー時の動作

| 状況 | 動作 |
|---|---|
| 個別ソースのネットワークエラー | そのソースをスキップして継続 |
| 新着tips 0件 | 「本日は新しいtipsが見つかりませんでした」をSlack投稿 |
| Slack投稿失敗（3回リトライ後） | GitHub Actions を失敗扱い（exit code 1） |
| 予期せぬエラー | 「本日のtips収集に失敗しました」をSlack通知して失敗終了 |

## セキュリティ

- Webhook URLは環境変数 `SLACK_WEBHOOK_URL` から読み込み（ハードコード禁止）
- 重複管理はURLのSHA256ハッシュのみ保存（個人情報は一切保存しない）
- スクレイピング時は robots.txt を尊重
- GitHub Secrets はログに出力されない
