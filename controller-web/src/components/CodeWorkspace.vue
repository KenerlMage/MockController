<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Compartment, EditorState } from '@codemirror/state'
import {
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
  placeholder as cmPlaceholder
} from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import {
  bracketMatching,
  defaultHighlightStyle,
  foldGutter,
  foldKeymap,
  indentOnInput,
  indentUnit,
  syntaxHighlighting
} from '@codemirror/language'
import { python } from '@codemirror/lang-python'
import { api } from '../api.js'

const props = defineProps({
  tpsId: { type: String, required: true }
})
const emit = defineEmits(['saved'])

// ---------------- 文件树 ----------------
// 后端 write_file 允许的后缀； .py/.md 归「源码」，其余归「资源」
const ALLOWED = ['.py', '.md', '.txt', '.json', '.yaml', '.yml']
const DEFAULT_NEW_FILE = 'testcase/new_case.py'
const isAllowed = (path) => ALLOWED.some((s) => String(path).toLowerCase().endsWith(s))
const isCodeLike = (path) => /\.(py|md)$/i.test(String(path))
const isHidden = (path) => {
  const name = String(path).split('/').pop() || ''
  return name.startsWith('.')
}

const files = ref([])
const filesError = ref('')
const filesLoading = ref(false)
const collapsed = ref(new Set())
const treeKind = ref('code') // code（.py/.md）| res（.txt/.json/.yaml）
const showHidden = ref(false)

// 目录树：{ name, path, dirs: Map, files: [{name, path}] }，目录折叠只作用于 dirs
function buildTree(list) {
  const root = { name: '', path: '', dirs: new Map(), files: [] }
  for (const path of list) {
    const segs = path.split('/').filter(Boolean)
    let node = root
    for (let i = 0; i < segs.length - 1; i += 1) {
      const name = segs[i]
      if (!node.dirs.has(name)) {
        node.dirs.set(name, { name, path: segs.slice(0, i + 1).join('/'), dirs: new Map(), files: [] })
      }
      node = node.dirs.get(name)
    }
    node.files.push({ name: segs[segs.length - 1], path })
  }
  return root
}

// 树分两层渲染：根的直属文件 + 一级目录（含其深层文件的相对路径），目录可折叠
const tree = computed(() => {
  const list = files.value
    .filter((f) => (treeKind.value === 'code' ? isCodeLike(f) : !isCodeLike(f)))
    .filter((f) => showHidden.value || !isHidden(f))
    .sort()
  return buildTree(list)
})

const visibleCount = computed(
  () =>
    files.value
      .filter((f) => (treeKind.value === 'code' ? isCodeLike(f) : !isCodeLike(f)))
      .filter((f) => showHidden.value || !isHidden(f)).length
)

const topDirs = computed(() => [...tree.value.dirs.values()].sort((a, b) => a.name.localeCompare(b.name)))

function isOpen(path) {
  return !collapsed.value.has(path)
}

