# 17 · 默认滤镜参数与扩展插件

滤镜图仍由用户、项目或内置 `.vpy` 编写；本次不新增 GUI 滤镜面板，也不恢复
旧 `vsconfig.json` 图配置。设备输出尺寸、帧数、帧率和颜色合同仍来自 RenderJob。

## 内置处理链

`default_pipeline.vpy` 的 `FINAL_RESIZER` / `FINAL_RESIZE_OPTIONS` 只负责尺寸缩放，
不指定 YUV420P8。处理顺序为源 → 颜色解释 → 旋转 → 图片循环 → output 1 →
截取 → 裁剪 → `process_source()` → 缩放 → output 0。

RIFE 模板在旋转之后插入补帧和项目磁盘缓存，将该节点另存为 output 2；
output 1 与 output 0 共享它。脚本保留处理中的位深与颜色，最终设备颜色、
格式、补边和安装方向由 `assetmaker_vs.encoding` 在导出边界处理。
详见 [15 多路输出与编码边界](15-output-contract.md)。

输入已有颜色标签时保留；缺少标签时按源类型和原始高度补足解释。
显示支路转换到 sRGB/709/full。自定义脚本应保留真实颜色属性，不应伪造设备标签。

## 缓存配置与对应测试

缓存值分两层：程序先应用运行时配置，再执行所选脚本；脚本中的赋值会覆盖运行时初值。
本次只调整默认脚本，没有把 RIFE 或运行时配置一起改成 4096。

| 文件 | 当前值与作用 |
| --- | --- |
| `resources/vapoursynth/default_pipeline.vpy` | `VS_CACHE_SIZE_MB = 4096`，内置默认脚本执行时生效 |
| `resources/vapoursynth/rife_pipeline.vpy` | `VS_CACHE_SIZE_MB = 16000`，选用 RIFE 脚本时生效 |
| `config/vs_runtime.json`、`config/vs_runtime.py` | `max_cache_size_mb` 默认仍为 `0`，含义是恢复 VS 原生值；随后可被脚本覆盖 |
| `tests/test_default_pipeline_cache.py` | 验证默认脚本将运行时初值覆写为 4096 MB，并检查 preview worker 与 VSPipe 的实际执行结果 |
| `tests/test_default_pipeline_rife.py` | 检查 RIFE 插帧和输出顺序、模型定位及缺文件诊断 |
| `tests/test_rife_auto_discovery.py` | 检查独立 DLC 的自动加载、旧路径处理和运行时身份 |

4096 和 16000 都是 VapourSynth 缓存策略的 MB 值，不是编辑器的总内存上限。
导出任务持有的图片、预览缓存和插件内部内存需要各自按生命周期释放。

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
应用根下的 `tools/vs-rife/rife`。默认模型取实际加载的 `core.rife.plugin_path` DLL
同目录的 `models/rife-v4.6_ensembleFalse`，手动外置插件目录优先时模型也随 DLL 定位。
标准安装不再要求配置 `plugins.native_plugin_dirs` 或修改 `RIFE_MODEL_PATH`。
`v2.2.1` 标签曾重复发布，需以构建 commit 判断。已发布 run `38861133446`（`3fcff63`）
仍有运行配置与模型路径缺陷；当前修复尚未发布，需重建并部署配套 EXE 与 DLC。

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
$sourceRoot = 'D:\Project_Folder\neo-assetmake-next'
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

预览、导出与导出预检解析同一份有效 runtime 配置；导出预检传递完整 snapshot 环境。
有效手动 `native_plugin_dirs` 保持优先；不存在的 旧版 `tools/vs-rife/rife` 仅在本地 DLC
可用时忽略，其他手动配置不自动覆盖。默认模型随实际加载的 DLL 定位，与 `.vpy` 位置无关。
模型单独存放时，可在实际选用的 `.vpy` 中将 `RIFE_MODEL_PATH` 改为绝对模型目录。
脚本头声明 `rife.RIFE`，因此即使素材会跳过插帧、或 `RIFE_ENABLED=False`，也仍要求插件可用；
不使用 RIFE 时应选择内置脚本。GPU、模型或插件失败会报告错误，不会静默当作插帧成功。

尺寸缩放保留源格式和位深；导出适配层单独转换设备格式，保留 `error_diffusion` 和原有 x264 设置。
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

## 补帧结果磁盘缓存与资源回收

已保存项目在项目 `.assetmaker-vs/rife-cache/` 保存未压缩的 RGBS float32 帧和颜色属性，
避免每次命中都执行 gzip 解压。浮点位模式和帧属性保持不变；旧 `.rgb.gz` 帧在命中时
逐帧原子转换为 `.rgb`，成功后移除旧文件，转换失败时保留原缓存。
未压缩文件更大，同样预算下可保留的帧数减少；具体比例取决于素材。
这是按需缓存，不会导入时等待整片算完；再次访问已完成帧时不重新推理。
默认项目磁盘预算 8 GiB，由 RIFE 模板的 `RIFE_DISK_CACHE_BYTES` 设置。
缓存节点位于源分辨率的补帧结果上，在 trim/crop 之前；每帧约 `宽 × 高 × 12` 字节。
3840×2160 约 94.9 MiB/帧，8 GiB 约容纳 86 帧（60 fps 下约 1.4 秒，未计属性开销）。
高分辨率长循环可能因淘汰而反复推理，640×360 样本的命中效果不可直接外推。
容量不足会淘汰旧帧，之后访问时重算；它不是永久素材归档。

RIFE 的 VS 工作缓存保持 16000 MB，默认脚本保持 4096 MB。磁盘结果节点关闭重复的
VS 自动帧缓存；工作图仍需要运行内存和显存，不承诺播放时零内存占用。
预览暂停或静态显示空闲 3 秒后关闭计算进程，保留当前显示帧与编辑状态。
下次播放、seek 或截帧会按需恢复，首次恢复可能有初始化延迟。
导出开始前释放预览进程，结束后只有原本播放的预览自动恢复。

通过“操作 → 清除补帧缓存”暂停预览并清理当前项目缓存。清理失败会显示错误；
完成的帧可复用，未完成写入使用临时文件，重试不会把半帧当作有效结果。
普通预览 session 的 `.lwi` 索引不是补帧缓存，不要混淆。
未保存项目使用 `<EXE 或源码根>/.assetmaker-work/projects/project_<unique>/`，
程序根需要可写。Roaming 中的小型 `vs_runtime.user.json` 用户配置仍保留。

测试入口：`tests.test_rife_disk_cache`、`tests.test_preview_worker_lifecycle`、
`tests.test_export_preview_pause`。发布产物是否包含这些改动，应以构建 commit 判断；更新主程序逻辑需重新构建 EXE。
