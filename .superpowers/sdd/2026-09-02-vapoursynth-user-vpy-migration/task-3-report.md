# Task 3 / M2 实施报告

状态：DONE

提交：本报告随 `feat: 引入自定义 vpy 启动器和输出契约` 提交；最终 hash 以该提交为准。

## 红灯记录

1. portable runner/executor 首轮：`tests.test_vs_runner` 运行 7 项、7 项失败、0 skip；
   缺失点分别命中 executor、固定 runner、中文路径模块导入与 VSPipe 调用。
2. 输出契约首轮：修正探针自身参数后运行 11 项、10 项失败；唯一通过项是真实
   R73 `_ColorRange` 语义探针，其余均因 `assetmaker_vs.contract` 尚不存在失败。
3. 显示支路首轮：3 项、3 项失败、0 skip；纯参数边界和两个真实 VS 子进程均因
   `assetmaker_vs.display` 缺失失败。
4. 默认 Vpy 首轮：4 项、4 项失败、0 skip；header、图片、视频和 P7 实媒体路径均
   精确命中默认脚本/helper 缺失，而非被工具链 skip。
5. helper/core 共享解析与 stdout/callable 补充红灯：adapter 身份 3 项失败，UTF-8
   `<4 MiB` 分块和完整 `namespace.Function` 检查分别因实现缺失失败。
6. 自审新增四组精确红灯：helper 在脚本根内会被按长包名驱逐；手工嵌套 dataclass
   泄漏 `AttributeError`/`TypeError`；compatible output 1 不检查 resolved end/FPS；
   非整数 frame prop 泄漏裸 `ValueError`。最小修复后 4/4 通过。

## 实现摘要

- 新增纯标准库 `assetmaker_vs` ABI：job/header 唯一 wire 解析、结构化错误、专用
  Python 模块目录环境变量、用户脚本 executor、图同寿命 import 环境、严格 stdout
  隔离与线程安全 UTF-8 分块。helper 不导入项目 `core`、`config` 或 PyQt。
- M1 的 `core.vs_runtime.job/script_header` 改为调用共享解析器后构造 frozen
  dataclass；保留原子 RenderJob 发布，并让 helper/core 对同一非法输入给出完全一致
  的 error code、field 和 path。
- 新增固定 `assetmaker_runner.vpy`：永久把 Python stdout 指向 stderr，在执行用户
  代码前校验 header/job/invocation/requirements，执行后只注册经过验证的 guarded
  output 0；不拼 shell 命令、不猜应用根目录、不追加修正滤镜。
- 新增严格 output 0/1 契约：tuple/node/alpha/alt-output、YUV420P8、coded size、
  4:2:0、帧数、分数 FPS、首中末 sentinel、四项色彩属性、compatible 编辑输出及
  x264 VUI 映射。`_Range` 与 R73 `_ColorRange` 分别按相反 0/1 语义解析。
- output 0 由官方 `std.ModifyFrame` 追加逐帧只读 guard；合法帧原样透传，动态属性
  漂移以可从 VapourSynth traceback 恢复的 ASCII JSON marker 中止消费。
- 新增独立 RGB24 显示支路：1%=fit 结果的 1%，100%=完整画面 fit，200%–10000%
  先裁源窗口再 Point 放大；奇数尺寸不做 YUV 偶数对齐，输出始终受 viewport 封顶。
- 新增可读的内置 compatible Vpy：视频和图片均在 rotation 后注册完整 output 1，
  再 trim/crop/resize/colour/padding/final-180 生成 output 0；图片先循环到完整虚拟
  时间轴。未打标签视频的矩阵启发式固定读取原始源高度，避免 P7 裁剪翻色。

## 关键运行证据

- 真实 bundled VSPipe 在中文 `素材/黍` 路径成功导入相邻 `modules/`；用户脚本及
  延迟 callback 的 print 只进入 stderr，VSPipe stdout 保持干净。
- mode 不匹配在用户代码前以 `invocation.mode` 非零退出；RGB 坏 output 以
  `contract.pixel_format` 令真实 VSPipe 非零退出。
- 真实 R73 证明 `range_s="limited"` 产生 `_ColorRange=1`、`full` 产生 0，且本包
  不产生 `_Range`；契约同时覆盖未来 `_Range` 的 0=limited/1=full 语义。
