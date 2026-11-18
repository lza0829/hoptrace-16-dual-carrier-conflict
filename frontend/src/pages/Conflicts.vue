<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const groups = ref([])
const err = ref('')

onMounted(async () => {
  try {
    groups.value = await api.conflicts()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

const FIELD_ROWS = [
  { key: 'event_ts', label: 'event_ts' },
  { key: 'seq', label: 'seq' },
  { key: 'location_code', label: '地点' },
  { key: 'event_code', label: '事件' },
]

function axisLabel(g) {
  return g.axis_type === 'seq' ? `同序 #${g.axis_value}` : `同时间窗 ${g.axis_value}`
}

function originLabel(m) {
  return m.origin === 'official' ? '正式线留证' : '入批隔离'
}
</script>

<template>
  <div class="page-card conflicts-page">
    <h1>冲突</h1>
    <p class="lead">
      双承运源同序 / 同 event_ts 窗但地点或事件分歧的冲突组。整组隔离、双方 payload 留证，
      正式线保持冲突前态 —— 任一方都不会单独进正式线。
    </p>
    <p v-if="err" class="muted">{{ err }}</p>

    <div v-for="g in groups" :key="g.id" class="conflict-card">
      <div class="conflict-head">
        <span class="gid">{{ g.id }}</span>
        <span class="tag">{{ g.waybill_id }}</span>
        <span class="tag">{{ axisLabel(g) }}</span>
        <span v-for="f in g.divergent_fields" :key="f" class="tag diff">分歧 {{ f }}</span>
        <span class="muted">批次 {{ g.batch_id }} · {{ g.created_at }}</span>
      </div>
      <table class="compare">
        <thead>
          <tr>
            <th></th>
            <th v-for="m in g.members" :key="m.hop_id">
              {{ m.source }}
              <div class="origin">{{ m.hop_id }} · {{ originLabel(m) }}</div>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in FIELD_ROWS" :key="row.key">
            <td class="flabel">{{ row.label }}</td>
            <td
              v-for="m in g.members"
              :key="m.hop_id"
              :class="{ diff: g.divergent_fields.includes(row.key) }"
            >
              {{ m[row.key] ?? '—' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-if="!groups.length && !err" class="muted">暂无冲突组 —— 跑一轮双源装载后在此对照双方。</p>
  </div>
</template>
