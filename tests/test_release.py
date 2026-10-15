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


class PortableModeTests(unittest.TestCase):
    def test_marker_next_to_a_frozen_exe_moves_the_data_folder(self):
        import sys
        import tempfile
        from zapi import config
        folder = tempfile.mkdtemp()
        exe = os.path.join(folder, "Zapi.exe")
        old_home, old_frozen, old_exe = os.environ.pop("ZAPI_HOME", None), getattr(sys, "frozen", None), sys.executable
        try:
            sys.frozen, sys.executable = True, exe
            self.assertIsNone(config.portable_dir())                                  # no marker: normal install
            with open(os.path.join(folder, "portable.txt"), "w") as f:
                f.write("x")
            self.assertEqual(config.portable_dir(), folder)
            self.assertEqual(config.data_dir(), os.path.join(folder, "ZapiData"))
            self.assertTrue(os.path.isdir(os.path.join(folder, "ZapiData")))
            os.environ["ZAPI_HOME"] = os.path.join(folder, "elsewhere")               # an explicit override still wins
            self.assertEqual(config.data_dir(), os.path.join(folder, "elsewhere"))
            from zapi import diagnostics
            self.assertIn("portable build", diagnostics.info())
        finally:
            sys.executable = old_exe
            if old_frozen is None:
                del sys.frozen
            else:
                sys.frozen = old_frozen
            os.environ.pop("ZAPI_HOME", None)
            if old_home is not None:
                os.environ["ZAPI_HOME"] = old_home

    def test_running_from_source_is_never_portable(self):
        from zapi import config
        self.assertIsNone(config.portable_dir())

    def test_cli_reports_the_data_folder(self):
        import io
        from contextlib import redirect_stdout
        from zapi import cli, config
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(["--data-dir"]), 0)
        self.assertEqual(buf.getvalue().strip(), config.data_dir())


class SingleFilePortableTests(unittest.TestCase):
    def test_exe_named_zapi_portable_is_portable_without_any_marker(self):
        import sys
        import tempfile
        from zapi import config
        folder = tempfile.mkdtemp()
        old_home, old_frozen, old_exe = os.environ.pop("ZAPI_HOME", None), getattr(sys, "frozen", None), sys.executable
        try:
            sys.frozen = True
            sys.executable = os.path.join(folder, "Zapi.exe")
            self.assertIsNone(config.portable_dir())                                  # the installed app is not portable
            sys.executable = os.path.join(folder, "Zapi-Portable.exe")
            self.assertEqual(config.portable_dir(), folder)
            self.assertEqual(config.data_dir(), os.path.join(folder, "ZapiData"))
            sys.executable = os.path.join(folder, "ZAPI-PORTABLE-0.2.exe")            # renamed / versioned copies too
            self.assertEqual(config.portable_dir(), folder)
        finally:
            sys.executable = old_exe
            if old_frozen is None:
                del sys.frozen
            else:
                sys.frozen = old_frozen
            if old_home is not None:
                os.environ["ZAPI_HOME"] = old_home

    def test_trim_list_filters_what_build_bat_deletes_and_keeps_what_is_needed(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import trim
        for gone in ("PySide6\Qt6Quick.dll", "PySide6/qml/QtQuick/x.qml", "PySide6/plugins/tls/qschannelbackend.dll",
                     "api-ms-win-core-file-l1-1-0.dll", "ucrtbase.dll", "PySide6\Qt6WebEngineCore.dll"):
            self.assertFalse(trim.wanted(gone), gone)
        for kept in ("PySide6\Qt6Core.dll", "PySide6\Qt6Widgets.dll", "PySide6\Qt6Qml.dll",
                     "PySide6\plugins\platforms\qwindows.dll", "libssl-3.dll", "libcrypto-3.dll", "python314.dll",
                     "fonts\IBMPlexSans-Regular.ttf"):
            self.assertTrue(trim.wanted(kept), kept)
        # the patterns must be the same ones build.bat deletes, so the two builds stay equal
        bat = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build.bat"),
                   encoding="utf-8").read().lower()
        for pat in ("qt6quick*.dll", "qt6webengine*.dll", "libssl-3-x64.dll", "qdirect2d.dll", "qt6test*.dll"):
            self.assertIn(pat, bat)
            self.assertTrue(any(pat in p for p in trim.PATTERNS), pat)
