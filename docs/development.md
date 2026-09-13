# 开发与联调记录

日期：2026-09-13。当前状态：持仓网页、GitHub 登录、免费日线与 RSS、DeepSeek 分析、D1 预算与发送记录、163 SMTP 和报告历史页已串联。完整测试日报通过校验并获 SMTP 接受，每日发送已启用，首次计划 2026-09-14 08:30 左右。真实定时调度、连续稳定性及用户收件箱验收仍待观察。最新进度以 [邮件与任务记录](email-setup.md) 为准；下文早期阶段及 Access 部分保留作历史记录。

当前部署版本：`660f81b8-b743-4eed-8656-f0d842fa3dca`。D1 已应用 `0001`–`0006` 迁移，外键检查通过；发送开关保存为 `email_paused=0`。每日只自动使用版本 1，人工测试可显式选择版本 1–3，不覆盖已发报告。任务身份使用 GitHub OIDC；无需另外保存长期机器凭证。

新增接口：本人访问 `GET /api/budget`、`GET /api/reports` 与 `GET /api/reports/:id`；指定 GitHub 工作流访问 `/internal/runs/claim` 及其预算、报告、发送状态端点。网页身份不能调用机器端点，机器身份不能修改持仓。

首版缺口：资金净流入、两融、ETF 申赎、估值、财报、完整交易所与未来事件日历均未接入。日线最多采集 20 只持仓，新闻仅来自当前配置的三家 RSS，不承诺全网覆盖。跨币种仓位不会合并，现金或价格不全时保留未知。没有自动交易功能。

## 本轮实现

使用 React、TypeScript、Vite、Cloudflare 官方 Vite 插件及 D1。本地网页与 Worker 共用入口，避免额外维护一套模拟服务器。

页面提供持仓列表、搜索、添加、编辑、删除确认、30 秒撤销、现金和投资偏好表单。用户已确认范围为 A 股、美股、ETF；现已支持沪深北股票、美股与两地上市 ETF。未纳入港股、场外基金和期权。

`GET /api/instruments?symbol=510300.SH` 或 `?symbol=VOO.US` 查询免费证券资料。服务端验证代码、市场与地区匹配；新增时再次核验，不信任客户端传来的名称、类型或币种。美股代码统一转为大写，保留 `BRK.B` 这种类别后缀，不自动把未知代码映射成其他证券。`US` 是行情源使用的市场分组标识。

实测发现 TickFlow 将 VOO、QQQ 的类型返回为 stock，因此美股使用 Nasdaq 官方 `nasdaqlisted.txt` / `otherlisted.txt` 的 ETF 字段区分股票与 ETF。目录不可用、格式变化、测试证券或代码未找到时拒绝新增，不将未知类型默认为股票。美股证券资料核验不等于完整行情或基本面已接通；未在当前官方上市目录中的证券暂不支持。

当前接口：

| 路径 | 行为 |
| --- | --- |
| `GET /api/portfolio` | 事务内读取当前持仓、现金、偏好与统一版本号 |
| `GET /api/instruments` | 核验 A 股、美股或 ETF 代码及类型 |
| `POST /api/positions` | 新增持仓 |
| `PATCH /api/positions/:id` | 修改数量、成本、期限与理由 |
| `DELETE /api/positions/:id` | 软删除当前持仓 |
| `POST /api/positions/:id/restore` | 在删除后 30 秒内恢复 |
| `PATCH /api/cash` | 必须携带 `currency: CNY / USD`，仅修改对应币种现金 |
| `PATCH /api/profile` | 更新风险偏好与邮件暂停偏好 |

所有写操作携带 `revision`。SQL 条件检查版本，触发器递增版本，D1 batch 在同一事务中返回新快照。两次相同版本的并发修改只能有一次成功；失败返回 409。偏好也计入统一版本，后续日报只需冻结一个一致的输入版本。暂不另设独立 profile revision，减少两个版本混用的可能。

