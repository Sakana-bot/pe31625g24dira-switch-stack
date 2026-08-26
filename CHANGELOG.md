# Changelog

## v1.0.0 — 2026-08-26

首个正式版本。

> 本项目全部代码由 ChatGPT 生成，不保证正确性、可靠性、安全性、可用性或适用于任何用途。

- WebUI 1.0.0：端口、VLAN、硬件监控、日志、备份恢复及 GitHub 在线升级。
- deploy-kit 1.0.0：首次安装、审计、完整卸载和保留配置的增量升级。
- legacy SDK runtime 2.1.0：从原始系统盘提取并清除设备身份信息，适配 `sil001-hw4-b0`。
- deploy-kit 内置已验证 platform，首次安装按需配合同一 Release 的 runtime；未知 platform 默认拒绝部署。
- 在线和上传更新包会自动校验 SHA-256、比较版本、审计差异并在确认后执行；驱动通过 DKMS 针对当前内核现场构建。
