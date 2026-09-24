# robot-viser-app

6軸ロボットアームとハンドを [viser](https://viser.studio/) でブラウザに 3D 表示し、表示構成・関節角度・軌道を操作するアプリ。NiceGUI の画面から操作できるほか、FastAPI の API を直接呼んでも操作できる。Docker での実行と、Windows 用 exe / インストーラのビルドができる。

## 構成

```
robot-viser-app/
├── compose.yaml              # frontend + backend を Docker で起動（自動起動あり）
├── .dockerignore             # ルートをコンテキストにするビルド用（requirements.txt のみ送る）
├── .devcontainer/            # VSCode Dev Container 設定
├── backend/                  # FastAPI（API サーバー）+ viser（3D ビューア）
│   ├── main.py               # API エンドポイントと viser の GUI（再生スライダー・ボタン、Screenshot ボタン）
│   ├── robot.py              # URDF の読み込み、アーム＋ハンドの合成表示、描画の取得、軌道の読み込み・再生・録画
│   ├── assets/               # ロボットモデルと軌道サンプル（下記）
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                 # NiceGUI（画面）。backend を HTTP で呼び出す
│   ├── main.py               # 操作パネル（構成・関節角度・軌道）と viser の埋め込み表示
│   ├── requirements.txt
│   └── Dockerfile
├── desktop/
│   └── launcher.py           # Windows exe 用: backend と frontend を 1 プロセスで起動
├── docs/manual/              # 使い方マニュアル（manual.md / manual.html と、スクリーンショット img/）
├── scripts/
│   └── make_manual.py        # マニュアル用のスクリーンショットを撮り直すスクリプト
└── windows/                  # Windows 版ビルド一式（build.sh, Dockerfile, installer.nsi など）
```

`backend/` と `frontend/` はそれぞれ Python パッケージ（`backend.main`、`frontend.main`）として import できる。Docker でも exe でも同じ import パスで動かしている。

### 通信の流れ

```
ブラウザ ──> frontend (NiceGUI :8080) ──HTTP──> backend (FastAPI :8000)
   └─ iframe ──> backend の viser (3D ビューア :8081)
```

- frontend から backend への接続先は、環境変数 `BACKEND_URL` で指定する（既定は `http://127.0.0.1:8000`）。
- viser のポートは、環境変数 `VISER_PORT` で変更できる（既定は 8081）。viser 自体の既定は 8080 だが、NiceGUI と衝突するため 8081 にしている。
- viser は `backend.main` を import した時点で起動する。uvicorn の単体起動、`--reload`、`desktop/launcher.py` のどれでも、追加の設定なしに立ち上がる。
- frontend の画面は viser を iframe で埋め込んでおり、ブラウザが viser に直接接続する。iframe の URL は、環境変数 `VISER_URL` があればそれを使う。なければ、画面を開いたときのホスト名にポート 8081 を付けたもの（例: `http://localhost:8081`）になる。

## 画面（frontend）

http://localhost:8080 を開くと、左に操作パネル、右に viser の 3D ビューアが表示される。

| パネル | 内容 |
|---|---|
| 構成 | アームとハンドをプルダウンで選ぶ。選ぶとすぐ `POST /robot` で表示を切り替える。ハンドは「なし」も選べる |
| 関節角度 [deg] | J1〜J6 の角度を入力し、「送信」で `POST /joints` を送る |
| 軌道 | 「CSV を選択」で軌道 CSV を選ぶと、すぐ `POST /trajectory/upload` へ送って再生する。点数と再生時間を通知で表示する。「録画する」にチェックを入れて形式（mp4 / gif）を選んでおくと、代わりに `POST /trajectory/record` で録画し、`<CSV名>.mp4` または `<CSV名>.gif` をダウンロードする。録画が終わった後は、通常どおり再生する |

右側の viser 画面のパネルでは、次の操作ができる。

- 再生中のシークや停止：Time スライダーと Play / Stop ボタンで行う（下記「軌道の再生」）。
- スクリーンショット：Screenshot ボタンを押すと、押したブラウザのカメラ視点と画面サイズのまま、`screenshot_YYYYmmdd_HHMMSS.png` として保存する。

## backend API

API ドキュメントは http://localhost:8000/docs で確認できる。ここから「Try it out」で実際にリクエストを送ることもできる。

| メソッド | パス | ボディ | 内容 |
|---|---|---|---|
| GET | `/robots` | - | 選べるアーム・ハンドの一覧と、表示中の構成を返す |
| POST | `/robot` | `{"arm": "robotA", "hand": "hand_jig"}` | 表示する構成を切り替える。`hand` を省略するか `null` にすると、アームのみを表示する |
| POST | `/joints` | `{"angles": [30, -20, 0, 0, 0, 0]}` | 6軸の関節角度 [deg] を反映する。順序はアーム URDF の可動関節の定義順 |
| POST | `/trajectory` | `{"csv_path": "/app/backend/assets/trajectories/test.csv"}` | 軌道 CSV を読み込んで再生する。パスは backend から見えるパスを指定する |
| POST | `/trajectory/upload` | multipart の `file`（CSV ファイル） | 送られてきた軌道 CSV を再生する。backend とは別の環境にあるファイルを送るときに使う |
| POST | `/trajectory/record?format=mp4` | multipart の `file`（CSV ファイル） | 送られてきた軌道 CSV を録画し、動画（`format` は `mp4` か `gif`）を返す。録画が終わった後は通常どおり再生する |
| GET | `/screenshot` | - | 今の 3D 表示を PNG で返す |

- 起動時は、アームとハンドそれぞれについて、フォルダ名順で先頭のものを表示する。
- `POST /robot` はアームの STL を読み直すため、数秒かかる。読み込み中に届いた構成の切り替えや角度の反映は、読み込みが終わるのを待ってから実行する。

### 録画とスクリーンショットの仕組み

画像の描画は、backend ではなく、viser 画面を開いているブラウザが行う（`ClientHandle.get_render`）。そのため、次の点に注意する。

- viser 画面（frontend の画面でもよい）を開いているブラウザが 1 つ以上必要になる。API から呼んだ場合は、先に接続したブラウザのカメラ視点と画面サイズが使われる。
- 開いていても、タブが裏に回って描画が止まっているブラウザは使えない。viser は、ブラウザから最初のカメラ情報が届いた時点で接続済みとして扱うためである。使えるブラウザがないと、500 エラーになる。
- 録画は、軌道を 20fps で間引いてコマ送りで行う。1 コマずつ姿勢を変えて描画するので、描画に時間がかかっても、動画は軌道の時刻どおりの速さになる。録画中は、ブラウザの表示もコマ送りで動く。
- gif は書き出しが遅く、ファイルも大きくなる。そのため、視野はそのままで、画面の半分の解像度で描画する。mp4 は画面と同じ解像度で描画する。

### 送信例

bash の場合:

```bash
curl -X POST localhost:8000/joints -H 'Content-Type: application/json' -d '{"angles":[30,-20,0,0,0,0]}'
```

Windows のコマンドプロンプトではシングルクォートが使えないため、ダブルクォートを `\"` でエスケープする。

```
curl -X POST localhost:8000/joints -H "Content-Type: application/json" -d "{\"angles\":[30,-20,0,0,0,0]}"
```

軌道ファイルを送る場合は、次のようにする（bash・コマンドプロンプト共通）。

```
curl -F file=@backend/assets/trajectories/test.csv localhost:8000/trajectory/upload
```

録画する場合とスクリーンショットを撮る場合は、次のようにする（bash・コマンドプロンプト共通）。

```
curl -F file=@backend/assets/trajectories/test.csv "localhost:8000/trajectory/record?format=gif" -o test.gif
curl localhost:8000/screenshot -o screenshot.png
```

### 軌道の再生

`POST /trajectory` または `POST /trajectory/upload` を送ると、軌道の時刻どおりにバックグラウンドで再生する。再生中に新しい軌道を送ると、前の軌道は打ち切って新しい軌道に切り替わる。`POST /robot` で構成を切り替えた場合は、再生を止めてから切り替える。

viser 画面の右側のパネルでも再生を操作できる。

- **Time (frame)** スライダー：再生中のフレームに追従する。ドラッグすると自動再生が止まり、そのフレームの姿勢になる。
- **Play / Stop** ボタン：
  - 停止中は「Play」と表示される。押すと、今のスライダー位置から再生する。末尾まで再生し終えている場合は、先頭から再生する。
  - 再生中は「Stop」と表示される。押すと、その場の姿勢で止まる。

軌道 CSV は次の 2 形式に対応している。

| 形式 | 内容 |
|---|---|
| t 形式 | ヘッダ `t,joint1,...,joint6`。t[sec] と 6 関節の角度 [deg] |
| ロボットログ形式 | メタ情報 2 行とデータヘッダ。`ElapsedTime[msec]` と `Joint(J1)[deg]`〜`Joint(J6)[deg]` の列を使う。末尾の終了情報の行は読み飛ばす |

## ロボットモデル（backend/assets）

```
backend/assets/
├── arms/<アーム名>/          # フォルダ名がアーム名になる。中の *.urdf を 1 つ使う
│   ├── arm.urdf
│   └── *.stl
├── hands/<ハンド名>/         # フォルダ名がハンド名になる
│   ├── *.urdf, *.stl
│   └── mount.json            # アームへの取り付け位置・姿勢
└── trajectories/             # 軌道 CSV のサンプル
```

アームやハンドは、フォルダを追加するだけで選択肢に加わる。URDF の中のメッシュの相対パスは、URDF のあるフォルダを基準に解決する。

`mount.json` には、ハンドを取り付けるアーム側のリンク名と、そのリンクから見た位置 [m]・姿勢（roll / pitch / yaw [rad]）を書く。

```json
{"link": "tool0", "position": [0.0, 0.0, 0.0], "rpy": [1.5707963, 0.0, 2.3561945]}
```

## Docker で実行する

```bash
docker compose up -d --build
```

- 画面（NiceGUI）: http://localhost:8080
- 3D ビューア（viser）: http://localhost:8081
- API（FastAPI）: http://localhost:8000
- API ドキュメント: http://localhost:8000/docs

compose 内では、frontend に `BACKEND_URL=http://backend:8000` を渡している。どちらのサービスも `restart: unless-stopped` を設定しているため、Docker の起動時に自動で起動する（`docker compose stop` で止めた場合を除く）。

| 操作 | コマンド |
|---|---|
| 状態確認 | `docker compose ps` |
| ログ確認 | `docker compose logs -f frontend`（または `backend`） |
| 停止 | `docker compose stop` |
| 削除 | `docker compose down` |

## 開発（Dev Container）

VSCode のコマンドパレットで「Dev Containers: Reopen in Container」を選択する。

- `.devcontainer/Dockerfile` で、backend と frontend の依存関係をまとめてインストールする。
- 起動時に backend（`uvicorn --reload`）と frontend を立ち上げる。
  - backend はファイルを保存すると自動で再読み込みされる。frontend は再起動が必要。
  - ログは `/tmp/backend.log` と `/tmp/frontend.log` に出る。
- compose のサービスがポート 8000 / 8080 / 8081 を使っている場合、VSCode は空いている別のポートに転送する（「ポート」タブで確認できる）。
- `forwardPorts` に入っているのは 8000 / 8080 だけ。viser の 8081 が転送されていない場合は、「ポート」タブから追加する。
- 8081 が別のポートに転送された場合、frontend の iframe は viser に接続できない。その場合は、frontend の環境変数 `VISER_URL` に転送先の URL を指定する。

## Windows 版をビルドする

必要なものは Docker（WSL / Linux 上で実行する）だけで、Windows 側に Python やビルドツールをインストールする必要はない。プロジェクト直下で次を実行する。

```bash
./windows/build.sh exe        # 単体 exe 2 つ → dist/robot-viser.exe、dist/robot-viser-backend.exe
./windows/build.sh installer  # インストーラ → dist/robot-viser-setup.exe（backend + frontend）
```

初回はビルド用イメージの作成に時間がかかる（pip install だけで 5 分ほど）。PyInstaller の処理も、exe 1 つあたり 10 分前後かかる。

| exe | エントリポイント | 内容 |
|---|---|---|
| `robot-viser.exe` | `desktop/launcher.py` | backend（API + viser）と frontend（画面）を 1 プロセスで起動する。backend は別スレッド、frontend はメインスレッドで動く |
| `robot-viser-backend.exe` | `desktop/backend_launcher.py` | backend（API + viser）だけを起動する。画面を使わず、API で操作する場合に使う |

### 仕組み

PyInstaller は実行中の OS 向けのバイナリしか作れない。Linux 上で Windows の exe を作るには Windows 版 Python が必要になるので、ビルド用イメージ（`windows/Dockerfile`）では Wine 上の Windows 版 Python で PyInstaller を実行している。インストーラは Linux 版の NSIS（`makensis`）で作る。

ビルド用コンテナは、実行用コンテナとは別にしている。Wine などのビルド用ツールを実行用イメージに入れないためで、ビルドが終わるとコンテナは削除される（`--rm`）。

| モード | PyInstaller | 出力 |
|---|---|---|
| `exe` | `--onefile`（1 ファイルにまとめる） | `dist/robot-viser.exe`、`dist/robot-viser-backend.exe` |
| `installer` | `--onedir` + NSIS | `dist/robot-viser-setup.exe` |

- exe には次のものを同梱している（`windows/build-in-container.sh`）。
  - viser の画面（JS/CSS）
  - trimesh のリソース
  - mp4 書き出し用の ffmpeg（imageio-ffmpeg）
  - imageio の mp4 / gif プラグイン
  - `backend/assets`
  - フル版では、さらに NiceGUI の静的ファイル
- UPX 圧縮は無効にしている（`--noupx`）。UPX で圧縮すると、ウイルス対策ソフトに誤検知されやすいため。
- ビルド用イメージでは、次の 3 点を調整している（`windows/Dockerfile`）。
  - Python を UTF-8 モードにする。requirements.txt の日本語コメントを読めるようにするため。
  - numpy を 2.1 系に固定する。2.2 以降の Windows 版は、Wine 9.17 で import すると落ちるため。
  - PyInstaller を 6.22 以上に上げる。ベースイメージの古い版では、scipy を正しく同梱できないため。

### Windows 版の使い方

```
robot-viser.exe                           # 起動してブラウザで http://127.0.0.1:8080 を開く
robot-viser.exe --no-browser              # ブラウザを自動で開かない
robot-viser.exe --frontend-port 9080 --backend-port 9000 --viser-port 9081   # ポートを変更

robot-viser-backend.exe                   # API（:8000）と viser（:8081）を起動する（同じ PC からの接続のみ）
robot-viser-backend.exe --host 0.0.0.0    # 他の PC からの接続も受け付ける
robot-viser-backend.exe --port 9000 --viser-port 9081   # ポートを変更
```

- コンソールウィンドウを閉じるか、`Ctrl+C` で停止する。
- 単体 exe は起動のたびに一時フォルダへ展開するため、使えるようになるまで 30〜60 秒ほどかかる。起動を速くしたい場合は、インストーラ版（`--onedir`）を使う。
- どちらの exe も、既定では 127.0.0.1 でだけ待ち受ける。backend のみの exe を `--host 0.0.0.0` で起動した場合は、初回に Windows ファイアウォールの確認が出る。
- backend のみの exe で録画やスクリーンショットを使う場合は、ブラウザで viser 画面（http://127.0.0.1:8081）を開いておく（「録画とスクリーンショットの仕組み」を参照）。
- インストーラ版の `robot-viser-setup.exe` を実行すると、次の処理が行われる（管理者権限が必要）。
  - `C:\Program Files\RobotViser` にインストールする。
  - スタートメニューに「robot-viser」「Uninstall」を作る。
  - 「設定 → アプリ」に登録する。ここからアンインストールできる。
- 署名していないため、初回の実行時に SmartScreen の警告が出ることがある。「詳細情報 → 実行」で起動できる。

## 使い方マニュアル

利用者向けのマニュアルを `docs/manual/` に置いている。`manual.html` をブラウザで開いて読む。画像は `img/` から相対パスで読むため、渡すときは `docs/manual/` フォルダごと渡す。元の Markdown（Marp）は `manual.md`。

画面を改修したら、スクリーンショットを撮り直して HTML を書き出し直す。アプリを Docker で起動した状態（http://localhost:8080）で、プロジェクト直下から実行する。

```bash
# 前回の軌道や姿勢が画面に残らないよう、backend を再起動しておく
docker compose restart backend

# スクリーンショットを docs/manual/img/ に撮り直す（5 分ほどかかる）
docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/work -w /work \
    mcr.microsoft.com/playwright/python:v1.63.0-noble \
    sh -c "pip install -q --user --break-system-packages playwright==1.63.0 && xvfb-run -a -s '-screen 0 1920x1080x24' python scripts/make_manual.py"

# manual.md から manual.html を書き出す
docker run --rm -v "$PWD":/home/marp/app -e MARP_USER="$(id -u):$(id -g)" marpteam/marp-cli \
    docs/manual/manual.md -o docs/manual/manual.html
```

- 撮影はダミーの軌道 CSV（乱数のシード固定）で行う。実機のログは使わない。
- 3D ビューアは WebGL で描画する。コンテナ内では GPU を使えないため、Xvfb 上の Chromium で Mesa の llvmpipe（CPU 描画）を使っている。ヘッドレスの既定の SwiftShader では、1 コマに 2 秒ほどかかって操作が追いつかない。

## 未対応・既知の問題

- Windows 版の exe は Wine 上でしか動作確認していない（起動、API、画面の配信まで）。実機の Windows での確認と、exe での録画の確認はまだ行っていない。インストーラ版は、今の構成ではまだビルドしていない。
- `trajectories/trajectory.csv` は値が 0〜0.6 程度と小さく、deg として扱うとほとんど動かない。値がラジアンで書かれている可能性がある。
