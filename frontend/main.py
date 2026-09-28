import os
import re
from pathlib import Path

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
            # 図形の編集画面は普段は出さず、別ウィンドウで開く（ブラウザのポップアップ制限にかからないよう、クリック時にブラウザ側で開く）
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("robot-viser").classes("text-2xl font-bold")
                ui.button("図形", icon="category").props("flat").on("click", js_handler="() => window.open('/shapes', 'shapes', 'width=760,height=860')")

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
                # 録画する場合は、読み込み時にコマ送りで動画を作ってダウンロードさせる（その後は通常どおり再生される）
                with ui.row().classes("items-center"):
                    record = ui.checkbox("録画する")
                    fmt = ui.select(["mp4", "gif"], value="mp4").bind_visibility_from(record, "value")
                async def upload_trajectory(e):
                    files = {"file": (e.file.name, await e.file.read())}
                    if record.value:
                        ui.notify(f"{e.file.name}: 録画中…")
                        # 録画は軌道の長さに応じて時間がかかるため、タイムアウトなしで待つ
                        res = await client.post("/trajectory/record", params={"format": fmt.value}, files=files, timeout=None)
                        res.raise_for_status()
                        ui.download.content(res.content, f"{Path(e.file.name).stem}.{fmt.value}")
                        ui.notify(f"{e.file.name}: 録画を保存しました")
                    else:
                        res = await client.post("/trajectory/upload", files=files)
                        res.raise_for_status()
                        ui.notify(f"{e.file.name}: {res.json()['num_points']}点 / {res.json()['duration_sec']:.2f}秒を再生")
                    # 同じファイルを続けてアップロードできるよう、一覧を空に戻す
                    upload.reset()
                # アップロード部品は枠が大きいので隠し、ボタンからファイル選択ダイアログだけを開く
                upload = ui.upload(auto_upload=True, on_upload=upload_trajectory).props("accept=.csv").classes("hidden")
                ui.button("CSV を選択", on_click=lambda: upload.run_method("pickFiles"))

        # 右: viser の 3D ビューア。ブラウザが直接 viser に接続するので、このページと同じホスト名を使う
        viser_url = os.environ.get("VISER_URL", f"http://{request.url.hostname}:{os.environ.get('VISER_PORT', 8081)}")
        ui.element("iframe").props(f'src="{viser_url}"').classes("grow h-[calc(100vh-2rem)] border-0")


# 図形の種類の表示名と、フォームに出す入力欄（1 行ごとの (項目, ラベル) の並び）
TYPES = {"point": "点", "line": "線", "sphere": "球", "cylinder": "円柱", "box": "直方体"}
XYZ = lambda label, sfx="": [(f"x{sfx}", f"{label} x [m]"), (f"y{sfx}", "y"), (f"z{sfx}", "z")]
FIELDS = {
    "point": [XYZ("位置"), [("size", "大きさ [m]")]],
    "line": [XYZ("始点"), XYZ("終点", "2"), [("size", "太さ [px]")]],
    "sphere": [XYZ("中心"), [("size", "半径 [m]")]],
    "cylinder": [XYZ("端点1"), XYZ("端点2", "2"), [("size", "半径 [m]")]],
    "box": [XYZ("中心"), [("sx", "辺 x [m]"), ("sy", "y"), ("sz", "z")], [("roll", "roll [deg]"), ("pitch", "pitch"), ("yaw", "yaw")]],
}
# 線・円柱は追加したときに長さがあるよう、終点をずらしておく（その他の項目は backend の既定値）
NEW = {"line": {"x2": 0.3}, "cylinder": {"z2": 0.3}}


