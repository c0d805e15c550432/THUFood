"""Tsinghua identity login and campus-card servicehall exchange.

No HAR cookies, tickets, passwords, or public keys are embedded here. Each
attempt starts a new session and reads the current identity form over HTTPS.
"""

from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from http.cookies import CookieError, SimpleCookie
import re
import secrets
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

import requests


CARD_ORIGIN = "https://card.tsinghua.edu.cn"
ID_ORIGIN = "https://id.tsinghua.edu.cn"
TRADE_PAGE = CARD_ORIGIN + "/userselftrade"
LOGIN_CHECK = ID_ORIGIN + "/do/off/ui/auth/login/check"
DOUBLE_AUTH_LOGIN = ID_ORIGIN + "/b/doubleAuth/login"
SAVE_TRUSTED_DEVICE = ID_ORIGIN + "/b/doubleAuth/personal/saveFinger"
DOUBLE_AUTH_REDIRECT = ID_ORIGIN + "/do/off/ui/auth/login/redirect2Jsp"
MANUAL_LOGIN_HINT = "请在校园卡官网完成登录或验证后，切换为手动输入 servicehall。"


class AuthenticationError(ValueError):
    """A safe, user-facing authentication failure (never includes server text)."""


class AdditionalVerificationRequired(AuthenticationError):
    """The identity provider requires interactive verification."""


@dataclass(frozen=True)
class TrustedDevice:
    fingerprint: str = field(repr=False)
    token: str = field(repr=False)
    device_name: str = "THUFood,Python"


@dataclass(frozen=True)
class LoginResult:
    servicehall: str = field(repr=False)
    idserial: str = ""
    trusted_device: object = field(default=None, repr=False)
    trust_saved: bool = False


@dataclass(frozen=True)
class SecondFactorChallenge:
    cookies: object = field(repr=False)
    methods: tuple
    challenge_url: str
    fingerprint: str = field(repr=False)
    device_name: str = "THUFood,Python"
    idserial: str = field(default="", repr=False)


@dataclass(frozen=True)
class SecondFactorVerification:
    cookies: object = field(repr=False)
    method: str
    challenge_url: str
    fingerprint: str = field(repr=False)
    device_name: str = "THUFood,Python"
    idserial: str = field(default="", repr=False)


