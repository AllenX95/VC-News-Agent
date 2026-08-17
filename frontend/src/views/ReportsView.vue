<template>
  <section class="page-grid" v-loading="loading">
    <section class="panel automation-report-card">
      <div class="panel-header">
        <div>
          <h2>最新自动日报</h2>
          <p class="muted">Headless / Codex 生成的自包含 HTML 日报</p>
        </div>
        <div class="toolbar">
          <el-tag v-if="automationStatus" :type="automationTagType(automationStatus.status)">
            {{ automationStatusLabel(automationStatus.status) }}
          </el-tag>
          <el-button
            v-if="automationStatus?.html_available"
            type="primary"
            @click="openAutomationReport"
          >
            打开 HTML 日报
          </el-button>
        </div>
      </div>
      <div v-if="automationStatus" class="automation-report-meta">
        <span>目标日期：{{ automationStatus.target_date || "-" }}</span>
        <span v-if="automationStatus.latest_run_id">运行：{{ automationStatus.latest_run_id }}</span>
        <span v-if="automationStatus.finished_at">完成：{{ automationStatus.finished_at }}</span>
      </div>
      <div v-if="automationStatus?.counts && Object.keys(automationStatus.counts).length" class="automation-report-counts">
        <el-tag v-for="(value, key) in automationStatus.counts" :key="key" effect="plain">
          {{ key }}：{{ value }}
        </el-tag>
      </div>
      <p v-if="automationStatus?.warnings?.length" class="muted automation-report-warnings">
        注意：{{ automationStatus.warnings.join("；") }}
      </p>
      <p v-if="automationStatus?.error" class="automation-report-error">{{ automationStatus.error }}</p>
      <p v-if="!automationStatus" class="muted">尚未读取自动日报状态</p>
    </section>

    <section class="panel">
      <div class="panel-header">
        <div><h2>报告工作区</h2><p class="muted">先预览输入，再生成草稿；每次生成或编辑都会保留独立版本。</p></div>
        <el-button @click="loadReports">刷新</el-button>
      </div>
      <el-form class="report-form" label-position="top">
        <el-form-item label="报告类型">
          <el-select v-model="form.report_type" style="width: 230px">
            <el-option label="融资周报" value="weekly_financing" />
            <el-option label="本周融资动态" value="current_week_financing" />
            <el-option label="关注项摘要" value="watchlist_digest" />
            <el-option label="自定义" value="custom" />
          </el-select>
        </el-form-item>
        <el-form-item label="开始日期"><el-date-picker v-model="form.period_start" type="date" value-format="YYYY-MM-DD" /></el-form-item>
        <el-form-item label="结束日期"><el-date-picker v-model="form.period_end" type="date" value-format="YYYY-MM-DD" /></el-form-item>
        <el-form-item label="标题（可选）"><el-input v-model="form.title" placeholder="生成时自动命名" /></el-form-item>
      </el-form>
      <div class="toolbar">
        <el-button :loading="previewLoading" @click="preview">预览输入</el-button>
        <el-button type="primary" :loading="generating" :disabled="!previewInputs.length" @click="generate">生成 v1 草稿</el-button>
      </div>
    </section>

    <section v-if="previewInputs.length" class="panel">
      <div class="panel-header"><h3>报告输入预览</h3><span class="muted">已选 {{ selectedInputIds.length }} / {{ previewInputs.length }}</span></div>
      <el-checkbox-group v-model="selectedInputIds" class="input-list">
        <el-checkbox v-for="item in previewInputs" :key="`${item.target_type}-${item.target_id}`" :label="item.target_id">
          <span>{{ item.title || item.snapshot?.event_title || item.snapshot?.title || `#${item.target_id}` }}</span>
          <small class="muted">{{ item.target_type }} · {{ item.summary || "暂无摘要" }}</small>
        </el-checkbox>
      </el-checkbox-group>
      <div v-if="selectedInputIds.length" class="selected-order">
        <span class="muted">生成顺序：</span>
        <div v-for="(targetId, index) in selectedInputIds" :key="`selected-${targetId}`" class="selected-order-item">
          <span>{{ index + 1 }}. {{ previewInputs.find((item) => item.target_id === targetId)?.title || `#${targetId}` }}</span>
          <el-button size="small" :disabled="index === 0" @click="moveInput(index, -1)">上移</el-button>
          <el-button size="small" :disabled="index === selectedInputIds.length - 1" @click="moveInput(index, 1)">下移</el-button>
        </div>
      </div>
    </section>

    <section class="report-layout">
      <section class="panel report-list-panel">
        <div class="panel-header"><h3>报告列表</h3><span class="muted">{{ reports.length }} 条</span></div>
        <el-empty v-if="!reports.length" description="还没有报告" />
        <button v-for="report in reports" :key="report.report_id" class="report-list-item" :class="{ active: selectedReport?.report_id === report.report_id }" @click="openReport(report.report_id)">
          <strong>{{ report.title }}</strong>
          <span>{{ report.status }} · v{{ report.latest_version_number }}</span>
        </button>
      </section>

      <section v-if="selectedReport" class="panel report-editor-panel">
        <div class="panel-header">
          <div><h3>{{ selectedReport.title }}</h3><p class="muted">{{ selectedReport.period_start }} 至 {{ selectedReport.period_end }}</p></div>
          <div class="toolbar">
            <el-button :loading="regenerating" @click="regenerate">重新生成</el-button>
            <el-button type="primary" @click="saveVersion">保存为新版本</el-button>
            <el-button @click="exportReport">导出 Markdown</el-button>
          </div>
        </div>
        <div class="toolbar report-status-toolbar">
          <el-tag>{{ selectedReport.status }}</el-tag>
          <el-select v-model="selectedReport.status" size="small" style="width: 130px" @change="updateStatus">
            <el-option label="草稿" value="draft" /><el-option label="已复核" value="reviewed" /><el-option label="已归档" value="archived" />
          </el-select>
          <span class="muted">输入快照 {{ selectedReport.inputs.length }} 条</span>
        </div>
        <el-input v-model="markdownText" type="textarea" :rows="22" class="markdown-editor" />
        <div class="version-list">
          <span class="muted">历史版本：</span>
          <el-button v-for="version in selectedReport.versions" :key="version.version_number" size="small" :type="version.version_number === selectedReport.latest_version_number ? 'primary' : 'default'" @click="openVersion(version.version_number)">
            v{{ version.version_number }} · {{ version.version_source }}
          </el-button>
        </div>
      </section>
      <section v-else class="panel empty-state"><el-empty description="从左侧选择报告，或先生成一份报告" /></section>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";

