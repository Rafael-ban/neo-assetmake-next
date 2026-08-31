# VapourSynth 知识库索引

**用法：先查这张表，只读命中的那一个文件。** 不要为了回答一个问题通读整个目录。

本库只收录**本项目实际用到**的 VapourSynth 面：`std` 几何滤镜、`resize` 色彩转换、`lsmas`/`imwri` 源、VSPipe 导出、便携部署与进程内加载。凡与本项目无关的滤镜（降噪、超分、抗锯齿）一律不收。

## 索引

| # | 文件 | 一句话 | 关键词 |
|---|---|---|---|
| 01 | [colour-range-props.md](01-colour-range-props.md) | `_Range` 与 `_ColorRange` 语义相同但**数值互为反转**，本捆绑包（api R4.1 + lsmas）只发 `_ColorRange` | `_Range` `_ColorRange` limited full range 反转 H.273 |
| 02 | [resize-semantics.md](02-resize-semantics.md) | `matrix_in`/`range_in` 只是兜底——帧属性一旦有值就**覆盖**参数；Bicubic 的 `filter_param_a/b` 就是 b/c | resize matrix_in_s range_in filter_param_a Bicubic dither_type 兜底 |
| 03 | [geometry-filters.md](03-geometry-filters.md) | `Transpose` 是矩阵转置（非旋转），须配 Flip 合成旋转；Crop/AddBorders 都受**子采样约束**且违反即报错 | Transpose Turn180 FlipHorizontal CropAbs AddBorders 偶数 mod2 子采样 旋转 镜像 |
| 04 | [trim-loop-zero-length.md](04-trim-loop-zero-length.md) | **VapourSynth 不允许 0 帧 clip**；`Loop(times<1)` 是"重复到最大长度"而非一次 | Trim Loop 切片 零长度 clip[a:a] 空clip times |
| 05 | [plugin-autoload-portable.md](05-plugin-autoload-portable.md) | 插件自动加载失败是**静默的**；`portable.vs` 机制只存在于 C++ 源码，官方文档不提 | autoload 静默 portable.vs vs-plugins add_dll_directory sys.path CI护栏 |
| 06 | [vspipe-cli.md](06-vspipe-cli.md) | 官方 Examples 给出的 y4m→x264 管道形式与本项目一致；`--` 是"求值但不写"的空跑模式 | vspipe y4m x264 demuxer 空跑 dry-run 具名管道 timecodes |
| 07 | [frame-lifetime-threading.md](07-frame-lifetime-threading.md) | frame 数据是 VS 自有内存，`close()` 前必须 copy；回调在 VS worker 线程，不得碰 Qt | frame lifetime close copy numpy GIL worker线程 get_frame_async 段错误 |
| 08 | [version-upgrade-notes.md](08-version-upgrade-notes.md) | 锁定 R73（api R4.1）的理由，及 R74 pip 化对便携部署的**间接**风险 | R73 R74 R78 升级 pip install 便携部署 cp312-abi3 Python3.12 |
| 09 | [plugin-ecosystem.md](09-plugin-ecosystem.md) | 只依赖 `lsmas`+`imwri`（均单 DLL）；imwri 上游已归档是**真实风险**；BestSource 作为替代**尚未核实**；叠加继续用核心内置 | BestSource vsfpng imwri归档 lsmas vsdb停更 std.Merge MaskedMerge Expr 单DLL vctrans |
| 10 | [research-method.md](10-research-method.md) | WebFetch 抓不了这些域名，**用 curl**；每源的限流与体积限制；全库核实状态清单 | curl WebFetch 受限 调研方法 置信度 未核实 vsdb doom9限流 912KB |
| 11 | [preview-zoom.md](11-preview-zoom.md) | 缩放走 **CropAbs 视口裁剪**而非整帧放大：100x 实测 1.53 ms（整帧放大是 9.3 s） | zoom 缩放 100x 10000% CropAbs 视口 viewport pan 平移 内存 恒定成本 |
| 12 | [field-hazards.md](12-field-hazards.md) | doom9 五条实战陷阱：`.vpy` 里 `print()` 会毁 y4m 管道；R73 的 `max_cache_size` **不是**硬约束 | print stdout y4m污染 lwi缓存 max_cache_size num_threads 32位段错误 子采样报错 |

## 置信度约定

每条条目标注来源等级，**不要把低等级当依据改代码**：

- ✅ **已核实原文** — 本机 curl 抓到官方页面 + 逐字引用（可复现：见 `10-research-method.md` 的命令）
- 🔬 **运行时实测** — 本机 `uv run python` + 真实 R73 探针，附输出
- ⚠️ **转述** — 只有搜索摘要/第三方转述，无逐字原文
- ❌ **未核实** — 仅标题级线索，或本次调研明确"找不到"

## 与项目代码的对应

| 代码位置 | 相关条目 |
|---|---|
| `core/vs_graph.py`（程序化构图） | 02, 03, 04, 12 |
| `core/vs_graph.py` `apply_preview_zoom()` | 11 |
| `core/vs_script.py`（`.vpy` 生成，golden 钉死） | 02, 03, 04, 12 |
| `core/vs_frame.py`（VS→numpy） | 07 |
| `core/vs_engine.py`（DLL 加载/core 单例/prewarm） | 05, 07, 08, 09 |
| `core/vs_engine.py` `lwi_cache_path()` | 12 |
| `gui/widgets/video_preview.py`（缩放/平移 UI） | 11 |
| `core/vs_player.py`（`FrameRequester`） | 07 |
| `core/media_pipeline.py`（VSPipe→x264→MP4Box） | 06 |
| `config/vsconfig.py` + `vsconfig.json`（色彩契约） | 01, 02 |
| `.github/workflows/build-app.yml`（打包契约断言） | 05 |
| `tests/test_export_color_roundtrip.py` | 01 |

## 维护

- 新增条目：建文件 + **同时更新本索引表**，否则等于没写（查不到）。
- 升级 VapourSynth 后：重跑 `10-research-method.md` 里的抓取命令，逐条复核 **01/02/05/08/09/12**——这六条对版本最敏感（09 的 imwri 归档与 12 的 `max_cache_size` 行为都会随版本改变）。
- 官方文档站当前是 **R76**，本项目锁 **R73**；条目里凡有版本差异的地方都已显式标注。
- **🔬 运行时实测优先于 ✅ 官方原文**：官方文档描述的是 R76+，与 R73 冲突时以本机探针为准。01 就是实例——官方原文与早期的一处误判都被探针推翻。
