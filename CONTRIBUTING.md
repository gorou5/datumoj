# CONTRIBUTING.md — 贡献指南（人类协作者）

欢迎。datumoj 为利用碎片时间协作而设计：你不需要了解整个项目，就能独立完成一个有价值的小贡献。

## 第一次参与（15 分钟）

1. 读 `README.md`（项目使命）和 `AGENTS.md`（含 AI 协作约定，即使你不用 AI agent 也建议浏览 §2 禁令）。
2. 浏览 `docs/architecture.md` 了解三个统一标准。
3. 跑通本地校验：`pip install jsonschema pyyaml && python tests/validate_schemas.py`。

## 标准开发流程

1. **小步独立**：每项功能尽量独立，避免无关功能混在同一次提交。
2. **分支开发**：在自己的分支上开发，**不直接向 `main` 推送业务代码**。
   ```bash
   git checkout -b feat/jp-meti-dx-ninsho   # 或 fix/xxx、docs/xxx
   ```
3. **先读后写**：开发前阅读相关模块规范；修改前先理解现有实现，避免重复造轮子。
4. **本地验证**：完成后运行适用的测试和检查（至少 `tests/validate_schemas.py`；有业务代码时运行其自带测试）。
5. **Pull Request**：用 PR 模板说明修改目的、影响范围、测试结果、安全/许可问题。
6. **审查合并**：CI 通过后，由其他参与者审查再合并。AI agent 的产出同样需要人类审查，**使用 AI 不免除代码审查和安全责任**。
7. **部署**：正式部署走统一 CI/CD，不依赖个人电脑发布。

## 新数据源 / 新模块清单

- [ ] 在 `schemas/` 找到对应 Schema（data-source / module），按字段填写；未知填 `unknown`，不编造。
- [ ] 在 `examples/` 或模块目录下给出最小可运行示例。
- [ ] 记录官方来源 URL、许可、访问限制、最后验证时间（见 `docs/data-sources.md`）。
- [ ] 测试使用模拟数据，不高频请求真实政府网站。
- [ ] 不提交任何密钥（见 `SECURITY.md`；`.env.example` 只放变量名和虚构值）。

## 行为准则

见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。核心：对事不对人，欢迎新手提问，拒绝骚扰。

## 需要帮助？

- 规范问题：看 `docs/`。
- 提需求/报 bug：用 `.github/ISSUE_TEMPLATE/` 模板提 Issue。
- 安全问题：**不要公开提 Issue**，见 [SECURITY.md](SECURITY.md)。
