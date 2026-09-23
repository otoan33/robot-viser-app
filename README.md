# robot-viser-app

6軸ロボットアームとハンドを [viser](https://viser.studio/) でブラウザに 3D 表示し、表示構成・関節角度・軌道を操作するアプリ。NiceGUI の画面から操作できるほか、FastAPI の API を直接呼んでも操作できる。Docker での実行と、Windows 用 exe / インストーラのビルドができる。

## 構成

```
robot-viser-app/
├── compose.yaml              # frontend + backend を Docker で起動（自動起動あり）
├── .dockerignore             # ルートをコンテキストにするビルド用（requirements.txt のみ送る）
├── .devcontainer/            # VSCode Dev Container 設定
├── backend/                  # FastAPI（API サーバー）+ viser（3D ビューア）
│   ├── main.py               # API エンドポイントと viser の GUI（再生スライダー・ボタン）
│   ├── robot.py              # URDF の読み込み、アーム＋ハンドの合成表示、軌道の読み込み・再生
│   ├── assets/               # ロボットモデルと軌道サンプル（下記）
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                 # NiceGUI（画面）。backend を HTTP で呼び出す
│   ├── main.py               # 操作パネル（構成・関節角度・軌道）と viser の埋め込み表示
│   ├── requirements.txt
│   └── Dockerfile
├── desktop/
│   └── launcher.py           # Windows exe 用: backend と frontend を 1 プロセスで起動
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
| 軌道 | 軌道 CSV を選ぶと、すぐ `POST /trajectory/upload` へ送って再生する。点数と再生時間を通知で表示する |

再生中のシークや停止は、右側の viser 画面にある Time スライダーと Play / Stop ボタンで行う（下記「軌道の再生」）。

## backend API

API ドキュメントは http://localhost:8000/docs で確認できる。ここから「Try it out」で実際にリクエストを送ることもできる。

| メソッド | パス | ボディ | 内容 |
|---|---|---|---|
| GET | `/robots` | - | 選べるアーム・ハンドの一覧と、表示中の構成を返す |
| POST | `/robot` | `{"arm": "robotA", "hand": "hand_jig"}` | 表示する構成を切り替える。`hand` を省略するか `null` にすると、アームのみを表示する |
| POST | `/joints` | `{"angles": [30, -20, 0, 0, 0, 0]}` | 6軸の関節角度 [deg] を反映する。順序はアーム URDF の可動関節の定義順 |
| POST | `/trajectory` | `{"csv_path": "/app/backend/assets/trajectories/test.csv"}` | 軌道 CSV を読み込んで再生する。パスは backend から見えるパスを指定する |
| POST | `/trajectory/upload` | multipart の `file`（CSV ファイル） | 送られてきた軌道 CSV を再生する。backend とは別の環境にあるファイルを送るときに使う |

- 起動時は、アームとハンドそれぞれについて、フォルダ名順で先頭のものを表示する。
- `POST /robot` はアームの STL を読み直すため、数秒かかる。読み込み中に届いた構成の切り替えや角度の反映は、読み込みが終わるのを待ってから実行する。

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
./windows/build.sh exe        # 単体 exe → dist/docker-test-app.exe
./windows/build.sh installer  # インストーラ → dist/docker-test-app-setup.exe
```

初回はビルド用イメージの作成に時間がかかる。PyInstaller の処理も 10 分前後かかる。

### 仕組み

PyInstaller は実行中の OS 向けのバイナリしか作れない。Linux 上で Windows の exe を作るには Windows 版 Python が必要になるので、ビルド用イメージ（`windows/Dockerfile`）では Wine 上の Windows 版 Python で PyInstaller を実行している。インストーラは Linux 版の NSIS（`makensis`）で作る。

ビルド用コンテナは、実行用コンテナとは別にしている。Wine などのビルド用ツールを実行用イメージに入れないためで、ビルドが終わるとコンテナは削除される（`--rm`）。

exe のエントリポイントは `desktop/launcher.py`。backend を別スレッドで、frontend をメインスレッドで起動する。Docker では別々のコンテナに分かれている 2 つを、1 つの exe にまとめて配布できる。

| モード | PyInstaller | 出力 |
|---|---|---|
| `exe` | `--onefile`（1 ファイルにまとめる） | `dist/docker-test-app.exe` |
| `installer` | `--onedir` + NSIS | `dist/docker-test-app-setup.exe` |

- NiceGUI の静的ファイル（JS/CSS）は `--add-data` で同梱している。
- UPX 圧縮は無効にしている（`--noupx`）。UPX で圧縮すると、ウイルス対策ソフトに誤検知されやすいため。

### Windows 版の使い方

```
docker-test-app.exe                       # 起動してブラウザで http://127.0.0.1:8080 を開く
docker-test-app.exe --no-browser          # ブラウザを自動で開かない
docker-test-app.exe --frontend-port 9080 --backend-port 9000   # ポートを変更
```

- コンソールウィンドウを閉じるか、`Ctrl+C` で停止する。
- 単体 exe は起動のたびに一時フォルダへ展開するため、画面が表示されるまで 20 秒ほどかかる。起動を速くしたい場合は、インストーラ版（`--onedir`）を使う。
- インストーラ版の `docker-test-app-setup.exe` を実行すると、次の処理が行われる（管理者権限が必要）。
  - `C:\Program Files\DockerTestApp` にインストールする。
  - スタートメニューに「docker-test App」「Uninstall」を作る。
  - 「設定 → アプリ」に登録する。ここからアンインストールできる。
- 署名していないため、初回の実行時に SmartScreen の警告が出ることがある。「詳細情報 → 実行」で起動できる。

## 未対応・既知の問題

- Windows 版には、viser・assets・新しい frontend を追加した後の変更がまだ反映されていない。viser クライアントの静的ファイルと `backend/assets` の同梱が必要になる見込み。
- `trajectories/trajectory.csv` は値が 0〜0.6 程度と小さく、deg として扱うとほとんど動かない。値がラジアンで書かれている可能性がある。
