"""메모 안의 파일·폴더·웹 링크: 경로와 href 변환, 종류 판정, 공유 경로 찾기.

화면 코드가 없는 순수 로직이다. 링크는 본문 HTML 에 ``<a href="file://...">``
로 저장한다. 폴더는 href 끝에 ``/`` 를 붙여 두어, 종류(아이콘)를 정할 때
파일 시스템이나 네트워크를 건드리지 않게 한다(그릴 때마다 꺼진 PC 의 공유
폴더를 찾아가면 화면이 멈춘다).

메모는 같은 내부망의 다른 사람이 쓴 것일 수 있다. 그래서 열 수 있는 주소를
파일·폴더와 http(s) 로만 제한하고, 실행 파일과 스크립트는 열기 전에 묻는다."""
from __future__ import annotations

import os
import platform
import re
from urllib.parse import quote, unquote, urlsplit

KIND_FOLDER = "folder"
KIND_WEB = "web"
KIND_PDF = "pdf"
KIND_WORD = "word"
KIND_HWP = "hwp"
KIND_SHEET = "sheet"
KIND_SLIDE = "slide"
KIND_TEXT = "text"
KIND_IMAGE = "image"
KIND_VIDEO = "video"
KIND_AUDIO = "audio"
KIND_ARCHIVE = "archive"
KIND_CODE = "code"
KIND_APP = "app"
KIND_FILE = "file"

KINDS = (
    KIND_FOLDER, KIND_WEB, KIND_PDF, KIND_WORD, KIND_HWP, KIND_SHEET,
    KIND_SLIDE, KIND_TEXT, KIND_IMAGE, KIND_VIDEO, KIND_AUDIO, KIND_ARCHIVE,
    KIND_CODE, KIND_APP, KIND_FILE,
)

_EXT_GROUPS = {
    KIND_PDF: "pdf",
    KIND_WORD: "doc docx docm dot dotx odt rtf",
    KIND_HWP: "hwp hwpx hwt hml",
    KIND_SHEET: "xls xlsx xlsm xlsb xlt xltx csv ods cell",
    KIND_SLIDE: "ppt pptx pptm pps ppsx odp key show",
    KIND_TEXT: "txt md log ini cfg conf",
    KIND_IMAGE: "png jpg jpeg gif bmp webp svg ico tif tiff heic psd ai",
    KIND_VIDEO: "mp4 mkv avi mov wmv webm m4v flv",
    KIND_AUDIO: "mp3 wav flac m4a ogg wma aac",
    KIND_ARCHIVE: "zip 7z rar tar gz tgz bz2 alz egg iso",
    KIND_CODE: ("py js ts jsx tsx html htm css json xml yml yaml sql c cpp h "
                "hpp cs java kt go rs php rb sh"),
    KIND_APP: ("exe msi bat cmd com scr pif ps1 vbs vbe js jse wsf wsh hta "
               "jar reg lnk url cpl msc appref-ms"),
}
EXT_KIND: dict[str, str] = {}
for _kind, _exts in _EXT_GROUPS.items():
    for _ext in _exts.split():
        # js 는 코드로도 실행 파일로도 걸린다. 여는 순간 실행되므로 실행 쪽이 이긴다.
        if _ext in EXT_KIND and _kind != KIND_APP:
            continue
        EXT_KIND[_ext] = _kind

# 여는 것이 곧 실행이거나 PC 설정을 바꾸는 형식. 열기 전에 경로를 보여 주고
# 묻는다. 아이콘은 코드나 문서로 보여도 연결 프로그램이 곧바로 실행하는 것
# (파이썬·셸 스크립트 등)도 함께 묻는다.
_RUNS_WHEN_OPENED = """
    msp mst ps1xml ps2 ps2xml psc1 psc2 psd1 psm1 cdxml pssc vb ws wsc sct
    jnlp scf inf ins isp application appinstaller appx appxbundle msix
    msixbundle gadget chm hlp rdp settingcontent-ms library-ms
    searchconnector-ms search-ms website theme themepack deskthemepack
    diagcab diagcfg diagpkg wsb xbap xll ade adp mde accde accdr
    py pyw pyz pyzw pyc pyo sh rb pl
"""
EXEC_EXTS = (frozenset(_EXT_GROUPS[KIND_APP].split())
             | frozenset(_RUNS_WHEN_OPENED.split()))

_WEB_SCHEMES = ("http", "https")
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


