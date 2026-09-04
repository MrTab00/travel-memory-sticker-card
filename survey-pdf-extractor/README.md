# アンケートPDF 自動抽出・集計ツール

紙のアンケート（顧客記入済み）をスキャンした PDF から、Claude の Vision で回答を読み取り、
Excel の集計表として出力します。手書きの自由記述と選択式が混在した用紙を想定しています。

**テキストレイヤーのないスキャン PDF が前提**です。pdfplumber / PyPDF2 によるテキスト抽出は
一切行わず、PDF を document ブロックとして Claude に直接渡して画像として読ませます。

---

> **入力 PDF は手元の PC から Anthropic API に直接送られます。**
> 顧客の記入済みアンケートを外部 API に送ることになるため、社内規程上の可否を先にご確認ください。

---

## 1. セットアップ

### Mac をお使いの場合（最短ルート）

ターミナル（アプリケーション → ユーティリティ → ターミナル）を開いて、上から順に貼り付けるだけです。

```bash
cd ~/Documents
git clone -b claude/survey-pdf-extraction-tool-vqb9pz https://github.com/MrTab00/travel-memory-sticker-card.git
cd travel-memory-sticker-card/survey-pdf-extractor

bash setup_mac.sh          # Python の確認・ライブラリ導入・動作確認まで自動
```

`setup_mac.sh` は仮想環境の作成から動作確認（API を呼ばないセルフテスト）までを行い、
最後に次にやることを表示します。**何度実行しても壊れません。**
途中で止まった場合は、表示されたメッセージのとおりに直してもう一度実行してください。

続けて、キーの設定と実行:

```bash
# APIキーは .env ファイルに書く（ターミナルに入力しない＝履歴に残らない）
open -e .env               # setup_mac.sh が作成済み。最終行を次の形にして保存
                           #   ANTHROPIC_API_KEY=sk-ant-...

# スキャンPDFを input/ に入れる（Finder からドラッグ＆ドロップで可）
open .

./run.sh --show-groups     # 右上の手書き番号でのグループ分けを確認（課金なし）
./run.sh --dry-run         # 概算コストを確認（課金なし）
./run.sh --only No01       # まず1名だけ。原本と突き合わせる
./run.sh                   # 全員分 → output/aggregate_YYYYMMDD.xlsx

open output                # 結果のフォルダを Finder で開く
```

**Mac でよくあるつまずき**

