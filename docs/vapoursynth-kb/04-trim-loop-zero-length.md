# 04 · Trim / Loop / 零长度 clip

**结论：VapourSynth 明令禁止 0 帧 clip。`clip[a:a]` 是空 clip 会直接报错，所以时间轴退化时必须至少留一帧。`Loop(times<1)` 不是"循环一次"，是"重复到最大长度"。**

## 官方原文 ✅ 已核实

`http://www.vapoursynth.com/doc/functions/video/trim.html`：

> Trim is **inclusive** so `Trim(clip, first=3, last=3)` will return one frame. If neither `last` nor `length` is specified, no frames are removed from the end of the clip.
> Specifying both `last` and `length` is considered to be an error.
> Likewise is calling Trim in a way that returns no frames, as **0 frame clips are not allowed in VapourSynth**.
> In Python, `std.Trim` can also be invoked by **slicing a clip**.

`http://www.vapoursynth.com/doc/functions/video/loop.html`：

> Returns a clip with the frames or samples repeated over and over again. **If `times` is less than 1 the clip will be repeated until the maximum clip length is reached**, otherwise it will be repeated `times` times.
> In Python, `std.Loop` can also be invoked using the **multiplication operator**.

## 本项目的做法为何正确

**零长度护栏**（`core/vs_graph.py`，`core/vs_script.py` 等价）：

```python
end_frame = max(start_frame + 1, int(params.end_frame))
clip = clip[start_frame:end_frame]
```

`clip[a:a]` 在 Python 切片语义下是 0 帧 → 官方明确"not allowed" → 抛错。用户把入点拖到等于出点（或工程文件里两者相同）时，旧写法直接崩在导出中途；护栏保证至少一帧。

⚠️ **注意切片与 `Trim` 的边界差异**：`Trim(first=3, last=3)` 返回**一帧**（inclusive），而切片 `clip[3:3]` 返回**零帧**（Python 半开区间）。项目用切片，所以 `end_frame` 是**排他**上界 —— 读代码时不要按 `Trim` 的 inclusive 直觉去核对帧数。

**图片循环**（M1f）：

```python
clip = core.std.Loop(clip, times=max(1, end_frame - start_frame))
```

`max(1, ...)` 是必需的：若时长算出 0 或负数，`times<1` 会按官方语义"重复到最大 clip 长度"——那是个天文数字的帧数，会让导出看起来像挂死而不是报错。

**Loop 在裁剪/旋转之后**：单帧图片先做几何变换再复制，比复制后对每帧重复做要省一个数量级。步序在 `vs_graph.py` 与 `vs_script.py` 里必须一致（parity 测试逐字节比对）。

## 相关

- [03-geometry-filters.md](03-geometry-filters.md) — 裁剪到 0 尺寸同样是报错而非静默
- `tests/test_vs_graph_player.py::GraphParityTests` — 两条构图链的逐字节一致性
