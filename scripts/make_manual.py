"""使い方マニュアル（docs/manual/manual.md）用のスクリーンショットを、ダミーデータで操作しながら docs/manual/img/ に撮る。

アプリの改修後に画面を撮り直すためのスクリプト。アプリを起動した状態（docker compose up -d）で、プロジェクト直下から実行する。
前回の軌道や姿勢が画面に残らないよう、先に backend を再起動しておく。

    docker compose restart backend

    docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/work -w /work \\
        mcr.microsoft.com/playwright/python:v1.63.0-noble \\
        sh -c "pip install -q --user --break-system-packages playwright==1.63.0 && xvfb-run -a -s '-screen 0 1920x1080x24' python scripts/make_manual.py"

衝突判定のスライド（20〜22）は、衝突判定を有効にして起動した状態（COLLISION=1 docker compose up -d）で、--collision を付けて撮る。
このときは 20〜22 だけを撮る（01〜19 は、パネルにチェックボックスが写らない通常の起動で撮る）。

図形ウィンドウ（別ウィンドウ）のスライド（13〜19）は、図形ウィンドウをメイン画面の左に重ねた 1 枚にする。
入力と 3D ビューアへの反映を 1 枚で見せ、画像の大きさもほかのスライドとそろえるため。

3D ビューア（viser）は WebGL で描画する。ヘッドレスの既定（SwiftShader）では 1 コマ 2 秒ほどかかり操作が追いつかないため、
Xvfb 上で Chromium を起動し、Mesa の llvmpipe で描画させている（1 コマ 0.4 秒ほど）。
Playwright の公式 Docker イメージでも動くよう、標準ライブラリと playwright だけを使う。
"""
import argparse
import base64
import csv
import math
import random
import re
import tempfile
from pathlib import Path

from playwright.sync_api import FrameLocator, Locator, Page, sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "manual" / "img"
rng = random.Random(0)  # 撮り直しても同じ画像になるよう乱数を固定する


# ---- ダミーデータ（実機のログを写さないため、乱数から作る） ----

def make_dummy_csv(folder: Path) -> Path:
    # t 形式の軌道（t[sec] と 6 関節の角度[deg]）。各関節をなめらかに往復させる 6 秒分
    amps, phases = [rng.uniform(10, 25) for _ in range(6)], [rng.uniform(0, math.pi) for _ in range(6)]
    rows = [[round(t, 2), *[round(a * math.sin(2 * math.pi * t / 6 + p) - a * math.sin(p), 3) for a, p in zip(amps, phases)]] for t in [i * 0.05 for i in range(121)]]
    path = folder / "sample_motion.csv"
    with open(path, "w", newline="") as f: csv.writer(f).writerows([["t", *[f"joint{i}" for i in range(1, 7)]], *rows])
    return path


def make_dummy_shapes_csv(folder: Path) -> Path:
    # 図形 CSV（寸法は mm）。図形ウィンドウに隠れない 3D ビューアの右側（-x）に、作業台と目標点・手先からの経路・柱・球を置く
    rows = [
        ["name", "type", "visible", "x", "y", "z", "x2", "y2", "z2", "sx", "sy", "sz", "roll", "pitch", "yaw", "size", "color", "opacity"],
        ["goal", "point", "true", -650, 200, 450, "", "", "", "", "", "", "", "", "", 50, "#43a047", ""],
        ["path", "line", "true", -200, 200, 1000, -650, 200, 450, "", "", "", "", "", "", 10, "#1e88e5", ""],
        ["table", "box", "true", -650, 200, 300, "", "", "", 500, 600, 40, 0, 0, 0, "", "#a1887f", 0.8],
        ["pole", "cylinder", "true", -1300, -200, 0, -1300, -200, 1000, "", "", "", "", "", "", 40, "#8e24aa", ""],
        ["ball", "sphere", "true", -300, 700, 900, "", "", "", "", "", "", "", "", "", 100, "#fb8c00", 0.6],
    ]
    path = folder / "sample_shapes.csv"
    with open(path, "w", newline="") as f: csv.writer(f).writerows(rows)
    return path


