# 校园卡认证分析与实现

## 证据范围

基础认证分析依据为 `tests/cloud.tsinghua.edu.cn_2026_08_31_23_42_29.har` 中的 106 条请求；二次验证分析依据为 2026-09-01 新增的 TOTP、企业邮箱验证码和短信验证码完整 HAR，以及 `tests/Website` 保存的登录页、二次验证页和 JavaScript。以下 HAR 序号从 0 开始，不在文档或代码中记录真实凭证。分析过程没有重放 HAR 中的 Cookie、密码密文、验证码或 ticket。

## 认证链路

| HAR 序号 | 请求 | 作用 |
| --- | --- | --- |
| 1 | `GET https://card.tsinghua.edu.cn/userselftrade` | 返回 302，同时设置 `servicehall`。这时尚未登录，Cookie 仅标识匿名会话。 |
| 2 | `GET /getTYSFLoginUrlRedirect` | 返回 `http://id.tsinghua.edu.cn/do/off/ui/auth/login/form/<appid>/0`。程序将其升级到 HTTPS 后请求，不硬编码 appid。 |
| 3 | 统一身份认证登录页 | 设置身份域的 `JSESSIONID`，提供 `theform` 表单和 `sm2publicKey`。 |
| 40 | `POST /b/doubleAuth/personal/getFinger3` | 登录前返回 `result:error`、`object:null`。这是浏览器信任状态查询，不是取校园卡 Cookie 的接口。 |
| 47 | `POST /do/off/ui/auth/login/check` | 提交账号、SM2 密码密文和设备相关表单字段；成功响应为 HTML，不是 JSON。原始 `Set-Cookie` 中的 `TSINGHUAUSERID` 提供查询学号。 |
| 49 | 再次查询 `getFinger3` | 登录后返回浏览器信任标识，不等于 `servicehall`。 |
| 52 | `GET https://card.tsinghua.edu.cn/userindex?ticket=...` | 兑换一次性 ticket，将校园卡会话绑定到已认证身份。捕获中沿用原 `servicehall`，未再次设置该 Cookie。 |
| 78 | `GET /commontop` | 登录后的页面头部含非空 `topUsername`。实现将其与受保护查询页访问共同作为会话验证依据。 |

其余大部分请求是 JS、CSS、图片、校园卡页面数据和浏览器后台活动；清华云盘、Edge、ChatGPT 的请求不参与校园卡认证。这是学校统一身份认证的票据回跳流程，不能仅凭 `ticket` 字段将它等同于标准 CAS，也没有证据表明它是 OAuth token 或 JWT。

登录成功页面包含直接跳转链接及 `window.location.replace(...)`；只使用 `requests` 默认的 HTTP 重定向并不能完成这一步。实现只提取受信任校园卡域名上的 ticket 链接，不执行远程 JavaScript。

### 2026-09-01 修正

最初的代码只升级校园卡域名的 HTTP 跳转，漏掉了 HAR 第 2 条中的身份认证 HTTP 跳转，因此出现“认证流程要求 HTTPS 安全连接，已停止”。现在两个受信任的学校域名上默认端口或 `:80` 的 HTTP 地址都会先升级成 HTTPS；实际请求仍全部使用 HTTPS，陌生域名或非预期端口仍会被拒绝。当前抓包模式的证书校验行为见下文。

学号直接取自 `login/check` 响应头中的 `TSINGHUAUSERID`。HAR 中它附带 `Expires=Thu, 01-Jan-1970 00:00:10 GMT`，会被普通 CookieJar 当作过期 Cookie 丢弃。因此代码读取原始 `Set-Cookie`，并保留 HTTP 重定向过程中的登录响应；不会从账号文本或校园卡 HTML 猜测学号。兼容多个 `Set-Cookie` 响应头，也不会把 `Expires` 中的逗号误当作 Cookie 分隔符。

## 密码编码和表单

页面 `doLogin()` 读取当前 `#sm2publicKey`，调用 `sm2Util.doEncryptStr(password, publicKey)`，将结果写入隐藏字段 `name="i_pass"`。密码输入框自身没有 `name`，因此明文不参与表单提交。

保存的 `sm2Util` 包装函数为：

```javascript
return "04" + sm2.doEncrypt(password, publicKey, 1);
```

