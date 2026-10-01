<template>
  <section class="page" data-module="substation">
    <header class="page-head">
      <div>
        <h2>升压站管理</h2>
        <p class="page-desc">
          负荷率按「当前负荷 ÷ 主变容量」自动计算，超过口径上限自动标记负荷越限，
          并区分短时冲击与持续越限；历史结论按当时口径留档。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记升压站</button>
        <button class="btn" type="button" @click="exportRows">导出升压站台账</button>
      </div>
    </header>

    <div class="caliber-bar">
      <span>
        当前判定口径：<strong>{{ caliber.active_version }}</strong>
        （{{ caliber.basis }}，自 {{ caliber.effective_since }} 起生效）
      </span>
      <span>负荷率上限：<strong>{{ formatPercent(caliber.load_rate_upper_limit) }}</strong></span>
      <span>短时冲击阈值：≤ <strong>{{ caliber.short_term_minutes }}</strong> 分钟，超出记为持续越限</span>
      <button class="btn ghost" type="button" @click="openCaliber">调整口径并重算</button>
    </div>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
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
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>只看越限</span>
        <input v-model="overloadOnly" type="checkbox" />
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
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '升压站状态'">
              <span :class="['status-tag', statusClass(row[column])]">{{ row[column] }}</span>
              <em v-if="row['判定说明']" class="cell-hint" :title="asText(row['判定说明'])">（不可判定）</em>
            </template>
            <template v-else-if="column === '越限分类'">
              <span v-if="row[column]" :class="['overload-tag', row[column] === '短时冲击' ? 'short' : 'sustained']">
                {{ row[column] }}
              </span>
              <span v-else>—</span>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="reportLoad(row)">上报负荷</button>
            <button class="link" type="button" @click="runAction('确认检修', row)">确认检修</button>
            <button class="link" type="button" @click="runAction('停运升压站', row)">停运升压站</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无升压站数据，可先登记升压站</td>
        </tr>
      </tbody>
    </table>

    <p class="dedup-hint">
      同一站区重复登记时，列表仅保留最近一次结论；完整重复记录与逐次判定留档见导出台账。
    </p>

    <footer class="page-foot">
      <span>共 {{ total }} 个站区（已按站区去重）</span>
      <span v-if="successMessage" class="success-text">{{ successMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type Stats = {
  total_stations: number
  in_service: number
  overload_stations: number
  short_term: number
  sustained: number
  avg_load_rate: number | null
}
type Caliber = {
  active_version: string
  effective_since: string
  basis: string
  load_rate_upper_limit: number
  short_term_minutes: number
}

const ENDPOINT = '/api/substation'
// 判定用负荷与容量随结论一起留下，负荷率结论可直接复算。
const columns = [
  '站区编号', '判定用容量', '判定用负荷', '负荷率', '越限分类', '判定口径版本',
  '登记时间', '升压站状态',
]
const statuses = ['待检修', '运行正常', '负荷越限', '已停运']

const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref<Stats>({
  total_stations: 0,
  in_service: 0,
  overload_stations: 0,
  short_term: 0,
  sustained: 0,
  avg_load_rate: null,
})
const caliber = ref<Caliber>({
  active_version: '-',
  effective_since: '-',
  basis: '',
  load_rate_upper_limit: 0,
  short_term_minutes: 0,
})
const errorMessage = ref('')
const successMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const overloadOnly = ref(false)

const statCards = computed(() => [
  { label: '在运升压站', value: stats.value.in_service },
  { label: '负荷越限站数', value: stats.value.overload_stations },
  { label: '短时冲击', value: stats.value.short_term },
  { label: '持续越限', value: stats.value.sustained },
  { label: '平均负荷率', value: stats.value.avg_load_rate == null ? '—' : formatPercent(stats.value.avg_load_rate) },
])

function formatPercent(value: number | null | undefined): string {
  if (value == null) return '—'
  return `${(value * 100).toFixed(1)}%`
}

function asText(value: string | number | boolean | null | undefined): string {
  return value == null ? '' : String(value)
}

function statusClass(status: string | number | boolean | null | undefined): string {
  return statusTagClass(String(status ?? ''))
}

function statusTagClass(status: string): string {
  if (status === '负荷越限') return 'status-overload'
  if (status === '运行正常') return 'status-normal'
  if (status === '已停运') return 'status-stopped'
  return 'status-pending'
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  overloadOnly.value = false
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function loadCaliber() {
  try {
    const response = await request(`${ENDPOINT}/caliber`)
    if (response.ok) {
      caliber.value = await response.json()
    }
  } catch {
    // 口径读不到不阻塞列表
  }
}

function openCreate() {
  const code = window.prompt('站区编号')
  if (!code) return
  const capacity = window.prompt('主变容量（MVA，必填数值）')
  if (capacity == null || Number.isNaN(Number(capacity))) {
    errorMessage.value = '主变容量必须为数值，未登记'
    return
  }
  const voltage = window.prompt('电压等级', '220kV') || ''
  const load = window.prompt('当前负荷（MW，可留空，留空则暂不判定）') ?? ''
  const duration = window.prompt('本次负荷持续时长（分钟，可留空）') ?? ''
  const values: Record<string, string | number> = {
    站区编号: code,
    主变容量: Number(capacity),
    电压等级: voltage,
  }
  if (load.trim() !== '') values['当前负荷'] = Number(load)
  if (duration.trim() !== '') values['持续时长(分钟)'] = Number(duration)
  void submitCreate(values)
}

async function submitCreate(values: Record<string, string | number>) {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '升压站登记失败')
    }
    successMessage.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '升压站登记失败'
  }
}

