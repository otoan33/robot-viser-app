import io
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import viser
from fastapi import FastAPI, Response, UploadFile
from PIL import Image
from pydantic import BaseModel, Field

from backend.collision import compute_distances
from backend.robot import Robot, TrajectoryPlayer, load_trajectory, record_trajectory
from backend.shapes import Shape, draw_shapes, dump_shapes_csv, load_shapes_csv

# 3D ビューア（viser）を起動する。frontend（NiceGUI :8080）と衝突しないよう既定は 8081。exe では待ち受けアドレスも起動オプションで変える
server = viser.ViserServer(host=os.environ.get("VISER_HOST", "0.0.0.0"), port=int(os.environ.get("VISER_PORT", 8081)))
server.scene.add_grid("/grid", width=2.0, height=2.0)

# 衝突判定（近似球・障害物・距離 API）は環境変数 COLLISION=1 のときだけ有効にする
COLLISION = os.environ.get("COLLISION", "0") == "1"

# 起動時はフォルダ名順で先頭のアームとハンドを表示する
robot = Robot(server, collision=COLLISION)
configs = Robot.list_configs()
robot.load(configs["arms"][0], configs["hands"][0] if configs["hands"] else None)

# 軌道の再生位置を手動シークするスライダー（値はフレーム番号）と再生/停止トグルボタン。軌道を読み込むまでは無効
slider = server.gui.add_slider("Time (frame)", min=0, max=0, step=1, initial_value=0, disabled=True)
button = server.gui.add_button("Play", disabled=True)
player = TrajectoryPlayer(robot, slider, button)


# 再生中なら停止し、停止中なら今のスライダー位置から再開する（末尾まで再生済みなら先頭から）
@button.on_click
def _(_event: viser.GuiEvent):
    if button.label == "Stop": return player.stop()
    start = int(slider.value) if slider.value < len(player.times) - 1 else 0
    player.play(player.times, player.angles_list, start)


# ユーザーがスライダーを動かしたら自動再生を止め、そのフレームの姿勢にする
# （プレイヤーによるサーバー側からの更新は event.client が None なので無視する）
@slider.on_update
def _(event: viser.GuiEvent):
    if event.client is None: return
    player.stop()
    robot.set_angles(player.angles_list[int(slider.value)])


# 画像を PNG のバイト列にする
def to_png(image) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(image).save(buf, format="PNG")
    return buf.getvalue()


# 押したブラウザのカメラ視点で描画し、そのブラウザに PNG を保存させる
screenshot_button = server.gui.add_button("Screenshot")
@screenshot_button.on_click
def _(event: viser.GuiEvent):
    event.client.send_file_download(f"screenshot_{datetime.now():%Y%m%d_%H%M%S}.png", to_png(robot.render(event.client)), save_immediately=True)


app = FastAPI(title="robot-viser API")


# 表示する構成の指定（hand を省略・null にするとアームのみ）
class RobotRequest(BaseModel):
    arm: str
    hand: str | None = None


# 選べるアーム・ハンドと、表示中の構成を返す
@app.get("/robots")
def get_robots():
    return {**Robot.list_configs(), "current": {"arm": robot.arm_name, "hand": robot.hand_name}}


# 表示する構成を切り替える（再生中の軌道は止めてから差し替える）
@app.post("/robot")
def post_robot(req: RobotRequest):
    player.stop()
    robot.load(req.arm, req.hand)
    return {"ok": True}


# 6軸アーム専用のため、常に6個の角度[deg]をまとめて受け取る
class AnglesRequest(BaseModel):
    angles: list[float] = Field(min_length=6, max_length=6)


# 関節角度を表示姿勢に反映する（送りっぱなしの SET 専用）
@app.post("/joints")
def post_joints(req: AnglesRequest):
    robot.set_angles(req.angles)
    return {"ok": True}


# 再生する軌道 CSV の指定（ファイル本体ではなく、backend から見えるパスを送る）
class TrajectoryRequest(BaseModel):
    csv_path: str


# 軌道を読み込み、バックグラウンドで再生する
def play_trajectory(text: str) -> dict:
    times, angles_list = load_trajectory(text)
    player.play(times, angles_list)
    return {"ok": True, "num_points": len(times), "duration_sec": times[-1] - times[0]}


# backend から見えるパスの軌道 CSV を再生する
@app.post("/trajectory")
def post_trajectory(req: TrajectoryRequest):
    return play_trajectory(Path(req.csv_path).read_text(encoding="utf-8-sig"))


