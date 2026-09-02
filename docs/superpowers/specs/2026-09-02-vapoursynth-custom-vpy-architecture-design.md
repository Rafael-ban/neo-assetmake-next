# VapourSynth 完全自定义 `.vpy` 架构设计

状态：架构已获用户批准，实施计划待用户审核
日期：2026-09-02

## 1. 背景与目标

当前应用已经移除 mpv，并以 VapourSynth 作为预览和导出的渲染核心，但仍同时维护两种滤镜图：

- `core/vs_graph.py` 在应用进程内用 Python API 构建预览图；
- `core/vs_script.py` 通过字符串拼接生成供 VSPipe 执行的 `.vpy`；
- `config/vsconfig.json` 用项目自定义 JSON 字段描述部分滤镜、色彩和格式策略；
- `core/media_pipeline.py` 又独立固定 x264 像素格式及色彩 VUI。

这套结构能通过 parity/golden 测试检测两边是否漂移，却不能消除双重实现本身。新增或修改一个滤镜时，开发者仍需同时修改预览图、脚本生成器、配置模型、测试 golden 和编码参数。近期原始源高度启发式的修复导致多个 `.vpy` golden 同时变化，就是该耦合的直接表现。

本次重构的最终目标是：

> 用户 `.vpy` 是唯一滤镜事实源；应用只负责作业数据、脚本执行、帧传输、信任控制和最终输出验证。预览与导出执行同一份脚本和同一份作业数据。

## 2. 依据与已证实问题

### 2.1 官方脚本模型

VapourSynth 官方入门文档以 `.vpy` 为标准入口：导入 `vapoursynth`、获取 `core`、创建源、调用滤镜，最终通过 `clip.set_output()` 注册输出。官方并没有用独立 JSON 描述滤镜调用的机制。

