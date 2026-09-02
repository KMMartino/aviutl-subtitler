"""Vendor-neutral Windows GPU memory budgeting through DXGI."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class VideoMemoryBudget:
    dedicated_bytes: int
    budget_bytes: int
    current_usage_bytes: int

    @property
    def available_budget_bytes(self) -> int:
        return max(0, self.budget_bytes - self.current_usage_bytes)


def primary_video_memory_budget() -> VideoMemoryBudget | None:
    """Read adapter 0's local-memory capacity and current process budget."""
    if os.name != "nt":
        return None
    try:
        return _query_primary_video_memory_budget()
    except (AttributeError, OSError, RuntimeError, ValueError):
        return None


def _query_primary_video_memory_budget() -> VideoMemoryBudget:
    import ctypes
    import uuid

    class Guid(ctypes.Structure):
        _fields_ = [
            ("data1", ctypes.c_uint32),
            ("data2", ctypes.c_uint16),
            ("data3", ctypes.c_uint16),
            ("data4", ctypes.c_ubyte * 8),
        ]

        @classmethod
        def from_string(cls, value: str) -> Guid:
            raw = uuid.UUID(value).bytes_le
            return cls.from_buffer_copy(raw)

    class Luid(ctypes.Structure):
        _fields_ = [("low_part", ctypes.c_uint32), ("high_part", ctypes.c_int32)]

    class AdapterDescription(ctypes.Structure):
        _fields_ = [
            ("description", ctypes.c_wchar * 128),
            ("vendor_id", ctypes.c_uint32),
            ("device_id", ctypes.c_uint32),
            ("subsystem_id", ctypes.c_uint32),
            ("revision", ctypes.c_uint32),
            ("dedicated_video_memory", ctypes.c_size_t),
            ("dedicated_system_memory", ctypes.c_size_t),
            ("shared_system_memory", ctypes.c_size_t),
            ("adapter_luid", Luid),
            ("flags", ctypes.c_uint32),
        ]

    class QueryVideoMemoryInfo(ctypes.Structure):
        _fields_ = [
            ("budget", ctypes.c_uint64),
            ("current_usage", ctypes.c_uint64),
            ("available_for_reservation", ctypes.c_uint64),
            ("current_reservation", ctypes.c_uint64),
        ]

    def method(pointer: ctypes.c_void_p, index: int, result_type, *argument_types):  # type: ignore[no-untyped-def]
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        address = table[index]
        return ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *argument_types)(address)

    def require_success(result: int, operation: str) -> None:
        if result < 0:
            raise OSError(f"{operation} failed with HRESULT 0x{result & 0xFFFFFFFF:08X}")

    factory_iid = Guid.from_string("770aae78-f26f-4dba-a829-253c83d1b387")
    adapter3_iid = Guid.from_string("645967A4-1392-4310-A798-8053CE3E93FD")
    factory = ctypes.c_void_p()
    adapter = ctypes.c_void_p()
    adapter3 = ctypes.c_void_p()
    dxgi = ctypes.WinDLL("dxgi")
    create_factory = dxgi.CreateDXGIFactory1
    create_factory.argtypes = [ctypes.POINTER(Guid), ctypes.POINTER(ctypes.c_void_p)]
    create_factory.restype = ctypes.c_long
    try:
        require_success(create_factory(ctypes.byref(factory_iid), ctypes.byref(factory)), "CreateDXGIFactory1")
        enum_adapters = method(factory, 12, ctypes.c_long, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))
        require_success(enum_adapters(factory, 0, ctypes.byref(adapter)), "IDXGIFactory1::EnumAdapters1")
        description = AdapterDescription()
        get_description = method(adapter, 10, ctypes.c_long, ctypes.POINTER(AdapterDescription))
        require_success(get_description(adapter, ctypes.byref(description)), "IDXGIAdapter1::GetDesc1")
        query_interface = method(
            adapter,
            0,
            ctypes.c_long,
            ctypes.POINTER(Guid),
            ctypes.POINTER(ctypes.c_void_p),
        )
        require_success(
            query_interface(adapter, ctypes.byref(adapter3_iid), ctypes.byref(adapter3)),
            "IDXGIAdapter::QueryInterface(IDXGIAdapter3)",
        )
        memory = QueryVideoMemoryInfo()
        query_memory = method(
            adapter3,
            14,
            ctypes.c_long,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(QueryVideoMemoryInfo),
        )
        require_success(
            query_memory(adapter3, 0, 0, ctypes.byref(memory)),
            "IDXGIAdapter3::QueryVideoMemoryInfo",
        )
        return VideoMemoryBudget(
            dedicated_bytes=int(description.dedicated_video_memory),
            budget_bytes=int(memory.budget),
            current_usage_bytes=int(memory.current_usage),
        )
    finally:
        for pointer in (adapter3, adapter, factory):
            if pointer.value:
                release = method(pointer, 2, ctypes.c_ulong)
                release(pointer)
