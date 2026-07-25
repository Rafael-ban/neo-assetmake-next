# VapourSynth 解耦 —— 修改文档

> 把 VapourSynth 的**脚本生成**与**配置**从 `core/media_pipeline.py` 拆出,
> 使以后加插件/滤镜成为**局部、可校验、低风险**的编辑,而非在主管道里手改
> 硬编码字符串。**输出与重构前逐字节一致**(golden 测试证明),行为零变化。

相关提交:`5833bc7`(脚本与配置抽离)、`5cf0dc3`(media_tools 接 VSConfig)、
`4798044`(打包 vsconfig.json)。

---

## 1. 背景与目标

`core/media_pipeline.py` 曾把三件事糅在 ~540 行:VS 脚本生成、命令构建
(VSPipe/x264/muxer)、通用子进程管道(`MediaEncoder`)。而 VS 的"配置"又
硬编码散落在 `core/media_tools.py`。目标:**VS 的配置文件和 VS 本身单独出来,
不定死在 media_pipeline.py**。

## 2. 证据(先证明,再改)

### 2.1 旧写法为何脆弱

- **三处独立事实源各自漂移**:
  1. `write_vpy_script`(`media_pipeline.py`)硬编码 `core.imwri.Read` /
     `core.lsmas.LWLibavSource` / `core.std.*` / `matrix_s='170m'`;
  2. `media_tools.py:18` `REQUIRED_VAPOURSYNTH_PLUGINS=("lsmas","imwri")`,
     并在探测脚本 `:97` 再插值一遍;
  3. `build.py:366-389` 整目录拷贝实际 DLL。
  往 `vs-plugins/` 丢一个 DLL 会**自动加载并随包分发,却既不被门控也不被
  校验**,除非有人再改那个元组 + 主管道字符串。
- **无扩展点**:脚本是一条**顺序载荷的 f-string 序列**(裁剪必须在 Loop 前,
  只靠读代码保证),加任何滤镜都要在函数中间插字符串、常要加 `if` 分支,
  且无校验;`matrix_s` 魔法串 `'170m'`/`'709'` 重复散布 ≥6 个文件。

### 2.2 新写法为何有效(框架源码 + 运行时双证)

- **VS R73 直接源码** `tools/media/vapoursynth-stubs/__init__.pyi`:`Core` 类
  把每个插件暴露为 **namespace 属性**(`:1505-1534`,`avs/imwri/lsmas/
  resize/std/text`)→ 证明 `hasattr(core, name)` 探测正确、按 namespace
  寻址正确。
- **全仓零 `core.std.LoadPlugin`**(stub `:1116` 有此函数但从不调用)→ app
  **100% 靠自动加载**(`portable.vs` 标记 + `vs-plugins`/`vs-coreplugins`
  目录 + `VAPOURSYNTH_EXTRA_PLUGIN_PATH`)。**故配置只需声明"所需 namespace +
  插件目录",无需任何 load 调用**——外部化为 JSON 充分且正确。
- **stub 由 `vsgenstubs` 生成**(`tools/media/vsgenstubs4/`)→ 插件集本就是
  "可再生数据",佐证外部化合理。
- **本仓已验证的配置模式**:`config/epconfig.py`(dataclass + from_dict/
  to_dict)+ `schemas/epconfig.schema.json`(Draft 2020-12)+
  `jsonschema.Draft202012Validator` + `atomic_write_json` → 新配置 1:1 镜像。
- **运行时证明**:
  1. `tests/test_vpy_golden.py` 对 5 个重构前抓取的 golden 断言**字节等价**;
  2. 真机编码 8 项实跑(`test_media_encode_integration` +
     `test_export_color_roundtrip`)——真实 VSPipe 门控 + 色彩回环通过;
  3. 全量 204 项通过。

## 3. 改了什么

