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
`USER_C_MODULES` に、このリポジトリの `src/micropython.cmake` ファイルを指定します。

> [!IMPORTANT]
> `USER_C_MODULES` には**絶対パス**を使用してください。

**例 (Linux/WSL2):**
```bash
export USER_C_MODULES="/path/to/PB-1000_emu_AG2/src/micropython.cmake"
make BOARD=RPI_PICO2 USER_C_MODULES="$USER_C_MODULES" clean
make BOARD=RPI_PICO2 USER_C_MODULES="$USER_C_MODULES" -j$(nproc)
```

**例 (PowerShell):**
```powershell
$USER_C_MODULES = "G:/path/to/PB-1000_emu_AG2/src/micropython.cmake"
make BOARD=RPI_PICO2 USER_C_MODULES=$USER_C_MODULES clean
make BOARD=RPI_PICO2 USER_C_MODULES=$USER_C_MODULES -j4
```

出力されるファームウェアは `build-RPI_PICO2/firmware.uf2` に配置されます。

## 書き込み

1.  **BOOTSEL モードへの移行**: Pico 2 の BOOTSEL ボタンを押しながら、USB で PC に接続します。
2.  **マウント**: Pico 2 が `RPI-RP2` という名前の USB マスストレージとして認識されます。
3.  **コピー**: `firmware.uf2` を `RPI-RP2` ドライブにドラッグ＆ドロップします。コピー後に Pico 2 は自動的に再起動します。

## ビルド後のセットアップ

ファームウェアの書き込みが終わったら、Python のロジックと ROM ファイルをアップロードする必要があります。

1.  **mpremote のインストール**:
    ```bash
    pip install mpremote
    ```
2.  **Python ファイルのアップロード**:
    ```bash
    cd PB-1000_emu_AG2/mp
    mpremote fs cp * :
    ```
3.  **ROM のアップロード**:
    ```bash
    # Pico 側に roms ディレクトリを作成
    mpremote fs mkdir :roms
    # ROM ファイル (rom0.bin, rom1.bin) をアップロード
    cd ../roms
    mpremote fs cp *.bin :roms/
    ```

## トラブルシューティング

- **"micropython.cmake not found"**: `USER_C_MODULES` の絶対パスが正しいか再確認してください。
- **"arm-none-eabi-gcc not found"**: ツールチェーンが `PATH` に通っているか確認してください。
- **ビルドが停止する (WSL2)**: Windows のマウントフォルダ (`/mnt/c/...`) 上で作業すると非常に低速で、git サブモジュールの処理で問題が発生することがあります。必ず WSL2 のホームディレクトリ (`~/...`) で作業してください。
