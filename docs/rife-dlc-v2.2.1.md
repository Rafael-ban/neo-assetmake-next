# v2.2.1 原版：手动启用 RIFE

**本页只适用于 GitHub 原 v2.2.1 发布包及与它配套的旧版 DLC。新版磁盘缓存脚本需要升级主程序，不能靠以下配置让旧 EXE 支持。支持自动识别的构建无需执行这些步骤。**

先把完整 DLC 的 `tools`、`resources` 两个文件夹复制到 EXE 所在目录，再进行下面的配置。
本页以 `D:\Apps\ArknightsPassMaker` 为程序位置；请替换成你实际使用的目录。
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

保存文件并重新启动编辑器。打开项目后，在“VapourSynth 脚本 → 来源”选择“全局本机脚本”，
再选中程序目录里的 `resources\vapoursynth\rife_pipeline.vpy`。
