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
