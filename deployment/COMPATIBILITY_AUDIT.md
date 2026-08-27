# 兼容性与历史遗留审计

## 实机验证

| 系统 | 内核 | 结果 |
| --- | --- | --- |
| Debian 13 | Debian 6.12 | 已验证整机部署 |
| Ubuntu 26.04 | Ubuntu 7.0.0-30-generic | 已在板卡本机完成 DKMS 编译；UIO、TestPoint、风扇、WebUI 和第二管理口正常 |

其他 Debian/Ubuntu 版本采用“现场编译、失败即停止”的策略，不能仅凭安装器接受该发行版就视为已验证。

## 主线 fm10k 变化

对 Linux 官方源码中 `drivers/net/ethernet/intel/fm10k` 的 v5.10、v5.15、v6.1、v6.6、v6.12 和 v7.0 逐文件比较：

| 区间 | 发生变化的文件数（共 20） |
| --- | ---: |
| v5.10 → v5.15 | 8 |
| v5.15 → v6.1 | 6 |
| v6.1 → v6.6 | 2 |
| v6.6 → v6.12 | 5 |
| v6.12 → v7.0 | 10 |

结论：FM10840 的核心硬件逻辑已经稳定，但主线驱动并非各内核版本完全相同。变化主要是 timer、ethtool、内存分配、PCI 与其他内核 API 适配。项目仍需保留少量按能力检测的兼容层，并对目标内核实际编译验证。

当前项目还与 Linux stable 官方 tag `v6.12.101` 做过逐文件校验：20 个主线 fm10k 文件中，15 个字节级一致；其余 5 个文件只包含本项目所需的 IES/UIO、FTAG、UIO 中断和跨内核 timer 兼容增量。`v6.12.106` 的 fm10k 目录与 `v6.12.101` 完全相同，因此继续固定使用 `v6.12.101` 作为可复现源码基线，不为没有实际驱动变化的稳定版点号重复换基线。

选择 6.12 LTS 而不是 5.10 或 7.0 的理由：它与 Debian 13 的主线内核代际一致，又能用很小的能力检测兼容 Linux 7.0；改用更早基线会增加向前适配，改用最新非 LTS 基线则会增加旧发行版适配。源码基线不要求目标机运行相同内核，最终兼容性始终以目标机 headers 的现场 DKMS 编译结果为准。

上游依据：

- [Linux v5.10 fm10k](https://github.com/torvalds/linux/tree/v5.10/drivers/net/ethernet/intel/fm10k)
- [Linux v6.12 fm10k](https://github.com/torvalds/linux/tree/v6.12/drivers/net/ethernet/intel/fm10k)
- [Linux v7.0 fm10k](https://github.com/torvalds/linux/tree/v7.0/drivers/net/ethernet/intel/fm10k)
- [Linux stable v6.12.101](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tag/?h=v6.12.101)
- [timer_shutdown_sync → timer_delete_sync](https://github.com/torvalds/linux/commit/41cb08555c4164996d67c78b3bf1c658075b75f1)
- [del_timer_sync → timer_delete_sync](https://github.com/torvalds/linux/commit/8fa7292fee5c5240402371ea19ffc6a02ad1486f)

## 当前仍在使用

- Intel IES/TestPoint runtime：交换芯片初始化和运行时控制。
- `sil001-hw4-b0` platform：端口映射、SerDes 和调度器配置。
- `fm10k 6.12.101-ies2`：Linux 6.12.101 主线基线加 Intel IES/UIO 与跨内核兼容增量；不是纯主线驱动。
- DKMS、当前内核 headers、build-essential：在板卡上针对当前内核编译驱动。
- `i2c-tools`：板级初始化脚本调用 `i2cset`。
- `libcrypt1`：旧版 runtime 动态链接依赖。
- `pci=realloc=off`：保持板卡所需 PCI 资源布局。
- `drm_kms_helper.poll=0`：抑制无显示器时的 EDID 轮询刷屏。
- `util-linux`：TestPoint 的 readline 需要由 `script(1)` 提供私有 PTY。
- `iproute2`、`kmod`、`rsync`、Python 3.9+：部署和运行工具。

## 已清理

- 不再安装、清除或替换 ifupdown/ifupdown2；不再覆盖 `/etc/network/interfaces`。
- 不再强制安装仅用于人工诊断的 ethtool、pciutils、procps；`util-linux` 因 `script(1)` 仍是运行依赖。
- 删除未被正式版本使用的 `xcvr.tp` 迁移补丁。
- 删除早期 `fm10840-*` 私有开发服务、路径和 GRUB 清理逻辑。
- 删除未参与运行的 `original-board`、runtime manifest 副本和整份网络模板。

仍保留 `fm10k-uio/1.1.0` 的 DKMS 清理，仅用于从公开 v1.0.0 升级；它不是当前运行依赖。`capture-legacy.sh` 和 runtime 构建脚本是离线制品工具，不会在安装时采集设备身份。
