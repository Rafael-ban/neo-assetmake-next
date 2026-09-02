# VapourSynth 用户自定义 VPY 迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务执行；每完成一个 checkbox 就更新本文件，不得跨过验证门。

**目标：** 将素材预览与导出迁移到同一份用户自定义 `.vpy` 和同一份不可变 `job.json`，由独立 VapourSynth worker 隔离执行，并以严格 output 0 契约保证设备与 x264 可消费。

**架构：** `.vpy` 是唯一滤镜事实源；Qt 主进程只管理项目状态、脚本信任、作业快照和帧显示。预览由常驻 `vs_worker` 按帧请求，导出由固定 `assetmaker_runner.vpy` 交给 VSPipe。两条路径共用同一个纯 Python 执行 ABI、同一脚本、同一 job 和同一输出校验器。

**技术栈：** Python 3.12、PyQt6 6.10、VapourSynth R73/API 4.1、VSPipe、x264-7mod、MP4Box/lsmash-muxer、JSON Schema Draft 2020-12、Windows named `mmap`、cx_Freeze、`unittest`。

**设计规范：** `docs/superpowers/specs/2026-09-02-vapoursynth-custom-vpy-architecture-design.md`

## 1. 证据门：为什么旧结构不满足目标，新结构为什么成立

| 问题 | 旧写法为何无效 | 修改后为何有效及验证门 |
|---|---|---|
| 双图实现会漂移 | `core/vs_graph.py::build_export_graph()` 与 `core/vs_script.py::VpyScriptBuilder` 分别实现相同滤镜链；它们各自语法合法，但无法满足“完全由用户自定义且预览=导出”的产品契约。 | VapourSynth 官方以 Python `.vpy` 建图并用 `set_output()` 注册输出：[Getting Started](https://www.vapoursynth.com/doc/gettingstarted.html)、[Python Reference](https://www.vapoursynth.com/doc/pythonreference.html)。worker 与 VSPipe 必须对同一脚本、同一 job 逐帧一致。 |
| JSON 假滤镜配置 | `config/vsconfig.py::_coerce_format()` 只检查标识符；测试允许 `YUV422P10`，而 `build_x264_command()` 固定 `--output-csp i420`。配置可以“通过”却无法被编码器消费。 | output 0 严格限制为项目设备契约 `YUV420P8/i420`；Resize 官方文档证明格式、矩阵、range、UV kernel 是不同参数，不能由一个全局 kernel 字符串代替：[Resize](https://www.vapoursynth.com/doc/functions/video/resize.html)。 |
| 缩放职责耦合 | 一个 `resampler_kernel` 同时控制导出缩放和 YUV→RGB 色度升采样；拼写错误在部分调用点静默回退 Bicubic，另一些调用点延迟到 VSPipe 才失败。 | 滤镜策略完全进入 `.vpy`；宿主只校验最终输出。私有显示支路固定为 RGB24 + Point 视口放大，不污染 output 0。 |
| RGB 放大有无效偶数约束 | `apply_preview_zoom()` 对 RGB24 仍 `& ~1`。本仓 R73 探针证明 RGB24 `CropAbs` 接受奇数尺寸/偏移；该约束会改变中心像素和实际倍率。 | 仅 YUV420 output 0 保留 mod-2；显示支路允许奇数窗口。10000% 放大采用“先裁小窗口，再 Point 放到固定 viewport”，成本与倍率无关。 |
| 色彩标签与编码 VUI 分裂 | `.vpy` 的矩阵可配置，x264 VUI 却独立默认 `smpte170m/tv`；像素转换与标签可能不一致。 | worker 从 output 0 frame props 规范化 `_Matrix/_Transfer/_Primaries/_ColorRange`，生成唯一 `X264Vui`，编码命令不再有隐式默认。 |
| 未打标源按裁剪后高度猜矩阵 | 现有链在 crop 后读取 `clip.height`；用户拖裁剪框跨过 720 会改变同一源的颜色。P7 实测真 709 红色误差从 1 变 22。 | 默认 `.vpy` 在任何 trim/crop 前保存原始源高度，只以原始高度做兼容启发式。P7 回归必须证明不同裁剪高度输出颜色一致。 |
| VSPipe 直接执行用户脚本的 import 不稳定 | 本仓 R73 探针复现：相邻模块直接 import 与仅设 `PYTHONPATH` 均可能 `ModuleNotFoundError`；用户 `print()` 还会污染 Y4M/stdout 协议。 | 官方 VSPipe 支持 `--arg key=value`：[Output/VSPipe](https://www.vapoursynth.com/doc/output.html)。固定 runner 显式管理 `sys.path`、globals 和 stdout，使用参数数组传路径。 |
| 插件路径语义混合 | 旧 `extra_plugin_dirs` 混合原生 DLL 与 Python 模块；autoload 失败可能无提示。 | 官方说明 portable/autoload 路径及错误可能被忽略：[Installation](https://www.vapoursynth.com/doc/installation.html)。新 runtime 分离 `native_plugin_dirs` 与 `python_module_dirs`，并校验完整 `namespace.Function`。 |
| Qt 主进程 prewarm VS | `main.py`/测试 harness 在 PyQt 前加载 VS；当前 bundle 反序会 exit 139，主进程崩溃不可恢复。 | 独立 worker 完全不导入 PyQt；主进程通过 `subprocess.Popen` 的参数数组与二进制 PIPE 管理它，并在 Windows 显式使用 `CREATE_NO_WINDOW`。Python 官方文档确认 `Popen` 提供 stdin/stdout/stderr PIPE、参数序列和该 creation flag：[subprocess](https://docs.python.org/3/library/subprocess.html#subprocess.CREATE_NO_WINDOW)。reader 线程只发 Qt signal，Qt 官方保证跨线程 Auto Connection 自动变为 Queued Connection：[Threads and QObjects](https://doc.qt.io/qt-6/threads-qobject.html#signals-and-slots-across-threads)。 |
| 帧生命周期跨线程风险 | VS frame 平面受 VS 管理、有 stride，frame 关闭后视图失效；callback 也不在 GUI 线程。 | worker 在 callback 内按 stride 复制为连续 BGR，再写 named mmap；GUI 只消费 `.copy()`。依据 `docs/vapoursynth-kb/07-frame-lifetime-threading.md` 的源码与实测。 |
| 预览不应另造播放器滤镜链 | 旧播放器预览与导出脚本各自解释编辑参数，无法证明同一帧。 | 官方 Python API 的 `VideoNode.get_frame_async()` 明确返回 Future/异步回调，官方 VSPipe 源也用 `getFrameAsync` 拉帧：[Python Reference](https://www.vapoursynth.com/doc/pythonreference.html)、[VSPipe source](https://github.com/vapoursynth/vapoursynth/blob/master/src/vspipe/vspipe.cpp)。本项目沿用“脚本→请求第 N 帧→显示”的模型；[VapourSynth-Editor](https://github.com/YomikoR/VapourSynth-Editor) 仅作为社区 UI 架构参考。 |

实施者必须先读上述链接、对应源码及 `docs/vapoursynth-kb/INDEX.md` 路由到的单篇条目。这里否定的是旧架构对本项目目标无效，不是宣称旧 Python/VS 语法本身非法。

## 2. 文件与接口地图（先冻结边界，再进入任务）

### 2.1 新增文件及唯一责任

| 文件 | 唯一责任 |
|---|---|
| `config/vs_runtime.py` / `.json` | worker/core/plugin/script 路径与超时；不得含滤镜策略。 |
| `schemas/vs_runtime.schema.json` | 运行配置 Draft 2020-12 schema。 |
| `schemas/vs_job.schema.json` | `RenderJob` wire-format schema。 |
| `core/vs_runtime/job.py` | 不依赖 Qt/VS 的不可变 job dataclass、共享 wire 校验适配与原子写入。 |
| `core/vs_runtime/script_header.py` | 把共享脚本头结果适配为宿主 frozen 类型；不重复解析语义。 |
| `core/vs_runtime/migration.py` | 从旧配置一次性迁移运行字段；滤镜字段明确忽略并报告。 |
| `core/vs_runtime/trust.py` | 规范化路径、递归代码 bundle hash、本机信任库。 |
| `core/vs_runtime/protocol.py` | 4-byte big-endian 长度前缀 JSON。 |
| `core/vs_runtime/shared_frame.py` | Windows named `mmap` 帧槽与连续 BGR24。 |
| `core/vs_runtime/vs_loader.py` | 仅 worker 使用的 R73 pyd/DLL/portable plugin 加载。 |
| `core/vs_runtime/session.py` | `ScriptSelection`、`RenderSession`、worker command resolution。 |
| `core/vs_runtime/worker_main.py` | worker 命令循环、graph/session/epoch、帧请求与错误隔离。 |
| `core/vs_runtime/worker_process.py` | 唯一 `Popen` 进程传输、二进制 reader/writer、退出监控与同步请求接口；供 GUI 包装、导出预检和 metadata API 共用。 |
| `vs_worker.py` | cx_Freeze worker 薄入口。 |
| `gui/workers/vs_worker_client.py` | 对共用 `WorkerProcess` 的 `QObject` 包装、mmap 槽池、GUI timers 与 Qt signals；不再维护第二套进程启动/协议实现。 |
| `gui/dialogs/vs_script_trust_dialog.py` | 展示脚本清单/hash/风险并取得本机授权。 |
| `gui/widgets/vs_script_panel.py` | 内置/全局/项目脚本选择、能力、信任与重载。 |
| `resources/vapoursynth/assetmaker_runner.vpy` | VSPipe 固定可信入口，只 bootstrap 并执行用户脚本。 |
| `resources/vapoursynth/default_pipeline.vpy` | 可读、可复制修改的内置兼容脚本；唯一默认滤镜图。 |
| `resources/vapoursynth/python/assetmaker_vs/job_api.py` | worker/VSPipe 共用 job wire 校验与读取。 |
| `resources/vapoursynth/python/assetmaker_vs/script_header.py` | worker/VSPipe 共用脚本头解析；`core` 只做强类型适配。 |
| `resources/vapoursynth/python/assetmaker_vs/executor.py` | 共用 globals/sys.path/stdout 隔离与脚本执行。 |
| `resources/vapoursynth/python/assetmaker_vs/contract.py` | 共用 output 0/1 校验、frame props 与 VUI 规范化。 |
| `resources/vapoursynth/python/assetmaker_vs/display.py` | 私有 RGB24 显示与 Point 放大；绝不修改 output 0。 |

### 2.2 迁移后修改/删除

- `gui/widgets/video_preview.py`：保留 public API/signals，后端替换为 `VSWorkerClient`。
- `gui/main_window.py`：信任脚本后加载媒体；导出冻结 `RenderSession`/job。
- `core/media_pipeline.py`：VSPipe 执行 runner + `--arg`；x264 VUI 必须显式传入。
- `core/export_service.py`：不再生成滤镜 `.vpy`；改为 immutable job + worker 预检。
- `core/media_tools.py`：只发现工具/运行环境，不再推断滤镜/output。
- `config/epconfig.py`：project-only `editor` 区新增脚本引用；导出仍剥离。
- `main.py`、`tests/qt_harness.py`：删除主进程 VS prewarm。
- `build.py`、`.github/workflows/build-app.yml`：打包 worker、runtime、runner、helper、default script 并验证产物。
- M7 删除 `core/vs_graph.py`、`core/vs_script.py`、`core/vs_engine.py`、`core/vs_player.py`、`core/vs_frame.py`、`config/vsconfig.py`、`config/vsconfig.json`、`schemas/vsconfig.schema.json`。

### 2.3 跨任务稳定接口

```python
@dataclass(frozen=True)
class ScriptSelection:
    script_path: str
    mode: Literal["compatible", "raw"]
    bundle_hash: str
    api_version: int = 1

@dataclass(frozen=True)
class RenderSession:
    epoch: int
    track: Literal["loop", "intro"]
    selection: ScriptSelection
    job_path: str
    runtime_fingerprint: str

@dataclass(frozen=True)
class X264Vui:
    colormatrix: str
    colorprim: str
    transfer: str
    range_: Literal["tv", "pc"]

@dataclass(frozen=True)
class ValidatedOutput:
    width: int
    height: int
    num_frames: int
    fps_num: int
    fps_den: int
    pixel_format: str
    matrix: str
    transfer: str
    primaries: str
    range: Literal["limited", "full"]
    vui: X264Vui

@dataclass(frozen=True)
class NodeMetadata:
    width: int
    height: int
    num_frames: int
    fps_num: int
    fps_den: int
    pixel_format: str
    matrix: str | None
    transfer: str | None
    primaries: str | None
    range: Literal["limited", "full"] | None

@dataclass(frozen=True)
class SessionMetadata:
    epoch: int
    mode: Literal["compatible", "raw"]
    capabilities: frozenset[str]
    output0: NodeMetadata
    editor: NodeMetadata | None
```

`ScriptSelection` 不直接从项目 JSON 反序列化；只允许 `ScriptSelection.from_header(script_path, header, bundle_hash)` 构造，确保 mode/API 没有第二事实源。

## 3. 全局约束与执行顺序

- 执行顺序固定为 M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8；不得把 M7 清理提前。
- 用户 `.vpy` 是唯一滤镜图；宿主不得在 output 0 后追加 Resize/Convert/Crop/Rotate/AddBorders/色彩修正。唯一允许的包装是逐帧、只读、原帧透传的 output-contract guard；它不得改变像素、格式、尺寸、帧属性或时间轴。
- output 0 是最终预览与导出；compatible 可选 output 1 仅为“完整源时间轴、旋转后、trim/crop 前”的编辑画布；raw 不读取 output 1。
- 首次 compatible bootstrap job 可令 `timeline.end_frame`/`fps` 为 `null`；编辑稳定后和所有导出 job 必须完全解析。
- job 使用 UTF-8 原子写入并按 epoch 唯一命名；导出冻结 job、脚本路径与 bundle hash。
- 项目脚本信任递归覆盖根目录内所有 `.vpy`/`.py`；信任只在本机，不写项目或导出包。
- worker 是崩溃隔离，不是权限沙箱；UI 必须明确说明 `.vpy` 是任意 Python。
- `runtime_fingerprint` 覆盖规范化运行配置以及参与执行的 `.py/.pyc/.pyd/.dll/.zip/.whl` 代码文件；配置或运行时代码变化必须重启 worker，并在 VSPipe 执行前再次核对。LUT/模型/图片等非代码资源仍不在此保证内。
- `gui/` 不加入混淆；VSPipe 只能依赖 `resources/vapoursynth/python/assetmaker_vs/` 的纯 Python ABI。
- 不修改 Rust simulator/设备固件，不重新引入 mpv，不修改 `docs/CHANGELOG.md` 顶部版本，不推送远端。
- 每一任务遵循红灯 → 最小实现 → 定向测试 → 全量回归 → 单独提交；提交无 Claude/Codex 共同作者。

---

### Task 1: M0 — 恢复废弃分辨率后的绿色基线

**Files:**
- Modify: `tests/fixtures/epconfig/full_overlay_transition.json`
- Modify: `tests/test_crop_aspect_lock.py`
- Modify: `tests/test_media_pipeline.py`
- Modify: `tests/test_vpy_golden.py`
- Delete: `tests/fixtures/vpy/image_480x854.golden`
- Create: `tests/fixtures/vpy/image_720x1080.golden`

**边界：** 本任务只修测试基线，不改变生产渲染。唯一输入事实是 `RESOLUTION_SPECS == {"360x640", "720x1080"}`。

- [ ] **Step 1: 保存当前红灯证据**

Run:

```powershell
uv run python -m unittest tests.test_epconfig_contract tests.test_media_pipeline tests.test_vpy_golden -v
```

Expected: 失败仅指向已废弃的 `480x854`；若出现 VS 初始化、编码或其他错误，先单独诊断，不得将它们归为 fixture 漂移。

- [ ] **Step 2: 将 stale fixture 和 image-loop case 迁到生产规格**

`tests/test_vpy_golden.py` 使用：

```python
"image_720x1080": VideoExportParams(
    video_path=r"C:\media\logo.png",
    cropbox=(0, 0, 0, 0),
    start_frame=0,
    end_frame=30,
    fps=30.0,
    resolution="720x1080",
    is_image=True,
),
```

同步修改 `test_writes_image_loop_script` 的宽高断言、epconfig fixture 和 crop profile 表。删除旧 golden。

- [ ] **Step 3: 一次性生成并人工审查新 golden**

Run:

```powershell
uv run python -c "from tests.test_vpy_golden import CASES,_generate_normalized; print(_generate_normalized(CASES['image_720x1080']), end='')"
```

Expected: stdout 只出现 `width=720, height=1080, format=vs.YUV420P8`，不出现 `480`/`854`。人工审查该输出后，使用 `apply_patch` 新建 `image_720x1080.golden`；不得让 Python 直接写仓库文件。所有 golden 一一被 case 覆盖。

- [ ] **Step 4: 验证并提交**

```powershell
uv run python -m unittest tests.test_epconfig_contract tests.test_media_pipeline tests.test_vpy_golden -v
uv run python -m unittest discover -s tests -p "test_*.py"
git add tests/fixtures/epconfig/full_overlay_transition.json tests/test_crop_aspect_lock.py tests/test_media_pipeline.py tests/test_vpy_golden.py tests/fixtures/vpy/image_720x1080.golden
git add -u tests/fixtures/vpy/image_480x854.golden
git commit -m "test: 修复废弃分辨率导致的 VS 测试基线"
```

Expected: 全量无 failure/error；媒体工具存在时真实媒体测试不得 skip；`git show -1 --format=full` 无共同作者 trailer。

---

### Task 2: M1 — 拆分运行配置、作业 ABI、脚本头与旧配置迁移

**Files:**
- Create: `config/vs_runtime.py`, `config/vs_runtime.json`
- Create: `schemas/vs_runtime.schema.json`, `schemas/vs_job.schema.json`
- Create: `core/vs_runtime/__init__.py`, `job.py`, `script_header.py`, `migration.py`
- Create: `tests/test_vs_runtime_contract.py`, `tests/test_vs_job_contract.py`, `tests/test_vs_script_header.py`
- Modify: `build.py`, `tests/test_media_packaging.py`

**Produces:** `load_vs_runtime()`、`RenderJob`、`load_render_job()`、`write_render_job()`、`parse_script_header()`、`migrate_legacy_vsconfig_once()`。

- [ ] **Step 1: 为运行配置写红灯测试**

```python
def test_filter_policy_is_not_a_runtime_field(self):
    serialized = json.dumps(VSRuntimeConfig().to_dict())
    for key in ("resampler_kernel", "output_format", "image_source_format",
                "matrix_s", "heuristic", "required_plugins"):
        self.assertNotIn(key, serialized)

def test_existing_but_invalid_file_fails_loudly(self):
    path = self.write_json({"schema_version": 999})
    with self.assertRaisesRegex(VSRuntimeConfigError, str(path)):
        load_vs_runtime(path)

def test_user_override_changes_global_script_without_writing_shipped_file(self):
    save_vs_runtime_override(user_path, {"scripts": {
        "global_script_path": r"D:\VS\pipeline.vpy"}})
    merged = load_vs_runtime(shipped_path, user_path)
    self.assertEqual(merged.scripts.global_script_path,
                     r"D:\VS\pipeline.vpy")
    self.assertEqual(shipped_path.read_bytes(), shipped_before)
```

Run: `uv run python -m unittest tests.test_vs_runtime_contract -v`

Expected: FAIL，因为新配置模块尚不存在。

- [ ] **Step 2: 实现只含运行策略的 frozen 配置**

```python
@dataclass(frozen=True)
class WorkerConfig:
    startup_timeout_ms: int = 15_000
    frame_timeout_ms: int = 10_000
    shutdown_timeout_ms: int = 3_000

@dataclass(frozen=True)
class CoreConfig:
    num_threads: int = 0
    max_cache_size_mb: int = 0

@dataclass(frozen=True)
class PluginConfig:
    native_plugin_dirs: tuple[str, ...] = ()
    python_module_dirs: tuple[str, ...] = ()

@dataclass(frozen=True)
class ScriptConfig:
    global_script_path: str = ""

@dataclass(frozen=True)
class VSRuntimeConfig:
    schema_version: Literal[1] = 1
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    core: CoreConfig = field(default_factory=CoreConfig)
    plugins: PluginConfig = field(default_factory=PluginConfig)
    scripts: ScriptConfig = field(default_factory=ScriptConfig)
```

拒绝未知键、负数、错误类型和未知 schema version。只有“文件不存在”可返回默认；文件存在但损坏必须带绝对路径抛错。`max_cache_size_mb` 文档写明 R73 仅为软建议。随包 `config/vs_runtime.json` 只读；用户可写覆盖文件固定为 `%APPDATA%/ArknightsPassMaker/vapoursynth/vs_runtime.user.json`，`load_vs_runtime()` 逐字段合并后再整体校验，`save_vs_runtime_override()` 原子写入。GUI 选择全局脚本只改 user override，不修改安装目录。M1 期间旧 `vsconfig.json` 暂时继续打包以完成迁移。

- [ ] **Step 3: 为 immutable RenderJob 写红灯 schema/模型测试**

```python
def test_bootstrap_nulls_are_preview_only(self):
    bootstrap = make_job(end_frame=None, fps=None)
    bootstrap.validate(for_export=False)
    with self.assertRaises(RenderJobError):
        bootstrap.validate(for_export=True)

def test_fraction_unicode_track_and_crop_space_roundtrip(self):
    job = make_job(source_path=r"D:\素材\黍\loop.mp4", track="loop")
    payload = job.to_dict()
    Draft202012Validator(JOB_SCHEMA).validate(payload)
    self.assertEqual(RenderJob.from_dict(payload), job)
    self.assertEqual(payload["timeline"]["fps"], {
        "numerator": 30000, "denominator": 1001,
    })
```

Run: `uv run python -m unittest tests.test_vs_job_contract -v`

Expected: FAIL，因为 `RenderJob` 尚不存在。

- [ ] **Step 4: 实现 job 类型、profile 推导与原子写入**

```python
@dataclass(frozen=True)
class RationalFPS:
    numerator: int
    denominator: int

@dataclass(frozen=True)
class SourceSpec:
    path: str
    kind: Literal["video", "image"]
    virtual_frame_count: int | None

@dataclass(frozen=True)
class TimelineSpec:
    start_frame: int
    end_frame: int | None
    fps: RationalFPS | None

@dataclass(frozen=True)
class CropSpec:
    coordinate_space: Literal["post_rotation_source_pixels"]
    x: int
    y: int
    width: int
    height: int

@dataclass(frozen=True)
class TransformSpec:
    rotation: Literal[0, 90, 180, 270]
    crop: CropSpec

@dataclass(frozen=True)
class PathSpec:
    cache_dir: str

@dataclass(frozen=True)
class OutputSpec:
    profile: Literal["360x640", "720x1080"]
    display_width: int
    display_height: int
    coded_width: int
    coded_height: int
    pixel_format: Literal["YUV420P8"] = "YUV420P8"
    matrix: str = "170m"
    transfer: str = "170m"
    primaries: str = "170m"
    range: Literal["limited", "full"] = "limited"
    final_rotate_180: bool = False

@dataclass(frozen=True)
class RenderJob:
    api_version: Literal[1]
    epoch: int
    track: Literal["loop", "intro"]
    project_root: str
    source: SourceSpec
    timeline: TimelineSpec
    transform: TransformSpec
    output: OutputSpec
    paths: PathSpec
```

`OutputSpec.from_profile()` 只能从 `RESOLUTION_SPECS` 推导；`write_render_job()` 使用 `atomic_write_json` 新建 `job-<epoch>.json`，不能覆盖旧 epoch。export 校验必须拒绝 null fps/end。

字段约束必须在 schema 与 Python 每层一致：所有 object 均 `additionalProperties: false`；FPS 分子/分母为正；source/project/cache 路径为规范化绝对路径；video 的 `virtual_frame_count` 必须为 null，image 必须为正整数；rotation 仅四个方向；crop x/y 非负，width/height 同时为 0 表示完整旋转后画面，否则都为正；timeline start 非负、resolved end 大于 start。图片的 `virtual_frame_count` 表示 trim 前完整合成时间轴，不能用 `timeline.end_frame` 代替。

`load_render_job(path, *, for_export=False)` 负责 UTF-8 JSON、schema、dataclass 和阶段语义校验；其他模块不得直接 `json.loads()` job 后自行猜字段。

- [ ] **Step 5: 为脚本 header 写红灯并实现前 8 KiB 安全解析**

```python
header = parse_script_header_text("""# assetmaker-api: 1
# assetmaker-mode: compatible
# assetmaker-capabilities: source,trim,crop,rotation,resolution,image_loop
# assetmaker-requires: lsmas.LWLibavSource,imwri.Read
# assetmaker-editor-output: 1
import vapoursynth as vs
""")
self.assertEqual(header.mode, "compatible")
self.assertEqual(header.editor_output, 1)
self.assertEqual(header.requires, ("lsmas.LWLibavSource", "imwri.Read"))
```

解析器只读开头连续注释/空行，遇首条 Python 语句停止。拒绝 API 非 1、未知 mode、非法 output、未知 capability、非 `namespace.Function` requirement。compatible 声明 trim/crop/rotation 时必须 `editor_output=1`。

- [ ] **Step 6: 实现可审计的一次性旧配置迁移**

只迁移：

```python
LEGACY_FIELD_MAP = {
    "core.num_threads": "core.num_threads",
    "core.max_cache_size_mb": "core.max_cache_size_mb",
    "extra_plugin_dirs": "plugins.native_plugin_dirs",
}
IGNORED_FILTER_FIELDS = (
    "required_plugins", "image_source_format", "output_format",
    "resampler_kernel", "colour",
)
```

marker 使用旧文件 SHA-256；返回 `MigrationReport(applied, migrated_fields, ignored_fields, source_hash)`。同 hash 第二次 `applied=False`；旧文件不删除，滤镜字段不迁移。

- [ ] **Step 7: 验证并提交 M1**

```powershell
uv run python -m unittest tests.test_vs_runtime_contract tests.test_vs_job_contract tests.test_vs_script_header tests.test_media_packaging -v
uv run python -m compileall config core/vs_runtime build.py tests
uv run python -m unittest discover -s tests -p "test_*.py"
git add config/vs_runtime.py config/vs_runtime.json schemas/vs_runtime.schema.json schemas/vs_job.schema.json core/vs_runtime/__init__.py core/vs_runtime/job.py core/vs_runtime/script_header.py core/vs_runtime/migration.py tests/test_vs_runtime_contract.py tests/test_vs_job_contract.py tests/test_vs_script_header.py tests/test_media_packaging.py build.py
git commit -m "refactor: 拆分 VS 运行配置与渲染作业协议"
```

Expected: 全量绿色；新 runtime JSON 不含滤镜字段；提交无共同作者。

---

### Task 3: M2 — 共享执行 ABI、可信 runner、默认脚本与严格输出契约

**Files:**
- Create: `resources/vapoursynth/python/assetmaker_vs/{__init__,job_api,script_header,executor,contract,display}.py`
- Create: `resources/vapoursynth/assetmaker_runner.vpy`
- Create: `resources/vapoursynth/default_pipeline.vpy`
- Create: `core/vs_runtime/executor.py`, `core/vs_runtime/output_contract.py`
- Create: `tests/fixtures/vs_scripts/raw_valid.vpy`, `compatible_bad_output.vpy`, `prints_and_imports.vpy`
- Create: `tests/helpers/run_vs_contract_case.py`, `tests/test_vs_runner.py`, `tests/test_vs_output_contract.py`, `tests/test_vs_display.py`, `tests/test_default_vpy_pipeline.py`

**Produces:** 两种执行者共用的 `load_job()`、`parse_script_header()`、`execute_user_script()`、`validate_outputs()`、`to_display_clip()`；宿主 adapter 只把纯 dict 转成 frozen dataclass。

- [ ] **Step 1: 为 portable runner 的参数、导入、重载和 stdout 写红灯**

```python
def test_module_search_order_is_deterministic(self):
    self.assertEqual(build_module_search_paths(
        script_path=PROJECT / "pipeline.vpy",
        runtime_dirs=[PROJECT / "third_party"],
    ), (PROJECT, PROJECT / "modules", PROJECT / "third_party", HELPER_ROOT))

def test_deferred_frame_callback_keeps_import_path_and_stdout_isolated(self):
    install_python_stdout(log_sink)
    graph = execute_user_script(...)
    render_frame_that_runs_frameeval_callback()
    self.assertEqual(log_sink.lines, [
        "hello from script", "lazy module imported", "hello from callback"])
    self.assertEqual(protocol_bytes.getvalue(), b"")
    graph.close_after_inflight_drained()

def test_reload_evicts_only_modules_under_script_root(self):
    self.assertEqual(execute_fixture(module_value="first"), "first")
    self.assertEqual(execute_fixture(module_value="second"), "second")
```

真实测试在中文路径 `素材/黍/` 创建 `pipeline.vpy` 与 `modules/marker.py`，通过固定 runner + `--arg` 运行 bundled VSPipe `--info`。

Run: `uv run python -m unittest tests.test_vs_runner -v`

Expected: FAIL，因为 runner/helper 尚不存在。

- [ ] **Step 2: 实现便携共享 executor，不依赖 app/Qt**

`assetmaker_vs` 不得 import `core`、`config` 或 PyQt。执行环境必须与图同寿命，而不是只包住 `exec()`：

```python
def execute_user_script(*, script_path, job_path, api_version, mode,
                        python_module_dirs=()) -> ExecutedGraph:
    script = Path(script_path).resolve(strict=True)
    job = Path(job_path).resolve(strict=True)
    search_paths = ordered_paths(
        script.parent, script.parent / "modules",
        *(Path(p) for p in python_module_dirs), helper_root(),
    )
    evict_modules_under(script.parent)
    importlib.invalidate_caches()
    vs.clear_outputs()
    namespace = {
        "__name__": "__vapoursynth__", "__file__": str(script),
        "assetmaker_job": str(job), "assetmaker_api": str(api_version),
        "assetmaker_script": str(script), "assetmaker_mode": mode,
    }
    environment = ExecutionEnvironment(search_paths)
    environment.activate()
    try:
        code = compile(script.read_bytes(), str(script), "exec")
        exec(code, namespace, namespace)
        return ExecutedGraph(namespace=namespace, environment=environment)
    except BaseException:
        environment.close()
        raise
```

`ExecutedGraph` 持有 namespace、output nodes、脚本根和 `ExecutionEnvironment`。worker 只有在该图的所有 frame future 都收到终态后才调用 `close()`；切换不同脚本根时若旧 future 未结束则等待、超时后重启 worker，不能提前恢复 `sys.path`。VSPipe runner 不恢复环境，直接保持到进程退出。只清理由脚本根目录加载的模块；不得移除 stdlib、第三方模块或 `assetmaker_vs`。runner/worker 必须在进程生命周期开始时安装线程安全 stdout sink；结构化日志按 UTF-8 边界切块，单条正文严格小于 4 MiB 协议上限。固定 runner 仅 bootstrap：

```python
# resources/vapoursynth/assetmaker_runner.vpy
from pathlib import Path
import sys
import vapoursynth as vs

_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_root / "python"))
sys.stdout = sys.stderr
from assetmaker_vs.executor import execute_user_script
from assetmaker_vs.job_api import load_job, runtime_python_dirs_from_env
from assetmaker_vs.script_header import parse_script_header, validate_invocation
from assetmaker_vs.contract import validate_outputs

header = parse_script_header(assetmaker_script)
job = load_job(assetmaker_job, for_export=True)
validate_invocation(header, api_version=assetmaker_api, mode=assetmaker_mode)
graph = execute_user_script(
    script_path=assetmaker_script,
    job_path=assetmaker_job,
    api_version=assetmaker_api,
    mode=assetmaker_mode,
    python_module_dirs=runtime_python_dirs_from_env(),
)
validated = validate_outputs(vs, job, header)
validated.guarded_clip.set_output(0)
```

runner 不调用/修改滤镜；contract 只检查注册结果。Python stdout 在整个 VSPipe 脚本及延迟帧回调期间永久指向 stderr，VSPipe 自身 C 层写出的 Y4M stdout 不受影响。subprocess 必须用参数数组，不拼 shell 字符串。宿主把已校验的 Python 模块目录序列化到 `ASSETMAKER_VS_PYTHON_DIRS_JSON`，把原生插件目录传给官方 `VAPOURSYNTH_EXTRA_PLUGIN_PATH`；runner 不猜 app root，也不重复解析 `vs_runtime.json`。

M1 的 `core.vs_runtime.job/script_header` 在本步骤改为调用共享纯 Python 解析器后再构造 frozen dataclass；不得保留两套字段/错误规则。同一非法 fixture 分别调用 helper 与 core adapter，必须得到相同 field/path/error code。父 unittest 进程只做纯 JSON/dataclass/protocol 断言；凡创建真实 `vs.core`/`BlankClip` 的 case 都由 `tests/helpers/run_vs_contract_case.py` 在全新子进程中执行，并返回结构化 JSON，防止全量 discover 先加载 PyQt 后再初始化 VS 导致 exit 139。

`validate_invocation()` 在任何用户代码执行前要求 header API/mode 与 worker load 或 VSPipe `--arg` 完全一致；项目文件没有可参与比较的 mode。测试故意把 compatible header 以 raw arg 启动，worker 与 VSPipe 必须返回同一 `invocation.mode` 错误。

- [ ] **Step 3: 为 output 0/1 与 VUI 写红灯**

```python
def test_valid_170m_limited_maps_to_x264(self):
    result = validate_output0(yuv420_clip(
        width=384, height=640, fps=(30000, 1001),
        props={"_Matrix": 6, "_Transfer": 6,
               "_Primaries": 6, "_ColorRange": 1}), job_360x640())
    self.assertEqual(result.vui, X264Vui(
        colormatrix="smpte170m", colorprim="smpte170m",
        transfer="smpte170m", range_="tv"))

def test_wrong_format_reports_contract_field(self):
    with self.assertRaises(OutputContractError) as ctx:
        validate_output0(yuv422p10_clip(), job_360x640())
    self.assertEqual(ctx.exception.field, "pixel_format")
    self.assertEqual(ctx.exception.expected, "YUV420P8")
```

覆盖 output 0 缺失/非 `VideoOutputTuple`、`.clip` 非 VideoNode、alpha 非空、`alt_output != 0`、错误 coded size、奇数 YUV420、0 帧/0 fps、resolved job 帧数或 fps 不等、缺/未知 frame props、compatible 要求 output1 但缺失。raw 不读取 output1。output1 同样要求 tuple/VideoNode/无 alpha/alt_output 0，但不套用 output0 的 YUV420/coded-size 契约。另造两帧属性不同的 clip：初始结构校验可完成，但消费第 2 帧必须由逐帧 guard 抛 `OutputContractError`；VSPipe 应非零退出，导出 staging 不得发布。

Run: `uv run python -m unittest tests.test_vs_output_contract -v`

Expected: FAIL，因为 contract 尚不存在。

- [ ] **Step 4: 实现只校验、不偷偷修正的 output contract**

```python
MATRIX_CODES = {"709": 1, "170m": 6}
TRANSFER_CODES = {"709": 1, "170m": 6}
PRIMARIES_CODES = {"709": 1, "170m": 6}
X264_MATRIX = {1: "bt709", 6: "smpte170m"}
X264_TRANSFER = {1: "bt709", 6: "smpte170m"}
X264_PRIMARIES = {1: "bt709", 6: "smpte170m"}
```

`_ColorRange` 与 `_Range` 必须按属性名分别解析，并用 R73 实测固定 0/1 语义；不得合并后套一个匿名规则。`validate_outputs()` 的顺序固定：`vs.get_output(0)` → `VideoOutputTuple.clip`/alpha/alt_output → format/尺寸/fps/frames → 取唯一 `{0, num_frames//2, num_frames-1}` sentinel frames 做快速预检 → 每帧四个色彩 props 必须彼此一致且等于 job expected → 由 expected 生成 VUI → 按 header 以同样 tuple 规则检查 output 1。随后 `guard_output0()` 用官方 `std.ModifyFrame` 包装 output0：selector 对**每一帧**只读检查格式、尺寸和四个属性，合法时直接透传原 frame，非法时抛结构化错误；官方文档明确允许“不修改属性时直接 pass through”：[ModifyFrame](https://www.vapoursynth.com/doc/functions/video/modifyframe.html)。worker 与 VSPipe 都消费该 guarded clip。失败抛 `OutputContractError(field, expected, actual, hint)`，绝不改 clip。

sentinel 只用于加载阶段尽早报错；逐帧 guard 才是实际消费路径的完整防线。它不预渲染整段，也不修改任何像素/属性，因此保持 VS 惰性计算，同时保证编码期间任一帧动态漂移都会中止输出。

执行用户脚本前，`verify_required_callables(vs.core, header.requires)` 逐段解析 `namespace.Function` 并确认最终对象可调用；worker 与 VSPipe runner 必须使用同一实现。不存在的 namespace/function 应在执行用户代码前给出结构化缺插件错误，禁止一端静默、一端延迟失败。

- [ ] **Step 5: 实现独立显示支路与 1%–10000% 缩放**

```python
def to_display_clip(clip, *, viewport, zoom_factor, pan):
    rgb = core.resize.Bicubic(clip, format=vs.RGB24)
    fit = min(viewport[0] / rgb.width, viewport[1] / rgb.height)
    fit_w = max(1, min(viewport[0], round(rgb.width * fit)))
    fit_h = max(1, min(viewport[1], round(rgb.height * fit)))
    if zoom_factor <= 1.0:
        out_w = max(1, round(fit_w * zoom_factor))
        out_h = max(1, round(fit_h * zoom_factor))
        return core.resize.Bicubic(rgb, width=out_w, height=out_h)
    win_w = max(1, min(rgb.width, math.ceil(rgb.width / zoom_factor)))
    win_h = max(1, min(rgb.height, math.ceil(rgb.height / zoom_factor)))
    left = min(max(0, round(pan[0] * rgb.width - win_w / 2)), rgb.width - win_w)
    top = min(max(0, round(pan[1] * rgb.height - win_h / 2)), rgb.height - win_h)
    window = core.std.CropAbs(rgb, width=win_w, height=win_h,
                              left=left, top=top)
    return core.resize.Point(window, width=fit_w, height=fit_h)
```

倍率语义固定：1%=完整画面的 1% fit 尺寸；100%=完整源画面按比例适配 viewport；200%=源宽高各显示约一半；10000%=源宽高各显示约 1/100。保留现有完整范围 `0.01 <= zoom_factor <= 100.0`；viewport 正数；RGB24 不使用 `& ~1`。返回帧保持源宽高比且宽高均不超过 viewport，任何倍率都不得让 4K source 扩大 mmap 槽。测试用 1920×1080→480×270 验证 1%≈5×3、100%=480×270、2x 窗口约 960×540，并用奇数尺寸、1px 窗口和棋盘格证明中心像素不偏移、计算量由 viewport 封顶。

上述纯图测试全部写入 `tests/test_vs_display.py`；M4 的 `tests/test_preview_zoom.py` 只验证 GUI 控件、worker 请求参数和 pan/zoom 状态，不重复实现几何算法。

- [ ] **Step 6: 写可读的内置 compatible `.vpy` 并先固定行为**

脚本头：

```python
# assetmaker-api: 1
# assetmaker-mode: compatible
# assetmaker-capabilities: source,trim,crop,rotation,resolution,image_loop
# assetmaker-requires: lsmas.LWLibavSource,imwri.Read
# assetmaker-editor-output: 1
```

主体必须显式可读。视频路径：source → 保存原始源高度 → rotation → output1 → trim → crop → resize/色彩 → padding → final 180 → output0。图片路径：Read → rotation → 按 `source.virtual_frame_count` Loop 成完整编辑时长 → output1 → trim → crop → resize/色彩 → padding → final 180 → output0。禁止调用宿主生成滤镜字符串；图片 bootstrap 因 `load_image_as_loop()` 已知 fps/时长/virtual frame count，不允许 null timeline。

测试至少覆盖：video/image；视频 bootstrap null 与 resolved job；output1 为完整编辑时间轴且处于旋转后、trim/crop 前；图片 output1 的帧数/FPS 等于 `virtual_frame_count` 而不是 1 帧；图片非零入点仍能得到非空 output0；两个 profile；奇数/越界 crop；四个色彩 props；P7 同一真 709 未打标源裁到 800/718 时 output0 颜色一致。

- [ ] **Step 7: 真实 runner/default pipeline 回归并提交**

```powershell
uv run python -m unittest tests.test_vs_runner tests.test_vs_output_contract tests.test_vs_display tests.test_default_vpy_pipeline -v
uv run python -m unittest tests.test_export_color_roundtrip tests.test_preview_zoom -v
uv run python -m compileall core/vs_runtime resources/vapoursynth/python tests
uv run python -m unittest discover -s tests -p "test_*.py"
git add resources/vapoursynth core/vs_runtime/executor.py core/vs_runtime/output_contract.py tests/helpers/run_vs_contract_case.py tests/fixtures/vs_scripts tests/test_vs_runner.py tests/test_vs_output_contract.py tests/test_vs_display.py tests/test_default_vpy_pipeline.py
git commit -m "feat: 引入自定义 vpy 启动器和输出契约"
```

Expected: 真实 VSPipe 测试未 skip；P7 与 10000% 放大通过；全量绿色；提交无共同作者。

---

### Task 4: M3 — 长度前缀协议、具名帧槽、独立 worker 与统一进程传输

**Files:**
- Create: `core/vs_runtime/{protocol,shared_frame,vs_loader,session,worker_main,worker_process}.py`
- Create: `vs_worker.py`, `gui/workers/vs_worker_client.py`
- Create: `tests/test_vs_worker_protocol.py`, `test_vs_shared_frame.py`, `test_vs_worker_process.py`, `test_vs_worker_client.py`
- Modify: `build.py`, `.github/workflows/build-app.yml`, `tests/test_build_obfuscation.py`, `tests/test_media_packaging.py`

**Produces:** `encode_message()`、`MessageDecoder`、`FrameSlot`、`WorkerProcess`、`SyncVSWorkerProcess`、`VSWorkerClient`、`resolve_worker_command()`、`compute_runtime_fingerprint()`。

- [ ] **Step 1: 为拆包、合包、上限与非法消息写红灯**

```python
def test_decoder_handles_split_header_and_coalesced_messages(self):
    first = encode_message({"type": "ready", "api_version": 1})
    second = encode_message({"type": "log", "message": "中文\n日志"})
    decoder = MessageDecoder()
    self.assertEqual(decoder.feed(first[:2]), [])
    self.assertEqual(decoder.feed(first[2:] + second), [
        {"type": "ready", "api_version": 1},
        {"type": "log", "message": "中文\n日志"},
    ])
```

协议为 4-byte unsigned big-endian + UTF-8 JSON object，正文 1..4 MiB；顶层必须有 string `type`，关联消息必须有正整数 `request_id`。zero/oversize/bad UTF-8/array 均抛 `ProtocolError`。

Run: `uv run python -m unittest tests.test_vs_worker_protocol -v`

Expected: FAIL，因为协议模块尚不存在。

- [ ] **Step 2: 实现增量协议并固定 wire messages**

```json
{"type":"hello","request_id":1,"api_version":1}
{"type":"load","request_id":2,"api_version":1,"track":"loop","epoch":42,"script_path":"...","job_path":"...","bundle_hash":"...","runtime_fingerprint":"...","mode":"compatible"}
{"type":"request_frame","request_id":3,"epoch":42,"index":7,"surface":"final","slot":{"name":"...","generation":9,"capacity":1229760},"display":{"viewport":[480,854],"zoom_factor":100.0,"pan":[0.5,0.5]}}
{"type":"request_plane_digest","request_id":4,"epoch":42,"index":7,"surface":"final"}
{"type":"cancel_epoch","request_id":5,"epoch":42}
{"type":"unload","request_id":6}
{"type":"shutdown","request_id":7}
```

wire 响应只允许 `ready/metadata/frame_ready/frame_discarded/plane_digest/requirement_error/script_error/contract_error/request_error/log`。错误携带 request_id、epoch、error_type、message、traceback；traceback 只进日志/折叠详情。每个 `request_frame` 必须恰好以 `frame_ready`、`frame_discarded` 或 `request_error` 之一终结；三者均回显 `request_id/epoch/slot_name/slot_generation`。`worker_crashed` 不是 wire message，而是共用 `WorkerProcess` 根据 child exit code/status 合成的本地事件。

`load` 必须同时校验：message API/track/epoch 与 job 完全一致；selection API/mode 与 header、job、worker 参数完全一致；`runtime_fingerprint` 与 worker 当场重算结果一致。任一差异都在执行用户代码前返回结构化错误。`request_plane_digest` 只用于诊断/测试：对 output0 的 Y/U/V 每个有效行去除 stride padding 后计算 SHA-256，不把像素放进 JSON。

`metadata` JSON 必须由 `SessionMetadata.from_wire()` 统一解析；同步客户端和 Qt 客户端只能接收该 frozen 类型，不得各自读取裸 dict。`NodeMetadata` 对 output0 的色彩字段必填，对 RGB editor output 可为 null；fps 分子/分母、尺寸和帧数必须为正。红灯测试分别喂缺字段、额外字段、零分母和错误 mode，并要求同一结构化错误。

- [ ] **Step 3: 为 Windows named mmap、stride 和所有权写红灯**

```python
owner = FrameSlot.create(capacity=3 * 5 * 7, generation=9)
peer = FrameSlot.open(owner.descriptor)
expected = np.arange(3 * 5 * 7, dtype=np.uint8).reshape(5, 7, 3)
peer.write_bgr(expected)
actual = owner.read_bgr(width=7, height=5, byte_count=expected.nbytes)
np.testing.assert_array_equal(actual, expected)
self.assertTrue(actual.flags["C_CONTIGUOUS"])
```

实现用 `mmap.mmap(-1, capacity, tagname=name)`，不使用 `multiprocessing.shared_memory`。主进程 owner 创建/关闭；worker peer 只 open/close。读取必须 `.copy()`。worker 按 VS 平面 stride 将 R/G/B 以 `[2,1,0]` 合成连续 BGR；迁移 red/blue/stride/frame-close 断言。

禁止手写帧槽容量。`checked_frame_bytes(width, height, channels=3, max_bytes=256*1024*1024)` 校验正整数、上限和乘法，再返回 `width*height*channels`；480×854 BGR 必须是 1,229,760 bytes。GUI 按 viewport 上界分配，worker 返回实际 `width/height/byte_count`，客户端要求 `byte_count == checked_frame_bytes(actual_w, actual_h, 3) <= capacity` 且 name/generation 与当前 owner 一致后才能映射。一个槽从 request 发出到 terminal response 前绝不复用；`cancel_epoch` 只标记取消，不代表异步 callback 已结束。只有收到该请求的 terminal response，或确认 worker 已死亡，owner 才能回收/提升 generation。确定性测试让旧 epoch callback 延迟到新 epoch 建立后返回，证明它既不能覆盖新槽，也不能提前释放旧槽。

- [ ] **Step 4: 为真实 worker 生命周期和崩溃隔离写红灯**

测试真实 source-mode worker：start → load → metadata → final/editor frame → unload → shutdown。还必须覆盖旧 epoch 丢弃、最大 3 in-flight + coalesce、cancel 后迟到 callback 的 terminal response、脚本/契约错误不杀 worker、`os._exit(23)` 只杀 child、超时 terminate/kill、mmap 释放。所有真实 VS case 在新子进程内运行；unittest 父进程结束后仍断言 `vapoursynth` 不在 `sys.modules`。

```python
frame = client.request_frame(
    epoch=session.epoch, index=0, surface="final",
    viewport=(384, 640), zoom_factor=1.0, pan=(0.5, 0.5),
    timeout_ms=10_000)
self.assertEqual(frame.shape, (640, 384, 3))
self.assertGreater(int(frame.max()), 0)
```

Run: `uv run python -m unittest tests.test_vs_worker_process -v`

Expected: FAIL，因为 worker 尚不存在。

- [ ] **Step 5: 实现 loader/session/worker 命令循环**

```python
@dataclass(frozen=True)
class ScriptSelection:
    script_path: str
    mode: Literal["compatible", "raw"]
    bundle_hash: str
    api_version: int = 1

def resolve_worker_command(app_dir: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(app_dir / "vs_worker.exe")]
    return [sys.executable, str((app_dir / "vs_worker.py").resolve())]
```

worker 启动先保存原始 `sys.stdout.buffer` 作为协议口，再把 Python `sys.stdout` 在整个进程生命周期永久替换为结构化 log writer；不得在单次 exec 后恢复。协议 writer 用锁。这样延迟 `FrameEval/ModifyFrame` callback 的 `print()` 也不会污染协议。load 时再次校验 bundle hash/runtime fingerprint、比对 message/job/header 的 track/mode/API/epoch、校验 required callables、执行脚本、校验 output、持有 `ExecutedGraph` 与 output0/1。frame callback 先执行逐帧 output guard，再将 VS planar RGB24 按 stride 转为连续 packed BGR24，最后写 mmap 并发送 terminal response；不触碰 Qt。旧 graph 的 async future 不强杀，取消后只发 `frame_discarded`。旧图的 future 全部终结前不得关闭其 execution environment。

`compute_runtime_fingerprint()` 对 canonical `vs_runtime` JSON、便携 `vapoursynth.pyd/vapoursynth.dll/portable.vs`、默认 plugin 目录及配置目录中的 `.py/.pyc/.pyd/.dll/.zip/.whl` 做有序 SHA-256；配置或代码变化后下一次 load 必须重启 worker。它不递归 LUT/模型/图片等非代码资源，UI/文档不得把 fingerprint 表述成完整可复现性保证。退役 job 只有在对应 graph 的 in-flight 请求全部 terminal 后删除；当前 session 和 export staging 的 job 保留到 unload/export 完成，clear/close 清理 session cache。

- [ ] **Step 6: 实现唯一 Popen 传输和 QObject 包装**

`WorkerProcess` 是唯一 child 启动和协议实现：使用绝对命令数组、`shell=False`、binary `stdin/stdout/stderr=PIPE`；Windows 固定 `creationflags=subprocess.CREATE_NO_WINDOW`。stdout reader thread 喂 decoder，stderr reader thread 持续 drain，waiter thread 产生 exit event；writer 有锁，request table/slot state 有锁。`SyncVSWorkerProcess` 只在其上增加 `queue.Queue` 等待接口，不再启动第二种 child。

`VSWorkerClient(QObject)` 包装同一个 `WorkerProcess`；reader/waiter 线程不直接读写 QWidget/QTimer，只发 signal，Qt 的跨线程 Auto Connection 排队回 GUI thread。信号固定为：

```python
ready = pyqtSignal()
metadata_ready = pyqtSignal(int, object)
frame_ready = pyqtSignal(int, int, object)
request_failed = pyqtSignal(int, str, str)
request_timed_out = pyqtSignal(int, int)  # request_id, epoch
worker_crashed = pyqtSignal(str)
log_received = pyqtSignal(str, str)
```

startup/frame/shutdown timer 必须绑定 request_id/epoch/worker generation，旧 timer 不得终止新 worker。frame timeout 只发 `request_timed_out`，不立即 kill；`continue_wait(request_id)` 延长同一请求 deadline，`terminate_and_restart()` 才结束 worker。shutdown timeout 无用户交互，可自动升级 terminate→kill。测试使用 fake clock 证明“继续等待”不会重复 frame request，旧 timeout 不会杀新 epoch。还要断言从 reader thread 发出的消息只在 GUI thread 的 slot 中更新 widget state。

- [ ] **Step 7: 打包第二 executable 并锁定冻结产物**

```python
executables = [
    Executable(script=MAIN_SCRIPT, base=base,
               target_name=f"{PROJECT_NAME}.exe", icon=ICON_FILE),
    Executable(script="vs_worker.py", base=None,
               target_name="vs_worker.exe"),
]
```

worker 使用 console subsystem (`base=None`) 保留标准协议管道，但父进程始终通过 `CREATE_NO_WINDOW` 启动，所以不创建可见控制台。CI 构建后必须由与 GUI 相同的 `WorkerProcess` 启动冻结 `vs_worker.exe --self-test`，完成双向 hello/长消息/Unicode/exit 0，并断言启动参数实际包含 Windows `CREATE_NO_WINDOW`；禁止直接双击或裸运行来替代该验收。构建检查必须包含 worker、runtime、runner、default pipeline、helper。`gui` 仍不得进入 `OBFUSCATABLE_ENTRIES`。

- [ ] **Step 8: 验证并提交 M3**

```powershell
uv run python -m unittest tests.test_vs_worker_protocol tests.test_vs_shared_frame tests.test_vs_worker_process tests.test_vs_worker_client tests.test_build_obfuscation tests.test_media_packaging -v
uv run python -m compileall core/vs_runtime gui/workers/vs_worker_client.py vs_worker.py build.py tests
uv run python -m unittest discover -s tests -p "test_*.py"
git add core/vs_runtime/protocol.py core/vs_runtime/shared_frame.py core/vs_runtime/vs_loader.py core/vs_runtime/session.py core/vs_runtime/worker_main.py core/vs_runtime/worker_process.py vs_worker.py gui/workers/vs_worker_client.py tests/test_vs_worker_protocol.py tests/test_vs_shared_frame.py tests/test_vs_worker_process.py tests/test_vs_worker_client.py build.py .github/workflows/build-app.yml tests/test_build_obfuscation.py tests/test_media_packaging.py
git commit -m "feat: 新增独立 VapourSynth 预览工作进程"
```

Expected: crash fixture 只结束 child；父测试继续；取消请求不复用未终结 slot；冻结 worker 管道双向可用且由 `CREATE_NO_WINDOW` 启动；无 mmap handle 警告；父进程未加载 VS；全量绿色。

---

### Task 5: M4 — 预览切换到 worker，移除 Qt 主进程 VS core

**Files:**
- Modify: `gui/widgets/video_preview.py`, `gui/main_window.py`
- Modify: `core/video_processor.py`, `core/optimized_processor.py`, `core/media_tools.py`
- Modify: `main.py`, `tests/qt_harness.py`
- Create: `tests/test_preview_worker_integration.py`
- Rewrite: `tests/test_preview_async_load.py`, `tests/test_preview_zoom.py`
- Modify: `tests/test_gui_thread_safety.py`, `tests/test_vs_frame_probe.py`

**兼容接口：** 保持 `video_loaded(int,float)`、`frame_changed(int)`、`load_failed(str)`、`cropbox_changed(int,int,int,int)`、`rotation_changed(int)`；新增 `set_render_context()`、`set_timeline_range()`、`flush_render_job()` 与 `current_render_session()`。

- [ ] **Step 1: 写“Qt 主进程不得加载 VS”的红灯**

```python
def test_qt_process_does_not_import_or_prewarm_vs(self):
    self.assertNotIn("vs_engine.prewarm", Path("main.py").read_text("utf-8"))
    self.assertNotIn("vs_engine.prewarm", Path("tests/qt_harness.py").read_text("utf-8"))
    self.assertNotIn("from core import vs_engine",
                     Path("gui/widgets/video_preview.py").read_text("utf-8"))
```

另起 offscreen 子进程先 import PyQt、构造 widget，再断言 `vapoursynth` 不在 `sys.modules`。

Run: `uv run python -m unittest tests.test_gui_thread_safety -v`

Expected: FAIL，旧 prewarm/in-process backend 仍存在。

- [ ] **Step 2: 用 fake worker 固定 bootstrap、surface/index 和 epoch**

```python
job = load_render_job(Path(self.client.last_session.job_path))
self.assertIsNone(job.timeline.end_frame)
self.assertIsNone(job.timeline.fps)

self.client.emit_metadata(
    epoch,
    output0=meta(frames=120, fps=(30, 1), size=(384, 640)),
    editor=meta(frames=300, fps=(30000, 1001), size=(1920, 1080)))
self.assertEqual(self.widget.total_frames, 300)

self.widget.set_timeline_range(20, 81)
self.widget.current_frame_index = 27
self.widget.set_preview_mode(True)
self.widget._request_current_frame()
self.assertEqual((self.client.last_request.surface,
                  self.client.last_request.index), ("final", 7))
```

上例的第二个参数是 **exclusive end**；UI 若把包含式出点 80 传入，主窗口必须先转换为 81。测试同时断言 edit=`editor,27`；raw 总是 final；旧 epoch metadata/frame/error 丢弃；crop/rotation 100ms 内只发送最后 job；显式 export flush 取消 debounce 并产出 resolved job。

- [ ] **Step 3: 替换 widget 后端并保留 public API**

```python
@dataclass(frozen=True)
class PreviewRenderContext:
    project_root: str
    track: Literal["loop", "intro"]
    selection: ScriptSelection
    cache_dir: str
```

每个 preview widget 懒启动一个 `VSWorkerClient`，持有一个当前 `RenderSession`。无 context 的旧项目使用内置 default pipeline。`load_video()` 仅校验路径、epoch++、写 bootstrap job、worker.load 并立即返回 True。图片因 fps/时长已知可直接 resolved。

compatible 从 output1 metadata 得完整源 fps/frames/尺寸，raw 从 output0；然后初始化 timeline/crop 并 emit 原 `video_loaded`。compatible 声明 output1 却没返回时必须 `load_failed`，不得退回隐藏内置图。

`set_timeline_range(start, end_exclusive)` 是主窗口向 widget 传递唯一 job 边界的入口；widget 内部只缓存 `_timeline_start/_timeline_end_exclusive` 用于 job 和 surface index 映射。现有 Timeline/`EditorTrackState.out_frame` 是**包含式**，现有 `_get_trim_bounds()` 已转换为 `(start, out + 1)`；主窗口每次更新 `_loop_in_out/_intro_in_out` 后必须调用 `_get_trim_bounds()` 再把 exclusive 值传给 widget。禁止直接把 UI out_frame 写入 job，也禁止新增第四份状态。`flush_render_job()` 立即取消 debounce、写 resolved job 并返回新 `RenderSession`；metadata 未解析时抛可显示错误。

边界测试必须固定：全范围 UI `(0, total-1)` → job `[0,total)`；单帧 UI `(7,7)` → job `[7,8)`；UI `(20,80)` → job `[20,81)`，final output 帧数 61；保存/重开项目仍存包含式 80，不被迁移成 81。

- [ ] **Step 4: 重接播放、seek、capture、缩放与生命周期**

```python
def _frame_request_target(self) -> tuple[str, int]:
    if self._selection.mode == "raw":
        return "final", min(max(0, self.current_frame_index),
                            self._output0_frames - 1)
    source_index = min(max(self._timeline_start, self.current_frame_index),
                       self._timeline_end_exclusive - 1)
    if self._preview_mode:
        return "final", source_index - self._timeline_start
    return "editor", source_index
```

QTimer 仍是播放时钟；每 tick 只发 coalesced request。内部帧率始终保存 `RationalFPS`，仅在既有 `video_loaded(int,float)` signal 边界转换为 float；禁止从 29.97 float 反推 30000/1001。capture 发一次非 coalesce 请求并回调 BGR 副本。缩放参数进入 worker 私有 display branch，保留 1%–10000%。切换到 final surface 时若当前 source index 在 trim 外，先夹到最近边界、更新 `current_frame_index` 并发出 source-space `frame_changed`，再减 start 请求 output0；切回 editor 仍用 source index。clear/close：stop timers → epoch++/cancel → 等各 slot terminal 或确认 worker 死亡 → unload/清理退役 job；worker crash 显示“渲染进程已退出”，项目仍可保存并提供“重启渲染”。frame timeout 显示非模态提示，至少提供“继续等待”和“终止并重启”；继续等待延长同一 request_id，终止才重启 worker。

- [ ] **Step 5: 删除 prewarm，迁移 metadata 兼容 API**

删除 `main.py`/harness prewarm。`missing_for_preview()` 只检查 worker、VS runtime、runner/default/helper 文件，不 import VS。

`probe_video_info(path)` 使用短生命周期 `SyncVSWorkerProcess` + 内置 pipeline bootstrap，读取 editor metadata 后 shutdown；删除 `MetadataProbeWorker`。`OptimizedVideoProcessor` 保留 public 调用。主窗口为 loop/intro 设置不同 `track` context。

- [ ] **Step 6: 真实 GUI/worker 冒烟、全量回归并提交**

```powershell
uv run python -m unittest tests.test_preview_async_load tests.test_preview_worker_integration tests.test_preview_zoom tests.test_gui_thread_safety tests.test_vs_frame_probe -v
uv run python -m compileall main.py core/video_processor.py core/optimized_processor.py core/media_tools.py gui/widgets/video_preview.py gui/main_window.py tests
uv run python -m unittest discover -s tests -p "test_*.py"
git add gui/widgets/video_preview.py gui/main_window.py core/video_processor.py core/optimized_processor.py core/media_tools.py main.py tests/qt_harness.py tests/test_preview_worker_integration.py tests/test_preview_async_load.py tests/test_preview_zoom.py tests/test_gui_thread_safety.py tests/test_vs_frame_probe.py
git commit -m "refactor: 预览切换到用户自定义 vpy"
```

真实用例必须覆盖中文路径 mp4、output1 metadata、首帧非黑、精确 seek、播放前进、1%/100%/10000% zoom、trim 外切换 final 自动夹取、30000/1001 不经 float 往返、capture、clear 后旧帧不回写、worker 正常退出，并断言父进程未 import `vapoursynth`。

---

### Task 6: M5 — 导出改用同一 runner/script/job，并从 output 0 派生编码契约

**Files:**
- Modify: `core/media_pipeline.py`, `core/export_service.py`, `core/media_tools.py`, `gui/main_window.py`
- Create: `tests/test_vspipe_render_request.py`, `tests/test_export_vpy_session.py`, `tests/test_worker_vspipe_parity.py`
- Modify: `tests/test_media_pipeline.py`, `tests/test_media_encode_integration.py`, `tests/test_preview_export_parity.py`, `tests/test_export_integrity.py`

**Produces:** `VSPipeRenderRequest`、显式 `X264Vui` 编码命令、冻结的导出 `RenderSession`。

- [ ] **Step 1: 为 VSPipe 参数数组与显式 VUI 写红灯**

```python
@dataclass(frozen=True)
class VSPipeRenderRequest:
    runner_path: str
    script_path: str
    job_path: str
    api_version: int
    mode: Literal["compatible", "raw"]
```

```python
cmd = build_vspipe_command(VSPIPE, request)
self.assertEqual(cmd, [
    VSPIPE, "-c", "y4m", "-p",
    "--arg", f"assetmaker_job={request.job_path}",
    "--arg", f"assetmaker_script={request.script_path}",
    "--arg", "assetmaker_api=1",
    "--arg", f"assetmaker_mode={request.mode}",
    request.runner_path, "-",
])
```

`build_x264_command()` 去掉色彩默认参数；缺 `X264Vui` 必须 TypeError/显式错误。中文、空格、`&`、单引号路径均作为单独 argument 原样保留。

Run: `uv run python -m unittest tests.test_vspipe_render_request tests.test_media_pipeline -v`

Expected: FAIL，当前函数仍接受生成 `.vpy` 路径并有独立 VUI 默认。

- [ ] **Step 2: 实现 runner 命令和显式 VUI 编码器接口**

```python
def encode_vpy_to_mp4(self, request: VSPipeRenderRequest,
                      output_path: str, fps: RationalFPS, *,
                      vui: X264Vui, progress_cb=None,
                      should_cancel=None) -> None:
    vspipe_cmd = build_vspipe_command(self.tc.vspipe, request)
    x264_cmd = build_x264_command(
        self.tc.x264, raw_path, vui=vui, ...)
```

不允许从 `VSRuntimeConfig`、全局默认或文件扩展名推导滤镜/VUI。fps 继续用 rational。临时 raw/mp4/job 在失败/取消时清理。

- [ ] **Step 3: 为导出冻结/hash 竞态写红灯**

`tests/test_export_vpy_session.py` 断言：

- `_collect_export_data()` 先 `preview.flush_render_job()`，得到 resolved job；
- ExportTask 持有 immutable `RenderSession`，不持有可变 widget/config；
- worker 预检前 bundle hash 或 runtime fingerprint 不符立即失败；
- 预检后、VSPipe 前或编码中脚本 `.vpy/.py` 改变，整包失败且 staging 不发布；
- 不复制脚本根目录，因为用户脚本可引用非代码资源；只冻结 hash 与绝对根路径；
- loop/intro 各自保存 track、timeline、source 和 script selection。

- [ ] **Step 4: 在 ExportWorker 内预检 output 0 并冻结编码参数**

流程必须是：

1. export UI flush 两个 preview job；
2. 复制 job 到导出 staging，记录 script canonical path + recursive code hash + runtime fingerprint；
3. ExportWorker 后台线程启动短生命周期 `SyncVSWorkerProcess`；
4. load 同一 session，取得 `ValidatedOutput`/VUI；
5. 核对 output0 coded size/fps/frames 与 job；
6. 再核对 bundle hash 与 runtime fingerprint；
7. VSPipe runner 执行同一 script/job，x264 使用预检 VUI；
8. 编码后再次核对两种 hash；
9. 全部成功才原子发布 staging；否则清理。

VSPipe runner 自身仍重复 output contract 校验，避免 TOCTOU 后产生不合法 Y4M。任何 script/contract error 通过现有 export error UI 显示 field、expected、actual、hint。

- [ ] **Step 5: 迁移 simulator image bake 和所有临时脚本调用**

`_bake_loop_image_for_simulator` 构造同一 `RenderJob` + default/user selection，通过 M5 导出 API生成 mp4；不得保留 `write_vpy_script()` 旁路。全仓 `rg "write_vpy_script|VpyScriptBuilder|build_export_graph"` 在 M5 后只允许旧测试/M7 待删代码命中，不允许生产调用命中。

- [ ] **Step 6: 证明 worker 与 VSPipe 同脚本同 job 一致**

`tests/test_worker_vspipe_parity.py`：同一中文路径脚本/job，先用 worker 的诊断 `request_plane_digest` 对 output0 frame N 的 Y/U/V 有效行（去 stride padding）做 SHA-256；再让 VSPipe runner 输出 Y4M、解析同帧有效平面并比较三组 digest，形成无色彩转换舍入的 byte-exact 证据。另将 worker 显示 BGR 与真实编码后解码 RGB 做有界容差测试，断言 geometry/色彩与 container/VUI 标签；不得把不同转换器的 RGB 舍入差异误写成逐字节要求。

覆盖 compatible 和 raw、两个 profile、image/video、crop/rotate/trim、30000/1001、脚本打印日志、脚本中途变更、runtime 配置/插件代码变化、取消与临时文件清理。额外脚本让第二帧色彩属性漂移：VSPipe 必须在消费该帧时由逐帧 guard 失败，x264/mux 不得发布成品。

- [ ] **Step 7: 验证并提交 M5**

```powershell
uv run python -m unittest tests.test_vspipe_render_request tests.test_export_vpy_session tests.test_worker_vspipe_parity tests.test_media_pipeline tests.test_media_encode_integration tests.test_preview_export_parity tests.test_export_integrity -v
uv run python -m compileall core/media_pipeline.py core/export_service.py core/media_tools.py gui/main_window.py tests
uv run python -m unittest discover -s tests -p "test_*.py"
git add core/media_pipeline.py core/export_service.py core/media_tools.py gui/main_window.py tests/test_vspipe_render_request.py tests/test_export_vpy_session.py tests/test_worker_vspipe_parity.py tests/test_media_pipeline.py tests/test_media_encode_integration.py tests/test_preview_export_parity.py tests/test_export_integrity.py
git commit -m "refactor: 导出执行与预览相同的 vpy"
```

Expected: 同帧 parity 通过；所有真实编码未 skip；脚本变更不会发布部分包；提交无共同作者。

---

### Task 7: M6 — 项目/全局脚本、能力声明、本机信任与旧项目兼容

**Files:**
- Create: `core/vs_runtime/trust.py`
- Create: `gui/dialogs/vs_script_trust_dialog.py`
- Create: `gui/widgets/vs_script_panel.py`
- Modify: `config/epconfig.py`, `gui/main_window.py`, `gui/widgets/video_preview.py`
- Create: `tests/test_vs_script_trust.py`, `tests/test_vs_script_panel.py`, `tests/test_vs_project_compatibility.py`
- Modify: `tests/test_epconfig_contract.py`, `tests/test_export_integrity.py`

**边界：** 信任是本机 UX 防线，不是沙箱；项目文件只存脚本来源与相对引用，不存 mode/API/hash/“已信任”。mode/API 只来自脚本 header。

- [ ] **Step 1: 为递归 bundle hash 与 trust store 写红灯**

hash 输入固定为脚本根目录内递归所有 `.vpy`/`.py`：每项按 POSIX 相对路径 UTF-8 字节排序，依次哈希 `path + NUL + 8-byte length + bytes`。拒绝脚本根之外的 symlink/reparse escape、无法 canonicalize 的路径和大小写碰撞。

```python
before = compute_script_bundle(script)
(root / "modules" / "helper.py").write_text("VALUE=2", "utf-8")
after = compute_script_bundle(script)
self.assertNotEqual(before.sha256, after.sha256)
self.assertEqual(after.files, ("modules/helper.py", "pipeline.vpy"))
```

测试必须证明：只改非代码资源不改变 code hash；新增/删除/改名 `.py/.vpy` 改变；信任库位于 `%APPDATA%/ArknightsPassMaker/vapoursynth/trust.json`；项目 JSON 与导出包不含 hash/trusted 字段。

Run: `uv run python -m unittest tests.test_vs_script_trust -v`

Expected: FAIL，因为 trust 模块不存在。

- [ ] **Step 2: 实现三种来源与信任规则**

```python
@dataclass(frozen=True)
class ScriptReference:
    source: Literal["builtin", "global", "project"]
    path: str
```

- builtin：随包、hash 由构建清单固定，自动信任；
- global：用户在本机文件选择器中主动选择即构成授权；显示一次风险说明，但不使用项目式逐 hash trust gate。每次 session/导出仍计算 hash 以检测竞态；
- project：每个新 bundle hash 都必须明确确认；任何代码文件变化重新确认；
- trust dialog 显示 canonical root、主脚本、代码文件列表、SHA-256、任意 Python/文件/网络/进程权限风险；按钮为“信任并运行”“取消”，不得默认勾选。
- 提示中明确：executor 只隔离普通 `print()`；可信脚本直接写文件描述符 1（如 `os.write(1, ...)`）仍可能破坏 worker 协议并触发 worker 重启，这不是权限沙箱可防止的行为。

项目脚本在运行前和 worker load 前都重算 hash。hash 变化时旧 worker session 失效，UI 回到未信任状态。全局脚本变化时 session 失效并要求用户重载，但不弹项目式信任框。

只改非代码资源不会改变 bundle hash，这是明确残余边界：LUT/模型/图片/JSON 不受 trust/TOCTOU 冻结保证；M8 文档要求用户渲染时不要修改它们。未来若要求完全复现，新增脚本显式资源清单，不能扩大为静默哈希整个项目。

- [ ] **Step 3: 在 EPConfig project-only editor 区保存脚本引用**

```python
@dataclass
class VSScriptState:
    source: str = "builtin"
    path: str = ""

@dataclass
class EditorState:
    # existing loop/intro state
    vapoursynth: VSScriptState = field(default_factory=VSScriptState)
```

三种持久化语义固定：builtin=`source:"builtin", path:""`；global=`source:"global", path:""`，实际绝对路径只从本机 `vs_runtime.user.json::scripts.global_script_path` 解析；project=`source:"project", path:"vapoursynth/pipeline.vpy"`，必须是 project-relative 且 canonical 后仍在项目根内。package export (`normalize_paths=True`) 完全剥离 `editor`，因此不会把脚本引用或信任状态写入设备 `epconfig.json`。旧项目缺字段时自动 builtin，解析内置脚本 header 得到 compatible，不标记 dirty。选择 global 时 GUI 原子更新 user override；项目不得保存该绝对路径。

- [ ] **Step 4: 实现脚本面板和 capability-driven UI**

面板显示来源、路径，以及从 header 只读解析出的 mode、API、capabilities、requires、hash/信任、重载/打开目录。它不是完整 VPY IDE，不提供任意代码编辑器。测试必须证明项目 JSON 没有 mode/API，`ScriptSelection.mode/api_version` 只能由 header 构造；load/`--arg` 若与 header 不一致，在执行前失败。

compatible capabilities 决定控件：没有 `crop` 就禁用裁剪；没有 `rotation` 禁用旋转；没有 `trim` 禁用入出点；没有 `resolution` 时 profile 仍作为**输出验证目标**可选择，但提示“脚本须自行适配”。raw mode 始终只显示 output0，禁用 trim/crop/rotation 编辑；保留 profile 选择器、播放、seek、zoom、capture。改变 raw profile 只更新下一份 job 的 expected coded size，不让宿主追加缩放。

控件禁用必须有 tooltip：“当前 `.vpy` 未声明该能力/原始模式由脚本完全控制”。不得暗中把禁用参数应用到 output0。

- [ ] **Step 5: 把信任门放到任何媒体执行之前**

`_apply_project_config()` 主进程顺序固定：解析 script reference → canonicalize/bundle hash → parse header → check local trust → 建 preview context → worker load。主进程不得为 requirements 加载 VS；worker 在执行任何用户代码前调用共享 `verify_required_callables()`，缺失时返回 `requirement_error`。取消信任时项目仍打开，但媒体 preview 显示“脚本未获信任”，导出按钮禁用；不能先执行再询问。

全局脚本不存在/损坏时显示路径和原因，不静默回 builtin。旧项目无引用时明确使用 builtin，不弹项目信任框。

- [ ] **Step 6: 验证信任/UI/兼容并提交 M6**

```powershell
uv run python -m unittest tests.test_vs_script_trust tests.test_vs_script_panel tests.test_vs_project_compatibility tests.test_epconfig_contract tests.test_export_integrity -v
uv run python -m compileall core/vs_runtime/trust.py config/epconfig.py gui/dialogs/vs_script_trust_dialog.py gui/widgets/vs_script_panel.py gui/main_window.py gui/widgets/video_preview.py tests
uv run python -m unittest discover -s tests -p "test_*.py"
git add core/vs_runtime/trust.py gui/dialogs/vs_script_trust_dialog.py gui/widgets/vs_script_panel.py config/epconfig.py gui/main_window.py gui/widgets/video_preview.py tests/test_vs_script_trust.py tests/test_vs_script_panel.py tests/test_vs_project_compatibility.py tests/test_epconfig_contract.py tests/test_export_integrity.py
git commit -m "feat: 添加 vpy 脚本选择与本机信任机制"
```

人工 offscreen/headed 检查：新项目 builtin 可直接运行；项目脚本首次弹窗；改 helper 后重新弹；取消不执行；raw 控件正确禁用；中文路径正常。

---

### Task 8: M7 — 删除双图、旧 VS 配置与主进程绑定

**Files:**
- Delete: `core/vs_graph.py`, `core/vs_script.py`, `core/vs_engine.py`, `core/vs_player.py`, `core/vs_frame.py`
- Delete: `config/vsconfig.py`, `config/vsconfig.json`, `schemas/vsconfig.schema.json`
- Rewrite/Delete: `tests/test_vpy_golden.py`, `tests/test_vs_graph_player.py`, `tests/test_vs_engine.py`, `tests/test_vsconfig*.py`
- Rewrite: `tests/test_preview_export_parity.py`, `tests/test_media_packaging.py`
- Modify: `core/media_tools.py`, `build.py`, `.github/workflows/build-app.yml`
- Create: `tests/test_vs_architecture_boundaries.py`

- [ ] **Step 1: 先写源树边界红灯**

```python
FORBIDDEN_FILES = (
    "core/vs_graph.py", "core/vs_script.py", "core/vs_engine.py",
    "core/vs_player.py", "core/vs_frame.py", "config/vsconfig.py",
    "config/vsconfig.json", "schemas/vsconfig.schema.json",
)

def test_no_main_process_vapoursynth_import(self):
    for path in MAIN_PROCESS_PYTHON_FILES:
        text = path.read_text("utf-8")
        self.assertNotRegex(text, r"(^|\n)\s*(import vapoursynth|from vapoursynth)")
```

另断言生产代码不出现 `build_export_graph/write_vpy_script/VpyScriptBuilder/resampler_kernel/image_source_format`；允许资源目录的用户脚本 `import vapoursynth` 和 worker loader。

Run: `uv run python -m unittest tests.test_vs_architecture_boundaries -v`

Expected: FAIL，因为旧文件仍存在。

- [ ] **Step 2: 将旧测试语义迁到新架构后再删除旧文件**

- `test_vpy_golden.py` 的“宿主生成字符串字节不变”删除，替换为 runner 参数/default script 行为与 output contract；
- `test_vs_graph_player.py` 的双图 parity 删除，替换为同脚本同 job 的 worker↔VSPipe parity；
- `test_vs_engine.py` 的 in-process loader/cache 删除，保留为 worker loader/plugin callable 测试；
- `test_vsconfig*` 迁到 runtime/job/header/migration；
- `test_preview_export_parity.py` 只比较 worker output0、VSPipe output0 和真实编码 decoded frame。

只有新测试已覆盖旧行为后才能删除文件。

- [ ] **Step 3: 删除生产旁路和旧打包项**

删除旧模块、imports、fallback 和 old config package entry。迁移器仍可读取用户安装目录中存在的旧 JSON，但仓库不再分发该文件。`media_tools` 只检查 worker/runtime/runner/helper/plugin callable；不加载旧 filter config。

检查：

```powershell
rg -n "vs_graph|vs_script|vs_engine|vs_player|vs_frame|config\.vsconfig|resampler_kernel|image_source_format" main.py config core gui build.py .github tests
```

Expected: 只命中新迁移测试中的字符串白名单和历史文档，不命中生产 import/call。

- [ ] **Step 4: 全量回归并提交 M7**

```powershell
uv run python -m unittest tests.test_vs_architecture_boundaries tests.test_vs_runner tests.test_vs_output_contract tests.test_vs_worker_process tests.test_preview_export_parity tests.test_media_packaging tests.test_build_obfuscation -v
uv run python -m compileall main.py config core gui utils _mext build.py tests
uv run python -m unittest discover -s tests -p "test_*.py"
git add -- core/media_tools.py build.py .github/workflows/build-app.yml tests/test_vs_architecture_boundaries.py tests/test_preview_export_parity.py tests/test_media_packaging.py
git add -u -- core/vs_graph.py core/vs_script.py core/vs_engine.py core/vs_player.py core/vs_frame.py config/vsconfig.py config/vsconfig.json schemas/vsconfig.schema.json tests/test_vpy_golden.py tests/test_vs_graph_player.py tests/test_vs_engine.py tests/test_vsconfig_contract.py tests/test_vsconfig_wiring.py
git status --short
git diff --cached --name-status
git commit -m "refactor: 删除双图实现和旧 vsconfig"
```

在提交前逐项检查 `git status`，禁止带入 `config/user_settings.json`、日志、构建产物、媒体二进制或本地规划文件。

---

### Task 9: M8 — 更新知识库、用户文档、构建验证与最终验收

**Files:**
- Modify: `docs/vapoursynth-kb/INDEX.md`
- Modify: `docs/vapoursynth-kb/01-colour-range-props.md`, `02-resize-semantics.md`, `03-geometry-filters.md`, `04-trim-loop-zero-length.md`, `05-plugin-autoload-portable.md`, `06-vspipe-cli.md`, `07-frame-lifetime-threading.md`, `08-version-upgrade-notes.md`, `09-plugin-ecosystem.md`, `10-research-method.md`, `11-preview-zoom.md`, `12-field-hazards.md`
- Create: `docs/vapoursynth-kb/13-user-vpy-abi.md`, `14-worker-protocol.md`, `15-output-contract.md`, `16-script-trust.md`
- Modify: `docs/VS_DECOUPLING.md`, `README.md`, `docs/USER_MANUAL.md`
- Create: `tests/test_vs_docs_contract.py`
- Modify: `tests/test_source_encoding.py`, `tests/test_media_packaging.py` as needed
- Do not modify: top release version in `docs/CHANGELOG.md`

- [ ] **Step 1: 先写文档契约检查**

`tests/test_vs_docs_contract.py` 断言：INDEX 链接 13–16 且所有本地链接存在；README/USER_MANUAL 链接用户 `.vpy` 说明；文档出现完整脚本头、四个 VSPipe args、output0/1、compatible/raw、信任模型、插件路径；不存在“编辑器仍自动生成 `.vpy`”“主进程 prewarm”“resampler_kernel 配置”等过时表述。`test_media_packaging.py` 只负责构建产物，不混入文档语义。

- [ ] **Step 2: 更新分层索引，避免每次全库读取**

`INDEX.md` 按任务路由：

| 任务 | 必读条目 |
|---|---|
| 写/改用户脚本 | 03、04、13、15 |
| 排查插件 | 05、09、13 |
| 排查色彩 | 01、02、15 |
| 排查预览/线程 | 07、10、11、14 |
| 排查信任/项目脚本 | 13、16 |
| 排查导出/VSPipe | 06、13、15 |
| 版本/API 升级 | 08、10、13、15 |

每篇首部写“适用范围/不适用范围/权威来源/本仓验证命令/最后验证版本”，避免重复全文。

- [ ] **Step 3: 写用户 `.vpy` ABI 与完整模板**

`13-user-vpy-abi.md` 必须解释：

- 宿主只通过 globals 传 `assetmaker_job/api/script/mode`；编辑参数在 UTF-8 `job.json`，不是拼接 Python；
- job 的 track/source/timeline/crop/output/paths 字段与单位；bootstrap null 只允许预览首次；
- compatible output1 坐标/时间轴语义；raw 只用 output0；
- 相邻 `modules/`、runtime Python dirs、helper 的 import 顺序；
- 用户可以完全替换滤镜链，但 output0 必须满足设备契约；
- 一份可复制的 compatible 模板和一份最小 raw 模板。

脚本组织示例可参考 [VCB-Studio 公开教程](https://guides.vcb-s.com/) 与其[源仓库](https://github.com/TKMYing/VCB-Studio-guides)，但 API/行为结论必须回链 VapourSynth 官方文档或本仓 R73 探针；不得把社区滤镜偏好写成强制契约。

- [ ] **Step 4: 写 worker、输出契约、信任和故障排查**

`14-worker-protocol.md` 记录长度前缀、消息类型、epoch、mmap、崩溃/重启；`15-output-contract.md` 记录 YUV420P8/coded size/fps/frame props/VUI 和错误例子；`16-script-trust.md` 明确任意 Python 风险、递归 hash、三种来源、本机存储和非沙箱边界。

故障排查必须包含：普通 `print()` 会进入结构化日志；直接写 stdout 文件描述符属于协议破坏，宿主将终止并重启 worker；用户应改用 `print()`/VS logger，而不是向 fd 1 写原始字节。

`USER_MANUAL` 加用户操作路径：复制内置脚本 → 选择全局/项目脚本 → 查看能力 → 信任 → 预览 → 导出。README 只保留概览和指向 KB/手册的链接。

- [ ] **Step 5: 最终自动验证**

```powershell
uv run python -m compileall main.py config core gui utils _mext build.py tests resources/vapoursynth/python
uv run python -m unittest tests.test_vs_docs_contract tests.test_source_encoding tests.test_media_packaging -v
uv run python -m unittest discover -s tests -p "test_*.py"
$placeholderPattern = @(
    ('TO' + 'DO'), ('TB' + 'D'), ('implement' + ' later'),
    ('fill in' + ' details'), ('Similar to' + ' Task'),
    ('add' + ' appropriate'), ('write tests' + ' for')
) -join '|'
rg -n $placeholderPattern docs/superpowers/plans/2026-09-02-vapoursynth-user-vpy-migration.md
rg -n "import vapoursynth|from vapoursynth|vs_engine\.prewarm|write_vpy_script|build_export_graph|resampler_kernel" main.py config core gui
git diff --check
```

Expected: compileall/test 全绿；placeholder scan 无命中；生产源扫描只允许 worker/runtime 预期白名单；diff-check 无错误。记录总测试数和真实媒体测试 skip 数。

- [ ] **Step 6: GitHub 构建门与手动冒烟**

本地不要求执行完整 cx_Freeze 构建；提交/推送前由 GitHub Actions 构建。CI 必须验证 `vs_worker.exe --self-test`、runner/default/helper 存在，随后执行 source-mode 全量测试和一个冻结 worker smoke。

手动源码运行检查：

1. `uv run python main.py` 可见 GUI；
2. 打开旧项目，builtin compatible 自动加载；
3. 中文路径 loop/intro；
4. trim/crop/rotation/profile；
5. 1%/100%/10000% 显示缩放，10000% 使用 Point 像素放大；
6. 捕获帧；
7. 预览 output0 与导出解码帧一致；
8. 项目脚本首次信任、修改后重信任；
9. raw mode 控件禁用；
10. worker crash 后 GUI 可保存、可重启；
11. 正常退出无残留 worker/mmap。

- [ ] **Step 7: 提交 M8 文档**

```powershell
git add docs/vapoursynth-kb docs/VS_DECOUPLING.md README.md docs/USER_MANUAL.md tests/test_vs_docs_contract.py tests/test_source_encoding.py tests/test_media_packaging.py
git commit -m "docs: 更新自定义 vpy 架构知识库与使用说明"
git show -1 --format=full
```

Expected: 不修改 `docs/CHANGELOG.md` 顶部版本；无共同作者；不推送，等待用户明确授权。

## 4. 参数如何传入（实现者不得另造旁路）

### 4.1 Qt/worker 预览路径

1. 主进程把项目编辑状态序列化为唯一 `job-<epoch>.json`；
2. `load` 协议传 `script_path/job_path/bundle_hash/runtime_fingerprint/mode/api_version/track/epoch`，这些都是身份与一致性字段，不复制 crop/滤镜参数；
3. worker 在自己的进程设置用户脚本 globals：`assetmaker_job`、`assetmaker_api`、`assetmaker_script`、`assetmaker_mode`；
4. 用户脚本调用 `load_job(assetmaker_job)` 读取完整参数；
5. 每次 crop/rotation/trim/profile 改变写新 job、新 epoch，并重新 load graph；
6. 帧请求另传 `surface/index/viewport/zoom_factor/pan`，其中 viewport/zoom/pan 只影响私有显示节点，不进入 output0。

### 4.2 VSPipe/导出路径

宿主启动：

```text
VSPipe.exe -c y4m -p
  --arg assetmaker_job=<absolute job.json>
  --arg assetmaker_script=<absolute user pipeline.vpy>
  --arg assetmaker_api=1
  --arg assetmaker_mode=<compatible|raw>
  <absolute assetmaker_runner.vpy> -
```

四个 `--arg` 只传入口元数据；crop、timeline、分辨率、色彩目标等全部由 job 读取，避免命令行转义和字符串生成。runner 与 worker 调用同一个 `assetmaker_vs.executor`；两边执行同一用户脚本并调用同一 contract。

运行环境不是滤镜参数：宿主把已校验 `python_module_dirs` 作为 UTF-8 JSON 放入 `ASSETMAKER_VS_PYTHON_DIRS_JSON`，把原生 DLL 目录放入 `VAPOURSYNTH_EXTRA_PLUGIN_PATH`，并把同一 `runtime_fingerprint` 放入 `ASSETMAKER_VS_RUNTIME_FINGERPRINT`。worker 与 runner 都重算并比较；两条路径的测试必须比较最终 module/plugin 搜索顺序和 fingerprint 完全一致。

### 4.3 用户脚本拿到什么

脚本是标准 Python `.vpy`，可：

```python
from assetmaker_vs.job_api import load_job
job = load_job(assetmaker_job)
source_path = job.source.path
crop = job.transform.crop
fps = job.timeline.fps
```

compatible 脚本承诺消费声明的能力并注册 output0/output1；raw 脚本可忽略大多数编辑字段，但仍必须注册满足 output0 契约的最终节点。宿主不会在脚本后自动补滤镜。

## 5. 完成定义与回滚边界

- M0–M6 每个提交可独立回滚；M7 只能在 M2–M6 全绿后执行。
- M4 前旧预览仍在；M4 后主进程不加载 VS。M5 前导出仍走旧生成器；M5 后预览/导出同脚本同 job。M7 才删除旧实现。
- 任一阶段若真实 VSPipe/worker/编码测试失败，停止在该阶段，不用 mock 绿灯替代。
- 完成必须同时满足：无双图生产调用、无主进程 VS、用户 `.vpy` 可完全控制 output0、1%–10000% 缩放、项目脚本重信任、worker/VSPipe parity、GitHub frozen worker smoke、全部文档与索引更新。
- 所有本地规划文件 `task_plan.md`、`findings.md`、`progress.md` 保持私有，不提交。

## 6. 实施方式

1. **子代理驱动（推荐）：** 每个 Task 分派独立实现代理，主代理逐阶段复核 diff、证据和测试，再允许下一个 Task。
2. **本线程顺序执行：** 由当前代理按 M0–M8 逐项执行，每个 Task 提交后向用户报告并等待阶段性审核。

无论选择哪种方式，都不得跨阶段合并提交、不得加入共同作者、不得在未经明确授权时推送。

## 7. 规范覆盖自检

| 已批准决策 | 实施任务 | 自动证据 |
|---|---|---|
| `.vpy` 是唯一滤镜事实源 | M2、M5、M7 | runner/default 行为、worker↔VSPipe parity、旧生产调用零命中 |
| 全局与项目脚本 | M6 | project/global/builtin 兼容测试 |
| 项目代码递归信任 | M6 | bundle 增删改/逃逸/hash 失效测试 |
| 独立常驻预览 worker | M3、M4 | Popen `CREATE_NO_WINDOW`、crash isolation、epoch/slot generation、main-process no-VS |
| 同一不可变 `job.json` | M1、M4、M5 | schema roundtrip、resolved export、same-job parity |
| output 0 严格最终输出，无宿主补滤镜 | M2、M5、M7 | `VideoOutputTuple`、逐帧 pass-through guard、动态属性失败、source boundary scan |
| compatible/raw | M1、M2、M4、M6 | header parser、output1/raw controls、旧项目默认 |
| output1 只作完整源编辑画布 | M2、M4 | metadata/index/crop-space 测试；编码只取 output0 |
| 参数通过 job/四个 `--arg` 传入 | M1、M2、M5 | 参数数组、中文路径、无 shell 拼接测试 |
| 1%–10000% 缩放，高倍使用 RGB24 CropAbs + Point | M2、M4 | 1%/100%/10000%、奇数/1px/中心像素/恒定 viewport 测试 |
| 旧配置仅迁移运行字段 | M1、M7 | migration report、滤镜字段零迁移、旧文件不再分发 |
| 文档与索引可按主题读取 | M8 | INDEX 路由和链接检查 |

若任一行在实现阶段没有对应绿色证据，该阶段不得标记完成。