- 真实 `ModifyFrame` 第 2 帧动态 matrix 漂移在实际取帧时中止，并恢复为
  `field=matrix, actual=709` 的结构化错误。
- 图片 8×6 旋转后 output 1 为 6×8、9 帧/30 fps，非零 trim 后 output 0 为 5 帧；
  视频 bootstrap/resolved 共用旋转后完整 8×12、8 帧 output 1。
- 默认图片的固定 runner guarded output 已实际通过 VSPipe Y4M→x264-7mod→mux
  产出非空 MP4；因此三工具链闭环作用于本次 runner/default，而不只是测试输入生成。
- P7 使用真实 VSPipe→x264-7mod→mux 生成未打标签真 BT.709 800×800 MP4；LSMASH
  解码得到 `_Matrix=2`，裁到 450×800 与 404×718 的 output 0 digest 完全相等。

## 最终验证

- Task 3 定向：
  `uv run python -m unittest tests.test_vs_runner tests.test_vs_output_contract tests.test_vs_display tests.test_default_vpy_pipeline -v`
  - 结果：34 tests，OK，10.098s；真实 VSPipe/x264/mux 用例均执行、0 skip。
- 既有回归：
  `uv run python -m unittest tests.test_export_color_roundtrip tests.test_preview_zoom -v`
  - 结果：14 tests，OK。
- 语法/导入：
  `uv run python -m compileall main.py config core gui utils _mext build.py tests resources/vapoursynth/python`
  - 结果：退出码 0。
- 全量：
  `uv run python -m unittest discover -s tests -p "test_*.py"`
  - 结果：350 tests，OK，33.626s。

全量输出中的 `encode boom`、无效图片编码、RNDIS 超时、缺失视频及 PyArmor
不完整输出均由既有负向测试刻意触发，最终 suite 为 OK。

## 自审与 concerns

- 新增父进程测试只使用 JSON/dataclass/protocol/fake objects；所有创建真实
  `vs.core`/`BlankClip` 的新用例均由 `tests/helpers/run_vs_contract_case.py` 在全新
  子进程执行。
- 未修改 CHANGELOG 顶部版本、`config/user_settings.json`、`tools` junction、GUI、
  旧导出切换点或本地构建产物；未推送、未 amend、提交不含共同作者。
- 无阻塞 concern。按仓库约定，本次修改属于 source tree；既有已安装
  `ArknightsPassMaker` 不会自动变化，需后续完成迁移阶段并重新构建/发布后生效。

---

## Fix round 1/5（2026-09-02）

状态：DONE

基线提交：`8a17563aa2985bf2b197ebc660ce12a171301caa`

修复提交：`551f386d513dc1be7fac77a098d16106014355b5`
（`fix: 修复 vpy 模块退休与输出契约`）

### 红灯证据

1. 修改生产代码前，原有 runner/contract 基线为 27 tests、0 skip、OK；加入本轮
   回归后同一定向套件运行 37 tests，出现 35 failures + 2 errors：
   - 真实 R73 A→B 跨根执行中，B 仍得到 A 的 `marker.VALUE`；
   - fake、真实 R73 与固定 runner 均接受 float/str/bytes 等可转换非整数属性；
   - bytes 与任意对象在 sentinel/guard 错误路径逃逸为裸 `TypeError`，无法恢复
     `ASSETMAKER_VS_ERROR`；
   - 模拟缺失 VSPipe 时，绑定用例 `setUp()` 不会明确失败。
2. namespace package 独立红灯确认：模块没有 `__file__`、只有位于旧根的
   `__path__` 时，旧 `evict_modules_under()` 返回空 tuple，模块仍留在
   `sys.modules`。
3. 去除 runner 类级 skip、改为显式门禁后，在生产代码仍未修改时运行绑定 runner
   与门禁共 8 tests：I3 门禁和两条 M1 正向路径通过，I1/I2 的 6 个子用例精确
   失败。可转换值令 VSPipe 返回 0；`b"not-an-int"` 虽返回非零，但 stderr 只有
   JSON 序列化 `TypeError`，没有结构化 marker。
