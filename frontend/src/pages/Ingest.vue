<script setup>
import { ref } from 'vue'
import { api } from '../api.js'

const result = ref(null)
const busy = ref(false)
const err = ref('')

async function run() {
  busy.value = true
  err.value = ''
  try {
    result.value = await api.ingest()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page-card">
    <h1>装载</h1>
    <p class="lead">
      从本地夹具跑一批双承运源装载。同运单同序 / 同 event_ts 窗且地点或事件分歧的点
      整组进隔离（双方 payload 留证），正式线保持冲突前态；无冲突点按既有规则入正式线。
    </p>
    <button class="btn" :disabled="busy" @click="run">{{ busy ? '装载中…' : '跑装载批次' }}</button>
    <p v-if="err" class="muted" style="margin-top:12px">{{ err }}</p>
    <pre v-if="result" style="margin-top:16px;font-family:var(--mono);font-size:12px;white-space:pre-wrap">{{ result }}</pre>
  </div>
</template>
