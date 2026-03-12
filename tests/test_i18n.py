import os
import tempfile
import unittest

import config
from utils.i18n import (
    get_language,
    read_persisted_language,
    set_language,
    t,
    write_persisted_language,
)


class I18nTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_settings_file = config.SETTINGS_FILE
        self.old_language = config.LANGUAGE
        config.SETTINGS_FILE = os.path.join(self.tmpdir.name, "settings.json")
        set_language("zh-CN")

    def tearDown(self):
        config.SETTINGS_FILE = self.old_settings_file
        set_language(self.old_language)
        self.tmpdir.cleanup()

    def test_set_language_translates_basic_keys(self):
        set_language("en-US")
        self.assertEqual(get_language(), "en-US")
        self.assertEqual(t("status.ready"), "Ready")

        set_language("zh-CN")
        self.assertEqual(t("status.ready"), "就绪")

    def test_write_and_read_persisted_language(self):
        write_persisted_language("en-US")
        self.assertEqual(read_persisted_language(), "en-US")


if __name__ == "__main__":
    unittest.main()