# ---- 画面操作と撮影 ----

def marked_png(page: Page, marks: list[Locator]) -> bytes:
    """marks を赤枠で囲んだ画面の PNG を返す。3D ビューア内の部品にも枠を付けられるよう、ページ上の座標から枠を描く。"""
    boxes = [b for mark in marks for el in mark.all() if (b := el.bounding_box())]
    page.evaluate("""boxes => boxes.forEach(r => {
        // 画面端の要素でも枠が切れないよう、画面内に収める
        const d = document.createElement('div');
        const left = Math.max(r.x - 5, 1), top = Math.max(r.y - 5, 1), right = Math.min(r.x + r.width + 5, innerWidth - 1), bottom = Math.min(r.y + r.height + 5, innerHeight - 1);
        d.className = 'manual-mark';
        Object.assign(d.style, {position: 'fixed', left: `${left}px`, top: `${top}px`, width: `${right - left}px`, height: `${bottom - top}px`, boxSizing: 'border-box',
            border: '3px solid #e53935', borderRadius: '6px', zIndex: 99999, pointerEvents: 'none'});
        document.body.append(d);
    })""", boxes)
    png = page.screenshot()
    page.evaluate("document.querySelectorAll('.manual-mark').forEach(e => e.remove())")
    return png


class Manual:
    def __init__(self, page: Page, api: str):
        self.page, self.api = page, api
        self.viewer: FrameLocator = page.frame_locator("iframe")  # 右側の 3D ビューア（viser）
        self.win: Page | None = None  # 図形ウィンドウ（開いてから入る）

    def shot(self, name: str, *marks: Locator, wait: int = 1500):
        """marks を赤枠で囲んで撮る。"""
        self.page.wait_for_timeout(wait)  # 3D の描画やメニューのアニメーションが落ち着くのを待つ
        (OUT / f"{name}.png").write_bytes(marked_png(self.page, list(marks)))
        print(name)

    def window_shot(self, name: str, *marks: Locator, wait: int = 2000):
        """図形ウィンドウをメイン画面の左に重ねて撮る。marks はどちらの画面の部品でもよく、それぞれの画面で赤枠を付ける。"""
        self.page.wait_for_timeout(wait)  # 図形の変更が 3D ビューアに届いて描画されるのを待つ
        main, win = [base64.b64encode(marked_png(pg, [m for m in marks if m.page == pg])).decode() for pg in (self.page, self.win)]
        # 図形ボタンが隠れないよう、ボタンの下からウィンドウを置く。タイトルバーと影を付けて別ウィンドウに見せる
        canvas = self.page.context.browser.new_page(viewport={"width": 1280, "height": 800})
        canvas.set_content(f"""<body style="margin:0; overflow:hidden">
            <img src="data:image/png;base64,{main}" style="display:block">
            <div style="position:fixed; left:20px; top:72px; border:1px solid #9aa5b1; border-radius:8px; overflow:hidden; box-shadow:0 10px 36px rgba(0,0,0,.35)">
              <div style="height:28px; background:#dde3ea; font:bold 13px sans-serif; color:#333; display:flex; align-items:center; padding-left:12px">図形</div>
              <img src="data:image/png;base64,{win}" style="display:block">
            </div></body>""")
        canvas.screenshot(path=OUT / f"{name}.png")
        canvas.close()
        print(name)

    def field(self, label: str, page: Page | None = None) -> Locator:
        page = page or self.page
        return page.locator(".q-field").filter(has=page.locator(".q-field__label", has_text=re.compile(f"^{re.escape(label)}$")))

    def notification(self, text: str, page: Page | None = None) -> Locator:
        """通知が出て、せり上がるアニメーションが終わる（enter の class が外れ、画面内に収まる）まで待って返す。"""
        page = page or self.page
        page.wait_for_function("""t => [...document.querySelectorAll('.q-notification')].some(e => {
            const r = e.getBoundingClientRect();
            return e.textContent.includes(t) && !/enter/.test(e.className) && r.height > 30 && r.bottom < innerHeight;
        })""", arg=text)
        return page.locator(".q-notification", has_text=text)

    def wait_robot(self, hand: str | None):
        """構成の切り替え（STL の読み直し）が終わるまで、backend の表示中の構成を見て待つ。"""
        for _ in range(60):
            if self.page.request.get(f"{self.api}/robots").json()["current"]["hand"] == hand: break
            self.page.wait_for_timeout(500)
        self.page.wait_for_timeout(3000)  # 読み込んだメッシュが 3D ビューアに届いて描画されるのを待つ


