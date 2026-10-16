# RIFE 补帧 DLC：从安装到使用

RIFE 可以把低于 60 帧的视频补到 60 帧，让运动更连贯。
DLC 已包含插件和模型，完整安装即可，不用另外下载模型。

> **使用同次构建的主程序与 DLC**
>
> `v2.2.1` 标签曾重复发布，不能只凭版本号判断功能。已发布的 run `38861133446`
> （commit `3fcff63`）仍有运行配置与模型路径问题。本页对应 `v2.2.2` 配套构建，
> 修复代码基于 `598a972`；请使用同一版本提供的 EXE 与 DLC。
> 需要重新构建并部署配套 EXE 与 DLC；只覆盖 `lib`、`tools` 或脚本不能升级主程序逻辑。

## 第 1 步：找到你正在使用的程序文件夹

在资源管理器里找到 **ArknightsPassMaker.exe**。这个 EXE 所在的文件夹，就是 DLC 要放的位置。
如果平时用快捷方式启动，可以右键快捷方式，选择“打开文件所在的位置”。

例如，你的程序在：

```text
D:\ArknightsPassMaker1\ArknightsPassMaker\ArknightsPassMaker.exe
```

那么 DLC 应装到：

```text
D:\ArknightsPassMaker1\ArknightsPassMaker\
```

不要继续进入 `lib` 或 `tools\media`。安装前先关闭编辑器。

## 第 2 步：把 DLC 的两个文件夹复制进去

使用与当前主程序同次发布的 `RIFE-DLC-r9_mod_v33.zip`。DLC 是附加包，使用前需要已经安装配套主程序。
本说明对应 `v2.2.2`；旧 `v2.2.1` 用户需要同时升级主程序与 DLC。

1. 打开或解压这个 ZIP，找到里面的 **tools** 和 **resources** 两个文件夹。
2. 同时复制这两个文件夹。
3. 回到第 1 步找到的 EXE 所在文件夹，粘贴。
4. Windows 提示合并同名文件夹时，选择合并。重新安装同一 DLC 时，可替换它的同名文件。

**复制的是 ZIP 里面的 tools 和 resources，不是外面包着它们的 RIFE-DLC 文件夹。**

完成后，位置应该像这样：

```text
ArknightsPassMaker\
├─ ArknightsPassMaker.exe
├─ resources\vapoursynth\rife_pipeline.vpy
└─ tools\
   ├─ media\                         原来的基础运行环境
   └─ vs-rife\rife\                  新增的补帧插件
      ├─ librife_windows_x86-64.dll
      └─ models\rife-v4.6_ensembleFalse\
         ├─ flownet.bin
         └─ flownet.param
```

`tools` 与 EXE 同级。合并文件夹是在 `tools` 里增加 `vs-rife`，不用删除原来的 `media`。
上图只列出用于确认位置的文件；DLC 里的其他文件也要保留。

## 第 3 步：在软件里打开补帧

1. 重新启动编辑器，新建或打开项目，导入视频。
2. 找到 **VapourSynth 脚本** 面板。
3. 把“来源”改为 **全局本机脚本**，会弹出文件选择窗口。
4. 进入刚才的程序文件夹，打开 `resources` → `vapoursynth`，选择 **rife_pipeline.vpy**。
5. 等待视频加载，然后点击播放。

标准 DLC 位于 EXE 根的 `tools/vs-rife/rife` 时，程序会自行找到插件。模型默认跟随实际加载的 RIFE DLL，使用 DLL 同目录的 `models/rife-v4.6_ensembleFalse`；无需修改 JSON 或脚本参数。
预览、导出和导出预检使用同一份有效运行配置。
如需手动指定插件或模型位置，见[手动配置与构建识别](rife-dlc-v2.2.1.md)。

## 第 4 步：预览、截取和导出

先确认画面能持续播放，再设置截取范围、拖动裁剪框，最后导出。
编辑画面显示的是**已经补帧、尚未裁剪的完整画面**；导出使用裁剪后的结果：

```text
视频 → 补帧并按需缓存到项目目录 → 编辑预览 → 截取和裁剪 → 缩放 → 导出格式转换
```

- 补帧会增加计算量。目标为 60 帧，不代表所有电脑都能实时预览 60 帧。
- 图片、只有一帧的视频，以及原本已经达到 60 帧的视频，会跳过补帧。
- 已经截取过的项目切换补帧后，要重新检查起止位置。当前版本不会自动换算旧的截取帧号。

