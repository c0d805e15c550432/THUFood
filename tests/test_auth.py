"""Offline authentication tests; all credentials and cookies are synthetic."""

from pathlib import Path
import json
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gmalg
import requests
from urllib3._collections import HTTPHeaderDict

from utils.auth import (
    AdditionalVerificationRequired,
    AuthenticationError,
    CARD_ORIGIN,
    DOUBLE_AUTH_LOGIN,
    DOUBLE_AUTH_REDIRECT,
    ID_ORIGIN,
    LOGIN_CHECK,
    SAVE_TRUSTED_DEVICE,
    SecondFactorChallenge,
    SecondFactorVerification,
    TRADE_PAGE,
    _safe_url,
    _student_id_from_login,
    encrypt_password,
    complete_second_factor,
    get_servicehall,
    login_with_password,
    request_second_factor_code,
    start_login,
)


# Public SM2 standard test vector, unrelated to any school account.
PRIVATE_KEY = "3945208f7b2144b13f36e38ac6d39f95889393692860b51a42fb81ef4df7c5b8"
PUBLIC_KEY = (
    "0409f9df311e5421a150dd7d161e4bc5c672179fad1833fc076bb08ff356f35020"
    "ccea490ce26775a52dc6ea718cc1aa600aed05fbf35e084a6632f6072da9ad13"
)
FORM_URL = ID_ORIGIN + "/do/off/ui/auth/login/form/current-app/0"
FORM = f"""
<p id="c_note" style="display:none"><span id="msg_note">用户名或密码不正确</span></p>
<form id="theform" action="/do/off/ui/auth/login/check" method="post">
<input name="csrf" type="hidden" value="synthetic-csrf">
<input name="i_user"><input name="i_pass" type="hidden">
<input name="singleLogin" type="checkbox" checked>
<input name="fingerPrint" type="hidden" value="never-copy-old-browser">
<div id="sm2publicKey">{PUBLIC_KEY}</div>
<div id="c_code" class="form-group hidden"><input name="i_captcha"></div>
</form>
"""
CALLBACK = CARD_ORIGIN + "/userindex?ticket=synthetic-ticket&test=1"
SUCCESS = f'<a href="{CALLBACK.replace("&", "&amp;")}">直接跳转</a>'
TRADE = '<input id="idserial" type="hidden" value="2025999999">'
TOP = '<input id="topUsername" value="Synthetic User" type="hidden">'
LOGIN_SET_COOKIE = "TSINGHUAUSERID=2025000000; Expires=Thu, 01-Jan-1970 00:00:10 GMT; Path=/"
SECONDARY = '<html><script src="/v2/dist/doubleAuth.bundle.js"></script></html>'


def response(url, body="", status=200, location=None, set_cookie=None):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = body.encode("utf-8")
    result.encoding = "utf-8"
    if location:
        result.headers["Location"] = location
    if set_cookie:
        result.headers["Set-Cookie"] = set_cookie
    return result


def json_response(url, payload, set_cookie=None):
    result = response(url, json.dumps(payload), set_cookie=set_cookie)
    result.headers["Content-Type"] = "application/json"
    return result


class FakeSession:
    def __init__(self, replies, rotate_cookie=True, cookie_domain="card.tsinghua.edu.cn"):
        self.replies = iter(replies)
        self.cookies = requests.cookies.RequestsCookieJar()
        self.headers = {}
        self.calls = []
        self.rotate_cookie = rotate_cookie
        self.cookie_domain = cookie_domain

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        expected_method, result = reply
        if (method, url) != (expected_method, result.url):
            raise AssertionError("Unexpected authentication request sequence")
        if kwargs.get("allow_redirects") is not False:
            raise AssertionError("Redirects must be checked before following")
        if len(self.calls) == 1:
            self.cookies.set("servicehall", "synthetic-anonymous-cookie", domain=self.cookie_domain, path="/")
        if url == CALLBACK and self.rotate_cookie:
            self.cookies.set("servicehall", "synthetic-authenticated-cookie", domain=self.cookie_domain, path="/")
        return result