数量和成本为十进制字符串，最大 12 位整数、6 位小数，可记录美股零碎股。空成本为 null，零成本为 `"0"`。A 股使用 CNY，美股使用 USD；后端及数据库均限制市场与币种匹配。

API 的 `cash` 和 `cashAsOf` 分别返回 `{ CNY: ..., USD: ... }`，两种币种的金额与更新时间独立保存。现金币种缺失时拒绝保存，避免旧页面将美元写成人民币。`0002_us_holdings.sql` 保留旧持仓、软删除记录、人民币余额和版本号，新增美元现金默认为未知。当前不计算账户市值、仓位或盈亏，也不在汇率缺失时合并跨币种金额。

默认暂停发送。取消勾选仅保存偏好，不会启动尚未实现的后台任务。日报页面没有假新闻、示例收益或虚构发送记录。

## 本轮验证

- `npm test`：改用 GitHub 后共 29 项 Worker / D1 集成测试通过（21 项持仓／预算和 8 项 OAuth），生产构建通过；测试出站请求全部由模拟接口处理。
- `python -m unittest jobs.test_deepseek -v`：7 项测试通过，覆盖标准对话参数、预算不足、未知扣费保留预留、无效输出计费和错误信息脱敏；未调用付费 API。
- `npm run build`：TypeScript 检查与前后端生产构建通过。
- 本地 D1 迁移成功，页面真实调用 Worker API。
- 真实免费接口用公开示例核验了沪市股票、深市股票、北市股票、境内 ETF、美股（AAPL、BRK.B、PDD）及美股 ETF（VOO、QQQ），并交叉检查了 Nasdaq 官方 ETF 标记。未使用真实持仓；该检查不代表行情、财务、新闻接口已验证。
- 浏览器完成添加、编辑、双页面冲突恢复、删除撤销、零现金保存测试。
- 美股扩展后，在浏览器实测 VOO 核验为 ETF、成本单位显示美元；人民币与美元现金有独立的输入和保存按钮。
- 通过本地 390 像素容器检查生产页面与新增表单的手机断点布局。当前浏览器视口覆盖能力未实际改变尺寸，因此采用临时测试页面，验证后移除。仍需部署后在实际手机和目标网络验收。

自动测试覆盖：初始未知值、精确小数与零成本、CRUD 和撤销、并发写入、现金与偏好版本冲突、重复证券、证券身份与资产类型校验、数据源失败、非法金额和数量、过期撤销、跨站写入、伪造邮箱头、JWT 签名／有效期／受众与个人白名单、机器身份拒绝、禁止缓存及未开放的任务端点。新增覆盖美股零碎股与带点代码、官方 ETF 分类、目录故障不降级成股票、双币种余额与冲突、旧库数据保留和市场／币种数据库约束。

本地身份模式需要 `.dev.vars` 中的 `LOCAL_DEV_AUTH=true`，且请求主机必须是回环地址。生产与预览请求都需要 Cloudflare JWT。客户端自填的邮箱头不作为身份依据；Access 尚未配置时返回 503，不开放页面和 API。仍需实际账号验收 Access 策略与生产入口。

## 已确认配置与预算实现

用户已有 Cloudflare 免费账号，选择 DeepSeek 标准对话模式，每月预算 10 元。根据本轮查阅的官方文档，采用 `deepseek-flash` 并显式设置 `thinking.type=disabled`。旧别名不作为新实现默认配置；模型、限额与价格基准保存在 `config/ai.json`，不包含密钥。

Python 适配器 `jobs/deepseek.py` 使用官方 HTTPS 接口与 JSON 输出；限制输入体积和输出 token，不自动重试。调用前必须经过持久化预算网关，拒绝未预留预算的付费调用。D1 模块 `worker/ai-budget.ts` 与 `0003_ai_budget.sql` 按北京时间自然月原子预留，每次 0.20 元；并发和重复调用不能绕过预留限额。成功取得 usage 后按当前高峰价格保守结算，未知结果保留预留额度，避免失败后重复扣费。

