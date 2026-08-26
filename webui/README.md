# PE31625G24DIRA Switch Manager

Version 1.1.0 is the management software for PE31625G24DIRA Switch Stack. It requires Linux and Python 3.9 or newer, provides first-run administrator initialization plus a dedicated session login page on HTTP port 80, and reads
external-port link state plus RMON/MAC statistics directly from FM10840
registers through `/dev/uio0` every second.

这是面向 Silicom PE31625G24DIRA Switch-On-NIC 的社区项目，并非 Silicom 官方软件。`PE31625G24DIRA` 用于整卡和板级功能，`Intel FM10840` 仅用于准确描述交换 ASIC、寄存器和驱动层。

板载、无 Web 框架依赖的 HTTP 管理页，最低支持 Python 3.9，不再保留 Python 2/3.5 兼容层。浏览器端为静态 HTML/CSS/JavaScript，Python 仅提供设备 API，常驻内存约 20 MiB。Debian 13 是当前一键安装器的实机验证平台，不是 WebUI 的 Python 版本要求。

端口、VLAN、统计端口和风扇响应时间统一使用可键盘操作的自绘控件；Tagged VLAN 多选及数字步进器也不依赖浏览器默认外观。每秒端口遥测只原位更新链路徽标，不会重建正在操作的配置表单。项目级命名已统一为 `pe31625g24dira-*`，不包含旧配置迁移或兼容层。

侧栏按状态、交换、系统三个一级功能域组织二级页面；字号、字重、颜色和图标语义统一遵循仓库根目录的 `UI_STYLE_GUIDE.md`。

## 本地界面预览

在 Windows 工作区根目录运行：

```powershell
python .\webui\qa_server.py
```

然后打开 `http://127.0.0.1:18741/`。预览服务器使用固定的模拟硬件数据，
不会连接板卡，也不会执行关机、端口配置或 VLAN 修改。编辑
`webui/static/index.html`、`app.js`、`controls.js` 或 `style.css` 后刷新浏览器即可看到结果；
结束时在启动它的终端按 `Ctrl+C`。

## 界面主题结构

当前只保留项目原有经典主题及浅色、深色、跟随系统三种明暗模式。`theme.js` 只管理明暗模式，
`style.css` 同时承载页面外壳与业务组件样式。Argon 适配层、素材、主题选择和专用登录页已在
0.10.9-dev 回滚；日志、系统设置、管理员、备份升级等功能没有随主题回退。0.10.10-dev 让未登录/初始化页通过只读公开身份接口显示 PCI VPD 检出的具体型号，移除初始化页底部的密码摘要提示，并清理经典主题中已经失效的抽屉菜单元素。

下一位如需重新设计主题，应先创建独立分支或备份，再以独立 `theme-<name>.css` 适配层实现；
不要复制端口、VLAN、风扇等业务 DOM 或事件逻辑，也不要把 LuCI CBI/dispatcher CSS 直接套入
本项目原生页面。

## 物理模型

两个 MPO24 对应六个四 Lane EPL 组：

界面中的 EPL 与 Lane 均保留 FM10840 原始零起始编号；每个 EPL 的四条通道显示为 Lane 0–3。

- MPO24-1：EPL0、EPL1、EPL2
- MPO24-2：EPL5、EPL6、EPL7

每个 EPL 组可独立选择：

- 拆分：4 个逻辑端口，每条 Lane 独立设为 10GBase-SR 或 25GBase-SR
- 聚合：1 个逻辑端口，四条 Lane 聚合为 40GBase-SR4 或 100GBase-SR4

因此支持全 6×40/100G、全 24×10/25G，或任意按 EPL 分组的混合拓扑。六个 EPL 始终占用固定的 24 个逻辑 lane 槽位；聚合时只启用每组 lane 0 代表端口，其余槽位保持 `DISABLED`。页面始终优先显示 MPO、EPL 和 Lane 物理身份。

## 监控与 VLAN

概览页显示 Atom CPU、内存、存储、运行时间、管理口、温度和交换端口汇总。传感器页集中显示 Linux hwmon 与 FM10840 的温度、电压和光引擎诊断；散热页显示 FM10840 当前温度、风扇转速与风扇曲线；系统信息页只显示能够从运行系统验证的管理软件、IES SDK、TestPoint、fm10k UIO/IES 驱动和内核版本，并注明驱动的主线与 Intel 参考源码来源。

