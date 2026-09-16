# MarketPilotDaily
每日的股市分析日报

通过手机和电脑网页管理个人持仓，每天由云端任务自动生成分析日报，以北京时间 05:15 为目标通过邮件发送。优先使用免费托管与免费数据，只有 AI API 允许付费。

当前设计、数据接口、开发阶段和验收标准见 [网页持仓与每日邮件实现方案](docs/web-email-implementation-plan.md)。

2026-09-13 已上线持仓网页、日报生成与历史阅读，支持 GitHub 个人登录和 Cloudflare D1 同步。访问 [MarketPilotDaily](https://market-pilot-daily.market-pilot-daily.workers.dev)，仅允许已配置的个人账号。DeepSeek 和 163 的真实联调通过，完整测试日报已获 SMTP 接受；每日发送开关已开启，首次计划于 2026-09-14 北京时间 08:30 左右发送。尚待真实定时运行及连续试用验证。旧 [iOS 实现方案](docs/ios-implementation-plan.md) 仅保留作历史记录。

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

证券核验使用 TickFlow 免费接口；美股再通过 Nasdaq 官方目录核验 ETF 标记，避免把 ETF 误认为股票。服务端在新增时再次核验，无法核验的证券不会保存。这些步骤需要联网；价格和新闻由日报任务另行采集。

人民币（CNY）和美元（USD）持仓、成本及现金分别记录，不直接相加。已使用旧版的本地数据库时，先运行 `npm run db:migrate:local`，再刷新网页；迁移会保留原有持仓及人民币现金。

```powershell
npm test
python -m pip install -r requirements.txt
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
- 桌面与手机布局；日报按重点、今日/短期/长期计划、行情卡片、新闻与来源分区；自动打开最新可读记录，保留最近 30 次任务切换。
- 邮件使用自适应宽度的 HTML 与纯文本备份，两者只发送重点摘要：今日结论、最多三项行动、重要事件与持仓变化、必要数据提醒和完整日报入口。正文目标 400–600 字，平静日更短，仅附引用来源；摘要与完整日报在同一次 AI 调用中生成。已有网页日报可重新排版，无需重复调用 AI。
- 本人登录后查看当月 AI 已用/预留费用、最近正式推送状态；06:00 后无当天发送记录时提示核查任务。
- DeepSeek 标准对话模式与 D1 持久化预算网关。配置见 `config/ai.json`，月度预算 10 元；自动测试不产生 API 费用。
- GitHub Actions 05:00 北京时间启动，目标 05:15 发送；使用 OIDC 短期身份，只允许本仓库 main 上指定工作流访问任务 API。
- 免费日线与国内外 RSS；只使用实际取得的证据。资金模块包含 A 股行业主力资金、沪市代表 ETF 净申赎估算和持仓关联；支持当日／近 5 日排名，邮件只发前三名和持仓方向变化。美股、深市 ETF 净申赎、完整交易日历与未来事件日历仍未覆盖。口径与限制见 [资金数据说明](docs/fund-flows.md)。
- 163 TLS 邮件、纯文本／HTML 正文、安全转义、D1 发送状态分类与防重复。配置与联调进度见 [邮件配置](docs/email-setup.md)。

用户已确认使用 Cloudflare 免费账号与 DeepSeek，并改用 GitHub 登录，无需开通 Access。保留当前公开仓库：代码公开不等于持仓公开，真实持仓和报告保存在受保护的 D1，凭证放入 Secrets，不能写进公开日志或构建附件。GitHub 应用与云端 Secret 已配置完成。配置与实际进度见 [GitHub 登录](docs/github-login.md)。

实施进度、验证结果、待确认信息和部署准备见 [开发与联调记录](docs/development.md)。
