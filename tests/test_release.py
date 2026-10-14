import faulthandler
import importlib
import os
import sys
import tempfile
import threading
import time
import unittest

os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from zapi import VERSION, crash  # noqa: E402


class CrashTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["ZAPI_HOME"] = self.dir
        self.saved = (sys.excepthook, threading.excepthook, crash._installed)
        self.home = os.environ.get("ZAPI_HOME")

    def tearDown(self):
        if self.home is None:
            os.environ.pop("ZAPI_HOME", None)
        else:
            os.environ["ZAPI_HOME"] = self.home          # other tests rely on a valid data folder
        faulthandler.disable()
        sys.excepthook, threading.excepthook, crash._installed = self.saved
        if crash._fatal_file:
            crash._fatal_file.close()
            crash._fatal_file = None

    def reports(self):
        return sorted(f for f in os.listdir(os.path.join(self.dir, "logs")) if f.startswith("crash-"))

    def test_report_has_version_traceback_and_privacy_note(self):
        try:
            raise ValueError("boom")
        except ValueError as e:
            path = crash.write_report(type(e), e, e.__traceback__)
        text = open(path, encoding="utf-8").read()
        self.assertIn(f"Zapi {VERSION}", text)
        self.assertIn("ValueError: boom", text)
        self.assertIn("test_report_has_version", text)               # the traceback names the failing function
        self.assertIn("does not send it anywhere", text)
        self.assertTrue(path.startswith(os.path.join(self.dir, "logs")))

    def test_only_the_newest_reports_are_kept(self):
        folder = crash.logs_dir()
        for i in range(14):
            open(os.path.join(folder, f"crash-20200101-0000{i:02d}-1.log"), "w").close()
        try:
            raise RuntimeError("x")
        except RuntimeError as e:
            newest = os.path.basename(crash.write_report(type(e), e, e.__traceback__))
        left = self.reports()
        self.assertEqual(len(left), crash.KEEP)
        self.assertIn(newest, left)

    def test_install_catches_errors_in_worker_threads_and_is_idempotent(self):
        crash._installed = False
        crash.install()
        hook = sys.excepthook
        crash.install()
        self.assertIs(sys.excepthook, hook)

        def boom():
            raise KeyError("in a thread")
        t = threading.Thread(target=boom, name="worker-x")
        t.start()
        t.join()
        time.sleep(0.2)
        text = "".join(open(os.path.join(self.dir, "logs", f), encoding="utf-8").read() for f in self.reports())
        self.assertIn("KeyError", text)
        self.assertIn("worker-x", text)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "logs", "fatal.log")))

    def test_the_handler_itself_never_raises(self):
        crash._installed = False
        crash.install()
        blocker = os.path.join(self.dir, "a-file")
        open(blocker, "w").close()
        os.environ["ZAPI_HOME"] = os.path.join(blocker, "sub")             # a folder cannot be created under a file
        try:
            raise ValueError("still fine")
        except ValueError as e:
            self.assertIsNone(crash.write_report(type(e), e, e.__traceback__))


class BuildArtifactTests(unittest.TestCase):
    def test_version_resource(self):
        mv = importlib.import_module("make_version")
        self.assertEqual(mv.version_tuple(), tuple(int(x) for x in (VERSION.split(".") + ["0", "0"])[:4]))
        mv.main()
        for name, exe in (("version_Zapi.txt", "Zapi.exe"), ("version_zapi-cli.txt", "zapi-cli.exe")):
            text = open(os.path.join(ROOT, "installer", "generated", name), encoding="utf-8").read()
            self.assertIn("Lavati Softwares", text)
            self.assertIn(f"'OriginalFilename', '{exe}'", text)
            compile(text.replace("# UTF-8", ""), name, "eval")          # valid Python, as PyInstaller evaluates it

    def test_notices_include_the_licences_that_require_it(self):
        mn = importlib.import_module("make_notices")
        mn.main()
        text = open(os.path.join(ROOT, "installer", "THIRD_PARTY_NOTICES.txt"), encoding="utf-8").read()
        for needle in ("GNU LESSER GENERAL PUBLIC LICENSE", "replace them with your own build", "grpcio ",
                       "protobuf ", "PyYAML ", "SIL OPEN FONT LICENSE", "Python Software Foundation License"):
            self.assertIn(needle.lower(), text.lower(), needle)

    def test_installer_ships_the_notices_and_license(self):
        iss = open(os.path.join(ROOT, "installer.iss"), encoding="utf-8").read()
        self.assertIn("THIRD_PARTY_NOTICES.txt", iss)
        self.assertIn("LICENSE.txt", iss)
        self.assertTrue(os.path.exists(os.path.join(ROOT, "LICENSE.txt")))


if __name__ == "__main__":
    unittest.main()


class FontLoadingTests(unittest.TestCase):
    """The installed app must find its fonts whatever the folder is called (found by the installer lifecycle test)."""

    def test_fonts_load_from_a_folder_with_non_latin_characters(self):
        import shutil
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from zapi import theme
        base = tempfile.mkdtemp()
        for name in ("李", "Zoë Zapi (test)", "plain"):
            fonts = os.path.join(base, name, "fonts")
            shutil.copytree(os.path.join(ROOT, "fonts"), fonts)
            real = theme.resource
            theme.resource = lambda *parts, _f=fonts: _f if parts == ("fonts",) else real(*parts)
            try:
                ids = theme.load_fonts()
            finally:
                theme.resource = real
            self.assertEqual(len(ids), len([f for f in os.listdir(fonts) if f.endswith(".ttf")]), name)
            self.assertTrue(all(i >= 0 for i in ids), (name, ids))
            self.assertEqual(theme.ui_family(), "IBM Plex Sans", name)


class InstallerScriptTests(unittest.TestCase):
    """Facts about installer.iss that the lifecycle test (tools/smoke_installed.ps1) depends on."""

    @classmethod
    def setUpClass(cls):
        cls.iss = open(os.path.join(ROOT, "installer.iss"), encoding="utf-8").read()

    def test_default_version_matches_the_app(self):
        import re
        m = re.search(r'#ifndef AppVersion\s+#define AppVersion "([^"]+)"', self.iss)
        self.assertEqual(m.group(1), VERSION)                    # build.bat passes the real one; this keeps a manual compile honest

    def test_the_uninstaller_never_kills_itself_and_data_is_kept_by_default(self):
        self.assertIn("-notlike ''unins*''", self.iss)           # the uninstaller runs from the install folder
        self.assertIn("KeepData := not HasParam('/REMOVEDATA')", self.iss)
        self.assertIn("MB_YESNO or MB_DEFBUTTON1", self.iss)     # "keep my data" is the default button
        self.assertNotIn("KeepData := HasParam('/KEEPDATA')", self.iss)

    def test_upgrade_cleans_old_files_and_refuses_downgrades(self):
        for needle in (r'Name: "{app}\_internal"', r'Name: "{app}\Zapi*.exe"', r'Name: "{app}\*.txt"', "InitializeSetup",
                       "/ALLOWDOWNGRADE", "PrepareToInstall", "UsePreviousAppDir=yes", "UsePreviousTasks=yes",
                       "CloseApplications=yes"):
            self.assertIn(needle, self.iss, needle)
        self.assertNotIn(r'Name: "{app}\*"', self.iss)          # never wipe the whole folder: the uninstaller lives there

    def test_path_cleanup_matches_whole_entries_only(self):
        self.assertIn("Padded := ';' + Paths + ';'", self.iss)
