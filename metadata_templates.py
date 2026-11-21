"""BOM 三要素常量表 - 设备类型清单 + 资源描述符模板

供 metadata.py 共用两处:
  1) BOM 条目校验 (_validate_bom_item): 判断 device_type 是否在清单内
  2) GET /api/metadata/device-templates: 直接作为接口返回内容

字段名与接口返回保持一致, 修改时两边同时生效:
  DEVICE_TYPES       key / name / programmable
  RESOURCE_TEMPLATES key / name / pattern / hint
"""

from __future__ import annotations

# 设备类型清单 (device_type 字段取值: 中文名或 key 均可)
DEVICE_TYPES: list[dict] = [
    {"key": "scope", "name": "示波器", "programmable": True},
    {"key": "signal_gen", "name": "信号发生器", "programmable": True},
    {"key": "dmm", "name": "万用表", "programmable": True},
    {"key": "psu", "name": "可编程电源", "programmable": True},
    {"key": "eload", "name": "电子负载", "programmable": True},
    {"key": "switch", "name": "开关矩阵", "programmable": True},
    {"key": "daq", "name": "数据采集", "programmable": True},
    {"key": "thermal", "name": "温控设备", "programmable": True},
    {"key": "motion", "name": "运动控制器", "programmable": True},
    {"key": "serial_dev", "name": "串口设备", "programmable": True},
    {"key": "fixture", "name": "夹具/工装", "programmable": False},
    {"key": "other", "name": "其他（VISA / 自定义）", "programmable": True},
]

# 资源描述符模板 (resource 字段的填写范式, 供前端做占位提示)
RESOURCE_TEMPLATES: list[dict] = [
    {
        "key": "socket",
        "name": "SOCKET",
        "pattern": "TCPIP0::192.168.1.10::5025::SOCKET",
        "hint": "网口 SCPI：IP 与端口",
    },
    {
        "key": "visa_lan",
        "name": "VISA(LAN)",
        "pattern": "TCPIP0::192.168.1.10::INSTR",
        "hint": "VISA over LAN",
    },
    {
        "key": "usb",
        "name": "USB",
        "pattern": "USB0::0x0699::0x0401::C010001::INSTR",
        "hint": "USB0::厂商ID::产品ID::序列号::INSTR",
    },
    {
        "key": "uart",
        "name": "UART",
        "pattern": "COM3:9600:8N1",
        "hint": "串口:波特率:数据位校验停止位，或 ASRL3::9600::INSTR",
    },
    {
        "key": "gpib",
        "name": "GPIB",
        "pattern": "GPIB0::12::INSTR",
        "hint": "GPIB 板卡::地址::INSTR",
    },
    {
        "key": "other",
        "name": "OTHER",
        "pattern": "",
        "hint": "其余设备或自定义 VISA 地址，直接填写",
    },
]

# 校验用索引: device_type 允许写 key, 也允许写中文名
DEVICE_TYPE_KEYS = {d["key"] for d in DEVICE_TYPES}
DEVICE_TYPE_NAMES = {d["name"] for d in DEVICE_TYPES}
KNOWN_DEVICE_TYPES = DEVICE_TYPE_KEYS | DEVICE_TYPE_NAMES
