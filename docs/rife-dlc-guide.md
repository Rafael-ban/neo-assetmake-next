# RIFE DLC 安装与使用说明

适用于独立 RIFE DLC（r9_mod_v33）和包含 DLC 自动识别修复的主程序。
已发布的 v2.2.1 旧 EXE 不包含此修复；仅替换说明文件不会升级主程序，旧版临时配置见文末。
RIFE 用于把低帧率视频插帧到 60 fps；普通视频制作不需要安装它。

**更新后的使用流程只有两步：把完整 DLC 解压到当前程序目录，再选择 RIFE 脚本。无需编辑 JSON，也无需单独下载模型或填写模型路径。**
看到 `缺少可调用 requirement rife.RIFE`，说明当前程序还没有加载到 RIFE 插件；这不代表已经检查过模型。
若要先恢复正常制作，在“VapourSynth 脚本 → 来源”选择“内置脚本”，然后重新打开素材。

## 1. 先确认你运行的是哪一份程序

- 双击 EXE 运行：安装目录就是**这个 `ArknightsPassMaker.exe` 所在的文件夹**。可从快捷方式的“打开文件所在的位置”找到它。
- 从源码运行 `main.py`：安装目录就是**包含 `main.py`、`plugin_distribution.py` 的仓库根目录**。
- `dist` 是打包输出目录；它不是源码运行时查找依赖的目录。除非你确实在这里解压并运行了 EXE，否则不要把 DLC 安装到这里。

下文以 `D:\Apps\ArknightsPassMaker` 为安装目录。请替换成你自己的实际路径。
这里的 `tools` 就在 EXE（或源码 `main.py`）旁边；不是 `lib\tools`，也不是 `tools\media\tools`。
解压时合并根目录的 `tools`，是在其中增加独立的 `vs-rife` 子目录，不是替换整个基础媒体库。
源码和编译后的程序是两份独立文件：给其中一份装依赖，不会给另一份自动安装。
即使源码目录已经装好 RIFE，当前基础构建也不会把可选 DLC 收进主程序；编译后的程序仍须单独部署 DLC。
给编译目录补装后，之前生成的便携 ZIP 也不会自动更新。

## 2. 下载并解压 DLC

1. 关闭编辑器。
2. 打开 v2.2.1 发布页：
   https://github.com/Rafael-ban/neo-assetmaker-dev/releases/tag/v2.2.1
3. 下载 `RIFE-DLC-r9_mod_v33.zip`。它是插件附件，不是主程序，也不是通用滤镜包。
4. 将 ZIP **里面的内容**解压到第 1 步确定的安装目录，合并 `tools` 和 `resources` 文件夹。
   不要多套一层 `RIFE-DLC-r9_mod_v33` 文件夹。
5. 检查以下文件确实存在。这里列的是关键文件，不是完整清单；其余随包文件也应保留。

```text
D:\Apps\ArknightsPassMaker\
├── ArknightsPassMaker.exe       ← 源码运行时这里是 main.py
├── resources\
│   ├── packaging\vs-rife.json
│   └── vapoursynth\rife_pipeline.vpy
└── tools\vs-rife\rife\
    ├── librife_windows_x86-64.dll
    └── models\rife-v4.6_ensembleFalse\
        ├── flownet.bin
        └── flownet.param
```

运行 RIFE 还需要支持 Vulkan 的显卡及可用驱动，以及 Microsoft Visual C++ v14 **x64** 运行库。
微软官方下载说明：https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist
基础 R79 包和 RIFE DLC 都不能代替显卡驱动或系统运行库。

## 3. 在编辑器里启用

1. 解压完成后，重新启动你要使用的那一份编辑器。
2. 新建或打开项目，导入一段视频。
3. 在“VapourSynth 脚本”面板的“来源”中选择“全局本机脚本”。
4. 在弹出的文件选择框中，选择当前安装目录里的：
   `resources\vapoursynth\rife_pipeline.vpy`。
5. 等待素材加载。如果刚编辑过脚本，点击面板的“重载”。
6. 确认可以显示和播放后，再设置截取入点、出点，最后保存项目并导出。

默认目标是 60 fps。只有低于 60 fps、且至少两帧的视频会插帧；图片、单帧视频和原本 ≥60 fps 的视频不会被强制改成 60 fps。
即使当前素材会跳过插帧，只要选了这个脚本，仍要求 RIFE 插件已经加载。
将脚本里的 `RIFE_ENABLED` 改为 `False` 也不会取消插件依赖；不需要 RIFE 时请选择“内置脚本”。

RIFE 同时作用于编辑预览与导出，会增加计算量；目标 60 fps 不代表电脑一定能实时预览 60 fps。
如果需要调整参数，可以用文本编辑器修改脚本顶部的 `RIFE_ENABLED`、`RIFE_TARGET_FPS`；普通使用保持默认即可。
当前没有独立的 GUI 补帧开关或“只在导出时插帧”选项。

**已有项目切换脚本后必须重新核对截取范围。** 当前项目保存的是帧编号，不会自动按时间换算。
例如 30 fps 的第 30 帧约在第 1 秒，换成 60 fps 后第 30 帧约在第 0.5 秒。
建议先决定是否使用 RIFE，再设置入点和出点。

