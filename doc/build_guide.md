# ビルド・ガイド

このガイドでは、PB-1000 エミュレータ用のカスタム MicroPython ファームウェアをビルドし、環境をセットアップする方法を説明します。

## 事前準備

### 1. ツールチェーンと依存関係

#### Windows (PowerShell)
最も高速で安定したビルド体験のために、**WSL2** の使用を推奨します。Windows ネイティブでのビルドも可能です。
```powershell
# CMake, Python, Git をインストール
winget install Kitware.CMake Python.Python.3.11 Git.Git
# ARM GCC Toolchain を以下からダウンロードしてインストール:
# https://developer.arm.com/downloads/-/gnu-rm
```

#### Linux (Ubuntu/Debian) / WSL2
```bash
sudo apt update
sudo apt install -y cmake gcc-arm-none-eabi libnewlib-arm-none-eabi build-essential git python3
```

#### macOS
```bash
brew install cmake gcc-arm-embedded python3
```

## ビルド手順

### 1. MicroPython のクローン
最新の安定版 MicroPython を使用することを推奨します。
```bash
git clone https://github.com/micropython/micropython.git
cd micropython
git submodule update --init --recursive
```

### 2. mpy-cross のビルド
MicroPython のクロスコンパイラが必要です。
```bash
make -C mpy-cross
```

### 3. Pico SDK の準備
MicroPython のツリー内で Pico SDK とそのサブモジュールを初期化します。
```bash
cd ports/rp2
make submodules
```

### 4. PB-1000 モジュールを含めたビルド

> [!IMPORTANT]
> 実機は **Raspberry Pi Pico 2 W (`RPI_PICO2_W`)** と **無印 Raspberry Pi Pico 2 (`RPI_PICO2`)**
> の両方に対応しています。`BOARD=` に指定するボードターゲットと、書き込み先のファイル名
> （`firmware_pb1000_pico2w.uf2` / `firmware_pb1000_pico2.uf2`）を実機に合わせて選んでください。
> CFLAGS・USER_C_MODULES は両ボードで共通です。無印 Pico 2 版は WiFi/Bluetooth コード
> （CYW43 ドライバ等）を含まないため、CYW43439 チップを搭載しない無印機でも起動時に
> `cyw43_init()` 待ちでハングすることなく正常に動作します。

複数の Pico プロジェクトを同じマシンで並行してビルドする場合は、MicroPython のクローン自体を
プロジェクトごとに分けることを強く推奨します。`ports/rp2/build-<BOARD>/` はビルドキャッシュ
（`CMakeCache.txt` の `USER_C_MODULES` を含む）を保持するため、複数プロジェクトで同じ
MicroPython チェックアウトを共有すると、あるプロジェクト向けに構成されたキャッシュを別の
プロジェクトが誤って再利用し、意図しない C モジュールが混入する恐れがあります。

`src/` の C ソースはこのリポジトリ（Google Drive）の `src/micropython.cmake` を直接指すのではなく、
まずビルド用の作業コピー（例: `~/projects/hd61700/src/`）へ同期してから `USER_C_MODULES` に
そのコピー内の `micropython.cmake` の**絶対パス**を指定するのがこのプロジェクトの標準的な手順です
（`src/` を直接編集した場合は必ずこの同期を行ってください。詳細は本リポジトリの
`.claude/rules/firmware-source-location.md` を参照）。

```bash
# 1. GD の src/ をビルド用コピーへ同期
cp -r /path/to/PB-1000_emu_AG2/src/* ~/projects/hd61700/src/
```

### mp/*.py のフリーズ（起動時ヒープ断片化対策）

`mp/`配下の大半の`.py`（`boot.py`/`main.py`と`ext/`配下は除く）は、ビルド時に
バイトコード化してファームウェアのフラッシュへ焼き込む「フリーズ」の対象です。
これにより起動時の`import`がコンパイルなしで済み、起動シーケンス中のGCヒープ
断片化による`MemoryError`を大幅に抑えられます（経緯は`調査用/mp_freeze_tradeoffs.md`参照）。

対象ファイルの一覧とフリーズの定義は、このリポジトリの`src/manifest_freeze.py`が正本です。
ビルド前に、この定義とフリーズ対象ファイル自体をビルド用コピーへ同期してください（`src/`の
同期と同様、`micropython.pb1000`チェックアウトはgit管理外のため、チェックアウトを作り直した
場合は`boards/manifest.py`への`include()`の追記も再度必要です — 下記は冪等なので毎回実行して
構いません）。

