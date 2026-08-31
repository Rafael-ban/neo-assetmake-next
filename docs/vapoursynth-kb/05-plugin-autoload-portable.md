# 05 · 插件自动加载：静默失败 + `portable.vs` 机制

**结论：自动加载插件时的任何错误都被官方明确定义为"静默忽略"。缺 `portable.vs` 不会报错，只会让 `lsmas`/`imwri` 不存在——症状是"能启动，加载媒体就失败"。所以 CI 必须硬校验打包产物。**

## 官方原文 ✅ 已核实

`http://www.vapoursynth.com/doc/installation.html`（R76）：

> **Plugin Autoloading**
> VapourSynth automatically recursively loads all the native plugins located in `<site-packages>/vapoursynth/plugins`. Autoloading works just like manual loading, with the exception that **any errors encountered while loading a plugin are silently ignored**.
> An additional plugin path can be loaded by setting the `VAPOURSYNTH_EXTRA_PLUGIN_PATH` environment variable. It is loaded after the normal plugin path.

⚠️ **官方文档只描述 pip 安装形态的路径**（`<site-packages>/vapoursynth/plugins`），**完全没有提到 `portable.vs`**。便携模式的权威依据是 DLL 二进制本身，不是文档。

## `portable.vs` 机制 🔬 本机实证

对 `tools/media/vapoursynth.dll` 做字符串提取，能看到 UTF-16 常量：`portable.vs`、`vs-plugins`、`vs-coreplugins`。`tools/media/portable.vs` 是一个 **0 字节标记文件**。

机制：被加载的 `VapourSynth.dll` 在**自身所在目录**查找 `portable.vs`；存在则切换到便携布局，从同目录的 `vs-plugins/` 加载插件。

由此推出两条硬性禁令（均本机实测确证）：

❌ **禁止 `sys.path.insert(0, tools/media)`**
实测报 `AttributeError: class must define a '_type_' attribute`。原因：`tools/media` 是一个**扁平的嵌入式 CPython 3.12.10 发行版**（含 `_ctypes.pyd`、`_socket.pyd`、`python312.dll`），插到 `sys.path` 头部会**遮蔽宿主解释器的扩展模块**。

❌ **禁止把 wheel 装进 venv**
wheel 的 `RECORD` 把 DLL 落到 `Lib/site-packages/vapoursynth.dll`。由于自动加载锚定"被加载的 DLL 自身所在目录"，装进 venv 后那里没有 `portable.vs` 也没有 `vs-plugins/` → `lsmas`/`imwri` **静默缺失**。

✅ **正解**（`core/vs_engine.py` 采用）：
`os.add_dll_directory(tools/media)` + `importlib.util.spec_from_file_location` 显式加载 `.pyd`。实测：import 成功、`lsmas`+`imwri` 自动加载、`sys.path` 未污染、`ctypes` 正常。

## 对本项目意味着什么

**CI 必须硬失败**。因为"缺 `portable.vs`"的表现是静默退化而非异常，`.github/workflows/build-app.yml` 断言冻结产物里存在：`vapoursynth.pyd`、`vapoursynth.dll`、`portable.vs`、`vs-plugins/LSMASHSource.dll`、`vs-plugins/libimwri.dll`。缺任一项立即失败——否则会打出一个"能装、能开、不能用"的包。

**cx_Freeze 无需新增 `includes`**：`build.py` 的 `_collect_media_tool_include_files` 按目录遍历整个 `tools/media`，VS 运行时随之打包。

**另一条约束**（见 07）：VS core 必须在 **PyQt6 加载之前** prewarm，否则本捆绑包段错误（exit 139）。

## 相关

- [07-frame-lifetime-threading.md](07-frame-lifetime-threading.md) — prewarm 顺序与线程规则
- [08-version-upgrade-notes.md](08-version-upgrade-notes.md) — R74 起官方转向 pip 分发，对便携部署的间接风险
- `core/vs_engine.py`、`build.py`、`.github/workflows/build-app.yml`
