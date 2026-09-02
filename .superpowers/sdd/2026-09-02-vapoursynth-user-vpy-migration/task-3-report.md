# Task 3 / M2 实施报告

状态：Fix round 3 DONE（实现完成，待独立复审；Round 2 的 `DONE` 已撤回）

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
- **事后更正**：初始实现当时仍有后续 Fix round 1 所处理的 I1/I2/I3；Fix round 2
  只修复了 mixed namespace 正常路径，清理异常仍可令整份退休计划失效，因此其 C1
  CLOSED/DONE 与“无阻塞 concern”结论一并撤回。该边界由 Fix round 3 实现修复，仍待
  独立复审。按仓库约定，本次修改属于 source tree；既有已安装
  `ArknightsPassMaker` 不会自动变化，需后续完成迁移阶段并重新构建/发布后生效。

---

## Fix round 1/5（2026-09-02）

状态：CHANGES_REQUESTED（事后更正；原 `DONE` 结论已由独立复审撤回）

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

- **C1 — NOT CLOSED（事后更正）**：Fix round 1 只修复了普通模块和单来源
  namespace；它在恢复 `sys.path` 后才扫描动态 namespace `__path__`，会漏掉 mixed
  namespace 的脚本贡献，并残留父包上的 A 子模块属性。原报告据此概括“namespace
  来源识别完成、外部模块均受保护”并不成立：stdlib、helper 和普通外部模块的窄用例
  虽通过，但 `python_module_dirs` 参与的外部 namespace 父子 identity 会被破坏，B
  也可能继续执行 A。该 Critical 由下方 Fix round 2 修复。
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
- 全量（Fix round 1 当时的历史结果，不作为 Fix round 2 的验证证据）：
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
- **阻塞结论更正**：Fix round 1 结束时仍有 mixed namespace Critical，并且
  executor 会接受重叠图；原“无本轮阻塞 concern”结论撤回。后续 M3 worker 仍必须
  只在旧图所有 frame future 到终态后调用 `ExecutedGraph.close()`，无法及时排空则
  重启 worker。另因本仓库是 source tree，已安装的 `ArknightsPassMaker` 仍需后续
  重建或发布后才会获得修复。

---

## Fix round 2/5（2026-09-02）

状态：CHANGES_REQUESTED（事后更正；独立复审确认清理异常边界仍未关闭 C1）

基线提交：`da3d07f7892c3717628ba2dd1c19010b560c3530`

实现提交：`8a22e46764c68b8feba0a39e6085ae2c9f89504e`
（`fix: 修复混合命名空间与图生命周期`）

### Round 1 不准确结论的正式更正

1. Round 1 的 **C1 — CLOSED** 已在原段落直接更正为 **NOT CLOSED**；普通模块与
   单来源 namespace 通过，不能证明 mixed namespace 正确退休。
2. “stdlib、外部模块、`assetmaker_vs` 均受保护”已限定为当时实际覆盖的
   stdlib/helper/普通外部模块；运行时注入的外部 namespace 父包与子模块 identity
   当时并未受到保护。
3. Round 1 的“无本轮阻塞 concern”已撤回；mixed namespace 是可跨 bundle 执行
   A 代码的 Critical，重叠图则会复用旧模块并破坏全局路径快照。
4. Round 1 的 361 tests 保留为历史记录并明确限定；本轮结论只使用下述新鲜验证。

### 红灯与特征锁定证据

1. 修改前 runner/contract 基线：38 tests，14.364s，0 skip，OK。
2. C1 新增三个真实 R73 子进程场景后，生产代码未改时运行 3 tests，9.154s，
   failures=3：
   - 第三方目录预先在 `sys.path`：父包 identity 尚在，但 `shared_ns.local_piece`
     属性仍指向 A；
   - 第三方目录仅由 `python_module_dirs` 注入：父包被删除重建，外部子模块仍缓存，
     旧父包还残留 A 属性；
   - A 脚本抛错的半加载路径复现同一父包/属性不一致。
3. N1 新增四个生命周期场景后，生产代码未改时运行 4 tests，16.521s，
   failures=3：同步 overlap 被接受、两线程 barrier 得到两个赢家、异常 close 会重复
   清理；脚本执行失败后的复用原本通过，作为不得回归的基线。
4. N2 是独立复审已确认产品正确的持久测试缺口；新增循环容器、坏 Mapping、坏
   repr、非有限 float 和超大整数后首次即通过，没有为制造红灯改动产品代码。

### 实现摘要

- C1（正常路径实现，非完整关闭）：在 `ExecutionEnvironment` 仍激活时冻结
  `_ModuleRetirementPlan`，记录具体模块
  对象与父包属性 identity。普通脚本模块和纯脚本 namespace 退休；mixed namespace
  父包、外部子模块及普通第三方对象保留；本地子模块同时从 `sys.modules` 和父包
  陈旧属性删除。正常 close 与执行失败共用同一清理顺序。