def flow(login_body=SUCCESS, form=FORM, trade=TRADE, top=TOP, identity_cookie=LOGIN_SET_COOKIE):
    return [
        ("GET", response(TRADE_PAGE, status=302, location="http://card.tsinghua.edu.cn:80/getTYSFLoginUrlRedirect")),
        ("GET", response(CARD_ORIGIN + "/getTYSFLoginUrlRedirect", status=302, location=FORM_URL.replace("https://", "http://"))),
        ("GET", response(FORM_URL, form)),
        ("POST", response(LOGIN_CHECK, login_body, set_cookie=identity_cookie)),
        ("GET", response(CALLBACK, "校园卡")),
        ("GET", response(TRADE_PAGE, trade)),
        ("GET", response(CARD_ORIGIN + "/commontop", top)),
    ]


def second_factor_flow(method="totp", code_result="success", *, has_totp=True, redirect_url="/do/off/ui/auth/login/redirect2Jsp"):
    method_type = {"enterprise_email": "wechat", "sms": "mobile", "totp": "totp"}[method]
    replies = flow(login_body=SECONDARY, identity_cookie=None)[:4]
    replies.extend([
        ("POST", json_response(DOUBLE_AUTH_LOGIN, {
            "result": "success", "msg": "", "object": {
                "flow": "LOOKED_FOR", "hasTotp": has_totp,
                "hasWeChatBool": True, "phone": "synthetic-phone",
            },
        })),
        ("POST", json_response(DOUBLE_AUTH_LOGIN, {
            "result": "success", "msg": "", "object": {
                "flow": "TOTPSENT" if method == "totp" else "SENT",
                "sendType": method_type,
            },
        })),
        ("POST", json_response(DOUBLE_AUTH_LOGIN, {
            "result": code_result, "msg": "synthetic-private-error", "object": {
                "flow": "VERIFIED", "type": "third", "redirectUrl": redirect_url,
            },
        }, set_cookie=LOGIN_SET_COOKIE if code_result == "success" else None)),
        ("POST", json_response(SAVE_TRUSTED_DEVICE, {
            "result": "success", "msg": "", "object": "abcdef0123456789abcdef0123456789",
        })),
        ("GET", response(DOUBLE_AUTH_REDIRECT, SUCCESS)),
        ("GET", response(CALLBACK, "校园卡")),
        ("GET", response(TRADE_PAGE, TRADE)),
        ("GET", response(CARD_ORIGIN + "/commontop", TOP)),
    ])
    return replies


def totp_flow(code_result="success", **kwargs):
    return second_factor_flow("totp", code_result, **kwargs)


class EncryptionTests(unittest.TestCase):
    def test_standard_ciphertext_vector_includes_point_prefix_and_c1c3c2(self):
        k = int("59276e27d506861a16680f3ad9c02dccef3cc1fa3cdbe4ce6d54b80deac1bc21", 16)
        with patch("utils.auth.secrets.randbits", return_value=k):
            cipher = encrypt_password("encryption standard", PUBLIC_KEY)
        self.assertEqual(cipher, (
            "0404ebfc718e8d1798620432268e77feb6415e2ede0e073c0f4f640ecd2e149a73"
            "e858f9d81e5430a57b36daab8f950a3c64e6ee6a63094d99283aff767e124df0"
            "59983c18f809e262923c53aec295d30383b54e39d609d160afcb1908d0bd8766"
            "21886ca989ca9c7d58087307ca93092d651efa"
        ))

    def test_unicode_whitespace_and_secure_randomness(self):
        password = "  测试-Pässword-🍜  "
        first = encrypt_password(password, PUBLIC_KEY)
        second = encrypt_password(password, PUBLIC_KEY)
        self.assertNotEqual(first, second)
        plain = gmalg.SM2(bytes.fromhex(PRIVATE_KEY)).decrypt(bytes.fromhex(first))
        self.assertEqual(plain.decode("utf-8"), password)

    def test_invalid_keys_never_allow_plaintext_fallback(self):
        for key in ("", "04abcd", "04" + "0" * 128):
            with self.subTest(key=key), self.assertRaises(AuthenticationError):
                encrypt_password("synthetic-password", key)

    def test_saved_website_js_can_decrypt_python_output(self):
        files = list(Path("tests/Website").rglob("sm2Util.js*"))
        node = shutil.which("node")
        if not files or not node:
            self.skipTest("Optional saved public JS and Node are not available")
        # Run only the public cryptographic bundle with synthetic input in a
        # restricted VM. Never execute the saved login page or read HAR secrets.
        script = """
const fs = require('fs'), vm = require('vm');
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = {Uint8Array, Uint32Array, Array, navigator: {appName: 'Netscape'}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(data.path, 'utf8'), context, {timeout: 5000});
context.cipher = data.cipher; context.key = data.key;
process.stdout.write(vm.runInContext('sm2Util.doDecryptStr(cipher, key)', context, {timeout: 5000}));
"""
        plain = "  synthetic-测试-password  "
        result = subprocess.run(
            [node, "-e", script], input=json.dumps({
                "path": str(files[0].resolve()), "cipher": encrypt_password(plain, PUBLIC_KEY), "key": PRIVATE_KEY,
            }), capture_output=True, text=True, encoding="utf-8", timeout=15, check=True,
        )
        self.assertEqual(result.stdout, plain)


