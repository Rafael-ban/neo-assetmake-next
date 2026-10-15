# 仓库历史与贡献来源

本仓库由 [neo-assetmaker-dev](https://github.com/Rafael-ban/neo-assetmaker-dev) 分离，继续 VapourSynth 架构及后续主线开发。旧项目的代码、许可及已有署名随代码基线保留；精简 Git 历史不表示这些代码由新仓库维护者独立创作。

## 保留范围

- 保留初微弦音（[momovlink](https://github.com/momovlink)，当时 Git 作者名为 `mozixuan`）的四条连续真实提交，涉及旧版配置、x264 参数与 NVENC/QSVENC 配置。这些是旧版贡献，不应描述为新增的 VapourSynth 实现。
- 保留原提交 `e999a59af35552bbb18e0a052db5ef49419aeab6`（含）之后的开发提交及合并结构。它在新历史中的对应提交为 `cf120bf9d06ba02f9fcbddf5efb36eac61dfe109`。
- 功能分支 `feat/minimal-build-optional-rife` 与 `fix/source-portable-reliability` 已合并，仓库只维护 `master`。
- 仅保留 `v2.1.6`、`v2.2.0`、`v2.2.1`、`media-tools-r79-v1` 四个发布标签。

## 两个导入快照

历史从上游 `fd942c248e9a3ac2e618102c3dfdf4e99d3d61ca` 的完整代码快照开始，再接入 momovlink 的四条提交；之后导入上游 `cbc2202369ec550d4862b03ebd3497d510f96ae5` 的代码快照，接回 `e999a59` 及后续开发。

这两次导入由 Rafael-ban 记录，提交说明明确保留上游来源。导入期间包含的既有工作属于原贡献者；导入者身份不是其中全部代码的原创署名。保留提交的代码快照、原作者、原日期、原说明及相对每个父提交的改动均保持不变。

父节点重建使保留提交的 SHA 发生变化，旧签名不能沿用。旧、新提交映射及完整历史备份另行保存；历史发布附件保持原始字节，并非按新提交重新构建。标签对应的代码内容保持一致。

## 关键提交对应关系

| 原提交 | 新提交 |
| --- | --- |
| `1372710` | [`0381b40`](https://github.com/Rafael-ban/neo-assetmake-next/commit/0381b407593431eccaede08b7cac27ec181d02bc) |
| `5b86ce8` | [`69383b6`](https://github.com/Rafael-ban/neo-assetmake-next/commit/69383b6e4a8119cb777d464d05cd7c0271067580) |
| `b6b7a74` | [`727fc00`](https://github.com/Rafael-ban/neo-assetmake-next/commit/727fc00787af74059666ed3538b3f684ec94949b) |
| `6809bd8` | [`d0768ec`](https://github.com/Rafael-ban/neo-assetmake-next/commit/d0768ecdc2e0261f95ec0916468b0ec29890e2ae) |
| `e999a59` | [`cf120bf`](https://github.com/Rafael-ban/neo-assetmake-next/commit/cf120bf9d06ba02f9fcbddf5efb36eac61dfe109) |

GitHub Contributors 由默认分支的实际作者记录和账户关联计算；本仓库没有为控制头像而伪造作者或共同作者。历史重写后的统计可能需要约 24 小时刷新，见 [GitHub 官方说明](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-a-projects-contributors)。
