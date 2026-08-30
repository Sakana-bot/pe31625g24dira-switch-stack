# PE31625G24DIRA Switch Manager

Switch Manager 是 PE31625G24DIRA Switch Stack 的管理界面，与仓库根目录 `VERSION` 共用版本号。后端使用 Python 3.9+ 标准库 HTTP 服务，前端使用原生 HTML、CSS 和 JavaScript，不依赖 Web 框架。

当前整机部署已在 Debian 13 和 Ubuntu 26.04 x86_64 上验证。面向设备用户的安装与升级方法见仓库根目录 `README.md` 和 `deployment/README.md`；本文只记录 WebUI 的当前开发结构。

## 页面与数据来源

- 概览：Linux CPU、内存、系统盘、管理口，以及 FM10840 端口汇总。
- 端口：EPL 拆分/聚合、10G/25G/40G/100G 速率、端口名称、发光与转发开关、RMON 统计。
- VLAN：Access、Trunk、Hybrid、Native VLAN、Tagged VLAN 和 MAC 地址表。
- 网络功能：环路保护、风暴抑制、被动 LLDP 邻居和端口镜像。
- 传感器：Linux hwmon、FM10840 温度/电压和板载光引擎身份、温度。
- 散热：风扇转速、温度阈值和硬件风扇曲线。
- 日志：本次启动的系统、内核和交换服务日志，只允许读取固定白名单来源。
- 备份与升级：逻辑配置导入导出、恢复默认配置、部署包检查与升级。
- 设置：主机名、明暗模式、流量单位和 Web 管理员账号。

高频链路与 RMON 数据直接从 `/dev/uio0` 读取；传感器、端口配置、VLAN、风扇和光引擎操作统一进入常驻 TestPoint 的单一队列。手动操作优先，周期采样作为可合并的低优先级任务，避免多个进程同时占用 FM10840 SDK。

## 端口模型

两个 MPO24 对应六个 EPL：

- MPO24-1：EPL 0、1、2
- MPO24-2：EPL 5、6、7

每个 EPL 固定占用四个逻辑槽位，六组共 24 个槽位。拆分模式启用四个 10G/25G Lane；聚合模式只启用该组第一个槽位作为 40G/100G 端口，其余三个保持禁用。EPL 与 Lane 均按 FM10840 原始的零起始编号显示。

端口拓扑仅重设发生变化的 EPL，VLAN 使用增量下发；正常操作不重启整个交换服务。全新安装阶段由部署器把已验证的原厂 platform 转换为固定槽位模型。

## 诊断数据说明

端口统计来自 FM10840 RMON/MAC 寄存器。编码错误在拔插光纤、失锁或重新训练期间增长并不等同于持续链路故障，应清零后在稳定链路下观察是否继续增长。

TestPoint 的 `FM_PORT_EYE_SCORE` 以低字节表示眼高、高字节表示眼宽，范围均为 0–64；值 `0xff` 表示该项不可用。当前 PE31625G24DIRA runtime 对所有已检查 Lane 均只提供眼高、眼宽返回 `0xff`，因此页面只显示“眼高 n / 64”，不会把 `n/NA` 当成完整眼图评分。

板载 FCI/Amphenol 光引擎不是标准可插拔 QSFP。页面按原厂实现读取发射光引擎温度，不会用其他标准模块的 DOM 偏移伪造电压或 TX 功率；私有 RX 功率路径无有效数据时明确显示不可用。

## 安全边界

- 首次访问由设备所有者创建 Web 管理员，不提供默认账号。
- 密码使用 PBKDF2 摘要；会话 Cookie 为 HttpOnly/SameSite，写操作需要 CSRF token。
- 服务使用 HTTP 80，不加密登录口令，只应部署在可信管理网络。
- 不提供终端、任意命令、SSH 密钥、系统用户或软件包管理入口。
- 关机、端口关闭、拓扑、VLAN、恢复和升级均要求确认；配置写入失败会尝试恢复旧状态。

## 本地前端预览

在 Windows 工作区根目录运行：

```powershell
python .\webui\qa_server.py
```

打开 `http://127.0.0.1:18741/`。预览服务器只提供模拟数据，不连接板卡，也不会执行硬件写操作。

前端文件位于 `webui/static/`：

- `index.html`：页面结构
- `app.js`：页面路由、状态与业务交互
- `controls.js`：自绘下拉、多选和数字步进控件
- `api-client.js`：API 与异步作业轮询
- `theme.js`：浅色、深色及跟随系统
- `style.css`：布局、组件和主题样式

后端入口为 `app.py`；`runtime_state.py` 负责会话、缓存与 SDK 任务队列，`l2_features.py` 负责二层功能模型，`optics.py` 只生成低频光引擎身份/RX 诊断脚本。周期温度读取保留在 `sensors.tp`，避免与身份缓存重复访问同一 I²C 器件。

界面字号、字重、颜色与图标规则见根目录 `UI_STYLE_GUIDE.md`。新增样式应复用现有 token，避免为单个页面堆叠特例。

桌面端使用常驻侧栏；宽度不超过 700px 时改用顶栏按钮控制的侧栏抽屉。移动端仍保留相同的一级/二级功能结构，表格只在自身区域滚动。

## 基础检查

```bash
python3 -m unittest webui.test_app
node --check webui/static/app.js
```

前端样式修改只需执行基础语法检查并在本地预览核对；涉及端口、VLAN、升级或硬件写入时再按风险补充实机验证。