| 症状 | 対処 |
|---|---|
| `command not found: git` | 初回は Xcode Command Line Tools の導入ダイアログが出ます。「インストール」を押して完了後に再実行 |
| `使える Python (3.10 以上) が見つかりません` | macOS 標準の python3 は 3.9 で更新できません。`brew install python@3.12`、または [python.org](https://www.python.org/downloads/macos/) の「macOS 64-bit universal2 installer」を入れて `bash setup_mac.sh` を再実行（PATH が古いままでもスクリプトが新しい方を探します） |
| `./run.sh: Permission denied` | `chmod +x run.sh setup_mac.sh` を実行 |
| `pip install` が SSL や proxy で失敗 | 社内ネットワークの可能性。`--proxy http://<プロキシ:ポート>` を付ける |
| `.env` が見つからない | `cp .env.example .env && chmod 600 .env` を実行 |

---

**Windows（PowerShell）**

```powershell
git clone -b claude/survey-pdf-extraction-tool-vqb9pz https://github.com/MrTab00/travel-memory-sticker-card.git
cd travel-memory-sticker-card\survey-pdf-extractor

py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
notepad .env               # ANTHROPIC_API_KEY=sk-ant-... の行を書いて保存
```

**macOS / Linux**

```bash
git clone -b claude/survey-pdf-extraction-tool-vqb9pz https://github.com/MrTab00/travel-memory-sticker-card.git
cd travel-memory-sticker-card/survey-pdf-extractor

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env && chmod 600 .env
$EDITOR .env               # ANTHROPIC_API_KEY=sk-ant-... の行を書いて保存
```

### APIキーの扱い

キーはコードに書かず、**`.env` ファイル**または環境変数 `ANTHROPIC_API_KEY` から読みます。

- `export ANTHROPIC_API_KEY=...` をターミナルに打つと、キーがシェルの履歴
  （`~/.zsh_history` 等）に残ります。それを避けるため、既定では `.env` に書く方式です
- `.env` は `.gitignore` 済みで、権限 600（自分だけが読める）に設定されます。
  緩んでいた場合は `./run.sh` が自動で直します
- `.env` は `source` されません。中身は `KEY=VALUE` として読むだけなので、
  書かれた文字列がコマンドとして実行されることはありません
- 環境変数が設定されていれば、そちらが優先されます（CI などで使う場合）
- 実行ログにキーは出ません（`sk-ant-...fXYZ` のように伏せ字で表示）
- **必要なときだけ書き、終わったら中身を消す**運用でも動きます

動作確認（API を呼ばず、課金も発生しません）:

```bash
python main.py --self-test
```

`output/selftest/` にダミーデータの Excel が生成されれば、Python 側の環境は正常です。

### 実行の全体像（この順に進めれば終わります）

```powershell
mkdir input                       # ここにスキャンPDFを置く
python main.py --self-test        # 課金なし。環境の確認
python main.py --show-groups      # 右上の番号での回答者の分かれ方を確認
python main.py --dry-run          # 概算コストを確認
python main.py --only No01        # まず1名だけ。原本と突き合わせる
python main.py                    # 全員分 → output\aggregate_YYYYMMDD.xlsx
```

---

## 2. 設問を定義する（最初に必ず行う作業）

`questions.yaml` を実際のアンケートに合わせて書き換えてください。
**このファイルが抽出スキーマ・Excel の列・集計方法のすべての元になります。**
Python コードを編集する必要はありません。

```yaml
questions:
  - id: Q1
    type: single_choice          # 選択式（1つ選ぶ）
    text: 貴社の業種は？
    choices: [製造業, 商社, その他]

  - id: Q3
    type: multi_choice           # 複数選択
    text: 関心のある規制
    choices: [CRA, NIS2, JC-STAR, EU Data Act]

  - id: Q4
    type: free_text              # 自由記述（手書き）
    text: 現在の課題
    note: 要約せず原文のまま         # 任意。読み取り時の注意をモデルに伝える

  - id: Q5
    type: number                 # 数値
    text: 対象製品数
    unit: 製品                    # 任意
```

| type | 意味 | Excel の値 | 集計 |
|---|---|---|---|
| `single_choice` | 選択式（1つ） | 選択肢の文字列 | 選択肢別の件数・構成比 |
| `multi_choice` | 複数選択 | `A / B` のように連結 | 選択肢ごとに独立カウント |
| `free_text` | 自由記述 | 原文のまま | 記入件数のみ |
| `number` | 数値 | 数値（Excel でも数値型） | 件数・平均・中央値・最小・最大 |
| `rating` | 5段階評価などの尺度 | 丸が付いた数字（数値型） | 件数・**平均・中央値**・最小・最大 ＋ 段階別の件数・構成比 |

`rating` は「○をつけてください」形式の満足度設問用です。`scale`（範囲）と `labels`
（各段階の文言）を指定します。同じ尺度を何度も書かずに済むよう、YAML のアンカーが使えます。

```yaml
x-scale5: &scale5          # ファイル先頭で1度だけ定義
  1: 非常に不満足
  2: やや不満足
  3: どちらともいえない
  4: やや満足
  5: 非常に満足

questions:
  - id: Q2_1
    type: rating
    text: ワークショップ全体の満足度
    scale: [1, 5]
    labels: *scale5        # ← 使い回す
```

**モデルには「丸が付いている数字」だけを見させ、選択肢の文言や他の設問の内容から
点数を推測することを禁止しています。**

`meta:` と `model:` セクションでページ数・レイアウト・モデル・解像度なども変更できます
（詳細は `questions.yaml` 内のコメント参照）。

---

## 3. 入力 PDF を置く

`input/` ディレクトリを作り、スキャン PDF を置きます。構成は 2 パターンに対応しています。

**(a) 1名 = 1ファイル** — ファイル名（拡張子を除く）がそのまま回答者IDになります。

```
input/
  回答者A.pdf     ← 6ページ
  回答者B.pdf
```

**(b) 全員が1ファイル** — 分け方は2通りあります。

```
input/
  アンケート全員分.pdf   ← 120ページ = 20名 × 6ページ
```

| layout | 分け方 | 向いている場合 |
|---|---|---|
| `by_page_marker`（**推奨・既定**） | **用紙右上の手書き番号**でグループ化 | 回収時に用紙へ通し番号を書いている場合 |
| `single_file` | `pages_per_respondent`（既定 6）ごとに機械的に分割 | 番号を書いていない場合 |

`by_page_marker` は各ページの**右上の隅だけ**を切り出して番号を読み取ります（1ページ約5KB、
コストはごくわずか）。固定ページ数での分割と違い、**1名分がページ抜け・重複・順番違いに
なっても以降の全員がずれることがなく**、「6ページ揃っているか」の検算にもなります。

```bash
# まずグループ分けだけ確認する（抽出はしない）
python main.py --show-groups

# 出力例
# 回答者ID        ページ                 ページ数  右上の番号
# ------------------------------------------------------------
# No01           p1-6                      6    1
# No02           p7-12                     6    2
# No03           p13-18                    6    3
```

- 番号が読めなかったページは、直前のページと同じ回答者に含めたうえで**必ず警告**を出します
- ページ数が想定と違う／ページが連続していないグループも警告に出ます
- 読み取り結果は `output/{ファイル名}_page_markers.json` にキャッシュされ、次回以降は再利用します
  （読み直すときは `--redetect-markers`）
- 番号の位置が右上でない場合は `meta.marker_region: [x0, y0, x1, y1]`（0〜1 の割合）で調整

`meta.layout: auto` にすると (a)/(b) を自動判定します（判定結果は実行時に表示）。
CLI からは `--layout by_page_marker` / `per_respondent` / `single_file` で上書きできます。

### PDF が大きくてアップロード・送信に失敗する場合

**回答者の区切りで分割して、`input/` に複数ファイルとして置いてください。**
複数ファイルでも回答者IDは重複しないよう自動で調整され、最後にまとめて1つの Excel になります。
一部だけ先に処理し、残りを後から追加しても構いません（処理済みは自動でスキップされます）。

```bash
# 例: 30ページ（5名分）ずつ4分割する
python -c "
from pypdf import PdfReader, PdfWriter
r = PdfReader('元ファイル.pdf')
for i in range(0, len(r.pages), 30):
    w = PdfWriter()
    for p in r.pages[i:i+30]:
        w.add_page(p)
    w.write(f'input/part{i//30+1:02d}.pdf')
"
```

---

## 4. 実行

```bash
# 概算コストだけ先に確認する（実際のトークン数を数えるだけで、抽出はしない）
python main.py --dry-run

# まず1名だけ試して、原本と突き合わせる（受け入れ確認の第一歩）
python main.py --only 回答者A

# 全員分を処理して Excel を生成
python main.py
```

進捗はコンソールに `[3/20] 処理中: 回答者C ...` の形式で表示されます。
1名分が失敗しても処理は止まらず、最後に失敗リストがまとめて表示されます。

### よく使うオプション

| オプション | 説明 |
|---|---|
| `--only ID [ID...]` | 指定した回答者だけ処理する |
| `--force` | 抽出済み（`output/raw/*.json` がある）でも再抽出する |
| `--aggregate-only` | API を呼ばず、既存の `output/raw/*.json` だけで Excel を作り直す |
| `--dry-run` | トークン数を数えて概算コストを表示。抽出はしない |
| `--images` | PDF 直送をやめ、最初から 300dpi の画像として送る |
| `--model MODEL` | モデルを一時的に変更する |
| `--layout` / `--pages` | 入力構成・1名あたりページ数を上書きする |
| `--show-groups` | 回答者のグループ分けだけ表示して終了（`by_page_marker` の確認用） |
| `--redetect-markers` | 右上の手書き番号を読み取り直す（キャッシュを無視） |
| `--self-test` | API を使わずダミーデータでパイプラインを検証する |

**再実行は安全です。** `output/raw/{回答者ID}.json` が既にある回答者は自動でスキップされるため、
途中で失敗しても続きから再開でき、API を無駄に再実行しません。

---

## 5. 出力

```
output/
  aggregate_YYYYMMDD.xlsx      ← 集計結果
  raw/
    回答者A.json               ← 抽出結果の生データ（再集計用）
    回答者B.json
```

### `aggregate_YYYYMMDD.xlsx`

| シート | 内容 |
|---|---|
| **生データ** | 1行 = 1回答者、1列 = 1設問。A列に回答者ID。自由記述は原文のまま（要約しません）。信頼度が低いセルには色が付きます（橙 = low、黄 = medium） |
| **集計** | 選択式は選択肢別の件数と構成比、複数選択は選択肢ごとの独立カウント、数値は件数・平均・中央値・最小・最大。各設問の未記入／判読不能の件数も出ます |
| **要確認** | confidence が `low` / `medium` のセルだけを抽出。回答者ID / 設問ID / 設問文 / 抽出値 / 読み取り原文 / 状態 / confidence / 該当ページ / 備考 |

**「要確認」シートは必ず原本と突き合わせてください。**
手書きの誤読は、いったん統計に紛れ込むと後から発見するのが困難です。

### `raw/{回答者ID}.json`

各設問について以下を保存します。集計をやり直すときに API を再実行しなくて済みます。

```json
"Q2": {
  "value": "着手済",              // 正規化後の値（選択式は選択肢と完全一致）
  "raw_text": "検討中を二重線で消し、着手済に丸",  // 実際に読み取った文字列（原文ママ）
  "status": "answered",           // answered / blank（未記入） / unreadable（判読不能）
  "confidence": "medium",         // high / medium / low
  "page": 2,                      // 該当ページ番号
  "flags": []                     // ツール側の検算で付いた警告
}
```

判読不能な場合は空文字ではなく `null` + `confidence: low` が入り、
**未記入（`blank`）と判読不能（`unreadable`）は明確に区別されます。**

---

## 6. 精度のための仕組み

- **構造化出力は tool use（function calling）で強制**しています。
  「JSON だけ返して」というプロンプト指示は前置きが混入するため使っていません。
  `tool_choice` でツール呼び出しを強制し、`input_schema`（`strict` モード）で構造を縛ります。
- プロンプトで以下を明示しています。
  - レ点・丸囲み・塗りつぶし・下線など、チェックの表現の多様さを見落とさない
  - 修正跡（二重線で消して書き直し）がある場合は最終的な回答を採用する
  - 推測で埋めない。読めないものは読めないと返す。未記入と判読不能を区別する
- モデルの出力をそのまま信じず、`normalize.py` で検算します。
  選択肢との突き合わせ（表記ゆれ補正）、全角・単位付き数値のパース、
  `status` と `value` の整合性チェックを行い、**怪しい値は自動で confidence を下げて
  「要確認」シートに必ず出します**（例: `choice_mismatch`, `number_from_raw_text`,
  `rating_out_of_range`）。
- **尺度の取り違え検知**: 5段階評価が全設問で最低値（1）なのに自由記述が書かれている回答者は、
  回答者が尺度の向きを取り違えた／読み取りが反転した可能性があるため、
  値は変えずに confidence を下げて「要確認」に出します（`uniform_min_rating`）。
  不要なら `questions.yaml` の `meta.flag_uniform_min_ratings: false` で無効化できます。

---

## 7. 概算コスト・所要時間

既定モデルは **`claude-sonnet-5`**（$2.00 / 1M 入力トークン、$10.00 / 1M 出力トークン）です。

スキャン A4 1ページはおおむね 1,500〜3,000 入力トークンになります。

| 単位 | 入力 | 出力 | 概算 |
|---|---|---|---|
| 1名（6ページ・24設問） | 約 10,000〜18,000 tok | 約 2,000〜4,000 tok | **$0.04〜0.08（約 6〜12 円）** |
| 20名（120ページ） | 約 20 倍 | 約 20 倍 | **$0.8〜1.6（約 120〜250 円）** |

設問数が多いほど出力トークンが増えます。**実行前に `python main.py --dry-run` を実行すると、
実際の PDF でトークン数を数えた見積りが出ます**（この確認自体に課金は発生しません）。

所要時間は 1名あたり 30〜60 秒程度、20名で 10〜20 分が目安です。
総ページ数 120 程度なので並列化はしていません（逐次処理）。

> 料金は変わりえます。正確な単価は公式の料金ページで確認してください。
> 為替は 1USD = 155円 で換算しています（`survey_extractor/pricing.py` で変更可）。

---

## 8. 受け入れ確認の手順

1. `python main.py --self-test` … API なしで Excel 生成まで通ることを確認
2. `python main.py --dry-run` … 概算コストを確認
3. `python main.py --only <サンプル1名のID>` … **生成された Excel と原本を目視で突き合わせる**
   （特に選択式のチェック位置、修正跡のある設問、数値の桁）
4. 問題なければ `python main.py` で全員分を実行
5. 「要確認」シートに並んだセルを原本と突き合わせ、必要なら「生データ」シートを手で修正

---

## 9. トラブルシューティング

| 症状 | 対応 |
|---|---|
| `APIキーが設定されていません` | `.env` を開き `ANTHROPIC_API_KEY=sk-ant-...` の行を書く |
| `モデルまたはエンドポイントが見つかりません` | `questions.yaml` の `model.name` を確認。モデル文字列は公式ドキュメントで最新を確認してください |
| `temperature` で 400 エラー | Claude Sonnet 5 / Opus 5 など最新世代では `temperature` は廃止されており、送ると 400 になります。既定では送信しません。どうしても `temperature: 0` を使いたい場合は旧世代モデル（例: `claude-sonnet-4-5`）を指定してください。なお本ツールは 400 の内容を見て `temperature` / `thinking` / `strict` などを自動で外して再試行します |
| PDF が大きすぎる / ページが多すぎる | 自動で 300dpi の PNG に変換して送り直します（`--images` で最初から画像にすることも可能） |
| 手書きの誤読が多い | `questions.yaml` の `model.thinking: adaptive` + `model.effort: high` を試す。それでも改善しない場合は `model.name: claude-opus-5` に変更（コストは上がります） |
| `max_tokens に達して出力が途中で切れました` | 設問数が多い場合。`model.max_tokens` を増やす |
| 429 / 5xx エラー | 指数バックオフで最大3回自動リトライします。それでも失敗した回答者は最後にリストされるので、再実行すれば続きから処理されます |

---

## 10. 顧客情報の取り扱い

`input/`（顧客記入済み PDF）、`output/`（中間 JSON・Excel）は
リポジトリルートの `.gitignore` で除外済みです。**絶対にコミットしないでください。**

---

## 11. ファイル構成

```
survey-pdf-extractor/
├── main.py                     エントリポイント
├── setup_mac.sh                Mac 用セットアップ（bash setup_mac.sh）
├── .env.example                APIキー設定ファイルのひな形（.env にコピーして使う）
├── run.sh                      実行用ラッパー（./run.sh --show-groups など）
├── questions.yaml              設問定義（★ここを書き換える）
├── requirements.txt
└── survey_extractor/
    ├── config.py               questions.yaml の読み込み・検証
    ├── env.py                  .env からの APIキー読み込み
    ├── markers.py              用紙右上の手書き番号の読み取りとグループ化
    ├── schema.py               設問定義 → tool use の input_schema
    ├── prompts.py              システムプロンプト・指示文
    ├── pdf_utils.py            PDF の列挙・分割・画像フォールバック
    ├── extractor.py            API 呼び出し・リトライ・結果の組み立て
    ├── normalize.py            出力の正規化と検算（confidence の引き下げ）
    ├── aggregate.py            pandas 結合・集計・Excel 出力
    ├── pricing.py              概算コスト計算
    ├── selftest.py             ダミーデータ生成（API 不要の動作確認用）
    └── cli.py                  コマンドライン処理
```
