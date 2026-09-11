#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main
from sheets_reader import SheetsReader


class RegionalLanguageTests(unittest.TestCase):
    def test_all_configured_regional_columns_are_mapped(self):
        expected = {
            "ar": ("arabic description", "arabic hashtag"),
            "vi": ("Vietnamese Description", "Vietnamese Hashtag"),
            "zh": ("Chinese Description", "Chinese Hashtag"),
            "sv": ("sweden description", "sweden hashtag"),
            "de": ("german description", "german hashtag"),
            "pl": ("polish description", "polish hahstag"),
            "da": ("danish description", "danish hashtag"),
            "fr": ("french description", "french hashtag"),
            "tr": ("turkish description", "turkish hashtag"),
            "it": ("italian description", "italian hashtag"),
            "es": ("spanish description", "spanish hashtag"),
            "ja": ("Japanese Description", "Japanese Hashtag"),
            "ko": ("Korean Description", "Korean Hashtag"),
            "ru": ("Russian Description", "Russian Hashtag"),
            "he": ("israli description", "israli hashtag"),
            "id": ("Indonesian description", "Indonesian hashtag"),
            "my": ("Burmese Description", "Burmese Hashtag"),
            "th": ("Thai Description", "Thai Hashtag"),
            "fil": ("Filipino Description", "Filipino Hashtag"),
        }
        for code, (caption_col, hashtag_col) in expected.items():
            self.assertEqual(SheetsReader.LANG_CAPTION_COLS[code], caption_col)
            self.assertEqual(SheetsReader.LANG_TAG_COLS[code], hashtag_col)

    def test_local_caption_wins_and_english_is_only_fallback(self):
        source = {
            "lang_captions": {"it": "Descrizione italiana"},
            "lang_hashtags": {"it": "#gioielli"},
            "facebook_caption": "English Facebook caption",
            "instagram_caption": "English Instagram caption",
            "youtube_shorts_caption": "English YouTube caption",
            "hashtags": "#diamond",
            "product_link": "",
            "product_name": "Ring",
        }
        account = {
            "primary_language": "it-IT",
            "fallback_language": "en-GB",
            "account_name": "Colour Diam Italy",
        }
        caption = main.build_caption({"platform": "instagram"}, source, account)
        self.assertEqual(caption, "Descrizione italiana\n\n#gioielli")

        account["primary_language"] = "vi-VN"
        caption = main.build_caption({"platform": "instagram"}, source, account)
        self.assertEqual(caption, "English Instagram caption\n\n#diamond")

    def test_vietnam_source_import_headers_are_supported(self):
        row = {
            "vietnam description": "Mô tả tiếng Việt",
            "vietnam hashtag": "#KimCuong",
        }
        caption = SheetsReader._pick(
            row,
            SheetsReader.LANG_CAPTION_COLS["vi"],
            *SheetsReader.LANG_CAPTION_ALIASES["vi"],
        )
        hashtag = SheetsReader._pick(
            row,
            SheetsReader.LANG_TAG_COLS["vi"],
            *SheetsReader.LANG_TAG_ALIASES["vi"],
        )
        self.assertEqual(caption, "Mô tả tiếng Việt")
        self.assertEqual(hashtag, "#KimCuong")

    def test_vietnam_hashtags_are_normalized_for_clickable_tags(self):
        source = {
            "lang_captions": {"vi": "Mô tả tiếng Việt"},
            "lang_hashtags": {
                "vi": "KimCuongThienNhien, #ColourDiam, TrangSucCaoCap"
            },
            "hashtags": "#diamond",
            "product_link": "",
        }
        account = {
            "primary_language": "vi-VN",
            "fallback_language": "en-GB",
            "account_name": "Colour Diam Vietnam",
        }
        caption = main.build_caption({"platform": "instagram"}, source, account)
        self.assertEqual(
            caption,
            "Mô tả tiếng Việt\n\n"
            "#KimCuongThienNhien #ColourDiam #TrangSucCaoCap",
        )

    def test_existing_space_separated_hashtags_stay_valid(self):
        self.assertEqual(
            main._normalize_hashtags("#ColourDiam #KimCuong #GIA"),
            "#ColourDiam #KimCuong #GIA",
        )

    def test_regional_account_never_falls_back_to_english_hashtags(self):
        source = {
            "lang_captions": {"vi": "Mô tả tiếng Việt"},
            "lang_hashtags": {},
            "hashtags": "#diamond #jewelry",
            "product_link": "",
        }
        account = {
            "primary_language": "vi-VN",
            "fallback_language": "en-GB",
            "account_name": "Colour Diam Vietnam",
        }
        caption = main.build_caption({"platform": "facebook"}, source, account)
        self.assertEqual(
            caption,
            "Mô tả tiếng Việt\n\n#KimCương #TrangSức #TrangSứcCaoCấp",
        )
        self.assertNotIn("#diamond", caption)

    def test_each_configured_region_has_native_fallback_hashtags(self):
        regional_codes = set(SheetsReader.LANG_TAG_COLS) - {"en"}
        self.assertTrue(regional_codes <= set(main._REGIONAL_FALLBACK_HASHTAGS))


if __name__ == "__main__":
    unittest.main()