| 模块 | 类型 | 内容 |
|---|---|---|
| `config/vsconfig.py` | 新增 | `VSConfig`/`MatrixHeuristic` dataclass + `load_vsconfig()`(lru_cache,读 `get_app_dir()/config/vsconfig.json`,缺失回落默认)。镜像 epconfig。 |
| `config/vsconfig.json` | 新增 | 默认数据:`required_plugins`、`extra_plugin_dirs`、`image_source_format`、`output_format`、`resampler_kernel`、`colour.matrix_s`+`heuristic`。 |
| `schemas/vsconfig.schema.json` | 新增 | Draft 2020-12,镜像 epconfig schema。 |
| `core/vs_script.py` | 新增 | `VpyScriptBuilder`(逐滤镜方法)读 VSConfig;`write_vpy_script` 变薄编排器;`_quote_vs_string`/`_vs_path` 迁入。 |
| `core/media_pipeline.py` | 改 | 删 VS 作者体(~120 行),改为从 `core.vs_script` 再导出;`__all__` 不变。 |
| `core/media_tools.py` | 改 | 探测脚本抽成 `_plugin_probe_script(cfg)` 读 `cfg.required_plugins`/`output_format`;env 迭代 `cfg.extra_plugin_dirs`;`REQUIRED_VAPOURSYNTH_PLUGINS` 降级垫片;`refresh` 清 vsconfig 缓存。 |
| `build.py` | 改 | 打包 `config/vsconfig.json` 到 `<app>/config/`(装好可改)。 |
| 测试 | 新增 | `test_vsconfig_contract`、`test_vpy_golden`(+5 golden)、`test_vsconfig_wiring`;`test_media_packaging` 加断言。 |

两个生产调用方(`export_service._export_video`、
`main_window._bake_loop_image_for_simulator`)经再导出**零改动**。

## 4. 以后怎么加插件/滤镜

- **只加插件门控**(某解码/去噪 DLL):① 把 DLL 丢进
  `tools/media/vs-plugins/`;② 在 `config/vsconfig.json` 的 `required_plugins`
  加它的 namespace。**零改 Python**,装好的程序里直接改 JSON。
- **把插件的滤镜接进导出链**(如加一步锐化):在 `core/vs_script.py` 加
  **一个** builder 方法(如 `def sharpen(self, ...)`),在 `write_vpy_script`
  编排器里加**一处**调用。**完全不碰 `media_pipeline.py`**,色彩/格式/顺序
  契约由 golden 测试自动守护。
- **改色彩矩阵/输出格式/分辨率启发式**:纯改 `config/vsconfig.json`,
  schema 自动校验。

## 5. `config/vsconfig.json` 字段说明

| 字段 | 默认 | 说明 |
|---|---|---|
| `required_plugins` | `["lsmas","imwri"]` | 导出前门控的 VS 插件 namespace(单一事实源)。 |
| `extra_plugin_dirs` | `["vs-plugins"]` | 注入 `VAPOURSYNTH_EXTRA_PLUGIN_PATH` 的自动加载目录(相对 `tools/media/`)。 |
| `image_source_format` | `"RGB24"` | `imwri.Read` 后统一到的图片像素格式。 |
| `output_format` | `"YUV420P8"` | 最终 `resize` 输出像素格式。 |
| `resampler_kernel` | `"Bicubic"` | `core.resize.<kernel>` 重采样核。 |
| `colour.matrix_s` | `"170m"` | RGB→YUV / 归一的色彩矩阵(与 x264 的 smpte170m VUI 标一致)。 |
| `colour.heuristic` | `{720,1,6}` | 视频源 `_Matrix` 未指定时的 H.273 SD/HD 启发式(≥阈值用 HD 矩阵,否则 SD)。 |

缺失文件时代码回落到与上表**完全相同**的 dataclass 默认值,故打不打包、
删不删文件都不改变行为。

## 6. 验证

```bash
uv run python -m unittest tests.test_vsconfig_contract tests.test_vpy_golden tests.test_vsconfig_wiring -v
uv run python -m unittest tests.test_media_pipeline -v            # 子串/顺序契约零改动
uv run python -m unittest tests.test_media_encode_integration tests.test_export_color_roundtrip -v  # 真机
QT_QPA_PLATFORM=offscreen uv run python -m unittest discover -s tests   # 全量 204 项
```

golden 再生是**手动 dev-only** 步骤(仅在有意改变输出时执行)。

## 7. 提醒

- 修复在**源码仓**;装好的 `ArknightsPassMaker` 需 `uv run python build.py`
  重建才带上(重建后 `config/vsconfig.json` 随包,可就地改)。
