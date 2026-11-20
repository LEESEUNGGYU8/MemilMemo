"""스토어판의 탐색기 우클릭 메뉴 DLL(shellext/memil_menu.cpp)을 빌드한다.

    python tools/build_shellext.py

산출물: build/shellext/MemilMenu.dll (x64, C 런타임을 안에 넣어 따로 필요 없다).
Visual Studio 빌드 도구(C++)와 Windows SDK 가 있어야 한다."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "shellext" / "memil_menu.cpp"
DEF = ROOT / "shellext" / "memil_menu.def"
OUT_DIR = ROOT / "build" / "shellext"
DLL = OUT_DIR / "MemilMenu.dll"


def find_vcvars() -> Path | None:
    pf86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    vswhere = pf86 / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        return None
    done = subprocess.run(
        [str(vswhere), "-latest", "-products", "*", "-requires",
         "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
         "-property", "installationPath"],
        capture_output=True, text=True)
    for line in done.stdout.splitlines():
        bat = Path(line.strip()) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
        if bat.is_file():
            return bat
    return None


def main() -> int:
    vcvars = find_vcvars()
    if vcvars is None:
        raise SystemExit("C++ 빌드 도구(vcvars64.bat)를 찾지 못했습니다.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    compile_cmd = (
        f'cl /nologo /utf-8 /LD /EHsc /O1 /MT /W4 /DUNICODE /D_UNICODE '
        f'/Fo"{OUT_DIR}\\\\" "{SOURCE}" '
        f'/link /DEF:"{DEF}" /OUT:"{DLL}" /IMPLIB:"{OUT_DIR}\\MemilMenu.lib" '
        f'shell32.lib shlwapi.lib ole32.lib uuid.lib')
    script = OUT_DIR / "build.cmd"
    script.write_text(
        "@echo off\r\n"
        f'call "{vcvars}" >nul\r\n'
        "if errorlevel 1 exit /b 1\r\n"
        f"{compile_cmd}\r\n", encoding="mbcs")
    done = subprocess.run(["cmd", "/c", str(script)], capture_output=True)
    out = (done.stdout + done.stderr).decode("mbcs", "replace").strip()
    if out:
        print(out)
    if done.returncode != 0 or not DLL.is_file():
        return done.returncode or 1
    print(f"빌드: {DLL} ({DLL.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
