import os

import httpx
from fastapi import Request
from nicegui import ui

# バックエンド（FastAPI）への接続。compose では BACKEND_URL=http://backend:8000 を渡す
client = httpx.AsyncClient(base_url=os.environ.get("BACKEND_URL", "http://127.0.0.1:8000"))


@ui.page("/")
async def index(request: Request):
    robots = (await client.get("/robots")).json()

    with ui.row().classes("w-full no-wrap"):
        # 左: 操作パネル
        with ui.column().classes("w-96 shrink-0"):
            ui.label("robot-viser").classes("text-2xl font-bold")

            # 表示するアームとハンドの構成を選ぶ（選んだらすぐ反映。ハンドは "" を「なし」として送る）
            with ui.card().classes("w-full"):
                ui.label("構成").classes("text-lg font-bold")
                async def load_robot(): (await client.post("/robot", json={"arm": arm.value, "hand": hand.value or None})).raise_for_status()
                arm = ui.select(robots["arms"], label="アーム", value=robots["current"]["arm"], on_change=load_robot).classes("w-full")
                hand = ui.select({"": "なし", **{h: h for h in robots["hands"]}}, label="ハンド", value=robots["current"]["hand"] or "", on_change=load_robot).classes("w-full")

            # 6軸の関節角度[deg]を入力し、まとめて送る
            with ui.card().classes("w-full"):
                ui.label("関節角度 [deg]").classes("text-lg font-bold")
                with ui.grid(columns=3).classes("w-full"):
                    inputs = [ui.number(f"J{i}", value=0) for i in range(1, 7)]
                async def send_joints(): (await client.post("/joints", json={"angles": [n.value for n in inputs]})).raise_for_status()
                ui.button("送信", on_click=send_joints)

            # 軌道 CSV を backend へ転送して再生させる（シーク・停止は viser 画面の Time スライダーと Play/Stop で行う）
            with ui.card().classes("w-full"):
                ui.label("軌道").classes("text-lg font-bold")
                async def upload_trajectory(e):
                    res = await client.post("/trajectory/upload", files={"file": (e.file.name, await e.file.read())})
                    res.raise_for_status()
                    ui.notify(f"{e.file.name}: {res.json()['num_points']}点 / {res.json()['duration_sec']:.2f}秒を再生")
                    # 同じファイルを続けてアップロードできるよう、一覧を空に戻す
                    upload.reset()
                # アップロード部品は枠が大きいので隠し、ボタンからファイル選択ダイアログだけを開く
                upload = ui.upload(auto_upload=True, on_upload=upload_trajectory).props("accept=.csv").classes("hidden")
                ui.button("CSV を選択", on_click=lambda: upload.run_method("pickFiles"))

        # 右: viser の 3D ビューア。ブラウザが直接 viser に接続するので、このページと同じホスト名を使う
        viser_url = os.environ.get("VISER_URL", f"http://{request.url.hostname}:8081")
        ui.element("iframe").props(f'src="{viser_url}"').classes("grow h-[calc(100vh-2rem)] border-0")


# 単体起動（Docker / Dev Container）用。exe では desktop/launcher.py から起動する
if __name__ in {"__main__", "__mp_main__"}:
    ui.run(host="0.0.0.0", port=8080, title="robot-viser", reload=False, show=False)
