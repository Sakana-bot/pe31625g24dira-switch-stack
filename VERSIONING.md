# 版本规则

项目中的组件独立版本化，不再用一个版本号同时表示部署逻辑、WebUI、驱动和私有 SDK。

| 组件 | 当前版本 | 规则 |
| --- | --- | --- |
| deploy-kit | `1.0.0` | 正式版本使用 SemVer；不兼容的安装/打包契约提升主版本，兼容功能提升次版本，兼容修复提升补丁版本 |
| WebUI | `1.0.0` | 用户功能或 API/schema 变化提升次版本，兼容 UI/逻辑修复提升补丁版本；开发构建保留 `-dev` |
| legacy SDK runtime 包 | `2.1.0` | 2.0.0 起使用身份无关 rootfs 格式；不兼容格式/ABI 提升主版本，兼容 profile 元数据或 SDK 内容更新提升次版本，纯打包修复提升补丁版本 |
| DKMS 驱动 | `1.1.0` | 按内核模块源码和 ABI 独立版本化 |

配置 schema、platform schema 和 runtime package format 使用单调递增整数，只在读取方无法兼容旧格式时增加。

日常开发只提交到 `dev`，不因小修复自动创建 Release。只有维护者明确确认发布时，才确定正式版本、合并到 `main`、创建标签和 GitHub Release。`main` 表示最近一次确认发布的稳定状态。

runtime `1.0.0` 是其独立制品格式的首个稳定版本，不等同于 Intel IES/TestPoint 的产品版本；实际 SDK 版本继续单独记录为 `4.3`。

runtime `2.0.0` 删除了 1.x 直接嵌套的整板备份结构，仅保留 `/opt/silicom-legacy`。它不再携带来源板卡的 VPD、DMI、序列号、OS 审计或重复 platform 配置，因此按不兼容制品格式提升主版本。

runtime `2.1.0` 不改 SDK 内容，只把兼容目标从散热器外观代称改为机器可识别的 `sil001-hw4-b0` platform profile。

runtime 是首次安装所需的独立制品，不属于日常在线升级范围。`upgrade-debian13.sh` 只更新项目维护的驱动、交换控制代码和 WebUI，始终保留设备上已经安装的 runtime；如确需更换 runtime，应作为单独的受控维护或重新安装处理。
