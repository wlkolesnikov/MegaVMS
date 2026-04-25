from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
import os
import random
import threading
import time
from pathlib import Path
import tempfile
from typing import Any
import xml.etree.ElementTree as ET

from contracts import (
    ArchiveCoverageReport,
    ArchiveFile,
    ArchiveSegment,
    ChannelInfo,
    ConnectionParams,
    DiagnosticReport,
    PluginCapabilities,
    RuntimeConfig,
    SnapshotResult,
    StreamProfile,
    VideoHostBinding,
    ZoomState,
)

logger = logging.getLogger(__name__)
DEFAULT_LIB_DIR = Path(os.environ.get("HIKVISION_LIB_DIR", Path.home() / ".local/lib/hikvision"))


def validate_sdk_layout(lib_dir: Path | None = None) -> list[str]:
    base = Path(lib_dir or DEFAULT_LIB_DIR)
    missing: list[str] = []
    required = [
        base / "libhcnetsdk.so",
        base / "libPlayCtrl.so",
        base / "libHCCore.so",
        base / "libhpr.so",
        base / "HCNetSDKCom",
    ]
    for entry in required:
        if not entry.exists():
            missing.append(str(entry))
    return missing


NET_DVR_PLAYSTART = 1
NET_DVR_PLAYSTOP = 2
NET_DVR_PLAYPAUSE = 3
NET_DVR_PLAYRESTART = 4
NET_DVR_PLAYFAST = 5
NET_DVR_PLAYSLOW = 6
NET_DVR_PLAYNORMAL = 7
NET_DVR_PLAYFRAME = 8
NET_DVR_KEEPALIVE = 25
NET_DVR_PLAYSETTIME = 26
NET_DVR_CHANGEWNDRESOLUTION = 36

SDK_ERROR_MESSAGES = {
    64: (
        "NET_DVR_LOADPLAYERSDKFAILED: HCNetSDK could not load Player SDK "
        "(libPlayCtrl.so or its dependencies). Ensure the process starts with "
        "the Hikvision lib directory and HCNetSDKCom in LD_LIBRARY_PATH."
    ),
}

LONG = ctypes.c_long
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
BYTE = ctypes.c_ubyte
HWND = ctypes.c_void_p
BOOL = ctypes.c_int
REALDATACALLBACK = ctypes.CFUNCTYPE(None, LONG, DWORD, ctypes.POINTER(BYTE), DWORD, ctypes.c_void_p)


class SDKLoginError(RuntimeError):
    def __init__(self, error_code: int, message: str | None = None) -> None:
        self.error_code = int(error_code)
        super().__init__(message or f"HCNetSDK login failed: error={self.error_code}")


class NET_DVR_STREAM_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", DWORD),
        ("byID", BYTE * 32),
        ("dwChannel", DWORD),
        ("byRes", BYTE * 32),
    ]


class NET_DVR_DEVICEINFO_V30(ctypes.Structure):
    _fields_ = [
        ("sSerialNumber", BYTE * 48),
        ("byAlarmInPortNum", BYTE),
        ("byAlarmOutPortNum", BYTE),
        ("byDiskNum", BYTE),
        ("byDVRType", BYTE),
        ("byChanNum", BYTE),
        ("byStartChan", BYTE),
        ("byAudioChanNum", BYTE),
        ("byIPChanNum", BYTE),
        ("byZeroChanNum", BYTE),
        ("byMainProto", BYTE),
        ("bySubProto", BYTE),
        ("bySupport", BYTE),
        ("bySupport1", BYTE),
        ("bySupport2", BYTE),
        ("wDevType", WORD),
        ("bySupport3", BYTE),
        ("byMultiStreamProto", BYTE),
        ("byStartDChan", BYTE),
        ("byStartDTalkChan", BYTE),
        ("byHighDChanNum", BYTE),
        ("bySupport4", BYTE),
        ("byLanguageType", BYTE),
        ("byVoiceInChanNum", BYTE),
        ("byStartVoiceInChanNo", BYTE),
        ("bySupport5", BYTE),
        ("bySupport6", BYTE),
        ("byMirrorChanNum", BYTE),
        ("wStartMirrorChanNo", WORD),
        ("bySupport7", BYTE),
        ("byRes2", BYTE),
    ]


class NET_DVR_DEVICEINFO_V40(ctypes.Structure):
    _fields_ = [
        ("struDeviceV30", NET_DVR_DEVICEINFO_V30),
        ("bySupportLock", BYTE),
        ("byRetryLoginTime", BYTE),
        ("byPasswordLevel", BYTE),
        ("byProxyType", BYTE),
        ("dwSurplusLockTime", DWORD),
        ("byCharEncodeType", BYTE),
        ("bySupportDev5", BYTE),
        ("bySupport", BYTE),
        ("byLoginMode", BYTE),
        ("dwOEMCode", DWORD),
        ("iResidualValidity", ctypes.c_int),
        ("byResidualValidity", BYTE),
        ("bySingleStartDTalkChan", BYTE),
        ("bySingleDTalkChanNums", BYTE),
        ("byPassWordResetLevel", BYTE),
        ("bySupportStreamEncrypt", BYTE),
        ("byMarketType", BYTE),
        ("byTLSCap", BYTE),
        ("byRes2", BYTE * 237),
    ]


class NET_DVR_USER_LOGIN_INFO(ctypes.Structure):
    _fields_ = [
        ("sDeviceAddress", ctypes.c_char * 129),
        ("byUseTransport", BYTE),
        ("wPort", WORD),
        ("sUserName", ctypes.c_char * 64),
        ("sPassword", ctypes.c_char * 64),
        ("cbLoginResult", ctypes.c_void_p),
        ("pUser", ctypes.c_void_p),
        ("bUseAsynLogin", BOOL),
        ("byProxyType", BYTE),
        ("byUseUTCTime", BYTE),
        ("byLoginMode", BYTE),
        ("byHttps", BYTE),
        ("iProxyID", LONG),
        ("byVerifyMode", BYTE),
        ("byRes3", BYTE * 119),
    ]


class NET_DVR_TIME(ctypes.Structure):
    _fields_ = [
        ("dwYear", ctypes.c_uint32),
        ("dwMonth", ctypes.c_uint32),
        ("dwDay", ctypes.c_uint32),
        ("dwHour", ctypes.c_uint32),
        ("dwMinute", ctypes.c_uint32),
        ("dwSecond", ctypes.c_uint32),
    ]


class NET_DVR_FILECOND(ctypes.Structure):
    _fields_ = [
        ("lChannel", LONG),
        ("dwFileType", DWORD),
        ("dwIsLocked", DWORD),
        ("dwUseCardNo", DWORD),
        ("sCardNumber", ctypes.c_char * 32),
        ("struStartTime", NET_DVR_TIME),
        ("struStopTime", NET_DVR_TIME),
    ]


class NET_DVR_FINDDATA_V30(ctypes.Structure):
    _fields_ = [
        ("sFileName", ctypes.c_char * 100),
        ("struStartTime", NET_DVR_TIME),
        ("struStopTime", NET_DVR_TIME),
        ("dwFileSize", DWORD),
        ("sCardNum", ctypes.c_char * 32),
        ("byLocked", BYTE),
        ("byFileType", BYTE),
        ("byRes", BYTE * 2),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class PLAYM4_SYSTEM_TIME(ctypes.Structure):
    _fields_ = [
        ("dwYear", ctypes.c_uint32),
        ("dwMon", ctypes.c_uint32),
        ("dwDay", ctypes.c_uint32),
        ("dwHour", ctypes.c_uint32),
        ("dwMin", ctypes.c_uint32),
        ("dwSec", ctypes.c_uint32),
        ("dwMs", ctypes.c_uint32),
    ]


@dataclass(frozen=True)
class PictureSize:
    width: int
    height: int


class PlayCtrl:
    def __init__(self, lib_dir: Path | None = None) -> None:
        base = Path(lib_dir or DEFAULT_LIB_DIR)
        lib_path = base / "libPlayCtrl.so"
        mode = getattr(ctypes, "RTLD_GLOBAL", 0) | getattr(ctypes, "RTLD_LAZY", 0)

        self._lib = None
        try:
            global_lib = ctypes.CDLL(None)
            has_picture = hasattr(global_lib, "PlayM4_GetPictureSize")
            has_region = hasattr(global_lib, "PlayM4_SetDisplayRegion") or hasattr(global_lib, "PlayM4_SetDisplayRegionOnWnd")
            if has_picture and has_region:
                self._lib = global_lib
        except Exception:
            self._lib = None

        if self._lib is None:
            noload = int(getattr(os, "RTLD_NOLOAD", 0))
            for candidate in ("libPlayCtrl.so", str(lib_path)):
                try:
                    self._lib = ctypes.CDLL(candidate, mode=mode | noload)
                    break
                except OSError:
                    self._lib = None

        if self._lib is None:
            self._lib = ctypes.CDLL(str(lib_path), mode=mode)

        self._lib.PlayM4_GetPictureSize.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        self._lib.PlayM4_GetPictureSize.restype = ctypes.c_int

        if hasattr(self._lib, "PlayM4_SetDisplayRegionOnWnd"):
            self._lib.PlayM4_SetDisplayRegionOnWnd.argtypes = [ctypes.c_int, ctypes.POINTER(RECT)]
            self._lib.PlayM4_SetDisplayRegionOnWnd.restype = ctypes.c_int

        if hasattr(self._lib, "PlayM4_SetDisplayRegion"):
            self._lib.PlayM4_SetDisplayRegion.argtypes = [
                ctypes.c_int,
                ctypes.c_uint,
                ctypes.POINTER(RECT),
                ctypes.c_void_p,
                ctypes.c_int,
            ]
            self._lib.PlayM4_SetDisplayRegion.restype = ctypes.c_int

        if hasattr(self._lib, "PlayM4_RefreshPlay"):
            self._lib.PlayM4_RefreshPlay.argtypes = [ctypes.c_int]
            self._lib.PlayM4_RefreshPlay.restype = ctypes.c_int

        if hasattr(self._lib, "PlayM4_GetSystemTime"):
            self._lib.PlayM4_GetSystemTime.argtypes = [ctypes.c_int, ctypes.POINTER(PLAYM4_SYSTEM_TIME)]
            self._lib.PlayM4_GetSystemTime.restype = ctypes.c_int

        if hasattr(self._lib, "PlayM4_GetLastError"):
            self._lib.PlayM4_GetLastError.argtypes = [ctypes.c_int]
            self._lib.PlayM4_GetLastError.restype = ctypes.c_uint32

    def get_picture_size(self, port: int) -> PictureSize | None:
        width = ctypes.c_int(0)
        height = ctypes.c_int(0)
        try:
            ret = int(self._lib.PlayM4_GetPictureSize(int(port), ctypes.byref(width), ctypes.byref(height)))
        except Exception:
            return None
        if ret != 1 or width.value <= 0 or height.value <= 0:
            return None
        return PictureSize(int(width.value), int(height.value))

    def find_active_port(self, *, first: int = 0, last: int = 4) -> int | None:
        for port in range(int(first), int(last) + 1):
            size = self.get_picture_size(port)
            if size is not None and size.width > 0:
                return port
            if self.get_system_time(port) is not None:
                return port
        return None

    def set_display_region(self, port: int, *, rect: RECT | None, hwnd: int, enable: bool) -> bool:
        if hasattr(self._lib, "PlayM4_SetDisplayRegionOnWnd"):
            rect_to_apply: RECT | None = rect
            if not enable or rect is None:
                try:
                    ret = int(self._lib.PlayM4_SetDisplayRegionOnWnd(int(port), None))
                except Exception:
                    ret = 0
                if ret == 1:
                    return True
                size = self.get_picture_size(port)
                if size is None:
                    return False
                rect_to_apply = RECT(left=0, top=0, right=int(size.width), bottom=int(size.height))

            try:
                ret = int(self._lib.PlayM4_SetDisplayRegionOnWnd(int(port), ctypes.byref(rect_to_apply)))
            except Exception as exc:
                logger.debug("PlayM4_SetDisplayRegionOnWnd failed: %s", exc)
                ret = 0
            if ret == 1:
                return True

        if not hasattr(self._lib, "PlayM4_SetDisplayRegion"):
            return False
        if hwnd <= 0:
            return False
        rect_ptr = ctypes.byref(rect) if rect is not None else None
        try:
            ret = int(
                self._lib.PlayM4_SetDisplayRegion(
                    int(port),
                    ctypes.c_uint(0),
                    rect_ptr,
                    ctypes.c_void_p(int(hwnd)),
                    ctypes.c_int(1 if enable else 0),
                )
            )
        except Exception as exc:
            logger.debug("PlayM4_SetDisplayRegion failed: %s", exc)
            return False
        return ret == 1

    def refresh_play(self, port: int) -> bool:
        if not hasattr(self._lib, "PlayM4_RefreshPlay"):
            return False
        try:
            ret = int(self._lib.PlayM4_RefreshPlay(int(port)))
        except Exception:
            return False
        return ret == 1

    def get_last_error(self, port: int) -> int | None:
        if not hasattr(self._lib, "PlayM4_GetLastError"):
            return None
        try:
            return int(self._lib.PlayM4_GetLastError(int(port)))
        except Exception:
            return None

    def get_system_time(self, port: int) -> datetime | None:
        if not hasattr(self._lib, "PlayM4_GetSystemTime"):
            return None
        st = PLAYM4_SYSTEM_TIME()
        try:
            ret = int(self._lib.PlayM4_GetSystemTime(int(port), ctypes.byref(st)))
        except Exception:
            return None
        if ret != 1:
            return None
        try:
            return datetime(
                int(st.dwYear),
                int(st.dwMon),
                int(st.dwDay),
                int(st.dwHour),
                int(st.dwMin),
                int(st.dwSec),
                int(st.dwMs) * 1000,
            )
        except Exception:
            return None


@dataclass(frozen=True)
class NativeArchiveItem:
    filename: str
    start_time: datetime | None
    end_time: datetime | None
    file_size: int


def _decode_zero_terminated(raw_value: bytes | ctypes.Array[Any]) -> str:
    raw = bytes(raw_value)
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="ignore").strip()


