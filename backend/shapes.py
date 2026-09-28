"""viser に重ねて描く補助図形（点・線・球・円柱・直方体）。JSON・CSV・画面のフォームで同じフラットな形（CSV の 1 行）として扱う。
座標はアームの base_link（= viser のワールド）基準 [m]、姿勢は roll / pitch / yaw [deg]。"""
import csv
import io
import threading
from typing import Literal

import numpy as np
import trimesh
import viser
import viser.transforms as vtf
from pydantic import BaseModel, Field, model_validator

# size の意味は種類ごとに異なる（点: 大きさ[m]、線: 太さ[px]、球・円柱: 半径[m]、直方体: 使わない）。省略時はこの値にする
DEFAULT_SIZE = {"point": 0.03, "line": 3.0, "sphere": 0.05, "cylinder": 0.03, "box": 0.0}


class Shape(BaseModel):
    name: str = ""
    type: Literal["point", "line", "sphere", "cylinder", "box"]
    visible: bool = True
    # 点・球・直方体の中心、線・円柱の始点
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    # 線・円柱の終点
    x2: float = 0.0
    y2: float = 0.0
    z2: float = 0.0
    # 直方体の辺の長さと姿勢
    sx: float = 0.1
    sy: float = 0.1
    sz: float = 0.1
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    size: float | None = None
    color: str = Field("#808080", pattern=r"^#[0-9a-fA-F]{6}$")
    # 不透明度（点・線は viser が対応していないので使わない）
    opacity: float = Field(1.0, ge=0.0, le=1.0)

    # 種類を変えたときや CSV で空欄のときは、その種類で見える大きさにする
    @model_validator(mode="after")
    def _default_size(self):
        if self.size is None: self.size = DEFAULT_SIZE[self.type]
        return self


COLUMNS = list(Shape.model_fields)


# CSV（ヘッダ行 + 1 行 1 図形）を図形のリストにする。空欄の列は既定値にする
def load_shapes_csv(text: str) -> list[dict]:
    return [Shape(**{k: v for k, v in row.items() if v}).model_dump() for row in csv.DictReader(io.StringIO(text))]


# 図形のリストを、読み込みと同じ列の CSV にする
def dump_shapes_csv(shapes: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(shapes)
    return buf.getvalue()


# 画面での連続編集で API が同時に呼ばれても、消す→描くが混ざらないよう排他する
_lock = threading.Lock()


# 図形をすべて消し、表示する図形を /shapes/<番号> に描き直す
def draw_shapes(server: viser.ViserServer, shapes: list[dict]) -> None:
    with _lock:
        server.scene.remove_by_name("/shapes")
        server.scene.add_frame("/shapes", show_axes=False)
        for i, s in enumerate(shapes):
            if not s["visible"]: continue
            name, color, size = f"/shapes/{i}", tuple(int(s["color"][k:k + 2], 16) for k in (1, 3, 5)), s["size"]
            p1, p2 = np.array([s["x"], s["y"], s["z"]], dtype=float), np.array([s["x2"], s["y2"], s["z2"]], dtype=float)
            opacity = s["opacity"] if s["opacity"] < 1 else None
            if s["type"] == "point":
                server.scene.add_point_cloud(name, points=p1[None], colors=color, point_size=size, point_shape="circle")
            elif s["type"] == "line":
                server.scene.add_line_segments(name, points=np.array([[p1, p2]]), colors=color, thickness=size, thickness_units="screen")
            elif s["type"] == "sphere":
                server.scene.add_icosphere(name, radius=size, color=color, opacity=opacity, position=p1)
            elif s["type"] == "cylinder":
                # viser の円柱は原点中心で Z 軸方向に伸びるので、Z 軸を始点→終点の向きに回して中点に置く（両端が同じ点なら向きはそのまま）
                d = p2 - p1
                wxyz = vtf.SO3.from_matrix(trimesh.geometry.align_vectors([0, 0, 1], d if d.any() else [0, 0, 1])[:3, :3]).wxyz
                server.scene.add_cylinder(name, radius=size, height=np.linalg.norm(d), color=color, opacity=opacity, wxyz=wxyz, position=(p1 + p2) / 2)
            else:
                wxyz = vtf.SO3.from_rpy_radians(*np.deg2rad([s["roll"], s["pitch"], s["yaw"]])).wxyz
                server.scene.add_box(name, color=color, dimensions=(s["sx"], s["sy"], s["sz"]), opacity=opacity, wxyz=wxyz, position=p1)
