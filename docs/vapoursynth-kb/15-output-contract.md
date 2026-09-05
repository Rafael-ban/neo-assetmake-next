# 15 — output 0/1 与设备编码契约

## 范围与边界

用户可以自由构图，但不是任意输出都能编码为设备素材。runner 在脚本运行后调用
`assetmaker_vs.contract.validate_outputs()`；只有校验通过的 `output 0` 才会进入
VSPipe → x264 → muxer。

## 两个输出

| 输出 | 谁使用 | 规则 |
|---|---|---|
| `output 0` | 预览的导出视图与 VSPipe 导出 | 必需；设备编码帧 |
| `output 1` | compatible 编辑器画布 | 仅脚本头声明 `assetmaker-editor-output: 1` 时必需；应为旋转后、trim/crop 前完整时间轴 |

因此 `clip.set_output()`（默认 output 0）不是装饰性尾句，而是 VS 图对宿主的明确出口。
官方 Python API 通过 `set_output()`/`get_output()` 管理输出；本机接口源码
`tools/media/cython/vapoursynth.pyx` 的 `get_output` 与 `VideoOutputTuple` 包装也证明
runner 只能从已注册输出取得 clip。

## output 0 的固定条件

输出须与 RenderJob 的设备规格一致，包括：

- coded 宽高与目标帧率；显示宽高不足的部分由脚本补边；
- `YUV420P8`，以及 4:2:0 所要求的偶数几何；
- `_Matrix`、`_Transfer`、`_Primaries` 与 `_ColorRange` 帧属性；
- x264 VUI 与上述颜色属性一致；
- 非空 clip，且导出帧数与 job 的 trim 一致。

这解释了为什么“滤镜正常显示”仍可能导出失败：例如输出 RGB、10-bit、奇数 YUV 裁剪
或没有设定颜色属性，都不满足设备和 x264 的联合契约。直接在 Python 宿主里再补一个
resize 来“修好”它是无效的，因为那会让预览与导出看到不同图；修复必须写回 `.vpy`。

## 颜色与 resize

本项目的内置设备目标使用 `170m`、limited range、YUV420P8。无标签视频的矩阵启发式
以**裁剪前源高度**判断，而不是裁剪后的高度；否则拖动裁剪框就会改变同一源的颜色。
`tests/test_default_vpy_pipeline.py::test_true_709_untagged_source_does_not_flip_at_p7_crop_height`
用真实 VS 图锁定了这个行为。

官方 [resize 文档](https://www.vapoursynth.com/doc/functions/video/resize.html) 说明矩阵和
范围参数属于 resize 转换的一部分；本项目具体的代码值、VUI 映射与错误信息见
`assetmaker_vs/contract.py`、`core/vs_runtime/output_contract.py` 和知识库 01/02。

## 验证

```powershell
uv run python -m unittest tests.test_vs_output_contract tests.test_preview_export_parity tests.test_export_color_roundtrip -v
```