def _datetime_to_sdk_time(value: datetime) -> NET_DVR_TIME:
    sdk_time = NET_DVR_TIME()
    sdk_time.dwYear = int(value.year)
    sdk_time.dwMonth = int(value.month)
    sdk_time.dwDay = int(value.day)
    sdk_time.dwHour = int(value.hour)
    sdk_time.dwMinute = int(value.minute)
    sdk_time.dwSecond = int(value.second)
    return sdk_time


def _sdk_time_to_datetime(value: NET_DVR_TIME) -> datetime | None:
    try:
        return datetime(
            int(value.dwYear),
            int(value.dwMonth),
            int(value.dwDay),
            int(value.dwHour),
            int(value.dwMinute),
            int(value.dwSecond),
        )
    except Exception:
        return None


class NativeDeviceSession:
    def __init__(self, sdk_wrapper: "NativeHCNetSDK", user_id: int, device_info: NET_DVR_DEVICEINFO_V40) -> None:
        self._sdk = sdk_wrapper
        self.user_id = int(user_id)
        self._device_info = device_info
        self._callbacks: list[Any] = []
        info_v30 = device_info.struDeviceV30
        self.start_channel = int(info_v30.byStartChan or 1)
        self.channel_count = int(info_v30.byChanNum)
        self.ip_channel_count = int(info_v30.byIPChanNum) + int(info_v30.byHighDChanNum) * 256
        self.serial_number = _decode_zero_terminated(info_v30.sSerialNumber)
        self.serial = self.serial_number
        self.model = ""
        self.device_model = ""

    def logout(self) -> None:
        self._sdk.logout(self.user_id)

    def find_files(
        self,
        *,
        channel: int,
        start_time: datetime,
        end_time: datetime,
        file_type: int,
        stream_type: int,
    ) -> list[NativeArchiveItem]:
        return self._sdk.find_files(
            user_id=self.user_id,
            channel=channel,
            start_time=start_time,
            end_time=end_time,
            file_type=file_type,
            stream_type=stream_type,
        )


class NativeHCNetSDK:
    def __init__(self, lib_dir: Path | None = None) -> None:
        base = Path(lib_dir or DEFAULT_LIB_DIR)
        lib_path = base / "libhcnetsdk.so"
        mode = getattr(ctypes, "RTLD_GLOBAL", 0) | getattr(ctypes, "RTLD_LAZY", 0)
        self._sdk = ctypes.CDLL(str(lib_path), mode=mode)
        self._configure_prototypes()

    def _configure_prototypes(self) -> None:
        self._sdk.NET_DVR_Init.argtypes = []
        self._sdk.NET_DVR_Init.restype = ctypes.c_bool
        self._sdk.NET_DVR_Cleanup.argtypes = []
        self._sdk.NET_DVR_Cleanup.restype = ctypes.c_bool
        self._sdk.NET_DVR_GetLastError.argtypes = []
        self._sdk.NET_DVR_GetLastError.restype = DWORD

        if hasattr(self._sdk, "NET_DVR_SetConnectTime"):
            self._sdk.NET_DVR_SetConnectTime.argtypes = [DWORD, DWORD]
            self._sdk.NET_DVR_SetConnectTime.restype = ctypes.c_bool
        if hasattr(self._sdk, "NET_DVR_SetReconnect"):
            self._sdk.NET_DVR_SetReconnect.argtypes = [DWORD, ctypes.c_bool]
            self._sdk.NET_DVR_SetReconnect.restype = ctypes.c_bool

        if hasattr(self._sdk, "NET_DVR_Login_V40"):
            self._sdk.NET_DVR_Login_V40.argtypes = [
                ctypes.POINTER(NET_DVR_USER_LOGIN_INFO),
                ctypes.POINTER(NET_DVR_DEVICEINFO_V40),
            ]
            self._sdk.NET_DVR_Login_V40.restype = LONG
        if hasattr(self._sdk, "NET_DVR_Login_V30"):
            self._sdk.NET_DVR_Login_V30.argtypes = [
                ctypes.c_char_p,
                WORD,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.POINTER(NET_DVR_DEVICEINFO_V30),
            ]
            self._sdk.NET_DVR_Login_V30.restype = LONG

        self._sdk.NET_DVR_Logout.argtypes = [LONG]
        self._sdk.NET_DVR_Logout.restype = ctypes.c_bool

        if hasattr(self._sdk, "NET_DVR_FindFile_V30"):
            self._sdk.NET_DVR_FindFile_V30.argtypes = [LONG, ctypes.POINTER(NET_DVR_FILECOND)]
            self._sdk.NET_DVR_FindFile_V30.restype = LONG
        if hasattr(self._sdk, "NET_DVR_FindNextFile_V30"):
            self._sdk.NET_DVR_FindNextFile_V30.argtypes = [LONG, ctypes.POINTER(NET_DVR_FINDDATA_V30)]
            self._sdk.NET_DVR_FindNextFile_V30.restype = LONG
        if hasattr(self._sdk, "NET_DVR_FindClose_V30"):
            self._sdk.NET_DVR_FindClose_V30.argtypes = [LONG]
            self._sdk.NET_DVR_FindClose_V30.restype = ctypes.c_bool

    def init(self) -> None:
        ok = bool(self._sdk.NET_DVR_Init())
        if not ok:
            raise RuntimeError(f"NET_DVR_Init failed: error={self.get_last_error()}")
        if hasattr(self._sdk, "NET_DVR_SetConnectTime"):
            self._sdk.NET_DVR_SetConnectTime(DWORD(2000), DWORD(1))
        if hasattr(self._sdk, "NET_DVR_SetReconnect"):
            self._sdk.NET_DVR_SetReconnect(DWORD(10_000), True)

    def cleanup(self) -> None:
        try:
            self._sdk.NET_DVR_Cleanup()
        except Exception:
            pass

    def get_last_error(self) -> int:
        try:
            return int(self._sdk.NET_DVR_GetLastError())
        except Exception:
            return -1

    def logout(self, user_id: int) -> None:
        self._sdk.NET_DVR_Logout(LONG(int(user_id)))

    def login(self, host: str, port: int, username: str, password: str) -> NativeDeviceSession:
        host_bytes = str(host).encode("utf-8")
        username_bytes = str(username).encode("utf-8")
        password_bytes = str(password).encode("utf-8")

        if hasattr(self._sdk, "NET_DVR_Login_V40"):
            login_info = NET_DVR_USER_LOGIN_INFO()
            ctypes.memset(ctypes.byref(login_info), 0, ctypes.sizeof(login_info))
            login_info.sDeviceAddress = host_bytes[:128]
            login_info.wPort = int(port)
            login_info.sUserName = username_bytes[:63]
            login_info.sPassword = password_bytes[:63]
            login_info.bUseAsynLogin = 0
            login_info.byLoginMode = 2
            login_info.byHttps = 0

            device_info_v40 = NET_DVR_DEVICEINFO_V40()
            ctypes.memset(ctypes.byref(device_info_v40), 0, ctypes.sizeof(device_info_v40))
            user_id = int(self._sdk.NET_DVR_Login_V40(ctypes.byref(login_info), ctypes.byref(device_info_v40)))
            if user_id >= 0:
                return NativeDeviceSession(self, user_id, device_info_v40)
            v40_error = self.get_last_error()
        else:
            v40_error = -1

        if hasattr(self._sdk, "NET_DVR_Login_V30"):
            device_info_v30 = NET_DVR_DEVICEINFO_V30()
            ctypes.memset(ctypes.byref(device_info_v30), 0, ctypes.sizeof(device_info_v30))
            user_id = int(
                self._sdk.NET_DVR_Login_V30(
                    host_bytes,
                    WORD(int(port)),
                    username_bytes,
                    password_bytes,
                    ctypes.byref(device_info_v30),
                )
            )
            if user_id >= 0:
                device_info_v40 = NET_DVR_DEVICEINFO_V40()
                ctypes.memset(ctypes.byref(device_info_v40), 0, ctypes.sizeof(device_info_v40))
                device_info_v40.struDeviceV30 = device_info_v30
                return NativeDeviceSession(self, user_id, device_info_v40)

        error_code = self.get_last_error()
        if error_code < 0:
            error_code = v40_error
        raise SDKLoginError(error_code, f"HCNetSDK login failed: error={error_code}")

    def find_files(
        self,
        *,
        user_id: int,
        channel: int,
        start_time: datetime,
        end_time: datetime,
        file_type: int,
        stream_type: int,
    ) -> list[NativeArchiveItem]:
        del stream_type
        if not hasattr(self._sdk, "NET_DVR_FindFile_V30"):
            raise RuntimeError("NET_DVR_FindFile_V30 unavailable")

        cond = NET_DVR_FILECOND()
        ctypes.memset(ctypes.byref(cond), 0, ctypes.sizeof(cond))
        cond.lChannel = int(channel)
        cond.dwFileType = int(file_type)
        cond.struStartTime = _datetime_to_sdk_time(start_time)
        cond.struStopTime = _datetime_to_sdk_time(end_time)

        find_handle = int(self._sdk.NET_DVR_FindFile_V30(LONG(int(user_id)), ctypes.byref(cond)))
        if find_handle < 0 or find_handle == 0xFFFFFFFF:
            raise RuntimeError(f"NET_DVR_FindFile_V30 failed: error={self.get_last_error()}")

        items: list[NativeArchiveItem] = []
        searching_count = 0
        try:
            while True:
                find_data = NET_DVR_FINDDATA_V30()
                ret = int(self._sdk.NET_DVR_FindNextFile_V30(LONG(find_handle), ctypes.byref(find_data)))
                if ret == 200:
                    searching_count = 0
                    items.append(
                        NativeArchiveItem(
                            filename=_decode_zero_terminated(find_data.sFileName),
                            start_time=_sdk_time_to_datetime(find_data.struStartTime),
                            end_time=_sdk_time_to_datetime(find_data.struStopTime),
                            file_size=int(find_data.dwFileSize),
                        )
                    )
                    continue
                if ret == 201:
                    searching_count += 1
                    if searching_count > 50:
                        break
                    time.sleep(0.05)
                    continue
                if ret == 202:
                    break
                if ret == 0:
                    break
                raise RuntimeError(
                    f"NET_DVR_FindNextFile_V30 failed: ret={ret} error={self.get_last_error()}"
                )
        finally:
            if hasattr(self._sdk, "NET_DVR_FindClose_V30"):
                self._sdk.NET_DVR_FindClose_V30(LONG(find_handle))
        return items