# 送られてきた軌道 CSV ファイルを再生する（frontend のアップロードなど、backend と別環境にあるファイル用）
# （player.stop() の待ちでイベントループを止めないよう、同期関数にしてスレッドプールで動かす）
@app.post("/trajectory/upload")
def post_trajectory_upload(file: UploadFile):
    return play_trajectory(file.file.read().decode("utf-8-sig"))


# 送られてきた軌道 CSV をコマ送りで録画して動画（mp4 / gif）を返し、その後は通常どおり再生する
# （描画に viser 画面を開いているブラウザが必要。録画中はそのブラウザの表示がコマ送りになる）
@app.post("/trajectory/record")
def post_trajectory_record(file: UploadFile, format: Literal["mp4", "gif"] = "mp4"):
    times, angles_list = load_trajectory(file.file.read().decode("utf-8-sig"))
    player.stop()
    video = record_trajectory(robot, times, angles_list, format)
    player.play(times, angles_list)
    return Response(video, media_type="video/mp4" if format == "mp4" else "image/gif")


# 今の 3D 表示を PNG で返す（viser 画面を開いているブラウザが必要）
@app.get("/screenshot")
def get_screenshot():
    return Response(to_png(robot.render()), media_type="image/png")


# ---- 補助図形（点・線・球・円柱・直方体）。一覧は backend が持ち、丸ごと置き換えて描き直す ----
shapes: list[dict] = []


# 一覧を置き換えて描き直し、置き換え後の一覧（省略した項目は既定値で埋まる）を返す
def set_shapes(new: list[dict]) -> dict:
    shapes[:] = new
    draw_shapes(server, shapes)
    return {"shapes": shapes}


class ShapesRequest(BaseModel):
    shapes: list[Shape]


# 表示中の図形の一覧を返す
@app.get("/shapes")
def get_shapes():
    return {"shapes": shapes}


# 図形の一覧を丸ごと置き換える
@app.post("/shapes")
def post_shapes(req: ShapesRequest):
    return set_shapes([s.model_dump() for s in req.shapes])


# 送られてきた図形 CSV で一覧を丸ごと置き換える
@app.post("/shapes/upload")
def post_shapes_upload(file: UploadFile):
    return set_shapes(load_shapes_csv(file.file.read().decode("utf-8-sig")))


# 今の図形の一覧を CSV で返す（/shapes/upload でそのまま読み戻せる）
@app.get("/shapes/csv")
def get_shapes_csv():
    return Response(dump_shapes_csv(shapes), media_type="text/csv")


# ---- 衝突判定（COLLISION=1 のときだけ。無効なら GUI にも /docs にも出ない） ----
if COLLISION:
    # 近似球の表示・非表示を viser のパネルで切り替える（近似の当てはまり具合の確認用）
    spheres_checkbox = server.gui.add_checkbox("Show collision spheres", initial_value=False)
    @spheres_checkbox.on_update
    def _(_event: viser.GuiEvent):
        robot.set_spheres_visible(spheres_checkbox.value)

    # 障害物の形状（座標は base_link 基準 [m]、姿勢は roll / pitch / yaw [rad]）
    Vec3 = Annotated[list[float], Field(min_length=3, max_length=3)]

    class SphereObstacle(BaseModel):
        type: Literal["sphere"]
        name: str
        center: Vec3
        radius: float

    class BoxObstacle(BaseModel):
        type: Literal["box"]
        name: str
        center: Vec3
        size: Vec3
        rpy: Vec3 = [0.0, 0.0, 0.0]

    class CapsuleObstacle(BaseModel):
        type: Literal["capsule"]
        name: str
        p1: Vec3
        p2: Vec3
        radius: float

    class ObstaclesRequest(BaseModel):
        obstacles: list[Annotated[SphereObstacle | BoxObstacle | CapsuleObstacle, Field(discriminator="type")]]

    # 距離を求める関節角度[deg]（6軸）。max_distance [m] を指定すると、それ以下の距離のペアだけを返す
    class DistancesRequest(BaseModel):
        angles: list[float] = Field(min_length=6, max_length=6)
        max_distance: float | None = None

    # 登録中の障害物を返す
    @app.get("/obstacles")
    def get_obstacles():
        return {"obstacles": robot.obstacles}

    # 障害物を丸ごと置き換えて、viser に表示する
    @app.post("/obstacles")
    def post_obstacles(req: ObstaclesRequest):
        robot.set_obstacles([o.model_dump() for o in req.obstacles])
        return {"ok": True, "num_obstacles": len(req.obstacles)}

    # 関節角度での近似球（制御点）の位置・ヤコビアンと、球×障害物の距離・向きを返す（表示の姿勢は変えない）
    @app.post("/distances")
    def post_distances(req: DistancesRequest):
        return compute_distances(robot.collision, robot.obstacles, req.angles, req.max_distance)