function toggleDir(path) {
  const next = new Set(collapsed.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  collapsed.value = next
}

// 目录下全部文件（含子目录），按其相对路径显示
function filesUnder(dir) {
  return files.value
    .filter((f) => f.startsWith(`${dir.path}/`))
    .filter((f) => (treeKind.value === 'code' ? isCodeLike(f) : !isCodeLike(f)))
    .filter((f) => showHidden.value || !isHidden(f))
    .map((f) => ({ path: f, name: f.slice(dir.path.length + 1) }))
    .sort((a, b) => a.name.localeCompare(b.name))
}

function fileIcon(path) {
  const lower = String(path).toLowerCase()
  if (lower.endsWith('.py')) return '🐍'
  if (lower.endsWith('.md')) return '📝'
  if (lower.endsWith('.json')) return '🧾'
  if (lower.endsWith('.yaml') || lower.endsWith('.yml')) return '⚙'
  return '▤'
}

// ---------------- 编辑区 ----------------
const current = ref('')
const loading = ref(false)
const saving = ref(false)
const dirty = ref(false)
const notice = ref('')
const error = ref('')
const pending = ref('') // 有未保存修改时点击的目标路径
const confirmDelete = ref('')

const host = ref(null)
let view = null
let suppressUpdate = false
const themeComp = new Compartment()
const langComp = new Compartment()

// 主题沿用页面变量：--panel 底、--panel-2 行号槽、--accent 光标/匹配括号
function pageTheme() {
  const css = getComputedStyle(document.documentElement)
  const v = (name, fallback) => (css.getPropertyValue(name) || '').trim() || fallback
  const accent = v('--accent', '#6d28d9')
  const text = v('--text', '#1d1733')
  const muted = v('--muted', '#6e688a')
  const panel = v('--panel', '#ffffff')
  const gutterBg = v('--panel-2', '#f5f2fd')
  const border = v('--border', '#e4def6')
  const mono = v('--mono', 'Consolas, monospace')
  return EditorView.theme(
    {
      '&': { backgroundColor: panel, color: text, fontSize: '13px', height: '100%' },
      '.cm-scroller': { fontFamily: mono, lineHeight: '1.7', overflow: 'auto' },
      '.cm-content': { fontFamily: mono, caretColor: accent, padding: '8px 0' },
      '.cm-gutters': {
        backgroundColor: gutterBg,
        color: muted,
        border: 'none',
        borderRight: `1px solid ${border}`
      },
      '.cm-activeLine': { backgroundColor: 'rgba(109, 40, 217, 0.045)' },
      '.cm-activeLineGutter': { backgroundColor: 'rgba(109, 40, 217, 0.08)', color: accent },
      '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, .cm-content ::selection': {
        backgroundColor: 'rgba(139, 92, 246, 0.2) !important'
      },
      '.cm-cursor': { borderLeftColor: accent },
      '.cm-matchingBracket': { backgroundColor: 'rgba(109, 40, 217, 0.16)', outline: `1px solid ${accent}` },
      '.cm-foldPlaceholder': { background: 'rgba(109, 40, 217, 0.1)', border: 'none', color: accent },
      '&.cm-focused': { outline: 'none' },
      '.cm-placeholder': { color: muted }
    },
    { dark: false }
  )
}

function langFor(path) {
  return isCodeLike(path) ? python() : []
}

function createView() {
  if (!host.value || view) return
  const state = EditorState.create({
    doc: '',
    extensions: [
      lineNumbers(),
      highlightActiveLineGutter(),
      highlightActiveLine(),
      foldGutter(),
      history(),
      indentOnInput(),
      indentUnit.of('    '),
      bracketMatching(),
      syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
      keymap.of([
        {
          key: 'Mod-s',
          preventDefault: true,
          // run 必须同步返回布尔值；save() 是异步，这里触发后立即返回 true 以吞掉默认行为
          run: () => {
            void save()
            return true
          }
        },
        ...defaultKeymap,
        ...historyKeymap,
        ...foldKeymap,
        indentWithTab
      ]),
      EditorView.updateListener.of((u) => {
        if (!u.docChanged || suppressUpdate) return
        if (!dirty.value) dirty.value = true
      }),
      themeComp.of(pageTheme()),
      langComp.of([]),
      cmPlaceholder('左侧选择文件后在此编辑，Ctrl+S 保存')
    ]
  })
  view = new EditorView({ state, parent: host.value })
}

function applyDoc(text) {
  if (!view) return
  suppressUpdate = true
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } })
  suppressUpdate = false
}

function applyLang(path) {
  if (view) view.dispatch({ effects: langComp.reconfigure(langFor(path)) })
}

// ---------------- 接口 ----------------
async function loadFiles(keepSelection = true) {
  filesError.value = ''
  filesLoading.value = true
  try {
    const data = await api.tpsFiles(props.tpsId)
    files.value = (data && data.files) || []
    if (keepSelection && current.value && !files.value.includes(current.value)) closeCurrent()
  } catch (e) {
    files.value = []
    filesError.value = `文件列表接口未就绪（GET /api/v1/tps/${props.tpsId}/files）：${e.message}`
  } finally {
    filesLoading.value = false
  }
}

function closeCurrent() {
  current.value = ''
  dirty.value = false
  pending.value = ''
  confirmDelete.value = ''
  applyDoc('')
  applyLang('')
}

async function openFile(path) {
  if (!path || path === current.value) return
  if (dirty.value) {
    pending.value = path
    return
  }
  error.value = ''
  notice.value = ''
  loading.value = true
  try {
    const text = await api.tpsFileContent(props.tpsId, path)
    current.value = path
    dirty.value = false
    pending.value = ''
    await nextTick()
    applyDoc(text)
    applyLang(path)
  } catch (e) {
    error.value = `读取 ${path} 失败：${e.message}`
  } finally {
    loading.value = false
  }
}