class LoginTests(unittest.TestCase):
    def login(self, replies, username="synthetic-account", totp_code=""):
        session = FakeSession(replies)
        with patch("utils.auth.requests.Session", return_value=session):
            result = login_with_password(username, " synthetic-password ", totp_code=totp_code)
        return result, session

    def test_full_flow_verifies_cookie_and_preserves_form_tokens(self):
        result, session = self.login(flow())
        self.assertEqual(result.servicehall, "synthetic-authenticated-cookie")
        self.assertEqual(result.idserial, "2025000000")
        self.assertIsNone(session.cookies.get("TSINGHUAUSERID"))
        self.assertNotIn(result.servicehall, repr(result))
        post = session.calls[3][2]
        fields = post["data"]
        self.assertEqual(fields["csrf"], "synthetic-csrf")
        self.assertNotIn("singleLogin", fields)
        self.assertEqual(fields["fingerGenPrint"], "")
        self.assertEqual(fields["fingerGenPrint3"], "")
        self.assertNotEqual(fields["fingerPrint"], "never-copy-old-browser")
        decrypted = gmalg.SM2(bytes.fromhex(PRIVATE_KEY)).decrypt(bytes.fromhex(fields["i_pass"]))
        self.assertEqual(decrypted, b" synthetic-password ")
        self.assertEqual(post["headers"]["Origin"], ID_ORIGIN)
        self.assertTrue(all(c[1].startswith("https://") for c in session.calls))
        self.assertTrue(session.verify)

    def test_javascript_ticket_callback(self):
        result, _ = self.login(flow(login_body=f'<script>window.location.replace("{CALLBACK}");</script>'))
        self.assertTrue(result.servicehall)

    def test_equivalent_ticket_renderings(self):
        variants = (
            f'<script>window.location.href = "{CALLBACK}";</script>',
            f'<script>window.location = "{CALLBACK}";</script>',
            f'<meta http-equiv="refresh" content="0; url={CALLBACK.replace("&", "&amp;")}">',
        )
        for body in variants:
            with self.subTest(body=body[:30]):
                result, _ = self.login(flow(login_body=body))
                self.assertEqual(result.idserial, "2025000000")

    def test_unknown_login_response_reports_safe_proxy_metadata(self):
        private_body = "<div>synthetic-private-response-body</div>"
        with self.assertRaises(AdditionalVerificationRequired) as error:
            self.login(flow(login_body=private_body))
        message = str(error.exception)
        self.assertIn("login/check", message)
        self.assertIn(str(len(private_body.encode('utf-8'))), message)
        self.assertNotIn("synthetic-private", message)

    def test_http_ticket_callback(self):
        replies = flow()
        replies[3] = ("POST", response(LOGIN_CHECK, status=302, location=CALLBACK, set_cookie=LOGIN_SET_COOKIE))
        result, _ = self.login(replies)
        self.assertTrue(result.servicehall)
        self.assertEqual(result.idserial, "2025000000")

    def test_full_totp_flow_uses_recorded_actions_and_returns_ticket(self):
        result, session = self.login(totp_flow(), totp_code="123456")
        self.assertEqual(result.servicehall, "synthetic-authenticated-cookie")
        self.assertEqual(result.idserial, "2025000000")
        self.assertEqual(session.calls[4][2]["data"], {"action": "FIND_APPROACHES"})
        self.assertEqual(session.calls[5][2]["data"], {"action": "SEND_CODE", "type": "totp"})
        self.assertEqual(session.calls[6][2]["data"], {"action": "VERITY_TOTP_CODE", "vericode": "123456"})
        self.assertEqual(session.calls[4][2]["headers"]["X-Requested-With"], "XMLHttpRequest")
        self.assertEqual(session.calls[6][2]["headers"]["Referer"], LOGIN_CHECK)
        self.assertEqual(session.calls[7][1], SAVE_TRUSTED_DEVICE)
        self.assertEqual(session.calls[7][2]["data"]["radioVal"], "是")
        self.assertEqual(session.calls[7][2]["headers"]["X-Requested-With"], "XMLHttpRequest")
        self.assertEqual(session.calls[8][1], DOUBLE_AUTH_REDIRECT)
        self.assertTrue(result.trust_saved)
        self.assertIsNotNone(result.trusted_device)

    def test_totp_challenge_rejects_invalid_codes_before_verification_call(self):
        for code in ("", "12345", "1234567", "abcdef"):
            session = FakeSession(totp_flow())
            with self.subTest(code=code), patch("utils.auth.requests.Session", return_value=session), self.assertRaisesRegex(AdditionalVerificationRequired, "六位"):
                login_with_password("synthetic-account", "synthetic-password", totp_code=code)
            # Password, FIND_APPROACHES and SEND_CODE have completed; an invalid
            # local code must not reach VERITY_TOTP_CODE.
            self.assertEqual(len(session.calls), 6)

    def test_totp_must_be_available_and_valid(self):
        with self.assertRaisesRegex(AdditionalVerificationRequired, "未提供 TOTP"):
            self.login(totp_flow(has_totp=False), totp_code="123456")
        with self.assertRaises(AdditionalVerificationRequired) as error:
            self.login(totp_flow(code_result="error"), totp_code="123456")
        self.assertNotIn("synthetic-private-error", str(error.exception))

    def test_totp_redirect_is_restricted_to_recorded_endpoint(self):
        with self.assertRaises(AuthenticationError):
            self.login(totp_flow(redirect_url="https://example.com/collect"), totp_code="123456")

    def test_staged_challenge_exposes_three_methods_then_email_or_sms_can_complete(self):
        for method, expected_type in (("enterprise_email", "wechat"), ("sms", "mobile")):
            session = FakeSession(second_factor_flow(method))
            with self.subTest(method=method), patch("utils.auth.requests.Session", return_value=session):
                challenge = start_login("synthetic-account", "synthetic-password")
                self.assertIsInstance(challenge, SecondFactorChallenge)
                self.assertEqual(challenge.methods, ("enterprise_email", "sms", "totp"))
                verification = request_second_factor_code(challenge, method)
                self.assertIsInstance(verification, SecondFactorVerification)
                result = complete_second_factor(verification, "123456")
            self.assertEqual(session.calls[5][2]["data"], {"action": "SEND_CODE", "type": expected_type})
            self.assertEqual(session.calls[6][2]["data"], {"action": "VERITY_CODE", "vericode": "123456"})
            self.assertTrue(result.trust_saved)

    def test_trusted_device_is_reused_without_persisting_or_exposing_its_token(self):
        first_session = FakeSession(second_factor_flow("sms"))
        with patch("utils.auth.requests.Session", return_value=first_session):
            challenge = start_login("synthetic-account", "synthetic-password")
            verification = request_second_factor_code(challenge, "sms")
            first = complete_second_factor(verification, "123456")
        self.assertNotIn("abcdef", repr(first))
        self.assertNotIn("abcdef", repr(first.trusted_device))

        second_session = FakeSession(flow())
        with patch("utils.auth.requests.Session", return_value=second_session):
            second = start_login(
                "synthetic-account", "synthetic-password",
                trusted_device=first.trusted_device,
            )
        fields = second_session.calls[3][2]["data"]
        self.assertEqual(fields["fingerPrint"], first.trusted_device.fingerprint)
        self.assertEqual(fields["fingerGenPrint"], first.trusted_device.token)
        self.assertIs(second.trusted_device, first.trusted_device)

    def test_failed_trust_write_does_not_discard_successful_verification(self):
        replies = second_factor_flow("sms")
        replies[7] = requests.Timeout("synthetic trust timeout")
        session = FakeSession(replies)
        with patch("utils.auth.requests.Session", return_value=session):
            challenge_state = start_login("synthetic-account", "synthetic-password")
            verification_state = request_second_factor_code(challenge_state, "sms")
            result = complete_second_factor(verification_state, "123456")
        self.assertEqual(result.servicehall, "synthetic-authenticated-cookie")
        self.assertFalse(result.trust_saved)
        self.assertIsNone(result.trusted_device)

    def test_challenge_and_verification_never_contain_password_or_show_secrets(self):
        session = FakeSession(second_factor_flow("enterprise_email"))
        with patch("utils.auth.requests.Session", return_value=session):
            challenge = start_login("synthetic-account", "synthetic-password")
            verification = request_second_factor_code(challenge, "enterprise_email")
        self.assertFalse(hasattr(challenge, "password"))
        self.assertFalse(hasattr(verification, "password"))
        self.assertNotIn("synthetic", repr(challenge))
        self.assertNotIn("synthetic", repr(verification))

    def test_mfa_preserves_student_id_from_login_check_header(self):
        replies = second_factor_flow("enterprise_email")
        replies[3] = (
            "POST", response(LOGIN_CHECK, SECONDARY, set_cookie=LOGIN_SET_COOKIE),
        )
        replies[6] = ("POST", json_response(DOUBLE_AUTH_LOGIN, {
            "result": "success", "msg": "", "object": {
                "flow": "VERIFIED", "type": "third",
                "redirectUrl": "/do/off/ui/auth/login/redirect2Jsp",
            },
        }))
        session = FakeSession(replies)
        with patch("utils.auth.requests.Session", return_value=session):
            challenge_state = start_login("synthetic-account", "synthetic-password")
            self.assertEqual(challenge_state.idserial, "2025000000")
            self.assertNotIn("2025000000", repr(challenge_state))
            verification_state = request_second_factor_code(
                challenge_state, "enterprise_email",
            )
            result = complete_second_factor(verification_state, "123456")
        self.assertEqual(result.idserial, "2025000000")

    def test_captcha_before_post_never_submits_credentials(self):
        session = FakeSession(flow(form=FORM.replace('form-group hidden', 'form-group')))
        with patch("utils.auth.requests.Session", return_value=session), self.assertRaises(AdditionalVerificationRequired):
            login_with_password("synthetic-account", "synthetic-password")
        self.assertEqual(len(session.calls), 3)

    def test_bad_password_does_not_return_anonymous_cookie_or_retry(self):
        session = FakeSession(flow(login_body=FORM))
        with patch("utils.auth.requests.Session", return_value=session), self.assertRaises(AuthenticationError):
            login_with_password("synthetic-account", "synthetic-password")
        self.assertEqual(len(session.calls), 4)

    def test_captcha_or_mfa_after_post(self):
        for body in (FORM.replace('form-group hidden', 'form-group'), "<div>短信二次认证</div>"):
            with self.subTest(body=body[:20]), self.assertRaises(AdditionalVerificationRequired):
                self.login(flow(login_body=body))

    def test_ticket_exchange_must_grant_protected_page_access(self):
        replies = flow()
        replies[5:] = [
            ("GET", response(TRADE_PAGE, status=302, location=FORM_URL)),
            ("GET", response(FORM_URL, FORM)),
        ]
        with self.assertRaises(AuthenticationError):
            self.login(replies)

    def test_logged_out_header_is_not_a_verified_login(self):
        with self.assertRaises(AuthenticationError):
            self.login(flow(top='<input id="topUsername" value="">'))

    def test_cookie_value_can_stay_unchanged_after_successful_ticket_exchange(self):
        with patch("utils.auth.requests.Session", return_value=FakeSession(flow(), rotate_cookie=False)):
            result = login_with_password("synthetic-account", "synthetic-password")
        self.assertEqual(result.servicehall, "synthetic-anonymous-cookie")

    def test_identity_domain_cookie_is_not_returned_as_card_cookie(self):
        with patch("utils.auth.requests.Session", return_value=FakeSession(flow(), cookie_domain="id.tsinghua.edu.cn")):
            with self.assertRaisesRegex(AuthenticationError, "servicehall"):
                login_with_password("synthetic-account", "synthetic-password")

    def test_official_domain_redirect_and_post_targets_are_checked(self):
        replies = flow()
        replies[0] = ("GET", response(TRADE_PAGE, status=302, location="https://example.com/"))
        with self.assertRaises(AuthenticationError):
            self.login(replies)
        for action in ("https://example.com/collect", CARD_ORIGIN + "/collect", "/changed-login"):
            with self.subTest(action=action), self.assertRaises(AuthenticationError):
                self.login(flow(form=FORM.replace('/do/off/ui/auth/login/check', action)))

    def test_307_never_replays_credentials(self):
        replies = flow()
        replies[3] = ("POST", response(LOGIN_CHECK, status=307, location=LOGIN_CHECK))
        with self.assertRaises(AuthenticationError):
            self.login(replies)

    def test_timeout_error_does_not_echo_request_or_credentials(self):
        with self.assertRaises(AuthenticationError) as error:
            self.login([requests.Timeout("synthetic-password synthetic-ticket")])
        self.assertNotIn("synthetic", str(error.exception))

    def test_student_id_comes_from_expired_response_cookie_not_numeric_username(self):
        result, _ = self.login(flow(trade="<html>trade page</html>"), username="2025888888")
        self.assertEqual(result.idserial, "2025000000")

    def test_missing_or_invalid_identity_cookie_never_falls_back_to_username_or_html(self):
        for cookie in (None, "TSINGHUAUSERID=invalid-value; Path=/", "TSINGHUAUSERID=; Path=/"):
            with self.subTest(cookie=cookie), self.assertRaisesRegex(AuthenticationError, "TSINGHUAUSERID"):
                self.login(flow(identity_cookie=cookie), username="2025888888")

    def test_multiple_raw_set_cookie_headers_preserve_expired_identity(self):
        reply = response(LOGIN_CHECK)
        reply.raw = SimpleNamespace(headers=HTTPHeaderDict([
            ("Set-Cookie", "JSESSIONID=synthetic-session; HttpOnly; Path=/"),
            ("Set-Cookie", LOGIN_SET_COOKIE),
            ("Set-Cookie", "other=synthetic-cookie; Path=/"),
        ]))
        self.assertEqual(_student_id_from_login(reply), "2025000000")

    def test_combined_set_cookie_header_preserves_expires_comma(self):
        reply = response(LOGIN_CHECK, set_cookie="JSESSIONID=synthetic-session; Path=/, " + LOGIN_SET_COOKIE)
        self.assertEqual(_student_id_from_login(reply), "2025000000")

    def test_identity_cookie_from_other_endpoint_is_not_used(self):
        self.assertEqual(_student_id_from_login(response(CALLBACK, set_cookie=LOGIN_SET_COOKIE)), "")

    def test_official_http_urls_are_upgraded_without_changing_query(self):
        for host in ("card.tsinghua.edu.cn", "id.tsinghua.edu.cn"):
            for port in ("", ":80"):
                with self.subTest(host=host, port=port):
                    self.assertEqual(_safe_url(f"http://{host}{port}/path?next=%2Ftest&v=1"),
                                     f"https://{host}/path?next=%2Ftest&v=1")

    def test_string_convenience_api(self):
        with patch("utils.auth.requests.Session", return_value=FakeSession(flow())):
            self.assertEqual(get_servicehall("synthetic-account", "synthetic-password"), "synthetic-authenticated-cookie")

    def test_string_convenience_api_supports_totp(self):
        with patch("utils.auth.requests.Session", return_value=FakeSession(totp_flow())):
            self.assertEqual(
                get_servicehall("synthetic-account", "synthetic-password", totp_code="123456"),
                "synthetic-authenticated-cookie",
            )

    def test_unsafe_urls(self):
        for url in (
            "https://card.tsinghua.edu.cn.example.com/", "http://id.tsinghua.edu.cn.example.com/",
            "http://id.tsinghua.edu.cn:8080/", "http://card.tsinghua.edu.cn:443/",
            "https://id.tsinghua.edu.cn:8443/", "https://user@id.tsinghua.edu.cn/",
        ):
            with self.subTest(url=url), self.assertRaises(AuthenticationError):
                _safe_url(url)


if __name__ == "__main__":
    unittest.main()