import { api, getAutomationLatestReportUrl, notifyError, openExternalUrl } from "../api/client";
import type { AutomationStatus, Report, ReportVersion } from "../api/types";

type ReportInput = { target_type: string; target_id: number; title?: string; summary?: string; snapshot?: Record<string, any> };

const loading = ref(false);
const previewLoading = ref(false);
const generating = ref(false);
const regenerating = ref(false);
const reports = ref<Report[]>([]);
const automationStatus = ref<AutomationStatus | null>(null);
const selectedReport = ref<Report | null>(null);
const markdownText = ref("");
const previewInputs = ref<ReportInput[]>([]);
const selectedInputIds = ref<number[]>([]);
const form = reactive({ report_type: "weekly_financing", period_start: defaultStart(), period_end: defaultEnd(), title: "" });

function defaultEnd() { return new Date().toISOString().slice(0, 10); }
function defaultStart() { const date = new Date(); date.setDate(date.getDate() - 7); return date.toISOString().slice(0, 10); }

function moveInput(index: number, offset: number) {
  const nextIndex = index + offset;
  if (nextIndex < 0 || nextIndex >= selectedInputIds.value.length) return;
  const next = [...selectedInputIds.value];
  [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
  selectedInputIds.value = next;
}

async function loadReports() {
  loading.value = true;
  try {
    reports.value = (await api.get<{ items: Report[] }>("/reports")).items;
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

async function loadAutomationStatus() {
  try {
    automationStatus.value = await api.get<AutomationStatus>("/automation/status");
  } catch (error) {
    notifyError(error);
  }
}

function automationStatusLabel(status: string): string {
  return {
    running: "运行中",
    success: "成功",
    partial: "部分完成",
    missing: "暂无日报",
    corrupt: "产物异常",
    invalid: "日期无效",
  }[status] || status || "未知";
}

function automationTagType(status: string): "success" | "warning" | "danger" | "info" {
  if (status === "success") return "success";
  if (status === "running" || status === "partial") return "warning";
  if (status === "corrupt" || status === "invalid") return "danger";
  return "info";
}

async function openAutomationReport() {
  const targetDate = automationStatus.value?.target_date;
  try {
    await openExternalUrl(getAutomationLatestReportUrl(targetDate));
  } catch (error) {
    notifyError(error);
  }
}

async function preview() {
  previewLoading.value = true;
  try {
    const payload = await api.post<{ inputs: ReportInput[] }>("/reports/preview", {
      report_type: form.report_type,
      period_start: form.period_start,
      period_end: form.period_end,
    });
    previewInputs.value = payload.inputs;
    selectedInputIds.value = payload.inputs.map((item) => item.target_id);
    ElMessage.success(`已找到 ${payload.inputs.length} 条默认输入`);
  } catch (error) {
    notifyError(error);
  } finally {
    previewLoading.value = false;
  }
}

async function generate() {
  generating.value = true;
  try {
    const payload = await api.post<{ report: Report }>("/reports", {
      report_type: form.report_type,
      period_start: form.period_start,
      period_end: form.period_end,
      title: form.title || undefined,
      target_type: form.report_type === "watchlist_digest" ? "watch_item" : "financing_event",
      target_ids: selectedInputIds.value,
    });
    selectedReport.value = payload.report;
    markdownText.value = payload.report.markdown_text || "";
    ElMessage.success("报告 v1 草稿已生成");
    await loadReports();
  } catch (error) {
    notifyError(error);
  } finally {
    generating.value = false;
  }
}

async function openReport(reportId: number) {
  try {
    const payload = await api.get<{ report: Report }>(`/reports/${reportId}`);
    selectedReport.value = payload.report;
    markdownText.value = payload.report.markdown_text || "";
  } catch (error) {
    notifyError(error);
  }
}

async function openVersion(versionNumber: number) {
  if (!selectedReport.value) return;
  try {
    const payload = await api.get<{ version: ReportVersion }>(`/reports/${selectedReport.value.report_id}/versions/${versionNumber}`);
    markdownText.value = payload.version.markdown_text || "";
  } catch (error) {
    notifyError(error);
  }
}

async function saveVersion() {
  if (!selectedReport.value || !markdownText.value.trim()) return;
  try {
    const payload = await api.post<{ report: Report }>(`/reports/${selectedReport.value.report_id}/versions`, { markdown_text: markdownText.value });
    selectedReport.value = payload.report;
    markdownText.value = payload.report.markdown_text || markdownText.value;
    ElMessage.success("已保存为新版本");
    await loadReports();
  } catch (error) {
    notifyError(error);
  }
}

async function regenerate() {
  if (!selectedReport.value) return;
  regenerating.value = true;
  try {
    const payload = await api.post<{ report: Report }>(`/reports/${selectedReport.value.report_id}/generate`);
    selectedReport.value = payload.report;
    markdownText.value = payload.report.markdown_text || "";
    ElMessage.success("已生成新版本");
    await loadReports();
  } catch (error) {
    notifyError(error);
  } finally {
    regenerating.value = false;
  }
}

async function updateStatus() {
  if (!selectedReport.value) return;
  try {
    const payload = await api.patch<{ report: Report }>(`/reports/${selectedReport.value.report_id}/status`, { status: selectedReport.value.status });
    selectedReport.value = payload.report;
    await loadReports();
  } catch (error) {
    notifyError(error);
  }
}

async function exportReport() {
  if (!selectedReport.value) return;
  try {
    const payload = await api.post<{ file_path: string }>(`/reports/${selectedReport.value.report_id}/export`, {});
    ElMessage.success(`已导出：${payload.file_path}`);
    await openReport(selectedReport.value.report_id);
  } catch (error) {
    notifyError(error);
  }
}

onMounted(async () => {
  await Promise.all([loadReports(), loadAutomationStatus()]);
  if (reports.value.length) await openReport(reports.value[0].report_id);
});
</script>

<style scoped>
.automation-report-card {
  border-left: 4px solid #2563eb;
}

.automation-report-meta,
.automation-report-counts {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.automation-report-meta {
  color: #475569;
  font-size: 13px;
  margin-bottom: 10px;
}

.automation-report-warnings {
  margin: 12px 0 0;
}

.automation-report-error {
  color: #b91c1c;
  margin: 12px 0 0;
}

.report-form { display: grid; gap: 12px; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.input-list { display: grid; gap: 10px; }
.input-list :deep(.el-checkbox) { align-items: flex-start; display: flex; height: auto; white-space: normal; }
.input-list small { display: block; margin-left: 8px; margin-top: 3px; }
.selected-order { display: grid; gap: 8px; margin-top: 16px; }
.selected-order-item { align-items: center; display: flex; flex-wrap: wrap; gap: 6px; }
.report-layout { display: grid; gap: 16px; grid-template-columns: minmax(240px, 0.35fr) minmax(0, 1fr); }
.report-list-panel { align-self: start; display: grid; gap: 8px; }
.report-list-item { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; cursor: pointer; display: grid; gap: 4px; padding: 12px; text-align: left; }
.report-list-item.active { border-color: #2563eb; background: #eff6ff; }
.report-list-item span { color: #64748b; font-size: 12px; }
.report-editor-panel { min-width: 0; }
.markdown-editor :deep(textarea) { font-family: Consolas, "SFMono-Regular", monospace; line-height: 1.55; }
.version-list { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.report-status-toolbar { margin-bottom: 12px; }
@media (max-width: 1000px) { .report-form, .report-layout { grid-template-columns: 1fr; } }
</style>
