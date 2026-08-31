# 11 · 预览缩放：视口放大镜，不是整帧放大

**结论：缩放必须"先 `CropAbs` 出视口能显示的源窗口，再 resize 到视口尺寸"。成本恒定在视口大小，与倍率无关。整帧放大到 100 倍在本捆绑包上实测是 7.37 GB/帧、9.3 秒取一帧——不可用。**

## 旧写法为何无效 🔬

直觉写法是"把整帧放大 N 倍，再让 UI 去显示其中一块"：

```python
# 反面写法：整帧放大
zoomed = core.resize.Point(clip, width=clip.width * 100, height=clip.height * 100)
```

本机实测（`core/vs_graph.py:153-157` 的注释即此结论的出处）：360×640 的显示帧放大 100 倍 → **38400×64000**，RGB24 单帧 **7.37 GB**，拉第 0 帧耗时 **9.3 秒**。而视口最多只能显示其中约 1000×1800 —— 算出来的像素 99.9% 当场丢弃。

这不是"慢一点"，是**量级错误**：帧内存随倍率平方增长，而可见区域恒定。`max_cache_size` 默认 4096 MB，单帧就已超出，缓存直接失效。

## 新写法为何有效 🔬

`core/vs_graph.py:158` `apply_preview_zoom(clip, *, zoom_factor, viewport, pan, kernel="Point", config=None)`：

```python
win_w = ceil(viewport_w / zoom_factor)      # 视口在这个倍率下能覆盖的源窗口
win_h = ceil(viewport_h / zoom_factor)
clip = core.std.CropAbs(clip, width=win_w, height=win_h, left=..., top=...)
return _resizer(core, kernel)(clip, width=viewport_w, height=viewport_h)
```

倍率越高，`win_*` 越小，`CropAbs` 切出的窗口越小 —— **进 resize 的像素量随倍率下降，出来的恒等于视口尺寸**。

本机实测（源 360×640，视口 1000×1800）：

```
   1.0x ->   360x640    原样返回:True    取帧 0.78 ms
   2.0x ->  1000x1800   原样返回:False   取帧 2.58 ms
  10.0x ->  1000x1800   原样返回:False   取帧 1.60 ms
 100.0x ->  1000x1800   原样返回:False   取帧 1.53 ms
```

倍率从 2x 涨到 100x，耗时**反而下降**（2.58 → 1.53 ms），因为切出的源窗口更小。对比整帧放大的 9.3 秒，差距约 **6000 倍**。

**返回值契约**（已验证）：`zoom_factor <= 1.0` 时**原样返回同一个 `clip` 对象**（不插入任何节点）；否则返回的 clip 宽高均 ≤ 视口。`pan` 取 `(0,0)`/`(1,1)`/`(0.5,0.5)` 在 100x 下均不越界。

## 三个设计细节及其理由

**核用 `Point`，不用 Bicubic**。10000% 缩放的用途就是**逐像素核对裁剪边界**，`Point`（最近邻）保持像素边缘硬朗，单个源像素放大后仍是可数的方块；平滑核会把要数的像素糊成渐变，直接毁掉这个功能的目的。这与导出链固定用 `Bicubic`（`cfg.resampler_kernel`）**不矛盾**——缩放是纯预览观察工具，不在导出图内。

**偶数对齐 `& ~1`**。`win_w`/`win_h` 与 `left`/`top` 都对齐到偶数。当前显示 clip 是 RGB24（不子采样），严格说不需要；但 `CropAbs` 对子采样格式有偶数约束（见 [03-geometry-filters.md](03-geometry-filters.md)），保持偶数使这段在将来改成在 YUV 上缩放时仍然合法。

**`pan` 是归一化中心**，`(0..1, 0..1)` 源坐标，默认 `(0.5, 0.5)` 居中。`left`/`top` 会被夹到 `[0, clip.width - win_w]`，所以平移到边缘不会越界。

## 与"预览=导出"的关系

缩放**不进导出图**。导出尺寸由 `get_resolution_spec()` 决定（360x640 等设备规格），缩放只作用在 `build_display_graph(...)` / `build_source_graph(...)` 产出的 RGB24 显示 clip **之后**。因此它对 `tests/test_vpy_golden.py` 和 `tests/test_preview_export_parity.py` 零影响 —— 这也是它可以自由选 `Point` 核的前提。

## 相关

- `core/vs_graph.py:153-202`（实现 + 实测数字的原始注释）
- [02-resize-semantics.md](02-resize-semantics.md) — resize 核与参数语义
- [03-geometry-filters.md](03-geometry-filters.md) — `CropAbs` 的偶数/子采样约束
- [07-frame-lifetime-threading.md](07-frame-lifetime-threading.md) — 帧取回与缓存行为
