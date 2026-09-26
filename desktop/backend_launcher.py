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
    parser.add_argument("--collision", action="store_true", help="衝突判定（近似球・障害物・距離 API）を有効にする")
    args = parser.parse_args()

    # backend が import 時に viser の待ち受けと衝突判定の有無を読むため、先に設定してからアプリを作る
    os.environ.update(VISER_HOST=args.host, VISER_PORT=str(args.viser_port), COLLISION="1" if args.collision else "0")
    from backend.main import app

    uvicorn.run(app, host=args.host, port=args.port)
