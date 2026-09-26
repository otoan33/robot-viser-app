"""衝突回避（RMP など）用に、ロボットを球の集まりで近似し、障害物（球・直方体・カプセル）との距離とヤコビアンを求める。
座標はすべてアームの base_link（= viser のワールド）基準、単位は m。"""
import json
from pathlib import Path

import numpy as np
import viser.transforms as vtf
import yourdfpy


# 回転軸 axis まわりに q[rad] 回す 4x4 変換（ロドリゲスの公式）
def rotation(axis: np.ndarray, q: float) -> np.ndarray:
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    T = np.eye(4)
    T[:3, :3] = np.eye(3) + np.sin(q) * K + (1 - np.cos(q)) * K @ K
    return T


# roll / pitch / yaw [rad] から回転行列を作る（viser は整数の角度を受け付けないため float にする）
def rpy_matrix(rpy) -> np.ndarray:
    return vtf.SO3.from_rpy_radians(*np.asarray(rpy, dtype=float)).as_matrix()


# 位置 [m] と roll / pitch / yaw [rad] から 4x4 変換を作る
def pose(position, rpy) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = rpy_matrix(rpy), position
    return T


# URDF の関節を定義順（親→子）にたどり、各リンクの変換・各リンクより根元側にある可動関節の番号・可動関節の位置と回転軸を求める
# （yourdfpy の update_cfg はシーングラフの更新が重いため、自前で計算する）
def forward(urdf: yourdfpy.URDF, q: np.ndarray):
    T, ancestors, axes = {urdf.base_link: np.eye(4)}, {urdf.base_link: []}, {}
    names = urdf.actuated_joint_names
    for j in urdf.robot.joints:
        F = T[j.parent] @ j.origin
        if j.name in names:
            i = names.index(j.name)
            axes[i] = (F[:3, 3], F[:3, :3] @ j.axis)
            T[j.child], ancestors[j.child] = F @ rotation(j.axis, q[i]), ancestors[j.parent] + [i]
        else:
            T[j.child], ancestors[j.child] = F, ancestors[j.parent]
    return T, ancestors, axes


# リンク名 → [[x, y, z, r], ...]（リンク座標）の近似球の定義。ファイルがなければ球なしとして扱う
def load_spheres(folder: Path) -> dict:
    path = folder / "collision.json"
    return json.loads(path.read_text()) if path.exists() else {}


class CollisionModel:
    """アーム（＋ハンド）の近似球。ハンドの球はアームの取り付けリンクの座標に直して、アームのリンクに付いた球として扱う。"""

    def __init__(self, arm_dir: Path, arm: yourdfpy.URDF, hand_dir: Path | None = None, hand: yourdfpy.URDF | None = None):
        self.arm = arm
        # 各球の、表示用のリンク名・計算上の親（アームのリンク）・親リンク座標での中心・半径
        self.links, self.parents, centers, self.radii = [], [], [], []
        for link, spheres in load_spheres(arm_dir).items():
            for x, y, z, r in spheres:
                self.links.append(link); self.parents.append(link); centers.append([x, y, z]); self.radii.append(r)
        # ハンドは可動関節なしの想定。取り付けリンク → mount のオフセット → ハンドの各リンクの順に変換する
        if hand_dir:
            mount = json.loads((hand_dir / "mount.json").read_text())
            T_hand, _, _ = forward(hand, np.zeros(len(hand.actuated_joint_names)))
            for link, spheres in load_spheres(hand_dir).items():
                T = pose(mount["position"], mount["rpy"]) @ T_hand[link]
                for x, y, z, r in spheres:
                    self.links.append(link); self.parents.append(mount["link"]); centers.append((T @ [x, y, z, 1])[:3]); self.radii.append(r)
        self.centers, self.radii = np.array(centers).reshape(-1, 3), np.array(self.radii)

    # 関節角度 [deg] での全球の中心 (N,3) と、中心位置の関節角度に対するヤコビアン (N,3,関節数) [m/rad]
    def points(self, angles_deg) -> tuple[np.ndarray, np.ndarray]:
        q = np.deg2rad(np.asarray(angles_deg, dtype=float))
        T, ancestors, axes = forward(self.arm, q)
        P = np.array([T[p][:3, :3] @ c + T[p][:3, 3] for p, c in zip(self.parents, self.centers)]).reshape(-1, 3)
        # 回転関節 i による球中心の速度は a_i × (p - o_i)。球のリンクより根元側にある関節だけが効く
        O, A = np.array([axes[i][0] for i in range(len(q))]), np.array([axes[i][1] for i in range(len(q))])
        mask = np.array([[i in ancestors[p] for i in range(len(q))] for p in self.parents]).reshape(-1, len(q))
        J = (np.cross(A, P[:, None] - O) * mask[..., None]).transpose(0, 2, 1)
        return P, J


# 球（中心 P (N,3)・半径 r (N,)）と障害物の表面間の符号付き距離 (N,)（めり込むと負）と、
# 障害物から離れる向きの単位ベクトル (N,3)（= 球中心に対する距離の勾配）
def obstacle_distance(ob: dict, P: np.ndarray, r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    # 直方体: 箱の座標で符号付き距離を求める。外側は最近点からの向き、内側は最も浅い面の法線を使う
    if ob["type"] == "box":
        R = rpy_matrix(ob["rpy"])
        L = (P - ob["center"]) @ R
        q = np.abs(L) - np.asarray(ob["size"]) / 2
        out = np.maximum(q, 0)
        d_out = np.linalg.norm(out, axis=1)
        n_in = np.eye(3)[q.argmax(axis=1)] * np.where(L >= 0, 1.0, -1.0)
        n = np.where(d_out[:, None] > 0, np.sign(L) * out / np.maximum(d_out, 1e-12)[:, None], n_in)
        return np.where(d_out > 0, d_out, q.max(axis=1)) - r, n @ R.T
    # 球は中心、カプセルは線分上の最近点からの距離にする
    if ob["type"] == "sphere":
        C = np.asarray(ob["center"], dtype=float)
    else:
        a, b = np.asarray(ob["p1"], dtype=float), np.asarray(ob["p2"], dtype=float)
        t = np.clip((P - a) @ (b - a) / ((b - a) @ (b - a)), 0, 1)
        C = a + t[:, None] * (b - a)
    v = P - C
    dist = np.linalg.norm(v, axis=1)
    return dist - ob["radius"] - r, v / dist[:, None]


# 関節角度 [deg] での各制御点（球）の情報と、全ての球×障害物の距離を返す（max_distance 指定時はそれ以下のペアのみ）
def compute_distances(model: CollisionModel, obstacles: list[dict], angles_deg, max_distance: float | None = None) -> dict:
    P, J = model.points(angles_deg)
    points = [{"link": l, "position": p.tolist(), "radius": float(r), "jacobian": j.tolist()} for l, p, r, j in zip(model.links, P, model.radii, J)]
    pairs, ds = [], []
    for ob in obstacles:
        d, n = obstacle_distance(ob, P, model.radii)
        ds.append(d)
        pairs += [{"point": k, "obstacle": ob["name"], "distance": float(d[k]), "normal": n[k].tolist()} for k in range(len(P)) if max_distance is None or d[k] <= max_distance]
    # 障害物か球がないときは null
    min_distance = float(np.concatenate(ds).min()) if ds and len(P) else None
    return {"min_distance": min_distance, "control_points": points, "pairs": pairs}
