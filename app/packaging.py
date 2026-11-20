"""이 실행이 MSIX 패키지(스토어판) 안에서 도는가.

패키지 앱은 설치 폴더가 읽기 전용이고, AppData 와 HKCU 에 쓰는 것이 앱 전용
자리로 돌려진다. 돌려진 것은 다른 프로그램(탐색기, Windows 의 시작 프로그램
목록, 그림 뷰어)이 보지 못하고 앱을 제거하면 지워진다. 그래서 스토어판은

* 메모를 AppData 밖에 두고(``config.app_data_dir``),
* 레지스트리에 적어서 되는 기능(자동 실행 값, 우클릭 메뉴)을 쓰지 않고,
* 새 버전을 스스로 받아 설치하지 않는다(스토어가 한다).

설치판에서는 여기 값이 늘 거짓이라 아무것도 달라지지 않는다."""
from __future__ import annotations

import ctypes
import sys
from functools import lru_cache

_ERROR_INSUFFICIENT_BUFFER = 122


@lru_cache(maxsize=1)
def package_full_name() -> str:
    """패키지 전체 이름. 패키지 밖에서 돌면 빈 글자다."""
    if sys.platform != "win32":
        return ""
    try:
        get_name = ctypes.windll.kernel32.GetCurrentPackageFullName
        get_name.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p]
        get_name.restype = ctypes.c_long
        length = ctypes.c_uint32(0)
        # 패키지 밖이면 APPMODEL_ERROR_NO_PACKAGE(15700)가 온다.
        if get_name(ctypes.byref(length), None) != _ERROR_INSUFFICIENT_BUFFER:
            return ""
        buf = ctypes.create_unicode_buffer(length.value)
        if get_name(ctypes.byref(length), buf) != 0:
            return ""
        return buf.value
    except Exception:
        return ""


def is_packaged() -> bool:
    return bool(package_full_name())