```bash
# 2. mp/*.py のフリーズ対象をビルド用コピーへ同期
mkdir -p ~/projects/hd61700/mp
cp /path/to/PB-1000_emu_AG2/mp/*.py ~/projects/hd61700/mp/
cp /path/to/PB-1000_emu_AG2/src/manifest_freeze.py ports/rp2/boards/manifest_freeze.py

# 3. ports/rp2/boards/manifest.py に include() が無ければ追記（チェックアウトを
#    作り直した場合のみ必要。既にあれば何もしない）
grep -q 'manifest_freeze.py' ports/rp2/boards/manifest.py || \
  sed -i '/freeze("\$(PORT_DIR)\/modules")/a include("$(PORT_DIR)/boards/manifest_freeze.py")' \
    ports/rp2/boards/manifest.py
```

`mp/*.py`を追加・削除・リネームした場合は、`src/manifest_freeze.py`のリストも
手動で更新する必要があります（自動検出はしていません — ディレクトリを丸ごと
フリーズする方式だと`ext/`配下まで誤って対象に入ってしまうため、明示的なファイル
リスト方式を採用しています）。

**例 (Linux/WSL2):**
```bash
cd ports/rp2
export USER_C_MODULES="/home/<user>/projects/hd61700/src/micropython.cmake"
export CFLAGS="-Wno-error=unused-parameter -Wno-error=unused-variable -I/home/<user>/projects/hd61700/src"
make BOARD=RPI_PICO2_W USER_C_MODULES="$USER_C_MODULES" clean
make BOARD=RPI_PICO2_W USER_C_MODULES="$USER_C_MODULES" WERROR=0 -j$(nproc)
# 無印 Pico 2 向けは BOARD=RPI_PICO2 に差し替えるだけ
```

これで **PIO-USB ホストモード（GP4/GP5 でキーボード接続、USB-C ポートは CDC デバイス）**が
デフォルトでビルドされます（`src/micropython.cmake` の `option(USE_PIO_USB ... ON)`）。
配線は [Hardware Guide](hardware_guide.md) の「8. USB キーボード」を参照してください。

**従来方式（ネイティブ USB ホスト専用、USB-C ポートはキーボード専用）でビルドする場合**:

```bash
# 新規の build-<BOARD>/ を作る場合 (CMAKE_ARGS で渡せる)
make BOARD=RPI_PICO2_W USER_C_MODULES="$USER_C_MODULES" CMAKE_ARGS="-DUSE_PIO_USB=OFF" WERROR=0 -j$(nproc)

# 既存の build-<BOARD>/ がある場合は、CMAKE_ARGS は無視される（cmake の再設定は
# build-<BOARD>/Makefile が無いときだけ走る）ので、先に cmake で明示的に再設定する
cmake -S . -B build-RPI_PICO2_W -DUSE_PIO_USB=OFF
make BOARD=RPI_PICO2_W USER_C_MODULES="$USER_C_MODULES" WERROR=0 -j$(nproc)
```

`CFLAGS` は上記のように必ず1行にすること。複数行（埋め込み改行入り）で `export` すると、
`build-<BOARD>/` が存在しない完全新規のビルド（cmake の初回コンパイラチェック）で
Makefile 生成が改行によって壊れ、`missing separator` エラーで構成自体が失敗します。

**注意:** `-DCFG_TUSB_MCU=...` や `-DCFG_TUSB_RHPORT1_MODE=(OPT_MODE_HOST|0x0100)`、
`-DCFG_TUH_ENABLED=1`、`-DCFG_TUD_ENABLED=0` 等の TinyUSB 関連マクロを CFLAGS に含めないこと。
これらは全て `src/micropython.cmake` が `USE_PIO_USB` オプションに応じて `add_compile_definitions()`
で設定するため、CFLAGS 側で重複して渡す必要はなく、渡すと完全新規の cmake 構成で
MicroPython 本体が `firmware` ターゲット向けに別途定義している同名マクロと衝突し、
`-Werror` により `py/asmarm.c` 等が `"CFG_TUSB_MCU" redefined [-Werror]` のようなエラーで
ビルド失敗することがあります（既存のビルドキャッシュを使い回している間は表面化しないため
気付きにくい）。

**注意:** `-DDEBUG_SKIP_CORE_INIT` を CFLAGS に含めないこと。`src/micropython.cmake`
の `USB_HOST_SKIP_INIT` オプション（デフォルト OFF）を迂回してしまい、実際の
USB ホスト初期化 (`tuh_init()`) が常にスキップされる。この状態でもビルドと
起動は成功するため気付きにくいが、USB キーボードが一切認識されなくなる。