class HikvisionDeviceService:
    def __init__(self) -> None:
        self._connected = False
        self._params: ConnectionParams | None = None
        self._sdk: Any | None = None
        self._device: Any | None = None
        self._mode = "disconnected"
        self._last_error = ""
        self._sdk_lock = threading.RLock()
        self._archive_days_fallback_scan = (
            os.environ.get("HIK_PLAYER_ARCHIVE_DAYS_FALLBACK_SCAN", "0").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self._sdk_to_isapi_channel_cache: dict[int, int] = {}
        self._demo_channels = [
            ChannelInfo(number=1, name="Camera 01", kind="analog"),
            ChannelInfo(number=2, name="Camera 02", kind="analog"),
            ChannelInfo(number=33, name="Camera 33", kind="ip"),
            ChannelInfo(number=34, name="Camera 34", kind="ip"),
        ]

    def _device_info_v30(self):
        if self._device is None:
            return None
        info_v40 = getattr(self._device, "_device_info", None)
        if info_v40 is None:
            return None
        return getattr(info_v40, "struDeviceV30", None)

    @staticmethod
    def _format_sdk_error(error_code: int) -> str:
        message = SDK_ERROR_MESSAGES.get(int(error_code))
        if not message:
            return f"error={error_code}"
        return f"error={error_code} ({message})"

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def device(self) -> Any | None:
        return self._device

    def login(self, params: ConnectionParams, *, allow_demo_fallback: bool = True) -> str:
        self.logout()
        self._params = params
        try:
            sdk = NativeHCNetSDK(DEFAULT_LIB_DIR)
            sdk.init()
            device = sdk.login(params.host, params.port, params.username, params.password)
            self._sdk = sdk
            self._device = device
            self._connected = True
            self._mode = "real"
            self._last_error = ""
            self._sdk_to_isapi_channel_cache.clear()
            return self._mode
        except Exception as exc:
            self._last_error = str(exc)
            self._sdk = None
            self._device = None
            if not allow_demo_fallback:
                self._connected = False
                self._mode = "disconnected"
                raise

            self._connected = True
            self._mode = "demo"
            return self._mode

    def logout(self) -> None:
        if self._device is not None:
            try:
                self._device.logout()
            except Exception:
                pass
        if self._sdk is not None:
            try:
                self._sdk.cleanup()
            except Exception:
                pass
        self._sdk = None
        self._device = None
        self._params = None
        self._connected = False
        self._mode = "disconnected"
        self._sdk_to_isapi_channel_cache.clear()

    def channels(self) -> list[ChannelInfo]:
        if self._mode != "real" or self._device is None:
            return list(self._demo_channels)

        channels: list[ChannelInfo] = []
        info_v30 = self._device_info_v30()
        start = int(getattr(self._device, "start_channel", 1))
        analog_count = int(getattr(self._device, "channel_count", 0))
        ip_count = int(getattr(self._device, "ip_channel_count", 0))
        ip_start = None

        if info_v30 is not None:
            low = int(getattr(info_v30, "byStartDChan", 0))
            high = int(getattr(info_v30, "byHighDChanNum", 0))
            combined = low + high * 256
            if combined > 0:
                ip_start = combined

        for index in range(analog_count):
            number = start + index
            channels.append(ChannelInfo(number=number, name=f"Analog {number:02d}", kind="analog"))

        if ip_start is None:
            ip_start = start + analog_count

        for index in range(ip_count):
            number = ip_start + index
            channels.append(ChannelInfo(number=number, name=f"IP {index + 1:02d}", kind="ip"))

        if not channels:
            return list(self._demo_channels)
        return channels

    def archive_days(self, channel: int, year: int, month: int) -> set[int]:
        if self._mode != "real" or self._device is None:
            random.seed(channel * 100 + month)
            return {day for day in range(1, 29) if random.random() > 0.35}

        isapi_days = self._archive_days_by_isapi(channel=channel, year=year, month=month)
        if isapi_days is not None:
            return isapi_days

        month_days = self._archive_days_by_month_record(channel=channel, year=year, month=month)
        if month_days is not None:
            return month_days

        if not self._archive_days_fallback_scan:
            logger.info(
                "archive_days month_record unavailable; fallback loop disabled channel=%s month=%04d-%02d",
                channel,
                year,
                month,
            )
            return set()

        logger.info(
            "archive_days fallback find_files loop channel=%s month=%04d-%02d",
            channel,
            year,
            month,
        )
        result: set[int] = set()
        for day in range(1, 32):
            try:
                day_start = datetime(year, month, day, 0, 0, 0)
            except ValueError:
                break
            if self.find_files(channel, day_start):
                result.add(day)
        return result

    def _archive_days_by_month_record(self, *, channel: int, year: int, month: int) -> set[int] | None:
        if self._mode != "real" or self._device is None or self._sdk is None:
            return None
        if channel < 1 or channel > 255:
            return None

        try:
            sdk = self._sdk._sdk
            if not hasattr(sdk, "NET_DVR_GetMonthRecord"):
                return None

            class NET_DVR_GETMONTHRECORD(ctypes.Structure):
                _fields_ = [
                    ("dwSize", ctypes.c_uint32),
                    ("dwYear", ctypes.c_uint32),
                    ("dwMonth", ctypes.c_uint32),
                    ("byChannel", ctypes.c_ubyte),
                    ("byRes", ctypes.c_ubyte * 3),
                ]

            class NET_DVR_MONTHRECORD(ctypes.Structure):
                _fields_ = [
                    ("dwSize", ctypes.c_uint32),
                    ("dwRecordDate", ctypes.c_uint32),
                ]

            sdk.NET_DVR_GetMonthRecord.argtypes = [
                ctypes.c_int,
                ctypes.POINTER(NET_DVR_GETMONTHRECORD),
                ctypes.POINTER(NET_DVR_MONTHRECORD),
            ]
            sdk.NET_DVR_GetMonthRecord.restype = ctypes.c_bool

            req = NET_DVR_GETMONTHRECORD()
            ctypes.memset(ctypes.byref(req), 0, ctypes.sizeof(req))
            req.dwSize = ctypes.sizeof(req)
            req.dwYear = year
            req.dwMonth = month
            req.byChannel = channel

            out = NET_DVR_MONTHRECORD()
            ctypes.memset(ctypes.byref(out), 0, ctypes.sizeof(out))
            out.dwSize = ctypes.sizeof(out)

            with self._sdk_lock:
                ok = sdk.NET_DVR_GetMonthRecord(
                    self._device.user_id,
                    ctypes.byref(req),
                    ctypes.byref(out),
                )
            if not ok:
                error_code = self._sdk.get_last_error()
                logger.warning(
                    "NET_DVR_GetMonthRecord failed: error=%s channel=%s month=%04d-%02d",
                    error_code,
                    channel,
                    year,
                    month,
                )
                return None

            mask = int(out.dwRecordDate)
            days: set[int] = set()
            for day in range(1, 32):
                if not (mask & (1 << (day - 1))):
                    continue
                try:
                    datetime(year, month, day)
                except ValueError:
                    continue
                days.add(day)

            self._last_error = ""
            logger.info(
                "archive_days month_record loaded count=%s channel=%s month=%04d-%02d",
                len(days),
                channel,
                year,
                month,
            )
            return days
        except Exception as exc:
            logger.warning("archive_days month_record unavailable: %s", exc)
            return None

    def _archive_days_by_isapi(self, *, channel: int, year: int, month: int) -> set[int] | None:
        if self._mode != "real" or self._device is None or self._sdk is None:
            return None

        isapi_channel = self._sdk_channel_to_isapi(channel)
        if isapi_channel is None:
            return None

        request_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            "<trackDailyParam>"
            f"<year>{int(year)}</year>"
            f"<monthOfYear>{int(month)}</monthOfYear>"
            "</trackDailyParam>"
        ).encode("utf-8")
        request_url = f"POST /ISAPI/ContentMgmt/record/tracks/{isapi_channel}/dailyDistribution"

        try:
            response_xml = self._stdxml_request(
                request_url=request_url,
                body=request_body,
                receive_timeout_ms=10_000,
                send_timeout_ms=10_000,
            )
            root = ET.fromstring(response_xml)
            days: set[int] = set()
            for day_node in root.findall(".//{*}day"):
                record_text = day_node.findtext("{*}record", default="").strip().lower()
                if record_text not in {"true", "1"}:
                    continue
                day_text = day_node.findtext("{*}dayOfMonth", default="").strip()
                if not day_text:
                    continue
                try:
                    day_value = int(day_text)
                    datetime(year, month, day_value)
                except (TypeError, ValueError):
                    continue
                days.add(day_value)

            self._last_error = ""
            logger.info(
                "archive_days isapi loaded count=%s sdk_channel=%s isapi_channel=%s month=%04d-%02d",
                len(days),
                channel,
                isapi_channel,
                year,
                month,
            )
            return days
        except Exception as exc:
            logger.warning(
                "archive_days isapi unavailable sdk_channel=%s month=%04d-%02d: %s",
                channel,
                year,
                month,
                exc,
            )
            return None

    def _sdk_channel_to_isapi(self, sdk_channel: int) -> int | None:
        if self._mode != "real" or self._device is None or self._sdk is None:
            return None

        cached = self._sdk_to_isapi_channel_cache.get(int(sdk_channel))
        if cached is not None:
            return cached

        try:
            sdk = self._sdk._sdk
            if not hasattr(sdk, "NET_DVR_SDKChannelToISAPI"):
                return None
            sdk.NET_DVR_SDKChannelToISAPI.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_bool]
            sdk.NET_DVR_SDKChannelToISAPI.restype = ctypes.c_int
            with self._sdk_lock:
                isapi_channel = int(
                    sdk.NET_DVR_SDKChannelToISAPI(self._device.user_id, int(sdk_channel), True)
                )
            if isapi_channel < 0:
                error_code = self._sdk.get_last_error()
                logger.warning(
                    "NET_DVR_SDKChannelToISAPI failed: error=%s sdk_channel=%s",
                    error_code,
                    sdk_channel,
                )
                return None
            self._sdk_to_isapi_channel_cache[int(sdk_channel)] = isapi_channel
            return isapi_channel
        except Exception as exc:
            logger.warning("sdk->isapi channel conversion unavailable sdk_channel=%s: %s", sdk_channel, exc)
            return None

    def _stdxml_request(
        self,
        *,
        request_url: str,
        body: bytes = b"",
        receive_timeout_ms: int = 5_000,
        send_timeout_ms: int = 5_000,
    ) -> bytes:
        if self._mode != "real" or self._device is None or self._sdk is None:
            raise RuntimeError("STDXML request requires active real device session")

        sdk = self._sdk._sdk
        if not hasattr(sdk, "NET_DVR_STDXMLConfig"):
            raise RuntimeError("NET_DVR_STDXMLConfig unavailable")

        class NET_DVR_XML_CONFIG_INPUT(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_uint32),
                ("lpRequestUrl", ctypes.c_void_p),
                ("dwRequestUrlLen", ctypes.c_uint32),
                ("lpInBuffer", ctypes.c_void_p),
                ("dwInBufferSize", ctypes.c_uint32),
                ("dwRecvTimeOut", ctypes.c_uint32),
                ("byForceEncrpt", ctypes.c_ubyte),
                ("byNumOfMultiPart", ctypes.c_ubyte),
                ("byMIMEType", ctypes.c_ubyte),
                ("byRes1", ctypes.c_ubyte),
                ("dwSendTimeOut", ctypes.c_uint32),
                ("byRes", ctypes.c_ubyte * 24),
            ]

        class NET_DVR_XML_CONFIG_OUTPUT(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_uint32),
                ("lpOutBuffer", ctypes.c_void_p),
                ("dwOutBufferSize", ctypes.c_uint32),
                ("dwReturnedXMLSize", ctypes.c_uint32),
                ("lpStatusBuffer", ctypes.c_void_p),
                ("dwStatusSize", ctypes.c_uint32),
                ("lpDataBuffer", ctypes.c_void_p),
                ("byNumOfMultiPart", ctypes.c_ubyte),
                ("byRes", ctypes.c_ubyte * 23),
            ]

        sdk.NET_DVR_STDXMLConfig.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(NET_DVR_XML_CONFIG_INPUT),
            ctypes.POINTER(NET_DVR_XML_CONFIG_OUTPUT),
        ]
        sdk.NET_DVR_STDXMLConfig.restype = ctypes.c_bool

        url_bytes = request_url.encode("utf-8")
        url_buffer = ctypes.create_string_buffer(url_bytes)
        body_buffer = ctypes.create_string_buffer(body) if body else None
        out_buffer = ctypes.create_string_buffer(1024 * 1024)
        status_buffer = ctypes.create_string_buffer(64 * 1024)

        request = NET_DVR_XML_CONFIG_INPUT()
        ctypes.memset(ctypes.byref(request), 0, ctypes.sizeof(request))
        request.dwSize = ctypes.sizeof(request)
        request.lpRequestUrl = ctypes.cast(url_buffer, ctypes.c_void_p)
        request.dwRequestUrlLen = len(url_bytes)
        if body_buffer is not None:
            request.lpInBuffer = ctypes.cast(body_buffer, ctypes.c_void_p)
            request.dwInBufferSize = len(body)
        request.dwRecvTimeOut = int(receive_timeout_ms)
        request.dwSendTimeOut = int(send_timeout_ms)

        response = NET_DVR_XML_CONFIG_OUTPUT()
        ctypes.memset(ctypes.byref(response), 0, ctypes.sizeof(response))
        response.dwSize = ctypes.sizeof(response)
        response.lpOutBuffer = ctypes.cast(out_buffer, ctypes.c_void_p)
        response.dwOutBufferSize = ctypes.sizeof(out_buffer)
        response.lpStatusBuffer = ctypes.cast(status_buffer, ctypes.c_void_p)
        response.dwStatusSize = ctypes.sizeof(status_buffer)

        with self._sdk_lock:
            ok = sdk.NET_DVR_STDXMLConfig(
                self._device.user_id,
                ctypes.byref(request),
                ctypes.byref(response),
            )
        if not ok:
            error_code = self._sdk.get_last_error()
            status_text = status_buffer.value.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(
                f"NET_DVR_STDXMLConfig failed: error={error_code} request={request_url} status={status_text}"
            )

        xml_size = int(response.dwReturnedXMLSize)
        xml_bytes = out_buffer.raw[:xml_size] if xml_size > 0 else out_buffer.value
        if not xml_bytes:
            status_text = status_buffer.value.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(
                f"NET_DVR_STDXMLConfig returned empty XML: request={request_url} status={status_text}"
            )
        return bytes(xml_bytes)

    def find_files(self, channel: int, day: datetime) -> list[ArchiveFile]:
        if self._mode != "real" or self._device is None:
            return self._demo_find_files(channel, day)

        start_time = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = start_time + timedelta(days=1) - timedelta(seconds=1)
        search_variants = (
            (0xFF, 0xFF),
            (0, 0xFF),
            (0xFF, 0),
            (0, 0),
        )
        max_attempts = 3
        last_error = ""

        for attempt in range(1, max_attempts + 1):
            for stream_type, file_type in search_variants:
                files: list[ArchiveFile] = []
                try:
                    with self._sdk_lock:
                        for item in self._device.find_files(
                            channel=channel,
                            start_time=start_time,
                            end_time=end_time,
                            file_type=file_type,
                            stream_type=stream_type,
                        ):
                            if item.start_time is None or item.end_time is None:
                                continue
                            files.append(
                                ArchiveFile(
                                    filename=item.filename,
                                    start_time=item.start_time,
                                    end_time=item.end_time,
                                    size_bytes=int(item.file_size),
                                )
                            )
                except Exception as exc:
                    last_error = str(exc)
                    continue

                if files:
                    self._last_error = ""
                    if attempt > 1 or (stream_type, file_type) != search_variants[0]:
                        logger.info(
                            "find_files recovered channel=%s day=%s attempt=%s stream=%s file_type=%s count=%s",
                            channel,
                            day.date(),
                            attempt,
                            stream_type,
                            file_type,
                            len(files),
                        )
                    return files

            if attempt < max_attempts:
                time.sleep(0.12 * attempt)

        self._last_error = last_error
        if last_error:
            logger.warning(
                "find_files failed channel=%s day=%s error=%s",
                channel,
                day.date(),
                last_error,
            )
        else:
            logger.info(
                "find_files empty channel=%s day=%s last_error=%s",
                channel,
                day.date(),
                self._last_error,
            )
        return []

    def _demo_find_files(self, channel: int, day: datetime) -> list[ArchiveFile]:
        random.seed(channel * 1000 + day.day)
        files: list[ArchiveFile] = []
        cursor = day.replace(hour=0, minute=25, second=0, microsecond=0)
        day_end = day.replace(hour=23, minute=59, second=59, microsecond=0)
        index = 1
        while cursor < day_end:
            duration = timedelta(minutes=20 + random.randint(0, 120))
            gap = timedelta(minutes=8 + random.randint(0, 40))
            end_time = min(cursor + duration, day_end)
            files.append(
                ArchiveFile(
                    filename=f"ch{channel:02d}_{index:03d}.mp4",
                    start_time=cursor,
                    end_time=end_time,
                    size_bytes=int((end_time - cursor).total_seconds() * 180_000),
                )
            )
            cursor = end_time + gap
            index += 1
        return files

    def playback_by_time(
        self,
        *,
        channel: int,
        start_time: datetime,
        end_time: datetime,
        resume_time: datetime,
        callback: Any,
    ) -> int:
        if self._mode != "real" or self._device is None or self._sdk is None:
            return -1

        try:
            class NET_DVR_VOD_PARA(ctypes.Structure):
                _fields_ = [
                    ("dwSize", DWORD),
                    ("struIDInfo", NET_DVR_STREAM_INFO),
                    ("struBeginTime", NET_DVR_TIME),
                    ("struEndTime", NET_DVR_TIME),
                    ("hWnd", HWND),
                    ("byDrawFrame", BYTE),
                    ("byVolumeType", BYTE),
                    ("byVolumeNum", BYTE),
                    ("byStreamType", BYTE),
                    ("dwFileIndex", DWORD),
                    ("byAudioFile", BYTE),
                    ("byCourseFile", BYTE),
                    ("byDownload", BYTE),
                    ("byOptimalStreamType", BYTE),
                    ("byUseAsyn", BYTE),
                    ("byRes2", BYTE * 19),
                ]

            sdk = self._sdk._sdk
            sdk.NET_DVR_PlayBackByTime_V40.argtypes = [ctypes.c_int, ctypes.POINTER(NET_DVR_VOD_PARA)]
            sdk.NET_DVR_PlayBackByTime_V40.restype = ctypes.c_int

            vod = NET_DVR_VOD_PARA()
            ctypes.memset(ctypes.byref(vod), 0, ctypes.sizeof(vod))
            vod.dwSize = ctypes.sizeof(NET_DVR_VOD_PARA)
            vod.struIDInfo.dwSize = ctypes.sizeof(NET_DVR_STREAM_INFO)
            vod.struIDInfo.dwChannel = channel
            vod.struBeginTime.dwYear = start_time.year
            vod.struBeginTime.dwMonth = start_time.month
            vod.struBeginTime.dwDay = start_time.day
            vod.struBeginTime.dwHour = start_time.hour
            vod.struBeginTime.dwMinute = start_time.minute
            vod.struBeginTime.dwSecond = start_time.second
            vod.struEndTime.dwYear = end_time.year
            vod.struEndTime.dwMonth = end_time.month
            vod.struEndTime.dwDay = end_time.day
            vod.struEndTime.dwHour = end_time.hour
            vod.struEndTime.dwMinute = end_time.minute
            vod.struEndTime.dwSecond = end_time.second
            vod.hWnd = 0
            vod.byStreamType = 0
            vod.byOptimalStreamType = 1
            vod.byUseAsyn = 1

            play_begin = min(max(resume_time, start_time), end_time)
            vod.struBeginTime.dwYear = play_begin.year
            vod.struBeginTime.dwMonth = play_begin.month
            vod.struBeginTime.dwDay = play_begin.day
            vod.struBeginTime.dwHour = play_begin.hour
            vod.struBeginTime.dwMinute = play_begin.minute
            vod.struBeginTime.dwSecond = play_begin.second

            with self._sdk_lock:
                handle = sdk.NET_DVR_PlayBackByTime_V40(self._device.user_id, ctypes.byref(vod))
            if handle < 0:
                error_code = self._sdk.get_last_error()
                self._last_error = (
                    "NET_DVR_PlayBackByTime_V40 failed: "
                    f"{self._format_sdk_error(error_code)}"
                )
                logger.warning("%s", self._last_error)
                return -1

            def _callback_wrapper(h, data_type, buffer, buf_size, user):
                try:
                    data = ctypes.string_at(buffer, buf_size)
                    callback(h, data_type, data)
                except Exception:
                    pass

            c_callback = REALDATACALLBACK(_callback_wrapper)
            self._device._callbacks.append(c_callback)
            with self._sdk_lock:
                callback_ok = sdk.NET_DVR_SetPlayDataCallBack_V40(handle, c_callback, None)
            if not callback_ok:
                error_code = self._sdk.get_last_error()
                self._last_error = f"NET_DVR_SetPlayDataCallBack_V40 failed: error={error_code}"
                logger.warning("%s", self._last_error)
                with self._sdk_lock:
                    sdk.NET_DVR_StopPlayBack(handle)
                return -1

            self._last_error = ""
            return int(handle)
        except Exception as exc:
            self._last_error = f"playback_by_time failed: {exc}"
            logger.exception("playback_by_time failed")
            raise

    def playback_by_time_hwnd(
        self,
        *,
        channel: int,
        start_time: datetime,
        end_time: datetime,
        resume_time: datetime,
        hwnd: int,
    ) -> int:
        if self._mode != "real" or self._device is None or self._sdk is None:
            return -1
        if int(hwnd) <= 0:
            self._last_error = "playback_by_time_hwnd requires a valid window id"
            return -1

        try:
            class NET_DVR_VOD_PARA(ctypes.Structure):
                _fields_ = [
                    ("dwSize", DWORD),
                    ("struIDInfo", NET_DVR_STREAM_INFO),
                    ("struBeginTime", NET_DVR_TIME),
                    ("struEndTime", NET_DVR_TIME),
                    ("hWnd", HWND),
                    ("byDrawFrame", BYTE),
                    ("byVolumeType", BYTE),
                    ("byVolumeNum", BYTE),
                    ("byStreamType", BYTE),
                    ("dwFileIndex", DWORD),
                    ("byAudioFile", BYTE),
                    ("byCourseFile", BYTE),
                    ("byDownload", BYTE),
                    ("byOptimalStreamType", BYTE),
                    ("byUseAsyn", BYTE),
                    ("byRes2", BYTE * 19),
                ]

            sdk = self._sdk._sdk
            sdk.NET_DVR_PlayBackByTime_V40.argtypes = [ctypes.c_int, ctypes.POINTER(NET_DVR_VOD_PARA)]
            sdk.NET_DVR_PlayBackByTime_V40.restype = ctypes.c_int

            vod = NET_DVR_VOD_PARA()
            ctypes.memset(ctypes.byref(vod), 0, ctypes.sizeof(vod))
            vod.dwSize = ctypes.sizeof(NET_DVR_VOD_PARA)
            vod.struIDInfo.dwSize = ctypes.sizeof(NET_DVR_STREAM_INFO)
            vod.struIDInfo.dwChannel = channel
            vod.struBeginTime.dwYear = start_time.year
            vod.struBeginTime.dwMonth = start_time.month
            vod.struBeginTime.dwDay = start_time.day
            vod.struBeginTime.dwHour = start_time.hour
            vod.struBeginTime.dwMinute = start_time.minute
            vod.struBeginTime.dwSecond = start_time.second
            vod.struEndTime.dwYear = end_time.year
            vod.struEndTime.dwMonth = end_time.month
            vod.struEndTime.dwDay = end_time.day
            vod.struEndTime.dwHour = end_time.hour
            vod.struEndTime.dwMinute = end_time.minute
            vod.struEndTime.dwSecond = end_time.second
            vod.hWnd = HWND(int(hwnd))
            vod.byStreamType = 0
            vod.byOptimalStreamType = 1
            vod.byUseAsyn = 1

            play_begin = min(max(resume_time, start_time), end_time)
            vod.struBeginTime.dwYear = play_begin.year
            vod.struBeginTime.dwMonth = play_begin.month
            vod.struBeginTime.dwDay = play_begin.day
            vod.struBeginTime.dwHour = play_begin.hour
            vod.struBeginTime.dwMinute = play_begin.minute
            vod.struBeginTime.dwSecond = play_begin.second

            with self._sdk_lock:
                handle = sdk.NET_DVR_PlayBackByTime_V40(self._device.user_id, ctypes.byref(vod))
            if handle < 0:
                error_code = self._sdk.get_last_error()
                self._last_error = (
                    "NET_DVR_PlayBackByTime_V40 failed: "
                    f"{self._format_sdk_error(error_code)}"
                )
                logger.warning("%s", self._last_error)
                return -1

            self._last_error = ""
            return int(handle)
        except Exception as exc:
            self._last_error = f"playback_by_time_hwnd failed: {exc}"
            logger.exception("playback_by_time_hwnd failed")
            raise

    def playback_control_v40(
        self,
        handle: int,
        command: int,
        *,
        in_buffer: ctypes.Structure | None = None,
        out_buffer: ctypes.Structure | None = None,
    ) -> tuple[bool, int]:
        if self._mode != "real" or self._device is None or self._sdk is None:
            return False, 0

        try:
            sdk = self._sdk._sdk
            in_ptr = ctypes.byref(in_buffer) if in_buffer is not None else None
            in_len = ctypes.sizeof(in_buffer) if in_buffer is not None else 0
            out_ptr = ctypes.byref(out_buffer) if out_buffer is not None else None
            out_len = ctypes.c_ulong(0)
            out_len_ptr = ctypes.byref(out_len) if out_buffer is not None else None
            with self._sdk_lock:
                ok = sdk.NET_DVR_PlayBackControl_V40(
                    handle,
                    command,
                    in_ptr,
                    in_len,
                    out_ptr,
                    out_len_ptr,
                )
            if not ok:
                error_code = self._sdk.get_last_error()
                self._last_error = f"NET_DVR_PlayBackControl_V40 failed: command={command}, error={error_code}"
            else:
                self._last_error = ""
            return bool(ok), int(out_len.value)
        except Exception as exc:
            self._last_error = f"playback_control_v40 failed: {exc}"
            return False, 0

    def playback_set_time(self, handle: int, target_time: datetime) -> bool:
        pause_ok, _ = self.playback_control_v40(handle, NET_DVR_PLAYPAUSE, in_buffer=None, out_buffer=None)
        sdk_time = NET_DVR_TIME()
        sdk_time.dwYear = target_time.year
        sdk_time.dwMonth = target_time.month
        sdk_time.dwDay = target_time.day
        sdk_time.dwHour = target_time.hour
        sdk_time.dwMinute = target_time.minute
        sdk_time.dwSecond = target_time.second
        ok, _ = self.playback_control_v40(handle, NET_DVR_PLAYSETTIME, in_buffer=sdk_time)
        restart_ok = False
        if ok:
            restart_ok, _ = self.playback_control_v40(handle, NET_DVR_PLAYRESTART, in_buffer=None, out_buffer=None)
            if not restart_ok:
                restart_ok, _ = self.playback_control_v40(handle, NET_DVR_PLAYSTART, in_buffer=None, out_buffer=None)
        final_ok = bool(ok and restart_ok)
        logger.info(
            "PLAYSETTIME handle=%s target=%s ok=%s pause_ok=%s restart_ok=%s last_error=%s",
            handle,
            target_time,
            final_ok,
            pause_ok,
            restart_ok,
            self._last_error,
        )
        return final_ok

    def playback_keepalive(self, handle: int) -> bool:
        if self._mode != "real" or self._device is None:
            return False
        ok, _ = self.playback_control_v40(handle, NET_DVR_KEEPALIVE, in_buffer=None, out_buffer=None)
        return ok

    def stop_playback(self, handle: int) -> bool:
        if self._mode != "real" or self._device is None:
            return True

        try:
            control_ok, _ = self.playback_control_v40(handle, NET_DVR_PLAYSTOP, in_buffer=None, out_buffer=None)
            sdk = self._sdk._sdk if self._sdk is not None else None
            if sdk is not None and hasattr(sdk, "NET_DVR_StopPlayBack"):
                with self._sdk_lock:
                    sdk.NET_DVR_StopPlayBack(handle)
            return control_ok
        except Exception as exc:
            self._last_error = f"stop_playback failed: {exc}"
            return False


