"""Writes the Windows version resources (file Properties -> Details) for Zapi.exe and zapi-cli.exe.

Run from the project folder:  python tools/make_version.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from zapi import VERSION  # noqa: E402

TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple}, prodvers={tuple}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Lavati Softwares'),
      StringStruct('FileDescription', '{description}'),
      StringStruct('FileVersion', '{dotted}'),
      StringStruct('InternalName', '{internal}'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Lavati Softwares (MIT License)'),
      StringStruct('OriginalFilename', '{exe}'),
      StringStruct('ProductName', 'Zapi'),
      StringStruct('ProductVersion', '{dotted}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def version_tuple():
    nums = [int(x) for x in re.findall(r"\d+", VERSION)][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def main():
    out = os.path.join(ROOT, "installer", "generated")
    os.makedirs(out, exist_ok=True)
    v = version_tuple()
    for exe, internal, desc in (("Zapi.exe", "Zapi", "Zapi - API client"),
                                ("zapi-cli.exe", "zapi-cli", "Zapi command line collection runner")):
        text = TEMPLATE.format(tuple=v, dotted=".".join(map(str, v)), description=desc, internal=internal, exe=exe)
        with open(os.path.join(out, f"version_{internal}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
    print("version resources written for", ".".join(map(str, v)))


if __name__ == "__main__":
    main()
