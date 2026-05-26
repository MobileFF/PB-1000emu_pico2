# Refactor Inventory

## Scope

この文書は、`mp/main.py` 周辺のリファクタリングで切り出した責務と、今後の整理候補を記録するための棚卸しである。

目的は次の 2 点である。

- 現在どこまで責務分離が進んだかを明確にする
- 次に整理すべき箇所を重複なく決められるようにする

## Current Status

`mp/main.py` は、当初の巨大な単一エントリポイントから、オーケストレーション中心の構成へかなり整理された。

現状の主な分離先は次の通り。

- `mp/main_boot.py`
- `mp/main_input.py`
- `mp/main_runtime.py`
- `mp/main_actions.py`
- `mp/main_diag.py`
- `mp/main_cleanup.py`

## Responsibilities Already Extracted

### Pass 1

- UART キー入力処理を `KeyboardInputManager` へ移動
- タッチ入力処理を `TouchInputManager` へ移動
- `main.py` から入力の詳細実装を分離

### Pass 2

- UART コンソール初期化を `main_boot.py` へ移動
- ディスプレイと `PB1000System` 初期化を `main_boot.py` へ移動
- ROM 読み込みと USB / PIO UART 初期化を `main_boot.py` へ移動

### Pass 3

- PIO UART MMIO ブリッジ処理を `main_runtime.py` へ移動

### Pass 4

- スクリーンショット保存と VRAM ダンプを `main_actions.py` へ移動
- save-state 要求処理を `main_actions.py` へ移動

### Pass 5

- wake trace 用のスナップショット、整形、トレース処理を `main_diag.py` へ移動

### Pass 6

- 終了時のワークエリア出力とメモリダンプ処理を `main_cleanup.py` へ移動
- `main.py` に残っていた未使用 wake-trace 定数を削除

### Pass 7

- CPU slice 実行を `main_runtime.py` へ移動
- フレーム更新スケジューリングを `main_runtime.py` へ移動
- timer tick 処理を `main_runtime.py` へ移動

## Responsibilities Still Present In `main.py`

現状の `main.py` に残っている責務は次の通り。

- エントリポイントとしての起動順序の組み立て
- 主要設定値の定義
- 入力マネージャ生成
- メインループのオーケストレーション
- 例外処理と終了時クリーンアップ呼び出し

これらは概ね妥当であり、入力マネージャ生成は通常起動時の構成を `main.py` で見通しやすくするため、この位置に維持する。

## Candidate Obsolete Or Risky Items

次の項目は削除候補、または debug 専用扱いで整理候補とする。

- 通常実行パスでは不要になった REPL 経由キー入力
- 暫定デバッグエントリポイントに蓄積しやすい重複初期化と重複実行ループ
- 将来 `main_*` モジュールが増えた場合のフラット構成のままの運用

## Candidate Inventory Table

| 対象 | 定義ファイル | 主な呼び出し元 | 用途 | 現時点で必要か | 判定 |
| --- | --- | --- | --- | --- | --- |
| `_create_input_managers()` | `mp/main.py` | `mp/main.py` | 入力マネージャ生成 | 必要 | `main.py` に維持 |
| 将来のデバッグ用派生エントリポイント | 未定 | 未定 | トレースや調査用の派生実行 | 将来必要 | 通常系安定後に再設計 |
| wake trace 補助 | `mp/main_diag.py` | debug 用経路 | wake 診断 | 通常実行では不要 | debug 専用整理候補 |

## Next Extraction / Cleanup Candidates

優先度順では次を推奨する。

1. `doc/regression_checklist.md` を先に整備して現状挙動を固定する
2. 通常系の構成が固まった後に、必要なデバッグ用エントリポイントだけ再作成する
3. 将来の `mp/runtime/` `mp/input/` などのパッケージ化の条件を決める

## Related Documents

- `doc/architecture.md`
- `doc/regression_checklist.md`
- `references/next_phase_plan.md`
