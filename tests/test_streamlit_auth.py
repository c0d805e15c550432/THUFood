"""Exercise real Streamlit forms against synthetic backend responses."""

from datetime import date
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import matplotlib
import requests
matplotlib.use("Agg")  # No Windows GUI event loop in the AppTest worker.

from streamlit.testing.v1 import AppTest

from utils.get_eat_record import RecordQueryError
from utils.auth import (
    AdditionalVerificationRequired,
    AuthenticationError,
    LoginResult,
    SecondFactorChallenge,
    SecondFactorVerification,
    TrustedDevice,
)


APP = str(Path(__file__).resolve().parents[1] / "st.py")
EMPTY_RECORDS = {"resultData": {"rows": []}}


def challenge(methods=('enterprise_email', 'sms', 'totp')):
    return SecondFactorChallenge(
        requests.cookies.RequestsCookieJar(), methods,
        'https://id.tsinghua.edu.cn/do/off/ui/auth/login/check',
        '0' * 32,
    )


def verification(method):
    return SecondFactorVerification(
        requests.cookies.RequestsCookieJar(), method,
        'https://id.tsinghua.edu.cn/do/off/ui/auth/login/check',
        '0' * 32,
    )


class StreamlitAuthTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "THUFOOD_SKIP_LEGACY_MIGRATION": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.start_patch = patch("utils.auth.start_login")
        self.start = self.start_patch.start()
        self.addCleanup(self.start_patch.stop)
        self.request_code_patch = patch("utils.auth.request_second_factor_code")
        self.request_code = self.request_code_patch.start()
        self.addCleanup(self.request_code_patch.stop)
        self.complete_patch = patch("utils.auth.complete_second_factor")
        self.complete = self.complete_patch.start()
        self.addCleanup(self.complete_patch.stop)
        self.load_trusted_patch = patch("utils.trusted_store.load_trusted_device", return_value=None)
        self.load_trusted = self.load_trusted_patch.start()
        self.addCleanup(self.load_trusted_patch.stop)
        self.save_trusted_patch = patch("utils.trusted_store.save_trusted_device", return_value=True)
        self.save_trusted = self.save_trusted_patch.start()
        self.addCleanup(self.save_trusted_patch.stop)
        self.profile_state_patch = patch(
            "utils.llm_profiles.load_profile_state",
            return_value={"version": 1, "selected": None, "profiles": []},
        )
        self.profile_state_patch.start()
        self.addCleanup(self.profile_state_patch.stop)
        self.profile_key_patch = patch("utils.llm_profiles.load_profile_api_key", return_value="")
        self.profile_key_patch.start()
        self.addCleanup(self.profile_key_patch.stop)
        self.ask_patch = patch("utils.ask_gpt.ask_gpt", return_value="合成 AI 评论")
        self.ask = self.ask_patch.start()
        self.addCleanup(self.ask_patch.stop)
        self.query_patch = patch("utils.get_eat_record.get_record", return_value=EMPTY_RECORDS)
        self.query = self.query_patch.start()
        self.addCleanup(self.query_patch.stop)
        self.app = AppTest.from_file(APP, default_timeout=20).run()
        self.assertEqual(len(self.app.exception), 0)

    def submit(self):
        self.app.button[0].click().run()
        self.assertEqual(len(self.app.exception), 0)

    def account_mode(self):
        if self.app.radio(key="auth_mode").value != "账号密码登录":
            self.app.radio(key="auth_mode").set_value("账号密码登录").run()
        self.assertFalse(any("TLS 证书校验已关闭" in warning.value for warning in self.app.warning))
        self.app.text_input(key="auth_username").set_value("synthetic-account")
        self.app.text_input(key="auth_password").set_value(" synthetic-password ")

    def test_manual_mode_queries_and_clears_cookie(self):
        self.app.radio(key="auth_mode").set_value("手动输入 servicehall").run()
        self.app.text_input(key="manual_idserial").set_value("2025000000")
        self.app.text_input(key="manual_servicehall").set_value("synthetic-cookie")
        self.app.date_input(key="query_start_date").set_value(date(2025, 1, 2))
        self.app.date_input(key="query_end_date").set_value(date(2025, 2, 3))
        self.submit()
        self.start.assert_not_called()
        self.query.assert_called_once_with("synthetic-cookie", "2025000000", date(2025, 1, 2), date(2025, 2, 3))
        self.assertEqual(self.app.text_input(key="manual_servicehall").value, "")
        self.assertNotIn('_submitted_credentials', self.app.session_state)
        self.assertEqual(len(self.app.error), 0)

    def test_manual_query_failure_suggests_account_password_login(self):
        self.query.side_effect = RecordQueryError("servicehall 未登录或已过期。")
        self.app.radio(key="auth_mode").set_value("手动输入 servicehall").run()
        self.app.text_input(key="manual_idserial").set_value("2025000000")
        self.app.text_input(key="manual_servicehall").set_value("expired-cookie")
        self.submit()
        self.assertIn("可能已失效", self.app.error[0].value)
        self.assertIn("账号密码登录", self.app.error[0].value)

    def test_ai_sidebar_offers_requested_presets_with_deepseek_first(self):
        self.app.toggle(key="llm_enabled").set_value(True).run()
        provider = next(widget for widget in self.app.selectbox if widget.label == "服务商预设")
        self.assertEqual(provider.options[0], "DeepSeek")
        self.assertEqual(
            set(provider.options),
            {"OpenAI", "Claude", "Grok", "Ollama", "Gemini", "千问", "智谱", "Kimi", "MiniMax", "并行智算云", "DeepSeek"},
        )
        base_url = next(widget for widget in self.app.text_input if widget.label == "Base URL")
        model = next(widget for widget in self.app.text_input if widget.label == "Model")
        self.assertEqual(base_url.value, "https://api.deepseek.com")
        self.assertEqual(model.value, "deepseek-v4-flash")

    def test_ai_comments_only_generate_after_button_click_and_are_cached(self):
        self.start.return_value = LoginResult("synthetic-login-cookie", "2025000000")
        self.query.return_value = {"resultData": {"rows": [
            {"txdate": "2025-01-02 06:30:00", "txamt": 1250, "meraddr": "紫荆园",
             "mername": "紫荆园_测试窗口", "username": "测试用户", "summary": "持卡人消费"},
            {"txdate": "2025-02-03 22:15:00", "txamt": 1500, "meraddr": "桃李园",
             "mername": "桃李园_测试窗口", "username": "测试用户", "summary": "持卡人消费"},
        ]}}
        self.account_mode()
        self.submit()
        self.app.toggle(key="llm_enabled").set_value(True).run()
        self.ask.assert_not_called()

        generate = next(button for button in self.app.button if button.label == "开始生成")
        generate.click().run()
        self.assertEqual(self.ask.call_count, 3)
        self.assertEqual(
            self.app.session_state["ai_comments"],
            {
                "earliest": "合成 AI 评论",
                "latest": "合成 AI 评论",
                "most_expensive": "合成 AI 评论",
            },
        )

        self.app.run()
        self.assertEqual(self.ask.call_count, 3)

    def test_account_mode_queries_with_verified_cookie_and_does_not_relogin_on_rerun(self):
        self.start.return_value = LoginResult("synthetic-login-cookie", "2025000000")
        self.account_mode()
        self.submit()
        self.start.assert_called_once_with(
            "synthetic-account", " synthetic-password ", trusted_device=None,
        )
        self.assertEqual(self.query.call_args.args[:2], ("synthetic-login-cookie", "2025000000"))
        self.assertEqual(self.app.radio(key="auth_mode").value, "手动输入 servicehall")
        self.assertEqual(self.app.text_input(key="manual_idserial").value, "2025000000")
        self.assertEqual(self.app.text_input(key="manual_servicehall").value, "synthetic-login-cookie")
        self.assertNotIn('_submitted_credentials', self.app.session_state)
        self.app.run()
        self.assertEqual(self.start.call_count, 1)
        self.assertEqual(self.query.call_count, 1)

    def test_account_authentication_still_autofills_manual_mode_when_record_query_fails(self):
        self.start.return_value = LoginResult("synthetic-login-cookie", "2025000000")
        self.query.side_effect = RecordQueryError("临时查询失败。")
        self.account_mode()
        self.submit()
        self.assertEqual(self.app.radio(key="auth_mode").value, "手动输入 servicehall")
        self.assertEqual(self.app.text_input(key="manual_idserial").value, "2025000000")
        self.assertEqual(self.app.text_input(key="manual_servicehall").value, "synthetic-login-cookie")
        self.assertTrue(any("账号认证已完成" in item.value for item in self.app.warning))

    def test_account_mode_only_shows_username_and_password(self):
        self.start.return_value = LoginResult("synthetic-login-cookie", "2025000000")
        self.assertEqual(self.app.radio(key="auth_mode").value, "账号密码登录")
        # Ignore any optional ID left in an already-open session from the old UI.
        self.app.session_state['login_idserial'] = '2025999999'
        self.account_mode()
        self.assertEqual([widget.key for widget in self.app.text_input], ['auth_username', 'auth_password'])
        self.submit()
        self.assertEqual(self.query.call_args.args[1], "2025000000")

    def test_authenticated_query_reaches_analysis_and_retains_only_report_data(self):
        self.start.return_value = LoginResult("synthetic-login-cookie", "2025000000")
        self.query.return_value = {"resultData": {"rows": [
            {"txdate": "2025-01-02 12:00:00", "txamt": 1250, "meraddr": "紫荆园",
             "mername": "紫荆园_测试窗口", "username": "测试用户", "summary": "持卡人消费"},
            {"txdate": "2025-02-03 18:00:00", "txamt": 1500, "meraddr": "桃李园",
             "mername": "桃李园_测试窗口", "username": "测试用户", "summary": "持卡人消费"},
        ]}}
        self.account_mode()
        self.app.date_input(key="query_start_date").set_value(date(2025, 1, 1))
        self.app.date_input(key="query_end_date").set_value(date(2025, 2, 28))
        self.submit()
        self.assertEqual(len(self.app.error), 0)
        self.assertIn("report_data", self.app.session_state)
        report = self.app.session_state["report_data"]
        self.assertEqual(report["df_raw"]["txamt"].sum(), 27.5)
        self.assertNotIn("servicehall", report)
        self.assertNotIn("password", report)
        self.assertEqual(self.app.metric[0].value, "¥27.50")
        self.app.run()
        self.assertEqual(len(self.app.error), 0)
        self.assertEqual(self.start.call_count, 1)
        self.assertEqual(self.query.call_count, 1)

    def test_verification_error_is_actionable_and_clears_password(self):
        self.start.side_effect = AdditionalVerificationRequired("请在官网完成验证码，再使用手动输入 servicehall。")
        self.account_mode()
        self.submit()
        self.query.assert_not_called()
        self.assertIn("验证码", self.app.error[0].value)
        self.assertNotIn("synthetic-password", self.app.error[0].value)
        self.assertEqual(self.app.text_input(key="auth_password").value, "")

    def test_wrong_password_can_be_corrected_without_reentering_username(self):
        self.start.side_effect = [
            AuthenticationError("登录未成功，请检查账号密码；不要连续重试。"),
            LoginResult("synthetic-login-cookie", "2025000000"),
        ]
        self.account_mode()
        self.submit()
        self.assertIn("检查账号密码", self.app.error[0].value)
        self.assertEqual(self.app.text_input(key="auth_username").value, "synthetic-account")
        self.assertEqual(self.app.text_input(key="auth_password").value, "")

        self.app.text_input(key="auth_password").set_value("corrected-password")
        self.submit()
        self.assertEqual(self.start.call_count, 2)
        self.assertEqual(self.start.call_args.args[:2], ("synthetic-account", "corrected-password"))
        self.assertEqual(self.app.radio(key="auth_mode").value, "手动输入 servicehall")

    def test_wrong_server_verification_code_can_retry_same_session(self):
        selected = verification('sms')
        self.start.return_value = challenge(('sms',))
        self.request_code.return_value = selected
        self.account_mode()
        self.submit()
        self.submit()

        self.complete.side_effect = [
            AdditionalVerificationRequired("二次验证码错误或已过期，请重新输入。"),
            LoginResult("synthetic-login-cookie", "2025000000"),
        ]
        self.app.text_input(key="auth_verification_code").set_value("111111")
        self.submit()
        self.assertIn("验证码错误", self.app.error[0].value)
        self.assertIs(self.app.session_state['_auth_verification'], selected)
        self.assertEqual(self.app.text_input(key="auth_verification_code").value, "")

        self.app.text_input(key="auth_verification_code").set_value("222222")
        self.submit()
        self.assertEqual(self.complete.call_count, 2)
        self.assertEqual(self.complete.call_args.args, (selected, "222222"))
        self.assertEqual(self.app.radio(key="auth_mode").value, "手动输入 servicehall")

    def test_staged_totp_trusts_device_and_reuses_token(self):
        self.start.return_value = challenge()
        self.account_mode()
        self.assertNotIn('auth_verification_code', [widget.key for widget in self.app.text_input])
        self.submit()
        self.assertEqual(
            self.app.radio(key="auth_second_factor_method").options,
            ['企业邮箱验证码', '短信验证码', 'TOTP 动态验证码'],
        )
        self.assertNotIn('auth_password', [widget.key for widget in self.app.text_input])
        self.assertEqual(self.app.session_state['auth_password'], '')

        selected = verification('totp')
        self.request_code.return_value = selected
        self.app.radio(key="auth_second_factor_method").set_value('totp')
        self.submit()
        self.request_code.assert_called_once_with(self.start.return_value, 'totp')
        self.app.text_input(key="auth_verification_code").set_value("123456")
        trusted = TrustedDevice('0' * 32, 'a' * 32)
        self.complete.return_value = LoginResult(
            "synthetic-login-cookie", "2025000000", trusted, True,
        )
        self.submit()
        self.complete.assert_called_once_with(selected, "123456")
        self.save_trusted.assert_called_once_with("synthetic-account", trusted)
        self.assertIn('synthetic-account', self.app.session_state['_trusted_devices'])
        self.assertNotIn('_auth_challenge', self.app.session_state)
        self.assertNotIn('_auth_verification', self.app.session_state)

        # A fresh Streamlit session loads the token from the OS credential store.
        self.load_trusted.return_value = trusted
        self.start.reset_mock()
        self.start.return_value = LoginResult(
            "synthetic-login-cookie", "2025000000", trusted, True,
        )
        self.app = AppTest.from_file(APP, default_timeout=20).run()
        self.app.text_input(key="auth_username").set_value("synthetic-account")
        self.app.text_input(key="auth_password").set_value("another-password")
        self.submit()
        self.start.assert_called_once_with(
            "synthetic-account", "another-password", trusted_device=trusted,
        )

    def test_invalid_second_factor_code_is_rejected_before_verification(self):
        self.start.return_value = challenge(('sms',))
        self.account_mode()
        self.submit()
        self.request_code.return_value = verification('sms')
        self.submit()
        self.app.text_input(key="auth_verification_code").set_value("12ab56")
        self.submit()
        self.start.assert_called_once()
        self.request_code.assert_called_once()
        self.complete.assert_not_called()
        self.query.assert_not_called()
        self.assertIn("六位数字", self.app.error[0].value)

    def test_invalid_dates_do_not_send_password(self):
        self.account_mode()
        self.app.date_input(key="query_start_date").set_value(date(2025, 2, 3))
        self.app.date_input(key="query_end_date").set_value(date(2025, 1, 2))
        self.submit()
        self.start.assert_not_called()
        self.query.assert_not_called()
        self.assertIn("开始日期", self.app.error[0].value)
        self.assertEqual(self.app.text_input(key="auth_password").value, "")

    def test_missing_credentials_and_missing_student_id(self):
        self.account_mode()
        self.app.text_input(key="auth_password").set_value("")
        self.submit()
        self.start.assert_not_called()
        self.assertIn("账号和密码", self.app.error[0].value)
        self.start.return_value = LoginResult("synthetic-login-cookie")
        self.app.text_input(key="auth_password").set_value("synthetic-password")
        self.submit()
        self.query.assert_not_called()
        self.assertIn("登录响应未返回有效学号", self.app.error[0].value)
        self.assertNotIn("补填", self.app.error[0].value)

    def test_mode_switch_updates_visible_fields_without_submitting(self):
        self.account_mode()
        self.app.radio(key="auth_mode").set_value("手动输入 servicehall").run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertIn("manual_servicehall", [widget.key for widget in self.app.text_input])
        self.assertNotIn("auth_password", [widget.key for widget in self.app.text_input])
        self.start.assert_not_called()
        self.query.assert_not_called()

    def test_mode_switch_cancels_pending_second_factor_session(self):
        self.start.return_value = challenge()
        self.account_mode()
        self.submit()
        self.assertIn('_auth_challenge', self.app.session_state)
        self.app.radio(key="auth_mode").set_value("手动输入 servicehall").run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertNotIn('_auth_challenge', self.app.session_state)
        self.assertNotIn('_auth_verification', self.app.session_state)
        self.assertNotIn('auth_second_factor_method', self.app.session_state)


if __name__ == "__main__":
    unittest.main()
