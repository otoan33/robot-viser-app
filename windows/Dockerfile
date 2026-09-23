# Windows 用 exe / インストーラのビルド環境（Wine 上の Windows 版 Python + NSIS）
# ビルドコンテキストはプロジェクト直下（.dockerignore で requirements.txt のみ送る）
FROM tobix/pywine:3.12

RUN apt-get update \
    && apt-get install -y --no-install-recommends nsis \
    && rm -rf /var/lib/apt/lists/*

# Windows 版 Python の既定の文字コード（cp1252）では日本語コメント入りのファイルを読めないため、UTF-8 モードにする
ENV PYTHONUTF8=1

COPY backend/requirements.txt /tmp/backend-requirements.txt
COPY frontend/requirements.txt /tmp/frontend-requirements.txt
# numpy 2.2 以降の Windows 版は Wine 9.17 が未対応の関数（ucrtbase.dll.crealf）を使い、import 時に落ちるため 2.1 系に固定する
# ベースイメージに入っている古い PyInstaller（6.10）は新しい scipy を正しく同梱できないため、新しい版に上げる
RUN wine python -m pip install --no-cache-dir \
    -r /tmp/backend-requirements.txt -r /tmp/frontend-requirements.txt "numpy<2.2" \
    "pyinstaller>=6.22" "pyinstaller-hooks-contrib>=2026.7"
