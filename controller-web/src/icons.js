// 项目内图标：沿用控制台已有字形，不引入图标库（离线可用、颜色随字体）。
// 用法：<AppIcon name="bom" /> / <AppIcon name="add" title="创建 BOM" />
export const ICONS = {
  // 导航与对象
  tps: '▤',
  package: '▣',
  metadata: '◈',
  product: '◈',
  subsystem: '▦',
  bench: '⚙',
  bom: '▤',
  device: '⌗',
  constants: '≡',
  process: '▦',
  records: '▥',
  settings: '⛭',
  assistant: '⚒',
  // 状态与动作
  add: '＋',
  remove: '✕',
  refresh: '⟳',
  search: '⌕',
  open: '▸',
  close: '▾',
  edit: '✎',
  check: '✓',
  warn: '!',
  arrow: '→',
  user: '◍',
  time: '◷'
}

export function iconOf(name, fallback = '·') {
  return ICONS[name] || fallback
}