async function reportLoad(row: Row) {
  const load = window.prompt(`上报 ${String(row['站区编号'])} 当前负荷（MW）`, String(row['判定用负荷'] ?? ''))
  if (load == null || load.trim() === '' || Number.isNaN(Number(load))) {
    return
  }
  const duration = window.prompt('本次负荷持续时长（分钟，用于区分短时冲击/持续越限）', '')
  if (duration === null) return
  const values: Record<string, number> = { 当前负荷: Number(load) }
  if (duration.trim() !== '') values['持续时长(分钟)'] = Number(duration)
  try {
    const response = await request(`${ENDPOINT}/${String(row.id)}/load`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '负荷上报失败')
    }
    successMessage.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '负荷上报失败'
  }
}

async function openCaliber() {
  const limit = window.prompt('负荷率上限（0~1 之间，例如 0.8）', String(caliber.value.load_rate_upper_limit))
  if (limit == null) return
  const minutes = window.prompt('短时冲击时长阈值（分钟）', String(caliber.value.short_term_minutes))
  if (minutes == null) return
  if (Number.isNaN(Number(limit)) || Number.isNaN(Number(minutes))) {
    errorMessage.value = '口径参数必须为数值'
    return
  }
  if (!window.confirm('口径调整后将生成新版本，全部升压站按新口径重算，重算前结论自动留档。是否继续？')) {
    return
  }
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/caliber/adjust`, {
      method: 'POST',
      body: JSON.stringify({
        values: { load_rate_upper_limit: Number(limit), short_term_minutes: Number(minutes) },
      }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '口径调整失败')
    }
    successMessage.value = payload.message
    await Promise.all([loadCaliber(), reload()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '口径调整失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${String(row.id)}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '升压站动作未生效')
    }
    successMessage.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '升压站操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  successMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
  if (statusFilter.value) query.set('status', statusFilter.value)
  if (overloadOnly.value) query.set('overload', 'true')
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('升压站列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (payload.stats) stats.value = payload.stats
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '升压站列表读取失败'
  }
}

onMounted(() => {
  void loadCaliber()
  void reload()
})
</script>

<style scoped>
.caliber-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  align-items: center;
  padding: 0.6rem 0.9rem;
  margin-bottom: 0.8rem;
  background: var(--color-surface-alt, #f4f7fb);
  border: 1px solid #d9e2ef;
  border-radius: 8px;
  font-size: 0.85rem;
}
.status-tag {
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  font-style: normal;
  font-size: 0.8rem;
}
.status-overload {
  color: #b42318;
  background: #fee4e2;
}
.status-normal {
  color: #067647;
  background: #d1fadf;
}
.status-stopped {
  color: #475467;
  background: #eaecf0;
}
.status-pending {
  color: #b54708;
  background: #fef0c7;
}
.overload-tag {
  padding: 0.1rem 0.5rem;
  border-radius: 4px;
  font-size: 0.8rem;
}
.overload-tag.short {
  color: #b54708;
  background: #fef0c7;
}
.overload-tag.sustained {
  color: #b42318;
  background: #fee4e2;
}
.cell-hint {
  color: #b54708;
  font-size: 0.75rem;
}
.dedup-hint {
  margin: 0.5rem 0;
  color: #667085;
  font-size: 0.8rem;
}
.success-text {
  color: #067647;
}
</style>