导出一段短视频并打开查看，确认范围和播放效果符合预期后，再制作完整素材。

## 缓存与内存回收

以下说明对应当前源码；发布产物是否具备这些行为，以构建 commit 为准。

- 已保存项目的补帧结果保存在项目内 `.assetmaker-vs/rife-cache/`，预览和导出共同使用。
- 未保存的临时项目位于当前 EXE 或源码根的 `.assetmaker-work/projects/project_<unique>/`；保存项目后使用目标项目的缓存目录。程序目录需要可写。
- `%APPDATA%/ArknightsPassMaker/vapoursynth/vs_runtime.user.json` 仍保存小型用户运行配置；上述路径调整不表示整个应用不再使用 AppData。
- 缓存按需生成，默认最多占用约 8 GiB 磁盘，达到预算后淘汰旧帧；不是整片永久保存。
- 缓存位于补帧后的源分辨率支路，发生在截取和裁剪之前。未压缩 RGBS 帧约占 `宽 × 高 × 12` 字节：3840×2160 素材约 94.9 MiB/帧，8 GiB 约能存 86 帧，即 60 fps 下约 1.4 秒（未计帧属性和文件开销）。4K 长循环重复访问已淘汰帧时仍会重算；小尺寸样本的缓存加速不代表 4K 也能持续命中。
- 新缓存直接保存未压缩的浮点帧，减少播放时反复解压的等待，画面精度不变。它比旧压缩缓存占用更多磁盘，因此相同预算下保留的帧数较少；旧缓存会在使用时逐帧转换，无需手动清空。
- 暂停或静态显示空闲约 3 秒后，程序关闭预览计算进程来释放内存和显存，画面仍保留。
  再次播放或定位会恢复计算，因此首次响应可能稍慢。
- 导出时先释放预览的计算资源；原本暂停的预览在导出后继续保持暂停。
- 需要回收磁盘空间时，使用“操作 → 清除补帧缓存”。后续访问会重新计算。
- 16000 MB 是 RIFE 的工作缓存设置，不是编辑器总内存或显存上限。

## 不想补帧了，怎么关？

把“VapourSynth 脚本 → 来源”改回 **内置脚本**，重新核对截取范围并保存项目。
不用删除 DLC，也不用修改脚本文件。

## 遇到问题时查这里

| 看到的情况 | 先做什么 |
| --- | --- |
| 提示缺少 `rife.RIFE` 或 `rife_dlc_missing` | 对照第 2 步检查文件位置。确认主程序与 DLC 来自配套构建，并按构建 commit 检查是否包含修复。 |
| 提示模型目录或模型文件缺失 | 检查上图中的 `flownet.bin` 和 `flownet.param`。重新完整解压 DLC，不要只复制 DLL。 |
| 提示缺少 `VCOMP140.DLL` 等运行库 | 从[微软官网](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)安装或修复 Visual C++ v14 **x64** 运行库，然后重新启动软件。 |
| 提示 Vulkan 或 RIFE 插帧失败 | RIFE 需要支持 Vulkan 的显卡及对应驱动。查看日志中的具体错误；可先切回内置脚本继续基础制作。 |
| 开启后预览明显变慢 | 先试短视频。切回内置脚本比较，确认是否是补帧计算带来的开销。 |
| 提示 `loop.mp4` 不存在 | 重新选择实际视频。这是素材路径问题，重新安装 DLC 不能找回文件。 |

## 从源码启动的人看这里

如果你运行的是 `main.py`，第 1 步的目标换成 **main.py 所在的仓库文件夹**。
保留当前仓库自带的 `resources/vapoursynth/rife_pipeline.vpy`，不要用旧 DLC 的脚本覆盖它。
在仓库根执行以下命令，按当前清单安装插件和模型，然后选择仓库中的 RIFE 脚本：

```powershell
uv run python plugin_distribution.py sync --app-dir . --manifest resources/packaging/vs-rife.json
```

源码目录和编译后的 EXE 目录是两份程序。给一份安装 DLC，不会自动安装到另一份。
基础构建不包含可选 DLC；编译完成后要给实际运行的 EXE 目录单独安装。
开发者下载依赖、校验和打包命令见仓库的《17 · 默认滤镜参数与扩展插件》，普通使用不需要执行这些命令。
