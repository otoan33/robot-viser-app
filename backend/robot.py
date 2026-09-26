import csv
import json
import tempfile
import threading
import time
from functools import partial
from pathlib import Path

import imageio
import numpy as np
import trimesh
import viser
import viser.transforms as vtf
import yourdfpy
from viser.extras import ViserUrdf
from viser.extras._urdf import _viser_name_from_frame

from backend.collision import CollisionModel, pose

# アーム・ハンドの候補は assets/arms/<名前>/ と assets/hands/<名前>/ のフォルダで管理する
ASSETS_DIR = Path(__file__).parent / "assets"


# メッシュの相対パスを URDF のある位置から解決するため
def load_urdf(folder: Path) -> yourdfpy.URDF:
    path = next(folder.glob("*.urdf"))
    return yourdfpy.URDF.load(str(path), filename_handler=partial(yourdfpy.filename_handler_magic, dir=str(folder)))


class Robot:
    """viser 上のアーム＋ハンド表示。構成を差し替えても同じインスタンスで扱えるようにする。"""

    def __init__(self, server: viser.ViserServer, collision: bool = False):
        self.server = server
        self.arm: ViserUrdf | None = None
        self.hand: ViserUrdf | None = None
        self.arm_name: str | None = None
        self.hand_name: str | None = None
        # API は別スレッドで同時に呼ばれるため、差し替え途中のノードに姿勢を書き込まないよう排他する
        self._lock = threading.Lock()
        # 衝突判定（近似球・障害物）は有効にしたときだけ使う
        self.collision_enabled = collision
        self.collision: CollisionModel | None = None
        self.obstacles: list[dict] = []
        self.show_spheres = False
        self._sphere_handles, self._obstacle_handles = [], []

    # 選択可能な構成の一覧（フォルダ名）
    @staticmethod
    def list_configs() -> dict:
        names = lambda kind: sorted(p.name for p in (ASSETS_DIR / kind).iterdir() if p.is_dir())
        return {"arms": names("arms"), "hands": names("hands")}

    # 表示中の構成を消して、指定のアーム（とハンド）で表示し直す
    def load(self, arm_name: str, hand_name: str | None = None) -> None:
        with self._lock:
            # アームの visual ルート配下にハンドもぶら下がっているので、まとめて消える
            self.server.scene.remove_by_name("/visual")
            arm_model = load_urdf(ASSETS_DIR / "arms" / arm_name)
            self.arm, self.hand = ViserUrdf(self.server, urdf_or_path=arm_model), None
            self.arm_name, self.hand_name = arm_name, hand_name

            # ハンドはアームの取り付けリンクのフレームを親にし、mount.json の位置・姿勢でオフセットする
            hand_dir, hand_model = None, None
            if hand_name:
                hand_dir, hand_model = ASSETS_DIR / "hands" / hand_name, load_urdf(ASSETS_DIR / "hands" / hand_name)
                mount = json.loads((hand_dir / "mount.json").read_text())
                parent = _viser_name_from_frame(arm_model.scene, mount["link"], "/visual")
                self.hand = ViserUrdf(self.server, urdf_or_path=hand_model, root_node_name=parent)
                self.hand._visual_root_frame.position = mount["position"]
                self.hand._visual_root_frame.wxyz = vtf.SO3.from_rpy_radians(*mount["rpy"]).wxyz
                self.hand.update_cfg(np.zeros(len(self.hand.get_actuated_joint_names())))

            # 近似球をアームの各リンクのフレームの子として置き、関節角度の変更に追従させる（/visual を消すと一緒に消える）
            if self.collision_enabled:
                self.collision = CollisionModel(ASSETS_DIR / "arms" / arm_name, arm_model, hand_dir, hand_model)
                self._sphere_handles = [
                    self.server.scene.add_icosphere(f"{_viser_name_from_frame(arm_model.scene, p, '/visual')}/collision_{k}", radius=r, color=(255, 140, 0), subdivisions=2, opacity=0.4, position=c, visible=self.show_spheres)
                    for k, (p, c, r) in enumerate(zip(self.collision.parents, self.collision.centers, self.collision.radii))]

            # 全関節0の姿勢で表示する
            self.arm.update_cfg(np.zeros(len(self.arm.get_actuated_joint_names())))

    # 近似球の表示・非表示を切り替える（構成を切り替えた後も引き継ぐ）
    def set_spheres_visible(self, visible: bool) -> None:
        with self._lock:
            self.show_spheres = visible
            for h in self._sphere_handles: h.visible = visible

    # 障害物を丸ごと置き換え、半透明で描き直す
    def set_obstacles(self, obstacles: list[dict]) -> None:
        for h in self._obstacle_handles: h.remove()
        self.obstacles, self._obstacle_handles = obstacles, []
        for i, ob in enumerate(obstacles):
            # 形状ごとに原点中心のメッシュと、置く位置・姿勢を作る（カプセルは Z 軸を p1→p2 の向きに合わせる）
            if ob["type"] == "sphere":
                mesh, T = trimesh.creation.icosphere(subdivisions=3, radius=ob["radius"]), trimesh.transformations.translation_matrix(ob["center"])
            elif ob["type"] == "box":
                mesh, T = trimesh.creation.box(extents=ob["size"]), pose(ob["center"], ob["rpy"])
            else:
                a, b = np.asarray(ob["p1"], dtype=float), np.asarray(ob["p2"], dtype=float)
                mesh, T = trimesh.creation.capsule(height=np.linalg.norm(b - a), radius=ob["radius"]), trimesh.geometry.align_vectors([0, 0, 1], b - a)
                T[:3, 3] = (a + b) / 2
            mesh.apply_transform(T)
            self._obstacle_handles.append(self.server.scene.add_mesh_simple(f"/obstacles/{i}", mesh.vertices, mesh.faces, color=(220, 60, 60), opacity=0.5))

    # 今の 3D 表示を画像 (H, W, 3) で取得する。描画はブラウザ側で行うため、指定のブラウザ（省略時は先頭の1つ）のカメラ視点・画面サイズを使う
    # （scale で解像度だけを縮められる。視野は変わらない）
    def render(self, client: viser.ClientHandle | None = None, scale: float = 1.0) -> np.ndarray:
        camera = (client or next(iter(self.server.get_clients().values()))).camera
        return camera.get_render(int(camera.image_height * scale), int(camera.image_width * scale))

    # 関節角度[deg]（アーム URDF の可動関節の定義順）を表示姿勢に反映する
    def set_angles(self, angles) -> None:
        with self._lock:
            self.arm.update_cfg(np.deg2rad(np.array(angles, dtype=float)))


