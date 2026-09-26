"""使い方マニュアル（docs/manual/manual.md）用のスクリーンショットを、ダミーデータで操作しながら docs/manual/img/ に撮る。

アプリの改修後に画面を撮り直すためのスクリプト。アプリを起動した状態（docker compose up -d）で、プロジェクト直下から実行する。
前回の軌道や姿勢が画面に残らないよう、先に backend を再起動しておく。

    docker compose restart backend

    docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/work -w /work \\
        mcr.microsoft.com/playwright/python:v1.63.0-noble \\
        sh -c "pip install -q --user --break-system-packages playwright==1.63.0 && xvfb-run -a -s '-screen 0 1920x1080x24' python scripts/make_manual.py"

衝突判定のスライド（13〜15）は、衝突判定を有効にして起動した状態（COLLISION=1 docker compose up -d）で、--collision を付けて撮る。
このときは 13〜15 だけを撮る（01〜12 は、パネルにチェックボックスが写らない通常の起動で撮る）。

3D ビューア（viser）は WebGL で描画する。ヘッドレスの既定（SwiftShader）では 1 コマ 2 秒ほどかかり操作が追いつかないため、
Xvfb 上で Chromium を起動し、Mesa の llvmpipe で描画させている（1 コマ 0.4 秒ほど）。
Playwright の公式 Docker イメージでも動くよう、標準ライブラリと playwright だけを使う。
"""
import argparse
import csv
import math
import random
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


# ---- 画面操作と撮影 ----

class Manual:
    def __init__(self, page: Page, api: str):
        self.page, self.api = page, api
        self.viewer: FrameLocator = page.frame_locator("iframe")  # 右側の 3D ビューア（viser）

    def shot(self, name: str, *marks: Locator, wait: int = 1500):
        """marks を赤枠で囲んで撮る。3D ビューア内の部品にも枠を付けられるよう、ページ上の座標から枠を描く。"""
        self.page.wait_for_timeout(wait)  # 3D の描画やメニューのアニメーションが落ち着くのを待つ
        boxes = [b for mark in marks for el in mark.all() if (b := el.bounding_box())]
        self.page.evaluate("""boxes => boxes.forEach(r => {
            // 画面端の要素でも枠が切れないよう、画面内に収める
            const d = document.createElement('div');
            const left = Math.max(r.x - 5, 1), top = Math.max(r.y - 5, 1), right = Math.min(r.x + r.width + 5, innerWidth - 1), bottom = Math.min(r.y + r.height + 5, innerHeight - 1);
            d.className = 'manual-mark';
            Object.assign(d.style, {position: 'fixed', left: `${left}px`, top: `${top}px`, width: `${right - left}px`, height: `${bottom - top}px`, boxSizing: 'border-box',
                border: '3px solid #e53935', borderRadius: '6px', zIndex: 99999, pointerEvents: 'none'});
            document.body.append(d);
        })""", boxes)
        self.page.screenshot(path=OUT / f"{name}.png")
        self.page.evaluate("document.querySelectorAll('.manual-mark').forEach(e => e.remove())")
        print(name)

    def field(self, label: str) -> Locator:
        return self.page.locator(".q-field").filter(has=self.page.locator(".q-field__label", has_text=label))

    def notification(self, text: str) -> Locator:
        """通知が出て、せり上がるアニメーションが終わる（enter の class が外れ、画面内に収まる）まで待って返す。"""
        self.page.wait_for_function("""t => [...document.querySelectorAll('.q-notification')].some(e => {
            const r = e.getBoundingClientRect();
            return e.textContent.includes(t) && !/enter/.test(e.className) && r.height > 30 && r.bottom < innerHeight;
        })""", arg=text)
        return self.page.locator(".q-notification", has_text=text)

    def wait_robot(self, hand: str | None):
        """構成の切り替え（STL の読み直し）が終わるまで、backend の表示中の構成を見て待つ。"""
        for _ in range(60):
            if self.page.request.get(f"{self.api}/robots").json()["current"]["hand"] == hand: break
            self.page.wait_for_timeout(500)
        self.page.wait_for_timeout(3000)  # 読み込んだメッシュが 3D ビューアに届いて描画されるのを待つ


