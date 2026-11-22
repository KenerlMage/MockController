import { reactive } from 'vue'

// 轻量全局状态：页签切换 + TPS 选中联动
// 场景：元数据页右键「创建 TPS」→ 切到「TPS 管理」并选中新清单
export const store = reactive({
  tab: 'software-tps',
  tpsFocusId: '',
  // 每次外部要求选中 TPS 时自增，TpsView 监听它做「刷新 + 选中」
  tpsFocusToken: 0,

  switchTab(id) {
    this.tab = id
  },

  focusTps(id) {
    this.tpsFocusId = id || ''
    this.tpsFocusToken += 1
    this.tab = 'software-tps'
  }
})
