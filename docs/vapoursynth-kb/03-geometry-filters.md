# 03 · 几何滤镜：Transpose 不是旋转，Crop/AddBorders 受子采样约束

**结论：`Transpose` 是矩阵转置（一次反射），单独用会得到镜像。旋转必须 Transpose+Flip 组合。裁剪与补边都必须满足子采样约束，违反即报错（不是静默取整）。**

## 官方原文 ✅ 已核实

`http://www.vapoursynth.com/doc/functions/video/transpose.html`：

> Flips the contents of the frames in the same way as a **matrix transpose** would do. **Combine it with FlipVertical or FlipHorizontal to synthesize a left or right rotation.** Calling Transpose twice in a row is the same as doing nothing (but slower).

`http://www.vapoursynth.com/doc/functions/video/crop_cropabs.html`：

> `CropAbs`, on the other hand, is special, because it can accept clips with variable frame sizes and crop out a fixed size area, thus making it a fixed size clip.
> Both functions return an error if the whole picture is cropped away, if the cropped area extends beyond the input **or if the subsampling restrictions aren't met**.

`http://www.vapoursynth.com/doc/functions/video/addborders.html`：

> Adds borders to frames. The arguments specify the number of pixels to add on each side. **They must obey the subsampling restrictions.** The newly added borders will be set to `color`.

## 本项目的做法为何正确

**旋转**（`core/vs_graph.py` / `core/vs_script.py`，两处必须一致）：

```python
# 90°  = 右旋
clip = core.std.FlipHorizontal(core.std.Transpose(clip))
# 180°
clip = core.std.Turn180(clip)
# 270° = 左旋
clip = core.std.FlipVertical(core.std.Transpose(clip))
```

旧写法只调 `Transpose` → 画面**镜像**而非旋转（项目历史 bug M1a）。官方原文的 "Combine it with FlipVertical or FlipHorizontal to synthesize a … rotation" 是直接依据。与 cv2 的 `ROTATE_90_CLOCKWISE` 行为对齐，所以预览（曾用 cv2）与导出（VS）方向一致。

**裁剪偶数对齐**（M1b）：`YUV420P8` 水平垂直都 2:1 子采样，裁剪原点与尺寸都必须偶数。项目用 `& ~1` 对齐，并用共享 `scale = min(cw/crop_w, ch/crop_h)` 按目标比例**等比收缩**——而不是单轴夹取（单轴夹取会让 resize 变成各向异性拉伸）。仅当 `cw >= 2 and ch >= 2` 才调 `CropAbs`。

**补边黑色**（M1c）：`AddBorders` 的 `color` 默认是 `<black>`，但在 YUV 里"全 0"不是黑（chroma 中点是 128），所以必须显式给出对应格式的黑色，否则得到绿边。

**用 `CropAbs` 而非 `Crop`**：本项目裁剪框是绝对坐标（x, y, w, h），`CropAbs` 直接接受"固定区域"语义；`Crop` 要换算成四边裁掉多少，多一层易错换算。

## 相关

- [02-resize-semantics.md](02-resize-semantics.md) — 顺序：裁剪在 resize 前，补边在 resize 后
- [04-trim-loop-zero-length.md](04-trim-loop-zero-length.md) — 裁剪到 0 会报错，与 0 帧 clip 同类约束
- `core/vs_graph.py` 与 `core/vs_script.py` 的**步序是载重的**：source → trim → rotation → crop → loop-if-image → colour → padding → final 180°
