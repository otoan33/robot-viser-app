#!/usr/bin/env bash
# ビルド用コンテナの中で実行される（windows/build.sh から呼ばれる）
set -euo pipefail

APP=robot-viser
BACKEND_APP=robot-viser-backend

rm -rf /tmp/build
mkdir /tmp/build
cp -r /src/backend /src/frontend /src/desktop /tmp/build/
cd /tmp/build

# backend に必要なファイルを同梱する
#   viser の画面（JS/CSS）・アイコン、trimesh のリソース、mp4 書き出し用の ffmpeg、実行時に名前で読み込まれる imageio のプラグイン（mp4 / gif 用）、
#   import 時に自分のバージョンをパッケージ情報から読むライブラリのメタデータ、ロボットモデル
BACKEND=(--noconfirm --noupx --paths . --collect-data viser --collect-data trimesh --collect-data imageio_ffmpeg
  --hidden-import imageio.plugins.ffmpeg --hidden-import imageio.plugins.pillow
  --copy-metadata imageio --copy-metadata trimesh --copy-metadata yourdfpy --add-data "backend/assets;backend/assets")
# frontend 用に NiceGUI の静的ファイル（JS/CSS など）も同梱する
NICEGUI_DIR="$(wine python -c 'import nicegui, os; print(os.path.dirname(nicegui.__file__))' | tr -d '\r')"
FULL=("${BACKEND[@]}" --name "$APP" --add-data "$NICEGUI_DIR;nicegui")

case "$MODE" in
  exe)
    wine python -m PyInstaller "${FULL[@]}" --onefile desktop/launcher.py
    wine python -m PyInstaller "${BACKEND[@]}" --name "$BACKEND_APP" --onefile desktop/backend_launcher.py
    cp "dist/$APP.exe" "dist/$BACKEND_APP.exe" /out/
    ;;
  installer)
    wine python -m PyInstaller "${FULL[@]}" --onedir desktop/launcher.py
    makensis -V2 -DSRC_DIR="/tmp/build/dist/$APP" \
      -DOUT_FILE="/out/$APP-setup.exe" /src/windows/installer.nsi
    ;;
esac

chown -R "$HOST_UID:$HOST_GID" /out
