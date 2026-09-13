# 163 日报邮箱配置

用户已确认同一 163 邮箱同时发件和收件。具体地址仅保存在仓库的 Actions Secrets，不写入公开代码、文档或日志。

## 凭证配置

在 [仓库 Actions Secrets](https://github.com/xerifg/MarketPilotDaily/settings/secrets/actions) 中配置：

| 名称 | 内容 | 当前状态 |
| --- | --- | --- |
| `SMTP_USERNAME` | 完整的 163 邮箱地址 | 已保存 |
| `MAIL_FROM` | 相同的完整邮箱地址 | 已保存 |
| `MAIL_TO` | 相同的完整邮箱地址 | 已保存 |
| `SMTP_AUTH_CODE` | 163 SMTP 客户端授权码，非网页登录密码 | 待用户录入 |
| `DEEPSEEK_API_KEY` | DeepSeek 平台 API Key | 待用户录入 |

授权码在 163 邮箱网页设置中的 POP3/SMTP/IMAP 服务相关页面申请；以账号实际显示为准。手机验证由用户完成。只在 GitHub 的 Secret 输入框填写，不发到聊天中。DeepSeek 密钥同样只填 Secret 输入框。邮箱地址和密钥均不配置为明文 Actions Variables。

## 已实现和验证的范围

`jobs/mail.py` 使用 Python 标准库 `SMTP_SSL`，连接 `smtp.163.com:465`，强制系统默认的证书和主机名验证。不会记录 SMTP 会话和上游错误正文。2026-09-13 本机真实 TLS 握手成功（TLS 1.3，EHLO 返回 250），未登录账号、未发邮件；这不代表 GitHub runner 的网络与账号授权已验证。

邮件采用纯文本和 HTML 两种正文，文本全部转义，信息来源只接受 HTTPS 链接。调用方只能传入经过隐私处理的邮件摘要，不能传入原始持仓；该传输模块不能自行判断自然语言中是否含有数量或成本。日报内容生成和隐私筛选尚待接入。

发送前必须由持久化网关原子领取报告并写入 `sending`。已领取的报告禁止自动重新发送。SMTP 结果区分：

- `failed_before_data`：正文提交前失败，例如认证失败或收件地址被拒绝。
- `smtp_rejected`：服务器明确拒绝正文。
- `delivery_uncertain`：正文提交期间连接断开或超时，不能确定服务器是否已接收。
- `smtp_accepted`：服务器已接受正文；不等于已进收件箱或已读。

任何结果都不在传输函数内自动重试；最终状态写入失败则保留原先的 `sending`，后续需核查。固定 Message-ID 仅用于追踪，不能替代服务端防重复机制。

10 项邮件测试已通过，覆盖 TLS 配置、领取失败禁止联网、重复领取、认证与收件拒绝、DATA 拒绝和超时、关闭连接失败、状态写入失败、HTML 与邮件头注入。另有 7 项 DeepSeek 测试通过，全部使用模拟数据，不发送邮件、不调用付费模型。

**尚未完成**：持久化发送网关、行情新闻与报告流水线、GitHub 定时工作流、真实 SMTP 与 AI 联调。当前每日推送没有启用，不能仅凭 Secrets 存在视为接通。

参考：[Python 官方 SMTP 文档](https://docs.python.org/3/library/smtplib.html)。
