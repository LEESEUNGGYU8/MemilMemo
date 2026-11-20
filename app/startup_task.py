"""스토어판의 자동 실행: 패키지의 시작 작업(StartupTask)을 켜고 끈다.

패키지 앱이 레지스트리에 적는 시작 프로그램 값은 Windows 에 보이지 않는다.
대신 매니페스트에 적어 둔 시작 작업을 Windows 런타임(WinRT)으로 켜고 끈다.

* 사용자가 Windows 설정의 시작 앱이나 작업 관리자에서 끈 것은 앱이 다시 켤 수
  없다(``DISABLED_BY_USER``). 조직 정책으로 정해진 것도 마찬가지다.
* 패키지 밖(설치판)에서는 쓸 수 없다. 모든 함수가 ``None`` 을 돌려준다.

WinRT 는 ctypes 로 직접 부른다(패키지를 하나 더 묶지 않으려고). 인터페이스의
식별값과 메서드 순서는 Windows SDK 의 windows.applicationmodel.idl 에서 옮겼다."""
from __future__ import annotations

import ctypes
import sys
import threading
import time

from . import packaging

# 매니페스트(tools/make_msix.py)에 적은 것과 같아야 한다.
TASK_ID = "MemilMemoStartup"

DISABLED = 0
DISABLED_BY_USER = 1
ENABLED = 2
DISABLED_BY_POLICY = 3
ENABLED_BY_POLICY = 4

_ON = (ENABLED, ENABLED_BY_POLICY)
# 앱이 바꿀 수 없는 상태.
_LOCKED = (DISABLED_BY_USER, DISABLED_BY_POLICY, ENABLED_BY_POLICY)

_TIMEOUT = 6.0
_S_OK = 0
_RO_INIT_MULTITHREADED = 1
_ASYNC_STARTED, _ASYNC_COMPLETED = 0, 1


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16),
                ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        p = text.strip("{}").split("-")
        tail = bytes.fromhex(p[3] + p[4])
        return cls(int(p[0], 16), int(p[1], 16), int(p[2], 16),
                   (ctypes.c_ubyte * 8)(*tail))


_IID_STATICS = _GUID.of("EE5B60BD-A148-41A7-B26E-E8B88A1E62F8")   # IStartupTaskStatics
_IID_ASYNC_INFO = _GUID.of("00000036-0000-0000-C000-000000000046")  # IAsyncInfo

# 가상 함수표의 자리. IInspectable 이 0~5 를 쓴다.
_QUERY_INTERFACE, _RELEASE = 0, 2
_STATICS_GET_ASYNC = 7              # IStartupTaskStatics.GetAsync
_TASK_REQUEST_ENABLE = 6            # IStartupTask.RequestEnableAsync
_TASK_DISABLE = 7                   # IStartupTask.Disable
_TASK_GET_STATE = 8                 # IStartupTask.get_State
_ASYNC_INFO_GET_STATUS = 7          # IAsyncInfo.get_Status
_ASYNC_GET_RESULTS = 8              # IAsyncOperation<T>.GetResults


class _Failed(Exception):
    pass


def _method(ptr: ctypes.c_void_p, index: int, *argtypes):
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    func = proto(vtable.contents[index])
    return lambda *args: func(ptr, *args)


def _check(hr: int, what: str) -> None:
    if hr != _S_OK:
        raise _Failed(f"{what}: {hr & 0xFFFFFFFF:#010x}")


def _release(ptr) -> None:
    if ptr:
        _method(ptr, _RELEASE)()


def _wait(operation: ctypes.c_void_p) -> None:
    """비동기 작업이 끝날 때까지 기다린다."""
    info = ctypes.c_void_p()
    _check(_method(operation, _QUERY_INTERFACE, ctypes.POINTER(_GUID),
                   ctypes.POINTER(ctypes.c_void_p))(
        ctypes.byref(_IID_ASYNC_INFO), ctypes.byref(info)), "IAsyncInfo")
    try:
        get_status = _method(info, _ASYNC_INFO_GET_STATUS,
                             ctypes.POINTER(ctypes.c_int32))
        status = ctypes.c_int32(_ASYNC_STARTED)
        end = time.monotonic() + _TIMEOUT
        while True:
            _check(get_status(ctypes.byref(status)), "get_Status")
            if status.value != _ASYNC_STARTED:
                break
            if time.monotonic() > end:
                raise _Failed("timeout")
            time.sleep(0.01)
        if status.value != _ASYNC_COMPLETED:
            raise _Failed(f"async status {status.value}")
    finally:
        _release(info)