# 軌道 CSV を (時刻[sec] のリスト, 6関節角度[deg] のリスト) として読み込む
# （パス指定とアップロードの両方から使えるよう、CSV の中身の文字列を受け取る）
def load_trajectory(text: str) -> tuple[list[float], list[list[float]]]:
    rows = [row for row in csv.reader(text.splitlines()) if row]
    # t 形式: ヘッダ "t,joint1..joint6" + t[sec] と6関節角度[deg]
    if rows[0][0].strip().lower() == "t":
        return [float(r[0]) for r in rows[1:]], [[float(v) for v in r[1:7]] for r in rows[1:]]
    # ロボットログ形式: メタ情報2行 + データヘッダ。末尾の終了情報行は列数が合わないので除く
    header = rows[2]
    t_col, j_cols = header.index("ElapsedTime[msec]"), [header.index(f"Joint(J{i})[deg]") for i in range(1, 7)]
    data = [r for r in rows[3:] if len(r) == len(header)]
    return [float(r[t_col]) / 1000 for r in data], [[float(r[c]) for c in j_cols] for r in data]


# 軌道を一定 fps のコマ送りで描画し、動画（mp4 / gif）のバイト列にする
# （1コマの描画に時間がかかっても、動画の再生速度は軌道の時刻どおりになる）
def record_trajectory(robot: Robot, times: list[float], angles_list: list[list[float]], fmt: str, fps: int = 20) -> bytes:
    # 各コマの時刻に対応する（直前の）軌道フレームを選ぶ
    indices = np.searchsorted(times, np.arange(times[0], times[-1] + 1e-9, 1 / fps), side="right") - 1
    frames = []
    for i in indices:
        robot.set_angles(angles_list[i])
        # gif は書き出しが遅くファイルも大きくなるため、半分の解像度で描画する
        frames.append(robot.render(scale=1.0 if fmt == "mp4" else 0.5))
    # 書き出しはファイル経由（mp4 は ffmpeg がファイルに書くため）
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / f"record.{fmt}"
        if fmt == "mp4": imageio.mimwrite(path, frames, fps=fps)
        else: imageio.mimwrite(path, frames, duration=1000 / fps, loop=0)
        return path.read_bytes()


class TrajectoryPlayer:
    """軌道をバックグラウンドスレッドで再生し、スライダーとボタンを再生位置・状態に追従させる。"""

    def __init__(self, robot: Robot, slider: viser.GuiSliderHandle, button: viser.GuiButtonHandle):
        self.robot, self.slider, self.button = robot, slider, button
        # 手動シークや再開に使うため、直近に読み込んだ軌道を保持する
        self.times: list[float] = []
        self.angles_list: list[list[float]] = []
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # start フレーム目から末尾まで再生する（再生中の軌道は打ち切って乗り換える）
    def play(self, times: list[float], angles_list: list[list[float]], start: int = 0) -> None:
        self.stop()
        self.times, self.angles_list = times, angles_list
        self.slider.max, self.slider.disabled, self.button.disabled, self.button.label = len(times) - 1, False, False, "Stop"
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(times, angles_list, start, self._stop_event), daemon=True)
        self._thread.start()

    # 再生中なら打ち切り、その場の姿勢で止める
    def stop(self) -> None:
        self._stop_event.set()
        if self._thread: self._thread.join(timeout=1.0)
        self.button.label = "Play"

    def _run(self, times, angles_list, start, stop_event) -> None:
        # 軌道の時刻 t と実時間を合わせて各フレームを表示する（停止要求が来たら待ち中でも抜ける）
        t0, wall0 = times[start], time.monotonic()
        for i in range(start, len(times)):
            if stop_event.wait(max(0.0, times[i] - t0 - (time.monotonic() - wall0))): return
            self.robot.set_angles(angles_list[i])
            self.slider.value = i
        # 最後まで再生し終えたらボタンを Play に戻す
        self.button.label = "Play"