def steps(m: Manual, csv_path: Path, shapes_path: Path, work: Path):
    page, viewer = m.page, m.viewer
    canvas, panel = page.locator("iframe"), page.locator(".q-card")

    # 前回の図形が写らないよう消してから、3D ビューアの接続を待ち、ソフトウェア描画のときだけ出る警告を閉じる（利用者の PC では通常出ない）
    viewer.get_by_text("Connected").wait_for()
    page.wait_for_timeout(3000)
    viewer.locator(".mantine-Notification-closeButton").evaluate_all("els => els.forEach(e => e.click())")

    # 画面の全体（左の操作パネルと右の 3D ビューア）
    page.request.post(f"{m.api}/shapes", data={"shapes": []})
    m.shot("01_start", panel, canvas)

    # 視点を変える：ホイールで拡大し、左ドラッグで回す
    page.mouse.move(840, 420)
    for _ in range(6): page.mouse.wheel(0, -150); page.wait_for_timeout(200)
    page.mouse.down(); page.mouse.move(760, 400, steps=5); page.mouse.up()
    m.shot("02_view", canvas, wait=3000)

    # 構成：アームのプルダウンを開く
    m.field("アーム").click()
    m.shot("03_arm", m.field("アーム"), page.locator(".q-menu"))
    page.keyboard.press("Escape"); page.locator(".q-menu").wait_for(state="detached")  # 閉じる途中のメニューに次のクリックが当たらないよう待つ

    # 構成：ハンドを「なし」にして、アームだけの表示にする
    m.field("ハンド").click(); page.locator(".q-menu .q-item", has_text="なし").click()
    m.wait_robot(None)
    m.shot("04_hand", m.field("ハンド"), canvas)

    # 以降はハンド付きで撮るため、元の構成に戻す
    m.field("ハンド").click(); page.locator(".q-menu .q-item", has_text="hand_jig").click()
    m.wait_robot("hand_jig")

    # 関節角度を入力する（ダミーの姿勢）
    for label, value in zip([f"J{i}" for i in range(1, 7)], [30, -20, 15, 0, 45, 0]): m.field(label).locator("input").fill(str(value))
    m.shot("05_joints", *[m.field(f"J{i}") for i in range(1, 7)])

    # 送信して、3D の姿勢が変わるのを見せる
    page.get_by_role("button", name="送信", exact=True).click()
    m.shot("06_send", page.get_by_role("button", name="送信", exact=True), canvas, wait=3000)

    # 軌道 CSV を読み込むと、点数と再生時間の通知が出て再生が始まる
    page.locator("input[type=file]").set_input_files(csv_path)
    m.shot("07_csv", page.get_by_role("button", name="CSV を選択"), m.notification("点 /"), wait=0)

    # 再生の途中で Stop を押し、Time スライダーと Play / Stop を見せる
    page.wait_for_timeout(1500)
    viewer.get_by_role("button", name="Stop").evaluate("e => e.click()")
    viewer.get_by_role("button", name="Play").wait_for()
    page.locator(".q-notification", has_text="点 /").wait_for(state="hidden")  # 前の通知が写り込まないよう、消えるのを待つ
    m.shot("08_play", viewer.locator(".mantine-Slider-root").locator("xpath=../.."), viewer.get_by_role("button", name="Play"))

    # 録画の設定：チェックを入れて形式を選ぶ
    page.get_by_text("録画する").click()
    fmt = page.locator(".q-card", has_text="軌道").locator(".q-select")
    fmt.click()
    m.shot("09_record", page.locator(".q-checkbox"), fmt, page.locator(".q-menu"))
    page.locator(".q-menu .q-item", has_text="mp4").click()

    # 録画する：CSV を選ぶと録画が始まり、終わると動画をダウンロードする
    with page.expect_download(timeout=600_000) as download:
        page.locator("input[type=file]").set_input_files(csv_path)
        m.shot("10_recording", page.get_by_role("button", name="CSV を選択"), m.notification("録画中"), wait=0)
    download.value.save_as(work / download.value.suggested_filename)
    m.shot("11_saved", m.notification("録画を保存しました"), wait=0)
    page.locator(".q-notification", has_text="録画を保存しました").wait_for(state="hidden")  # 次の画面に通知が残らないよう、消えるのを待つ

    # 3D ビューアの Screenshot ボタンで、見えている視点のまま PNG を保存する
    with page.expect_download(timeout=60_000) as download:
        viewer.get_by_role("button", name="Screenshot").evaluate("e => e.click()")
    download.value.save_as(work / download.value.suggested_filename)
    m.shot("12_screenshot", viewer.get_by_role("button", name="Screenshot"))

    # 図形ウィンドウを開く（最初は空）。重ねたときにメイン画面の 3D ビューアが見えるよう、ウィンドウは小さめにする
    shapes_button = page.get_by_role("button", name="図形")
    with page.expect_popup() as popup: shapes_button.click()
    m.win = win = popup.value
    win.set_viewport_size({"width": 660, "height": 680})
    win.get_by_role("button", name="追加").wait_for()
    m.window_shot("13_shapes_open", shapes_button)

    # 追加のメニューを開いて、種類を選ぶ
    win.get_by_role("button", name="追加").click()
    m.window_shot("14_shapes_add", win.get_by_role("button", name="追加"), win.locator(".q-menu"), wait=1000)
    win.locator(".q-menu .q-item", has_text="球").click()

    # 名前・中心・半径を mm で入れると、すぐ 3D に出る
    for label, value in [("名前", "ball"), ("中心 x [mm]", -400), ("y", 500), ("z", 800), ("半径 [mm]", 120)]: m.field(label, win).locator("input").fill(str(value))
    m.window_shot("15_shapes_values", *[m.field(label, win) for label in ["中心 x [mm]", "y", "z", "半径 [mm]"]])

    # 色と不透明度を変える（不透明度はスライダーの 6 割の位置を押して 0.6 にする）
    m.field("色", win).locator("input").fill("#fb8c00")
    box = win.locator(".q-slider").bounding_box()
    win.mouse.click(box["x"] + box["width"] * 0.6, box["y"] + box["height"] / 2)
    m.window_shot("16_shapes_color", m.field("色", win), win.locator(".q-slider"))

    # 図形 CSV を読み込むと、一覧が CSV の内容に置き換わる
    win.locator("input[type=file]").set_input_files(shapes_path)
    m.window_shot("17_shapes_csv", win.get_by_role("button", name="CSV 読込"), m.notification("個の図形を読み込みました", win), win.locator(".q-list"), wait=0)
    win.locator(".q-notification").wait_for(state="hidden")  # 次の画面に通知が残らないよう、消えるのを待つ

    # 一覧で柱を選び、作業台のチェックを外して非表示にする（複製・削除は選んでいる図形が対象）
    win.locator(".q-item", has_text="pole").click()
    table_check = win.locator(".q-item", has_text="table").locator(".q-checkbox")
    table_check.click()
    m.window_shot("18_shapes_list", table_check, win.get_by_role("button", name="複製"), win.get_by_role("button", name="削除"))

    # 今の一覧を CSV で保存する
    with win.expect_download() as download: win.get_by_role("button", name="CSV 保存").click()
    download.value.save_as(work / download.value.suggested_filename)
    m.window_shot("19_shapes_save", win.get_by_role("button", name="CSV 保存"), wait=1000)


