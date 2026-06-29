<template>
  <section class="page-grid" v-loading="loading">
    <section class="panel">
      <div class="panel-header">
        <h2>系统设置</h2>
        <el-button type="primary" @click="save">保存设置</el-button>
      </div>
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item label="每日抓取时间（北京时间）"><el-input v-model="settings.daily_crawl_time" /></el-form-item>
          <el-form-item label="clean_text 缓存小时数"><el-input-number v-model="settings.content_cache_hours" :min="1" /></el-form-item>
          <el-form-item label="每日汇总最大 item 数"><el-input-number v-model="settings.daily_summary_max_items" :min="1" /></el-form-item>
          <el-form-item label="信息源并发数"><el-input-number v-model="settings.source_parallelism" :min="1" :max="12" /></el-form-item>
          <el-form-item label="LLM 并发数"><el-input-number v-model="settings.llm_parallelism" :min="1" :max="6" /></el-form-item>
          <el-form-item label="LLM 每日汇总文本">
            <el-switch v-model="settings.daily_summary_use_llm" />
          </el-form-item>
        </div>
      </el-form>
    </section>

    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>网络代理</h2>
          <p class="muted">默认关闭；保存后会立即应用到当前后端进程。</p>
        </div>
        <el-button @click="save">保存并应用代理</el-button>
      </div>
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item label="代理模式">
            <el-select v-model="settings.network_proxy_mode">
              <el-option label="关闭代理" value="off" />
              <el-option label="跟随系统代理" value="system" />
              <el-option label="手动代理" value="custom" />
            </el-select>
          </el-form-item>
          <el-form-item label="手动代理地址">
            <el-input
              v-model="settings.network_proxy_url"
              :disabled="settings.network_proxy_mode !== 'custom'"
              placeholder="例如 http://127.0.0.1:7890"
            />
          </el-form-item>
          <el-form-item class="full" label="不走代理的地址">
            <el-input v-model="settings.network_proxy_no_proxy" placeholder="localhost,127.0.0.1,::1" />
          </el-form-item>
        </div>
      </el-form>
      <el-descriptions :column="2" border>
        <el-descriptions-item v-for="(value, key) in proxyInfo" :key="key" :label="key">{{ value || "-" }}</el-descriptions-item>
      </el-descriptions>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>备份</h2>
        <el-button type="primary" @click="backup">手动创建备份</el-button>
      </div>
      <el-table :data="backups" stripe>
        <el-table-column prop="created_at" label="时间" width="180" />
        <el-table-column prop="backup_type" label="类型" width="100" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column prop="integrity_status" label="完整性" width="120" />
        <el-table-column prop="backup_path" label="路径" />
      </el-table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { api, notifyError } from "../api/client";

const loading = ref(false);
const settings = reactive<any>({});
const backups = ref<any[]>([]);
const proxyInfo = ref<Record<string, string>>({});

function normalizeSettings(raw: any) {
  Object.assign(settings, {
    daily_crawl_time: raw.daily_crawl_time || "10:00",
    content_cache_hours: Number(raw.content_cache_hours || 48),
    daily_summary_max_items: Number(raw.daily_summary_max_items || 50),
    source_parallelism: Number(raw.source_parallelism || 4),
    llm_parallelism: Number(raw.llm_parallelism || 2),
    daily_summary_use_llm: raw.daily_summary_use_llm === "true",
    network_proxy_mode: raw.network_proxy_mode || "off",
    network_proxy_url: raw.network_proxy_url || "",
    network_proxy_no_proxy: raw.network_proxy_no_proxy || "localhost,127.0.0.1,::1",
  });
}

async function load() {
  loading.value = true;
  try {
    const payload: any = await api.get("/settings");
    normalizeSettings(payload.settings || {});
    backups.value = payload.backups || [];
    proxyInfo.value = payload.proxy_info || {};
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

async function save() {
  try {
    await api.patch("/settings", settings);
    ElMessage.success("已保存设置");
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function backup() {
  try {
    const payload: any = await api.post("/settings/backup");
    ElMessage[payload.ok ? "success" : "error"](payload.backup?.message || "备份完成");
    await load();
  } catch (error) {
    notifyError(error);
  }
}

onMounted(load);
</script>
