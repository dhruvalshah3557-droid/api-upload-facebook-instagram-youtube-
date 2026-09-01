#!/usr/bin/env python3
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from meta_account_sync import market_settings, sync_accounts
from sheets_reader import SheetsReader


HEADERS = SheetsReader.ACCOUNTS_HEADER_COLS


class FakeAccountsWorksheet:
    def __init__(self):
        self.updated = []
        self.appended = []

    def row_values(self, row):
        return list(HEADERS)

    def batch_update(self, data, value_input_option=None):
        self.updated.extend(data)

    def append_rows(self, rows, value_input_option=None):
        self.appended.extend(rows)


class FakeSheets:
    accounts_header_row = 1
    _col_letter = staticmethod(SheetsReader._col_letter)

    def __init__(self):
        self.accounts_ws = FakeAccountsWorksheet()
        self._accounts = [
            {
                "row": 2,
                "account_id": "FB-SWEDEN",
                "platform": "facebook",
                "account_name": "Colour Diam Sweden",
                "platform_account_id": "PAGE123",
                "username_or_channel": "colourdiamsweden",
            },
            {
                "row": 46,
                "account_id": "IG-SWEDEN",
                "platform": "instagram",
                "account_name": "Colour Diam Sweden",
                "platform_account_id": "",
                "username_or_channel": "colourdiamsweden",
                "enabled": True,
            },
        ]

    def get_accounts(self):
        return self._accounts


class MetaAccountSyncTests(unittest.TestCase):
    def test_updates_existing_enabled_instagram_placeholder(self):
        sheets = FakeSheets()
        pages = [{
            "id": "PAGE123",
            "name": "Colour Diam Sweden",
            "instagram_business_account": {
                "id": "IG123",
                "username": "colourdiamsweden",
            },
        }]
        changes = sync_accounts(pages, sheets)

        self.assertEqual(sheets.accounts_ws.appended, [])
        updates = {
            item["range"]: item["values"][0][0]
            for item in sheets.accounts_ws.updated
        }
        self.assertEqual(updates["D46"], "IG123")
        self.assertEqual(updates["E46"], "colourdiamsweden")
        self.assertIn("PAGE123", updates["T46"])
        self.assertNotIn("I46", updates)
        self.assertEqual(changes[0]["action"], "updated_platform_id")

    def test_new_markets_have_regional_language_and_timezone(self):
        self.assertEqual(market_settings("Colour Diam Germany"), ("GERMANY", "de-DE", "Europe/Berlin"))
        self.assertEqual(market_settings("colourdiamchina"), ("CHINA", "zh-CN", "Asia/Shanghai"))


if __name__ == "__main__":
    unittest.main()