function discardAndOpen() {
  const target = pending.value
  pending.value = ''
  dirty.value = false
  openFile(target)
}

async function saveAfterSwitch() {
  const ok = await save()
  if (ok) discardAndOpen()
}

async function save() {
  if (!current.value || !view || saving.value) return false
  error.value = ''
  notice.value = ''
  saving.value = true
  const path = current.value
  const content = view.state.doc.toString()
  try {
    const res = await api.tpsFileSave(props.tpsId, path, content)
    dirty.value = false
    notice.value = `已保存 ${(res && res.path) || path}（${(res && res.size) ?? content.length} 字节）`
    if (!files.value.includes(path)) files.value = [...files.value, path].sort()
    emit('saved', { path })
    return true
  } catch (e) {
    error.value = `保存 ${path} 失败：${e.message}`
    return false
  } finally {
    saving.value = false
  }
}

// ---------------- 新建 / 删除 / 重命名 ----------------
const newFile = ref(null) // { value }
const newDir = ref(null) // { value }
const folderTip = ref('')
const renameTarget = ref(null) // { path, value }

function currentFolder() {
  return (current.value || '').split('/').slice(0, -1).join('/')
}

function startNewFile(dir) {
  const folder = dir != null ? dir : currentFolder()
  newDir.value = null
  renameTarget.value = null
  folderTip.value = ''
  newFile.value = { value: folder ? `${folder}/new_case.py` : DEFAULT_NEW_FILE }
}

// 后端写文件会自动建父目录，"新建文件夹"先记下目录，再让用户填文件名
function startNewDir(dir) {
  const folder = dir != null ? dir : currentFolder()
  newFile.value = null
  renameTarget.value = null
  folderTip.value = ''
  newDir.value = { value: folder ? `${folder}/new_folder` : 'testcase/new_folder' }
}

function normPath(value) {
  return String(value || '')
    .trim()
    .replace(/\\/g, '/')
    .replace(/^\/+/, '')
    .replace(/\/+$/, '')
}

async function createFile() {
  const raw = normPath(newFile.value && newFile.value.value)
  error.value = ''
  notice.value = ''
  if (!raw) return (error.value = '请填写文件路径')
  if (raw.split('/').includes('..')) return (error.value = '路径不能包含 ..')
  if (!isAllowed(raw)) return (error.value = `只允许 ${ALLOWED.join(' / ')} 文件`)
  if (files.value.includes(raw)) return (error.value = `文件已存在：${raw}`)
  try {
    const seed = /\.py$/i.test(raw) ? `# -*- coding: utf-8 -*-\n# ${raw}\n` : ''
    await api.tpsFileSave(props.tpsId, raw, seed)
    newFile.value = null
    if (!isCodeLike(raw)) treeKind.value = 'res'
    await loadFiles()
    dirty.value = false
    await openFile(raw)
    notice.value = `已创建 ${raw}`
  } catch (e) {
    error.value = `创建 ${raw} 失败：${e.message}`
  }
}

function createDir() {
  const raw = normPath(newDir.value && newDir.value.value)
  error.value = ''
  notice.value = ''
  if (!raw) return (error.value = '请填写目录名')
  if (raw.split('/').includes('..')) return (error.value = '目录名不能包含 ..')
  if (/\.\w+$/.test(raw.split('/').pop())) return (error.value = '这是文件名的形状，请填目录名（不加后缀）')
  if (files.value.some((f) => f.startsWith(`${raw}/`))) return (error.value = `目录已存在：${raw}`)
  newDir.value = null
  startNewFile(raw)
  notice.value = `空目录不会落盘；已把新文件路径预填在 ${raw}/ 下，创建文件时一并建目录`
}

async function remove(path) {
  error.value = ''
  notice.value = ''
  try {
    await api.tpsFileDelete(props.tpsId, path)
    if (current.value === path) closeCurrent()
    confirmDelete.value = ''
    await loadFiles()
    notice.value = `已删除 ${path}`
  } catch (e) {
    error.value = `删除 ${path} 失败：${e.message}`
  }
}