散热页以闲置/负载温度、闲置/负载 PWM 百分比、临界温度和响应时间定义风扇曲线。闲置与负载 PWM 可在 0–100% 内设置，允许高功率风扇使用很低的占空比，但负载档不能低于闲置档，临界温度仍强制 100%。后端把两个端点间自动展开成 LM96163 的 12 项硬件 LUT，先进入全速直接模式再写表，成功后交回硬件自主控制；管理软件停止或重启不影响调速。PWM 是开环控制，实际转速以页面 TACH 为准。固定的 4°C 降档回差用于防止温度临界点附近反复变速，与 PWM 响应时间是两个独立参数。

端口页面支持每个逻辑端口与整组 MPO24 的持久化开关；MPO 总开关是批量动作和状态汇总，子端口始终可单独开启。速率由用户明确选择，不执行自动探测：拆分 Lane 支持 10G/25G，聚合端口支持 40G/100G。开关逻辑端口时还会同步 FCI 私有发射掩码，并通过写后回读确认，避免出现 UI 显示关闭但仍发光。计数器视图直接读取 FM10840 RMON/MAC 寄存器，显示单播、组播、广播、好/坏字节、帧长分布、FCS/编码错误，以及 STP、VLAN、FFU、Policer、TTL、Trigger 等丢弃原因，不经过 TestPoint SDK。

VLAN 页面以物理 EPL/Lane 为稳定标识，按端口配置接入（Access）、中继（Trunk）或混合（Hybrid）模式。Access 只允许一个 Native VLAN；Trunk 丢弃未标记报文并允许一个或多个 Tagged VLAN；Hybrid 同时提供一个 Native VLAN 和多个 Tagged VLAN。支持 VLAN 1–4094，拓扑改变时仍存在的物理端点保留成员关系，新拆分或聚合出的端点回到 VLAN 1。

启动脚本会先重置外部端口的 VLAN 表，显式启用 `drop_bv`，再按模式设置 `drop_tagged`、`drop_untagged`、PVID、成员关系、出方向标签和 MTU。应用后通过常驻 TestPoint 回读每个 VLAN 的外部端口 U/T 成员及所有端口的 PVID/准入属性；任何不一致均判定失败并自动恢复旧配置。

## 安全与恢复

应用前同时备份活动平台文件、持久平台文件、交换机启动脚本、状态脚本、VLAN 配置、端口开关配置、风扇配置和风扇初始化脚本。固定模型下，拓扑/速率只 powerdown 并重设发生变更的 EPL，VLAN 按成员、tagging、PVID 和准入规则生成增量命令；两者都通过唯一常驻 TestPoint 在线完成。旧的动态逻辑端口模型只在首次迁移时重启一次交换服务。风扇曲线、低频传感器读取和端口开关同样排队到该 TestPoint，不启动第二个 SDK owner。脚本以完成标记和硬件回读确认结束，失败先在线恢复，在线恢复失败才重启旧配置。

设置页不再使用分类页签：主机名、明暗模式、流量单位和管理员账户直接纵向排列；时区与时间同步完全跟随 Debian 系统，不由 WebUI 修改。管理员功能只修改 WebUI 用户名和密码，不开放系统用户、SSH 密钥、终端或任意 root 权限。“备份与升级”已拆成独立导航页。日志页只读显示本次启动的系统日志、内核日志和交换服务日志，日志来源由后端固定白名单决定；页面每 3 秒刷新并默认跟随末行，用户向上滚动或选择文字时暂停自动跟随。端口拓扑和 VLAN 修改均先显示变更预览并二次确认；固定模型下只提示目标 EPL 短暂断链或 VLAN 在线更新，首次迁移才显示全端口约 40 秒中断警告。进入预览后隐藏原待应用操作条，避免重复操作入口。

顶栏只保留一个电源图标入口；用户先选择重启或关机，再进入对应的二次确认，避免把低频危险操作作为两个常驻按钮展示。

备份与升级页支持导出和导入版本化 JSON 逻辑配置。导出包含端口拓扑、VLAN、端口开关和风扇曲线，不包含账号密码、管理网络、设备身份、Flash 或原厂资料。导入会先完整校验并创建本机回滚点，再重启交换服务和重新下发风扇曲线；任一步失败都会尝试恢复旧配置。

