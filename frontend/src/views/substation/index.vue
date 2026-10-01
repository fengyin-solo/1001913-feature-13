<template>
  <section class="page" data-module="substation">
    <header class="page-head">
      <div>
        <h2>升压站管理</h2>
        <p class="page-desc">
          负荷率＝当前负荷÷主变容量，超过口径上限自动标记负荷越限，并区分短时冲击与持续越限；
          同一站区重复登记只展示最近一次结论。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="openCriteria">判定口径（当前 {{ activeVersion }}）</button>
        <button class="btn" type="button" @click="openVerdicts">越限留档</button>
        <button class="btn" type="button" @click="exportRows">导出升压站清单</button>
        <button class="btn primary" type="button" @click="openCreate">登记升压站</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.display }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>站区编号</span>
        <input v-model="keyword" placeholder="按站区编号检索" />
      </label>
      <label class="filter-item">
        <span>站区状态</span>
        <select v-model="statusFilter">
          <option value="">全部</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-overload': row['越限标记'] === '负荷越限' }">
          <td>{{ row['站区编号'] ?? '—' }}</td>
          <td>{{ fmtNumber(row['主变容量']) }}</td>
          <td>{{ fmtNumber(row['当前负荷']) }}</td>
          <td>{{ fmtRatio(row['负荷率']) }}</td>
          <td>
            <span v-if="row['越限标记'] === '负荷越限'" class="tag tag-overload">负荷越限</span>
            <span v-else-if="row['越限标记'] === '运行正常'" class="tag tag-normal">未越限</span>
            <span v-else-if="row['越限标记'] === '不可判定'" class="tag tag-na">不可判定</span>
            <span v-else>—</span>
          </td>
          <td>
            <span v-if="row['越限类型'] === '短时冲击'" class="tag tag-short">短时冲击</span>
            <span v-else-if="row['越限类型'] === '持续越限'" class="tag tag-persist">持续越限</span>
            <span v-else>—</span>
          </td>
          <td>{{ row['判定版本'] ?? '—' }}</td>
          <td>{{ row['电压等级'] ?? '—' }}</td>
          <td>{{ row['所属场站'] ?? '—' }}</td>
          <td>{{ row['值班班组'] ?? '—' }}</td>
          <td>{{ row['升压站状态'] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="runAction('确认检修', row)">确认检修</button>
            <button class="link" type="button" @click="openJudge(row)">负荷判定</button>
            <button class="link" type="button" @click="runAction('停运升压站', row)">停运升压站</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无升压站数据，可先登记升压站</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 个站区（同站区重复登记只保留最近一次结论）</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 登记升压站 -->
    <div v-if="createOpen" class="modal-mask" @click.self="createOpen = false">
      <div class="modal">
        <h3>登记升压站</h3>
        <p class="modal-tip">主变容量与当前负荷用于计算负荷率；当前负荷缺失的记录不允许判定。</p>
        <label v-for="field in createFields" :key="field.key" class="form-item">
          <span>{{ field.label }}{{ field.required ? ' *' : '' }}</span>
          <input v-model="createForm[field.key]" :placeholder="field.tip" />
        </label>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="createOpen = false">取消</button>
          <button class="btn primary" type="button" @click="submitCreate">提交登记</button>
        </div>
      </div>
    </div>

    <!-- 负荷判定：补录/刷新当前负荷与越限持续分钟 -->
    <div v-if="judgeOpen" class="modal-mask" @click.self="judgeOpen = false">
      <div class="modal">
        <h3>负荷判定 · {{ judgeForm['站区编号'] }}</h3>
        <p class="modal-tip">
          主变容量 {{ fmtNumber(judgeForm['主变容量']) }}，按口径 {{ activeVersion }} 判定；
          持续时长达到口径阈值记为持续越限，否则记为短时冲击。
        </p>
        <label class="form-item">
          <span>当前负荷</span>
          <input v-model="judgeForm['当前负荷']" placeholder="与主变容量同单位（如 MW）" />
        </label>
        <label class="form-item">
          <span>越限持续分钟</span>
          <input v-model="judgeForm['越限持续分钟']" placeholder="本次越限已持续的分钟数" />
        </label>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="judgeOpen = false">取消</button>
          <button class="btn primary" type="button" @click="submitJudge">执行判定</button>
        </div>
      </div>
    </div>

    <!-- 判定口径版本 -->
    <div v-if="criteriaOpen" class="modal-mask wide" @click.self="criteriaOpen = false">
      <div class="modal">
        <h3>负荷率判定口径</h3>
        <p class="modal-tip">
          新口径生效后，现有升压站记录自动按新版重算越限结论；历史越限结论按当时那一版留档。
        </p>
        <table class="data-table inner">
          <thead>
            <tr><th>版本</th><th>负荷率上限</th><th>持续越限阈值(分钟)</th><th>生效时间</th><th>状态</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="item in criteriaRows" :key="String(item['版本'])">
              <td>{{ item['版本'] }}</td>
              <td>{{ fmtRatio(item['负荷率上限']) }}</td>
              <td>{{ item['持续越限分钟'] }}</td>
              <td>{{ item['生效时间'] }}</td>
              <td>{{ item.active ? '当前生效' : '历史版本' }}</td>
              <td>
                <button v-if="!item.active" class="link" type="button" @click="activate(String(item['版本']))">切换生效</button>
              </td>
            </tr>
          </tbody>
        </table>
        <h4>登记新口径</h4>
        <div class="form-line">
          <label class="form-item">
            <span>负荷率上限 *</span>
            <input v-model="criteriaForm['负荷率上限']" placeholder="如 0.85 或 85（百分数）" />
          </label>
          <label class="form-item">
            <span>持续越限分钟</span>
            <input v-model="criteriaForm['持续越限分钟']" placeholder="默认 15" />
          </label>
          <label class="form-item">
            <span>版本号（可空）</span>
            <input v-model="criteriaForm['版本']" placeholder="留空自动生成 vN" />
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="criteriaOpen = false">关闭</button>
          <button class="btn primary" type="button" @click="submitCriteria">登记并生效</button>
        </div>
      </div>
    </div>

    <!-- 越限留档 -->
    <div v-if="verdictsOpen" class="modal-mask wide" @click.self="verdictsOpen = false">
      <div class="modal">
        <h3>越限留档（历史结论按当时版本留档）</h3>
        <table class="data-table inner">
          <thead>
            <tr>
              <th>留档时间</th><th>站区编号</th><th>判定版本</th><th>上限</th>
              <th>主变容量</th><th>当前负荷</th><th>负荷率</th><th>越限类型</th><th>触发方式</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in verdictRows" :key="String(item.id)">
              <td>{{ item['留档时间'] }}</td>
              <td>{{ item['站区编号'] }}</td>
              <td>{{ item['判定版本'] }}</td>
              <td>{{ fmtRatio(item['负荷率上限']) }}</td>
              <td>{{ fmtNumber(item['主变容量']) }}</td>
              <td>{{ fmtNumber(item['当前负荷']) }}</td>
              <td>{{ fmtRatio(item['负荷率']) }}</td>
              <td>{{ item['越限类型'] }}</td>
              <td>{{ item['触发方式'] }}</td>
            </tr>
            <tr v-if="!verdictRows.length">
              <td colspan="9" class="empty-state">暂无越限留档</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="verdictsOpen = false">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/substation'
const columns = [
  '站区编号', '主变容量', '当前负荷', '负荷率', '越限标记', '越限类型',
  '判定版本', '电压等级', '所属场站', '值班班组', '升压站状态',
]
const statuses = ['待检修', '运行正常', '负荷越限', '已停运']

type Stat = { label: string; display: string | number }
const stats = ref<Stat[]>([
  { label: '在运升压站', display: 0 },
  { label: '负荷越限站数', display: 0 },
  { label: '其中：短时冲击', display: 0 },
  { label: '其中：持续越限', display: 0 },
  { label: '平均负荷率', display: '—' },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')

const activeVersion = ref('—')

function fmtNumber(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = Number(value)
  return Number.isFinite(n) ? String(n) : '—'
}

function fmtRatio(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  // 上限与负荷率都按小数存，统一展示成百分比
  return `${(n * 100).toFixed(1)}%`
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('升压站列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    const s = payload.stats ?? {}
    stats.value = [
      { label: '在运升压站', display: s['在运升压站'] ?? 0 },
      { label: '负荷越限站数', display: s['负荷越限站数'] ?? 0 },
      { label: '其中：短时冲击', display: s['短时冲击站数'] ?? 0 },
      { label: '其中：持续越限', display: s['持续越限站数'] ?? 0 },
      { label: '平均负荷率', display: s['平均负荷率'] == null ? '—' : fmtRatio(s['平均负荷率']) },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '升压站列表读取失败'
  }
}

async function postJson(path: string, body: Record<string, unknown>) {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  const payload = await response.json().catch(() => null)
  if (!response.ok || !payload || payload.ok === false) {
    throw new Error(payload?.message ? String(payload.message) : '升压站操作未生效，请稍后重试')
  }
  return payload as { message: string; entry: Row | null }
}

// ---------- 登记 ----------

const createFields = [
  { key: '站区编号', label: '站区编号', required: true, tip: '如 SUBS-0008' },
  { key: '主变容量', label: '主变容量', required: true, tip: '数值，如 100（MW/MVA）' },
  { key: '电压等级', label: '电压等级', required: true, tip: '如 220kV' },
  { key: '所属场站', label: '所属场站', required: false, tip: '所属风电场站' },
  { key: '值班班组', label: '值班班组', required: false, tip: '如 运维一班' },
  { key: '当前负荷', label: '当前负荷', required: false, tip: '与主变容量同单位；缺失则不可判定' },
  { key: '越限持续分钟', label: '越限持续分钟', required: false, tip: '本次越限持续时长' },
]
const emptyCreateForm = () => ({
  '站区编号': '', '主变容量': '', '电压等级': '', '所属场站': '',
  '值班班组': '', '当前负荷': '', '越限持续分钟': '',
})
const createOpen = ref(false)
const createForm = ref<Record<string, string>>(emptyCreateForm())

function openCreate() {
  createForm.value = emptyCreateForm()
  errorMessage.value = ''
  createOpen.value = true
}

async function submitCreate() {
  const values: Record<string, string> = {}
  for (const field of createFields) {
    const value = createForm.value[field.key]?.trim()
    if (value) values[field.key] = value
  }
  try {
    const result = await postJson(ENDPOINT, { values })
    createOpen.value = false
    errorMessage.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '升压站登记失败'
  }
}

// ---------- 动作 ----------

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const result = await postJson(`${ENDPOINT}/${row.id}/actions`, { values: { action } })
    errorMessage.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '升压站操作失败'
  }
}

const judgeOpen = ref(false)
const judgeForm = ref<Record<string, string | number | boolean | null>>({})

function openJudge(row: Row) {
  judgeForm.value = {
    id: row.id,
    '站区编号': String(row['站区编号'] ?? ''),
    '主变容量': (row['主变容量'] as string | number | null) ?? null,
    '当前负荷': row['当前负荷'] == null ? '' : String(row['当前负荷']),
    '越限持续分钟': row['越限持续分钟'] == null ? '' : String(row['越限持续分钟']),
  }
  errorMessage.value = ''
  judgeOpen.value = true
}

async function submitJudge() {
  const values: Record<string, string> = { action: '负荷判定' }
  const load = String(judgeForm.value['当前负荷'] ?? '').trim()
  const minutes = String(judgeForm.value['越限持续分钟'] ?? '').trim()
  if (load) values['当前负荷'] = load
  if (minutes) values['越限持续分钟'] = minutes
  try {
    const result = await postJson(`${ENDPOINT}/${judgeForm.value.id}/actions`, { values })
    judgeOpen.value = false
    errorMessage.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '负荷判定失败'
  }
}

// ---------- 口径 ----------

const criteriaOpen = ref(false)
const criteriaRows = ref<Row[]>([])
const criteriaForm = ref<Record<string, string>>({ '负荷率上限': '', '持续越限分钟': '', '版本': '' })

async function openCriteria() {
  criteriaOpen.value = true
  errorMessage.value = ''
  await loadCriteria()
}

async function loadCriteria() {
  try {
    const response = await request(`${ENDPOINT}/criteria`)
    const payload = await response.json()
    criteriaRows.value = payload.items ?? []
    activeVersion.value = payload.active ? String(payload.active['版本']) : '—'
  } catch {
    errorMessage.value = '判定口径读取失败'
  }
}

async function submitCriteria() {
  const values: Record<string, string> = {}
  for (const [key, value] of Object.entries(criteriaForm.value)) {
    if (value.trim()) values[key] = value.trim()
  }
  try {
    const result = await postJson(`${ENDPOINT}/criteria`, { values })
    errorMessage.value = result.message
    criteriaForm.value = { '负荷率上限': '', '持续越限分钟': '', '版本': '' }
    await loadCriteria()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '口径登记失败'
  }
}

async function activate(version: string) {
  try {
    const result = await postJson(`${ENDPOINT}/criteria/${encodeURIComponent(version)}/activate`, { values: {} })
    errorMessage.value = result.message
    await loadCriteria()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '口径切换失败'
  }
}

// ---------- 留档 ----------

const verdictsOpen = ref(false)
const verdictRows = ref<Row[]>([])

async function openVerdicts() {
  verdictsOpen.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/verdicts`)
    const payload = await response.json()
    verdictRows.value = payload.items ?? []
  } catch {
    errorMessage.value = '越限留档读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; }
.tag-overload { background: #fee4e2; color: #b42318; }
.tag-persist { background: #fda29b; color: #7a271a; }
.tag-short { background: #fef0c7; color: #b54708; }
.tag-normal { background: #d1fadf; color: #05603a; }
.tag-na { background: #e4e7ec; color: #475467; }
.row-overload { background: #fff7f6; }
.modal-mask {
  position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45);
  display: flex; align-items: center; justify-content: center; z-index: 20;
}
.modal-mask.wide .modal { width: 820px; max-width: 92vw; }
.modal {
  width: 480px; max-width: 92vw; max-height: 86vh; overflow: auto;
  background: #fff; border-radius: 10px; padding: 18px 20px;
}
.modal h3 { margin: 0 0 8px; font-size: 16px; }
.modal h4 { margin: 14px 0 6px; font-size: 14px; }
.modal-tip { font-size: 12px; color: var(--muted); margin: 0 0 10px; }
.form-item { display: flex; flex-direction: column; gap: 4px; margin-bottom: 10px; font-size: 13px; }
.form-item span { color: var(--muted); font-size: 12px; }
.form-item input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.form-line { display: flex; gap: 10px; }
.form-line .form-item { flex: 1; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.data-table.inner { font-size: 12px; }
select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
</style>
