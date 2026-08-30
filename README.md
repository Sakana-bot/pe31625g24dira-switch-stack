# PE31625G24DIRA Switch Stack

Silicom PE31625G24DIRA Switch-on-NIC 的社区管理软件，可将板载 Intel FM10840 作为独立二层交换机使用。提供端口拆分、VLAN、传感器监控、风扇调速、光发射控制、配置备份和在线升级。

> [!WARNING]
> 本项目全部代码由 ChatGPT 生成，不保证正确性、可靠性、安全性、可用性或适用于任何用途。使用者应自行审计并承担网络中断、数据丢失及硬件损坏等风险。

## 准备系统

- 用户需先自行安装干净的 x86_64 Debian 或 Ubuntu 系统，并确保管理口联网且可使用 root 或 sudo。
- 已完成整机实机验证：Debian 13、Ubuntu 26.04（Linux 7.0）。其他 Debian/Ubuntu 版本会先在本机编译驱动；编译失败时安装器会在改动交换服务前停止。
- Python：3.9 或更高版本
- 已内置 profile：`sil001-hw4-b0`（PCI subsystem `1374:01d0`，VPD 为 PE31625G24DIRA-MPS B0）

安装器会读取硬件身份。匹配 `sil001-hw4-b0` 时自动使用 deploy-kit 内的 platform，并按需下载项目首次正式发布的稳定 legacy SDK runtime，不需要原系统备份；日常 Release 不重复发布 runtime。其他 platform 会拒绝自动部署，需另行提供匹配的 platform bundle 与 runtime。

## 安装

联网板卡可一条命令安装：

```bash
curl -fsSL https://raw.githubusercontent.com/Sakana-bot/pe31625g24dira-switch-stack/main/install.sh | sudo bash
```

完整卸载项目文件（保留管理网络和发行版软件包）：

```bash
sudo bash deployment/uninstall.sh --yes
```

也可从最新 Release 下载 deploy-kit，并从首次正式发布 `v1.0.0` 下载稳定的 legacy SDK runtime；同时下载各自的 `.sha256` 后上传到板卡执行：

```bash
sha256sum -c pe31625g24dira-deploy-kit-*.tar.gz.sha256
sha256sum -c pe31625g24dira-legacy-sdk-runtime-*.tar.gz.sha256
tar -xzf pe31625g24dira-deploy-kit-*.tar.gz
cd pe31625g24dira-deploy-kit-*
sudo bash deployment/deploy.sh --runtime ../pe31625g24dira-legacy-sdk-runtime-*.tar.gz --audit
sudo bash deployment/deploy.sh --runtime ../pe31625g24dira-legacy-sdk-runtime-*.tar.gz
sudo reboot
```

首次打开 WebUI 时由设备所有者创建管理员账号。全新安装会清除已有的 Web 管理员账号、端口与 VLAN 配置、端口名称和风扇曲线。

## 升级

联网升级：

```bash
curl -fsSL https://raw.githubusercontent.com/Sakana-bot/pe31625g24dira-switch-stack/main/install.sh | sudo bash -s -- --upgrade
```

也可在 WebUI 的“备份与升级”页面上传新版部署包，或在终端执行：

```bash
sudo bash deployment/upgrade.sh --audit
sudo bash deployment/upgrade.sh --apply
```

普通升级只更新驱动、交换控制代码和 WebUI，并保留已安装的 runtime 与用户配置。
WebUI 的“备份与升级”页面也可直接检查 GitHub 上的最新正式版本，完成校验与差异审计后再由用户确认更新。
需要参加 RC 测试时，可在检查前启用“包含预发布版本”；降级默认禁止，只有显式启用“允许降级”后才可执行。

如果旧版本 WebUI 提示“部署包不完整”，请解压该版本部署包并执行包内实际提供的升级脚本；早期 Debian 13 部署包使用 `deployment/upgrade-debian13.sh --apply`，当前部署包使用 `deployment/upgrade.sh --apply`。

## 其他硬件版本

未识别的 platform 不会套用内置配置。拥有对应文件时可使用：

```bash
sudo bash deployment/deploy.sh \
  --platform-profile bundle --bundle ./board-bundle.tar.gz \
  --runtime ./legacy-sdk-runtime.tar.gz --audit
```

也可把 `--runtime` 换成 `--runtime-url HTTPS_URL`；建议同时提供 `--runtime-sha256`。

更详细的安装、回滚与发行版移植说明见 [deployment/README.md](deployment/README.md)。
