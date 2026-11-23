"""PyInstaller 폴더 빌드(dist/SyncNotes)를 스토어용 MSIX 로 묶는다.

    python tools/build_shellext.py          (우클릭 메뉴 DLL 을 먼저 빌드)
    python tools/make_msix.py --identity-name <이름> --publisher "CN=..." \
        --publisher-display "<게시자 이름>"

    python tools/make_msix.py --layout build/msix/layout
        묶지 않고 폴더로 풀어 놓는다. 개발자 모드를 켠 PC 에서
        Add-AppxPackage -Register <폴더>/AppxManifest.xml 로 등록해 시험한다.

세 값은 파트너 센터에서 앱 이름을 예약하면 나온다(제품 ID 화면). 주지 않으면
시험용 값으로 묶는다: 매니페스트와 묶음이 규칙에 맞는지 보는 데 쓴다. 서명은
하지 않는다(스토어가 한다).

묶음에 들어가는 것
* 실행 파일과 런타임(dist/SyncNotes 그대로)
* 매니페스트: 전체 신뢰 실행, 시작 작업(기본 꺼짐), 실행 별칭, 방화벽 규칙,
  탐색기 우클릭 메뉴(메뉴 DLL 의 COM 클래스)
* 우클릭 메뉴 DLL(build/shellext/MemilMenu.dll)
* 로고(Resources/Image/Icon.png 에서 만든다)와 그 목록(resources.pri).
  배율별 그림과, 작업 표시줄과 시작 메뉴가 쓰는 판 없는 그림을 넣는다.
  없으면 아이콘이 강조색 네모 판 위에 작게 그려진다.
* 언어별 앱 이름과 설명(같은 resources.pri 안). Windows 표시 언어에 맞는 것이
  시작 메뉴와 스토어에 나온다. 이름은 파트너 센터에 예약한 것과 글자가 같아야 한다.

Windows SDK 의 MakeAppx 와 MakePri 가 있어야 한다."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, startup_task  # noqa: E402

EXE_NAME = "SyncNotes.exe"
STARTUP_TASK_ID = startup_task.TASK_ID
ALIAS = "MemilMemo.exe"
# 우클릭 메뉴 DLL 과 그 안의 COM 클래스(shellext/memil_menu.cpp 와 같아야 한다).
MENU_DLL = "MemilMenu.dll"
MENU_DLL_SOURCE = ROOT / "build" / "shellext" / MENU_DLL
CLSID_NEW_NOTE = "75353010-835E-4152-9B9C-4985F6DD6C77"
CLSID_LINK_NOTE = "82DA3587-DE72-4930-8943-9AD6AADB32C3"
CLSID_NEW_FOLDER = "C3A1F6B2-5D0E-4B7A-9E64-2F81D7C05A39"
# 로고: 매니페스트에 적는 이름 → (배율 100% 일 때의 한 변, 그림이 차지하는 비율).
# 타일(150)은 아이콘 둘레를 비워 둔다. 꽉 채우면 시작 화면에서 혼자 크게 보인다.
LOGOS = {
    "StoreLogo": (50, 1.0),
    "Square44x44Logo": (44, 0.82),
    "Square150x150Logo": (150, 0.56),
}
SCALES = (100, 125, 150, 200, 400)
# 작업 표시줄, 시작 메뉴, 알트 탭이 고르는 크기. 판 없는 그림을 함께 넣는다.
TARGET_SIZES = (16, 24, 32, 48, 256)
ICON_LOGO = "Square44x44Logo"
# 언어별 (앱 이름, 설명). 첫째가 기본 언어다(매니페스트의 Resource 순서와 같게 둔다).
STRINGS = {
    "ko-KR": ("메밀메모", "사무실 PC끼리 같이 쓰는 바탕화면 메모"),
    "en-US": ("MemilMemo", "Desktop sticky notes shared across office PCs"),
    "ja-JP": ("メミメモ", "オフィスの PC で一緒に使うデスクトップ付箋メモ"),
}

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:uap3="http://schemas.microsoft.com/appx/manifest/uap/windows10/3"
  xmlns:desktop="http://schemas.microsoft.com/appx/manifest/desktop/windows10"
  xmlns:desktop2="http://schemas.microsoft.com/appx/manifest/desktop/windows10/2"
  xmlns:desktop4="http://schemas.microsoft.com/appx/manifest/desktop/windows10/4"
  xmlns:desktop5="http://schemas.microsoft.com/appx/manifest/desktop/windows10/5"
  xmlns:desktop10="http://schemas.microsoft.com/appx/manifest/desktop/windows10/10"
  xmlns:com="http://schemas.microsoft.com/appx/manifest/com/windows10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap uap3 desktop desktop2 desktop4 desktop5 desktop10 com rescap">
  <Identity Name={identity} Publisher={publisher} Version={version} ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>ms-resource:AppName</DisplayName>
    <PublisherDisplayName>{publisher_display}</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Resources>
{languages}
  </Resources>
  <Applications>
    <Application Id="MemilMemo" Executable="{exe}" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements
        DisplayName="ms-resource:AppName"
        Description="ms-resource:AppDescription"
        BackgroundColor="transparent"
        Square150x150Logo="Assets\\Square150x150Logo.png"
        Square44x44Logo="Assets\\Square44x44Logo.png" />
      <Extensions>
        <desktop:Extension Category="windows.startupTask" Executable="{exe}" EntryPoint="Windows.FullTrustApplication">
          <desktop:StartupTask TaskId="{task}" Enabled="false" DisplayName="ms-resource:AppName" />
        </desktop:Extension>
        <uap3:Extension Category="windows.appExecutionAlias" Executable="{exe}" EntryPoint="Windows.FullTrustApplication">
          <uap3:AppExecutionAlias>
            <desktop:ExecutionAlias Alias="{alias}" />
          </uap3:AppExecutionAlias>
        </uap3:Extension>
        <com:Extension Category="windows.comServer">
          <com:ComServer>
            <com:SurrogateServer DisplayName="MemilMemo menu">
              <com:Class Id="{clsid_new}" Path="{menu_dll}" ThreadingModel="STA" />
              <com:Class Id="{clsid_link}" Path="{menu_dll}" ThreadingModel="STA" />
              <com:Class Id="{clsid_folder}" Path="{menu_dll}" ThreadingModel="STA" />
            </com:SurrogateServer>
          </com:ComServer>
        </com:Extension>
        <desktop4:Extension Category="windows.fileExplorerContextMenus">
          <desktop4:FileExplorerContextMenus>
            <desktop5:ItemType Type="*">
              <desktop5:Verb Id="MemilLinkNote" Clsid="{clsid_link}" />
            </desktop5:ItemType>
            <desktop5:ItemType Type="Directory">
              <desktop5:Verb Id="MemilLinkFolder" Clsid="{clsid_link}" />
            </desktop5:ItemType>
            <desktop10:ItemType Type="DesktopBackground">
              <desktop10:Verb Id="MemilNewNote" Clsid="{clsid_new}" />
              <desktop10:Verb Id="MemilNewNoteFolder" Clsid="{clsid_folder}" />
            </desktop10:ItemType>
          </desktop4:FileExplorerContextMenus>
        </desktop4:Extension>
      </Extensions>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
  <Extensions>
    <desktop2:Extension Category="windows.firewallRules">
      <desktop2:FirewallRules Executable="{exe}">
        <desktop2:Rule Direction="in" IPProtocol="TCP" LocalPortMin="{port}" LocalPortMax="{port}" Profile="private" />
        <desktop2:Rule Direction="in" IPProtocol="TCP" LocalPortMin="{port}" LocalPortMax="{port}" Profile="domain" />
        <desktop2:Rule Direction="in" IPProtocol="UDP" LocalPortMin="{find_port}" LocalPortMax="{find_port}" Profile="private" />
        <desktop2:Rule Direction="in" IPProtocol="UDP" LocalPortMin="{find_port}" LocalPortMax="{find_port}" Profile="domain" />
      </desktop2:FirewallRules>
    </desktop2:Extension>
  </Extensions>
</Package>
"""


