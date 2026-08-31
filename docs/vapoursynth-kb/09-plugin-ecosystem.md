# 09 · 插件生态：本项目依赖的两个插件，及可选替代

**结论：本项目只依赖 `lsmas`（视频源）+ `imwri`（图片源），二者都是单 DLL、可直接放进 `vs-plugins/`，与便携部署模型兼容。叠加/合成继续用核心内置 `std.Merge`/`MaskedMerge`/`Expr` 是正确的——没有值得引入的专用插件。**

## 数据源可信度前提 ⚠️ 必读

`https://vsdb.top/` 抓取到的条目**日期最大值只到 2022-03-19**，之后无任何记录 —— 该数据库已停更约 4 年。因此：

- 它对 2022 年之后成型的项目（`BestSource`、`fpng`/`vsfpng`）**完全没有收录**，查不到 ≠ 不存在。
- 它的字段里**没有** "是否单 DLL"、"是否兼容 api 4.x"、"维护状态"、"推荐替代" 这些列 —— 这些问题 vsdb 结构上就答不了，必须去各插件自己的 GitHub 核实。
- 页面正文全内嵌在 HTML（去标签后约 912 KB），**不可整体读入**，只能 grep 片段。见 [10-research-method.md](10-research-method.md)。

**不要把 vsdb 的沉默当成任何结论的证据。**

## 本项目实际依赖的两个插件

`config/vsconfig.json` 的 `required_plugins` 即事实源，`core/vs_engine.py` `missing_plugins()` 在预热时校验。

### `lsmas` — 视频源

| 项 | 值 |
|---|---|
| vsdb 显示名 | `L-SMASH-Works`，描述逐字 `LSMASHSource for VapourSynth` |
| namespace | `lsmas` |
| Identifier | `systems.innocent.lsmas` |
| vsdb 最新记录 | `vA.3h`，2021-11-29（fork：`AkarinVS/L-SMASH-Works`） |
| 本项目用到的函数 | `LWLibavSource`（vsdb 另列 `LibavSMASHSource`，项目未用） |
| 便携部署 | ✅ `tools/media/vs-plugins/LSMASHSource.dll` 单 DLL |

vsdb 上**没有**维护状态、已知问题、api 兼容性的记载。项目侧的实证：R73 / api 4.1 上 `LWLibavSource` 正常工作，且 `.lwi` 索引策略见 [05-plugin-autoload-portable.md](05-plugin-autoload-portable.md)。

### `imwri` — 图片源

| 项 | 值 |
|---|---|
| vsdb 显示名 | `ImageMagick`，描述逐字 `VapourSynth ImageMagick 7 HDRI Writer/Reader` |
| namespace | `imwri` |
| Identifier | `com.vapoursynth.imwri` |
| vsdb 最新记录 | `R1`，2021-09-25（`vapoursynth/vs-imwri`） |
| 本项目用到的函数 | `Read`（`core/vs_engine.py:245`、`core/vs_script.py:58`） |
| 便携部署 | ✅ `tools/media/vs-plugins/libimwri.dll` 单 DLL |

⚠️ **上游仓库已归档（archived）这件事，vsdb 上没有任何记载** —— vsdb 只是停在 R1 一条孤立记录，没有 archived/deprecated 标记，这是它停更导致的信息缺失，不是"未归档"的证据。

**这是本项目的真实风险，不是理论风险**：`imwri` 是 `required_plugins` 的成员，图片素材（过渡图、静态图循环）完全依赖它。当前 R73 上工作正常，但上游不会再有修复。**替代路径必须在升级 VapourSynth 之前先确认**，见下。

## `BestSource` — 潜在的统一替代（vsdb 查不到）

vsdb 上**完全没有** `BestSource` 条目（停更所致）。能查到的邻居是 `BestAudioSource`（`com.vapoursynth.bestaudiosource`，R1，`vapoursynth/bestaudiosource`），印证 `vapoursynth` 官方组织确实在做 `Best*Source` 系列。

社区共识是 `BestSource` 意在统一 `lsmas` + `imwri` + `ffms2`（**这是推断，vsdb 无可交叉核实的信息**）。若要评估，**必须自行核实三件事**，缺一即判为不可用：

1. 是否单 DLL（能否直接放 `vs-plugins/`，不走 pip/vsrepo）—— 本项目部署模型的硬约束，见 [05-plugin-autoload-portable.md](05-plugin-autoload-portable.md)。
2. 是否兼容 **api 4.1**（R73）；若只支持 4.2+，则与升级 VapourSynth 绑定成一件事。
3. 图片读取能力是否覆盖 `imwri.Read` 当前用法（`core/vs_script.py:58` 的 `.vpy` 生成路径也要跟着改，会动 golden）。

**注意**：官方入门文档 `gettingstarted.html` 的示例已改用 `core.bs.VideoSource`（BestSource），而 `lsmas` 在官方核心文档六个页面里**一次都没出现**——官方推荐重心已经转移。这不影响 R73 上的现状，但是升级时的方向信号。

## 叠加/合成：继续用核心内置，不引插件

vsdb 上检索"叠加/合成"的结果全部是**脚本层封装**（PyScript），且都依赖 `std.Merge` 等核心函数：

| 脚本包 | 相关函数 | 逐字描述 |
|---|---|---|
| `havsfunc` | `Overlay` | "Simplified Overlay(), does not perform any checking or fitting. Users need to take care of inputs themselves." |
| `xvs` | `Overlaymod` | "modified overlay by xyx98. Based on havsfunc.Overlay()" |
| `havsfunc` | `InsertSign` | "This overlays a clip onto another. Default matrix for RGB -> YUV conversion is 601 to match AviSynth's Overlay()" |

**没有任何独立编译的 VSPlugin 专做叠加合成**。vsdb 分类为 `Effects and Transitions` 的条目全是转场/噪声（`colorfade`、`AddGrain`、`vctrans`、`NoiseGen` 等），不是叠加。

唯一的独立转场插件 `vctrans`（`in.trans.vcm`）最后更新 **2015-09-10**、无 vsrepo 支持、所有函数的位深/色彩空间标注均为 `unknown`、**无任何 api 4.x 兼容性说明** —— 判为不可用。

**结论：引入 `havsfunc` 只会新增一个 Python 脚本依赖而不减少任何插件依赖（它底层还是 `std.Merge`）。继续直接调核心函数是正确选择。**

## `fpng` / `vsfpng`

vsdb 上查不到（停更所致）。本项目当前没有 PNG 编码需求（导出走 `VSPipe → x264-7mod`），无需评估。

## 相关

- [05-plugin-autoload-portable.md](05-plugin-autoload-portable.md) — 单 DLL 便携加载与 `portable.vs` 锚点
- [05-plugin-autoload-portable.md](05-plugin-autoload-portable.md) — `LWLibavSource` 与 `.lwi` 索引
- [08-version-upgrade-notes.md](08-version-upgrade-notes.md) — 升级前的检查清单
- [10-research-method.md](10-research-method.md) — 这些结论是怎么抓到的、哪些未核实