**注意:** `CMAKE_C_FLAGS` は cmake にキャッシュされる。同じ `build-<BOARD>/` を再利用したまま
CFLAGS だけ変更しても反映されないため、CFLAGS を変更したら `rm -rf build-<BOARD>` してから
再度 `make` すること。

Windows ネイティブビルドは上記 CFLAGS の関係で非推奨です。WSL2 上で上記コマンドを実行してください。

出力されるファームウェアは `build-RPI_PICO2_W/firmware.uf2`（無印 Pico 2 なら
`build-RPI_PICO2/firmware.uf2`）に配置されます。これをそのまま `RPI-RP2` ドライブへコピーするか、
他プロジェクトと見分けがつき、かつ `USE_PIO_USB` の値も分かるよう、以下の名前でコピーしておきます。

| ボード | `USE_PIO_USB=ON`（デフォルト） | `USE_PIO_USB=OFF`（従来方式） |
| :--- | :--- | :--- |
| Pico 2 W | `firmware/firmware_pb1000_pico2w.uf2` | `firmware/firmware_pb1000_pico2w_native_usb.uf2` |
| 無印 Pico 2 | `firmware/firmware_pb1000_pico2.uf2` | `firmware/firmware_pb1000_pico2_native_usb.uf2` |

> 実際のビルドスクリプトの例は `/home/flex/projects/micropython.pb1000/ports/rp2/bldfrm.sh`
>（このプロジェクト専用のチェックアウト、このプロジェクト固有の作業環境向け）を参照してください。

## 書き込み

1.  **BOOTSEL モードへの移行**: Pico 2 (W) の BOOTSEL ボタンを押しながら、USB で PC に接続します。
2.  **マウント**: Pico 2 が `RPI-RP2` という名前の USB マスストレージとして認識されます。
3.  **コピー**: 実機とビルド方式に応じて、上表のファイル名の `.uf2` を `RPI-RP2` ドライブに
    ドラッグ＆ドロップします。コピー後に Pico 2 は自動的に再起動します。

## ビルド後のセットアップ

ファームウェアの書き込みが終わったら、Python のロジックと ROM ファイルをアップロードする必要があります。

> [!IMPORTANT]
> デフォルト（`USE_PIO_USB=ON`）でビルドした場合、Pico 本体の USB-C ポートは CDC デバイスとして
> 動作するため、`mpremote connect /dev/ttyACM0`（Windows は `COMx`）のように USB-C ケーブル1本で
> 接続できます。`USE_PIO_USB=OFF` でビルドした場合は、Pico の USB ポートが **USB ホスト専用**に
> なるため（`CFG_TUD_ENABLED=0`）、`mpremote` は Pico 本体の USB ポート経由では接続できません。
> その場合は **GP0/GP1 に配線した UART REPL** に USB-シリアル変換アダプタを接続し、そのシリアル
> ポート（例: `/dev/ttyUSB0`、Windows は `COMx`）を明示的に指定して接続してください（配線は
> [Hardware Guide](hardware_guide.md) の「UART およびシリアル」参照）。
> 下記コマンド例の `mpremote connect /dev/ttyACM0` は、`USE_PIO_USB=OFF` ビルドの場合
> `mpremote connect /dev/ttyUSB0` に読み替えてください。

1.  **mpremote のインストール**:
    ```bash
    pip install mpremote
    ```
2.  **Python ファイルのアップロード**:

    ```bash
    cd PB-1000_emu_AG2/mp
    mpremote connect /dev/ttyACM0 fs cp * :
    ```

3.  **ROM のアップロード**:
    ```bash
    # Pico 側に roms ディレクトリを作成
    mpremote connect /dev/ttyACM0 fs mkdir :roms
    # ROM ファイル (rom0.bin, rom1.bin) をアップロード
    cd ../roms
    mpremote connect /dev/ttyACM0 fs cp *.bin :roms/
    ```

## トラブルシューティング

- **"micropython.cmake not found"**: `USER_C_MODULES` の絶対パスが正しいか再確認してください。
- **"arm-none-eabi-gcc not found"**: ツールチェーンが `PATH` に通っているか確認してください。
- **ビルドが停止する (WSL2)**: Windows のマウントフォルダ (`/mnt/c/...`) 上で作業すると非常に低速で、git サブモジュールの処理で問題が発生することがあります。必ず WSL2 のホームディレクトリ (`~/...`) で作業してください。