RESW = """<?xml version="1.0" encoding="utf-8"?>
<root>
  <resheader name="resmimetype"><value>text/microsoft-resx</value></resheader>
  <resheader name="version"><value>2.0</value></resheader>
  <data name="AppName" xml:space="preserve"><value>{name}</value></data>
  <data name="AppDescription" xml:space="preserve"><value>{description}</value></data>
</root>
"""


def find_sdk_tool(name: str) -> Path | None:
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) \
        / "Windows Kits" / "10" / "bin"
    found = sorted(kits.glob(f"10.*/x64/{name}"), reverse=True)
    return found[0] if found else None


def package_version() -> str:
    """앱 버전에 넷째 자리 0 을 붙인다(스토어는 넷째 자리를 0 으로 둔다)."""
    parts = [p for p in config.APP_VERSION.split(".") if p.isdigit()][:3]
    parts += ["0"] * (3 - len(parts))
    return ".".join(parts + ["0"])


def write_logos(folder: Path) -> list[str]:
    """배율별 로고와 크기별 아이콘을 만든다. 만든 파일 이름을 돌려준다."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter

    app = QGuiApplication.instance() or QGuiApplication([])
    source = QImage(str(config.ICON_PATH))
    if source.isNull():
        raise SystemExit(f"아이콘을 읽지 못했습니다: {config.ICON_PATH}")
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    written: list[str] = []

    def draw(name: str, side: int, fill: float) -> None:
        canvas = QImage(side, side, QImage.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.transparent)
        inner = max(1, round(side * fill))
        scaled = source.scaled(inner, inner, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
        painter = QPainter(canvas)
        painter.drawImage(QRectF((side - scaled.width()) / 2.0,
                                 (side - scaled.height()) / 2.0,
                                 scaled.width(), scaled.height()), scaled)
        painter.end()
        if not canvas.save(str(folder / name), "PNG"):
            raise SystemExit(f"로고를 쓰지 못했습니다: {name}")
        written.append(name)

    for base, (side, fill) in LOGOS.items():
        for scale in SCALES:
            draw(f"{base}.scale-{scale}.png", round(side * scale / 100), fill)
    for size in TARGET_SIZES:
        draw(f"{ICON_LOGO}.targetsize-{size}.png", size, 1.0)
        for form in ("unplated", "lightunplated"):
            draw(f"{ICON_LOGO}.targetsize-{size}_altform-{form}.png", size, 1.0)
    del app
    return written


def make_pri(work: Path, manifest: Path, logo_names: list[str]) -> Path:
    """로고와 글의 목록(resources.pri)을 만든다. 매니페스트는 'Assets/StoreLogo.png'
    처럼 배율 없는 이름과 'ms-resource:AppName' 같은 글 이름을 적고, Windows 가
    이 목록에서 화면과 표시 언어에 맞는 것을 고른다."""
    makepri = find_sdk_tool("makepri.exe")
    if makepri is None:
        raise SystemExit("Windows SDK 의 makepri.exe 를 찾지 못했습니다.")
    stage = work / "pri"
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "Assets").mkdir(parents=True)
    shutil.copy2(manifest, stage / "AppxManifest.xml")
    for name in logo_names:
        shutil.copy2(work / "Assets" / name, stage / "Assets" / name)
    for language, (name, description) in STRINGS.items():
        folder = stage / "Strings" / language
        folder.mkdir(parents=True)
        (folder / "Resources.resw").write_text(RESW.format(
            name=escape(name), description=escape(description)), encoding="utf-8")
    config_file = work / "priconfig.xml"
    pri = work / "resources.pri"

    def run(*args: str) -> None:
        done = subprocess.run([str(makepri), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if done.returncode != 0:
            tail = (done.stdout + done.stderr).strip().splitlines()[-12:]
            raise SystemExit("makepri 실패:\n" + "\n".join(tail))

    run("createconfig", "/cf", str(config_file), "/dq", next(iter(STRINGS)), "/o")
    # 묶음 하나에 모두 넣는다. 그대로 두면 배율과 언어별로 목록이 갈라져 나온다.
    text = config_file.read_text(encoding="utf-8")
    start, end = text.find("<packaging>"), text.find("</packaging>")
    if start >= 0 and end > start:
        text = text[:start] + text[end + len("</packaging>"):]
        config_file.write_text(text, encoding="utf-8")
    run("new", "/pr", str(stage), "/cf", str(config_file), "/of", str(pri),
        "/mn", str(stage / "AppxManifest.xml"), "/o")
    return pri


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default=str(ROOT / "dist" / "SyncNotes"))
    ap.add_argument("--work", default=str(ROOT / "build" / "msix"))
    ap.add_argument("--out", default=str(ROOT / "dist" / "MemilMemo.msix"))
    ap.add_argument("--identity-name", default="MemilMemo.Preview")
    ap.add_argument("--publisher",
                    default="CN=00000000-0000-0000-0000-000000000000")
    ap.add_argument("--publisher-display", default="MemilMemo")
    ap.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    ap.add_argument("--layout", default="",
                    help="묶지 않고 이 폴더에 풀어 놓는다(등록 시험용)")
    args = ap.parse_args()

    source = Path(args.source)
    if not (source / EXE_NAME).is_file():
        raise SystemExit(f"빌드 폴더에 {EXE_NAME} 가 없습니다: {source}")
    if not MENU_DLL_SOURCE.is_file():
        raise SystemExit(f"우클릭 메뉴 DLL 이 없습니다: {MENU_DLL_SOURCE}\n"
                         "먼저 python tools/build_shellext.py 를 돌립니다.")

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    logo_names = write_logos(work / "Assets")
    manifest = work / "AppxManifest.xml"
    manifest.write_text(MANIFEST.format(
        identity=quoteattr(args.identity_name),
        publisher=quoteattr(args.publisher),
        version=quoteattr(package_version()),
        publisher_display=args.publisher_display.replace("&", "&amp;")
        .replace("<", "&lt;"),
        languages="\n".join(f'    <Resource Language="{code}" />'
                            for code in STRINGS),
        exe=EXE_NAME, task=STARTUP_TASK_ID, alias=ALIAS, port=args.port,
        # 내부망에서 공유 PC 를 찾는 신호를 받는 포트(app/network/discovery.py).
        find_port=config.DISCOVERY_PORT,
        menu_dll=MENU_DLL, clsid_new=CLSID_NEW_NOTE, clsid_link=CLSID_LINK_NOTE,
        clsid_folder=CLSID_NEW_FOLDER,
    ), encoding="utf-8")

    pri = make_pri(work, manifest, logo_names)

    # 묶음에 넣을 파일: (원본, 묶음 안의 자리).
    files: list[tuple[Path, str]] = [(manifest, "AppxManifest.xml"),
                                     (pri, "resources.pri"),
                                     (MENU_DLL_SOURCE, MENU_DLL)]
    files += [(work / "Assets" / name, f"Assets\\{name}") for name in logo_names]
    odd: list[str] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(source))
        if path.parent == source and path.name != EXE_NAME:
            # 빌드 폴더에 함께 놓인 설치 파일 같은 것은 넣지 않는다.
            continue
        if not rel.isascii():
            odd.append(rel)
        files.append((path, rel))
    if odd:
        raise SystemExit("묶음에 넣을 수 없는 파일 이름(ASCII 가 아님): "
                         + ", ".join(odd[:5]))

    if args.layout:
        layout = Path(args.layout)
        if layout.exists():
            shutil.rmtree(layout)
        for src, rel in files:
            dest = layout / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        print(f"풀어 놓음: {layout} (파일 {len(files)}개)")
        return 0

    makeappx = find_sdk_tool("makeappx.exe")
    if makeappx is None:
        raise SystemExit("Windows SDK 의 makeappx.exe 를 찾지 못했습니다.")
    # 빌드 폴더를 베끼지 않고, 어느 파일을 묶음의 어디에 넣을지만 적어 준다.
    lines = ["[Files]"] + [f'"{src}" "{rel}"' for src, rel in files]
    mapping = work / "mapping.txt"
    mapping.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        [str(makeappx), "pack", "/o", "/f", str(mapping), "/p", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (done.stdout + done.stderr).strip().splitlines()[-12:]
    print("\n".join(tail))
    if done.returncode != 0:
        return done.returncode
    print(f"묶음: {out} ({out.stat().st_size:,} bytes, 파일 {len(files)}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