def _with_task(action):
    """시작 작업 객체를 얻어 ``action(task)`` 의 값을 돌려준다(이 스레드에서)."""
    combase = ctypes.WinDLL("combase")
    combase.RoInitialize.argtypes = [ctypes.c_int]
    combase.RoInitialize.restype = ctypes.c_long
    combase.WindowsCreateString.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    combase.WindowsCreateString.restype = ctypes.c_long
    combase.WindowsDeleteString.argtypes = [ctypes.c_void_p]
    combase.RoGetActivationFactory.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    combase.RoGetActivationFactory.restype = ctypes.c_long

    inited = combase.RoInitialize(_RO_INIT_MULTITHREADED) in (0, 1)   # S_OK, S_FALSE
    name = ctypes.c_void_p()
    task_id = ctypes.c_void_p()
    factory = ctypes.c_void_p()
    operation = ctypes.c_void_p()
    task = ctypes.c_void_p()
    try:
        cls = "Windows.ApplicationModel.StartupTask"
        _check(combase.WindowsCreateString(cls, len(cls), ctypes.byref(name)),
               "WindowsCreateString")
        _check(combase.WindowsCreateString(TASK_ID, len(TASK_ID),
                                           ctypes.byref(task_id)),
               "WindowsCreateString")
        _check(combase.RoGetActivationFactory(
            name, ctypes.byref(_IID_STATICS), ctypes.byref(factory)),
            "RoGetActivationFactory")
        _check(_method(factory, _STATICS_GET_ASYNC, ctypes.c_void_p,
                       ctypes.POINTER(ctypes.c_void_p))(
            task_id, ctypes.byref(operation)), "GetAsync")
        _wait(operation)
        _check(_method(operation, _ASYNC_GET_RESULTS,
                       ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(task)),
               "GetResults")
        return action(task)
    finally:
        for ptr in (task, operation, factory):
            _release(ptr)
        for text in (task_id, name):
            if text:
                combase.WindowsDeleteString(text)
        if inited:
            combase.RoUninitialize()


def _state_of(task: ctypes.c_void_p) -> int:
    value = ctypes.c_int32(-1)
    _check(_method(task, _TASK_GET_STATE, ctypes.POINTER(ctypes.c_int32))(
        ctypes.byref(value)), "get_State")
    return int(value.value)


def _enable(task: ctypes.c_void_p) -> int:
    operation = ctypes.c_void_p()
    _check(_method(task, _TASK_REQUEST_ENABLE, ctypes.POINTER(ctypes.c_void_p))(
        ctypes.byref(operation)), "RequestEnableAsync")
    try:
        _wait(operation)
        value = ctypes.c_int32(-1)
        _check(_method(operation, _ASYNC_GET_RESULTS,
                       ctypes.POINTER(ctypes.c_int32))(ctypes.byref(value)),
               "GetResults")
        return int(value.value)
    finally:
        _release(operation)


def _disable(task: ctypes.c_void_p) -> int:
    _check(_method(task, _TASK_DISABLE)(), "Disable")
    return _state_of(task)


def _run(action) -> int | None:
    """WinRT 호출을 따로 띄운 스레드에서 한다.

    부르는 쪽(화면 스레드)의 COM 방식과 섞이지 않고, 응답이 없어도 화면이
    오래 멈추지 않는다."""
    if sys.platform != "win32" or not packaging.is_packaged():
        return None
    box: list[int | None] = [None]

    def work() -> None:
        try:
            box[0] = _with_task(action)
        except (_Failed, OSError, ValueError, ctypes.ArgumentError):
            box[0] = None

    thread = threading.Thread(target=work, name="startup-task", daemon=True)
    thread.start()
    thread.join(_TIMEOUT + 2.0)
    return box[0]


def state() -> int | None:
    """지금 상태. 알 수 없으면 ``None``."""
    return _run(_state_of)


def is_enabled() -> bool:
    return state() in _ON


def can_change() -> bool:
    """앱이 켜고 끌 수 있는가. 사용자가 Windows 에서 껐거나 정책으로 정해졌으면
    바꿀 수 없다."""
    current = state()
    return current is not None and current not in _LOCKED


def set_enabled(on: bool) -> bool:
    """켜거나 끈다. 바란 대로 됐으면 참."""
    after = _run(_enable if on else _disable)
    if after is None:
        return False
    return (after in _ON) == bool(on)

