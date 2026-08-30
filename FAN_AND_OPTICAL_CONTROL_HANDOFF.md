# PE31625G24DIRA 风扇调速与光发射控制技术交接

> 更新时间：2026-08-17。当前权威实现是 `webui/app.py`；旧的单独诊断脚本只用于对照，不应覆盖运行配置。大小散热外观目前使用同一风扇控制方案，差异化需等待大散热实板验证。

本文面向继续维护 Silicom PE31625G24DIRA Switch Stack 的开发者，集中说明两个容易误操作的硬件功能：

1. 以 Intel FM10840 核心温度驱动系统风扇；
2. 关闭某个端口或整个 MPO24 对应的板载光引擎发射通道。

本文记录的是当前 Linux 实现和已在实板上验证过的行为，不是对其他 FM10000 系列板卡的通用说明。

## 1. 先读哪些资料

建议按以下顺序阅读：

| 优先级 | 资料 | 重点 |
| --- | --- | --- |
| 1 | [`资料/PE31625G24DiRA-MPS_UG1V0.pdf`](资料/PE31625G24DiRA-MPS_UG1V0.pdf) | 第 11 页的风扇口定义；第 21 页 I²C 拓扑；第 23–24 页间接访问与 mux；第 26–27 页器件地址和 TACH 映射 |
| 2 | [TI LM96163 数据手册](https://www.ti.com/lit/ds/symlink/lm96163.pdf) | 远端温度、12 点 LUT、PWM、TACH、回差、响应时间和寄存器初始化顺序 |
| 3 | [`switch_service/pe31625g24dira-fan-init.tp`](switch_service/pe31625g24dira-fan-init.tp) | 当前默认风扇初始化脚本，可直接对照实际写寄存器顺序 |
| 4 | [`webui/app.py`](webui/app.py) | `render_fan_init()`、`fan_lut_points()`、`port_admin_plan()`、`xcvr_verification_script()` 是当前实现的权威来源 |
| 5 | [`webui/reference_original_6x100.cfg`](webui/reference_original_6x100.cfg) | 内置 `sil001-hw4-b0` platform 母版；逻辑端口与 `hwResourceId` 映射 |
| 6 | [`_analysis/sdk/IES_SDK-4.3.2-20160607_6ports_15032017_14i_LINK_OPT_EEE_VRM/ies/src/platforms/libertyTrail/platform.c`](_analysis/sdk/IES_SDK-4.3.2-20160607_6ports_15032017_14i_LINK_OPT_EEE_VRM/ies/src/platforms/libertyTrail/platform.c) | 原厂 `fmFCIByteWrite()` 的写入/回读/重试，以及私有 `Tx Squelch Disable` 初始化 |
| 7 | [`_analysis/sdk/IES_SDK-4.3.2-20160607_6ports_15032017_14i_LINK_OPT_EEE_VRM/ies/src/platforms/libertyTrail/platform_app_api.c`](_analysis/sdk/IES_SDK-4.3.2-20160607_6ports_15032017_14i_LINK_OPT_EEE_VRM/ies/src/platforms/libertyTrail/platform_app_api.c) | `fmPlatformXcvrMemRead/Write()` 如何选择光引擎并访问 `0x50` |
| 8 | [`backups/20260809-postinstall/optical-eeprom-and-state.txt`](backups/20260809-postinstall/optical-eeprom-and-state.txt) | 两块板载 FCI 光引擎的原始 EEPROM 和初始状态留档 |

板卡手册把 FM10840 称为 Red Rock Canyon（RRC），把两块板载光引擎称为 FCI OBT。代码和本文沿用 FM10840、MPO1/MPO2 和 FCI 光引擎这些更清晰的称呼。

## 2. 共同的 I²C 与 SDK 约束

### 2.1 硬件拓扑

FM10840 通过板载 PCA9545 访问多个同地址器件。关键地址如下：

| 对象 | 7-bit 地址 | PCA9545 `0x58` 选择值 |
| --- | ---: | ---: |
| MPO1 / FCI-1 TX | `0x50` | `0x01` |
| MPO2 / FCI-2 TX | `0x50` | `0x02` |
| LM96163 风扇控制器 | `0x4c` | `0x08` |
| CPLD memory handler | `0x59` | 不通过上述分支直接访问 |

两块光引擎地址相同并不是冲突，必须通过 mux 切换。任何手工风扇诊断结束后都应把 `0x58` 恢复为 `0x01`，避免后续代码误读 MPO1。

### 2.2 只能有一个 SDK owner

当前唯一常驻 ASIC/SDK owner 是：

```text
pe31625g24dira-switch.service
  -> TestPoint 4.3
  -> /run/pe31625g24dira-testpoint/control
```

低频 I²C 和 SDK 操作必须生成 TestPoint 脚本，再向 FIFO 写入 `load <脚本路径>`。不要同时启动第二个 TestPoint、TestPointShared、rdifd 或 rrcd；多个进程会争用 SDK 共享状态和 I²C，典型结果是“共享 SDK 操作超时”、初始化失败，严重时需要重新启动整个交换服务。

推荐的执行前检查：

```bash
systemctl is-active pe31625g24dira-switch.service
test -p /run/pe31625g24dira-testpoint/control
test -f /run/pe31625g24dira-testpoint/switch-ready
```

执行现有脚本的标准形式：

```bash
printf 'load /etc/pe31625g24dira/pe31625g24dira-fan-init.tp\n' \
  > /run/pe31625g24dira-testpoint/control
journalctl -u pe31625g24dira-switch.service --since '1 minute ago' --no-pager
```

生产代码应像 `queue_testpoint_script()` 一样记录 journal cursor、等待唯一完成标记并检查 SDK 错误，不能仅凭 FIFO 写入成功就宣告操作成功。

## 3. 风扇调速

### 3.1 硬件和接线

板卡用户手册第 11 页明确给出标准 4-pin 风扇接口：

| 针脚 | 当前线色 | 功能 |
| ---: | --- | --- |
| 1 | 黑 | GND |
| 2 | 红 | +12 V |
| 3 | 黄 | TACH |
| 4 | 蓝 | PWM |

当前使用 AVC DAZG1225R2H。颜色是这把风扇的实机记录，维护其他风扇时仍应以针脚定义和该风扇规格书为准。

板载 LM96163 位于 `0x4c`、mux 分支 `0x08`。它的远端热二极管连接 FM10840，因此 WebUI 的温度源固定为 FM10840 核心温度，不提供传感器选择。

### 3.2 为什么不用软件循环调速

LM96163 自带 12 点温度/PWM LUT、回差和渐变控制。WebUI 只负责生成并下发曲线，之后由 LM96163 独立调速：

- WebUI 停止或重启不影响当前曲线；
- Linux 无需每秒轮询并写 PWM；
- 开机时由 `pe31625g24dira-fan-init.service` 重新写入，解决控制器掉电后恢复默认满转的问题；
- 初始化过程中先进入 100% 故障保护，全部寄存器写完后才交回 LUT 控制。

注意：曲线存在系统盘上的 JSON/TP 文件中，不在 LM96163 的永久存储中。掉电后必须重新初始化。

### 3.3 配置文件和启动链路

板卡上的关键文件：

```text
/etc/pe31625g24dira/webui/fan.json
/etc/pe31625g24dira/pe31625g24dira-fan-init.tp
/usr/local/sbin/pe31625g24dira-queue-fan-init
```

本地源文件：

```text
webui/fan-default.json
webui/app.py
switch_service/pe31625g24dira-fan-init.tp
switch_service/pe31625g24dira-queue-fan-init.sh
switch_service/pe31625g24dira-fan-init.service
```

启动顺序为：

1. `pe31625g24dira-switch.service` 启动唯一常驻 TestPoint；
2. 交换初始化成功后创建 `switch-ready`；
3. `pe31625g24dira-fan-init.service` 等待 FIFO 和 `switch-ready`；
4. 向 FIFO提交 `load /etc/pe31625g24dira/pe31625g24dira-fan-init.tp`；
5. 脚本成功后创建 `/run/pe31625g24dira-testpoint/fan-ready`。

### 3.4 曲线参数如何解释

当前配置结构：

```json
{
  "sensor": "fm10840_core",
  "idle_temperature_c": 30,
  "load_temperature_c": 70,
  "critical_temperature_c": 80,
  "idle_speed_percent": 40,
  "load_speed_percent": 80,
  "response_time_s": 10.9,
  "hysteresis_c": 4
}
```

- 低于闲置温度：保持闲置 PWM；
- 闲置温度至负载温度：后端插值生成 9 个点；
- 到达负载温度：达到负载 PWM；
- 到达临界温度：直接进入 100%；
- 回差：降档所需的温度差，防止阈值附近反复升降速；它不是响应时间；
- 响应时间：LM96163 在相邻 LUT 档位间平滑变化的时间常数。

可用的硬件响应时间只有 `5.45`、`10.9`、`21.6`、`43.7` 秒。当前后端还限制：闲置温度 10–80°C、负载温度至少比闲置高 10°C、临界温度高于负载温度、闲置 PWM 不低于 25%、负载 PWM不低于闲置 PWM。

PWM 百分比按下式变成 8-bit 值：

```text
PWM = round(百分比 × 255 / 100)
```

PWM 是开环占空比，不等于转速百分比。不同风扇的最低启动占空比、转速曲线和每转 TACH 脉冲数都可能不同。

### 3.5 关键 LM96163 寄存器

完整语义以 TI 数据手册为准，当前实现涉及：

| 寄存器 | 用途 |
| ---: | --- |
| `0x01` / `0x10` | 远端温度高/低字节，即 FM10840 热二极管 |
| `0x03` | TACH enable、T_CRIT override 等配置 |
| `0x19` | 远端 T_CRIT 温度 |
| `0x21` | T_CRIT 回差 |
| `0x30` | TruTherm 配置 |
| `0x33` | POR/not-ready 状态 |
| `0x45` | 高分辨率 PWM 和 LUT 平滑响应时间 |
| `0x46` / `0x47` | TACH count LSB/MSB，必须先读 LSB 再读 MSB |
| `0x4a` | `PWPGM`、`PWOP`、PWM 时钟和 TACH 模式 |
| `0x4b` | 风扇 spin-up 配置 |
| `0x4c` | 当前 PWM 值；`PWPGM=1` 时可直接写 |
| `0x4d` | PWM 频率，当前使用 22.5 kHz 高分辨率模式 |
| `0x4e` / `0x4f` | LUT 温度偏移和回差 |
| `0x50`–`0x67` | 12 组交替排列的温度/PWM LUT |

当前板级配置使用 `PWOP=1`。不要只依据 LM96163 裸芯片的开漏极性说明擅自翻转，因为板级信号链和已验证风扇行为才是最终依据。

### 3.6 正常操作和验收

日常修改应直接使用管理界面“系统 -> 散热”，它会：

1. 备份现有配置；
2. 原子写入 `fan.json` 和生成的 TP 脚本；
3. 通过唯一常驻 TestPoint 下发；
4. 等待完成标记；
5. 失败时恢复原配置并重新应用。

命令行只读检查：

```bash
systemctl --no-pager --full status \
  pe31625g24dira-switch.service pe31625g24dira-fan-init.service
ls -l /run/pe31625g24dira-testpoint/{switch-ready,fan-ready}
cat /etc/pe31625g24dira/webui/fan.json
journalctl -b -u pe31625g24dira-fan-init.service \
  -u pe31625g24dira-switch.service --no-pager | tail -n 120
```

需要读 LM96163 全部关键寄存器时，可通过 FIFO加载：

```text
/etc/pe31625g24dira/pe31625g24dira-fan-dump.tp
```

当前 WebUI 从 CPLD `0x59` 的缓存地址 `0xA3/0xA4` 取得 LM96163 `0x46/0x47` 的 TACH count。`0` 或 `0xffff` 视为无有效转速信号；当前 AVC 风扇的 RPM 为近似值，计算常数和标称最大值见 `webui/app.py`。

已验证样例：

- 约 33.25°C、50% PWM 时曾测得约 1421 RPM；
- 2026-08-12 重新插好 PWM 蓝线后，45.2°C 时 TACH count 为 `0x0df3`，页面换算约 1510 RPM；
- 风扇初始化、温度读取、PWM LUT 和 TACH 链路均可工作。

### 3.7 风扇满转故障树

1. 先看 `fan-ready` 和 journal。没有完成标记时才查启动顺序、FIFO、SDK 错误或脚本语法。
2. 读取 `0x4a`。`PWPGM=0` 表示 LUT 已接管；`PWPGM=1` 可能仍停在手动/故障保护模式。
3. 读取 `0x4c`。若温度低而值接近 `0xff`，查 LUT 写入和临界温度；若 `0x4c` 已是合理值而实际仍满转，查 PWM 信号链。
4. 检查蓝线和第 4 针。四线风扇 PWM 悬空通常会故障保护满转。
5. 检查黄线和第 3 针。黄线仅影响测速，不会导致 PWM 本身失效。
6. 只有确认接线良好后，才用短时 20%/80% 直接 PWM 阶跃判断风扇是否响应；测试结束必须恢复 LUT。

2026-08-12 的真实故障就是蓝线接触问题：

- 服务、`fan-ready`、温度、`0x4a` 和 `0x4c` 全部正常；
- TACH 正常，但 20% 和 80% PWM 下都保持约 2800 RPM；
- 重新插好风扇接头后立即恢复约 1510 RPM。

因此“寄存器回读正常 + TACH 正常 + 大幅改变 PWM 后转速完全不变”应优先检查蓝线，而不是修改风扇曲线或 systemd。

## 4. 关闭光模块发射

### 4.1 这里不是普通可插拔 QSFP

板卡上是两块焊接的 FCI/Amphenol 24-lane OBT 光引擎，每块在外部表现为一个 MPO24，并承载 12 条双工 Lane。实板 EEPROM 标识包括：

```text
FCI MergeOptics
P/N 10124588-211
```

两个模块 EEPROM 布局存在一字节偏移，原 SDK 的标准 QSFP 解码会产生看似合理但实际错误的温度、电压和光功率。因此：

- 不要把这两块 OBT 当普通 QSFP/SFF-8636 模块；
- 标准 DOM 温度、电压、Rx/Tx power 目前均不作为可信数据展示；
- 关闭发射使用原厂 Liberty Trail 平台代码确认过的私有 FCI 寄存器。

### 4.2 MPO、EPL 和 mux 映射

| 前面板 | EPL | 组内位置 | `hwResourceId` | mux |
| --- | --- | ---: | ---: | ---: |
| MPO1 | EPL0 | 1 | 0 | `0x01` |
| MPO1 | EPL1 | 2 | 1 | `0x01` |
| MPO1 | EPL2 | 3 | 2 | `0x01` |
| MPO2 | EPL5 | 1 | 3 | `0x02` |
| MPO2 | EPL6 | 2 | 4 | `0x02` |
| MPO2 | EPL7 | 3 | 5 | `0x02` |

每个 EPL 有 Lane 0–3。发射掩码的 bit 编号为：

```text
bit = (组内位置 - 1) × 4 + Lane
```

例如 MPO1 的 EPL0 Lane0 是 bit 0，EPL1 Lane0 是 bit 4，EPL2 Lane3 是 bit 11。

### 4.3 为什么 `set port powerdown` 不够

关闭逻辑端口需要两个互补动作：

1. `set port <logical> powerdown`：关闭 FM10840 的逻辑端口/MAC/SerDes；
2. 更新 FCI TX squelch-disable mask：真正允许或禁止对应 VCSEL 发射。

只执行第一步不能保证板载光引擎熄光。实板曾出现 MPO1 的端口在 UI 中全部关闭，但仍有红光；原始读回为：

```text
MPO1: offset 56 = 0x00, offset 57 = 0xff
MPO2: offset 56 = 0x00, offset 57 = 0x00
```

也就是 MPO1 仍有低 8 路发射允许位没有清除。

### 4.4 私有发射掩码

原厂 `platform.c` 将十进制 offset `56`、`57` 标为 `Tx Squelch Disable`，初始化时分别写 `15` 和 `255`，即打开全部 12 路发射。

当前项目把它作为 12-bit “TX enable / squelch-disable”掩码：

| 状态 | offset 56（高 4 bit） | offset 57（低 8 bit） |
| --- | ---: | ---: |
| 全部开启 | `0x0f` | `0xff` |
| 全部关闭 | `0x00` | `0x00` |

注意两套表示法：

- SDK API 中 offset `56` / `57` 是十进制；
- 原始 I²C dump 中对应十六进制 `0x38` / `0x39`。

标准 QSFP byte 86 的 TxDisable 是另一套 4-lane 语义，不能代替这块 12-lane FCI 的私有掩码，也不要同时混写两套控制，除非以后取得准确的 OBT 寄存器定义并重新实测。

### 4.5 写入为什么必须回读重试

FCI 光引擎的寄存器写入偶尔不会锁存。原厂 `fmFCIByteWrite()` 对每个字节执行最多 51 次“写入 -> 读回 -> 不一致则等待后重试”。当前 WebUI 使用相同原则：

1. 先读目标字节；已经正确则不写；
2. 调用 `fmPlatformXcvrMemWrite()`；
3. 立即用 `fmPlatformXcvrMemRead()` 回读；
4. 不一致时最多重试 12 次；
5. offset 56、57 全部匹配后才输出完成标记；
6. 任一字节失败则操作失败，WebUI 恢复原配置。

实板修复 MPO1 熄光时，offset 57 的第一次写入未锁存，重试后才成功。这说明回读不是“保险起见”，而是必要流程。

### 4.6 正常操作

推荐只通过 WebUI“端口”页面操作：

- 单端口开关会更新对应 mask bit；
- MPO 总开关是批量开启/关闭该 MPO 的所有端口；
- 部分端口开启时总开关不显示为全开；
- 开启任意子端口不需要先开启总开关；
- 变更同时写入 `/etc/pe31625g24dira/webui/ports.json` 和交换启动脚本，重启后保持。

后端执行顺序见 `port_admin_worker()`：备份配置、更新端口状态、生成启动脚本、通过 FIFO 加载临时 TP 脚本、等待发射掩码回读成功；失败时恢复原状态。

不要为了关闭发射直接编辑 JSON。JSON 只表示期望状态，真正生效必须经过 SDK 写入和回读。

### 4.7 命令行确认

先确认期望状态和服务日志：

```bash
cat /etc/pe31625g24dira/webui/ports.json
journalctl -u pe31625g24dira-switch.service --since '5 minutes ago' --no-pager \
  | grep -E 'XCVR_VERIFIED|XCVR_VERIFY_FAILED|PORT_ADMIN'
```

成功日志形式：

```text
PE31625G24DIRA_XCVR_VERIFIED mpo=1 port=1 offset=56 value=0 attempts=0
PE31625G24DIRA_XCVR_VERIFIED mpo=1 port=1 offset=57 value=0 attempts=1
```

若需要脱离 WebUI做专家级验证，应复用 `xcvr_verification_script()` 生成的 `fmPlatformXcvrMemRead/Write()` 闭环，不要只执行一次裸 `i2c write`。全关后的最终判据必须是两个 MPO 均读到：

```text
offset 56 / raw 0x38 = 0x00
offset 57 / raw 0x39 = 0x00
```

然后再从安全位置观察是否还有发光。不能只看 UI 开关，也不能只看 `set port ... powerdown` 的状态。

### 4.8 光口关不掉故障树

1. 检查 `ports.json` 是否确实要求目标 Lane 关闭。
2. 检查日志是否同时出现 port powerdown 和两个 FCI 字节的 `XCVR_VERIFIED`。
3. 如果只有逻辑端口关闭，没有 FCI 验证，说明只关了 FM10840，没有关发射器。
4. 回读 offset 56/57。`0x00ff` 表示高 4 路已关、低 8 路仍开，属于写入未锁存或脚本未完整执行。
5. 确认 MPO1 使用 mux `0x01`，MPO2 使用 `0x02`；两者都在地址 `0x50`，mux 错误会读写到另一块模块。
6. 使用写后回读重试，不要通过不断重启交换服务碰运气。
7. 操作完成后恢复 mux `0x01`。

### 4.9 模块身份与 RX 光功率私有映射实测（更新于 2026-08-19）

群友提供的 `netlab-os` 路径为：

```text
CLI -> mgmtd -> switchd RPC method 90
    -> PCA9545 0x58，分支 0x01 / 0x02
    -> FCI RX 设备 0x40
    -> page 1，绝对 offset 0xce
    -> 12 个大端 u16，单位 0.0001 mW
```

该项目源码会把全零向量直接视为 unavailable，而不是 12 路真实的 0 mW。小散热 `sil001 / hw_version 4` 实板严格复现了上述 mux、设备、分页和字节序，结果如下：

- mux `0x01` 和 `0x02` 下的 `0x40` lower page 内容不同，确认确实访问了两块独立 FCI；
- TX 设备 `0x50` page 0 可稳定读取身份：两块都是 `FCI MergeOptics / 10124588-211`，序列号分别为 `ESOM1647-00011`、`A1OM1704-00028`，日期码分别为 `20161114`、`20170116`；
- page 1 选择、读取、恢复均返回 `FM_OK`，且 page 1 与 page 0 内容明显不同，确认分页操作真实生效；
- 两块 RX 设备在 common upper page 0 的 CXP capability byte `0x8c` 都返回 `0x04`：只声明 internal temperature monitor；逐 Lane RX input power（bit 5）、TX light output power（bit 6）和 TX bias（bit 7）均未声明；
- 无外部链路时，两块模块 `page 1:0xce..0xe5` 均为 24 字节全零；
- 接入一路光纤后，逻辑端口 1 明确为 `UP`、Signal Detect 为 `Y`，但同一区域仍为 24 字节全零；
- 扫描 RX `0x40` 和 TX `0x50` 的 lower page 与 page 0–15，除 page 0/1 外其余页面为全零或全 `0xff`，没有发现可确认为 12 路 RX 功率的连续向量；
- 扫描 mux 分支下的其余应答地址后，`0x59` 和 `0x64` 在 mux 关闭时仍可访问（其中 `0x64` 是平台配置里的 PCA9538），`0x49` 的一次性非零内容读后清零且同样可在 mux 关闭时访问；这些都不是光引擎后端的实时 RSSI/ADC；
- 在 EPL0 Lane 0 链路正常时拔纤对比，RX `0x40:0x08` 从 `0xfe` 变为 `0xff`，TX `0x50:0x06` 从 `0x02` 变为 `0x00`、`0x50:0x11` 从 `0x01` 变为 `0x00`，确认这些是 LOS/链路状态位；此前怀疑的 `0x40:0x0c..0x0d = 0x0e7f` 不随插拔变化，不是光功率。

因此当前结论是：模块身份并未不可读，旧界面只是没有把 `0x50` 的身份读取与功率读取分开；但本机 B0 上这两块 2016/2017 年生产的 `10124588-211` **没有通过 CXP 管理接口声明或输出可用的光功率监控数据**。大散热板上同一 `page 1:0xce` 代码能返回数据，应视为其 OBT 版本/固件能力不同，不能反推 B0 只缺一个通用解锁开关。厂商密码页、编程接口或未公开固件仍无法从现有资料绝对排除，但在没有寄存器定义和校准参数时不具备可验证、可产品化的读取路径。WebUI 仅在 24 字节功率向量非零时解码，绝不把全零显示为有效光功率。

当前读取逻辑由 WebUI 临时生成 TestPoint 脚本，只选择 mux/page、读取后恢复，不在板卡上遗留诊断文件。若后续取得大散热实板或完整 FCI 应用说明，应在至少一路已确认 Link Up 的条件下复用相同的 `0x8c` capability 与 `page 1:0xce` 对照流程，不能把 AP8 大散热结果直接套用到 `sil001`。

## 5. 安全边界

- 不要直视 MPO、光纤端面或以“看不到红光”判断绝对安全；工作波长可能含不可见红外光。
- 熄光、端口 powerdown、MPO 总开关都会中断业务，必须在维护窗口执行。
- FM10840 和两块 OBT 必须有强制风冷。诊断时不要长时间把风扇降至过低占空比，当前 UI 最低限制为 25%。
- 风扇脚本应先进入 100% 故障保护，成功写完 LUT 后再交回自动控制；发生异常宁可满转，不要停转。
- 不要绕过唯一常驻 TestPoint owner。
- 不要把 `i2cdetect`/Linux hwmon 读不到 `0x4c` 误判为器件不存在；该路径位于 FM10840 switch I²C/CPLD 后面。
- 不要修改或擦除 FCI EEPROM 中未理解的私有数据。本文控制的只是运行时发射掩码，不是光模块出厂校准。

## 6. 修改代码后的最小检查表

风扇相关：

1. 生成脚本中先选择 mux `0x08`，最后恢复 `0x01`；
2. `PWPGM=1` 时写 LUT，结束后清零交回 LUT；
3. 临界温度固定进入 100%；
4. 配置同时写入 JSON 和启动 TP 脚本；
5. 冷启动后出现 `switch-ready`、`fan-ready` 和 `FAN_DONE`；
6. TACH 会随明显不同的 PWM 改变；不改变时先查蓝线。

光发射相关：

1. logical port power 状态和 FCI mask 同时更新；
2. Lane 使用原始 0–3 编号；
3. bit 位置按 MPO 内 EPL 位置计算，不能用 logical port 编号直接移位；
4. offset 56/57 逐字节写入、回读、重试；
5. 全关时两个 MPO 最终都是 `0x0000`；
6. 失败不能写完成标记，必须触发配置回滚；
7. 不重新引入标准 QSFP byte 86 控制。

## 7. 已确认与仍未知

已经通过手册、源码和实板确认：

- 风扇 4-pin 定义、LM96163 地址、mux 分支、FM10840 远端温度源和 CPLD TACH 映射；
- LM96163 12 点硬件 LUT 可自主调速，并能通过启动服务持久恢复；
- PWM 蓝线断开/接触不良会出现“寄存器正常但实际满转”；
- 两块 FCI 使用同一 I²C 地址并通过 mux 区分；
- 仅 powerdown 逻辑端口不足以可靠熄光；
- FCI offset 56/57 可逐 Lane 控制发射，且必须写后回读重试；
- 两个 MPO 全关时 raw `0x38/0x39` 均可达到 `0x00/0x00`。
- 两块小散热 FCI 的 `0x50` page 0 均可读取厂商、型号、序列号与日期码；
- `netlab-os` RPC 90 的 mux、`0x40` 和 page 1 访问路径可在实板复现，但本机 `0xce` 功率向量在端口 1 Link Up 时仍为全零，当前不可用。

仍不应作确定结论：

- FCI 私有 EEPROM 的完整寄存器定义；
- 当前 OBT 的标准 DOM、逐 Lane 光功率、衰减和偏置电流；已排除直接采用 `0x40/page 1/0xce` 的方案；
- AVC DAZG1225R2H 每个占空比对应的精确 RPM 曲线；
- 不同替换风扇的 PWM 极性、最低启动占空比和 TACH 每转脉冲数。

这些未知项必须保留为未知，不能从标准 QSFP 格式或相似风扇型号推断后直接写入产品逻辑。
