# RIFE 手动配置与 v2.2.1 构建识别

`v2.2.1` 标签曾重复发布，请以构建 commit 判断行为。已发布的 run `38861133446`
（commit `3fcff63`）仍有运行配置与模型路径问题，当前源码修复尚未发布。
升级需要重新构建并部署配套 EXE 与 DLC，不能靠覆盖 `lib`、`tools` 或 `.vpy` 升级主程序逻辑。
常规安装见 [RIFE DLC 使用说明](rife-dlc-guide.md)。

## 标准安装无需手动路径

完整 DLC 的 `tools`、`resources` 应合并到当前 EXE 所在目录。
当前源码会发现 `tools/vs-rife/rife` 下的插件，默认模型取实际加载的
`core.rife.plugin_path` 所在目录下的 `models/rife-v4.6_ensembleFalse`。
预览、导出及导出预检使用同一份有效运行配置。

## 使用外置插件目录

如需使用自备 RIFE DLL，可关闭编辑器后编辑
`%APPDATA%/ArknightsPassMaker/vapoursynth/vs_runtime.user.json`：

```json
{
  "plugins": {
    "native_plugin_dirs": [
      "D:\\MyVSPlugins\\rife"
    ]
  }
}
```

填写包含 DLL 的目录，并在该目录保留 `models/rife-v4.6_ensembleFalse`。
JSON 中反斜杠需写成 `\\`；请用实际存在的 Windows 绝对路径。
已有文件只合并 `plugins.native_plugin_dirs`，保留其他字段和插件目录，避免加载两份 RIFE DLL。
手动配置的有效外置目录优先于本机 DLC，默认模型也跟随被选中的 DLL。

旧配置中已经不存在的 旧版 `tools/vs-rife/rife` 目录，仅在当前程序的本地 DLC
可用时会被忽略；其他手动配置不会被自动覆盖。若报路径错误，请修正实际失效的条目。
用户配置由同一 Windows 用户的不同程序副本共用，移动或删除外置插件后需要同步修改。
不要将这些配置写入项目 `epconfig.json`，也不要覆盖随包的 `config/vs_runtime.json`。

## 单独指定模型目录

只有模型与 DLL 分开存放时才需要修改脚本。编辑软件当前实际选用的 `.vpy`，
将整个 `RIFE_MODEL_PATH` 表达式替换为模型目录的绝对路径，例如：

```python
RIFE_MODEL_PATH = Path(r"D:\Models\rife-v4.6_ensembleFalse")
```

该目录内应直接包含 `flownet.bin` 与 `flownet.param`。如果使用的是复制到项目或其他位置的脚本，
修改程序目录的同名脚本不会改变它。保存后重新加载脚本，并重新核对截取范围。