预算是本项目的估算控制，不是 DeepSeek 账户的全局账单上限；当前按输入 2 元／百万 token、输出 8 元／百万 token 估算，不扣缓存和非高峰优惠。价格调整后需复核配置；本项目以外的调用不计入本账本。预留额结合 60,000 字节请求限制和 8,192 输出 token 设置。若返回用量超过预留则记录实际估算并报错，不隐去超额。

适配器和数据库模块尚未通过生产任务 API 连接；当前 `/internal/` 仍关闭。尚未加入行情采集、日报业务结构校验、定时工作流或真实模型调用，不能将基础模块视为日报已接通。

公开仓库可以运行本项目，不强制转为私有；维持当前可见性。私有仓库只是减少误把持仓、报告写进日志／附件后的暴露面。公开方案要求个人数据只存受保护的 D1，密钥存 Secrets，任务日志只输出状态码和必要的非敏感信息，不上传输入、报告或个人资料附件。Secrets 自动遮蔽不能代替这项约束。

## 待用户对齐

持仓范围已确认：A 股、美股、ETF；无需再次确认。余下部署输入：

1. GitHub 登录、Worker 与 D1 已配置并完成浏览器验收；仍需用户在实际手机及目标网络试用。
2. DeepSeek 密钥通过安全配置入口录入，不进入聊天、Git 或前端；模型和每月 10 元预算无需再次确认。
3. 163 发件和收件地址已由用户确认使用同一邮箱，地址保存在 GitHub Secrets；SMTP 客户端授权码仍需用户在安全配置入口录入。当前模块与验证范围见 [邮件配置](email-setup.md)。

## Cloudflare 部署记录（2026-09-13）

- Wrangler OAuth 登录已确认成功；凭证采用加密文件与 Windows Credential Manager 保存。
- 创建 APAC 区域 `market-pilot-daily` D1 数据库，已应用 `0001`–`0003` 三项迁移；真实 ID 已写入 `wrangler.jsonc`。没有上传本地数据库。
- 生产构建及 `wrangler deploy` 成功。入口为 `https://market-pilot-daily.market-pilot-daily.workers.dev`，版本 `2e6f6b08-b3c2-47a2-b5f6-8cc2d1d8ff0b`。
- 部署绑定中没有 `LOCAL_DEV_AUTH`。云端 SQL 验证持仓数量为 0，邮件暂停标记为 1。
- 用 Node HTTPS 请求真实 `/api/portfolio`，返回 503 和“访问保护尚未配置”。尚未验收正常登录和云端 CRUD。
- Windows curl 出现 TLS 握手错误，Chrome 自动化打开网址返回 `ERR_BLOCKED_BY_CLIENT`；Node 使用正常证书验证可连接，不能据此断言目标浏览器或手机已可用。
- Access 开通到达 `Activate Zero Trust Free` 页面：要求支付方式、账单地址及同意每月超额扣费。没有填写或提交支付信息。已向用户提出改用仅允许本人 GitHub 账号的登录方案，等待确认，不擅自开通扣费。

## Cloudflare 原部署步骤（登录保护尚未完成）

1. 登录 Cloudflare 并确认 Workers、D1、Zero Trust / Access 的免费计划可用；不升级付费计划。
2. 创建 D1，将返回的真实 database ID 写入 `wrangler.jsonc`，替换全零占位符。不能将本地测试数据库上传作为真实持仓。
3. 在 Access 中保护生产 Worker 的全部访问，并将 Allow 策略精确限制到本人的完整邮箱。预览 URL 当前禁用；以后启用时必须同样保护。
4. 配置 `ACCESS_TEAM_DOMAIN`（仅域名，例如 `team.cloudflareaccess.com`）与 Access application 的 `ACCESS_AUD`；使用 Worker secret 配置 `OWNER_EMAIL`，避免个人地址被提交到公开代码。云端不配置 `LOCAL_DEV_AUTH`。
5. 在远端应用 D1 迁移并部署构建产物。上线前验证匿名访问、无效 JWT、错误邮箱、正确邮箱及可用入口；用实际手机测试访问和增删改。
6. M1 云端验收后，再建立独立的机器身份及 `/internal/` 任务接口。目前所有任务接口均返回 404，机器身份不能编辑持仓。

