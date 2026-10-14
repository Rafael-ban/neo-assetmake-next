# 17 · 默认滤镜参数与扩展插件

滤镜图仍由用户、项目或内置 `.vpy` 编写；本次不新增 GUI 滤镜面板，也不恢复
旧 `vsconfig.json` 图配置。设备输出尺寸、帧数、帧率和颜色合同仍来自 RenderJob。

## 内置处理链

`resources/vapoursynth/default_pipeline.vpy` 顶部集中缓存、未标记源的解释、
尺寸缩放核和设备格式转换参数。默认使用 Lanczos，尺寸缩放不传 `format=vs.YUV420P8`，
保留源格式和位深。修改 `FINAL_RESIZER` / `FINAL_RESIZE_OPTIONS` 可选择其他 resize 核。
缩放结束后单独执行设备所需的格式/颜色转换；`OUTPUT_CONVERSION_OPTIONS` 保留原有
`dither_type="error_diffusion"`，显示端的 RGB24 转换也保留该抖动设置。

顺序为：取源 → 补充未标记源的颜色信息 → 旋转 → 图片循环 → output 1 →
时间裁切 → 空间裁剪 → `process_source()` → 保留源格式的尺寸缩放 → 设备格式/色彩转换 → 补边/设备旋转 →
output 0。`process_source()` 默认原样返回，供按实际素材加入去噪、去色带等处理。
启用第三方调用时，同时在脚本头的 `assetmaker-requires` 中声明具体函数。

output 1 保留完整编辑画布与时间轴；output 0 固定为设备需要的 YUV420P8。
中间处理可以使用高位深或 float，但必须在输出前回到该合同。
不要把教程中的固定 1920×1080、10-bit 输出或 IVTC 帧率直接用于 compatible 脚本。

输入已有颜色标签时保留标签；缺少标签时，YUV 按原始源高度推断 709/170m，
RGB 图片默认 sRGB/709/full。默认图实际执行 matrix、transfer、primaries、range
转换，不仅重写最终标签。显示支路转换到 sRGB/709/full；适应窗口使用 Lanczos，
像素放大保留 Point。画面比较应在相同显示色彩空间中进行。
设备合同的 transfer `170m` 传入 resize 时使用 `601`，两者数值代码均为 6；
matrix 与 primaries 的名称仍为 `170m`。

## 可选 RIFE 插帧到 60 fps

`default_pipeline.vpy` 不包含 RIFE。需要插帧时选择独立的
`resources/vapoursynth/rife_pipeline.vpy`，默认目标为 60 fps；仅处理低于60 fps、
至少两帧的视频。图片、单帧和≥60 fps视频保持原帧率。
独立模板仍保留完整编辑画布、trim/crop、旋转、色彩和设备输出合同。