- N1：portable executor 在 import VapourSynth、模块退休、`clear_outputs()` 和
  `sys.path` 激活前原子获取进程级 graph lease。第二张活图立即抛
  `GraphLifecycleError(code="executor.graph_active")`；token 由 `ExecutedGraph` 持有
  到 close，失败回滚和异常 close 均在最外层 `finally` 幂等释放。close 在清理前先
  标记终态，因此 double close 与异常后的再次 close 不会重复执行。
- N2：永久覆盖循环 list/dict、`Mapping.items()` 抛异常、`repr()` 抛异常、
  NaN/±Infinity 和正负百万 bit 整数；逐项固定稳定结构，并断言 marker UTF-8 小于
  4096 bytes、`allow_nan=False` JSON 编码成功及 decode 后 `to_dict()` 等价。
- `core.vs_runtime.executor` 只 re-export 新生命周期错误，portable helper 仍只依赖
  标准库和包内模块，没有复制执行规则。

### 修复后验证

- C1 三个正常 mixed namespace 定向：3 tests，10.323s，0 skip，OK；A→B 得到 B，
  两种第三方路径变体及失败清理均保持外部父/子/属性 identity。
- N1 四个生命周期定向：4 tests，13.650s，0 skip，OK；副作用补充探针 2 tests，
  5.447s，OK，证明被拒 overlap 不改变活动 `sys.path`、不再次 clear outputs，线程
  竞态也只执行一次全局清理。
- 完整 runner：27 tests，38.007s，0 skip，OK；真实固定 VSPipe 用例全部执行。
- 完整 output contract：19 tests，1.842s，0 skip，OK；新增极端值及真实 R73
  子进程全部通过。
- Task 3 最终定向：
  `uv --cache-dir .uv-cache run python -m unittest tests.test_vs_runner tests.test_vs_output_contract tests.test_vs_display tests.test_default_vpy_pipeline -v`
  - 结果：53 tests，50.319s，0 skip，OK；真实 VSPipe/R73/default pipeline 均执行。
- 既有导出色彩与预览缩放：14 tests，2.748s，0 skip，OK。
- 语法/导入：
  `uv --cache-dir .uv-cache run python -m compileall -q main.py config core gui utils _mext build.py tests resources/vapoursynth/python`
  - 结果：退出码 0。
- 全量：
  `uv --cache-dir .uv-cache run python -m unittest discover -s tests -p "test_*.py"`
  - 结果：369 tests，77.886s，0 skip，OK。
- `git diff --check` 与实现提交前 `git diff --cached --check`：退出码 0。

全量输出中的 `encode boom`、无效图片编码、RNDIS 超时、缺失视频和 PyArmor
不完整输出均为既有负向测试主动触发；最终 suite 为 OK。

### 范围与剩余边界

- 实现提交恰好修改 5 个文件：portable executor、core re-export、真实子进程 helper
  和两份测试；未修改 CHANGELOG、`tools/`、用户配置、GUI 或构建输出。
- 实现提交无共同作者；未 amend、未推送。
- executor lease 是非法 overlap 的进程级防线，不替代 M3 worker 的 inflight 编排。
  M3 仍须等待旧 future 全终态后 close；超时必须重启 worker，不能提前抢占 lease。
- **阻塞结论更正**：Round 2 结束时，任一无关模块的 `__file__`、`__path__` 或父属性
  getter 抛错仍会中止整个退休计划；lease 虽释放，B 却可继续复用 A。原 C1 CLOSED、
  Round 2 DONE 与无阻塞结论均撤回，不能由三个正常 mixed namespace 用例外推到该
  Critical 清理异常边界。
- 本仓库是 source tree；已安装的 `ArknightsPassMaker` 只有后续重建或发布后才会获得
  本轮修复。

---

## Fix round 3/5（2026-09-02）

状态：DONE（实现完成，待独立复审）

基线提交：`a179696ba2c8691fc5a26cc0d568beb9bdde8847`

实现提交：`ac330184a7daf88f3bd941c9efa8d6441aeafd24`
（`fix: 隔离 vpy 模块退休异常`）

### Round 2 不准确结论的正式更正

1. Round 2 状态已在原段落由 **DONE** 直接更正为 **CHANGES_REQUESTED**；三个正常
   mixed namespace 用例通过，只能证明无清理异常时的计划冻结与 identity 保护。
2. Round 2 的 C1 CLOSED 结论撤回。独立复审的成功 close 与脚本失败反例均证明：
   无关 poison 模块可令 `_capture_module_retirement()` 整体中止，A 留在
   `sys.modules`，lease 释放后 B 继续得到 A。
3. Round 2 的无阻塞结论撤回；原脚本异常还会被 cleanup 异常覆盖，因此该问题同时
   跨越 bundle 信任边界和用户可诊断性边界。
4. Round 2 的 53/369 tests 等历史结果保留，但明确不覆盖 poison getter 或退休执行
   本身抛错的边界，不作为本轮关闭证据。

