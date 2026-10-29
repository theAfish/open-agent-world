# 贡献与维护 OAW

修改宿主、维护文档和操作部署从这里开始。独立 Card App 请走[开发指南](../developers/index.md)。

## 开发改动

1. 从最新 `origin/dev` 创建独立分支或 worktree。
2. 按[源码运行指南](../getting-started.md)准备独立开发档案。
3. 查[架构](../architecture.md)与受影响子系统的契约。
4. 先跑最小相关测试，再按实际风险运行子系统测试。
5. 更新对应文档，向 `dev` 提交 PR。

不能为通过检查删除安全断言、弱化授权验证或跳过失败测试。报告实际运行的测试及覆盖缺口；单元测试或浏览器构建不能代表原生桌面、生产或隔离运行时已经验收。

## CI 与权限设置

[CI 指南](../../.github/CI.md)说明工作流中的 `CI required`、`Docs required` 和 `Native required`。合并前查看实际 PR 当前 commit 的检查结果，不重复触发同一 commit 的完整 CI。

Ruleset、审批数和 bypass 是 GitHub 实时设置，仓库副本无法证明当前状态。管理员应在 **Settings → Rules → Rulesets** 核对；本指南不声明某套 Ruleset 当前启用，也不授权修改权限。

## 维护入口

- [桌面开发](../desktop.md)、[发布流程](../releasing.md)。
- [锁定工作区部署](../deployment.md)、[多用户托管](hosting.md)。
- [教程维护](../tutorial.md)、[文档维护](../developers/documentation.md)、[写作检查表](docs-checklist.md)。
- [请求边界](../enterprise-foundations.md)、[安全](../security.md)、[图与画布](../graph-architecture.md)。

[内部记录](../internal/README.md)只保存历史依据，不是当前操作说明。