NET_DVR_GET_IPPARACFG_V40 = 1062
NAME_LEN = 32
PASSWD_LEN = 16
MAX_DOMAIN_NAME = 64
MAX_CHANNUM_V30 = 64
MAX_ANALOG_CHANNUM = 32
MAX_IP_DEVICE_V40 = 64
URL_LEN = 240
STREAM_ID_LEN = 32
STATUS_ONLINE = "ONLINE"
STATUS_OFFLINE = "OFFLINE"
STATUS_NO_VIDEO = "NO VIDEO"
STATUS_DISABLED = "DISABLED"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_CONNECTING = "CONNECTING"
STATUS_ACCOUNT_ERROR = "ACCOUNT ERROR"
STATUS_CHANNEL_ERROR = "CHANNEL ERROR"
STATUS_NETWORK_ERROR = "NETWORK ERROR"
STATUS_IPC_ERROR = "IPC ERROR"

DIGITAL_STATUS_TEXT = {
    1: (STATUS_ONLINE, True, True, ""),
    2: (STATUS_CONNECTING, False, False, "Connecting"),
    3: ("BANDWIDTH EXCEEDED", False, False, "Bandwidth exceeded"),
    4: ("DOMAIN ERROR", False, False, "Domain error"),
    5: (STATUS_CHANNEL_ERROR, False, False, "Invalid remote channel"),
    6: (STATUS_ACCOUNT_ERROR, False, False, "Wrong username/password or account issue"),
    7: ("STREAM TYPE ERROR", False, False, "Stream type not supported"),
    8: ("DVR CONFLICT", False, False, "Conflict with DVR"),
    9: ("IPC CONFLICT", False, False, "Conflict with IPC"),
    10: (STATUS_NETWORK_ERROR, False, False, "Network unreachable"),
    11: ("IPC NOT EXIST", False, False, "IPC not found"),
    12: (STATUS_IPC_ERROR, False, False, "IPC exception"),
    13: ("OTHER ERROR", False, False, "Other connection error"),
    14: ("RESOLUTION ERROR", False, False, "Resolution not supported"),
    15: ("IPC LAN ERROR", False, False, "IPC LAN error"),
    16: ("USER LOCKED", False, False, "User locked"),
    17: ("NOT ACTIVATED", False, False, "IPC not activated"),
    18: ("USER NOT EXIST", False, False, "User does not exist"),
    19: ("UNREGISTERED", False, False, "IPC unregistered"),
    20: ("POE DETECTING", False, False, "PoE port detecting"),
    21: ("RESOURCE EXCEEDED", False, False, "Resource exceeded"),
    22: ("NEED REPAIR", False, False, "IPC needs repair"),
    23: ("ACTIVATING", False, False, "IPC activating"),
    24: ("TOKEN AUTH FAILED", False, False, "Token authentication failed"),
}


