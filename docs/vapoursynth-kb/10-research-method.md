# 10 · 调研方法与核实清单（本库怎么被建立、哪些还不可信）

**结论：本库的信息源全部要用 `curl` 抓取，WebFetch 在这几个域名上不可用。每条结论都带置信度标记；这一条记录如何复现抓取，以及哪些结论仍是转述、不可当事实用。**

## 抓取方法（可复现）

统一命令形态：

```bash
curl -sS -m 30 -L -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" <URL> | sed 's/<[^>]*>//g'
```

- `-L` 必需：`vapoursynth.com` 与 doom9 都有跳转。
- `-A` 必需：doom9 对空 User-Agent 返回拦截页。
- `sed 's/<[^>]*>//g'` 去标签：这几个站都是纯静态 HTML（Sphinx / vBulletin / WordPress），**不需要 JS 渲染**。

### 各源的具体限制

| 源 | 限制 |
|---|---|
| `vapoursynth.com/doc/*` | Sphinx 静态站，直接可抓。页眉显示的是**当前最新版**（R76+），**不是** R73 —— 逐条核对版本标注 |
| `vsdb.top` | 单页内嵌全部条目，去标签后约 **912 KB**。**禁止整体读入上下文**，只能 `grep -n` 取片段。数据停更于 2022-03-19 |
| `vapoursynth.com/2026/08/` | WordPress 归档页，一次抓取会把多个月份的旧文一并列出 |
| `forum.doom9.org` | 板块页 `forumdisplay.php` 只有标题索引，**无正文**；正文要走 `search.php?do=process&forumchoice[]=82` 或 `showthread.php?t=`。**搜索接口强制两次间隔 ≥10 秒**（guest 限流），需 `sleep` 分批 |
| `github.com/<user>` | 用 API（`api.github.com/users/<u>/repos`）取仓库列表；文件内容走 `raw.githubusercontent.com/<repo>/<tag>/<path>`。**部分 tag 的 raw 请求会反复超时**，抓不到就如实标注 |

## 置信度标记约定

全库统一使用：

| 标记 | 含义 |
|---|---|
| ✅ 已核实 | 抓到官方文档/源码**逐字原文**并引用 |
| 🔬 运行时定论 | 在本捆绑包（R73 / api 4.1）上跑过探针，**以实测为准** |
| ⚠️ 转述/推断 | 有依据但非逐字原文，或来自论坛/社区意见 |
| ❌ 未核实 | 想要但**没抓到**，明确标注缺口，不填猜测 |

**规则：🔬 优先级最高。** 官方文档描述的是当前版本（R76+），与 R73 冲突时以探针为准 —— [01-colour-range-props.md](01-colour-range-props.md) 就是一个文档与早期误判都被探针推翻的实例。

## 全库核实状态清单

| 条目 | 主要证据类型 | 缺口 |
|---|---|---|
| 01 色彩范围帧属性 | 🔬 `resize` 语义探针 + ✅ 官方原文 | 无（已定论） |
| 02 resize 语义 | ✅ 官方原文 + 🔬 帧属性优先级探针 | `dither_type`/Bicubic b/c 的取值经验 ❌ 未找到权威建议 |
| 03 几何滤镜约束 | ✅ 官方原文 | `Transpose`/`CropAbs`/`AddBorders` 三者专属的子采样报错文案 ❌ 未找到 |
| 04 便携部署 | ⚠️ 官方仅描述 pip/site-packages 布局 | **`portable.vs` 这个文件名官方文档零提及** ❌ —— 本项目机制无官方背书，见下 |
| 05 lsmas 源 | ⚠️ 插件无官方文档站 | `.lwi` 并发访问/损坏恢复/非 ASCII 路径 ❌ 论坛也没有答案 |
| 06 VSPipe 与导出管线 | ✅ 官方 `output.html` 选项表 | 无 |
| 07 帧生命周期与线程 | ✅ 官方 `get_frame_async` 原文 | **"回调内不得碰 Qt"官方从未提及** ⚠️ 属工程经验 |
| 08 版本升级注记 | ✅ GitHub release notes 逐条 | R77/R80A1/A2 的 `VapourSynth4.h` ❌ raw 请求超时抓不到 |
| 09 插件生态 | ⚠️ vsdb 停更，字段缺失 | `BestSource` 全部信息 ❌ vsdb 无收录 |
| 11 预览缩放 | 🔬 成本与契约实测 | 无 |
| 12 实战陷阱 | ⚠️ 论坛意见 | 见该条目内逐项标注 |

## 三处"官方文档留白、本项目自行填补"

这三处**没有任何官方文档支持或否定**，是项目独立摸索的工程实践。改动它们时不要去官方文档找依据（找不到），只能靠探针：

1. **DLL 显式加载**：`os.add_dll_directory` + `spec_from_file_location`（`core/vs_engine.py`）。官方推荐的是 `pip install vapoursynth`，与本项目做法相反。
2. **`portable.vs` 锚点**：官方 `installation.html` 讲的是 `<site-packages>/vapoursynth/plugins` 自动加载，**从未提到 `portable.vs` 这个文件名**。但官方明确写了插件加载失败 **"silently ignored"**，与本项目"缺 `portable.vs` 会静默降级为无插件"的观察一致 —— 失败**品性**吻合，**锚点机制**无文档印证。
3. **VS core 必须在 PyQt6 之前预热**：doom9 论坛检索无任何独立复现讨论。仅有旁证：位数不匹配时 `vsscript_init()` 会**段错误而非抛异常**，说明 VS 初始化在错误环境下确实以段错误方式失败 —— 同类现象，非同一场景。

## 复现全库调研的入口

五个源：
- `https://www.vapoursynth.com/doc/introduction.html`（及 `installation` / `gettingstarted` / `pythonreference` / `apireference` / `functions/*` / `output.html`）
- `https://vsdb.top/`
- `https://www.vapoursynth.com/2026/08/`（+ `github.com/vapoursynth/vapoursynth/releases`）
- `https://forum.doom9.org/forumdisplay.php?f=82`
- `https://github.com/mawen1250`

## 相关

- [INDEX.md](INDEX.md) — 按问题查条目
- [08-version-upgrade-notes.md](08-version-upgrade-notes.md) — 升级前重跑哪些探针
