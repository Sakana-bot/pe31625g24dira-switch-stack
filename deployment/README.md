# 安装与升级

> **风险提示：**本项目全部代码由 ChatGPT 生成，不保证正确性、可靠性、安全性或可用性。请先审计并准备本地恢复路径。

当前版本：`1.1.0`。管理软件与 deploy-kit 共用一个版本号；legacy SDK runtime 独立提供，日常升级不会重复安装。

## 文件说明

- `pe31625g24dira-deploy-kit-*.tar.gz`：通用安装与升级程序，包含驱动、交换服务、Switch Manager 和 `sil001-hw4-b0` 的原厂 `6×100G` platform 母版，不包含 legacy SDK。
- `pe31625g24dira-legacy-sdk-runtime-*.tar.gz`：独立的 Intel IES/TestPoint legacy SDK runtime；首次安装时使用。格式 2 仅携带隔离的 `/opt/silicom-legacy`，不包含来源板卡的 VPD、DMI、序列号、系统审计或 platform 配置。
- `pe31625g24dira-board-bundle-*.tar.gz`：仅用于尚未内置的硬件平台，包含其私有 legacy SDK、platform 配置和硬件审计信息。

`sil001-hw4-b0` 的 SDK 和 platform 都是 profile 通用内容，不是单板校准数据。重装系统盘后不需要该板原系统的备份包。

已核对的两块 `sil001 / hw_version 4 / B0` 板卡使用相同的 SDK 与 platform，未发现按序列号生成的校准文件。该正式 profile 命名为 `sil001-hw4-b0`；散热器大小仅是外观代称，不参与识别。其他 platform 应提供自己的 bundle 和 runtime。

## 全新安装

当前一键安装器仅在 x86_64 Debian 13 上完成实机验证。全新安装会重置现有 Switch Manager 账号、端口配置、VLAN 和风扇曲线。

```bash
sha256sum -c pe31625g24dira-deploy-kit-*.tar.gz.sha256
sha256sum -c pe31625g24dira-legacy-sdk-runtime-*.tar.gz.sha256
tar -xzf pe31625g24dira-deploy-kit-*.tar.gz

sudo bash pe31625g24dira-deploy-kit-*/deployment/deploy-debian13.sh \
  --runtime ./pe31625g24dira-legacy-sdk-runtime-*.tar.gz --audit

sudo bash pe31625g24dira-deploy-kit-*/deployment/deploy-debian13.sh \
  --runtime ./pe31625g24dira-legacy-sdk-runtime-*.tar.gz
sudo reboot
```

也可以不预先上传 runtime，由板卡从用户准备的 HTTPS 地址下载：

```bash
sudo bash pe31625g24dira-deploy-kit-*/deployment/deploy-debian13.sh \
  --runtime-url 'https://example.invalid/pe31625g24dira-legacy-sdk-runtime-2.1.0.tar.gz' \
  --runtime-sha256 '<64 位 SHA-256>' --audit
```

脚本顶部的 `DEFAULT_RUNTIME_URL` 与 `DEFAULT_RUNTIME_SHA256` 默认留空，可由私有使用者自行填写。若不提供外部 SHA-256，安装器仍会校验 runtime 包内的文件清单，但不具备发布来源认证能力。

私有 GitHub Release 使用 API asset URL，并通过 `PE31625G24DIRA_RUNTIME_TOKEN` 临时传入只读 token。下载器会发送 GitHub 所需的 Bearer 与 `application/octet-stream` 请求头，token 不会保存到磁盘：

```bash
sudo env PE31625G24DIRA_RUNTIME_TOKEN='<只读 token>' \
  bash pe31625g24dira-deploy-kit-*/deployment/deploy-debian13.sh \
  --runtime-url 'https://api.github.com/repos/OWNER/REPO/releases/assets/ASSET_ID' \
  --runtime-sha256 '<64 位 SHA-256>' --audit
```

安装器会验证包内哈希、硬件 PCI ID、bundle 格式和 SDK/platform 内容，然后安装 DKMS 驱动、交换服务及 WebUI。默认管理口布局为：

- `enp2s0`：DHCP
- `enp3s0`：`192.168.255.2/24`

首次打开 WebUI 后需要自行创建管理员账号。

### Platform 选择与拒绝策略

默认 `--platform-profile auto` 只会选择部署包中存在且与硬件 ID 匹配的 profile。当前已知 profile：