# 図形の編集画面（メイン画面から別ウィンドウで開く）。一覧は backend が持ち、変更のたびに丸ごと送って即座に描き直させる
@ui.page("/shapes", title="図形")
async def shapes_page():
    shapes = (await client.get("/shapes")).json()["shapes"]
    sel = {"i": 0 if shapes else None}

    # 一覧を送る。reload なら、既定値で埋まった一覧を受け取って画面を作り直す（追加・種類変更・削除など）
    async def push(reload=False):
        res = await client.post("/shapes", json={"shapes": shapes})
        res.raise_for_status()
        if reload: shapes[:] = res.json()["shapes"]; redraw()

    # 一覧と編集フォームを作り直し、選択中の図形を一覧の見える位置までスクロールする
    def redraw():
        shape_list.refresh(); editor.refresh()
        ui.run_javascript("document.querySelector('.shape-selected')?.scrollIntoView({block: 'nearest'})")

    def select(i):
        sel["i"] = i; redraw()

    # 入力途中の空欄や書きかけの色は送らない
    async def update(s, key, value):
        if value is None or (key == "color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value)): return
        s[key] = value
        await push()

    async def add(t):
        shapes.append({"type": t, "name": f"{t}{len(shapes) + 1}", **NEW.get(t, {})})
        sel["i"] = len(shapes) - 1
        await push(reload=True)

    async def duplicate():
        if sel["i"] is None: return
        shapes.insert(sel["i"] + 1, {**shapes[sel["i"]], "name": shapes[sel["i"]]["name"] + "_copy"})
        sel["i"] += 1
        await push(reload=True)

    async def delete():
        if sel["i"] is None: return
        shapes.pop(sel["i"])
        sel["i"] = min(sel["i"], len(shapes) - 1) if shapes else None
        await push(reload=True)

    # 種類を変えたら、大きさはその種類の既定値に戻す（線の太さ[px]が球の半径[m]になったりしないように）
    async def change_type(s, t):
        s["type"], s["size"] = t, None
        await push(reload=True)

    # CSV で一覧を置き換える
    async def upload_csv(e):
        res = await client.post("/shapes/upload", files={"file": (e.file.name, await e.file.read())})
        res.raise_for_status()
        shapes[:] = res.json()["shapes"]
        sel["i"] = 0 if shapes else None
        redraw()
        ui.notify(f"{e.file.name}: {len(shapes)} 個の図形を読み込みました")
        upload.reset()

    async def download_csv():
        ui.download.content((await client.get("/shapes/csv")).content, "shapes.csv")

    # 操作ボタン
    with ui.row().classes("w-full items-center gap-1"):
        with ui.dropdown_button("追加", icon="add", auto_close=True):
            for t, label in TYPES.items(): ui.item(label, on_click=lambda t=t: add(t))
        ui.button("複製", icon="content_copy", on_click=duplicate).props("flat")
        ui.button("削除", icon="delete", on_click=delete).props("flat")
        ui.space()
        upload = ui.upload(auto_upload=True, on_upload=upload_csv).props("accept=.csv").classes("hidden")
        ui.button("CSV 読込", icon="upload_file", on_click=lambda: upload.run_method("pickFiles")).props("flat")
        ui.button("CSV 保存", icon="download", on_click=download_csv).props("flat")

    # 図形の一覧（クリックで選択、チェックで表示・非表示）
    @ui.refreshable
    def shape_list():
        with ui.element().classes("w-full h-64 overflow-y-auto border rounded"):
            with ui.list().props("dense separator").classes("w-full"):
                for i, s in enumerate(shapes):
                    with ui.item(on_click=lambda i=i: select(i)).classes("shape-selected bg-blue-100" if i == sel["i"] else ""):
                        with ui.item_section().props("side"):
                            ui.checkbox(value=s["visible"], on_change=lambda e, s=s: update(s, "visible", e.value))
                        with ui.item_section():
                            ui.item_label().bind_text_from(s, "name")
                        with ui.item_section().props("side"):
                            ui.item_label(TYPES[s["type"]])

    # 選択中の図形の編集フォーム（種類に応じた入力欄だけを出す）
    @ui.refreshable
    def editor():
        if sel["i"] is None: return ui.label("「追加」か「CSV 読込」で図形を作ってください").classes("text-gray-500")
        s = shapes[sel["i"]]
        with ui.row().classes("w-full items-center no-wrap"):
            ui.input("名前", value=s["name"], on_change=lambda e: update(s, "name", e.value)).classes("grow")
            ui.select(TYPES, label="種類", value=s["type"], on_change=lambda e: change_type(s, e.value)).classes("w-28")
        for row in FIELDS[s["type"]]:
            with ui.row().classes("w-full no-wrap"):
                for key, label in row:
                    ui.number(label, value=s[key], step=1 if key in ("roll", "pitch", "yaw") or (key, s["type"]) == ("size", "line") else 0.01, on_change=lambda e, key=key: update(s, key, e.value)).classes("w-40")
        with ui.row().classes("w-full items-center no-wrap"):
            ui.color_input("色", value=s["color"], on_change=lambda e: update(s, "color", e.value)).classes("w-40")
            # 点・線は viser が不透明度に対応していないので出さない
            if s["type"] not in ("point", "line"):
                ui.label("不透明度").classes("whitespace-nowrap")
                ui.slider(min=0, max=1, step=0.05, value=s["opacity"], on_change=lambda e: update(s, "opacity", e.value)).props("label").classes("grow")

    shape_list()
    ui.separator()
    editor()


# 単体起動（Docker / Dev Container）用。exe では desktop/launcher.py から起動する
if __name__ in {"__main__", "__mp_main__"}:
    ui.run(host="0.0.0.0", port=8080, title="robot-viser", reload=False, show=False)