即 UTF-8 密码、SM2、`04 || C1 || C3 || C2` 的小写十六进制串。实现使用 `gmalg==1.1.1`，显式传入 `secrets.randbits` 提供安全随机数，每次登录读取新公钥，不复用抓包密文。测试使用公开标准向量，还用保存页面的 JS 解密 Python 生成的测试密文，确认跨语言格式和 Unicode 处理一致。[gmalg API](https://gmalg.readthedocs.io/zh-cn/latest/api/)

HAR 的 URL 编码请求体包含 `i_user`、`i_pass`、`singleLogin`、`fingerPrint`、`fingerGenPrint`、`fingerGenPrint3`、`deviceName`、`i_captcha`。首次登录时信任令牌为空；可信设备后续登录会把服务器返回的令牌放入 `fingerGenPrint`，同时复用同一随机指纹。部分 HAR 查看器省略空字段，需要解析 `postData.text` 才能看到完整结构。

代码保留当前表单的隐藏字段以兼容动态 token，但覆盖凭证和设备字段。它不复制保存页面里的浏览器指纹；首次登录为本次客户端创建随机指纹，设备名标明 `THUFood,Python`。二次验证完成后，只接受 `saveFinger` 返回的 32 位十六进制信任令牌；后续登录将令牌与原指纹配对提交。这不是浏览器 FingerprintJS 的完整模拟，学校仍可能要求额外验证；此时重新进入二次验证流程，不反复提交密码。

## 代码接口

```python
from getpass import getpass
from utils.auth import (
    SecondFactorChallenge,
    complete_second_factor,
    request_second_factor_code,
    start_login,
)

# 第一步只提交账号密码。
result = start_login(input("统一身份认证账号: "), getpass("密码: "))
if isinstance(result, SecondFactorChallenge):
    # 实际应用应让用户从 result.methods 中选择。
    method = input("验证方式（enterprise_email / sms / totp）: ").strip()
    verification = request_second_factor_code(result, method)
    result = complete_second_factor(verification, getpass("六位验证码: "))

# result.servicehall 和 result.idserial 只应在内存中使用，不要打印或写入文件。
```

`start_login`、`request_second_factor_code` 和 `complete_second_factor` 通过临时 `requests.Session` 状态串联两个域名；状态对象只保存继续流程所需的 Cookie 和设备信息。兼容接口 `login_with_password(..., totp_code=...)` 仍可用于无需交互选择的 TOTP 调用。HAR 中校园卡和身份认证域名的旧 HTTP 跳转会升级成 HTTPS；陌生域名、非预期端口、不符合预期的密码提交地址，以及要求重放密码的 307/308 跳转均被拒绝。当前抓包模式按下文说明关闭证书校验。

完成回跳后，再访问 `/userselftrade` 和 `/commontop` 验证认证状态，最后只提取能用于校园卡查询接口的 `servicehall`。不将登录前 Cookie 的存在或 HTTP 200 本身当作成功。查询参数 `idserial` 使用登录响应 `TSINGHUAUSERID` 的数字值；若该响应字段缺失或无效，则明确报错，不回退到账号或页面中的其他数字。`idserial` 是查询参数，不是另一种认证凭证。

## 企业邮箱、短信和 TOTP 二次验证

2026-09-01 的完整抓包与保存页面显示，密码验证返回二次验证页后，前端先探测可用方式，再按用户选择调用同一个 AJAX 接口 `POST /b/doubleAuth/login`：

| 步骤 | 表单 | 成功状态 |
| --- | --- | --- |
| 查询方式 | `action=FIND_APPROACHES` | `flow=LOOKED_FOR`；`hasWeChatBool`、`phone`、`hasTotp` 分别表示企业邮箱、短信和 TOTP 是否可用 |
| 选择企业邮箱 | `action=SEND_CODE&type=wechat` | `flow=SENT`、`sendType=wechat` |
| 选择短信 | `action=SEND_CODE&type=mobile` | `flow=SENT`、`sendType=mobile` |
| 选择 TOTP | `action=SEND_CODE&type=totp` | `flow=TOTPSENT`、`sendType=totp` |
| 校验企业邮箱或短信验证码 | `action=VERITY_CODE&vericode=<六位数字>` | `flow=VERIFIED`、`type=third` |
| 校验 TOTP | `action=VERITY_TOTP_CODE&vericode=<六位数字>` | `flow=VERIFIED`、`type=third` |
| 保存可信设备 | `POST /b/doubleAuth/personal/saveFinger`，`radioVal=是` | `result=success`，`object` 为 32 位信任令牌 |
| 生成 ticket | `GET /do/off/ui/auth/login/redirect2Jsp` | HTML 中包含校园卡 ticket |

实现仅在密码响应确实包含二次验证页面标记时运行这些步骤。验证码发送、校验和可信设备请求带 `Origin`、`Referer` 和 `X-Requested-With: XMLHttpRequest`，与浏览器记录一致。验证码必须是六位 ASCII 数字；错误、过期、所选方式不可用、流程状态不符或非预期回跳都会停止，不自动重试密码。

`TSINGHUAUSERID` 在普通密码成功时由 `login/check` 返回，在二次验证流程中改由验证码校验响应返回，代码兼容这两个来源。企业邮箱在服务端协议中仍使用历史字段名 `wechat`，界面按实际用途标为“企业邮箱验证码”。

两次抓包分别展示了不信任设备和信任设备：`radioVal=否` 时 `saveFinger` 返回空对象；`radioVal=是` 时返回 32 位令牌。实现总是请求“信任此设备”，并将原随机指纹与令牌通过 `keyring` 写入当前操作系统用户的安全凭据库。账号只以 SHA-256 摘要作为凭据条目标识，令牌不写入配置 JSON、消费记录或日志。再次输入同一账号密码时会读取并提交原指纹和令牌；若学校仍要求验证，则以服务器结果为准重新显示验证方式并更新令牌。

## Streamlit 行为和隐私

- 程序启动后默认选择“账号密码登录”；首屏只显示账号和密码，不显示学号或验证码输入框。密码响应要求二次验证后，界面才列出服务器返回的企业邮箱、短信和 TOTP 方式。
- 账号认证和消费查询成功后，界面自动切换到“手动输入 servicehall”，并回填认证响应中的学号与已经验证的 `servicehall`。
- 手动模式查询失败时明确提示 `servicehall` 可能失效，并建议切换到账号密码登录重新获取。
- 选择方式并提交后，企业邮箱或短信验证码才会由学校发送；TOTP 则进入动态码校验状态。下一屏只显示六位验证码输入框，不再显示账号或密码。
- 认证模式选择放在表单外，切换立即更新输入项；密码、验证码和手动 Cookie 均使用密码框，提交回调清空输入框，在当前脚本运行中使用一次。二次验证会话只保留 CookieJar、可用方式和随机指纹，不包含密码。该回调遵循 [Streamlit 表单执行顺序](https://docs.streamlit.io/develop/concepts/architecture/forms)。
- 图形验证码、扫码、改密或其他尚未适配的交互认证需在官网完成，再切换手动模式。程序不破解验证码、不绕过 MFA，也不自动重复登录。
- 只在本机或可信部署输入密码：运行 Streamlit 的服务器需要在内存中处理凭证；密码框不代表远端部署无法读取密码。Python 字符串不能保证底层内存安全擦除。
- 不记录或输出密码、Cookie、ticket、服务端原始错误正文，不把它们交给 AI。原有消费数据本地保存逻辑仍保留；打开 AI 开关本身不会发送数据，只有用户点击“开始生成”后才会发送用于评论的消费记录，因此仍建议本地部署。
- 捕获文件可能包含身份信息、会话 Cookie 和可重放密文，继续排除在版本控制之外。可提交的测试只含虚构账号、Cookie 和公开密码学测试向量。
- 消费记录保存在操作系统的稳定用户数据目录，AI 非敏感配置保存在稳定用户配置目录；API Key 和可信设备令牌保存在系统凭据库。程序不再把运行时数据写入源码目录、可执行文件旁或 PyInstaller 的临时解压目录，也不再读取 `.env`。

## TLS 校验

认证模块的临时 `Session` 和消费查询请求均启用 TLS 证书校验。URL 继续限制为预期学校域名和端口。若通过 HTTPS 调试代理分析网络流量，必须将代理 CA 安装到 Python 或系统信任链；证书无效时连接会安全失败，界面不再显示抓包模式警告。

当提交响应既没有登录表单，也没有可识别的校园卡 ticket 时，界面会报告 `login/check` 的状态码、内容类型和响应字节数。这些元数据不含 Cookie、ticket 或响应正文，可与代理中的完整响应对照。

## 验证与限制

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p "test_*.py" -v
streamlit run st.py
```

测试不连接学校服务器。保存的公共 SM2 JS 存在且 Node 可用时，会额外运行跨语言验证；全新检出中无该文件时跳过此项。回归测试覆盖 HAR 中的 HTTP 身份认证跳转、带过期日期的学号 Cookie、三种二次验证方式、可信设备保存与复用、重定向前的响应头保留、账号与学号不同，以及账号模式首屏不存在学号和验证码输入框。实际账号的登录成功率和设备风控仍需用户在自己的网络环境下验证，不能由抓包回放或匿名入口检查保证。