“恢复默认配置”是 ext4 系统上的应用级恢复，不依赖 squashfs：先保存本机快照，再恢复原厂 6×100G platform、VLAN 1、全部端口开启和默认风扇曲线，并清除管理员账户回到首次初始化。Debian、管理网络、驱动、SDK 和板载 Flash 均保持不变。

原厂 6×100G platform 会让 SDK 打印六条 `scheduler reduced speed`，但只要后续出现 `PE31625G24DIRA_SWITCH_READY` 且服务保持 active，就属于可接受告警。恢复与拓扑作业不会再把该字符串单独当成 SDK 初始化失败；API 返回日志会移除 TestPoint 的终端旋转光标控制字符。

0.10.12-dev 已移除无效的 `flushOnPortDown` 设置、API、配置文件和备份字段。链路恢复由独立的轻量监视完成：只在服务启动或端口 DOWN→UP 后比较 SDK 缓存与硬件 DMAC/SMAC 表，连续两次确认错位才定向删除对应 MAC/FID 让硬件重新学习，不清空端口或全表。

页面在可信管理网的 HTTP 80 端口提供服务。首次打开时由设备所有者创建管理员账号，不生成默认账号或随机密码文件；初始化完成后入口自动关闭。凭据使用 PBKDF2 摘要，并配合 HttpOnly/SameSite 会话 Cookie、每会话 CSRF 校验和登录限速。HTTP 不加密登录口令，因此管理口不能暴露到不可信网络。实时链路状态与 RMON 统计直接读取 `/dev/uio0`，板级温度和电压通过常驻 TestPoint 低频读取。

板载 FCI/Amphenol 12-Lane 光引擎不是标准可插拔 QSFP，旧 SDK 的标准 QSFP DOM 解码会生成伪读数，因此页面不展示由标准偏移解析出的温度、电压、偏置或 TX 功率。传感器页的手动光引擎诊断从 `0x50` 独立读取厂商、型号、序列号和生产日期，同时复现私有 RX 功率路径 `PCA9545 0x58 -> 0x40 -> page 1 -> 0xce`；24 字节向量全零时明确判为“不可用”，不会伪装成 12 路 0 mW。

重启和关机位于顶栏右侧，均使用一次确认弹窗和 CSRF 保护；如果硬件配置或 SDK 读取正在执行，后端会拒绝操作。重启确认后使用与其他长操作相同的顶部进度提示，管理服务恢复后自动进入登录页。在线更新可从固定 GitHub 仓库的 latest Release 获取通用部署包及独立 SHA-256 文件，也可手动上传同一部署包；两种方式都会自动显示外层 SHA-256、比较版本并执行只读审计，只有新版本能够进入二次确认和执行阶段。上传限制为 64 MiB，拒绝路径穿越、链接和设备文件，限制成员数量与解压尺寸，并校验 `KIT-SHA256SUMS`。更新通过独立 systemd transient unit 执行，页面轮询作业状态，避免 WebUI 更新自身时终止升级作业。当前哈希能验证传输与包内完整性，但不替代发布者数字签名。

## 设备部署说明

原 IBM Aurum 服务会在启动时把 80、443、623、3900、5900 等前面板端口 DNAT 到板内 BMC。本设备已停用 `cld-aurum.service`、`fabcon.service` 和 `netfabagent.service` 并清空遗留 NAT 表；原规则仍保存在旧开发命名路径 `/data/fm10840-webui-backups/webui-before-3.2-20260802/iptables-before-cleanup.rules`。

Aurum 原先还会设置 Silicom 板卡的 I²C 电源寄存器。停用 Aurum 后由独立的
`pe31625g24dira-board-init.service` 在交换服务前加载 `i2c_i801`，动态定位
`SMBus I801 adapter`，并向 address `0x36`、register `0x00` 写入 `0x3c`，保留必要的
硬件初始化而不启动 IBM 控制平面。风扇转速优先读取 Linux hwmon；板载 LM96163
没有 hwmon 节点时，则通过 FM10840 I²C 与 CPLD `0x59` 的 `A3/A4` tach 缓存读取。
