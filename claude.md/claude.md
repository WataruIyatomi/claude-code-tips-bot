# Claude Code Tips Bot

## 概要
Claude Codeの裏技・隠しコマンド・有効活用事例を毎朝自動収集し、
Slackに日本語で投稿するボット。毎日10つくらい投稿して欲しい

## 技術スタック
- Python 3.11+
- requests + BeautifulSoup4（スクレイピング）
- GitHub Actions（スケジュール実行、毎朝6時JST）
- Slack Incoming Webhook（通知）
- 外部APIキーは一切不要

## 作成するファイル
- bot.py（メインスクリプト）
- .github/workflows/daily.yml（GitHub Actionsワークフロー）
- sources.json（収集ソース一覧）
- seen_tips.json（重複排除キャッシュ、初期値は{}）
- requirements.txt
- README.md（セットアップ手順付き）

## 収集ソース
- https://dev.to/t/claudecode（DEV.to）
- https://www.reddit.com/r/ClaudeAI/.json（Reddit API）
- https://github.com/Njengah/claude-code-cheat-sheet（GitHub）
- https://hannahstulberg.substack.com（Substack）
- https://www.sabrina.dev（個人ブログ）
他にもどこでも良い

## Slackメッセージフォーマット
🧠 Claude Code Tips — 今日の1本 (YYYY/MM/DD)
💡 [タイトル]
[150字以内の日本語説明]
📂 カテゴリ: [ショートカット / スラッシュコマンド / CLI / CLAUDE.md / MCP / エージェント]
🔗 ソース: [URL]

## セキュリティ要件
- Webhook URLは環境変数 SLACK_WEBHOOK_URL から読む（ハードコード禁止）
- seen_tips.jsonはURLのハッシュで重複管理（個人情報は一切保存しない）
- スクレイピングはUser-Agentを適切に設定し、robots.txtを尊重する
- GitHub ActionsではSecretsのみ使用し、ログにURLを出力しない

## 動作仕様
1. sources.jsonの各URLからtipsを収集
2. seen_tips.jsonと照合して未投稿のものを抽出
3. 最も新鮮・有用なtipsを1件選択
4. 日本語に整形してSlackに投稿
5. seen_tips.jsonを更新してコミット