class NET_DVR_IPADDR(ctypes.Structure):
    _fields_ = [
        ("sIpV4", ctypes.c_char * 16),
        ("byIPv6", ctypes.c_ubyte * 128),
    ]


class NET_DVR_IPDEVINFO_V31(ctypes.Structure):
    _fields_ = [
        ("byEnable", ctypes.c_ubyte),
        ("byProType", ctypes.c_ubyte),
        ("byEnableQuickAdd", ctypes.c_ubyte),
        ("byCameraType", ctypes.c_ubyte),
        ("sUserName", ctypes.c_ubyte * NAME_LEN),
        ("sPassword", ctypes.c_ubyte * PASSWD_LEN),
        ("byDomain", ctypes.c_ubyte * MAX_DOMAIN_NAME),
        ("struIP", NET_DVR_IPADDR),
        ("wDVRPort", ctypes.c_uint16),
        ("szDeviceID", ctypes.c_ubyte * 32),
        ("byEnableTiming", ctypes.c_ubyte),
        ("byCertificateValidation", ctypes.c_ubyte),
    ]


class NET_DVR_IPCHANINFO(ctypes.Structure):
    _fields_ = [
        ("byEnable", ctypes.c_ubyte),
        ("byIPID", ctypes.c_ubyte),
        ("byChannel", ctypes.c_ubyte),
        ("byIPIDHigh", ctypes.c_ubyte),
        ("byTransProtocol", ctypes.c_ubyte),
        ("byGetStream", ctypes.c_ubyte),
        ("byRes", ctypes.c_ubyte * 30),
    ]


class NET_DVR_IPCHANINFO_V40(ctypes.Structure):
    _fields_ = [
        ("byEnable", ctypes.c_ubyte),
        ("byRes1", ctypes.c_ubyte),
        ("wIPID", ctypes.c_uint16),
        ("dwChannel", ctypes.c_uint32),
        ("byTransProtocol", ctypes.c_ubyte),
        ("byTransMode", ctypes.c_ubyte),
        ("byFactoryType", ctypes.c_ubyte),
        ("byRes", ctypes.c_ubyte),
        ("strURL", ctypes.c_ubyte * URL_LEN),
    ]


class NET_DVR_JPEGPARA(ctypes.Structure):
    _fields_ = [
        ("wPicSize", ctypes.c_uint16),
        ("wPicQuality", ctypes.c_uint16),
    ]


class NET_DVR_GET_STREAM_UNION(ctypes.Union):
    _fields_ = [
        ("struChanInfo", NET_DVR_IPCHANINFO),
        ("struIPChan", NET_DVR_IPCHANINFO_V40),
        ("byRaw", ctypes.c_ubyte * 492),
    ]


class NET_DVR_STREAM_MODE(ctypes.Structure):
    _fields_ = [
        ("byGetStreamType", ctypes.c_ubyte),
        ("byRes", ctypes.c_ubyte * 3),
        ("uGetStream", NET_DVR_GET_STREAM_UNION),
    ]


class NET_DVR_IPPARACFG_V40(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("dwGroupNum", ctypes.c_uint32),
        ("dwAChanNum", ctypes.c_uint32),
        ("dwDChanNum", ctypes.c_uint32),
        ("dwStartDChan", ctypes.c_uint32),
        ("byAnalogChanEnable", ctypes.c_ubyte * MAX_CHANNUM_V30),
        ("struIPDevInfo", NET_DVR_IPDEVINFO_V31 * MAX_IP_DEVICE_V40),
        ("struStreamMode", NET_DVR_STREAM_MODE * MAX_CHANNUM_V30),
        ("byRes2", ctypes.c_ubyte * 20),
    ]


class NET_DVR_CHANNELSTATE_V30(ctypes.Structure):
    _fields_ = [
        ("byRecordStatic", ctypes.c_ubyte),
        ("bySignalStatic", ctypes.c_ubyte),
        ("byHardwareStatic", ctypes.c_ubyte),
        ("byRes1", ctypes.c_ubyte),
        ("dwBitRate", ctypes.c_uint32),
        ("dwLinkNum", ctypes.c_uint32),
        ("struClientIP", NET_DVR_IPADDR * 6),
        ("dwIPLinkNum", ctypes.c_uint32),
        ("byExceedMaxLink", ctypes.c_ubyte),
        ("byRes", ctypes.c_ubyte * 3),
        ("dwAllBitRate", ctypes.c_uint32),
        ("dwChannelNo", ctypes.c_uint32),
    ]


class NET_DVR_DISKSTATE(ctypes.Structure):
    _fields_ = [
        ("dwVolume", ctypes.c_uint32),
        ("dwFreeSpace", ctypes.c_uint32),
        ("dwHardDiskStatic", ctypes.c_uint32),
    ]


class NET_DVR_WORKSTATE_V30(ctypes.Structure):
    _fields_ = [
        ("dwDeviceStatic", ctypes.c_uint32),
        ("struHardDiskStatic", NET_DVR_DISKSTATE * 33),
        ("struChanStatic", NET_DVR_CHANNELSTATE_V30 * MAX_CHANNUM_V30),
        ("byAlarmInStatic", ctypes.c_ubyte * 160),
        ("byAlarmOutStatic", ctypes.c_ubyte * 96),
        ("dwLocalDisplay", ctypes.c_uint32),
        ("byAudioChanStatus", ctypes.c_ubyte * 2),
        ("byRes", ctypes.c_ubyte * 10),
    ]


class NET_DVR_DIGITAL_CHANNEL_STATE(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("byDigitalAudioChanTalkState", ctypes.c_ubyte * MAX_CHANNUM_V30),
        ("byDigitalChanState", ctypes.c_ubyte * MAX_CHANNUM_V30),
        ("byDigitalAudioChanTalkStateEx", ctypes.c_ubyte * (MAX_CHANNUM_V30 * 3)),
        ("byDigitalChanStateEx", ctypes.c_ubyte * (MAX_CHANNUM_V30 * 3)),
        ("byAnalogChanState", ctypes.c_ubyte * MAX_ANALOG_CHANNUM),
        ("byRes", ctypes.c_ubyte * 32),
    ]


class NET_DVR_PREVIEWINFO(ctypes.Structure):
    _fields_ = [
        ("lChannel", ctypes.c_int),
        ("dwStreamType", ctypes.c_uint32),
        ("dwLinkMode", ctypes.c_uint32),
        ("hPlayWnd", ctypes.c_uint32),
        ("bBlocked", ctypes.c_uint32),
        ("bPassbackRecord", ctypes.c_uint32),
        ("byPreviewMode", ctypes.c_ubyte),
        ("byStreamID", ctypes.c_ubyte * STREAM_ID_LEN),
        ("byProtoType", ctypes.c_ubyte),
        ("byRes1", ctypes.c_ubyte),
        ("byVideoCodingType", ctypes.c_ubyte),
        ("dwDisplayBufNum", ctypes.c_uint32),
        ("byNPQMode", ctypes.c_ubyte),
        ("byRecvMetaData", ctypes.c_ubyte),
        ("byDataType", ctypes.c_ubyte),
        ("byRes", ctypes.c_ubyte * 213),
    ]


@dataclass
class PlaybackSessionState:
    start_time: datetime
    end_time: datetime
    window_id: int
    play_port: int | None = 0
    last_system_time: datetime | None = None


@dataclass
class LiveSessionState:
    channel: int
    user_id: int
    window_id: int
    width: int
    height: int
    profile: StreamProfile
    play_port: int | None = None


