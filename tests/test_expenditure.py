import unittest
from datetime import date
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

import pandas as pd

from utils.analyze_data import get_monthly_expenditure
from utils.get_eat_record import RecordQueryError, get_record
from utils.process_data import process_data


class MonthlyExpenditureTests(unittest.TestCase):
    def test_inclusive_period_and_missing_months(self):
        transactions = pd.DataFrame(
            {
                "txdate": pd.to_datetime(
                    ["2025-01-31 23:59:59", "2025-03-01 00:00:00"]
                ),
                "txamt": [7.25, 9.75],
            }
        )

        result = get_monthly_expenditure(
            transactions,
            date(2025, 1, 31),
            date(2025, 2, 28),
        )

        self.assertEqual(
            result.to_dict(),
            {
                pd.Period("2025-01", freq="M"): 7.25,
                pd.Period("2025-02", freq="M"): 0.0,
            },
        )

    def test_empty_api_result_can_be_processed(self):
        raw, merged = process_data({"resultData": {"rows": []}})

        self.assertTrue(raw.empty)
        self.assertTrue(merged.empty)
        self.assertIn("time_only", merged.columns)


class RecordQueryTests(unittest.TestCase):
    @patch("utils.get_eat_record.decrypt_aes_ecb")
    @patch("utils.get_eat_record.requests.post")
    def test_selected_dates_are_sent_to_api(self, post, decrypt):
        response = Mock()
        response.status_code = 200
        response.text = '{"data": "encrypted"}'
        response.json.return_value = {"data": "encrypted"}
        post.return_value = response
        decrypt.return_value = '{"resultData": {"rows": []}}'

        with tempfile.TemporaryDirectory() as temporary, patch(
            "utils.get_eat_record.records_dir", return_value=Path(temporary),
        ):
            get_record(
                "cookie",
                "student",
                date(2025, 2, 3),
                date(2025, 4, 5),
            )
            self.assertEqual(len(list(Path(temporary).glob("eat_record_*.json"))), 1)

        params = post.call_args.kwargs["params"]
        self.assertEqual(params["starttime"], "2025-02-03")
        self.assertEqual(params["endtime"], "2025-04-05")
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertTrue(post.call_args.kwargs["verify"])

    @patch("utils.get_eat_record.requests.post")
    def test_expired_servicehall_redirect_is_not_followed(self, post):
        post.return_value.status_code = 302
        with self.assertRaisesRegex(RecordQueryError, "servicehall"):
            get_record("synthetic-cookie", "student", "2025-01-01", "2025-01-02")
        post.return_value.json.assert_not_called()

    @patch("utils.get_eat_record.requests.post")
    def test_html_login_response_and_server_message_are_not_exposed(self, post):
        post.return_value.status_code = 200
        post.return_value.json.side_effect = ValueError("synthetic-sensitive-response")
        with self.assertRaises(RecordQueryError) as error:
            get_record("synthetic-cookie", "student", "2025-01-01", "2025-01-02")
        self.assertNotIn("synthetic", str(error.exception))
        post.return_value.json.side_effect = None
        post.return_value.json.return_value = {"message": "synthetic-sensitive-response"}
        with self.assertRaises(RecordQueryError) as error:
            get_record("synthetic-cookie", "student", "2025-01-01", "2025-01-02")
        self.assertNotIn("synthetic", str(error.exception))


if __name__ == "__main__":
    unittest.main()
