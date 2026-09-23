"""Windows exe（robot-viser-backend.exe）用のエントリポイント。画面（frontend）なしで backend（API + viser）だけを起動する。"""
import argparse
import os

import uvicorn

if __name__ == "__main__":
    # 待ち受けアドレスとポートをオプションで切り替える（既定は同じ PC からの接続のみ）
    parser = argparse.ArgumentParser(description="robot-viser backend")
    parser.add_argument("--host", default="127.0.0.1", help="待ち受けアドレス（他の PC から使う場合は 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8000, help="API のポート")
    parser.add_argument("--viser-port", type=int, default=8081, help="3D ビューア（viser）のポート")
    args = parser.parse_args()

    # backend が import 時に viser の待ち受けを読むため、先に設定してからアプリを作る
    os.environ.update(VISER_HOST=args.host, VISER_PORT=str(args.viser_port))
    from backend.main import app

    uvicorn.run(app, host=args.host, port=args.port)
