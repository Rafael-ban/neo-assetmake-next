# 12 · 实战陷阱（doom9 f=82，社区意见）

**结论：五条与本项目管线直接相关的陷阱。本项目已规避三条（有代码实证），一条不适用，一条仍是未处理风险。**

⚠️ **本条目全部为论坛意见，非官方文档。** 每条标注发帖者身份（是否插件作者本人）与是否有他人复现。抓取方法与限流见 [10-research-method.md](10-research-method.md)。

---

## A. 已被本项目规避（有代码实证）

### A1 · `.vpy` 里的 `print()` 会污染 y4m 管道 🔬 已规避

**来源**：doom9 t=186257，提问者 `_Al_`，回复者 **HolyWu**（知名插件作者，`vs-mlrt` 等维护者 —— 权威性高，且问题被复现）。

> `vspipe.exe test.vpy -c y4m - | ffmpeg.exe -y -i - out.mp4`
> …Notice that print() statement in it…So if that print() is in that script, it fails if using python to encode.

HolyWu 的修复：`print("Hi", file=sys.stderr)`。

**机制**：VSPipe 的 stdout **同时承载 y4m 帧数据**，脚本里任何 `print()` 默认写 stdout，会插进帧流中间 → 下游解析失败或死锁。

**本项目状态**：`grep -n "print(" core/vs_script.py` → **零命中**。生成的 `.vpy` 不含任何 stdout 输出，管线（`core/media_pipeline.py`：`VSPipe → x264-7mod → MP4Box`）安全。

**维护约束**：日后若要在 `.vpy` 里加调试输出，**必须** `file=sys.stderr`。这也是 `tests/test_vpy_golden.py` 逐字节钉死 `.vpy` 的一个附带好处 —— 意外加入的 `print` 会立刻让 golden 失败。

### A2 · `.lwi` 索引写在源目录会踩只读介质/脏目录 ⚠️→🔬 已规避

**来源**：doom9 t=183198，`Quadratic`（普通用户，讨论的是 `ffms2` 的 `.ffindex`，同类机制）。

> Write your cache file in a temporary location. Since you're dealing with so many files, it would be best to try and automate this.

**本项目状态**：`core/vs_engine.py:209` `lwi_cache_path()` 已经这么做 —— 预览索引落在 `get_app_dir()/.cache/lwi/`，文件名是 `<stem>_<sha1(abspath)[:16]>.lwi`。三个好处：用户素材目录保持干净、只读介质不受影响、**非 ASCII 路径被哈希掉**（不进文件名）。导出侧另走 staging 目录并在 `finally` 删除。

### A3 · 便携模式只从便携子目录加载插件 ✅ 官方作者确认

**来源**：doom9 t=165771，**Myrsloik**（VapourSynth 本体作者 —— 最高权威，且给出源码行号）。

> But it doesn't. Portable mode only loads from the portable subdirs.
> See: https://github.com/vapoursynth/vapoursynth/blob/master/src/core/vscore.cpp#L1846

**这是本项目 `portable.vs` + `vs-plugins/` 机制唯一的权威印证**（文件名本身仍无文档提及，见 [05-plugin-autoload-portable.md](05-plugin-autoload-portable.md)）。

Selur 同帖给出的**规避自动加载**技巧（若将来需要精确控制加载哪些插件）：把 `plugins` 目录改名、建一个空 `plugins` 目录，然后显式 `core.std.LoadPlugin(path=...)`。

---

## B. 不适用于本项目

### B1 · 32 位便携版 `vsscript_init()` 段错误

**来源**：doom9 t=185270，`LigH`（资深用户）报告，**Selur** 复现并加强结论：

> Vapoursynth 32bit portable seems problem to me...=> seems to me 32bit portable Vapoursynth never worked.

**为何不适用**：本项目是 64 位（`tools/media/` 下 R73 x64）。

**但有侧面价值**：它说明 **VS 初始化在错误环境下是段错误而非异常** —— 与本项目"VS core 必须在 PyQt6 之前预热，否则 exit 139"是同类现象（初始化极度敏感、失败不可捕获），虽非同一触发条件。这是目前唯一能与那条硬约束呼应的公开材料；论坛**没有**任何关于 VS/Qt 初始化顺序的独立讨论。

