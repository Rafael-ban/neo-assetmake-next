# 02 · `resize` 语义：兜底参数、帧属性优先、Bicubic 系数

**结论：`matrix_in`/`transfer_in`/`primaries_in`/`range_in` 都是"帧属性缺失时的兜底"，帧属性一旦有值就覆盖参数。所以要强制色彩解释，必须写帧属性，不能只传参数。**

## 官方原文 ✅ 已核实

`http://www.vapoursynth.com/doc/functions/video/resize.html`：

> **When converting to YUV colorspaces, the `matrix` … must be specified.** … The `_in` versions of the arguments are **only used as a fallback when the corresponding frame property is not set**.

> Note that `dither_type="error_diffusion"` is not deterministic …

> `filter_param_a`, `filter_param_b` … For `bicubic`, `filter_param_a`/`filter_param_b` correspond to **"b" and "c"** … For `lanczos`, `filter_param_a` is the number of taps.

## "帧属性覆盖参数"已运行时坐实 🔬

用 `Y=16`（limited 黑点）的 YUV420P8 单帧转 RGB24，让**帧属性与参数互相矛盾**，看谁赢：

```
_ColorRange=0 (full)    + range_in_s=limited -> R= 16   # 帧属性赢
_ColorRange=1 (limited) + range_in_s=full    -> R=  0   # 帧属性赢
```

两次都是帧属性说了算，参数被忽略。**所以要强制色彩解释只能写帧属性**——传 `range_in_s`/`matrix_in_s` 在源已带标记时是无效操作。复现见 [01-colour-range-props.md](01-colour-range-props.md)。

## 本项目的做法为何正确

`core/vs_graph.py` 的色彩段（`core/vs_script.py` 生成等价 `.vpy`）：

```python
if clip.get_frame(0).props.get("_Matrix", 2) == 2:
    clip = core.std.SetFrameProps(clip, _Matrix=...)   # 先写属性
clip = resizer(clip, width=..., height=..., format=out_fmt, matrix_s=cfg.matrix_s)
```

- 先检查 `_Matrix == 2`（unspecified），**只有缺失时**才 `SetFrameProps` 补标 —— 与"`_in` 仅作兜底"的语义一致：不覆盖源已有的正确标记。
- 输出侧用 `matrix_s`（**非** `matrix_in_s`）指定目标矩阵，因为这是 RGB→YUV 方向，官方明确要求"converting to YUV … must be specified"。

**旧写法为何无效**（这是项目历史上真实修过的 bug，见 `tests/test_export_color_roundtrip.py` 顶部注释）：用 `matrix_s='709'` 转换、而 H.264 流不带色彩标签。未打标的 sub-HD 内容按惯例（H.273）被解码为 BT.601，于是导出颜色与预览可见偏移。**新写法**：以 `'170m'` 转换 + x264 `--colormatrix/--colorprim/--transfer smpte170m --range tv` 打标，两端一致。

## 不要碰的项

`config/vsconfig.json` 固定：`matrix_s='170m'`、`output_format='YUV420P8'`、`resampler_kernel='Bicubic'`、`image_source_format='RGB24'`、`MatrixHeuristic(720/1/6)`。`tests/test_export_color_roundtrip.py` 就是为钉死它而存在。

## 可选深化（未采用，记录理由）

- `dither_type`：项目未显式设置。文档警告 `error_diffusion` 不确定性 —— 这会破坏 `tests/test_vs_graph_player.py::GraphParityTests` 的**逐字节**比对。**若将来要设，只能设确定性算法**（如 `ordered`/`none`），且必须同步改 `.vpy` 生成器并重抓 golden。
- `filter_param_a/b`：Bicubic 的 b/c 未显式设置，用 VS 默认。改动会改变所有输出像素 → golden 全部漂移。

## 相关

- [01-colour-range-props.md](01-colour-range-props.md) — `range_in` 对应的帧属性及其反转陷阱
- [03-geometry-filters.md](03-geometry-filters.md) — resize 前后的裁剪/补边约束
