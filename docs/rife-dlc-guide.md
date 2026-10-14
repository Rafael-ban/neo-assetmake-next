# RIFE 补帧 DLC：从安装到使用

RIFE 可以把低于 60 帧的视频补到 60 帧，让运动更连贯。
DLC 已包含插件和模型，完整安装即可，不用另外下载模型。

> **先确认主程序版本**
>
> 下面的直接启用流程用于支持 DLC 自动识别的主程序。
> GitHub 原 v2.2.1 发布包没有自动识别功能；继续使用它时，安装后还要按同包的
> `RIFE-DLC-v2.2.1.txt` 完成一次配置，再进行第 3 步。
> 自动识别修复目前已进入本地源码（9545150），尚未发布为新版 EXE。

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

从 [v2.2.1 发布页](https://github.com/Rafael-ban/neo-assetmaker-dev/releases/tag/v2.2.1)
下载 `RIFE-DLC-r9_mod_v33.zip`。DLC 是附加包，使用前需要已经安装主程序。

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

支持自动识别的程序会自行找到插件和模型，到这里就完成了。无需修改 JSON、模型路径或脚本参数。
如果你使用的是原 v2.2.1 发布包，请先完成包内旧版说明中的配置。

## 第 4 步：预览、截取和导出

先确认画面能持续播放，再设置截取范围、拖动裁剪框，最后导出。
编辑画面显示的是**已经补帧、尚未裁剪的完整画面**；导出使用裁剪后的结果：

```text
视频 → 补帧 → 编辑预览 → 截取和裁剪 → 导出
```

- 补帧会增加计算量。目标为 60 帧，不代表所有电脑都能实时预览 60 帧。
- 图片、只有一帧的视频，以及原本已经达到 60 帧的视频，会跳过补帧。
- 已经截取过的项目切换补帧后，要重新检查起止位置。当前版本不会自动换算旧的截取帧号。

导出一段短视频并打开查看，确认范围和播放效果符合预期后，再制作完整素材。

## 不想补帧了，怎么关？

把“VapourSynth 脚本 → 来源”改回 **内置脚本**，重新核对截取范围并保存项目。
不用删除 DLC，也不用修改脚本文件。

## 遇到问题时查这里

| 看到的情况 | 先做什么 |
| --- | --- |
| 提示缺少 `rife.RIFE` 或 `rife_dlc_missing` | 对照第 2 步检查文件位置。若运行的是原 v2.2.1 EXE，还要完成旧版专用说明中的配置。 |
| 提示模型目录或模型文件缺失 | 检查上图中的 `flownet.bin` 和 `flownet.param`。重新完整解压 DLC，不要只复制 DLL。 |
| 提示缺少 `VCOMP140.DLL` 等运行库 | 从[微软官网](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)安装或修复 Visual C++ v14 **x64** 运行库，然后重新启动软件。 |
| 提示 Vulkan 或 RIFE 插帧失败 | RIFE 需要支持 Vulkan 的显卡及对应驱动。查看日志中的具体错误；可先切回内置脚本继续基础制作。 |
| 开启后预览明显变慢 | 先试短视频。切回内置脚本比较，确认是否是补帧计算带来的开销。 |
| 提示 `loop.mp4` 不存在 | 重新选择实际视频。这是素材路径问题，重新安装 DLC 不能找回文件。 |

## 从源码启动的人看这里

如果你运行的是 `main.py`，第 1 步的目标换成 **main.py 所在的仓库文件夹**。
同样把完整 DLC 的 `tools`、`resources` 放进去，再选择 RIFE 脚本。

源码目录和编译后的 EXE 目录是两份程序。给一份安装 DLC，不会自动安装到另一份。
基础构建不包含可选 DLC；编译完成后要给实际运行的 EXE 目录单独安装。
开发者下载依赖、校验和打包命令见仓库的《17 · 默认滤镜参数与扩展插件》，普通使用不需要执行这些命令。
