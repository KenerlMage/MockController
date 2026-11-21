/**
 * BOM 提交载荷合并规则（纯函数，供 BOM 编辑器与测试共用）。
 *
 * 每行只覆盖三要素 alias / device_type / resource，
 * 原始条目里其余字段（型号、接口、IP、端口、串口号、通道等连接参数）原样保留，
 * 避免整张 BOM 保存时清空历史数据。
 */
export function buildBomItems(rows) {
  return (rows || []).map((row) => {
    const item = { ...((row && row.src) || {}) }
    item.alias = ((row && row.alias) || '').trim()
    item.device_type = (row && row.device_type) || ''
    item.resource = ((row && row.resource) || '').trim()
    // 兼容仍要求 name / model 的后端版本：仅在缺失时补齐，已有值一律保留
    if (!item.name) item.name = item.alias
    if (!item.model) item.model = item.device_type
    if (!item.id) delete item.id
    return item
  })
}
