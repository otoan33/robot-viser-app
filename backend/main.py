import os
from pathlib import Path

import viser
from fastapi import FastAPI, UploadFile
from pydantic import BaseModel, Field

from backend.robot import Robot, TrajectoryPlayer, load_trajectory

# 3D ビューア（viser）を起動する。frontend（NiceGUI :8080）と衝突しないよう既定は 8081
server = viser.ViserServer(port=int(os.environ.get("VISER_PORT", 8081)))
server.scene.add_grid("/grid", width=2.0, height=2.0)

# 起動時はフォルダ名順で先頭のアームとハンドを表示する
robot = Robot(server)
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
