<template>
  <section class="page-grid" v-loading="loading">
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>{{ selectedDate }} 汇总</h2>
          <p v-if="summary" class="muted">
            总条数 {{ summary.total_items }}，成功 {{ summary.successful_items }}，partial {{ summary.partial_items }}，失败
            {{ summary.failed_items }}
          </p>
          <p v-else class="muted">当前日期还没有生成汇总。</p>
        </div>
        <div class="toolbar">
          <el-date-picker
            v-model="selectedDate"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            :clearable="false"
            @change="goSelectedDate"
          />
          <el-tag v-if="summary">LLM {{ summary.llm_summary_status }}</el-tag>
          <el-button v-if="summary" @click="copyMarkdown">复制 Markdown</el-button>
          <el-button v-else type="primary" @click="generateSelectedSummary">生成该日期汇总</el-button>
        </div>
      </div>

      <pre v-if="summary" class="summary-text">{{ summary.markdown_text }}</pre>
      <el-empty v-else description="暂无该日期汇总">
        <el-button type="primary" @click="generateSelectedSummary">生成该日期汇总</el-button>
      </el-empty>
    </section>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, notifyError } from "../api/client";

const route = useRoute();
const router = useRouter();
const loading = ref(false);
const selectedDate = ref("");
const summary = ref<any>(null);

async function load() {
  const date = String(route.params.date || "");
  selectedDate.value = date;
  loading.value = true;
  summary.value = null;
  try {
    const payload: any = await api.get(`/summaries/${date}`);
    summary.value = payload.summary;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (!message.includes("404") && !message.includes("Summary not found")) {
      notifyError(error);
    }
  } finally {
    loading.value = false;
  }
}

async function copyMarkdown() {
  try {
    const date = String(route.params.date || "");
    const markdown = await api.markdown(`/summaries/${date}/markdown`);
    await navigator.clipboard.writeText(markdown);
    ElMessage.success("已复制 Markdown");
  } catch (error) {
    notifyError(error);
  }
}

async function generateSelectedSummary() {
  if (!selectedDate.value) return;
  loading.value = true;
  try {
    const payload: any = await api.post("/summaries/generate", { summary_date: selectedDate.value });
    summary.value = payload.summary;
    await router.replace(`/summaries/${payload.summary.summary_date}`);
    ElMessage.success("汇总已生成");
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

async function goSelectedDate(value: string) {
  if (!value || value === route.params.date) return;
  await router.push(`/summaries/${value}`);
}

watch(() => route.params.date, load, { immediate: true });
</script>
