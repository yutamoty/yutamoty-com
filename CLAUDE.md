# CLAUDE.md

## プロジェクト概要

yutamoty.com の個人サイト。Hugo で構築し、Cloudflare Pages でホスティング。

## コマンド

- ビルド: `hugo`
- ローカルプレビュー: `hugo server -D`

## 構成

- テーマ: `themes/yutamoty`（カスタムテーマ）
- コンテンツ: `content/` 配下に Markdown で管理
- 記事: `content/posts/` に配置

## デプロイ

Cloudflare Pages で `master` ブランチを自動デプロイ。

- ビルドコマンド: `hugo`
- 出力ディレクトリ: `public`
- 環境変数 `HUGO_VERSION`: `0.145.0`

## 言語

サイトの言語は日本語。コミットメッセージやコメントも日本語で可。