```text
PCI 8086:15a4 · subsystem 1374:01d0 + 匹配的 PE31625G24DIRA-MPS B0 VPD → sil001-hw4-b0
```

没有匹配 profile、profile 文件缺失或 legacy SDK runtime 缺失时，审计和正式安装都会立即报错，且不会开始修改系统。通用部署包必须添加 `--runtime` 或 `--runtime-url`。因为缺少 platform 时 TestPoint 无法初始化端口映射、SerDes 和调度器，所以不提供“无配置继续安装”的选项。

如已人工确认硬件可以使用某个现有 profile，但自动检测因 subsystem 不同而拒绝，可显式强制：

```bash
sudo bash pe31625g24dira-deploy-kit-*/deployment/deploy-debian13.sh \
  --platform-profile sil001-hw4-b0 --force-platform-profile --audit
```

强制选项只绕过硬件身份匹配，不会绕过配置文件、SDK、包校验或型号检查。未知平台使用自己的 bundle：

```bash
sudo bash pe31625g24dira-deploy-kit-*/deployment/deploy-debian13.sh \
  --bundle ./pe31625g24dira-board-bundle-*.tar.gz \
  --platform-profile bundle --audit
```

通用部署包不包含原厂 legacy SDK。runtime 包涉及私有二进制；对外提供前应单独确认再分发授权。

## 日常升级

先查看将要发生的变化：

```bash
sudo bash pe31625g24dira-deploy-kit-*/deployment/upgrade-debian13.sh --audit
```

确认后应用：

```bash
sudo bash pe31625g24dira-deploy-kit-*/deployment/upgrade-debian13.sh --apply
```

升级保留账号、端口拓扑、VLAN、端口状态、风扇曲线、platform、管理网络和板载 Flash。发生实际变更前会备份到 `/var/backups/pe31625g24dira/`。

升级只同步项目维护的驱动、交换控制代码和 WebUI，不会检查或改动已安装 runtime。需要更换 runtime 时应使用单独的受控维护流程或重新安装，避免把长期稳定的私有运行环境混入普通在线更新。

也可以在 WebUI 的“备份与升级”页面上传同一份通用部署包。当前更新包只提供 SHA-256 完整性校验，尚未提供发布者数字签名，不应安装来源不明的包。

版本与发布规则见项目根目录的 `VERSIONING.md`。

## 其他 Linux 发行版

可以移植，但当前还没有可直接运行的通用安装器。软件各层的实际要求如下：

| 组件 | 最低要求 | 发行版相关部分 |
| --- | --- | --- |
| Switch Manager | Python 3.9+、Linux | systemd unit、日志读取和重启/关机命令 |
| Web 前端 | 现代浏览器 | 无发行版依赖 |
| UIO 驱动 | x86_64、DKMS、匹配当前内核的 headers | 内核 API 兼容性和包名 |
| legacy SDK | x86_64 glibc、兼容的 `libcrypt` | 库包名和动态链接器环境 |
| 交换与风扇服务 | systemd、bash、常用 GNU 工具 | unit 安装路径 |
| 启动参数 | 能向内核加入所需参数 | GRUB、systemd-boot 等实现不同 |
| 管理网络 | Linux 网络栈 | ifupdown、NetworkManager、systemd-networkd 等实现不同 |

当前脚本把包管理、网络后端和 bootloader 写成了 Debian 适配器。要正式支持 Ubuntu、Fedora/Rocky、Arch 等发行版，应分别增加依赖安装、网络配置和启动参数适配，并对目标发行版的内核做 DKMS 编译和实机交换测试。不能只删除 Debian 版本检查就视为支持。

Python 3.9 是代码语法/API的真实下限，而不是 Debian 13 自带的 Python 3.13。低于 3.9 的 Python 不受支持。

## 常见问题

- 安装后没有 `/dev/uio0`：检查 `dkms status`、当前内核 headers 和启动参数，必要时重启。
- 内核升级后交换服务无法启动：先确认 DKMS 已针对新内核成功构建，再检查 `/dev/uio0`。
- WebUI 无法初始化 SDK：查看 `journalctl -u pe31625g24dira-switch.service`。
- 未连接显示器时日志反复出现 `EDID block 0 is all zeroes`：部署包会通过内核参数关闭未接显示口轮询，重启后生效。
