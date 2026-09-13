# MarketPilotDaily
每日的股市分析日报

通过手机和电脑网页管理个人持仓，每天由云端任务自动生成分析日报，以北京时间 08:30 为目标通过邮件发送。优先使用免费托管与免费数据，只有 AI API 允许付费。

当前设计、数据接口、开发阶段和验收标准见 [网页持仓与每日邮件实现方案](docs/web-email-implementation-plan.md)。

2026-09-13 已上线持仓网页，支持 GitHub 个人登录、持仓管理、投资偏好和 Cloudflare D1 同步；真实登录、云端增删改和退出已验证。访问 [MarketPilotDaily](https://market-pilot-daily.market-pilot-daily.workers.dev)，仅允许已配置的个人账号。AI 日报、定时任务和 163 邮件仍未接通。旧 [iOS 实现方案](docs/ios-implementation-plan.md) 仅保留作历史记录。

## 本地启动

需要 Node.js 22.12 或更高版本。Windows PowerShell 下可将 `npm` 改为 `npm.cmd`。

```powershell
npm ci
Copy-Item .dev.vars.example .dev.vars
npm run db:migrate:local
npm run dev
```

打开 `http://127.0.0.1:5173/`。首次启动为空持仓，数据库保存在被 Git 忽略的 `.wrangler/` 目录。已有 `.dev.vars` 时不要覆盖它。本地身份模式仅接受回环地址，不支持直接通过局域网地址访问。

持仓范围已确定为 A 股、美股及这两个市场上市的 ETF。新增持仓先选择市场／交易所，再输入代码：A 股例如 `510300`，美股例如 `AAPL`、`VOO`、`BRK.B`。美股零碎股可填写小数，数量最多 6 位小数。

证券核验使用 TickFlow 免费接口；美股再通过 Nasdaq 官方目录核验 ETF 标记，避免把 ETF 误认为股票。服务端在新增时再次核验，无法核验的证券不会保存。这些步骤需要联网，当前没有接入价格和新闻。

人民币（CNY）和美元（USD）持仓、成本及现金分别记录，不直接相加。已使用旧版的本地数据库时，先运行 `npm run db:migrate:local`，再刷新网页；迁移会保留原有持仓及人民币现金。

```powershell
npm test
python -m unittest discover -s jobs -p 'test_*.py' -v
npm run build
```

测试使用独立的内存 D1 与模拟外部接口，不读取真实持仓、不调用付费模型、不发送邮件。`package-lock.json` 固定本轮验证的依赖。

## 已实现与下一步

- 持仓添加、修改、删除及 30 秒撤销；名称和资产类型由服务端核验。
- 数量和金额用十进制字符串保存，未知成本和零成本分别处理。
- 现金、风险偏好、暂停发送偏好；未填写值保持未知。
- 数据库事务、版本检查、并发冲突提示；失败时保留表单。
- GitHub OAuth 登录与固定用户 ID 白名单，24 小时会话、服务端退出撤销；配置缺失时拒绝访问。
- 桌面与手机布局；日报页展示实际“尚未启用”状态。
- DeepSeek 标准对话模式适配器与 D1 月度预算基础模块；尚未连接实际日报任务。配置见 `config/ai.json`，月度预算 10 元，测试不产生 API 费用。
- 163 TLS 邮件适配器，包含纯文本／HTML 邮件、安全转义、发送状态分类和禁止自动重发；尚未连接持久化发送网关和定时任务。配置进度见 [邮件配置](docs/email-setup.md)。

用户已确认使用 Cloudflare 免费账号与 DeepSeek，并改用 GitHub 登录，无需开通 Access。保留当前公开仓库：代码公开不等于持仓公开，真实持仓和报告保存在受保护的 D1，凭证放入 Secrets，不能写进公开日志或构建附件。GitHub 应用与云端 Secret 已配置完成。配置与实际进度见 [GitHub 登录](docs/github-login.md)。

实施进度、验证结果、待确认信息和部署准备见 [开发与联调记录](docs/development.md)。