@dataclass
class HikvisionPlugin:
    service: HikvisionDeviceService
    current_params: ConnectionParams | None = None
    last_report: DiagnosticReport | None = None

    def __init__(self) -> None:
        self.service = HikvisionDeviceService()
        self.current_params = None
        self.last_report = None
        self._playctrl: PlayCtrl | None = None
        self._playback_sessions: dict[int, PlaybackSessionState] = {}
        self._live_sessions: dict[int, LiveSessionState] = {}
        self._keepalive_threads: dict[int, tuple[threading.Thread, threading.Event]] = {}

    def _ensure_playctrl_loaded(self) -> bool:
        if self._playctrl is not None:
            return True
        try:
            self._playctrl = PlayCtrl()
        except Exception:
            self._playctrl = None
        return self._playctrl is not None

    def _probe_play_port(self, session: PlaybackSessionState) -> int | None:
        if not self._ensure_playctrl_loaded() or self._playctrl is None:
            return session.play_port
        if session.play_port is None:
            session.play_port = 0
        size0 = self._playctrl.get_picture_size(session.play_port)
        st0 = self._playctrl.get_system_time(session.play_port)
        if size0 is not None or st0 is not None:
            return session.play_port
        port = self._playctrl.find_active_port()
        if port is not None:
            session.play_port = int(port)
        return session.play_port

    def _resolve_live_play_port(self, session_id: int, session: LiveSessionState) -> int | None:
        if not self._ensure_playctrl_loaded() or self._playctrl is None:
            return None
        if session.play_port is not None and session.play_port >= 0:
            size = self._playctrl.get_picture_size(int(session.play_port))
            st = self._playctrl.get_system_time(int(session.play_port))
            if size is not None or st is not None:
                return int(session.play_port)

        hcnetsdk = self._resolve_sdk_library()
        if not hasattr(hcnetsdk, "NET_DVR_GetRealPlayerIndex"):
            return None
        hcnetsdk.NET_DVR_GetRealPlayerIndex.argtypes = [ctypes.c_int]
        hcnetsdk.NET_DVR_GetRealPlayerIndex.restype = ctypes.c_int
        with self.service._sdk_lock:
            play_port = int(hcnetsdk.NET_DVR_GetRealPlayerIndex(int(session_id)))
        if play_port < 0:
            return None
        session.play_port = play_port
        return play_port

    def _start_playback_keepalive(self, handle: int) -> None:
        if handle in self._keepalive_threads:
            return
        stop_event = threading.Event()

        def _keepalive_worker() -> None:
            while not stop_event.wait(2.0):
                try:
                    self.service.playback_keepalive(handle)
                except Exception:
                    pass

        thread = threading.Thread(target=_keepalive_worker, daemon=True)
        self._keepalive_threads[handle] = (thread, stop_event)
        thread.start()

    def _stop_playback_keepalive(self, handle: int) -> None:
        pair = self._keepalive_threads.pop(handle, None)
        if pair is None:
            return
        _, stop_event = pair
        stop_event.set()

    def _missing_sdk_files(self) -> list[str]:
        return validate_sdk_layout(DEFAULT_LIB_DIR)

    def _demo_channels(self) -> list[ChannelInfo]:
        return [
            ChannelInfo(
                number=item.number,
                name=item.name,
                kind=item.kind,
                is_online=item.is_online,
                configured=True,
                enabled=True,
                transport_online=item.is_online if item.kind == "ip" else None,
                video_present=item.is_online,
                status_text=STATUS_ONLINE if item.is_online else STATUS_OFFLINE,
                error_code=None,
                error_text="",
            )
            for item in self.service.channels()
        ]

    def _sdk_handles(self) -> tuple[object, int]:
        if self.service.mode != "real" or self.service.device is None or self.service._sdk is None:  # type: ignore[attr-defined]
            raise RuntimeError("Real SDK session is not available")
        return self.service._sdk._sdk, int(self.service.device.user_id)  # type: ignore[attr-defined]

    def _get_ipparacfg_v40(self) -> NET_DVR_IPPARACFG_V40 | None:
        sdk, user_id = self._sdk_handles()
        sdk.NET_DVR_GetDVRConfig.argtypes = [
            ctypes.c_long,
            ctypes.c_uint32,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        sdk.NET_DVR_GetDVRConfig.restype = ctypes.c_bool

        payload = NET_DVR_IPPARACFG_V40()
        ctypes.memset(ctypes.byref(payload), 0, ctypes.sizeof(payload))
        payload.dwSize = ctypes.sizeof(payload)
        returned = ctypes.c_uint32(0)

        last_error = None
        for channel in (0, -1):
            ok = sdk.NET_DVR_GetDVRConfig(
                ctypes.c_long(user_id),
                ctypes.c_uint32(NET_DVR_GET_IPPARACFG_V40),
                ctypes.c_long(channel),
                ctypes.byref(payload),
                ctypes.c_uint32(ctypes.sizeof(payload)),
                ctypes.byref(returned),
            )
            if ok:
                return payload
            if hasattr(sdk, "NET_DVR_GetLastError"):
                last_error = int(sdk.NET_DVR_GetLastError())
        if last_error is not None:
            self.service._last_error = f"NET_DVR_GET_IPPARACFG_V40 failed: error={last_error}"  # type: ignore[attr-defined]
        return None

    def _get_work_state_v30(self) -> NET_DVR_WORKSTATE_V30 | None:
        sdk, user_id = self._sdk_handles()
        sdk.NET_DVR_GetDVRWorkState_V30.argtypes = [
            ctypes.c_long,
            ctypes.POINTER(NET_DVR_WORKSTATE_V30),
        ]
        sdk.NET_DVR_GetDVRWorkState_V30.restype = ctypes.c_bool

        state = NET_DVR_WORKSTATE_V30()
        ctypes.memset(ctypes.byref(state), 0, ctypes.sizeof(state))
        ok = sdk.NET_DVR_GetDVRWorkState_V30(
            ctypes.c_long(user_id),
            ctypes.byref(state),
        )
        if not ok:
            return None
        return state

    def _get_digital_channel_state(self) -> NET_DVR_DIGITAL_CHANNEL_STATE | None:
        sdk, user_id = self._sdk_handles()
        sdk.NET_DVR_GetDVRConfig.argtypes = [
            ctypes.c_long,
            ctypes.c_uint32,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        sdk.NET_DVR_GetDVRConfig.restype = ctypes.c_bool

        payload = NET_DVR_DIGITAL_CHANNEL_STATE()
        ctypes.memset(ctypes.byref(payload), 0, ctypes.sizeof(payload))
        payload.dwSize = ctypes.sizeof(payload)
        returned = ctypes.c_uint32(0)
        ok = sdk.NET_DVR_GetDVRConfig(
            ctypes.c_long(user_id),
            ctypes.c_uint32(6126),
            ctypes.c_long(0),
            ctypes.byref(payload),
            ctypes.c_uint32(ctypes.sizeof(payload)),
            ctypes.byref(returned),
        )
        if not ok:
            return None
        return payload

    @staticmethod
    def _analog_enabled(config: NET_DVR_IPPARACFG_V40 | None, analog_index: int) -> bool:
        if config is None:
            return True
        if analog_index < 0 or analog_index >= MAX_ANALOG_CHANNUM:
            return False
        first_block = [int(config.byAnalogChanEnable[index]) for index in range(MAX_ANALOG_CHANNUM)]
        if all(value in (0, 1) for value in first_block):
            return bool(first_block[analog_index])
        packed_value = int(config.byAnalogChanEnable[analog_index // 8])
        return bool(packed_value & (1 << (analog_index % 8)))

    @staticmethod
    def _ip_slot_state(config: NET_DVR_IPPARACFG_V40 | None, channel_number: int) -> dict[str, bool | None]:
        fallback = {
            "configured": True,
            "enabled": True,
            "transport_online": None,
            "video_present": None,
        }
        if config is None:
            return fallback

        start_dchan = int(config.dwStartDChan)
        index = channel_number - start_dchan
        if index < 0 or index >= MAX_CHANNUM_V30:
            return {
                "configured": False,
                "enabled": False,
                "transport_online": None,
                "video_present": None,
            }

        stream_mode = config.struStreamMode[index]
        stream_type = int(stream_mode.byGetStreamType)
        if stream_type == 0:
            chan_info = stream_mode.uGetStream.struChanInfo
            device_id = int(chan_info.byIPID) + int(chan_info.byIPIDHigh) * 256
            channel_ref = int(chan_info.byChannel)
            transport_online = bool(chan_info.byEnable)
        elif stream_type == 6:
            chan_info_v40 = stream_mode.uGetStream.struIPChan
            device_id = int(chan_info_v40.wIPID)
            channel_ref = int(chan_info_v40.dwChannel)
            transport_online = bool(chan_info_v40.byEnable)
        else:
            device_id = 0
            channel_ref = 0
            transport_online = None

        configured = device_id > 0 and channel_ref > 0
        if not configured:
            return {
                "configured": False,
                "enabled": False,
                "transport_online": None,
                "video_present": None,
            }

        device_valid = False
        if 1 <= device_id <= MAX_IP_DEVICE_V40:
            device_valid = bool(config.struIPDevInfo[device_id - 1].byEnable)
        enabled = device_valid or configured
        return {
            "configured": configured,
            "enabled": enabled,
            "transport_online": transport_online,
            "video_present": transport_online,
        }

    @staticmethod
    def _status_text(
        *,
        enabled: bool,
        transport_online: bool | None,
        video_present: bool | None,
        kind: str,
    ) -> str:
        if not enabled:
            return STATUS_DISABLED
        if kind == "analog":
            if video_present is False:
                return STATUS_NO_VIDEO
            if video_present is True:
                return STATUS_ONLINE
            return STATUS_UNKNOWN
        if transport_online is True:
            return STATUS_ONLINE
        if transport_online is False:
            return STATUS_OFFLINE
        return STATUS_UNKNOWN

    @staticmethod
    def _decode_bytes(value: ctypes.Array[ctypes.c_ubyte] | ctypes.Array[ctypes.c_char] | bytes) -> str:
        raw = bytes(value)
        return raw.split(b"\x00", 1)[0].decode("utf-8", errors="ignore").strip()

    def _get_ip_device_details(self, config: NET_DVR_IPPARACFG_V40 | None, channel_number: int) -> dict[str, str | int] | None:
        if config is None:
            return None
        start_dchan = int(config.dwStartDChan)
        index = channel_number - start_dchan
        if index < 0 or index >= MAX_CHANNUM_V30:
            return None
        stream_mode = config.struStreamMode[index]
        stream_type = int(stream_mode.byGetStreamType)
        if stream_type == 0:
            chan_info = stream_mode.uGetStream.struChanInfo
            device_id = int(chan_info.byIPID) + int(chan_info.byIPIDHigh) * 256
        elif stream_type == 6:
            chan_info = stream_mode.uGetStream.struIPChan
            device_id = int(chan_info.wIPID)
        else:
            return None
        if device_id < 1 or device_id > MAX_IP_DEVICE_V40:
            return None
        device_info = config.struIPDevInfo[device_id - 1]
        return {
            "device_id": device_id,
            "host": self._decode_bytes(device_info.struIP.sIpV4),
            "port": int(device_info.wDVRPort) or 8000,
            "username": self._decode_bytes(device_info.sUserName),
            "password": self._decode_bytes(device_info.sPassword),
        }

    def _diagnose_ip_login(self, device_details: dict[str, str | int] | None) -> tuple[str, int | None, str]:
        if not device_details:
            return STATUS_OFFLINE, None, "Connection failed; device details are unavailable"
        host = str(device_details.get("host", "")).strip()
        username = str(device_details.get("username", "")).strip()
        password = str(device_details.get("password", ""))
        port = int(device_details.get("port", 8000))
        if not host:
            return STATUS_OFFLINE, None, "Connection failed; IPC address is empty"

        sdk = NativeHCNetSDK(DEFAULT_LIB_DIR)
        device = None
        try:
            sdk.init()
            device = sdk.login(host, port, username, password)
            return STATUS_ONLINE, None, ""
        except SDKLoginError as exc:
            error_code = exc.error_code
            if error_code == 1:
                return STATUS_ACCOUNT_ERROR, error_code, "Wrong username/password"
            if error_code == 44:
                return STATUS_OFFLINE, error_code, "Device offline"
            return STATUS_OFFLINE, error_code, str(exc)
        except Exception as exc:
            return STATUS_OFFLINE, None, str(exc)
        finally:
            try:
                if device is not None:
                    device.logout()
            except Exception:
                pass
            try:
                sdk.cleanup()
            except Exception:
                pass

    @staticmethod
    def _ip_error_state(digital_state: NET_DVR_DIGITAL_CHANNEL_STATE | None, channel_number: int, start_dchan: int) -> tuple[bool | None, bool | None, str, int | None, str]:
        if digital_state is None:
            return None, None, STATUS_UNKNOWN, None, ""
        index = channel_number - start_dchan
        if index < 0 or index >= MAX_CHANNUM_V30:
            return None, None, STATUS_UNKNOWN, None, ""
        code = int(digital_state.byDigitalChanState[index])
        if code <= 0:
            return None, None, STATUS_UNKNOWN, None, ""
        status_text, transport_online, video_present, error_text = DIGITAL_STATUS_TEXT.get(
            code,
            (f"STATE {code}", False, False, "Unknown digital channel state"),
        )
        return transport_online, video_present, status_text, code, error_text

    def _probe_channels(self, warnings: list[str] | None = None) -> list[ChannelInfo]:
        if self.service.mode != "real" or self.service.device is None:
            return self._demo_channels()

        base_channels = self.service.channels()
        config = None
        work_state = None
        digital_state = None

        try:
            config = self._get_ipparacfg_v40()
            if config is None and warnings is not None:
                warnings.append("IP channel configuration status is unavailable.")
        except Exception as exc:
            if warnings is not None:
                warnings.append(f"IP channel status query failed: {exc}")

        try:
            work_state = self._get_work_state_v30()
            if work_state is None and warnings is not None:
                warnings.append("Analog channel work state is unavailable.")
        except Exception as exc:
            if warnings is not None:
                warnings.append(f"Analog channel status query failed: {exc}")

        try:
            digital_state = self._get_digital_channel_state()
            if digital_state is None and warnings is not None:
                warnings.append("Digital channel state is unavailable.")
        except Exception as exc:
            if warnings is not None:
                warnings.append(f"Digital channel state query failed: {exc}")

        start_channel = int(getattr(self.service.device, "start_channel", 1))
        start_dchan = int(config.dwStartDChan) if config is not None else start_channel
        channels: list[ChannelInfo] = []
        for item in base_channels:
            if item.kind == "analog":
                analog_index = item.number - start_channel
                enabled = self._analog_enabled(config, analog_index)
                signal_lost = None
                if work_state is not None and 0 <= analog_index < MAX_CHANNUM_V30:
                    signal_lost = bool(work_state.struChanStatic[analog_index].bySignalStatic)
                video_present = None if signal_lost is None else not signal_lost
                status_text = self._status_text(
                    enabled=enabled,
                    transport_online=None,
                    video_present=video_present,
                    kind=item.kind,
                )
                channels.append(
                    ChannelInfo(
                        number=item.number,
                        name=item.name,
                        kind=item.kind,
                        is_online=bool(enabled and video_present is not False),
                        configured=enabled,
                        enabled=enabled,
                        transport_online=None,
                        video_present=video_present,
                        status_text=status_text,
                        error_code=None,
                        error_text="",
                    )
                )
                continue

            slot_state = self._ip_slot_state(config, item.number)
            transport_online = slot_state["transport_online"]
            enabled = bool(slot_state["enabled"])
            video_present = slot_state["video_present"]
            error_code = None
            error_text = ""
            if bool(slot_state["configured"]):
                detected_transport, detected_video, detected_status, detected_code, detected_error = self._ip_error_state(
                    digital_state,
                    item.number,
                    start_dchan,
                )
                if detected_transport is not None:
                    transport_online = detected_transport
                if detected_video is not None:
                    video_present = detected_video
                status_text = detected_status
                error_code = detected_code
                error_text = detected_error
                if status_text == STATUS_UNKNOWN:
                    status_text = self._status_text(
                        enabled=enabled,
                        transport_online=transport_online,
                        video_present=video_present,
                        kind=item.kind,
                    )
                if transport_online is False and error_code is None:
                    status_text, error_code, error_text = self._diagnose_ip_login(
                        self._get_ip_device_details(config, item.number)
                    )
                    transport_online = status_text == STATUS_ONLINE
                    video_present = transport_online
            else:
                status_text = STATUS_DISABLED
            channels.append(
                ChannelInfo(
                    number=item.number,
                    name=item.name,
                    kind=item.kind,
                    is_online=bool(transport_online is True),
                    configured=bool(slot_state["configured"]),
                    enabled=enabled,
                    transport_online=transport_online,
                    video_present=video_present,
                    status_text=status_text,
                    error_code=error_code,
                    error_text=error_text,
                )
            )
        return channels

    def diagnose_and_connect(self, params: ConnectionParams) -> tuple[DiagnosticReport, RuntimeConfig]:
        warnings: list[str] = []
        error = ""
        mode = "disconnected"
        try:
            mode = self.service.login(
                ConnectionParams(
                    host=params.host,
                    port=params.port,
                    username=params.username,
                    password=params.password,
                ),
                allow_demo_fallback=True,
            )
        except Exception as exc:
            error = str(exc)

        channels = self._probe_channels(warnings)

        missing_sdk_files = self._missing_sdk_files()
        if missing_sdk_files:
            warnings.append(f"SDK layout incomplete under {DEFAULT_LIB_DIR}")
        if mode == "demo":
            warnings.append("Connected in demo mode; real SDK login did not succeed.")
        if self.service.last_error:
            warnings.append(self.service.last_error)

        device_label = params.host
        if self.service.device is not None:
            serial = getattr(self.service.device, "serial_number", "") or getattr(self.service.device, "serial", "")
            model = getattr(self.service.device, "model", "") or getattr(self.service.device, "device_model", "")
            pretty = " ".join(part for part in (str(model).strip(), str(serial).strip()) if part)
            if pretty:
                device_label = pretty

        generated_at = datetime.now().isoformat(timespec="seconds")
        report = DiagnosticReport(
            plugin_name="hikvision",
            connected=self.service.connected,
            mode=mode,
            generated_at=generated_at,
            device_label=device_label,
            channels=channels,
            missing_sdk_files=missing_sdk_files,
            warnings=warnings,
            error=error,
        )

        runtime_config = RuntimeConfig(
            plugin_name="hikvision",
            created_at=generated_at,
            connection=params,
            detected_mode=mode,
            baseline_channels=channels,
            current_channels=channels,
            diagnostics_summary=report.as_text(),
            last_diagnostic_at=generated_at,
            last_diagnostic_summary=report.as_text(),
            online_views=[],
            selected_online_view_id="",
        )
        self.current_params = params
        self.last_report = report
        return report, runtime_config

    def disconnect(self) -> None:
        for handle in list(self._live_sessions):
            try:
                self.stop_live(handle)
            except Exception:
                pass
        for handle in list(self._playback_sessions):
            try:
                self.stop_archive_playback(handle)
            except Exception:
                pass
        self.service.logout()

    def ensure_connected(self) -> None:
        if self.service.connected:
            return
        if self.current_params is None:
            raise RuntimeError("No runtime config loaded")
        self.service.login(
            ConnectionParams(
                host=self.current_params.host,
                port=self.current_params.port,
                username=self.current_params.username,
                password=self.current_params.password,
            ),
            allow_demo_fallback=True,
        )

    def list_channels(self) -> list[ChannelInfo]:
        self.ensure_connected()
        return self._probe_channels()

    def get_capabilities(self) -> PluginCapabilities:
        supports_live = self.service.mode == "real"
        return PluginCapabilities(
            supports_live=supports_live,
            supports_archive=True,
            supports_native_surface_binding=True,
            supports_grid_low_res_profile=supports_live,
            supports_profile_switch=supports_live,
            supports_archive_seek=True,
            supports_rate_control=True,
            supports_frame_step=True,
            supports_native_zoom=True,
            supports_snapshot=supports_live,
            supports_diagnostics=True,
            supports_archive_coverage_report=True,
        )

    def list_archive_days(self, channel: int, year: int, month: int) -> set[int]:
        self.ensure_connected()
        return self.service.archive_days(channel, year, month)

    def list_archive_files(self, channel: int, day: datetime) -> list[ArchiveFile]:
        self.ensure_connected()
        return [
            ArchiveFile(
                filename=item.filename,
                start_time=item.start_time,
                end_time=item.end_time,
                size_bytes=item.size_bytes,
            )
            for item in self.service.find_files(channel, day)
        ]

    def list_archive_segments(self, channel: int, day: datetime) -> list[ArchiveSegment]:
        return [
            ArchiveSegment(
                start_time=item.start_time,
                end_time=item.end_time,
                label=item.filename,
            )
            for item in self.list_archive_files(channel, day)
        ]

    def start_archive_playback(
        self,
        *,
        channel: int,
        start_time: datetime,
        end_time: datetime,
        resume_time: datetime,
        window_id: int,
    ) -> int:
        self.ensure_connected()
        handle = self.service.playback_by_time_hwnd(
            channel=channel,
            start_time=start_time,
            end_time=end_time,
            resume_time=resume_time,
            hwnd=window_id,
        )
        if handle < 0:
            raise RuntimeError(self.service.last_error or "playback_by_time_hwnd failed")
        ok, _ = self.service.playback_control_v40(handle, NET_DVR_PLAYSTART, in_buffer=None, out_buffer=None)
        if not ok:
            error = self.service.last_error or "NET_DVR_PLAYSTART failed"
            self.service.stop_playback(handle)
            raise RuntimeError(error)
        self._playback_sessions[int(handle)] = PlaybackSessionState(
            start_time=start_time,
            end_time=end_time,
            window_id=window_id,
            play_port=0,
            last_system_time=resume_time,
        )
        self._start_playback_keepalive(int(handle))
        return int(handle)

    def stop_archive_playback(self, handle: int) -> None:
        if handle < 0:
            return
        self.ensure_connected()
        self._stop_playback_keepalive(int(handle))
        ok = self.service.stop_playback(handle)
        self._playback_sessions.pop(int(handle), None)
        if not ok:
            raise RuntimeError(self.service.last_error or "stop_playback failed")

    def _resolve_sdk_library(self):
        sdk_obj = None
        if self.service.device is not None and hasattr(self.service.device, "_sdk"):
            sdk_obj = self.service.device._sdk
        if sdk_obj is None and hasattr(self.service, "_sdk"):
            sdk_obj = self.service._sdk
        if sdk_obj is None:
            raise RuntimeError("SDK object not available")
        if hasattr(sdk_obj, "_sdk"):
            sdk_obj = sdk_obj._sdk
        if sdk_obj is None:
            raise RuntimeError("hcnetsdk library not available")
        return sdk_obj

    def _get_last_sdk_error(self, hcnetsdk) -> int:
        if not hasattr(hcnetsdk, "NET_DVR_GetLastError"):
            return -1
        try:
            return int(hcnetsdk.NET_DVR_GetLastError())
        except Exception:
            return -1

    def _snapshot_jpeg_params(self) -> NET_DVR_JPEGPARA:
        params = NET_DVR_JPEGPARA()
        params.wPicSize = 0xFF
        params.wPicQuality = 1
        return params

    def _capture_jpeg_to_memory(self, *, user_id: int, channel: int) -> bytes | None:
        hcnetsdk = self._resolve_sdk_library()
        if not hasattr(hcnetsdk, "NET_DVR_CaptureJPEGPicture_NEW"):
            return None

        hcnetsdk.NET_DVR_CaptureJPEGPicture_NEW.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(NET_DVR_JPEGPARA),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        hcnetsdk.NET_DVR_CaptureJPEGPicture_NEW.restype = ctypes.c_bool

        jpeg_params = self._snapshot_jpeg_params()
        for buffer_size in (1 * 1024 * 1024, 4 * 1024 * 1024, 8 * 1024 * 1024, 16 * 1024 * 1024):
            buffer = ctypes.create_string_buffer(buffer_size)
            size_returned = ctypes.c_uint32(0)
            with self.service._sdk_lock:
                ok = hcnetsdk.NET_DVR_CaptureJPEGPicture_NEW(
                    int(user_id),
                    int(channel),
                    ctypes.byref(jpeg_params),
                    ctypes.cast(buffer, ctypes.c_void_p),
                    ctypes.c_uint32(buffer_size),
                    ctypes.byref(size_returned),
                )
            if ok and size_returned.value > 0:
                return bytes(buffer.raw[: size_returned.value])
        return None

    def _capture_jpeg_to_file(self, *, user_id: int, channel: int) -> bytes:
        hcnetsdk = self._resolve_sdk_library()
        if not hasattr(hcnetsdk, "NET_DVR_CaptureJPEGPicture"):
            raise RuntimeError("NET_DVR_CaptureJPEGPicture is not available")

        hcnetsdk.NET_DVR_CaptureJPEGPicture.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(NET_DVR_JPEGPARA),
            ctypes.c_char_p,
        ]
        hcnetsdk.NET_DVR_CaptureJPEGPicture.restype = ctypes.c_bool

        jpeg_params = self._snapshot_jpeg_params()
        temp_file = tempfile.NamedTemporaryFile(prefix="hik-snapshot-", suffix=".jpg", delete=False)
        temp_path = temp_file.name
        temp_file.close()
        try:
            with self.service._sdk_lock:
                ok = hcnetsdk.NET_DVR_CaptureJPEGPicture(
                    int(user_id),
                    int(channel),
                    ctypes.byref(jpeg_params),
                    temp_path.encode("utf-8"),
                )
            if not ok:
                error_code = self._get_last_sdk_error(hcnetsdk)
                raise RuntimeError(f"NET_DVR_CaptureJPEGPicture failed: error={error_code}")
            return Path(temp_path).read_bytes()
        finally:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass

    def _start_real_preview(
        self,
        *,
        user_id: int,
        channel: int,
        host_binding: VideoHostBinding,
        profile: StreamProfile,
    ) -> int:
        if self.service.mode != "real" or self.service.device is None:
            raise RuntimeError("Not connected in real mode")

        hcnetsdk = self._resolve_sdk_library()
        if not hasattr(hcnetsdk, "NET_DVR_RealPlay_V40"):
            raise RuntimeError("NET_DVR_RealPlay_V40 not available")

        hcnetsdk.NET_DVR_RealPlay_V40.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(NET_DVR_PREVIEWINFO),
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        hcnetsdk.NET_DVR_RealPlay_V40.restype = ctypes.c_int

        preview_info = NET_DVR_PREVIEWINFO()
        ctypes.memset(ctypes.byref(preview_info), 0, ctypes.sizeof(preview_info))
        preview_info.lChannel = channel
        preview_info.dwStreamType = int(profile.stream_type)
        preview_info.dwLinkMode = 0
        preview_info.hPlayWnd = int(host_binding.window_id)
        preview_info.bBlocked = 1
        preview_info.byPreviewMode = 0
        preview_info.dwDisplayBufNum = 1

        logger.info(
            "Starting live preview user_id=%s channel=%s profile=%s stream_type=%s window_id=%s",
            user_id,
            channel,
            profile.id,
            profile.stream_type,
            host_binding.window_id,
        )
        with self.service._sdk_lock:
            handle = hcnetsdk.NET_DVR_RealPlay_V40(
                user_id,
                ctypes.byref(preview_info),
                None,
                None,
            )

        if handle < 0:
            error_code = -1
            if hasattr(hcnetsdk, "NET_DVR_GetLastError"):
                try:
                    error_code = hcnetsdk.NET_DVR_GetLastError()
                except Exception:
                    pass
            raise RuntimeError(f"NET_DVR_RealPlay_V40 failed: error={error_code}")
        return int(handle)

    def start_live(
        self,
        *,
        channel: int,
        profile: StreamProfile,
        host_binding: VideoHostBinding,
    ) -> int:
        self.ensure_connected()
        user_id = self.service.device.user_id if hasattr(self.service.device, "user_id") else -1
        if user_id < 0:
            raise RuntimeError("Unable to get user_id from device")

        handle = self._start_real_preview(
            user_id=user_id,
            channel=channel,
            host_binding=host_binding,
            profile=profile,
        )
        self._live_sessions[int(handle)] = LiveSessionState(
            channel=channel,
            user_id=user_id,
            window_id=host_binding.window_id,
            width=host_binding.width,
            height=host_binding.height,
            profile=profile,
            play_port=None,
        )
        logger.info("Live preview started handle=%s channel=%s profile=%s", handle, channel, profile.id)
        return int(handle)

    def stop_live(self, session_id: int) -> None:
        if session_id < 0:
            return

        self.ensure_connected()
        self._live_sessions.pop(int(session_id), None)
        hcnetsdk = self._resolve_sdk_library()
        if not hasattr(hcnetsdk, "NET_DVR_StopRealPlay"):
            raise RuntimeError("NET_DVR_StopRealPlay not available")
        hcnetsdk.NET_DVR_StopRealPlay.argtypes = [ctypes.c_int]
        hcnetsdk.NET_DVR_StopRealPlay.restype = ctypes.c_bool
        with self.service._sdk_lock:
            ok = hcnetsdk.NET_DVR_StopRealPlay(int(session_id))
        if not ok:
            raise RuntimeError("NET_DVR_StopRealPlay returned False")

    def switch_live_profile(
        self,
        *,
        session_id: int,
        profile: StreamProfile,
        host_binding: VideoHostBinding,
    ) -> int:
        session = self._live_sessions.get(int(session_id))
        if session is None:
            raise RuntimeError("Live session is not active")
        channel = session.channel
        self.stop_live(session_id)
        return self.start_live(
            channel=channel,
            profile=profile,
            host_binding=host_binding,
        )

    def start_live_preview(
        self,
        *,
        channel: int,
        window_id: int,
    ) -> int:
        return self.start_live(
            channel=channel,
            profile=StreamProfile(id="main", label="Main stream", stream_type=0),
            host_binding=VideoHostBinding(window_id=window_id, width=0, height=0),
        )

    def stop_live_preview(self, handle: int) -> None:
        self.stop_live(handle)

    def request_live_snapshot(self, channel: int) -> SnapshotResult:
        self.ensure_connected()
        if self.service.mode != "real" or self.service.device is None:
            raise RuntimeError("Snapshot capture is available only in real mode")
        user_id = int(getattr(self.service.device, "user_id", -1))
        if user_id < 0:
            raise RuntimeError("Unable to get user_id from device")

        image_bytes = self._capture_jpeg_to_memory(user_id=user_id, channel=channel)
        if not image_bytes:
            image_bytes = self._capture_jpeg_to_file(user_id=user_id, channel=channel)
        if not image_bytes:
            raise RuntimeError("Snapshot capture returned empty image data")

        return SnapshotResult(
            channel=int(channel),
            image_bytes=image_bytes,
            captured_at=datetime.now().isoformat(timespec="seconds"),
        )

    def resize_surface(
        self,
        session_id: int,
        width: int,
        height: int,
        *,
        window_id: int | None = None,
    ) -> None:
        if session_id < 0:
            return
        self.ensure_connected()
        live_session = self._live_sessions.get(int(session_id))
        if live_session is not None:
            if window_id is not None and int(window_id) > 0:
                live_session.window_id = int(window_id)
            live_session.width = width
            live_session.height = height
            # Linux live preview resize requires explicit window resolution refresh.
            try:
                hcnetsdk = self._resolve_sdk_library()
                if hasattr(hcnetsdk, "NET_DVR_ChangeWndResolution"):
                    hcnetsdk.NET_DVR_ChangeWndResolution.argtypes = [ctypes.c_int]
                    hcnetsdk.NET_DVR_ChangeWndResolution.restype = ctypes.c_bool
                    with self.service._sdk_lock:
                        ok = hcnetsdk.NET_DVR_ChangeWndResolution(int(session_id))
                    if not ok:
                        logger.debug("NET_DVR_ChangeWndResolution returned False for live session_id=%s", session_id)
            except Exception as exc:
                logger.debug("NET_DVR_ChangeWndResolution failed for live session_id=%s: %s", session_id, exc)
            try:
                hcnetsdk = self._resolve_sdk_library()
                if hasattr(hcnetsdk, "NET_DVR_RealPlayRestart") and live_session.window_id > 0:
                    hcnetsdk.NET_DVR_RealPlayRestart.argtypes = [ctypes.c_int, ctypes.c_void_p]
                    hcnetsdk.NET_DVR_RealPlayRestart.restype = ctypes.c_bool
                    with self.service._sdk_lock:
                        ok = hcnetsdk.NET_DVR_RealPlayRestart(
                            int(session_id),
                            ctypes.c_void_p(int(live_session.window_id)),
                        )
                    if not ok:
                        logger.warning(
                            "NET_DVR_RealPlayRestart returned False for session_id=%s window_id=%s",
                            session_id,
                            live_session.window_id,
                        )
            except Exception as exc:
                logger.warning("Live resize restart failed for session_id=%s: %s", session_id, exc)
            return
        ok, _ = self.service.playback_control_v40(session_id, NET_DVR_CHANGEWNDRESOLUTION, in_buffer=None, out_buffer=None)
        if not ok:
            raise RuntimeError(self.service.last_error or "NET_DVR_CHANGEWNDRESOLUTION failed")
        session = self._playback_sessions.get(session_id)
        if session and session.play_port is not None and self._playctrl is not None:
            self._playctrl.refresh_play(session.play_port)

    def set_zoom(self, session_id: int, zoom_state: ZoomState) -> None:
        if session_id < 0:
            return
        self.ensure_connected()
        window_id = 0
        live_session = self._live_sessions.get(int(session_id))
        if live_session is not None:
            port = self._resolve_live_play_port(int(session_id), live_session)
            window_id = int(live_session.window_id)
        else:
            session = self._playback_sessions.get(session_id)
            if session is None:
                return
            port = self._probe_play_port(session)
            window_id = int(session.window_id)
        if port is None or self._playctrl is None or window_id <= 0:
            return
        picture_size = self._playctrl.get_picture_size(port)
        if picture_size is None:
            return
        fw, fh = picture_size.width, picture_size.height
        left = int(zoom_state.x * fw)
        top = int(zoom_state.y * fh)
        right = int((zoom_state.x + zoom_state.width) * fw)
        bottom = int((zoom_state.y + zoom_state.height) * fh)
        left = max(0, min(left, fw - 1))
        top = max(0, min(top, fh - 1))
        right = max(left + 1, min(right, fw))
        bottom = max(top + 1, min(bottom, fh))
        rect = RECT(left, top, right, bottom)
        success = self._playctrl.set_display_region(port, rect=rect, hwnd=window_id, enable=True)
        if success:
            self._playctrl.refresh_play(port)

    def reset_zoom(self, session_id: int) -> None:
        if session_id < 0:
            return
        self.ensure_connected()
        window_id = 0
        live_session = self._live_sessions.get(int(session_id))
        if live_session is not None:
            port = self._resolve_live_play_port(int(session_id), live_session)
            window_id = int(live_session.window_id)
        else:
            session = self._playback_sessions.get(session_id)
            if session is None:
                return
            port = self._probe_play_port(session)
            window_id = int(session.window_id)
        if port is None or self._playctrl is None or window_id <= 0:
            return
        success = self._playctrl.set_display_region(port, rect=None, hwnd=window_id, enable=False)
        if success:
            self._playctrl.refresh_play(port)

    def seek_archive_playback(self, handle: int, target_time: datetime) -> None:
        if handle < 0:
            raise RuntimeError("Playback handle is not active")
        self.ensure_connected()
        ok = self.service.playback_set_time(handle, target_time)
        if not ok:
            raise RuntimeError(self.service.last_error or "playback_set_time failed")
        session = self._playback_sessions.get(int(handle))
        if session is not None:
            session.last_system_time = target_time

    def pause_archive_playback(self, handle: int) -> None:
        if handle < 0:
            raise RuntimeError("Playback handle is not active")
        self.ensure_connected()
        ok, _ = self.service.playback_control_v40(handle, NET_DVR_PLAYPAUSE, in_buffer=None, out_buffer=None)
        if not ok:
            raise RuntimeError(self.service.last_error or "NET_DVR_PLAYPAUSE failed")

    def resume_archive_playback(self, handle: int) -> None:
        if handle < 0:
            raise RuntimeError("Playback handle is not active")
        self.ensure_connected()
        ok, _ = self.service.playback_control_v40(handle, NET_DVR_PLAYRESTART, in_buffer=None, out_buffer=None)
        if not ok:
            ok, _ = self.service.playback_control_v40(handle, NET_DVR_PLAYSTART, in_buffer=None, out_buffer=None)
        if not ok:
            raise RuntimeError(self.service.last_error or "NET_DVR_PLAYRESTART failed")

    def set_archive_playback_speed(self, handle: int, factor: float) -> None:
        if handle < 0:
            raise RuntimeError("Playback handle is not active")
        self.ensure_connected()
        try:
            desired = float(factor)
        except Exception:
            desired = 1.0
        if desired <= 0:
            desired = 1.0

        if desired == 1.0:
            ok, _ = self.service.playback_control_v40(handle, NET_DVR_PLAYNORMAL, in_buffer=None, out_buffer=None)
            if not ok:
                raise RuntimeError(self.service.last_error or "NET_DVR_PLAYNORMAL failed")
            return

        if desired > 1.0:
            steps = max(int(round(math.log(desired, 2))), 0)
            cmd = NET_DVR_PLAYFAST
        else:
            steps = max(int(round(math.log(1.0 / desired, 2))), 0)
            cmd = NET_DVR_PLAYSLOW

        ok, _ = self.service.playback_control_v40(handle, NET_DVR_PLAYNORMAL, in_buffer=None, out_buffer=None)
        if not ok:
            raise RuntimeError(self.service.last_error or "NET_DVR_PLAYNORMAL failed")
        for _ in range(steps):
            ok, _ = self.service.playback_control_v40(handle, cmd, in_buffer=None, out_buffer=None)
            if not ok:
                raise RuntimeError(self.service.last_error or "Playback speed command failed")

    def step_archive_playback_frame(self, handle: int) -> None:
        if handle < 0:
            raise RuntimeError("Playback handle is not active")
        self.ensure_connected()
        self.service.playback_control_v40(handle, NET_DVR_PLAYPAUSE, in_buffer=None, out_buffer=None)
        ok, _ = self.service.playback_control_v40(handle, NET_DVR_PLAYFRAME, in_buffer=None, out_buffer=None)
        if not ok:
            raise RuntimeError(self.service.last_error or "NET_DVR_PLAYFRAME failed")

    def get_archive_playback_time(self, handle: int) -> datetime | None:
        if handle < 0:
            return None
        self.ensure_connected()
        session = self._playback_sessions.get(int(handle))
        if session is None:
            return None
        if not self._ensure_playctrl_loaded() or self._playctrl is None:
            return session.last_system_time
        port = self._probe_play_port(session)
        if port is None:
            return session.last_system_time
        system_time = self._playctrl.get_system_time(int(port))
        if system_time is None:
            return session.last_system_time
        if system_time < session.start_time:
            system_time = session.start_time
        if system_time > session.end_time:
            system_time = session.end_time
        session.last_system_time = system_time
        return system_time

    def build_archive_coverage_report(
        self,
        *,
        channel: int,
        period_start: datetime,
        period_end: datetime,
    ) -> ArchiveCoverageReport:
        self.ensure_connected()
        if period_end <= period_start:
            raise ValueError("period_end must be greater than period_start")

        day_cursor = period_start.replace(hour=0, minute=0, second=0, microsecond=0)
        merged: list[tuple[datetime, datetime]] = []
        segments: list[tuple[datetime, datetime]] = []
        while day_cursor <= period_end:
            for item in self.service.find_files(channel, day_cursor):
                start = max(item.start_time, period_start)
                end = min(item.end_time, period_end)
                if end <= start:
                    continue
                segments.append((start, end))
            day_cursor += timedelta(days=1)

        for start, end in sorted(segments, key=lambda item: item[0]):
            if not merged or start > merged[-1][1]:
                merged.append((start, end))
                continue
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))

        covered_seconds = int(sum((end - start).total_seconds() for start, end in merged))
        total_seconds = max(1, int((period_end - period_start).total_seconds()))
        coverage_percent = covered_seconds / total_seconds * 100.0

        gaps: list[tuple[datetime, datetime]] = []
        gap_cursor = period_start
        for start, end in merged:
            if start > gap_cursor:
                gaps.append((gap_cursor, start))
            gap_cursor = max(gap_cursor, end)
        if gap_cursor < period_end:
            gaps.append((gap_cursor, period_end))

        return ArchiveCoverageReport(
            channel=channel,
            period_start=period_start,
            period_end=period_end,
            total_seconds=total_seconds,
            covered_seconds=covered_seconds,
            coverage_percent=coverage_percent,
            segment_count=len(merged),
            gaps=gaps,
        )