- [VapourSynth Getting Started](https://www.vapoursynth.com/doc/gettingstarted.html)
- [VapourSynth Python Reference](https://www.vapoursynth.com/doc/pythonreference.html)

VSPipe 的职责是执行 `.vpy` 并读取输出节点；`--arg key=value` 可向脚本注入字符串变量。因此，本设计只用 `--arg` 传递一个作业文件路径，不把几十个滤镜参数展开为字符串，也不拼接脚本源码。

- [VapourSynth Output with VSPipe](https://www.vapoursynth.com/doc/output.html)

VCB-Studio 教程同样采用“加载源 → 命名中间 clip → 调用滤镜 → 输出”的普通脚本组织方式。本项目借鉴其可读性和模块组织经验，但不会复制已过时的 API3 写法、`vs.get_core()` 或特定第三方插件假设。

- [VCB-Studio 教程](https://guides.vcb-s.com/)
- [VCB-Studio-guides 源码](https://github.com/TKMYing/VCB-Studio-guides)

### 2.2 当前仍存在的问题

已由 `429bd68` 修复的问题——单字段错误导致整份配置丢失、非法 kernel 静默回退——不属于本次待修范围。剩余问题如下：

1. **`output_format` 是伪配置。** `config/vsconfig.py::_coerce_format()` 只验证字符串能否成为 Python 标识符；现有测试允许 `YUV422P10`，而 x264 命令固定 `--output-csp i420`。
2. **色彩契约可能分裂。** `.vpy` 使用可配置 `matrix_s`，x264 VUI 固定为 SMPTE 170M/tv，显示转换也曾拥有独立默认值。
3. **预览与导出维护两张图。** `vs_graph.py` 与 `vs_script.py` 重复实现 trim、旋转、裁剪、图片循环、缩放、色彩转换、补边和最终 180° 旋转。
4. **一个 `resampler_kernel` 承担多种语义。** 几何缩放、图片格式转换和 YUV 色度升采样并不必然适合相同核。官方 Resize API 已提供 `resample_filter_uv`、`filter_param_a/b`、UV 参数、源矩形及输入/输出色彩属性，证明这些是可独立选择的处理阶段。[Resize 官方文档](https://www.vapoursynth.com/doc/functions/video/resize.html)
5. **插件检查只到 namespace。** 当前能检查 `lsmas`，但不能证明 `lsmas.LWLibavSource` 存在或签名兼容。
6. **插件目录语义混合。** 原生 DLL 自动加载目录与 Python 模块搜索路径是两套机制。[VapourSynth Installation](https://www.vapoursynth.com/doc/installation.html)
7. **schema 未参与运行时加载。** 当前源码注释明确说明 `schemas/vsconfig.schema.json` 只由测试检查。
8. **主进程 VS 初始化有顺序陷阱。** 当前 bundle 必须在 PyQt6 前预热 VS core，否则可能 exit 139。
9. **RGB24 像素缩放仍有无用偶数限制。** 实测 RGB24 `CropAbs` 接受奇数尺寸与偏移；强制向下取偶会改变实际倍率和中心。
10. **测试仍含已废弃的 `480x854` 规格。** 调查基线中 237 项测试有 3 项因此失败，需要在架构迁移前恢复绿色基线。

### 2.3 已完成运行时探针

使用项目携带的 R73 VSPipe 验证：

- 用户 `.vpy` 直接导入相邻 `shared_pipeline.py` 时出现 `ModuleNotFoundError`；
- 仅设置子进程 `PYTHONPATH` 在当前便携 Python 中仍未按预期导入；
- 可信启动器显式将用户脚本目录加入 `sys.path` 后，相邻模块导入成功；
- 同一探针通过 `--arg width=32` 成功注入参数并输出 32×16 RGB24 帧。

因此，旧设想“直接把用户脚本交给 VSPipe，并依赖工作目录或 `PYTHONPATH`”对当前 bundle 无效；固定执行启动器是经过真实 VSPipe 验证的有效方案。

## 3. 已确认的设计决策

1. 用户完全自定义 `.vpy`，应用不生成滤镜图。
2. 同时支持全局脚本和项目脚本；项目脚本必须显式信任。
3. 项目脚本按 bundle SHA-256 记录信任，内容变化后重新确认；信任记录只保存在本机。
4. 预览在独立常驻 VS 工作进程执行，避免脚本或插件直接拖垮 Qt 主进程。
5. 应用通过单一、不可变 `job.json` 向脚本提供素材编辑数据。
6. output 0 使用严格契约；应用不在用户脚本后隐藏追加修正滤镜。
7. 提供“兼容模式”和“原始脚本模式”：兼容模式保留 GUI 编辑能力，原始模式允许完全接管管线。
8. 默认兼容模板使用普通、可读、可编辑的 VapourSynth/Python 语法。

## 4. 目标架构

```text
Qt 素材编辑器
    │
    ├── 项目/编辑状态
    ├── 原子生成 job-<epoch>.json
    ├── 验证脚本信任与 bundle 哈希
    └── 启动/控制独立 VS Worker
             │
             ├── 可信执行启动器
             ├── 用户 pipeline.vpy（唯一滤镜图）
             ├── output 0 严格验证
             └── 私有 RGB 显示支路 → 共享内存 → Qt

同一 pipeline.vpy + 同一 job.json
    └── VSPipe → x264-7mod → MP4Box/lsmash → MP4
```

预览与导出的差异仅是消费方式：预览按需请求第 N 帧，导出顺序消费全部帧。两者不再使用两套滤镜实现。

## 5. 文件和配置边界

### 5.1 用户 `.vpy`

项目脚本建议位于：

```text
<project>/vapoursynth/pipeline.vpy
<project>/vapoursynth/modules/*.py
```

全局脚本位于：

```text
%APPDATA%/ArknightsPassMaker/vapoursynth/
```

随包 `config/vs_runtime.json` 只读；GUI 选择的全局脚本绝对路径保存在同一
APPDATA 目录下的 `vs_runtime.user.json`。项目若选择 global，只保存
`source="global"` 和空 path；选择 project 才保存项目相对 `.vpy` 路径，
绝不把本机绝对路径写入项目。

`.vpy` 可以使用任意合法 VS 插件及 Python 代码。应用不向其中插入 `core.resize.*`、Crop、Rotate 或色彩转换语句。

脚本头的 `assetmaker-mode` 与 `assetmaker-api` 是 mode/API 的唯一事实源。项目只保存脚本来源和路径；`ScriptSelection`、worker load 及 VSPipe `--arg` 的 mode/API 必须从已解析 header 派生，并在执行前再次比对，禁止项目配置另存一份可漂移的 mode。

### 5.2 `job.json`

`job.json` 是一次渲染任务的不可变数据快照，不是滤镜配置。核心字段：

```json
{
  "api_version": 1,
  "epoch": 42,
  "track": "loop",
  "project_root": "D:/Project",
  "source": {
    "path": "D:/Project/loop.mp4",
    "kind": "video",
    "virtual_frame_count": null
  },
  "timeline": {
    "start_frame": 0,
    "end_frame": 300,
    "fps": { "numerator": 30000, "denominator": 1001 }
  },
  "transform": {
    "rotation": 90,
    "crop": {
      "coordinate_space": "post_rotation_source_pixels",
      "x": 20,
      "y": 40,
      "width": 320,
      "height": 568
    }
  },
  "output": {
    "profile": "360x640",
    "display_width": 360,
    "display_height": 640,
    "coded_width": 384,
    "coded_height": 640,
    "pixel_format": "YUV420P8",
    "matrix": "170m",
    "transfer": "170m",
    "primaries": "170m",
    "range": "limited",
    "final_rotate_180": true
  },
  "paths": {
    "cache_dir": "C:/.../session/cache"
  }
}
```

语义固定为：

- `start_frame` 包含，`end_frame` 不包含；
- 现有时间轴 UI 与 `EditorTrackState.out_frame` 仍保存“包含式出点”；只有在构造
  job 时通过既有 `_get_trim_bounds()` 转换为 `end_frame = out_frame + 1`。例如
  UI `20..80` 对应 job `[20,81)`，保存项目时仍写 80，避免旧项目语义漂移；
- `track` 只允许 `loop` 或 `intro`，同一项目脚本可据此为两条素材轨道选择不同处理；
- `source.virtual_frame_count` 对视频必须为 `null`；对图片必须是 trim 前完整
  合成时间轴的正整数。图片先循环到该帧数再注册 output 1，不能用 trim 的
  `timeline.end_frame` 代替完整时长；
- 帧率使用分数，避免 29.97 与 30000/1001 的累计误差；
- 首次加载视频时源帧数和分数帧率尚未知，兼容模式的 bootstrap job
  允许 `timeline.end_frame` 与 `timeline.fps` 为 `null`；脚本必须按完整源范围
  执行。worker 返回元数据后，后续编辑 job 与所有导出 job 必须把二者解析成
  明确的正整数/分数，导出阶段拒绝任何未解析的 `null`；
- crop 坐标位于旋转后的源画面像素空间；
- 路径按 UTF-8 JSON 保存，支持中文；
- 每个 epoch 创建独立文件，不覆盖 worker 正在读取的旧文件；
- 导出冻结当前作业快照。

### 5.3 `vs_runtime.json`

`config/vsconfig.json` 替换为 `config/vs_runtime.json`，只保存执行环境：

```json
{
  "schema_version": 1,
  "worker": {
    "startup_timeout_ms": 15000,
    "frame_timeout_ms": 10000,
    "shutdown_timeout_ms": 3000
  },
  "core": {
    "num_threads": 0,
    "max_cache_size_mb": 0
  },
  "plugins": {
    "native_plugin_dirs": [],
    "python_module_dirs": []
  },
  "scripts": {
    "global_script_path": ""
  }
}
```

以下字段从全局运行配置删除：

- `resampler_kernel`；
- `output_format`；
- `image_source_format`；
- `matrix_s`；
- `heuristic`；
- 固定 `required_plugins`。

它们属于具体脚本的处理策略，不是 VS 运行环境。

宿主对规范化 runtime 配置、便携 `vapoursynth.pyd/vapoursynth.dll/portable.vs`、
默认 plugin 目录及配置目录内的 `.py/.pyc/.pyd/.dll/.zip/.whl` 代码文件计算
有序 SHA-256 `runtime_fingerprint`。预览 worker 与导出 runner 必须使用并重算
同一个 fingerprint；运行配置或代码变化后重启 worker。LUT、模型、图片等非代码资源
不在该 fingerprint 内，不能把它表述成“整个渲染依赖完全冻结”。

## 6. 参数注入与执行 ABI

应用不会拼接脚本文本。每次参数改变后进行约 80–120 ms 防抖，生成新的 `job-<epoch>.json`。

注入给用户脚本的固定 globals：

```python
assetmaker_job       # job.json 绝对路径
assetmaker_api       # 宿主 ABI 版本，例如 "1"
assetmaker_script    # 用户 .vpy 绝对路径
assetmaker_mode      # "compatible" 或 "raw"
```

### 6.1 预览执行

主进程向 worker 发送 `load` 命令，其中包含 script path、job path、API、track、mode、epoch、bundle SHA-256 与 runtime fingerprint。worker 先交叉校验这些身份字段，再使用独立 globals：

```python
script_globals = {
    "__name__": "__vapoursynth__",
    "__file__": script_path,
    "assetmaker_job": job_path,
    "assetmaker_api": "1",
    "assetmaker_script": script_path,
    "assetmaker_mode": mode,
}
```

执行顺序：

```python
vs.clear_outputs()
code = compile(script_text, script_path, "exec")
exec(code, script_globals, script_globals)
output = vs.get_output(0)
clip = output.clip
```

### 6.2 导出执行

VSPipe 执行固定、可信的 `assetmaker_runner.vpy`：

```text
VSPipe.exe
  --arg assetmaker_job=<job-path>
  --arg assetmaker_script=<script-path>
  --arg assetmaker_api=1
  --arg assetmaker_mode=<compatible|raw>
  assetmaker_runner.vpy
  -
```

实际进程调用使用参数数组，不经过 PowerShell/cmd 字符串拼接。启动器只建立相同 globals、模块路径并执行用户脚本，不生成滤镜，也不修改 output 0。

执行用户脚本返回 `ExecutedGraph`，由它持有 namespace、output nodes 和模块搜索环境。
`sys.path` 不能只在 `exec()` 周围临时修改：`FrameEval`、`ModifyFrame` 等回调可能在
后续取帧时才延迟 import。worker 要等该图全部异步 frame future 发出终态后才恢复环境；
切换脚本根时无法及时排空则重启 worker。VSPipe runner 直接保持环境到进程退出。

模块搜索路径按顺序加入：

1. 用户脚本目录；
2. 用户脚本的 `modules/`；
3. `vs_runtime.json` 的 `python_module_dirs`；
4. 应用提供的可选 `assetmaker_vs` 辅助包。

宿主先校验 `vs_runtime.json`，再把第 3 项编码到专用环境变量
`ASSETMAKER_VS_PYTHON_DIRS_JSON`；runner 只解析这个 UTF-8 JSON 数组，不自行
猜测应用目录或重复实现运行配置解析。原生插件目录仍通过 VapourSynth 官方的
`VAPOURSYNTH_EXTRA_PLUGIN_PATH` 传给 VSPipe，预期 fingerprint 通过
`ASSETMAKER_VS_RUNTIME_FINGERPRINT` 传入并由 runner 重算。job、脚本头和输出契约的解析器
位于 VSPipe/worker 均可导入的纯 Python `assetmaker_vs` 包中；宿主 `core`
模块只提供强类型适配，不维护第二套 wire 语义。

不得把整个 `tools/media/` 插入 `sys.path`，避免嵌入式 Python 扩展覆盖宿主环境。

## 7. 兼容模式与原始模式

能力信息使用 `.vpy` 顶部普通注释，执行前可安全解析：

```python
# assetmaker-api: 1
# assetmaker-mode: compatible
# assetmaker-capabilities: source,trim,crop,rotation,resolution,image_loop
# assetmaker-requires: lsmas.LWLibavSource,imwri.Read
# assetmaker-editor-output: 1
```

### 7.1 兼容模式

- 读取 `assetmaker_job`；
- capability 对应的 GUI 控件可用；
- `requires` 检查完整 namespace/function 路径；
- 声明 crop/rotation/trim 能力的兼容脚本应把“完整源时间轴、旋转后、
  trim/crop 前”的编辑画布注册到可选 output 1。宿主用它取得源帧数、源 FPS
  和裁剪坐标空间，并在编辑模式绘制裁剪框；output 1 不参与最终预览或编码，
  最终预览和导出始终共同使用 output 0；
- 图片源必须先按 resolved timeline 循环到完整编辑时长，再注册 output 1，然后
  执行 trim/crop 生成 output 0；否则只有 1 帧的图片 output 1 无法提供真实时间轴；
- 默认模板支持现有全部编辑参数；
- capability 是脚本作者的接口承诺，宿主不声称能静态证明脚本内部语义。

### 7.2 原始脚本模式

- 不要求读取 job；
- 保留脚本选择、加载、播放、暂停、跳帧、显示缩放、预览和导出；
- 禁用脚本未声明支持的裁剪、旋转、trim 等编辑控件；
- 未声明 editor output 时不读取 output 1；原始模式直接以 output 0 的帧数和
  FPS 作为时间轴，trim/crop/rotation 编辑控件保持禁用；
- 设备目标 profile 始终保留，因为它是输出验证目标而非隐藏滤镜：原始脚本必须自行输出该 profile 的 coded size；改变 profile 不会触发宿主自动缩放；
- 仍必须满足 output 0 契约。

## 8. 信任模型

1. 内置模板默认可信；
2. 用户主动选择的全局脚本视为用户所有；
3. 项目脚本首次执行前显示绝对路径、来源、代码文件列表和 SHA-256，并明确说明它是可执行 Python；bundle 哈希递归覆盖脚本根目录内的 `.vpy` 与 `.py` 文件（包括 `modules/`）；
4. 本机记录 canonical path、bundle hash 和授权时间；
5. 主 `.vpy` 或同目录 Python 模块变化后授权失效；
6. 素材市场、下载目录或外部压缩包项目中的脚本绝不自动执行；
7. 信任记录不写入项目或导出包。

全局脚本的文件选择动作本身构成授权，可显示一次风险说明，但不使用项目脚本的逐 hash 信任门；仍为当前 session/导出计算 bundle hash 以检测代码竞态。bundle 信任和 TOCTOU 检查只覆盖 `.vpy/.py` 代码；LUT、模型、图片、JSON 等非代码资源不会被冻结。需要完全可复现资源时必须在未来引入显式资源清单，不能静默递归整个项目。

该机制不是安全沙箱。用户授权后，脚本仍能访问文件、进程和网络。独立 worker 提供的是崩溃隔离，不是权限隔离。

## 9. VS 工作进程

主程序通过共用 `WorkerProcess` 传输启动常驻 worker：底层使用
`subprocess.Popen` 参数数组和二进制 stdin/stdout/stderr PIPE，Windows 显式传入
`CREATE_NO_WINDOW`；`VSWorkerClient(QObject)` 只把 reader 线程事件转换为 Qt signals，
不维护第二套进程协议。这样无需依赖当前 PyQt6 未暴露的
`QProcess.setCreateProcessArgumentsModifier()`，同时保留可自动验证的标准管道和无控制台启动。
依据：[Python `subprocess.Popen`/`CREATE_NO_WINDOW`](https://docs.python.org/3/library/subprocess.html#subprocess.CREATE_NO_WINDOW)、[Qt 跨线程 signals/slots](https://doc.qt.io/qt-6/threads-qobject.html#signals-and-slots-across-threads)。
控制协议使用长度前缀 JSON：

- 请求：`hello`、`load`、`request_frame`、`request_plane_digest`、`cancel_epoch`、`unload`、`shutdown`；
- 响应：`ready`、`metadata`、`frame_ready`、`frame_discarded`、`plane_digest`、`requirement_error`、`script_error`、`contract_error`、`request_error`、`log`。

`worker_crashed` 不是 wire message：已经退出的进程无法发送它；共用进程传输依据 child exit code/status 合成本地崩溃事件。`metadata` wire 统一解析为 `SessionMetadata(epoch, mode, capabilities, output0, editor)`，其中每个节点使用 `NodeMetadata(width, height, num_frames, fps_num, fps_den, pixel_format, matrix, transfer, primaries, range)`，同步接口与 Qt 包装不得各自猜 dict 字段。

worker 在启动时捕获原始 binary stdout 作为协议 writer，并在整个进程生命周期把 Python `sys.stdout` 替换为线程安全、分块受限的结构化日志 writer；VSPipe runner 同样在整个脚本/帧回调生命周期把 Python stdout 指向 stderr。只在 `exec()` 周围临时 `redirect_stdout` 无效，因为 `FrameEval/ModifyFrame` 回调可能稍后打印。直接 `os.write(1, ...)` 仍是可信脚本可破坏协议的明确边界。

像素帧不使用 JSON/Base64。主进程创建带 generation 的共享内存槽；worker 先从 VS planar RGB24 按 stride 复制并重排为 packed BGR24，再写入共享内存，主进程负责最终释放。一个槽在对应 `request_frame` 收到 `frame_ready`、`frame_discarded` 或 `request_error` 终态之前不得复用；`cancel_epoch` 只标记取消，不能假设 VS callback 已停止。终态必须回显 request/epoch/slot name/generation，进程死亡时才可批量回收。

`load` 同时携带 API、track、epoch、mode、bundle hash 和 runtime fingerprint；worker 在执行用户代码前核对 message、job、header、selection 与本机 runtime 的全部值。`request_plane_digest` 仅供诊断/测试，以去除 stride padding 的 output0 Y/U/V 有效行 SHA-256 证明 worker/VSPipe 字节一致，不通过 JSON 传输帧数据。

超时后先允许用户继续等待，再提供“终止并重启渲染进程”。worker 崩溃不能阻止主窗口保存项目或正常退出。

### 9.1 私有显示支路

用户 output 0 原样用于导出。worker 可从 output 0 创建不注册为输出的私有显示支路：

```text
output 0（严格 YUV 编码输出）
    ├── VSPipe/x264：原样消费
    └── GUI 显示：依据帧属性转换 RGB24 → 可选 Point 像素放大
```

RGB 转换和 1%–10000% 显示缩放是显示传输，不会修正或改变编码输出。100%=完整画面 fit viewport；低于 100% 缩小 fit 结果；高于 100% 先从 RGB24 裁取约 `source/zoom` 的窗口，再用 Point 放到 fit viewport，10000% 即约 1/100 源窗口。RGB24 的预览窗口与偏移取消偶数对齐；YUV420 output 0 仍须满足偶数宽高，脚本内部的子采样裁剪偏移则由 VS 滤镜自身校验。

## 10. 严格输出契约

worker 在脚本执行后立即验证 output 0：

- `vs.get_output(0)` 存在且为 `VideoOutputTuple`；
- `.clip` 为 `VideoNode`、`.alpha is None`、`alt_output == 0`；
- 后续格式、尺寸、帧率、帧数与 frame props 全部针对 `.clip` 校验；
- 分辨率和格式固定；
- 为设备/编码器支持的 YUV420P8/i420；
- 宽高满足 4:2:0 约束并符合所选 profile 的 coded size；
- 帧数与分数帧率有效；
- `_Matrix`、`_Transfer`、`_Primaries` 以及范围属性完整且可映射到 x264 VUI；范围读取必须按运行时版本规范化：优先采用当前 API 的 `_Range`，R73 则兼容并测试其 `_ColorRange`，不能把两套数值语义直接混用；
- 首/中/末三个唯一 sentinel frame 的四个色彩属性彼此一致并等于 job 目标，用于加载阶段尽早失败；
- output 0 随后由 `std.ModifyFrame` 加一层只读 validation guard：selector 对每个实际消费帧检查格式、尺寸和色彩属性，合法时直接透传原 frame，失败则中止预览/编码。该 guard 不改变像素、属性、格式、时间轴或滤镜策略，是宿主唯一允许追加的 output0 包装。

若 header 声明 output 1，它同样必须是 `VideoOutputTuple`，且 `.clip` 为 `VideoNode`、无 alpha、`alt_output == 0`；其尺寸/FPS/帧数按编辑画布语义校验，不套用 output 0 的 YUV420/设备 coded-size 限制。

不符合时应用显示实际值、要求值和 `.vpy` 修正示例，但不追加 Resize、Convert、AddBorders 或色彩滤镜。x264 的 `--colormatrix`、`--colorprim`、`--transfer` 和 `--range` 从已验证的输出属性生成，不再使用与脚本分离的硬编码色彩策略。

## 11. 分阶段迁移

### M0：恢复绿色基线

- 清理 fixture、媒体测试及 golden 中已废弃的 `480x854`；
- 使用仍支持的规格保留等价覆盖；
- 全量测试通过后单独提交。

### M1：运行配置与作业 ABI

新增：

- `config/vs_runtime.py`、`config/vs_runtime.json`；
- `schemas/vs_runtime.schema.json`、`schemas/vs_job.schema.json`；
- `core/vs_runtime/job.py`、`script_header.py`、`trust.py`、`output_contract.py`。

旧 `vsconfig.json` 仅迁移 core 和插件路径字段；滤镜字段不迁移，并显示迁移报告。旧文件保留一个版本周期，不自动删除。

### M2：可信启动器

新增：

- `core/vs_runtime/executor.py`、`protocol.py`、`runner.py`；
- `resources/vapoursynth/assetmaker_runner.vpy`；
- `resources/vapoursynth/default_pipeline.vpy`。

覆盖真实 VSPipe、中文路径、相邻模块导入、参数注入和错误 output。

### M3：独立 worker

新增：

- `core/vs_runtime/worker_main.py`、`shared_frame.py`；
- `core/vs_runtime/worker_process.py`（唯一 Popen/PIPE/退出监控实现）；
- `gui/workers/vs_worker_client.py`。

覆盖脚本异常、超时、崩溃、重启、`CREATE_NO_WINDOW` 冻结 worker 管道、共享内存清理、slot generation 和 epoch 竞争。

### M4：预览切换

修改：

- `gui/widgets/video_preview.py`；
- `gui/main_window.py`；
- `main.py`；
- `tests/qt_harness.py`。

移除主进程 VS prewarm 和 in-process VideoNode。加载、seek、播放、裁剪重建和缩放全部经 worker。

### M5：导出切换

修改：

- `core/media_pipeline.py`；
- `core/export_service.py`；
- `core/media_tools.py`。

导出冻结 script hash + runtime fingerprint + job，worker 预检 output 0，VSPipe 执行相同脚本，逐帧 guard 检查动态属性，x264 VUI 从输出属性生成。

### M6：脚本 UI 与旧项目兼容

新增：

- `gui/dialogs/vs_script_trust_dialog.py`；
- `gui/widgets/vs_script_panel.py`。

修改 `config/epconfig.py` 与主窗口，保存项目相对脚本路径和编辑状态。旧项目没有脚本字段时自动使用内置兼容模板，不要求用户手工迁移。

### M7：删除旧双图

所有新路径通过后，删除或合并：

- `core/vs_graph.py`；
- `core/vs_script.py`；
- `core/vs_engine.py`；
- `core/vs_player.py`；
- `core/vs_frame.py`；
- `config/vsconfig.py`；
- `config/vsconfig.json`。

最终源码不得保留 `write_vpy_script()`、`build_export_graph()`、全局 `resampler_kernel`、全局 `matrix_s` 或主进程 `vs.core`。

### M8：文档与知识库

修订知识库 01、02、03、06、08、10、11，并新增：

- `13-user-vpy-abi.md`；
- `14-worker-protocol.md`；
- `15-output-contract.md`；
- `16-script-trust.md`。

更新 `INDEX.md`、`docs/VS_DECOUPLING.md`、README 和用户手册。本轮不修改 CHANGELOG 顶部版本，避免推送时触发自动发布。

## 12. 测试与验收

每阶段必须运行：

```powershell
uv run python -m compileall main.py config core gui utils tests
uv run python -m unittest discover -s tests -p "test_*.py"
```

工具存在时不得跳过以下真实路径：

- VSPipe 参数注入及中文路径；
- x264-7mod 与 MP4Box/lsmash；
- worker output 0 有效平面 digest 与 VSPipe Y4M 帧平面 byte-exact 对比；
- 视频、图片循环、trim、旋转、裁剪、补边；
- 输出 MP4 解码后的几何与色彩检查；
- 1%/100%/10000% 显示缩放与高倍 Point 像素检查；
- 取消后迟到 callback 不覆盖或释放新 generation 帧槽；
- 第二帧及任意后续帧色彩属性漂移会中止编码；
- 项目脚本哈希变化与重新信任；
- worker 卡死、崩溃和恢复；
- 旧项目自动使用默认模板。

人工验收流程：打开旧项目 → 默认模板预览 → 裁剪/旋转/trim/缩放 → 原始脚本模式 → 项目脚本信任 → 修改脚本使信任失效 → 重启 worker → 导出 → 解码/播放成品 → 保存、自动保存和正常退出。

## 13. 提交划分

1. `test: 修复废弃分辨率导致的 VS 测试基线`
2. `refactor: 拆分 VS 运行配置与渲染作业协议`
3. `feat: 引入自定义 vpy 启动器和输出契约`
4. `feat: 新增独立 VapourSynth 预览工作进程`
5. `refactor: 预览切换到用户自定义 vpy`
6. `refactor: 导出执行与预览相同的 vpy`
7. `feat: 添加脚本能力声明与本地信任机制`
8. `refactor: 删除双图实现和旧 vsconfig`
9. `docs: 更新 VapourSynth 知识库与使用说明`

所有提交均使用中文说明，不添加 Claude/Codex 共同作者。每个提交独立验证，不将架构迁移压成一个不可审查的大提交。

## 14. 非目标与边界

- 不重新引入 mpv；
- 不在本轮实现完整 `.vpy` IDE；用户可使用外部编辑器；
- 不把 worker 描述为安全沙箱；
- 不允许宿主静默修正用户 output 0；
- 不修改 x264 质量参数，除非输出属性要求同步 VUI；
- 不修改模拟器或设备固件；
- 不推送远程或触发发布，除非用户另行明确授权。

## 15. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 自定义脚本执行任意 Python | 项目脚本显式信任、bundle 哈希、本机信任记录、下载项目不自动运行 |
| 插件导致进程崩溃 | 独立 worker、主窗口可保存、可重启渲染进程 |
| 滤镜图重建影响拖动体验 | 参数防抖、epoch 丢弃旧帧、worker 常驻 |
| VSPipe 便携 Python 找不到模块 | 固定 runner 显式配置脚本及模块目录，真实 VSPipe 回归 |
| 色彩与编码标签不一致 | 严格检查帧属性，x264 VUI 从已验证属性生成 |
| 用户脚本声明能力但实际忽略参数 | 文档明确能力是作者承诺；原始模式默认禁用相关控件 |
| 重构期间两条路径并存 | 分阶段 feature cutover；新路径全部通过后立即删除旧双图 |
