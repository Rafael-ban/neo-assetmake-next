# 02 · `resize` 语义：输入解释、输出转换与帧属性

**结论：`*_in` 参数解释输入，`matrix_s`/`range_s` 等参数决定输出转换；输出
frame props 是对结果的元数据描述。写属性不等于转换像素，做了像素转换也不保证
编码器会自动写出相同标签。**

## R73 历史语义与探针

以下来源和像素样本保留作历史对照，不代表 R79 实测；当前 R79 的 Range
合同见 [01](01-colour-range-props.md)，部署及验收范围见 [08](08-version-upgrade-notes.md)。

固定 tag：
`https://github.com/vapoursynth/vapoursynth/blob/R73/doc/functions/video/resize.rst`。

- 转到 YUV 时必须指定输出 matrix；项目通过 `matrix_s` 明确给出。
- `matrix_in`、`transfer_in`、`primaries_in`、`range_in` 的 `_in` 形式只在对应
  输入 frame prop 未设置时作为兜底；已有输入属性优先。
- Bicubic 的 `filter_param_a`/`filter_param_b` 分别是 b/c。
- 当前默认链已改为 Spline36，并显式选择 `dither_type="error_diffusion"`；
  不应跨不同缩放几何要求逐字节相等。

R73 探针用 `Y=16` 的 YUV420P8 单帧验证了输入优先级：

```text
_ColorRange=0 + range_in_s=limited -> RGB R=16
_ColorRange=1 + range_in_s=full    -> RGB R=0
```

冲突时 frame prop 获胜。因此 `*_in` 不是“强制覆盖源标签”的开关；若源标签错误，
必须先有意识地修正属性或删除错误属性，再转换，而不是假设传参必然覆盖。

## 当前默认脚本的真实顺序

内置脚本在取源后补足缺失的颜色解释，在 trim/crop 与 `process_source()` 后只缩放尺寸：

```python
clip = FINAL_RESIZER(
    clip,
    width=output["display_width"],
    height=output["display_height"],
    **FINAL_RESIZE_OPTIONS,
)
clip.set_output(0)
```

`FINAL_RESIZER` 默认 Lanczos，不指定 format，保留处理中的格式和位深。
RIFE 处理结果可以保持 RGBS；显示支路按真实源颜色转到显示色彩空间。

导出模块的 `assetmaker_vs.encoding.prepare_encoding_clip()` 单独负责当前设备的
YUV420P8、170m/limited 转换及设备方向，使用 error_diffusion。它不替脚本缩放尺寸。
VSPipe Y4M 和 x264 使用这个编码适配结果。设备 transfer 名称 `170m` 在 resize
中使用 `601`，两者对应数值代码 6。

## 受保护的输出合同

下表约束导出适配层的最终编码结果，不限制脚本中间处理格式；profile 来自 `assetmaker_vs.job_api`：

| profile | 内容画布 | 编码画布 | format | matrix/range |
|---|---:|---:|---|---|
| `360x640` | 360×640 | 360×640 | YUV420P8 | 170m / limited |
| `720x1280` | 720×1280 | 720×1280 | YUV420P8 | 170m / limited |
| `800x1280` | 800×1280 | 800×1280 | YUV420P8 | 170m / limited |

修改 kernel、Bicubic b/c、dither、输出格式或色彩参数会改变实际像素或编码合同，
必须同时验证默认脚本、output contract、worker/VSPipe parity 和真实导出回读；不能
只改文档或一端参数。

## 相关

- [01 色彩范围](01-colour-range-props.md) — 当前 R79 Range 合同与旧键兼容
- [03 几何](03-geometry-filters.md) — crop、resize、AddBorders 的实际顺序
- [15 输出契约](15-output-contract.md) — output 0 的几何/色彩验收
