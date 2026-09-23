"""Windows exe（robot-viser.exe）用のエントリポイント。backend を別スレッド、frontend をメインスレッドで起動する。"""
import argparse
import multiprocessing
import os
import threading

import uvicorn
from nicegui import ui

if __name__ in {"__main__", "__mp_main__"}:
    # PyInstaller でビルドした exe でマルチプロセスを正しく動かすため
    multiprocessing.freeze_support()

    # ポートの変更とブラウザの自動起動をオプションで切り替える
    parser = argparse.ArgumentParser(description="robot-viser")
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=8080)
    parser.add_argument("--viser-port", type=int, default=8081)
    parser.add_argument("--no-browser", action="store_true", help="ブラウザを自動で開かない")
    args = parser.parse_args()

    # frontend・backend が import 時に接続先・待ち受けを読むため、先に設定してからページとアプリを作る
    os.environ.update(BACKEND_URL=f"http://127.0.0.1:{args.backend_port}", VISER_HOST="127.0.0.1", VISER_PORT=str(args.viser_port))
    import frontend.main  # noqa: F401
    from backend.main import app

    # backend はバックグラウンドで動かし、frontend の終了と一緒に止まるようにする
    threading.Thread(target=uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.backend_port)).run, daemon=True).start()
    ui.run(host="127.0.0.1", port=args.frontend_port, title="robot-viser", reload=False, show=not args.no_browser)