依赖使用 [VapourSynth-RIFE-ncnn-Vulkan](https://github.com/styler00dollar/VapourSynth-RIFE-ncnn-Vulkan)
的 `r9_mod_v33` DLL 和 `rife-v4.6_ensembleFalse` 模型。启用电脑需要 Vulkan GPU/驱动
及 [Microsoft VC++ v14 x64运行库](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)。
DLL导入包含VCOMP140.DLL和MSVC v14运行库；基础R79包不包含VCOMP140.DLL。
这些要求只适用于选用RIFE的电脑。

### 安装与打包（给开发者）

安装版和便携版用户请直接阅读 [RIFE DLC 中文安装说明](../rife-dlc-guide.md)。
完整 DLC 包含 DLL、模型、许可、独立脚本和清单。当前源码在选用 RIFE 脚本时自动加载
应用根下的 `tools/vs-rife/rife`，模型使用同目录的 `models/rife-v4.6_ensembleFalse`。
标准安装不再要求配置 `plugins.native_plugin_dirs` 或修改 `RIFE_MODEL_PATH`。
已发布的 v2.2.1 旧 EXE 仍使用旧加载逻辑，需要重新构建或升级主程序。

以下命令区分三个路径，不能混用：

| 路径 | 用途 |
| --- | --- |
| `$sourceRoot` | 包含 `main.py` 的源码根；运行源码时，依赖必须在这里 |
| `$portableRoot` | 已构建的 `ArknightsPassMaker.exe` 所在目录；运行 EXE 时，依赖必须在这里 |
| `$dlcOutput` | DLC ZIP 的保存位置；不是运行程序时的依赖目录 |

按你的实际位置修改变量。`--app-dir` 决定安装目标；`.` 只是当前 PowerShell 工作目录，
不会自动表示源码根。主程序基础构建只包含 R79 媒体树，明确排除可选 RIFE 和通用扩展。
因此“先装依赖再构建”也不会把 DLC 收入基础包；给产物目录补装也不会同步源码目录。

**从源码运行时安装或修复：**

```powershell
$sourceRoot = 'D:\Project_Folder\neo-assetmaker-dev'
Set-Location -LiteralPath $sourceRoot
uv run python plugin_distribution.py sync --app-dir "$sourceRoot" --manifest "$sourceRoot\resources\packaging\vs-rife.json"
uv run python plugin_distribution.py verify --app-dir "$sourceRoot" --manifest "$sourceRoot\resources\packaging\vs-rife.json"
```

这两条命令安装并校验 DLL 和模型。之后运行源码编辑器，按用户指南选择 RIFE 脚本即可。
`verify` 只确认文件完整性，不执行 GPU 推理。

**给编译后的程序部署：** 推荐把完整 DLC ZIP 解压到 `$portableRoot`，这样脚本、清单、
DLL、模型都会到位。只想用源码工具修复其中的插件文件时，仍在源码目录执行：

```powershell
$portableRoot = 'D:\Apps\ArknightsPassMaker'
uv run python plugin_distribution.py sync --app-dir "$portableRoot" --manifest "$sourceRoot\resources\packaging\vs-rife.json"
uv run python plugin_distribution.py verify --app-dir "$portableRoot" --manifest "$sourceRoot\resources\packaging\vs-rife.json"
```

`sync` 只部署清单管理的插件和模型，不会复制 `rife_pipeline.vpy`，也不会升级 EXE。
如果目标缺少脚本，应部署完整 DLC。旧版 EXE 缺少自动识别修复时，复制插件也不会改变加载逻辑。
上述命令不会修改已经生成的便携 ZIP；分发时应提供基础包和独立 DLC。

**制作 DLC：** 先完成源码根的 `sync`，再从同一根收集资源打包，不需要先编译主程序。

```powershell
$dlcOutput = "$sourceRoot\dist\RIFE-DLC-r9_mod_v33.zip"
uv run python rife_dlc.py --app-dir "$sourceRoot" --output "$dlcOutput"
```

打包会校验插件、模型，并加入独立脚本、清单和中文指南；不会打包主程序或基础媒体树。
此工具不覆盖已有 ZIP，重新制作时请使用新输出文件名。
GitHub Actions 的 **Build RIFE DLC** 也按“源码根安装资源 → 制作 DLC”的顺序执行。

清理时先切回内置脚本并退出编辑器。只清理你指定目录中的 RIFE 资源：

```powershell
uv run python plugin_distribution.py clean --app-dir "$sourceRoot" --manifest "$sourceRoot\resources\packaging\vs-rife.json"
```

`tools/vs-rife` 与通用扩展 `tools/vs-plugins` 分开管理，清理一组不会删除另一组。
旧教程添加过的 RIFE 本机路径可从用户配置移除；不应删除其他插件配置。

### 脚本行为与验证边界

独立模板默认 `RIFE_ENABLED=True`、`RIFE_TARGET_FPS=60`。插帧位于 output 1 和 trim 之前，
所以预览、编辑和导出使用同一条插帧时间轴，不存在独立的“仅导出补帧”模式。
帧数按 floor(源帧数 × 60 / 源帧率) 取整，尾部误差小于一个 60 fps 帧。
旧项目仅保存 trim 帧编号，切换脚本后不会自动换算时间，必须重新核对入出点。

模型定位基于 RenderSession 的实际应用根，与 `.vpy` 复制到哪个项目无关。
只有主动把模型放到其他位置的自定义脚本才需要修改 `RIFE_MODEL_PATH`。
脚本头声明 `rife.RIFE`，因此即使素材会跳过插帧、或 `RIFE_ENABLED=False`，也仍要求插件可用；
不使用 RIFE 时应选择内置脚本。GPU、模型或插件失败会报告错误，不会静默当作插帧成功。

尺寸缩放保留源格式和位深；设备输出前单独转换格式，仍保留 `error_diffusion` 和原有 x264 设置。
文件校验、打包成功和模拟测试不代表 Vulkan GPU 实际推理验收。

## 通用扩展的安装、修复与清理

通用扩展清单为 `resources/packaging/vs-plugins.json`，不是基础构建要求。
需要时运行：

```powershell
uv run python plugin_distribution.py sync --app-dir .
uv run python plugin_distribution.py verify --app-dir .
uv run python plugin_distribution.py clean --app-dir .
```

构建只校验R79基础媒体树，不校验或收集扩展；CI不再下载整套插件/模型。
安装后的插件目录、依赖和模型仍通过现有runtime配置与身份校验供worker/VSPipe共用。
无需改变现有R79下载变量，也不需要重新生成基础媒体工具包。

## 教程来源与验证边界

用户提供的 `vs_plugins.md`、`vs_plugins_dump.txt`、`vs_plugins_signatures.txt`
记录了另一套 R79 / Python 3.13 环境，但没有其完整版本锁。本项目从
[官方 VSRepo](https://github.com/vapoursynth/vsrepo) 的 Win64 包及
[mvutensils 上游](https://github.com/myrsloik/mvutensils) 重建相应接口库存。
现有 R79 ABI 可在项目 Python 3.12 上加载这些 native DLL，因此不为枚举插件而
更换宿主 Python。插件版本以本项目新清单为准，不能称为原环境的二进制副本。

2026-10-08 本地 R79 实际加载得到 63 个命名空间、308 个函数；逐项匹配教程的
62 个命名空间、306 个函数，无缺项，多出的两项为图片读写 `imwri.Read/Write`。
这个结果证明加载及接口存在，不证明每个参数组合、GPU 算子或模型的实际运算效果。
默认链不自动开启 GPU、AI、去隔行、降噪和去色带。

### 本次本地验证（2026-10-08）

- 最小测试首轮 15 项，8 通过、6 失败、1 错误；仅对未通过的 7 项复测一次，
  4 通过、3 失败。后续补齐 runner 的 DLL 搜索目录并调整旧测试的像素比较口径，
  按项目测试轮次限制没有继续运行 unittest，因此不能报告 15 项全通过。
- cx_Freeze 和本地便携 ZIP 构建成功。解压后的基础媒体 120 文件与扩展 140 文件
  均通过清单校验。此本地媒体验证包沿用开发版本号，不是新发布版本；未构建
  Rust 模拟器、刷机工具和 Inno 安装程序。
- 在解压目录实际启动 `vs_worker.exe`，请求显示帧，并与随包 VSPipe 比较第
  0/1/3 帧的 Y/U/V 平面摘要，全部一致。中文及空格路径图片经旋转、裁剪、循环
  编码后可回读，帧率 30 fps，VUI 为 matrix/transfer/primaries=6、Range=0。
- 以上不包含完整 GUI 操作、所有 GPU/AI 插件运算或目标设备人工验收。

已核实的教程修正：

- Bicubic 的 B=C=1/3 是 Mitchell–Netravali；Catmull–Rom 为 B=0、C=1/2。
  resize 参数名为 `filter_param_a` / `filter_param_b`，不是教程简写的 b/c。
  参见 [VapourSynth Resize](https://www.vapoursynth.com/doc/functions/video/resize.html)。
- `_FieldBased` 为 1=BFF、2=TFF，教程反写了场序。
  参见 [SetFieldBased](https://www.vapoursynth.com/doc/functions/video/setfieldbased.html)。
- BM3DCPU 的 `chroma=True` 需要 YUV444PS，不能仅将 YUV420 提升位深。
  参见 [BM3DCPU/BM3DCUDA 参数](https://github.com/WolframRhodium/VapourSynth-BM3DCUDA#parameters)。
- Yadifmod 的 `edeint` 必须与源同尺寸；`znedi3(dh=True)` 倍高的结果不能直接传入。
  参见 [Yadifmod](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-Yadifmod#usage)
  和 [znedi3](https://github.com/sekrit-twc/znedi3)。

不能仅凭插件名把任意 HDR、AI 或插帧链当作默认画质增强。此类脚本需明确源格式、
目标格式和时间轴语义，使用单个实际样本验证后再用于项目。
