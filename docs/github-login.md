# GitHub 个人登录

2026-09-13：用户确认使用 GitHub 登录，替代要求绑卡的 Cloudflare Access。仅允许 `xerifg` 对应的固定用户 ID `24712937`，不通过可修改的用户名判断权限。

## 配置

正式入口：`https://market-pilot-daily.market-pilot-daily.workers.dev`。

GitHub OAuth App 已注册为 MarketPilotDaily，设置入口为 `https://github.com/settings/applications/3855255`。Homepage 为正式入口，唯一 Redirect URI 为 `https://market-pilot-daily.market-pilot-daily.workers.dev/auth/callback`；不允许通配，不启用 Device Flow，保留用户 token 到期选项。

`APP_ORIGIN`、`GITHUB_CLIENT_ID`、`GITHUB_OWNER_ID` 是非敏感部署配置，保存在 `wrangler.jsonc`。`GITHUB_CLIENT_SECRET` 必须保存为本项目 Cloudflare Worker 的 Secret，不能提交到 Git、前端或日志。可以在 Worker Settings → Variables and Secrets 中选择 Secret 类型录入，也可以交互运行 `wrangler secret put GITHUB_CLIENT_SECRET`。

用户已完成 GitHub 身份验证，client secret 已保存为 Cloudflare 的 `GITHUB_CLIENT_SECRET` Secret（界面显示 Value encrypted，CLI 验证类型为 secret_text）。密钥没有写入本地文件或代码。当前部署版本为 `62699c6e-d40f-44df-9fb5-a9d6ca3c5c1e`，远端 `0004_github_auth.sql` 迁移已完成。

已在 Chrome 完成真实 GitHub 登录：授权页面仅显示 Public data only，回调成功后可读取云端持仓。匿名首页返回 200 登录页，匿名 `/api/portfolio` 返回 401。使用明确标注的 VOO.US 临时记录完成证券核验、新增 0.125 份、编辑为 0.25 份及删除，确认当前持仓为 0；数据库保留一条软删除测试记录，版本为 3，没有录入真实投资数据。最终退出返回登录页，数据库会话数量验证为 0。

联调中 Chrome 对整页表单退出出现 ERR_BLOCKED_BY_CLIENT。退出改用与持仓操作相同的同源请求，收到服务器 JSON 确认后才导航；加载最终构建后实际验证成功。未关闭浏览器安全设置。实际手机和其他网络访问尚待用户试用；日报与邮件仍未接通。

## 已实现流程

1. 匿名用户访问首页只看到登录页；API 和业务静态资源仍需要有效会话。
2. `/auth/github` 建立 10 分钟有效的随机 state，采用 PKCE S256；不申请 repo、user 或邮箱 scope，只查询 GitHub 公开身份。
3. state 与浏览器的 HttpOnly／Secure／SameSite=Lax Cookie 绑定，D1 保存 state 哈希与短期 verifier；回调必须同时匹配，并原子消费状态，阻止重放。
4. 服务端向 GitHub 换取 token，查询 `/user` 后核对固定 ID；遇到非空权限 scope、异常响应、错误账号或过期回调均拒绝。
5. token 仅在该请求内使用，不存数据库、不返回浏览器。网站使用独立的 256 位随机会话，D1 只存其 SHA-256 哈希、用户 ID 和过期时间。
6. 会话有效期 24 小时；退出必须为同源 POST，并删除服务器会话，旧 Cookie 随即失效。前端收到撤销成功的 JSON 确认后返回登录页，失败显示错误。重新登录替换当前浏览器的旧会话。
7. 每次发起登录清理过期状态和会话；最多保留 100 个有效待完成流程，超过时返回 429。无独立定时清理任务，过期行不会恢复有效。

所有请求只接受配置中的正式 HTTPS origin；预览入口禁用。访问保护直接在 Worker 内完成，无需开通 Cloudflare Zero Trust。普通业务写入保留 Origin 校验、JSON 校验和持仓版本检查。任务机器身份仍未实现，`/internal/` 不会因为 GitHub 登录而开放。

## 验证

`npm test` 的 29 项测试全部通过；退出流程调整后，8 项 OAuth 测试与生产构建再次通过。OAuth 测试使用模拟 GitHub 响应和真实内存 D1，覆盖本人／他人身份、PKCE、回调与 Cookie 不匹配、重复 Cookie、过期与重复回调、拒绝授权、上游故障、意外权限、会话过期、退出撤销及替代域名拒绝。不会调用真实邮箱或付费模型。

GitHub 登录和 GitHub Actions 执行日报是独立用途。用户在网页进行 GitHub 授权，不会因此向日报任务授予持仓修改权限，也不需要向登录应用授予仓库访问权。

参考：[GitHub OAuth 授权流程](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)、[OAuth scope 说明](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)。