# 撮影用のダミーの障害物（ロボットの前の台・手先付近の球・横の柱）
DUMMY_OBSTACLES = [
    {"type": "box", "name": "table", "center": [0.0, 1.0, 0.4], "size": [0.9, 0.5, 0.05], "rpy": [0.0, 0.0, 0.0]},
    {"type": "sphere", "name": "ball", "center": [0.35, 0.8, 1.0], "radius": 0.12},
    {"type": "capsule", "name": "pole", "p1": [0.6, 0.2, 0.0], "p2": [0.6, 0.2, 1.3], "radius": 0.05},
]


def collision_steps(m: Manual):
    page, viewer = m.page, m.viewer
    canvas = page.locator("iframe")
    spheres = viewer.get_by_text("Show collision spheres").locator("xpath=../../..")  # チェックボックスの行（ラベルとボックス）

    # 前回の障害物と図形が写らないよう消してから、3D ビューアの接続を待つ（警告を閉じるのは steps と同じ）
    page.request.post(f"{m.api}/obstacles", data={"obstacles": []})
    page.request.post(f"{m.api}/shapes", data={"shapes": []})
    viewer.get_by_text("Connected").wait_for()
    page.wait_for_timeout(3000)
    viewer.locator(".mantine-Notification-closeButton").evaluate_all("els => els.forEach(e => e.click())")

    # 衝突判定を有効にして起動すると、3D ビューアのパネルにチェックボックスが出る
    m.shot("20_collision_start", spheres)

    # 障害物を登録すると 3D に出る。見やすいよう少し拡大してから撮る
    page.request.post(f"{m.api}/obstacles", data={"obstacles": DUMMY_OBSTACLES})
    page.mouse.move(840, 420)
    for _ in range(6): page.mouse.wheel(0, -150); page.wait_for_timeout(200)
    m.shot("21_obstacles", canvas, wait=3000)

    # 近似球を表示し、関節角度を送信して姿勢に合わせて動くのを見せる（手先が球の障害物に近づく姿勢）
    viewer.get_by_role("checkbox").evaluate("e => e.click()")
    for label, value in zip([f"J{i}" for i in range(1, 7)], [-8, -20, 20, 0, 30, 0]): m.field(label).locator("input").fill(str(value))
    page.get_by_role("button", name="送信", exact=True).click()
    m.shot("22_spheres", spheres, canvas, wait=3000)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8080", help="アプリの画面の URL")
    parser.add_argument("--api", default="http://localhost:8000", help="backend API の URL（構成の切り替えの完了を待つのに使う）")
    parser.add_argument("--collision", action="store_true", help="衝突判定のスライド（20〜22）だけを撮る（衝突判定を有効にして起動しておく）")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        work = Path(tmp)
        csv_path, shapes_path = make_dummy_csv(work), make_dummy_shapes_csv(work)
        # llvmpipe で描画させるため、ヘッドレスにせず Xvfb 上で起動する
        browser = p.chromium.launch(headless=False, args=["--ignore-gpu-blocklist", "--use-angle=gl"])
        page = browser.new_page(viewport={"width": 1280, "height": 800}, accept_downloads=True)
        page.set_default_timeout(60_000)
        page.goto(args.url)
        m = Manual(page, args.api)
        if args.collision: collision_steps(m)
        else: steps(m, csv_path, shapes_path, work)
        browser.close()


if __name__ == "__main__":
    main()