4. M1 两条行为在当前实现本就正确，本轮将其固化为真实仓库回归：
   - `VSPipe -c y4m` stdout 以 `YUV4MPEG2` 开头，用户脚本、延迟 import 和
     callback 的 print 只进入 stderr；
   - 5 帧脚本仅第 2 帧（index 1，非 `{0, 2, 4}` sentinel）漂移时，VSPipe 顺序
     消费返回非零，stderr 含 `ASSETMAKER_VS_ERROR` 与 `"field":"matrix"`。

### Finding 关闭映射

- **C1 — CLOSED**：`ExecutedGraph.close()` 只在调用者确认该图全部 inflight 已到
  终态后恢复执行环境，并按退休图自身 `script_root` 精确驱逐模块；操作幂等，失败
  执行也清理半加载模块。来源识别同时覆盖 `__file__` 与 namespace `__path__`，
  真实 A→B 同名模块回归证明 B 加载 B；stdlib、外部模块、`assetmaker_vs` 与图关闭
  前仍在使用的 A 模块均受保护。
- **I1 — CLOSED**：`_Matrix`、`_Transfer`、`_Primaries`、`_Range`、
  `_ColorRange` 统一要求 `type(value) is int`，不再通过 `int()` 修正 bool、float、
  str 或 bytes。fake 覆盖五字段四种非法类型；真实 R73 和固定 runner 覆盖五字段
  的可转换 float/bytes 值。
- **I2 — CLOSED**：`OutputContractError` 构造边界统一把 expected/actual 归一化为
  有界 JSON-safe 值；bytes 记录原长度、最多 64 bytes 的 hex 前缀和截断标记，长
  字符串、容器、循环、超大整数及任意对象也有节点/条目/文本上限。真实 bytes 的
  sentinel 与晚期逐帧 guard 均可经 `decode_output_contract_error()` 恢复，未再泄漏
  裸 `TypeError`。
- **I3 — CLOSED**：删除 `TrustedRunnerTests` 的类级 `skipUnless`；每个绑定用例在
  `setUp()` 明确断言发现的 VSPipe 是现存文件，缺失时以 FAIL 报告 discover root、
  预期路径与完整 toolchain describe。额外策略测试禁止类字典重新出现 unittest
  skip 标记。
- **M1 — CLOSED**：新增真实二进制 Y4M stdout/stderr 隔离回归与非 sentinel 第 2
  帧漂移的 VSPipe 顺序消费回归；两者均使用固定 `assetmaker_runner.vpy`，不是直接
  调 helper 替代真实消费链。

### 修复后验证

- 核心定向：
  `uv --cache-dir .uv-cache run python -m unittest tests.test_vs_runner tests.test_vs_output_contract`
  - 结果：38 tests，OK，14.179s；0 skip。
- Task 3 完整定向：
  `uv --cache-dir .uv-cache run python -m unittest tests.test_vs_runner tests.test_vs_output_contract tests.test_vs_display tests.test_default_vpy_pipeline -v`
  - 结果：45 tests，OK，24.256s；真实 R73/VSPipe/default pipeline 全部执行，0 skip。
- 语法/导入：
  `uv --cache-dir .uv-cache run python -m compileall -q main.py config core gui utils _mext build.py tests resources/vapoursynth/python`
  - 结果：退出码 0。
- 全量：
  `uv --cache-dir .uv-cache run python -m unittest discover -s tests -p "test_*.py"`
  - 结果：361 tests，OK，51.017s；0 skip。
- `git diff --check` / 提交前 `git diff --cached --check`：退出码 0。

全量输出中的 `encode boom`、无效图片编码、RNDIS 超时、缺失视频和 PyArmor
不完整输出仍为既有负向测试主动触发；最终 suite 为 OK。

### 范围与剩余风险

- 修复提交仅含 portable executor/contract、真实子进程 helper、两份测试及一个
  晚漂移 fixture，共 6 个文件；未修改 CHANGELOG、`tools/`、用户配置、GUI 或构建
  输出，提交正文没有共同作者；未 amend、未推送。
- portable helper 继续只依赖标准库和包内模块；共享解析/契约没有复制到 core
  adapter，单一规则源保持不变。
- 无本轮阻塞 concern。后续 M3 worker 必须继续遵守既定调用契约：只有旧图所有
  frame future 到终态后才调用 `ExecutedGraph.close()`；若无法及时排空则重启
  worker。另因本仓库是 source tree，已安装的 `ArknightsPassMaker` 仍需后续重建
  或发布后才会获得本修复。
