<template>
  <section class="page-grid" v-loading="loading">
    <section class="panel date-navigation-panel">
      <div class="date-navigation" aria-label="融资新闻日期筛选">
        <el-button @click="changeDay(-1)">上一天</el-button>
        <el-date-picker
          v-model="selectedDate"
          type="date"
          value-format="YYYY-MM-DD"
          format="YYYY年MM月DD日"
          placeholder="选择日期"
          :clearable="false"
          :disabled-date="disableFutureDate"
          @change="selectDate"
        />
        <el-button :disabled="isCurrentDate" @click="changeDay(1)">下一天</el-button>
      </div>
    </section>

    <div class="metric-grid">
      <div class="metric-card">
        <div class="value">{{ data?.financing_items?.length ?? "-" }}</div>
        <div class="label">去重后事件</div>
      </div>
      <div class="metric-card">
        <div class="value">{{ data?.selected_count ?? "-" }}</div>
        <div class="label">所选日期原始命中</div>
      </div>
      <div class="metric-card">
        <div class="value">{{ data?.total_count ?? "-" }}</div>
        <div class="label">累计原始命中</div>
      </div>
      <div class="metric-card">
        <div class="value">{{ windowLabel }}</div>
        <div class="label">展示窗口</div>
      </div>
    </div>

    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>{{ data?.financing_window_label || "融资新闻" }}</h2>
          <p class="muted">上周与本周总结均按已识别融资事件去重生成 Markdown</p>
        </div>
        <div class="toolbar">
          <el-button :loading="identifying" type="primary" @click="identifyThisWeek">本周识别</el-button>
          <el-button
            :disabled="currentWeekReporting"
            :loading="previousWeekReporting"
            type="success"
            @click="generatePreviousWeekReport"
          >
            上周总结
          </el-button>
          <el-button
            :disabled="previousWeekReporting"
            :loading="currentWeekReporting"
            type="success"
            @click="generateCurrentWeekReport"
          >
            本周总结
          </el-button>
          <el-button @click="load">刷新</el-button>
        </div>
      </div>
      <div class="toolbar report-toolbar">
        <el-input
          v-model="reportOutputDir"
          placeholder="周报保存目录或 .md 文件路径"
          style="max-width: 680px"
        >
          <template #append>
            <el-button
              :icon="FolderOpened"
              :loading="selectingReportDir"
              @click="selectReportDirectory"
            >
              浏览
            </el-button>
          </template>
        </el-input>
        <span v-if="reportPath" class="muted">已生成：{{ reportPath }}</span>
      </div>
      <el-alert
        v-if="reportError"
        class="report-error"
        :title="reportErrorTitle"
        :description="reportError"
        type="error"
        show-icon
        :closable="false"
      />
      <div class="page-grid">
        <el-card
          v-for="item in data?.financing_items || []"
          :key="item.content_id"
          class="financing-news-card"
          shadow="never"
        >
          <div class="financing-card-header">
            <RouterLink class="item-title" :to="`/content/${item.content_id}`">{{ item.title }}</RouterLink>
            <el-button
              class="exclude-button"
              :icon="Delete"
              :loading="isExcluding(item)"
              plain
              size="small"
              type="danger"
              @click="excludeItem(item)"
            >
              排除
            </el-button>
          </div>
          <p class="financing-card-summary">{{ item.summary }}</p>
          <div class="toolbar">
            <el-tag>{{ item.source_name }}</el-tag>
            <el-tag type="info">{{ item.display_time }}</el-tag>
            <el-tag :type="item.llm_status === 'success' ? 'success' : 'warning'">LLM {{ item.llm_status }}</el-tag>
            <a :href="item.url" target="_blank">原文</a>
          </div>
          <el-collapse v-if="item.related_count">
            <el-collapse-item :title="`另有 ${item.related_count} 条相关报道 · ${item.source_names}`">
              <div v-for="related in item.related_reports" :key="related.content_id" class="toolbar related-report-row">
                <RouterLink :to="`/content/${related.content_id}`">{{ related.title }}</RouterLink>
                <span class="muted">{{ related.source_name }} · {{ related.display_time }}</span>
                <a :href="related.url" target="_blank">原文</a>
              </div>
            </el-collapse-item>
          </el-collapse>
        </el-card>
        <el-empty v-if="!data?.financing_items?.length" description="暂无融资新闻" />
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox, ElNotification } from "element-plus";
import { Delete, FolderOpened } from "@element-plus/icons-vue";
import { isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { api, notifyError } from "../api/client";

const route = useRoute();
const router = useRouter();
const loading = ref(false);
const identifying = ref(false);
const previousWeekReporting = ref(false);
const currentWeekReporting = ref(false);
const selectingReportDir = ref(false);
const data = ref<any>(null);
const selectedDate = ref(validDateString(route.query.date) ? String(route.query.date) : "");
const currentDate = ref("");
const excludingIds = ref<number[]>([]);
const reportOutputDir = ref("");
const reportPath = ref("");
const reportError = ref("");
const reportErrorTitle = ref("总结生成失败");

const isCurrentDate = computed(() => Boolean(currentDate.value && selectedDate.value >= currentDate.value));
const windowLabel = computed(() => {
  const label = data.value?.financing_window_label || "";
  if (label.includes("最新")) return "最新";
  if (data.value?.selected_date && data.value.selected_date !== data.value.current_date) return "日期";
  return "今日";
});

function validDateString(value: unknown): boolean {
  if (typeof value !== "string" || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day;
}

function formatDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function shiftedDate(value: string, offset: number): string {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  date.setDate(date.getDate() + offset);
  return formatDate(date);
}

function disableFutureDate(value: Date): boolean {
  if (!currentDate.value) return false;
  return formatDate(value) > currentDate.value;
}

function financingPath() {
  const params = new URLSearchParams();
  if (validDateString(selectedDate.value)) {
    params.set("date", selectedDate.value);
  }
  const query = params.toString();
  return query ? `/financing?${query}` : "/financing";
}

async function load() {
  loading.value = true;
  try {
    data.value = await api.get<any>(financingPath());
    selectedDate.value = data.value?.selected_date || selectedDate.value;
    currentDate.value = data.value?.current_date || currentDate.value;
    if (!reportOutputDir.value && data.value?.weekly_report_dir) {
      reportOutputDir.value = data.value.weekly_report_dir;
    }
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

async function updateSelectedDate(value: string) {
  if (!validDateString(value) || (currentDate.value && value > currentDate.value)) return;
  selectedDate.value = value;
  await router.replace({ query: value === currentDate.value ? {} : { date: value } });
  await load();
}

async function selectDate(value: string | null) {
  if (value) await updateSelectedDate(value);
}

async function changeDay(offset: number) {
  if (!selectedDate.value) return;
  await updateSelectedDate(shiftedDate(selectedDate.value, offset));
}

function contentIdsForItem(item: any): number[] {
  return Array.from(
    new Set(
      [item?.content_id, ...(item?.related_reports || []).map((related: any) => related.content_id)]
        .map((contentId) => Number(contentId))
        .filter((contentId) => Number.isFinite(contentId) && contentId > 0),
    ),
  );
}

function isExcluding(item: any) {
  return excludingIds.value.includes(Number(item?.content_id));
}

async function excludeItem(item: any) {
  const contentIds = contentIdsForItem(item);
  if (!contentIds.length) return;
  const relatedText = contentIds.length > 1 ? `及 ${contentIds.length - 1} 条相关报道` : "";
  try {
    await ElMessageBox.confirm(`确认将这条融资新闻${relatedText}从融资页排除？`, "排除融资新闻", {
      confirmButtonText: "排除",
      cancelButtonText: "取消",
      type: "warning",
    });
  } catch {
    return;
  }

  const primaryId = Number(item.content_id);
  excludingIds.value = Array.from(new Set([...excludingIds.value, primaryId]));
  try {
    const payload = await api.post<any>("/financing/exclude", { content_ids: contentIds });
    ElMessage.success(`已排除 ${payload.excluded || contentIds.length} 条新闻`);
    await load();
  } catch (error) {
    notifyError(error);
  } finally {
    excludingIds.value = excludingIds.value.filter((contentId) => contentId !== primaryId);
  }
}

async function identifyThisWeek() {
  identifying.value = true;
  try {
    const payload = await api.post<any>("/financing/identify-this-week", { limit: 300 });
    const result = payload.result || {};
    if (!result.financing_llm_ready) {
      ElMessage.warning("融资识别 LLM 任务未配置，已完成可用标签检查");
    } else {
      ElMessage.success(`本周识别完成：候选 ${result.candidates || 0} 条，高相关 ${result.high || 0} 条`);
    }
    await load();
  } catch (error) {
    notifyError(error);
  } finally {
    identifying.value = false;
  }
}

async function selectReportDirectory() {
  selectingReportDir.value = true;
  try {
    if (isTauri()) {
      const selected = await open({
        directory: true,
        multiple: false,
        title: "选择周报保存文件夹",
        defaultPath: reportOutputDir.value || undefined,
      });
      if (typeof selected === "string") {
        reportOutputDir.value = selected;
      }
    } else {
      const payload = await api.post<{ path: string }>("/system/select-directory", {
        initial_path: reportOutputDir.value,
      });
      if (payload.path) {
        reportOutputDir.value = payload.path;
      }
    }
  } catch (error) {
    notifyError(error);
  } finally {
    selectingReportDir.value = false;
  }
}

function notifyReportGenerated(periodLabel: string, report: any) {
  reportError.value = "";
  const savedPath = report.path || reportOutputDir.value || "已选择的保存目录";
  ElNotification({
    title: periodLabel + "总结生成成功",
    message: "文件已保存至：" + savedPath,
    type: "success",
    duration: 10000,
    position: "bottom-right",
    showClose: true,
  });
}

function reportErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

async function generatePreviousWeekReport() {
  previousWeekReporting.value = true;
  reportError.value = "";
  try {
    const payload = await api.post<any>("/financing/weekly-report", { output_dir: reportOutputDir.value });
    const report = payload.report || {};
    reportPath.value = report.path || "";
    notifyReportGenerated("上周", report);
    await load();
  } catch (error) {
    reportErrorTitle.value = "上周总结生成失败";
    reportError.value = reportErrorMessage(error);
    notifyError(error);
  } finally {
    previousWeekReporting.value = false;
  }
}

async function generateCurrentWeekReport() {
  currentWeekReporting.value = true;
  reportError.value = "";
  try {
    const payload = await api.post<any>("/financing/current-week-report", { output_dir: reportOutputDir.value });
    const report = payload.report || {};
    reportPath.value = report.path || "";
    notifyReportGenerated("本周", report);
    await load();
  } catch (error) {
    reportErrorTitle.value = "本周总结生成失败";
    reportError.value = reportErrorMessage(error);
    notifyError(error);
  } finally {
    currentWeekReporting.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.date-navigation-panel {
  padding: 12px 18px;
}

.date-navigation {
  align-items: center;
  display: flex;
  gap: 10px;
  justify-content: center;
}

.date-navigation :deep(.el-date-editor) {
  width: 190px;
}

.financing-news-card :deep(.el-card__body) {
  display: grid;
  gap: 12px;
}

.financing-card-header {
  align-items: flex-start;
  display: flex;
  gap: 16px;
  justify-content: space-between;
}

.financing-card-header .item-title {
  flex: 1;
  line-height: 1.5;
  min-width: 0;
}

.exclude-button {
  flex: 0 0 auto;
}

.financing-card-summary {
  line-height: 1.7;
  margin: 0;
}

.related-report-row {
  align-items: center;
}

@media (max-width: 720px) {
  .date-navigation,
  .financing-card-header {
    align-items: stretch;
    flex-direction: column;
  }

  .date-navigation :deep(.el-date-editor) {
    width: 100%;
  }

  .exclude-button {
    align-self: flex-start;
  }
}
</style>