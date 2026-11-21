import { api } from './api.js'

// device-templates 接口不可用（404 / 字段缺失）时的内置后备清单，保证 BOM 编辑器仍可工作
export const FALLBACK_DEVICE_TYPES = [
  { key: 'scope', name: '示波器' },
  { key: 'signal_gen', name: '信号发生器' },
  { key: 'dmm', name: '万用表' },
  { key: 'psu', name: '可编程电源' },
  { key: 'eload', name: '电子负载' },
  { key: 'switch', name: '开关矩阵' },
  { key: 'daq', name: '数据采集' },
  { key: 'thermal', name: '温控设备' },
  { key: 'motion', name: '运动控制器' },
  { key: 'serial_dev', name: '串口设备' },
  { key: 'fixture', name: '夹具·工装' },
  { key: 'other', name: '其他（VISA·自定义）' }
]

export const FALLBACK_RESOURCE_TEMPLATES = [
  { key: 'socket', name: 'SOCKET', pattern: 'TCPIP0::192.168.1.10::5025::SOCKET', hint: '网口 SCPI：IP 与端口' },
  { key: 'visa_lan', name: 'VISA(LAN)', pattern: 'TCPIP0::192.168.1.10::INSTR', hint: 'VISA over LAN' },
  { key: 'usb', name: 'USB', pattern: 'USB0::0x0699::0x0401::C010001::INSTR', hint: 'USB0::厂商ID::产品ID::序列号::INSTR' },
  { key: 'uart', name: 'UART', pattern: 'COM3:9600:8N1', hint: '串口:波特率:数据位校验停止位，或 ASRL3::9600::INSTR' },
  { key: 'gpib', name: 'GPIB', pattern: 'GPIB0::12::INSTR', hint: 'GPIB 板卡::地址::INSTR' },
  { key: 'other', name: 'OTHER', pattern: '', hint: '其余设备或自定义 VISA 地址，直接填写' }
]

function normalizeDeviceTypes(raw) {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((d) => d && (d.name || d.key))
    .map((d) => ({ key: d.key || d.name, name: d.name || d.key }))
}

function normalizeResourceTemplates(raw) {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((t) => t && (t.name || t.key))
    .map((t) => ({
      key: t.key || t.name,
      name: t.name || t.key,
      pattern: t.pattern || '',
      hint: t.hint || ''
    }))
}

/**
 * 读取 BOM 三要素的下拉数据源。
 * 接口 404 / 抛错 / 返回空清单时回退内置清单，source 标明实际来源（api / fallback）。
 */
export async function loadBomTemplates() {
  try {
    const data = await api.metadataDeviceTemplates()
    const deviceTypes = normalizeDeviceTypes(data && data.device_types)
    const resourceTemplates = normalizeResourceTemplates(data && data.resource_templates)
    const fromApi = deviceTypes.length > 0 && resourceTemplates.length > 0
    return {
      deviceTypes: deviceTypes.length ? deviceTypes : FALLBACK_DEVICE_TYPES,
      resourceTemplates: resourceTemplates.length ? resourceTemplates : FALLBACK_RESOURCE_TEMPLATES,
      source: fromApi ? 'api' : 'fallback'
    }
  } catch {
    return {
      deviceTypes: FALLBACK_DEVICE_TYPES,
      resourceTemplates: FALLBACK_RESOURCE_TEMPLATES,
      source: 'fallback'
    }
  }
}