### 红灯证据

1. 修改产品前完整 runner 基线：27 tests，37.788s，0 skip，OK。
2. 新增成功 close 与失败回滚各一条 poison 元数据持久回归后运行 2 tests，5.499s，
   failures=2：
   - 成功 close 被 `poison getattr: __path__` 中止，A marker 仍缓存，B 得到 A；
   - 脚本失败被 `poison getattr: __file__` 覆盖，A marker 仍缓存，B 得到 A。
3. 为排除“只包 getter”这一不完整修复，另增父解绑失败的 best-effort 与主异常 note
   两条回归。产品未改时运行 2 tests，5.968s，failures=2：正常 close 传播裸
   `RuntimeError` 并中止后续退休；失败回滚则再次覆盖 `script failure`。

### 实现摘要

- `_module_file_path()`、`_package_paths()`、路径正规化及父属性捕获均按单个对象捕获
  `BaseException`；无法安全分类的无关模块只保留该对象，不放弃此前或此后识别出的
  脚本根模块。
- `_ModuleRetirementPlan.retire()` 对每个 `sys.modules` 条目和父属性分别做 identity
  比较、删除与异常隔离；一个条目失败后继续处理所有剩余模块/绑定，捕获后出现的替换
  模块和替换属性保持不变。
- 退休诊断最多保存 8 条、每条最多 160 字符，只记录固定操作、目标和异常类型，不调用
  用户异常的 `str()`/`repr()`。正常 close 在 best-effort 完成后以
  `GraphLifecycleError(code="executor.retirement_failed")` 明确报告不完整清理。
- `_close_execution_environment()` 对计划捕获、环境恢复、模块退休和 cache invalidation
  依次尽力执行；既有 environment close 异常仍保持主异常，后续清理错误只作为 note。
- `execute_user_script()` 失败回滚捕获 cleanup 错误，以 `BaseException.add_note()` 附加
  稳定错误码后裸 `raise` 原脚本异常；最外层 `finally` 仍幂等释放 graph lease。
- portable helper 继续只依赖标准库和包内模块，没有引入 `core`、`config` 或 Qt。

### Finding 状态

- **C1 — ADDRESSED（待独立复审）**：成功 close 与脚本失败均对无关 poison 元数据
  逐项隔离；真实 A 模块被退休，B 得到 B。实际解绑失败时其余模块/父属性仍完成退休，
  替换 identity 与外部 parent 保留。
- **N1 — PRESERVED**：正常 close、脚本失败、cleanup 异常、double close、同步 overlap
  与线程竞态仍遵守进程级单活 lease；清理错误后第二张图可正常取得 lease。
- **N2 — PRESERVED**：本轮未修改 output contract 或错误载荷归一化；Task 3 定向与
  完整 contract 套件继续覆盖既有极端值用例。

### 修复后验证

- 四条 Fix round 3 精确回归（最终测试夹具）：4 tests，11.108s，0 skip，OK。
- 完整 runner：31 tests，48.517s，0 skip，OK；C1/N1、真实固定 VSPipe 与四条新回归
  全部通过。
- Task 3 最终定向：
  `uv --cache-dir .uv-cache run python -m unittest tests.test_vs_runner tests.test_vs_output_contract tests.test_vs_display tests.test_default_vpy_pipeline -v`
  - 结果：57 tests，61.804s，0 skip，OK；真实 VSPipe/R73/default pipeline 均执行。
- 既有导出色彩与预览缩放：14 tests，2.808s，0 skip，OK。
- 语法/导入：
  `uv --cache-dir .uv-cache run python -m compileall -q main.py config core gui utils _mext build.py tests resources/vapoursynth/python`
  - 结果：退出码 0。
- 全量：
  `uv --cache-dir .uv-cache run python -m unittest discover -s tests -p "test_*.py"`
  - 结果：373 tests，89.668s，0 skip，OK。
- `git diff --check` 与实现提交前 `git diff --cached --check`：退出码 0。

全量输出中的 `encode boom`、无效图片编码、RNDIS 超时、缺失视频和 PyArmor
不完整输出均为既有负向测试主动触发；最终 suite 为 OK。

### 范围与剩余边界

- 实现提交恰好修改 3 个文件：portable executor、真实子进程 helper 与 runner 测试；
  未修改 CHANGELOG、`tools/`、用户配置、GUI、core adapter 或构建输出。
- 实现提交无共同作者；未 amend、未推送。本报告将作为独立文档提交。
- 本轮只为已捕获对象提供 best-effort 回收；无法安全读取来源的 poison 对象自身按
  fail-closed 原则保留，不声称可恢复任意恶意脚本对全局解释器状态的破坏。
- M3 仍须等待旧图所有 frame future 到达终态后 close；超时重启 worker 的边界未变。
- 本仓库是 source tree；已安装的 `ArknightsPassMaker` 仍需后续重建或发布后才会获得
  本轮修复。
