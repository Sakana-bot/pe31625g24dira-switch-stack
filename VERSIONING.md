# 版本规则

仓库根目录的 `VERSION` 是管理软件和 deploy-kit 的唯一版本来源。当前开发版本为 `1.3.0-dev`，正式版本仍为 `1.2.0`。

| 组件 | 当前版本 | 规则 |
| --- | --- | --- |
| 管理软件与 deploy-kit | `1.3.0-dev` | 共用唯一版本；不兼容变化提升主版本，新功能提升次版本，兼容修复提升补丁版本 |
| legacy SDK runtime 包 | `2.1.0` | 2.0.0 起使用身份无关 rootfs 格式；不兼容格式/ABI 提升主版本，兼容 profile 元数据或 SDK 内容更新提升次版本，纯打包修复提升补丁版本 |
| fm10k UIO/IES 驱动 | `6.12.101-ies2` | Linux 6.12.101 主线源码基线；第二版项目增量加入 IES/UIO 与跨内核 timer API 兼容；模块、UIO 与 DKMS 使用同一版本 |

日常开发在周期开始时确定下一个版本并加 `-dev`，只提交到 `dev`。普通提交和小修复不会创建 Release；只有维护者明确说“发布”后，才移除 `-dev`、合并到 `main`、创建标签和 GitHub Release。

runtime 的制品版本不等同于 Intel IES/TestPoint 的产品版本，也不在管理界面展示。界面从运行系统读取 IES SDK、TestPoint 和 fm10k-uio 的实际版本。

runtime `2.0.0` 删除了 1.x 直接嵌套的整板备份结构，仅保留 `/opt/silicom-legacy`。它不再携带来源板卡的 VPD、DMI、序列号、OS 审计或重复 platform 配置，因此按不兼容制品格式提升主版本。

Intel `fm10k-0.27.1` 是 UIO/IES 移植时使用的原厂参考源码，不是当前混合驱动的完整版本。旧的 `fm10k-uio/1.1.0` 只是误设的 DKMS 包装标识，升级时会迁移为上面的真实版本。

runtime `2.1.0` 不改 SDK 内容，只把兼容目标从散热器外观代称改为机器可识别的 `sil001-hw4-b0` platform profile。

runtime 是首次安装所需的独立制品，不属于日常在线升级范围。`upgrade.sh` 只更新项目维护的驱动、交换控制代码和 WebUI，始终保留设备上已经安装的 runtime；如确需更换 runtime，应作为单独的受控维护或重新安装处理。
