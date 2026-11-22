import { stopServer } from './fixtures.js'

// 关闭 uvicorn，删除本次运行创建的临时数据根
export default async function globalTeardown() {
  await stopServer()
}
