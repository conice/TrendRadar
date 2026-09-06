import os
from pathlib import Path
import tempfile
import unittest

from mcp_server.services.parser_service import ParserService
from trendradar.core.frequency import match_frequency_title


class FrequencyParserTests(unittest.TestCase):
    def test_cache_tracks_custom_file_creation_edits_and_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom_dir = root / "config" / "custom" / "keyword"
            custom_dir.mkdir(parents=True)
            base = root / "config" / "frequency_words.txt"
            base.write_text("[GLOBAL_FILTER]\n\n[WORD_GROUPS]\n\n芯片\n", encoding="utf-8")
            parser = ParserService(str(root))
            self.assertFalse(
                match_frequency_title("鲁迅", *parser.parse_frequency_config()).accepted
            )

            custom = custom_dir / "Total.txt"
            custom.write_text("[WORD_GROUPS]\n\n鲁迅\n", encoding="utf-8")
            self.assertTrue(
                match_frequency_title("鲁迅", *parser.parse_frequency_config()).accepted
            )

            previous_stat = custom.stat()
            custom.write_text("[WORD_GROUPS]\n\n李白\n", encoding="utf-8")
            os.utime(
                custom,
                ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns + 1_000_000_000),
            )
            rules = parser.parse_frequency_config()
            self.assertFalse(match_frequency_title("鲁迅", *rules).accepted)
            self.assertTrue(match_frequency_title("李白", *rules).accepted)
            self.assertTrue(match_frequency_title("芯片", *rules).accepted)

            custom.unlink()
            rules = parser.parse_frequency_config()
            self.assertFalse(match_frequency_title("李白", *rules).accepted)
            self.assertTrue(match_frequency_title("芯片", *rules).accepted)

    def test_cache_tracks_base_filters_when_custom_file_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom_dir = root / "config" / "custom" / "keyword"
            custom_dir.mkdir(parents=True)
            base = root / "config" / "frequency_words.txt"
            base.write_text("[GLOBAL_FILTER]\n\n[WORD_GROUPS]\n\n芯片\n", encoding="utf-8")
            custom = custom_dir / "Total.txt"
            custom.write_text("[WORD_GROUPS]\n\n鲁迅\n", encoding="utf-8")
            parser = ParserService(str(root))
            self.assertTrue(
                match_frequency_title(
                    "鲁迅广告", *parser.parse_frequency_config(str(custom))
                ).accepted
            )

            base.write_text(
                "[GLOBAL_FILTER]\n广告\n\n[WORD_GROUPS]\n\n芯片\n", encoding="utf-8"
            )
            result = match_frequency_title(
                "鲁迅广告", *parser.parse_frequency_config(str(custom))
            )
            self.assertFalse(result.accepted)
            self.assertEqual(result.filtered_by, "global:广告")


if __name__ == "__main__":
    unittest.main()