async function doRename() {
  const target = renameTarget.value
  if (!target) return
  const to = normPath(target.value)
  error.value = ''
  notice.value = ''
  if (!to || to === target.path) {
    renameTarget.value = null
    return
  }
  if (!isAllowed(to)) return (error.value = `只允许 ${ALLOWED.join(' / ')} 文件`)
  if (files.value.includes(to)) return (error.value = `文件已存在：${to}`)
  try {
    // 后端没有 move 接口：读原文 → 写新路径 → 删旧路径
    const text = await api.tpsFileContent(props.tpsId, target.path)
    await api.tpsFileSave(props.tpsId, to, text)
    await api.tpsFileDelete(props.tpsId, target.path)
    const wasOpen = current.value === target.path
    renameTarget.value = null
    await loadFiles()
    if (wasOpen) {
      current.value = ''
      dirty.value = false
      await openFile(to)
    }
    notice.value = `已重命名为 ${to}`
  } catch (e) {
    error.value = `重命名 ${target.path} → ${to} 失败：${e.message}`
  }
}

// ---------------- 生命周期 ----------------
function onBeforeUnload(e) {
  if (!dirty.value) return
  e.preventDefault()
  e.returnValue = ''
}

function reset() {
  closeCurrent()
  files.value = []
  filesError.value = ''
  notice.value = ''
  error.value = ''
  newFile.value = null
  newDir.value = null
  folderTip.value = ''
  renameTarget.value = null
  loadFiles(false)
}

watch(() => props.tpsId, reset)

onMounted(async () => {
  createView()
  window.addEventListener('beforeunload', onBeforeUnload)
  await loadFiles(false)
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
  if (view) {
    view.destroy()
    view = null
  }
})

defineExpose({ save, reload: () => loadFiles(true), isDirty: () => dirty.value })
</script>