## 4. 按报错处理

| 提示或现象 | 含义与处理 |
| --- | --- |
| `native_plugin.rife_dlc_missing` / `native_plugin.requirement_missing` / `缺少可调用 requirement rife.RIFE` | 当前运行时没有加载到 RIFE。先确认正在运行的是包含自动识别修复的程序，再按第 2 步检查解压层级和 DLL 位置。旧 v2.2.1 EXE 需要升级，或使用文末的临时配置。报错的“已检查目录”中应包含当前安装目录的 `tools\vs-rife\rife`；只出现 `tools\vs-plugins` 里的普通滤镜不算装好了 RIFE。 |
| DLL 无法加载 / 缺少 `VCOMP140.DLL` 等运行库 | 检查是否完整解压，并安装或修复微软 VC++ v14 x64 运行库。不要仅凭缺插件提示就认定显卡不支持。 |
| `模型目录不存在` / `模型文件缺失` | 检查 `flownet.bin` 和 `flownet.param` 是否放在第 2 步列出的模型目录，而不是只复制了 DLL。 |
| `RIFE 插帧失败` / Vulkan 相关错误 | 插件加载之后仍可能推理失败；检查显卡驱动、Vulkan 支持以及日志中的具体原因。先切回内置脚本可继续基础制作。 |
| 预览变慢或超时 | 插帧需要额外计算；先用短素材验证。切回内置脚本，区分基础预览问题与插帧开销。 |
| `循环素材文件不存在: ...loop.mp4` | 项目配置引用的视频不在对应位置。重新选择实际视频；安装 DLC 不会找回丢失素材。 |
| `Release 缺少 SHA-256 校验文件` / `所有更新源均无法访问` | 属于更新检查，与 RIFE 插件加载是两个问题。v2.2.1 为预览版，当前更新器仍查询稳定版接口；可手动打开第 2 步的版本发布页。不要为解决此提示反复安装滤镜。 |

能打开脚本、打包通过或文件校验通过，都不等于已在你的显卡上完成插帧验证。
请用一小段视频确认预览能播放、导出可回读，且实际截取范围正确，再处理长素材。

## 5. 暂停使用或移除

暂时不用：在编辑器中选择“内置脚本”，重新核对入出点并保存项目即可。

彻底移除：先切回内置脚本并关闭编辑器，再删除当前安装目录下的 `tools\vs-rife`。
如果曾按旧教程手动配置过 `native_plugin_dirs`，同时移除其中的 RIFE 路径；保留其他插件设置和文件。

## 附录：尚未升级的 v2.2.1 旧 EXE 临时配置

**仅旧版本需要以下操作。更新后的标准 DLC 安装不需要做这一节。**
旧版本不会自动加载独立 DLC；如果暂时不能升级，可以配置当前 Windows 用户的插件路径。编辑器保持关闭。

1. 按 `Win + R`，输入 `%APPDATA%`，回车。
2. 进入 `ArknightsPassMaker\vapoursynth`；不存在就依次新建这两个文件夹。
3. 用文本编辑器打开 `vs_runtime.user.json`。文件不存在则新建，保存为 UTF-8（不带 BOM）；
   注意文件名不能变成 `vs_runtime.user.json.txt`。
4. 如果是新文件，写入以下内容，**把示例安装目录替换为你的实际安装目录**：

```json
{
  "plugins": {
    "native_plugin_dirs": [
      "D:\\Apps\\ArknightsPassMaker\\tools\\vs-rife\\rife"
    ]
  }
}
```

这里填的是包含 RIFE DLL 的文件夹，不是 DLL 文件本身，也不是 `tools\vs-rife` 上一级目录。
JSON 中每个反斜杠要写成 `\\`，不能直接粘贴单反斜杠，也不要改成 `/`；当前配置要求 Windows 绝对路径。

**如果文件已有内容，不要用上面整段覆盖。** 保留原来的 `worker`、`core`、`scripts` 等字段，
在已有 `plugins.native_plugin_dirs` 数组中新增 RIFE 路径，并保留其他插件目录。
只有这个字段不存在时才添加它。数组项之间用逗号分隔，最后一项后面不能有逗号；JSON 不支持注释。

例如，你原来已经有一个自定义插件目录，修改后可以是：

```json
{
  "plugins": {
    "native_plugin_dirs": [
      "D:\\MyVSPlugins",
      "D:\\Apps\\ArknightsPassMaker\\tools\\vs-rife\\rife"
    ]
  }
}
```

`D:\MyVSPlugins` 只是展示“保留原有目录”的例子；没有这个目录时不要照抄。
旧版若配置过 `tools\vs-plugins\rife`，请用新的 `tools\vs-rife\rife` 路径替换它。
不要同时添加两份 RIFE DLL 所在目录。

这份用户配置由同一 Windows 用户运行的源码版、安装版和便携版共用。
换用另一份程序或移动安装文件夹后，需要把 RIFE 路径改为当前那一份；不要仍指向已经删除的旧目录。
不要把此配置写进项目的 `epconfig.json`，也不要覆盖程序随包的 `config\vs_runtime.json`。

保存后重新启动编辑器，再按第 3 步选择 RIFE 脚本。
