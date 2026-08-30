# 安装与升级

> **风险提示：**本项目全部代码由 ChatGPT 生成，不保证正确性、可靠性、安全性或可用性。请先审计并准备本地恢复路径。

当前正式版本：`1.3.2`。管理软件与 deploy-kit 共用版本号；legacy SDK runtime 独立提供，普通升级不会替换它。

## 已验证范围

- Debian 13 x86_64
- Ubuntu 26.04 x86_64，Linux 7.0
- Python 3.9 或更高版本
- `sil001-hw4-b0` platform（PCI subsystem `1374:01d0`）

安装器也接受其他 x86_64 Debian/Ubuntu 版本，但这不等于已经完成实机验证。它会先使用当前内核 headers 在板卡本机编译 DKMS 驱动；编译失败时会在写入交换服务和 WebUI 前停止。Python 3.9 的发行版边界大致对应 Debian 11、Ubuntu 22.04 的系统自带版本，但这两个系统仍需后续实机验证。

安装器保留发行版已有的 NetworkManager、Netplan、ifupdown 或 systemd-networkd，只创建项目自己的第二管理口配置，将 `enp3s0` 设为 `192.168.255.2/24`。不会替换主管理口或清除 `ifupdown2`。

## 安装包

- `pe31625g24dira-deploy-kit-*.tar.gz`：安装器、驱动源码、交换服务、WebUI 和已知 platform。
- `pe31625g24dira-legacy-sdk-runtime-*.tar.gz`：首次安装所需的 Intel IES/TestPoint runtime，不包含单板 VPD、DMI、序列号或系统审计。
- `pe31625g24dira-board-bundle-*.tar.gz`：仅供尚未内置的 platform 使用。

## 全新安装

```bash
sha256sum -c pe31625g24dira-deploy-kit-*.tar.gz.sha256
sha256sum -c pe31625g24dira-legacy-sdk-runtime-*.tar.gz.sha256
tar -xzf pe31625g24dira-deploy-kit-*.tar.gz

sudo bash pe31625g24dira-deploy-kit-*/deployment/deploy.sh \
  --runtime ./pe31625g24dira-legacy-sdk-runtime-*.tar.gz --audit

sudo bash pe31625g24dira-deploy-kit-*/deployment/deploy.sh \
  --runtime ./pe31625g24dira-legacy-sdk-runtime-*.tar.gz
sudo reboot
```

也可用 `--runtime-url HTTPS_URL` 和 `--runtime-sha256 SHA256` 从用户提供的位置下载 runtime。没有匹配的 platform 或 runtime 时，安装器会拒绝部署，不提供跳过核心配置的强制选项。

首次打开 WebUI 时由设备所有者创建管理员账号。全新安装会重置项目账号、端口/VLAN 配置、端口名称和风扇曲线。

## 日常升级

```bash
sudo bash pe31625g24dira-deploy-kit-*/deployment/upgrade.sh --audit
sudo bash pe31625g24dira-deploy-kit-*/deployment/upgrade.sh --apply
```

升级保留账号、交换配置、platform、runtime、管理网络和板载 Flash，并在实际修改前备份到 `/var/backups/pe31625g24dira/`。版本与发布规则见根目录 `VERSIONING.md`。

## 卸载

```bash
sudo bash deployment/uninstall.sh --yes
```

卸载会删除项目服务、DKMS 增量和项目创建的第二管理口配置片段；不会卸载发行版软件包、替换网络管理器或删除原有网络配置。完成后应重启。

驱动兼容性与历史遗留审计见 `COMPATIBILITY_AUDIT.md`。
