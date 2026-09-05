# VapourSynth 知识库索引

**先从本页按问题定位，再只读取命中的文章。** 本库服务于本项目的用户 `.vpy`
架构，不是完整的 VapourSynth 滤镜百科。

## 快速路由

| 你要解决的问题 | 先读 |
|---|---|
| 写第一份项目脚本、导入视频/图片、添加滤镜 | [13 用户 VPY ABI](13-user-vpy-abi.md) |
| 脚本为何能预览、如何避免过时帧覆盖新画面 | [14 worker 协议](14-worker-protocol.md) |
| `set_output()`、尺寸/颜色/帧率为何导出失败 | [15 输出契约](15-output-contract.md) |
| 项目脚本是否安全、为何修改后要再次确认 | [16 脚本信任](16-script-trust.md) |
| 旋转、裁剪、补边与 YUV 子采样 | [03 几何](03-geometry-filters.md)、[04 Trim/Loop](04-trim-loop-zero-length.md) |
| resize、矩阵、范围与色彩标签 | [01 色彩](01-colour-range-props.md)、[02 Resize](02-resize-semantics.md)、[15 输出契约](15-output-contract.md) |
| 插件如何被便携运行时加载 | [05 便携插件](05-plugin-autoload-portable.md)、[09 插件生态](09-plugin-ecosystem.md)、[13 用户 VPY ABI](13-user-vpy-abi.md) |
| VSPipe、编码和导出排错 | [06 VSPipe](06-vspipe-cli.md)、[13 用户 VPY ABI](13-user-vpy-abi.md)、[15 输出契约](15-output-contract.md) |
| 预览缩放、帧生命周期或线程 | [07 帧生命周期](07-frame-lifetime-threading.md)、[11 预览缩放](11-preview-zoom.md)、[14 worker 协议](14-worker-protocol.md) |

## 文章目录

| # | 文件 | 主题 |
|---|---|---|
| 01 | [colour-range-props.md](01-colour-range-props.md) | 色彩范围与帧属性 |
| 02 | [resize-semantics.md](02-resize-semantics.md) | Resize 的矩阵、范围和核参数 |
| 03 | [geometry-filters.md](03-geometry-filters.md) | 旋转、裁剪与子采样约束 |
| 04 | [trim-loop-zero-length.md](04-trim-loop-zero-length.md) | Trim、Loop 和零长度 clip |
| 05 | [plugin-autoload-portable.md](05-plugin-autoload-portable.md) | 便携插件自动加载 |
| 06 | [vspipe-cli.md](06-vspipe-cli.md) | VSPipe 与编码管道 |
| 07 | [frame-lifetime-threading.md](07-frame-lifetime-threading.md) | 帧内存和线程边界 |
| 08 | [version-upgrade-notes.md](08-version-upgrade-notes.md) | 版本与升级风险 |
| 09 | [plugin-ecosystem.md](09-plugin-ecosystem.md) | 当前插件依赖与替代方案 |
| 10 | [research-method.md](10-research-method.md) | 来源分级与复核方式 |
| 11 | [preview-zoom.md](11-preview-zoom.md) | 高倍率预览缩放 |
| 12 | [field-hazards.md](12-field-hazards.md) | 常见运行时陷阱 |
| 13 | [user-vpy-abi.md](13-user-vpy-abi.md) | 用户脚本接口、模板与外部教程映射 |
| 14 | [worker-protocol.md](14-worker-protocol.md) | 预览 worker、epoch、mmap 与恢复 |
| 15 | [output-contract.md](15-output-contract.md) | output 0/1 与设备编码约束 |
| 16 | [script-trust.md](16-script-trust.md) | 来源、bundle hash 与本机信任 |

## 证据等级

- **官方文档**：VapourSynth 官方 API 或 CLI 文档。
- **框架直接源码**：本项目随包的 R73 Python/Cython 接口与 runner/helper 源码。
- **运行时实测**：真实 worker、VSPipe 或编码回归；命令写在对应文章末尾。
- **第三方经验**：只用于可读性、教程结构或性能启发，不能单独改变输出契约。

升级 VapourSynth、替换便携插件或改变 runner 时，应先复核 05、06、13、14、15，
再运行对应测试。索引中没有列出的滤镜不应被默认加入内置脚本；应由用户脚本
显式声明依赖和处理顺序。