def steps(m: Manual, csv_path: Path, work: Path):
    page, viewer = m.page, m.viewer
    canvas, panel = page.locator("iframe"), page.locator(".q-card")

    # 3D ビューアの接続を待ち、ソフトウェア描画のときだけ出る警告を閉じる（利用者の PC では通常出ない）
    viewer.get_by_text("Connected").wait_for()
    page.wait_for_timeout(3000)
    viewer.locator(".mantine-Notification-closeButton").evaluate_all("els => els.forEach(e => e.click())")

    # 画面の全体（左の操作パネルと右の 3D ビューア）
    m.shot("01_start", panel, canvas)

    # 視点を変える：ホイールで拡大し、左ドラッグで回す
    page.mouse.move(840, 420)
    for _ in range(6): page.mouse.wheel(0, -150); page.wait_for_timeout(200)
    page.mouse.down(); page.mouse.move(760, 400, steps=5); page.mouse.up()
    m.shot("02_view", canvas, wait=3000)

    # 構成：アームのプルダウンを開く
    m.field("アーム").click()
    m.shot("03_arm", m.field("アーム"), page.locator(".q-menu"))
    page.keyboard.press("Escape")

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

    # 前回の障害物が写らないよう消してから、3D ビューアの接続を待つ（警告を閉じるのは steps と同じ）
    page.request.post(f"{m.api}/obstacles", data={"obstacles": []})
    viewer.get_by_text("Connected").wait_for()
    page.wait_for_timeout(3000)
    viewer.locator(".mantine-Notification-closeButton").evaluate_all("els => els.forEach(e => e.click())")

    # 衝突判定を有効にして起動すると、3D ビューアのパネルにチェックボックスが出る
    m.shot("13_collision_start", spheres)

    # 障害物を登録すると 3D に出る。見やすいよう少し拡大してから撮る
    page.request.post(f"{m.api}/obstacles", data={"obstacles": DUMMY_OBSTACLES})
    page.mouse.move(840, 420)
    for _ in range(6): page.mouse.wheel(0, -150); page.wait_for_timeout(200)
    m.shot("14_obstacles", canvas, wait=3000)

    # 近似球を表示し、関節角度を送信して姿勢に合わせて動くのを見せる（手先が球の障害物に近づく姿勢）
    viewer.get_by_role("checkbox").evaluate("e => e.click()")
    for label, value in zip([f"J{i}" for i in range(1, 7)], [-8, -20, 20, 0, 30, 0]): m.field(label).locator("input").fill(str(value))
    page.get_by_role("button", name="送信", exact=True).click()
    m.shot("15_spheres", spheres, canvas, wait=3000)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8080", help="アプリの画面の URL")
    parser.add_argument("--api", default="http://localhost:8000", help="backend API の URL（構成の切り替えの完了を待つのに使う）")
    parser.add_argument("--collision", action="store_true", help="衝突判定のスライド（13〜15）だけを撮る（衝突判定を有効にして起動しておく）")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        work = Path(tmp)
        csv_path = make_dummy_csv(work)
        # llvmpipe で描画させるため、ヘッドレスにせず Xvfb 上で起動する
        browser = p.chromium.launch(headless=False, args=["--ignore-gpu-blocklist", "--use-angle=gl"])
        page = browser.new_page(viewport={"width": 1280, "height": 800}, accept_downloads=True)
        page.set_default_timeout(60_000)
        page.goto(args.url)
        if args.collision: collision_steps(Manual(page, args.api))
        else: steps(Manual(page, args.api), csv_path, work)
        browser.close()


if __name__ == "__main__":
    main()