def is_web(href: str) -> bool:
    return (urlsplit(href or "").scheme or "").lower() in _WEB_SCHEMES


def is_file_href(href: str) -> bool:
    return (urlsplit(href or "").scheme or "").lower() == "file"


def is_supported(href: str) -> bool:
    """열어 줄 수 있는 주소인가. 그 밖의 스킴(프로토콜 처리기 등)은 열지 않는다."""
    return is_web(href) or is_file_href(href)


def is_unc(path: str) -> bool:
    p = (path or "").replace("/", "\\")
    return p.startswith("\\\\") and len(p) > 2 and p[2] not in "\\?."


def is_local_drive(path: str) -> bool:
    return bool(_DRIVE_RE.match(path or ""))


def path_to_href(path: str, is_dir: bool = False) -> str:
    """Windows 경로를 ``file:`` href 로 바꾼다. 폴더는 끝에 ``/`` 를 붙인다."""
    p = (path or "").strip().strip('"').replace("\\", "/")
    trailing = p.endswith("/")
    if p.startswith("//"):
        body = p[2:].strip("/")
        host, _, rest = body.partition("/")
        href = "file://" + quote(host, safe="") + "/" + quote(rest, safe="/")
    else:
        href = "file:///" + quote(p.strip("/"), safe="/:")
    if (is_dir or trailing) and not href.endswith("/"):
        href += "/"
    return href


def href_to_path(href: str) -> str | None:
    """``file:`` href 를 Windows 경로로 되돌린다. 파일 링크가 아니면 None."""
    if not is_file_href(href):
        return None
    parts = urlsplit(href)
    host = unquote(parts.netloc or "")
    path = unquote(parts.path or "")
    if host and host.lower() != "localhost":
        full = "//" + host + path
    else:
        # file:///C:/dir → C:/dir, file:////server/share → //server/share
        full = path
        if re.match(r"^/[A-Za-z]:", full):
            full = full[1:]
    win = full.replace("/", "\\")
    if len(win) > 3 and win.endswith("\\") and not win.endswith(":\\"):
        win = win.rstrip("\\")
    return win or None


def is_folder_href(href: str) -> bool:
    return is_file_href(href) and urlsplit(href).path.endswith("/")


def _ext_of(name: str) -> str:
    base = name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    # Windows 는 이름 끝의 점과 빈칸을 떼고 연다('a.cmd.' 와 'a.cmd ' 는 'a.cmd'
    # 다). 그대로 두면 실행 파일인데도 확장자가 없는 것으로 보여 묻지 않고 열린다.
    # 데이터 스트림 표기('a.cmd::$DATA')도 떼고 본다.
    base = base.split(":", 1)[0].rstrip(". ")
    if "." not in base.strip("."):
        return ""
    return base.rsplit(".", 1)[-1].lower()


def kind_of(href: str) -> str:
    """링크의 종류. 표시 글자가 아니라 실제 연결 대상의 주소만 본다."""
    if is_web(href):
        return KIND_WEB
    if not is_file_href(href):
        return KIND_FILE
    if is_folder_href(href):
        return KIND_FOLDER
    path = href_to_path(href) or ""
    return EXT_KIND.get(_ext_of(path), KIND_FILE)


def runs_when_opened(path: str) -> bool:
    """이 이름의 파일을 열면 곧바로 실행되거나 PC 설정이 바뀌는가."""
    return _ext_of(path or "") in EXEC_EXTS


def needs_confirm(href: str) -> bool:
    """여는 것이 곧 실행인 대상일 수 있는가(실행 파일·스크립트·바로 가기).

    이름만 본다. 링크 끝에 ``/`` 를 붙여 폴더라고 적어 두어도 믿지 않는다
    (``\\\\서버\\공유\\a.bat/`` 처럼 적으면 폴더 아이콘으로 보이지만 열면
    실행된다). 정말 폴더인지는 열 때 확인한다(``link_open``)."""
    if not is_file_href(href):
        return False
    return runs_when_opened(href_to_path(href) or "")


def display_name(href: str) -> str:
    """표시할 내용을 비워 두었을 때 쓸 이름(파일 이름, 폴더 이름, 사이트 주소)."""
    if is_web(href):
        parts = urlsplit(href)
        tail = parts.path.rstrip("/")
        return (parts.netloc + tail) if tail else parts.netloc
    path = href_to_path(href) or ""
    name = path.rstrip("\\").rsplit("\\", 1)[-1]
    return name or path


