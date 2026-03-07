# yutamoty-com

[yutamoty.com](https://yutamoty.com/) のソースコード。Hugo + Cloudflare Pages で運用。

## 構成

- `content/` - Markdown コンテンツ
  - `_index.md` - トップページ
  - `posts/` - ブログ記事
- `themes/yutamoty/` - カスタムテーマ
- `hugo.toml` - Hugo 設定

## ローカル開発

```bash
hugo server -D
```

## 更新方法

1. `content/` 配下の Markdown ファイルを編集
2. コミット & プッシュ
3. Cloudflare Pages が自動でビルド・デプロイ

### Cloudflare Pages 設定

- ビルドコマンド: `hugo`
- 出力ディレクトリ: `public`
- 環境変数: `HUGO_VERSION` = `0.145.0`（またはそれ以降）