<template>
  <div class="workspace">
    <!-- 左侧文件树 -->
    <aside class="files">
      <div class="files-head">
        <div class="kind-tabs">
          <button class="kind" :class="{ on: treeKind === 'code' }" @click="treeKind = 'code'">源码</button>
          <button class="kind" :class="{ on: treeKind === 'res' }" @click="treeKind = 'res'">资源</button>
          <span class="count">{{ visibleCount }}</span>
        </div>
        <button class="mini" :disabled="filesLoading" title="刷新文件列表" @click="loadFiles(true)">
          {{ filesLoading ? '…' : '⟳' }}
        </button>
      </div>

      <div class="file-actions">
        <button class="mini" @click="startNewFile()">＋ 文件</button>
        <button class="mini" @click="startNewDir()">＋ 文件夹</button>
        <label class="mini check-hidden" title="显示 .keep.md 等点开头文件">
          <input v-model="showHidden" type="checkbox" />隐藏项
        </label>
      </div>

      <div v-if="filesError" class="inline-error">{{ filesError }}</div>

      <div v-if="newDir" class="inline-form">
        <span class="form-label">新建文件夹</span>
        <input
          v-model="newDir.value"
          placeholder="testcase/new_folder"
          @keydown.enter.prevent="createDir"
          @keydown.esc="newDir = null"
        />
        <div class="row">
          <button class="mini primary" @click="createDir">确认</button>
          <button class="mini" @click="newDir = null">取消</button>
        </div>
        <span class="hint">目录会随第一个文件一起创建</span>
      </div>

      <div v-if="newFile" class="inline-form">
        <span class="form-label">新建文件</span>
        <input
          v-model="newFile.value"
          :placeholder="DEFAULT_NEW_FILE"
          @keydown.enter.prevent="createFile"
          @keydown.esc="newFile = null"
        />
        <div class="row">
          <button class="mini primary" @click="createFile">创建</button>
          <button class="mini" @click="newFile = null">取消</button>
        </div>
        <span class="hint">相对 TPS 目录，父目录不存在会自动创建</span>
      </div>

      <div class="tree">
        <!-- 根目录直属文件 -->
        <div
          v-for="file in tree.files"
          :key="file.path"
          class="node file"
          :class="{ on: file.path === current, dim: isHidden(file.path) }"
          @click="openFile(file.path)"
        >
          <span class="icon">{{ fileIcon(file.path) }}</span>
          <span class="name mono">{{ file.name }}</span>
          <span class="file-tools">
            <button class="tool" title="重命名" @click.stop="renameTarget = { path: file.path, value: file.path }">✎</button>
            <button class="tool danger" title="删除" @click.stop="confirmDelete = file.path">🗑</button>
          </span>
        </div>

        <!-- 一级目录（可折叠） -->
        <template v-for="dir in topDirs" :key="dir.path">
          <div class="node folder" @click="toggleDir(dir.path)">
            <span class="caret">{{ isOpen(dir.path) ? '▾' : '▸' }}</span>
            <span class="icon">📁</span>
            <span class="name">{{ dir.name }}</span>
            <span class="dir-tools">
              <button class="tool" title="在此目录新建文件" @click.stop="startNewFile(dir.path)">＋</button>
              <button class="tool" title="在此目录新建子文件夹" @click.stop="startNewDir(dir.path)">⊞</button>
            </span>
          </div>

          <template v-if="isOpen(dir.path)">
            <div
              v-for="sub in filesUnder(dir)"
              :key="sub.path"
              class="node file child"
              :class="{ on: sub.path === current, dim: isHidden(sub.path) }"
              @click="openFile(sub.path)"
            >
              <span class="icon">{{ fileIcon(sub.path) }}</span>
              <span v-if="renameTarget && renameTarget.path === sub.path" class="rename-box" @click.stop>
                <input
                  v-model="renameTarget.value"
                  @keydown.enter.prevent="doRename"
                  @keydown.esc="renameTarget = null"
                />
                <button class="tool" title="确认" @click="doRename">✓</button>
                <button class="tool" title="取消" @click="renameTarget = null">✕</button>
              </span>
              <template v-else>
                <span class="name mono">{{ sub.name }}</span>
                <span class="file-tools">
                  <button class="tool" title="重命名" @click.stop="renameTarget = { path: sub.path, value: sub.path }">✎</button>
                  <button class="tool danger" title="删除" @click.stop="confirmDelete = sub.path">🗑</button>
                </span>
              </template>
              <span v-if="confirmDelete === sub.path" class="confirm" @click.stop>
                <button class="tool danger" @click="remove(sub.path)">删除</button>
                <button class="tool" @click="confirmDelete = ''">取消</button>
              </span>
            </div>
          </template>
        </template>

        <!-- 根目录直属文件的重命名 / 删除确认（与目录内一致） -->
        <div v-if="renameTarget && !renameTarget.path.includes('/')" class="inline-form">
          <span class="form-label">重命名 {{ renameTarget.path }}</span>
          <input v-model="renameTarget.value" @keydown.enter.prevent="doRename" @keydown.esc="renameTarget = null" />
          <div class="row">
            <button class="mini primary" @click="doRename">确认</button>
            <button class="mini" @click="renameTarget = null">取消</button>
          </div>
        </div>
        <div v-if="confirmDelete && !confirmDelete.includes('/')" class="inline-form">
          <span class="form-label danger-text">删除 {{ confirmDelete }}？</span>
          <div class="row">
            <button class="mini primary" @click="remove(confirmDelete)">删除</button>
            <button class="mini" @click="confirmDelete = ''">取消</button>
          </div>
        </div>

        <div v-if="!tree.files.length && !topDirs.length" class="muted empty">
          {{ filesError ? '文件列表不可用' : treeKind === 'code' ? '暂无源码文件（.py / .md）' : '暂无资源文件' }}
        </div>
      </div>
    </aside>

    <!-- 右侧编辑器 -->
    <section class="editor">
      <div class="editor-bar">
        <span class="mono path">{{ current || '未选择文件' }}</span>
        <span v-if="dirty" class="dot" title="有未保存修改">● 未保存</span>
        <span v-if="loading" class="muted">读取中…</span>
        <button class="mini primary" :disabled="!current || saving || loading || !dirty" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </div>

      <div v-if="pending" class="switch-warn">
        当前文件有未保存修改，切换到 <span class="mono">{{ pending }}</span> 将丢失修改
        <button class="mini" @click="saveAfterSwitch">保存后切换</button>
        <button class="mini" @click="discardAndOpen">仍然切换</button>
        <button class="mini" @click="pending = ''">取消</button>
      </div>

      <div v-if="error" class="error">{{ error }}</div>
      <div v-if="notice" class="ok">{{ notice }}</div>

      <div class="editor-host-wrap">
        <div ref="host" class="editor-host"></div>
        <div v-if="!current" class="editor-hint muted">
          左侧选择文件开始编辑；Ctrl+S 保存，Tab 缩进 4 空格，括号自动匹配。
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.workspace {
  display: grid;
  grid-template-columns: 236px minmax(0, 1fr);
  gap: 10px;
  align-items: stretch;
}

