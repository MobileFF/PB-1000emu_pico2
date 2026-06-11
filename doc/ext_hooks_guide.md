# 搭載済み汎用フック リファレンス

エミュレータに標準搭載されている汎用サブルーチンフックの一覧と BASIC からの使い方。

ハードウェア専用フック（DHT20 温湿度センサー等）は本書の対象外。  
フックの仕組みや独自フックの作り方は [extension_api.md](extension_api.md) を参照。

---

## 共通ルール

### ワークエリア

| アドレス | 方向 | 内容 |
|---|---|---|
| `&H5F00` | OUT | 結果コード（CALL 後に PEEK で確認） |
| `&H5F01` 以降 | IN/OUT | フックごとのパラメータ／結果データ |

### 結果コードの確認パターン

```basic
CALL &H5Exx
IF PEEK(&H5F00)<>0 THEN PRINT "ERR:";PEEK(&H5F00): END
```

---

## bank_loader.py

SDカードのバイナリファイル、または仮想FDDのディスクイメージ内ファイルを  
バンク1〜3のRAMに転送する。

### CALL &H5E81 — SDカード → バンクRAM 転送

#### 入力パラメータ（CALL 前に POKE）

| オフセット | 内容 |
|---|---|
| `&H5F00` | バンク番号（1〜3） |
| `&H5F01` | 転送先オフセット 上位バイト（バンク内相対、0x0000〜0x7FFF） |
| `&H5F02` | 転送先オフセット 下位バイト |
| `&H5F03` | ファイル読み出し開始位置 上位バイト（0=先頭から） |
| `&H5F04` | ファイル読み出し開始位置 下位バイト |
| `&H5F05` | 最大読み込みバイト数 上位バイト（0x0000=全て） |
| `&H5F06` | 最大読み込みバイト数 下位バイト |
| `&H5F07`〜 | ファイルパス（ヌル終端 ASCII 文字列） |

#### 出力（CALL 後に PEEK）

| オフセット | 内容 |
|---|---|
| `&H5F00` | 結果コード（下表参照） |
| `&H5F01` | 転送バイト数 上位バイト |
| `&H5F02` | 転送バイト数 下位バイト |

| 結果コード | 意味 |
|---|---|
| `0x00` | 正常終了 |
| `0x01` | バンク未搭載 |
| `0x02` | ファイル未発見 / 読み取りエラー |
| `0xFF` | その他エラー |

#### 使用例

```basic
1000 REM --- LOAD /sd/game.bin TO BANK 2 OFFSET 0 ---
1010 POKE &H5F00, 2      : REM BANK 2
1020 POKE &H5F01, &H00   : REM DEST OFFSET HI
1030 POKE &H5F02, &H00   : REM DEST OFFSET LO
1040 POKE &H5F03, 0      : REM FILE OFFSET HI
1050 POKE &H5F04, 0      : REM FILE OFFSET LO
1060 POKE &H5F05, 0      : REM MAX LEN HI (0=ALL)
1070 POKE &H5F06, 0      : REM MAX LEN LO
1080 REM SET FILENAME "/sd/game.bin" + NUL
1090 F$="/sd/game.bin"
1100 FOR I=1 TO LEN(F$)
1110   POKE &H5F06+I, ASC(MID$(F$,I,1))
1120 NEXT I
1130 POKE &H5F07+LEN(F$), 0   : REM NUL TERMINATOR
1140 CALL &H5E81
1150 IF PEEK(&H5F00)<>0 THEN PRINT "LOAD ERR:";PEEK(&H5F00): END
1160 N=PEEK(&H5F01)*256+PEEK(&H5F02)
1170 PRINT "LOADED:";N;"BYTES"
```

> **注意**: ファイルパスの書き込みは `&H5F07` から始まる。  
> 上の例では `&H5F06+1 = &H5F07` となるため正しく動作する。

---

### CALL &H5E91 — 仮想FDD → バンクRAM 転送

仮想FDDのディスクイメージに含まれるファイルを指定バンクへ転送する。  
事前に仮想FDDが有効になっている必要がある。

#### 入力パラメータ（CALL 前に POKE）

| オフセット | 内容 |
|---|---|
| `&H5F00` | バンク番号（1〜3） |
| `&H5F01` | 転送先オフセット 上位バイト |
| `&H5F02` | 転送先オフセット 下位バイト |
| `&H5F03` | スキップレコード数（0=先頭から、1レコード=256バイト） |
| `&H5F04`〜`&H5F0E` | ファイル名 11バイト（下記「FDDファイル名形式」参照） |

#### 出力（CALL 後に PEEK）

| オフセット | 内容 |
|---|---|
| `&H5F00` | 結果コード（下表参照） |
| `&H5F01` | 転送バイト数 上位バイト |
| `&H5F02` | 転送バイト数 下位バイト |