class _Page(HTMLParser):
    """Read form metadata without running scripts from the remote page."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.action = None
        self.hidden = {}
        self.inputs = {}
        self.text_by_id = {}
        self.links = []
        self.captcha_required = False
        self._form = False
        self._capture = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        element_id = attrs.get("id", "")
        if tag == "form":
            self._form = element_id == "theform"
            if self._form:
                self.action = attrs.get("action", "")
        if tag == "input":
            name = attrs.get("name", element_id)
            value = attrs.get("value", "")
            self.inputs[element_id or name] = value
            if self._form and attrs.get("type", "").lower() == "hidden" and name:
                self.hidden[name] = value
        if element_id in ("sm2publicKey", "msg_note"):
            self._capture = (tag, element_id)
            self.text_by_id[element_id] = ""
        if element_id == "c_code":
            style = re.sub(r"\s+", "", attrs.get("style", "")).lower()
            self.captcha_required = not (
                "hidden" in attrs.get("class", "").split()
                or "display:none" in style or "hidden" in attrs
            )
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = False
        if self._capture and self._capture[0] == tag:
            self._capture = None

    def handle_data(self, data):
        if self._capture:
            self.text_by_id[self._capture[1]] += data


def _safe_url(url, base=TRADE_PAGE):
    """Upgrade legacy HTTP redirects on both official hosts before sending."""
    parts = urlsplit(urljoin(base, unescape(url)))
    try:
        port = parts.port
    except ValueError:
        raise AuthenticationError("登录重定向地址异常，已停止发送认证信息。") from None
    if parts.username or parts.password or parts.hostname not in (
        "card.tsinghua.edu.cn", "id.tsinghua.edu.cn"
    ):
        raise AuthenticationError("登录重定向离开了受信任的学校域名，已停止。")
    if parts.scheme == "http" and port in (None, 80):
        return urlunsplit(("https", parts.hostname, parts.path, parts.query, ""))
    if parts.scheme != "https" or port not in (None, 443):
        raise AuthenticationError("认证流程要求 HTTPS 安全连接，已停止。")
    return urlunsplit(("https", parts.hostname, parts.path, parts.query, ""))


def _request(session, method, url, *, data=None, timeout=30, referer=None):
    history = []
    for _ in range(12):
        url = _safe_url(url)
        if method == "POST" and url not in (LOGIN_CHECK, DOUBLE_AUTH_LOGIN, SAVE_TRUSTED_DEVICE):
            raise AuthenticationError("登录表单提交地址异常，已停止发送密码。")
        headers = {"Referer": referer} if referer else {}
        if method == "POST":
            headers["Origin"] = ID_ORIGIN
        if method == "POST" and url in (DOUBLE_AUTH_LOGIN, SAVE_TRUSTED_DEVICE):
            headers["X-Requested-With"] = "XMLHttpRequest"
        response = session.request(
            method, url, data=data, headers=headers, timeout=timeout,
            allow_redirects=False,
        )
        if response.status_code in (301, 302, 303, 307, 308):
            target = response.headers.get("Location")
            if not target:
                raise AuthenticationError("登录服务器返回了无效的重定向。")
            target = _safe_url(target, url)
            # Never automatically replay a credential-bearing POST.
            if method == "POST" and response.status_code in (307, 308):
                raise AuthenticationError("认证服务器要求重新提交密码，请使用手动登录。")
            history.append(response)
            referer = url.split("?", 1)[0]  # tickets must not leak into Referer
            url, method, data = target, "GET", None
            continue
        if response.status_code in (401, 403):
            raise AuthenticationError("学校服务器拒绝了本次认证。" + MANUAL_LOGIN_HINT)
        if response.status_code == 429:
            raise AuthenticationError("登录尝试过于频繁，请稍后再试，避免连续重试。")
        response.raise_for_status()
        response.encoding = "utf-8"
        response.history = history
        return response
    raise AuthenticationError("登录重定向次数过多。" + MANUAL_LOGIN_HINT)


def encrypt_password(password, public_key):
    """Match sm2Util.doEncryptStr: lowercase hex, 04 || C1 || C3 || C2."""
    if not password:
        raise AuthenticationError("请输入统一身份认证密码。")
    if not re.fullmatch(r"04[0-9a-fA-F]{128}", public_key):
        raise AuthenticationError("登录页未提供有效的 SM2 公钥，无法安全提交密码。")
    try:
        from gmalg import SM2
    except ImportError:
        raise AuthenticationError(
            "账号密码登录需要 gmalg，请先安装 requirements.txt 中的依赖；手动模式仍可使用。"
        ) from None
    try:
        key = bytes.fromhex(public_key)
        cipher = SM2(pk=key, rnd_fn=secrets.randbits)
        if not cipher.verify_pk(key):
            raise ValueError("Invalid SM2 point")
        return cipher.encrypt(password.encode("utf-8")).hex()
    except (ValueError, OverflowError):
        raise AuthenticationError("登录页的 SM2 公钥无效，已停止发送密码。") from None


def _ticket_link(response):
    page = _Page(response.text)
    candidates = list(page.links)
    # The recorded success page uses an anchor and location.replace, not 302.
    candidates.extend(re.findall(
        r"(?:window\.)?location\.replace\(\s*['\"]([^'\"]+)['\"]\s*\)",
        response.text,
    ))
    # Accept equivalent location.href/window.location/meta-refresh renderings,
    # while still validating the final host, path, and required ticket below.
    candidates.extend(re.findall(
        r"(?:https?:)?//card\.tsinghua\.edu\.cn(?::(?:80|443))?/userindex\?[^'\"<>\\\s]+|"
        r"/userindex\?[^'\"<>\\\s]+",
        response.text,
        re.I,
    ))
    for candidate in candidates:
        candidate = unescape(candidate).replace(r"\/", "/")
        parts = urlsplit(urljoin(response.url, candidate))
        if parts.hostname == "card.tsinghua.edu.cn" and parse_qs(parts.query).get("ticket"):
            return _safe_url(candidate, response.url)
    return None


def _raise_login_failure(page, response=None):
    # Ignore hidden default error labels: the saved page contains one even
    # before a login attempt. Only inspect it after a failed submission.
    if page.captcha_required:
        raise AdditionalVerificationRequired("学校要求图形验证码。" + MANUAL_LOGIN_HINT)
    if page.action is not None:
        raise AuthenticationError("登录未成功，请检查账号密码；不要连续重试。" + MANUAL_LOGIN_HINT)
    if response is not None:
        content_type = response.headers.get("Content-Type", "unknown").split(";", 1)[0]
        raise AdditionalVerificationRequired(
            "登录提交后未获得校园卡 ticket"
            f"（HTTP {response.status_code}，{content_type}，响应 {len(response.content)} 字节）。"
            "请在 HTTP 代理中检查 login/check 响应；它可能是二次认证、扫码或改密页面。"
        )
    raise AdditionalVerificationRequired(
        "认证未完成，可能需要二次认证、扫码或修改密码。" + MANUAL_LOGIN_HINT
    )


def _is_double_auth_page(response):
    return (
        urlsplit(response.url).hostname == "id.tsinghua.edu.cn"
        and any(marker in response.text for marker in (
            "doubleAuth.bundle.js", "doubleAuth.bundle.js.download",
            "/b/doubleAuth/login",
        ))
    )


def _double_auth_json(response, expected_flow):
    try:
        payload = response.json()
    except ValueError:
        raise AdditionalVerificationRequired(
            "二次验证接口没有返回 JSON，请在代理中检查 /b/doubleAuth/login 响应。"
        ) from None
    if not isinstance(payload, dict) or payload.get("result") != "success":
        raise AdditionalVerificationRequired(
            "二次验证码错误或已过期，请重新输入；若仍失败，请重新开始账号登录并获取新验证码。"
        )
    obj = payload.get("object")
    if not isinstance(obj, dict) or obj.get("flow") != expected_flow:
        raise AdditionalVerificationRequired(
            f"二次验证流程状态异常（期望 {expected_flow}），请在代理中检查响应。"
        )
    return obj


def _find_second_factor_methods(session, challenge, timeout):
    response = _request(
        session, "POST", DOUBLE_AUTH_LOGIN,
        data={"action": "FIND_APPROACHES"}, timeout=timeout,
        referer=challenge.url,
    )
    approaches = _double_auth_json(response, "LOOKED_FOR")
    methods = []
    if approaches.get("hasWeChatBool") is True:
        methods.append("enterprise_email")
    if isinstance(approaches.get("phone"), str) and approaches["phone"]:
        methods.append("sms")
    if approaches.get("hasTotp") is True:
        methods.append("totp")
    if not methods:
        raise AdditionalVerificationRequired("账号需要二次验证，但服务器没有返回受支持的验证方式。" + MANUAL_LOGIN_HINT)
    return tuple(methods)


def _send_second_factor_code(session, challenge, method, timeout):
    method_types = {"enterprise_email": "wechat", "sms": "mobile", "totp": "totp"}
    if method not in method_types or method not in challenge.methods:
        raise AdditionalVerificationRequired("所选二次验证方式不可用，请重新开始登录。")

    response = _request(
        session, "POST", DOUBLE_AUTH_LOGIN,
        data={"action": "SEND_CODE", "type": method_types[method]},
        timeout=timeout, referer=challenge.challenge_url,
    )
    expected_flow = "TOTPSENT" if method == "totp" else "SENT"
    sent = _double_auth_json(response, expected_flow)
    if sent.get("sendType") != method_types[method]:
        raise AdditionalVerificationRequired("学校服务器没有进入所选验证方式，请获取新验证码后重试。")


def _verify_second_factor(session, verification, code, timeout):
    if not isinstance(verification, SecondFactorVerification):
        raise AuthenticationError("二次验证会话无效，请重新输入账号密码。")
    if not re.fullmatch(r"[0-9]{6}", code or ""):
        raise AdditionalVerificationRequired("请输入当前六位二次验证码。")

    verified = _request(
        session, "POST", DOUBLE_AUTH_LOGIN,
        data={
            "action": "VERITY_TOTP_CODE" if verification.method == "totp" else "VERITY_CODE",
            "vericode": code,
        },
        timeout=timeout, referer=verification.challenge_url,
    )
    result = _double_auth_json(verified, "VERIFIED")
    if result.get("type") != "third":
        raise AdditionalVerificationRequired("学校返回了暂不支持的二次验证回跳类型。" + MANUAL_LOGIN_HINT)
    redirect = _safe_url(result.get("redirectUrl", ""), verification.challenge_url)
    if redirect != DOUBLE_AUTH_REDIRECT:
        raise AuthenticationError("二次验证返回了异常的回跳地址，已停止。")
    return verified, redirect


def _save_trusted_device(session, verification, timeout):
    try:
        response = _request(
            session, "POST", SAVE_TRUSTED_DEVICE,
            data={
                "fingerprint": verification.fingerprint,
                "deviceName": verification.device_name,
                "radioVal": "是",
                "singleLogin": "",
            },
            timeout=timeout, referer=verification.challenge_url,
        )
    except (AuthenticationError, requests.RequestException):
        # Verification has already succeeded. A failed optional trust write
        # must not discard the one-time authenticated session.
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    token = payload.get("object") if isinstance(payload, dict) and payload.get("result") == "success" else None
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", token):
        return None
    return TrustedDevice(verification.fingerprint, token, verification.device_name)


def _student_id_from_login(response):
    """Read TSINGHUAUSERID from login/check, including an expired Set-Cookie.

    The captured header has a 1970 Expires date, so RequestsCookieJar drops it.
    Read raw response headers instead, retaining the POST response on a 302.
    Never infer the student ID from the username or the campus-card HTML.
    """
    for reply in [*response.history, response]:
        if reply.url not in (LOGIN_CHECK, DOUBLE_AUTH_LOGIN):
            continue
        raw_headers = getattr(reply.raw, "headers", None)
        if raw_headers is not None and hasattr(raw_headers, "getlist"):
            headers = raw_headers.getlist("Set-Cookie")
        elif raw_headers is not None and hasattr(raw_headers, "get_all"):
            headers = raw_headers.get_all("Set-Cookie") or []
        else:
            # Some adapters combine duplicate headers. Split cookie boundaries,
            # but preserve the comma in Expires=Thu, 01-Jan-1970 ... .
            headers = re.split(r",(?=\s*[^;,=\s]+=)", reply.headers.get("Set-Cookie", ""))
        for header in headers:
            cookies = SimpleCookie()
            try:
                cookies.load(header)
            except CookieError:
                continue
            if "TSINGHUAUSERID" in cookies:
                value = unquote(cookies["TSINGHUAUSERID"].value)
                if re.fullmatch(r"[0-9]+", value):
                    return value
    return ""


def _new_session(cookies=None):
    session = requests.Session()
    session.verify = True
    session.headers.update({"User-Agent": "THUFood/1.0 (Python requests)"})
    if cookies is not None:
        session.cookies.update(cookies)
    return session


def _finish_campus_login(session, response, student_id, timeout, trusted_device=None):
    ticket = _ticket_link(response)
    if ticket:
        response = _request(session, "GET", ticket, timeout=timeout)
    if urlsplit(response.url).hostname != "card.tsinghua.edu.cn":
        _raise_login_failure(_Page(response.text), response)

    trade = _request(session, "GET", TRADE_PAGE, timeout=timeout)
    if _safe_url(trade.url) != TRADE_PAGE or _Page(trade.text).action is not None:
        raise AuthenticationError("校园卡会话尚未认证或已过期。" + MANUAL_LOGIN_HINT)
    top = _request(session, "GET", CARD_ORIGIN + "/commontop", timeout=timeout)
    if urlsplit(top.url).hostname != "card.tsinghua.edu.cn" or not _Page(top.text).inputs.get("topUsername", "").strip():
        raise AuthenticationError("无法确认校园卡登录状态。" + MANUAL_LOGIN_HINT)
    prepared = requests.Request("GET", CARD_ORIGIN + "/business/querySelfTradeList").prepare()
    cookie = SimpleCookie()
    cookie.load(requests.cookies.get_cookie_header(session.cookies, prepared) or "")
    if "servicehall" not in cookie or not cookie["servicehall"].value:
        raise AuthenticationError("校园卡服务器没有返回可用于查询的 servicehall。")
    if not student_id:
        raise AuthenticationError(
            "认证响应未返回有效的 TSINGHUAUSERID 学号，无法查询。请重新登录，或切换手动输入 servicehall。"
        )
    return LoginResult(
        cookie["servicehall"].value, student_id,
        trusted_device=trusted_device,
        trust_saved=trusted_device is not None,
    )


def start_login(username, password, *, trusted_device=None, timeout=30):
    """Submit only username/password; return LoginResult or a second-factor challenge."""
    username = username.strip()
    if not username or not password:
        raise AuthenticationError("请填写统一身份认证账号和密码。")
    try:
        with _new_session() as session:
            page_response = _request(session, "GET", TRADE_PAGE, timeout=timeout)
            page = _Page(page_response.text)
            if page.captcha_required:
                _raise_login_failure(page)
            if page.action is None or urlsplit(page_response.url).hostname != "id.tsinghua.edu.cn":
                _raise_login_failure(page)
            action = _safe_url(page.action, page_response.url)
            if action != LOGIN_CHECK:
                raise AuthenticationError("统一身份认证表单地址发生变化，已停止发送密码。")
            encrypted = encrypt_password(password, page.text_by_id.get("sm2publicKey", "").strip())
            fields = dict(page.hidden)
            if isinstance(trusted_device, TrustedDevice):
                fingerprint = trusted_device.fingerprint
                finger_token = trusted_device.token
                device_name = trusted_device.device_name
            else:
                fingerprint = secrets.token_hex(16)
                finger_token = ""
                device_name = "THUFood,Python"
            fields.pop("singleLogin", None)
            fields.update({
                "i_user": username, "i_pass": encrypted,
                "fingerPrint": fingerprint,
                "fingerGenPrint": finger_token, "fingerGenPrint3": "",
                "deviceName": device_name, "i_captcha": "",
            })
            response = _request(session, "POST", action, data=fields,
                                timeout=timeout, referer=page_response.url)
            student_id = _student_id_from_login(response)
            if _ticket_link(response) or urlsplit(response.url).hostname == "card.tsinghua.edu.cn":
                return _finish_campus_login(
                    session, response, student_id, timeout,
                    trusted_device=trusted_device if isinstance(trusted_device, TrustedDevice) else None,
                )
            if _is_double_auth_page(response):
                methods = _find_second_factor_methods(session, response, timeout)
                return SecondFactorChallenge(
                    session.cookies.copy(), methods, response.url,
                    fingerprint, device_name, student_id,
                )
            _raise_login_failure(_Page(response.text), response)
    except requests.Timeout:
        raise AuthenticationError("连接学校认证服务器超时，请检查校园网或 VPN 后重试。") from None
    except requests.RequestException:
        raise AuthenticationError("无法连接学校认证服务器，请检查网络、校园网、VPN 或 HTTP 代理设置。") from None


def request_second_factor_code(challenge, method, *, timeout=30):
    """Select one offered method and request/prepare its verification code."""
    if not isinstance(challenge, SecondFactorChallenge):
        raise AuthenticationError("二次验证会话无效，请重新输入账号密码。")
    try:
        with _new_session(challenge.cookies) as session:
            _send_second_factor_code(session, challenge, method, timeout)
            return SecondFactorVerification(
                session.cookies.copy(), method, challenge.challenge_url,
                challenge.fingerprint, challenge.device_name, challenge.idserial,
            )
    except requests.Timeout:
        raise AuthenticationError("请求二次验证码超时，请稍后重试。") from None
    except requests.RequestException:
        raise AuthenticationError("无法连接二次验证服务器，请检查网络或 HTTP 代理设置。") from None


def complete_second_factor(verification, code, *, timeout=30):
    """Verify a requested code, trust the device, and finish campus-card login."""
    if not isinstance(verification, SecondFactorVerification):
        raise AuthenticationError("二次验证会话无效，请重新输入账号密码。")
    try:
        with _new_session(verification.cookies) as session:
            verified, redirect = _verify_second_factor(
                session, verification, code.strip(), timeout,
            )
            # login/check is the primary source. Some MFA responses repeat the
            # expired Set-Cookie, but completion must not depend on repetition.
            student_id = verification.idserial or _student_id_from_login(verified)
            trusted_device = _save_trusted_device(session, verification, timeout)
            ticket_page = _request(
                session, "GET", redirect, timeout=timeout,
                referer=verification.challenge_url,
            )
            return _finish_campus_login(
                session, ticket_page, student_id, timeout,
                trusted_device=trusted_device,
            )
    except requests.Timeout:
        raise AuthenticationError("二次验证连接超时，请获取新验证码后重试。") from None
    except requests.RequestException:
        raise AuthenticationError("无法连接二次验证服务器，请检查网络或 HTTP 代理设置。") from None


def login_with_password(username, password, *, totp_code="", timeout=30):
    """Compatibility helper for non-interactive direct or TOTP login."""
    result = start_login(username, password, timeout=timeout)
    if isinstance(result, SecondFactorChallenge):
        if "totp" not in result.methods:
            raise AdditionalVerificationRequired("账号未提供 TOTP 验证方式，请使用分阶段登录界面。")
        verification = request_second_factor_code(result, "totp", timeout=timeout)
        return complete_second_factor(verification, totp_code, timeout=timeout)
    return result


def get_servicehall(username, password, *, totp_code="", timeout=30):
    """Convenience API for callers that only need the servicehall value."""
    return login_with_password(
        username, password, totp_code=totp_code, timeout=timeout,
    ).servicehall