/* ---------------- 文件树 ---------------- */
.files {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  height: 520px;
  min-height: 320px;
  overflow: hidden;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 8px;
}

.files-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.kind-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
}

.kind {
  padding: 2px 8px;
  font-size: 11.5px;
  border-radius: 5px;
  border-color: transparent;
  background: transparent;
  color: var(--muted);
  box-shadow: none;
}

.kind.on {
  background: #fff;
  border-color: var(--border-strong);
  color: var(--accent);
  font-weight: 600;
}

.count {
  font-size: 10.5px;
  color: var(--muted);
  font-family: var(--mono);
}

.file-actions {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}

.check-hidden {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  color: var(--muted);
  cursor: pointer;
}

.check-hidden input {
  width: auto;
  margin: 0;
}

.mini {
  padding: 3px 8px;
  font-size: 11.5px;
  border-radius: 5px;
}

.mini.primary {
  box-shadow: none;
  color: #fff;
}

.tree {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding-right: 2px;
}

.node {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 6px;
  border-radius: 5px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text);
  user-select: none;
}

.node:hover {
  background: rgba(var(--accent-rgb), 0.07);
}

.node.on {
  background: rgba(var(--accent-rgb), 0.13);
  color: var(--accent);
  font-weight: 600;
}

.node.dim .name {
  color: var(--muted);
  font-style: italic;
}

.node.child {
  padding-left: 22px;
}

.caret {
  width: 10px;
  flex: 0 0 10px;
  font-size: 9px;
  color: var(--muted);
}

.icon {
  font-size: 11.5px;
  line-height: 1;
}

.name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-tools,
.dir-tools {
  display: none;
  gap: 2px;
}

.node:hover .file-tools,
.node:hover .dir-tools {
  display: inline-flex;
}

.tool {
  padding: 0 4px;
  min-width: 18px;
  height: 18px;
  font-size: 10.5px;
  border-radius: 4px;
  border-color: transparent;
  background: transparent;
  color: var(--muted);
  box-shadow: none;
}

.tool:hover:not(:disabled) {
  background: #fff;
  color: var(--accent);
  box-shadow: none;
}

.tool.danger:hover:not(:disabled) {
  color: var(--err);
}

.rename-box {
  display: flex;
  align-items: center;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.rename-box input {
  flex: 1;
  padding: 2px 5px;
  font-size: 11.5px;
  font-family: var(--mono);
}

.confirm {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-left: 6px;
  padding: 1px 5px;
  background: #fef1f1;
  border: 1px solid #f6c9c9;
  border-radius: 5px;
}

.inline-form {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 7px;
  background: #fff;
  border: 1px solid var(--border-strong);
  border-radius: 6px;
}

.form-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--accent);
}

.danger-text {
  color: var(--err);
}

.inline-form input {
  padding: 4px 6px;
  font-size: 11.5px;
  font-family: var(--mono);
}

.inline-form .row {
  gap: 6px;
}

.hint {
  font-size: 10.5px;
  color: var(--muted);
}

.inline-error {
  padding: 5px 7px;
  font-size: 11px;
  color: var(--err);
  background: #fef1f1;
  border: 1px solid #f6c9c9;
  border-radius: 5px;
}

.empty {
  padding: 8px 4px;
}

.mono {
  font-family: var(--mono);
  font-size: 12px;
}

/* ---------------- 编辑区 ---------------- */
.editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.editor-bar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
}

.dot {
  color: var(--accent);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.editor-host-wrap {
  position: relative;
  flex: 1;
  min-height: 320px;
  height: 520px;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  overflow: hidden;
  background: var(--panel);
}

.editor-host,
.editor-host :deep(.cm-editor) {
  height: 100%;
}

.editor-hint {
  position: absolute;
  inset: 0;
  padding: 18px;
  pointer-events: none;
}

.switch-warn {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--warn);
  background: #fff8ed;
  border: 1px solid #f3d9b0;
  padding: 7px 10px;
  border-radius: 7px;
}

@media (max-width: 1100px) {
  .workspace {
    grid-template-columns: 200px minmax(0, 1fr);
  }
}
</style>
