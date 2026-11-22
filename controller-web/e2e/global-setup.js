import { startServer } from './fixtures.js'

// 起一个指向临时数据根的 uvicorn，并等 /api/v1/health 就绪
export default async function globalSetup() {
  await startServer()
}