| 結果コード | 意味 |
|---|---|
| `0x00` | 正常終了 |
| `0x01` | バンク未搭載 |
| `0x02` | ファイル未発見 |
| `0x03` | 仮想FDD 未接続 |
| `0xFF` | その他エラー |

#### FDDファイル名形式

MD-100 DOS のディレクトリエントリと同じ 11バイト固定長形式。  
ファイル名8バイト＋拡張子3バイト、いずれも半角スペースで右パディング。

| ファイル名 | 11バイト表現 | ASCII コード列 |
|---|---|---|
| `GAME.BAS` | `GAME    BAS` | 71,65,77,69,32,32,32,32,66,65,83 |
| `DATA.BIN` | `DATA    BIN` | 68,65,84,65,32,32,32,32,66,73,78 |
| `PROGRAM.BAS` | `PROGRAM BAS` | 80,82,79,71,82,65,77,32,66,65,83 |

#### 使用例

```basic
1000 REM --- LOAD "GAME.BAS" FROM FDD TO BANK 2 OFFSET 0 ---
1010 POKE &H5F00, 2      : REM BANK 2
1020 POKE &H5F01, &H00   : REM DEST OFFSET HI
1030 POKE &H5F02, &H00   : REM DEST OFFSET LO
1040 POKE &H5F03, 0      : REM SKIP RECORDS
1050 REM FILENAME "GAME    BAS" (11 BYTES)
1060 FOR I=0 TO 10
1070   READ C
1080   POKE &H5F04+I, C
1090 NEXT I
1100 DATA 71,65,77,69,32,32,32,32,66,65,83
1110 CALL &H5E91
1120 IF PEEK(&H5F00)<>0 THEN PRINT "LOAD ERR:";PEEK(&H5F00): END
1130 N=PEEK(&H5F01)*256+PEEK(&H5F02)
1140 PRINT "LOADED:";N;"BYTES"
```

---

## ram_test.py

BANK2/3 のバンク切替フックとRAMテストを提供する。  
バンク切替フックは POKE/PEEK で直接バンクRAMにアクセスする際に使用する。

### CALL &H5E41 — データバンクを BANK2 に切替

UA レジスタ bit[5:4] を `10`（BANK2）に設定する。  
このCALL以降、`&H8000`〜`&HFFFF` へのアクセスが BANK2 RAM にルーティングされる。

パラメータ・戻り値なし。

```basic
CALL &H5E41     ' BANK2 を選択
POKE &H8000, 42
PRINT PEEK(&H8000)   ' 42
```

---

### CALL &H5E51 — データバンクを BANK3 に切替

UA レジスタ bit[5:4] を `11`（BANK3）に設定する。  
このCALL以降、`&H8000`〜`&HFFFF` へのアクセスが BANK3 RAM にルーティングされる。

パラメータ・戻り値なし。

```basic
CALL &H5E51     ' BANK3 を選択
POKE &H8000, 99
PRINT PEEK(&H8000)   ' 99
```

---

### CALL &H5E61 — データバンクを元に戻す

UA レジスタ bit[5:4] を `00` にクリアし、通常状態（ROM領域）に戻す。  
BANK2/3 操作が終わったら必ずこの CALL を実行すること。

パラメータ・戻り値なし。

```basic
CALL &H5E41     ' BANK2 を選択
POKE &H8000, 42
CALL &H5E61     ' バンクを元に戻す
```

---

### CALL &H5E71 — BANK2/3 RAM 全テスト実行

搭載されている BANK2/3 RAM に対してライト/リードベリファイを実行し、  
結果をシリアルコンソール（REPL）に出力する。

テスト内容：
- **Test1**: キーアドレス × 複数パターン（R/W）
- **Test2**: 先頭256バイト 連番パターン
- **Test3**: 末尾256バイト 逆順パターン
- **Test4**: BANK2/3 独立性（同アドレスに別値書き込み）

#### 出力（CALL 後に PEEK）

| オフセット | 内容 |
|---|---|
| `&H5F00` | `0x00`=全テスト合格 / `0x01`=1件以上失敗 |
| `&H5F01` | 合格テスト数 |
| `&H5F02` | 失敗テスト数 |

#### 使用例

```basic
1000 CALL &H5E71
1010 IF PEEK(&H5F00)=0 THEN PRINT "ALL PASSED": END
1020 PRINT "FAILED:";PEEK(&H5F02);" / PASSED:";PEEK(&H5F01)
```

---

## フックアドレス 一覧

| アドレス | モジュール | 機能 |
|---|---|---|
| `&H5E41` | ram_test | データバンク → BANK2 |
| `&H5E51` | ram_test | データバンク → BANK3 |
| `&H5E61` | ram_test | データバンク → 元に戻す |
| `&H5E71` | ram_test | BANK2/3 RAM 全テスト |
| `&H5E81` | bank_loader | SDカードファイル → バンクRAM |
| `&H5E91` | bank_loader | FDDファイル → バンクRAM |