上方部署记录为实际完成状态。尚未登录邮箱、配置模型凭证、启用定时任务或发送邮件。

## 后续开发顺序

- 完成 M0 账号与网络实测、明确 AI 和邮件配置。M2 按已确定的 A 股、美股及 ETF 范围接入行情、分类分析与两地交易日历。
- 完成 M1 云端部署、跨设备与 Access 验收。
- M2：Python 免费数据采集、确定性计算、证据包与结构化 AI 输出；资金流、财务等未覆盖项必须明确记录。
- M3：运行记录、不可变快照、发送状态机、163 TLS SMTP、默认暂停的 GitHub 工作流；处理迟到、失败、发送结果不明及同日重跑。
- M4：真实报告与历史页面、连续试运行。

## 本轮参考

- [Cloudflare 官方 Vite 插件](https://developers.cloudflare.com/workers/vite-plugin/get-started/)
- [D1 batch 事务语义](https://developers.cloudflare.com/d1/worker-api/d1-database/)
- [Worker Access 保护](https://developers.cloudflare.com/workers/configuration/cloudflare-access/)
- [Access JWT 验证](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
- [TickFlow 免费服务](https://docs.tickflow.org/zh-Hans/quickstart)
- [TickFlow 官方 OpenAPI](https://docs.tickflow.org/zh-Hans/api-reference/openapi.json)
- [Nasdaq 官方证券目录字段定义](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs)
- [Nasdaq 上市证券目录](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt)
- [其他美国交易所上市证券目录](https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt)
- [DeepSeek 模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)
- [DeepSeek JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)
- [GitHub Actions 日志访问](https://docs.github.com/en/actions/how-tos/monitor-workflows/using-workflow-run-logs)
- [GitHub Actions Secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)


## 日报阅读优化（2026-09-13）

新增 `jobs/presentation.py` 与网页 `src/report-format.ts`，从已存证据生成可读展示；旧版保持内容不变，新版缩短提示词中的篇幅要求。邮件采用 680px 最大宽度、内联样式和纯文本备份，在 375px 宽度检查分段。网页新增预算摘要、正式推送状态、最新日报自动打开、章节导航、行情卡片与来源折叠。没有修改持仓、数据库结构或发送去重记录。

预算接口在原有 owner 鉴权之后，按北京时间月份聚合；已结算、预留与不确定调用分开，不向匿名访问或机器身份开放。网页的缺失日报提示只反映本次刷新所见的发送记录，不等于主动后台监控。

验证：35 项 TypeScript 与 31 项 Python 测试通过，生产构建通过；新增校验包含预算聚合与鉴权、金额/时区/缺失值、历史文本完整保留、模型 HTML 转义、失败分析不展示原始建议。

实际测试第 3 版通过结构校验并获 SMTP 接受，详见邮件配置文档。末次补充风险口径说明与报告详情刷新重试：点击刷新会重新读取所选报告，避免临时详情请求失败后无法恢复。新提示词规则不追溯修改已发原文。


## 缺失数据与新闻时间修复（2026-09-13）

继续检查发现两项可复现问题，均先添加失败测试再修复：

- 行情获取失败会将来源时间写为“未知”，网页来源索引仍会调用日期格式化，导致 `Invalid time value`。现在无效时间显示“时间未知”，不会阻断报告阅读。
- RSS 新闻曾按带不同时区偏移的 ISO 字符串排序。现在按实际时间比较，确保相关性相同时较新的新闻优先。

本次验证：3 项网页格式测试、32 项 Python 测试及生产构建通过。未调用付费模型或额外发送邮件。GitHub 工作流当前 active，最新成功记录仍为手动测试；首个正式定时运行尚未发生。

下一轮优先强化 AI 正文与确定性指标/引用的一致性检查、交易日及过期数据判断，再扩展基金/行业相关来源与独立故障告警。现有提示词约束和 JSON 结构校验不能替代内容事实核验。
