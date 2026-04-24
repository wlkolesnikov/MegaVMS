from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
import os
import threading
from pathlib import Path
import sys

from contracts import (
    ArchiveCoverageReport,
    ArchiveFile,
    ArchiveSegment,
    ChannelInfo,
    ConnectionParams,
    DiagnosticReport,
    PluginCapabilities,
    RuntimeConfig,
    StreamProfile,
    VideoHostBinding,
    ZoomState,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
QT_ROOT = REPO_ROOT / "sdk-hik-QT"
if str(QT_ROOT) not in sys.path:
    sys.path.insert(0, str(QT_ROOT))

from hikvision_player.config import DEFAULT_LIB_DIR, validate_sdk_layout  # type: ignore  # noqa: E402

from hikvision_player.sdk.device import (  # type: ignore  # noqa: E402
    ConnectionParams as QtConnectionParams,
    HikvisionDeviceService,
)
from hikvision_player.sdk.playctrl_native import PlayCtrl, RECT  # type: ignore  # noqa: E402
from hikvision_player.sdk.playctr import NET_DVR_PLAYSTART  # type: ignore  # noqa: E402
from hikvision_player.sdk.playctr import (  # type: ignore  # noqa: E402
    NET_DVR_CHANGEWNDRESOLUTION,
    NET_DVR_PLAYFAST,
    NET_DVR_PLAYFRAME,
    NET_DVR_PLAYNORMAL,
    NET_DVR_PLAYPAUSE,
    NET_DVR_PLAYRESTART,
    NET_DVR_PLAYSLOW,
)
from hikvision_sdk.exceptions import LoginError  # type: ignore  # noqa: E402


logger = logging.getLogger(__name__)


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

        import hikvision_sdk  # type: ignore

        sdk = hikvision_sdk.HCNetSDK()
        device = None
        try:
            sdk.init()
            if hasattr(sdk, "_sdk") and hasattr(sdk._sdk, "NET_DVR_SetConnectTime"):
                sdk._sdk.NET_DVR_SetConnectTime(1000, 1)
            device = sdk.login(host, port, username, password)
            return STATUS_ONLINE, None, ""
        except LoginError as exc:
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
                    sdk.logout(device.user_id)
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
                QtConnectionParams(
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
            QtConnectionParams(
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
            supports_snapshot=False,
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
        session = self._playback_sessions.get(session_id)
        if session is None:
            return
        port = self._probe_play_port(session)
        if port is None or self._playctrl is None:
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
        success = self._playctrl.set_display_region(port, rect=rect, hwnd=session.window_id, enable=True)
        if success:
            self._playctrl.refresh_play(port)

    def reset_zoom(self, session_id: int) -> None:
        if session_id < 0:
            return
        self.ensure_connected()
        session = self._playback_sessions.get(session_id)
        if session is None:
            return
        port = self._probe_play_port(session)
        if port is None or self._playctrl is None:
            return
        success = self._playctrl.set_display_region(port, rect=None, hwnd=session.window_id, enable=False)
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