def describe_target(href: str) -> str:
    """사람이 읽을 연결 대상(말풍선·확인 창에 쓴다)."""
    if is_file_href(href):
        return href_to_path(href) or href
    return href


def parse_target(text: str, is_dir: bool | None = None) -> str | None:
    """사용자가 적거나 붙여 넣은 경로·주소를 href 로 바꾼다. 알 수 없으면 None.

    받는 꼴: ``C:\\폴더\\파일``, ``\\\\PC\\공유\\파일``, ``file://...``,
    ``http(s)://...``, ``www.…``. 그 밖의 스킴은 받지 않는다."""
    raw = (text or "").strip().strip('"').strip()
    if not raw:
        return None
    low = raw.lower()
    if low.startswith(("http://", "https://")):
        return raw
    if low.startswith("www."):
        return "https://" + raw
    if low.startswith("file:"):
        path = href_to_path(raw)
        if not path:
            return None
        return path_to_href(path, is_dir=raw.rstrip().endswith("/")
                            if is_dir is None else is_dir)
    if is_unc(raw) or is_local_drive(raw):
        folder = raw.endswith(("\\", "/")) if is_dir is None else is_dir
        return path_to_href(raw, is_dir=folder)
    if _SCHEME_RE.match(raw):
        return None
    return None


# ── 다른 PC 에서도 열리는 경로 찾기 ─────────────────────────────────────

def mapped_drive_unc(path: str) -> str | None:
    """네트워크 드라이브(Z:\\…)의 경로를 ``\\\\서버\\공유\\…`` 로 바꾼다."""
    if platform.system() != "Windows" or not is_local_drive(path):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        drive = path[:2]
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        rc = ctypes.windll.mpr.WNetGetConnectionW(drive, buf, ctypes.byref(size))
        if rc != 0 or not buf.value:
            return None
        return buf.value.rstrip("\\") + "\\" + path[3:].lstrip("\\/")
    except Exception:
        return None


def local_shares() -> list[tuple[str, str]]:
    """이 PC 가 공유해 둔 폴더 ``(공유 이름, 로컬 경로)``. 관리용 공유(C$ 등)와
    프린터 공유는 뺀다. 관리자 권한 없이 읽히는 레지스트리에서 가져온다."""
    if platform.system() != "Windows":
        return []
    shares: list[tuple[str, str]] = []
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Services\LanmanServer\Shares")
    except OSError:
        return []
    try:
        index = 0
        while True:
            try:
                name, value, _type = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if not name or name.endswith("$"):
                continue
            items = value if isinstance(value, (list, tuple)) else [value]
            fields = {}
            for item in items:
                k, sep, v = str(item).partition("=")
                if sep:
                    fields[k.strip().lower()] = v.strip()
            if fields.get("type", "0") not in ("0", ""):
                continue
            local = fields.get("path", "")
            if local:
                shares.append((name, local))
    finally:
        winreg.CloseKey(key)
    return shares


def share_unc_for(path: str, shares: list[tuple[str, str]] | None = None,
                  computer: str | None = None) -> str | None:
    """``path`` 가 이 PC 의 공유 폴더 안에 있으면 그 네트워크 경로를 돌려준다.
    여러 공유에 걸리면 가장 깊은(구체적인) 공유를 고른다."""
    if not is_local_drive(path):
        return None
    if shares is None:
        shares = local_shares()
    if computer is None:
        computer = os.environ.get("COMPUTERNAME") or platform.node()
    if not computer:
        return None
    norm = os.path.normcase(os.path.normpath(path))
    best: tuple[int, str] | None = None
    for name, local in shares:
        root = os.path.normcase(os.path.normpath(local))
        if norm == root or norm.startswith(root.rstrip("\\") + "\\"):
            rest = os.path.normpath(path)[len(root.rstrip("\\")):].lstrip("\\")
            unc = "\\\\" + computer + "\\" + name
            if rest:
                unc += "\\" + rest
            if best is None or len(root) > best[0]:
                best = (len(root), unc)
    return best[1] if best else None


def network_path_for(path: str) -> str | None:
    """다른 PC 에서도 열 수 있는 경로가 있으면 돌려준다(이미 네트워크 경로이면
    그대로). 없으면 None. 이 PC 에서만 통하는 경로라는 뜻이다."""
    if is_unc(path):
        return path
    return mapped_drive_unc(path) or share_unc_for(path)