---

## C. 仍未处理的风险

### C1 · `max_cache_size` 在 R73 上不是硬约束 ⚠️ 需注意

**来源**：doom9 t=165771，**Myrsloik**（作者本人，R76RC2 发布说明）：

> Note that R76 has extensive changes to how max_cache_size and num_threads is handled. **Previous VS versions would in some cases happily use far more memory than max_cache_size allowed and more or less ignore it.** That time is over…
> Another thing that can greatly drive up memory usage is having many threads running. Therefore VS will now use up to num_threads but if the running script is memory constrained the actually used number will be decreased.

同作者在 t=185774 补充缓存行为：

> Unlike avisynth that always caches up to the max limit vapoursynth will shrink/grow caches based on what's optimal for the current request pattern…
> 4GB is enough for just about all fullhd script and probably a significant number of 4k ones.

> You never get 'problems', what you get is either slowdown due to the cache being too small (and maybe a warning) or slowdown due to the cache using too much ram and things start paging.

**对本项目的含义**：项目锁定 **R73**，即"`max_cache_size` 会被大体忽略"的旧行为。所以**不能把它当成内存上限的保证**。预览是长时间运行 + 大量随机 seek（拖时间轴），正是作者所说"请求模式变化导致缓存伸缩"的场景。

**缓解现状**：[11-preview-zoom.md](11-preview-zoom.md) 的视口裁剪把缩放的单帧成本压在视口尺寸（实测 100x 仅 1.53 ms），避免了最容易撑爆缓存的那条路径。但**整体内存行为在 R73 上仍是软约束**，升级到 R76+ 时这一条会反向变化（变严格后可能出现"缓存太小导致变慢"），属 [08-version-upgrade-notes.md](08-version-upgrade-notes.md) 的检查项。

### C2 · 子采样整除约束会在参数没对齐时直接报错 ⚠️

**来源**：doom9 t=183193，**Selur**（Hybrid 作者，熟悉此类边界）：

> does not work here it gives:
> `image dimensions must be divisible by subsampling factor`
> so instead of +1 one needs to use at least +4

以及 fmtconv 作者 **cretindesalpes**（t=166504）确认这类维度检查是通用行为。

**对本项目的含义**：导出链在 `resize` 之后是 `YUV420P8`（4:2:0），宽高必须是偶数；`CropAbs`/`AddBorders` 的参数若未对齐会**直接抛错**。项目已在 `core/vs_graph.py` 用 `& ~1` 做偶数对齐（裁剪偏移与尺寸），[11-preview-zoom.md](11-preview-zoom.md) 的缩放窗口同样对齐。

**未处理部分**：论坛**没有**针对 `Transpose`/`CropAbs`/`AddBorders` 三者各自报错文案的讨论，只确认了这类检查的通用存在性。极端裁剪框（接近 `_MIN_CROP_SIDE=64`）+ 旋转组合下的对齐是否始终成立，目前靠 `tests/test_crop_aspect_lock.py` / `test_crop_rotation_remap.py` 覆盖，**未做穷举**。

### C3 · `.lwi` 的并发与损坏恢复 ❌ 无答案

论坛检索**没有**关于 `.lwi` 并发访问（多个预览请求同时触发索引重建）、索引损坏恢复的讨论。本项目也未处理。属已知空白，需要时只能自行做压力测试。

---

## 相关

- [05-plugin-autoload-portable.md](05-plugin-autoload-portable.md) — A3 对应的部署机制
- [05-plugin-autoload-portable.md](05-plugin-autoload-portable.md) — A2/C3 对应的 `.lwi` 策略
- [06-vspipe-cli.md](06-vspipe-cli.md) — A1 对应的管线
- [08-version-upgrade-notes.md](08-version-upgrade-notes.md) — C1 是升级检查项
- [10-research-method.md](10-research-method.md) — doom9 抓取方法与 10 秒限流
