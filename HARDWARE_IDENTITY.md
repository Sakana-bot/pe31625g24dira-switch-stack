# PE31625G24DIRA 硬件身份与命名依据

> 更新时间：2026-08-17。本文区分硬件原始字段、原厂软件推导和项目命名，避免把推导值描述成芯片直接提供的信息。

## 项目采用的名称

| 层级 | 规范名称 | 说明 |
| --- | --- | --- |
| 硬件型号 | Silicom PE31625G24DIRA | Silicom 官方产品页使用的产品型号 |
| 官方可订购料号 | PE31625G24DIRA-MPS | 官方页面当前列出的 P/N；不能据此推断手中 OEM 卡的完整订购后缀 |
| Dell OEM 标签 | DP/N 0R2T7N、DP/N 0891G5 | 实物标签及二级市场交叉信息；暂不宣称两者是完全可互换的同一修订版 |
| 交换 ASIC | Intel FM10840（FM10000 family） | 驱动、BAR、寄存器、温度源和 SDK 语义中继续使用 |
| 原镜像标识 | AP8 / SW 1.0 | 原系统 `/opt/image_ver.txt` 的软件镜像版本，不作为硬件型号后缀 |
| 项目名称 | PE31625G24DIRA Switch Stack | 社区软件项目；与 Silicom 或 Dell 无隶属、认可或支持关系 |
| Web 管理器 | PE31625G24DIRA Switch Manager | 项目的 WebUI 组件 |

## 原镜像如何描述这块板

原镜像没有找到 `PE31625G24DIRA`、`0R2T7N` 或 `0891G5` 字符串，也没有发现能够直接给出商业 SKU 的 DMI/ACPI 标识。它使用的是工程内部命名：

- `/opt/image_ver.txt` 精确记录 `ver. AP8`、`SW ver. 1.0`、`SEQ ver. 2.17.08.20`、`Track Number F123206701001` 和 2017-09-18 的构建时间；因此 `AP8` 应解释为镜像/平台发布代号。
- 日志中的主机名是 `AdiRRCAtomBoard`。
- Aurum 启动脚本把目标称为 `silicom bridge boards` 或 `Silicom cards`。
- RDIF 源码和帮助使用 `RRC register`，readme 标题为 `Silicom Linux RDI Control Utility`。
- BIOS/ACPI 是通用 Intel EDK2/VLV2 标识，不能用来确认 Dell 或 Silicom 的销售料号。

所以，原镜像对它的实质描述是“Silicom RRC/bridge board 的 AP8 软件平台”，不是 `PE31625G24DIRA-AP8` 这一完整硬件型号。项目过去把两者拼在一起并不严谨，现已拆开。

## 重装 Linux 后仍能读取的板卡身份

FM10840 PCI function 的 sysfs `vpd` 节点不依赖原系统盘，因此重装 Debian 或 Ubuntu 后仍可读取。当前 WebUI 的字段来源如下：

| 页面字段 | 来源 | 性质 |
| --- | --- | --- |
| `Silicom PE31625G24DIRA-MPS` | FM10840 PCI VPD 产品字符串 + subsystem vendor `0x1374` | 硬件原始信息 |
| 序列号 `S916260490015` | FM10840 PCI VPD | 硬件原始信息 |
| VPD `0490` | FM10840 PCI VPD 产品版本字段 | 硬件原始信息 |
| `Silicom B0` | 原厂 Aurum `au-boardcfg.sh` 规则：VPD 主版本 `< 6` 判为 B0，`>= 6` 判为 A11 | 原厂规则推导 |
| `sil001` | 当前 platform 文件的 `api.platform.config.platformName` | 软件平台标识 |
| `hw_version 4` | 原厂 `sil001.conf` / `/etc/netfab.conf`；A11 的 `sil006.conf` 对应 5 | 原厂软件配置值 |

因此页面可以展示这些值，但不能把 `B0`、`sil001` 或 `hw_version 4` 解释成 FM10840 寄存器直接报告的商业硬件型号，也不能据此自动恢复未知的光引擎 EEPROM 调优参数。

## 大小散热版本

当前实板是小散热外观；已有系统镜像来自大散热外观。大散热镜像的活动 platform 与 B0 `fm_platform_attributes_silicom.cfg` 完全一致，所以“大小散热”不能等同于 B0/A11，也不能据此选择不同 SDK 或 platform。

风扇接口、LM96163 控制方式和当前温控曲线暂按相同方案处理。其他差异等大散热实板到手后再读取 VPD、PCI subsystem、CPLD/Flash、platform 和真实温度行为后确认。

## 外部资料的证据强度

Silicom 官方资料明确把 PE31625G24DIRA 定义为六端口 100GbE Switch-on-NIC / Director Server Adapter，基于 Intel FM10840 和 Atom E3826；订购表列出 `PE31625G24DIRA-MPS`。这是采用 PE31625G24DIRA 作为主型号的最高可信依据：

- <https://www.silicom-usa.com/pr/server-adapters/switch-on-nic-server-adapters/pe31625g24dira-server-adapter/>

Dell 部件号目前只能由实物标签和二级市场资料交叉确认：

- `R2T7N` 被列为 “Dell 100Gb Network Fabric RRC Q2 Assembly Card”：<https://spwindustrial.com/dell-100gb-network-fabric-rrc-q2-assembly-card-r2t7n/>
- `0891G5`/`891G5` 被列为 “Dell Network Fabric RRC Q2 Assembly Card” 或六端口 100GbE PCIe Fabric Server Adapter：<https://www.ebay.com/p/7037228502>、<https://directmacro.com/dell-0891g5-network-accessory.html>

这些页面支持“两个号码都属于 Dell RRC Q2 / 100Gb Fabric 卡家族”，但没有权威 Dell 文档证明 `0R2T7N` 与 `0891G5` 是同一 BOM、同一硬件修订或可无条件互换。它们可能分别对应整卡、组件、装配阶段或不同修订；在取得完整 PPID、REV 和标签照片前保持为 OEM 交叉编号。Dell 标签中开头的 `0` 是常见的 `DP/N` 印刷形式，检索时可同时用 `0R2T7N/R2T7N` 与 `0891G5/891G5`。

## 下次板卡开机后的确认项

1. 拍摄主板、Atom 子板及支架上所有标签，完整保留 `DP/N`、`CN-...` PPID、`REV`、序列号和条码文字。
2. 保存 `dmidecode -t 1 -t 2`、`lspci -Dnnvv` 和 `/sys/class/dmi/id/*`；部署前采集脚本已经加入这些 DMI 字段和 `/opt/image_ver.txt`。
3. 核对两块带不同 Dell 标签的板是否具有相同 PCB 丝印、Silicom P/N、PCI subsystem ID 和 CPLD/Flash 配置，再决定是否在兼容清单中合并。
4. 软件运行时只把检测到的 PCI ID、platform 文件和 Flash 内容视为兼容性依据，绝不单凭二级市场标题自动选择或写入固件。
