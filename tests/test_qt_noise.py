import io
import unittest
from contextlib import redirect_stderr

from PySide6.QtCore import qWarning


class QtNoiseTests(unittest.TestCase):
    def test_platform_plugin_chatter_is_hidden_but_real_warnings_are_not(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            qWarning("This plugin does not support propagateSizeHints()")
            qWarning("This plugin does not support raise()")
            qWarning("QObject::startTimer: Timers can only be used with threads started with QThread")
        out = buf.getvalue()
        self.assertNotIn("does not support", out)
        self.assertIn("Timers can only be used", out)                               # genuine problems still show


if __name__ == "__main__":
    unittest.main()
