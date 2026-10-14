# 15 — 多路处理输出与编码边界

## 脚本输出的职责

| 输出 | 用途 | 内容 |
| --- | --- | --- |
| output 0 | final 预览、导出的处理输入 | 已截取、裁剪、滤镜和缩放的画面；保留处理格式与真实颜色属性 |
| output 1 | compatible 编辑画布 | 旋转和补帧之后、截取和裁剪之前的完整时间轴 |
| output 2 | 内置 RIFE 的补帧结果节点 | 项目磁盘缓存支路；与 output 1/0 共用上游，不建立第二套推理图 |

output 0 必需，output 1 由 `assetmaker-editor-output: 1` 声明。
output 2 是 RIFE 模板的额外输出，普通自定义脚本不必提供。
raw 模式仍由用户自行构图，只需 output 0。

## 处理输入与编码输出是两层合同

`validate_script_outputs()` 检查固定的 RGB/YUV/GRAY 格式、display 尺寸、正帧数和帧率，
以及已解析的 timeline。脚本保留真实的 matrix/transfer/primaries/range；不能只重写
目标颜色标签而不转换像素。宿主不替脚本补尺寸缩放。

`validate_encoding_outputs()` 在导出预检和 VSPipe 中使用同一个
`assetmaker_vs.encoding.prepare_encoding_clip()`：

1. 将处理结果转换为当前设备所需的 YUV420P8、170m、limited，保留 error_diffusion。
2. 根据 profile 补齐 coded 尺寸并应用设备安装方向。
3. 用严格合同验证编码帧，生成 x264 VUI，再逐帧 guard。

这是导出模块的格式适配，不是滤镜脚本中的强制格式归一化。现有传输为
VSPipe Y4M → x264；Y4M 不能直接承载 RGB 浮点，因此不能只删掉转换而保持其他链路不变。
`--output-csp i420` 指定最终编码格式，不能代替上游 RGBS 的传输适配和正确颜色转换。

| profile | 脚本 display 尺寸 | 当前 coded 尺寸 |
| --- | --- | --- |
| 360x640 | 360×640 | 360×640 |
| 720x1280 | 720×1280 | 720×1280 |
| 800x1280 | 800×1280 | 800×1280 |

不再补右侧 24 像素。旧自定义脚本若自行补边或应用最终设备旋转，应移除这些步骤，
避免与导出层重复。已有固定格式 YUV420P8 脚本仍可作为处理输入。

## 预览与验证

普通 worker 的 final surface 是脚本处理结果；editor surface 是 output 1。
导出预检显式传 `for_export=True`，VSPipe runner 注册适配后的编码 clip 为 output 0。
因此显示预览保留源颜色解释，设备转换后的像素由编码预检与真实导出 parity 验证。

严格编码合同仍要求真实 FrameProps 的 `_Range`，不接受物理旧 `_ColorRange` 键；
sentinel 和逐帧 guard 保留。`validate_outputs()` 是严格编码结果校验入口，
不是普通脚本结果的直接入口。

对应测试：`tests.test_vs_encoding_boundary`、`tests.test_vs_output_contract`、
`tests.test_default_vpy_pipeline`、`tests.test_preview_export_parity`。
