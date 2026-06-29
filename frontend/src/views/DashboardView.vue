<template>
  <section class="page-grid" v-loading="loading">
    <section class="panel date-navigation-panel">
      <div class="date-navigation" aria-label="新闻日期导航">
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
        <div class="value">{{ dashboard?.today_contents ?? "-" }}</div>
        <div class="label">当日新增内容</div>
      </div>
      <div class="metric-card">
        <div class="value">{{ dashboard?.total_contents ?? "-" }}</div>
        <div class="label">累计内容</div>
      </div>
      <div class="metric-card">
        <div class="value">{{ dashboard?.enabled_sources ?? "-" }}</div>
        <div class="label">启用信息源</div>
      </div>
      <div class="metric-card">
        <div class="value">{{ dashboard?.failed_sources ?? "-" }}</div>
        <div class="label">有失败记录的源</div>
      </div>
    </div>

    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>{{ dashboard?.selected_date || dashboard?.today || "今日" }} 新闻</h2>
          <p class="muted">
            每日自动抓取：北京时间 {{ dashboard?.daily_crawl_time || "10:00" }} · 最近自动抓取：
            {{ dashboard?.last_auto_crawl_date || "尚未记录" }}
          </p>
        </div>
        <div class="toolbar">
          <el-button
            :disabled="!weekStatus || weekStatus.missing_dates.length === 0 || progress.running || weeklyCrawlLoading"
            :loading="weeklyCrawlLoading"
            @click="backfillWeek"
          >
            补充本周数据
          </el-button>
          <el-button type="primary" :loading="crawlLoading || progress.running" @click="runCrawl">手动抓取</el-button>
        </div>
      </div>

      <el-progress
        v-if="progress.running || progress.status"
        :percentage="progress.percent || 0"
        :status="progress.status === 'failed' ? 'exception' : undefined"
      />
      <p v-if="progress.message" class="muted">
        {{ progress.message }} · {{ progress.completed_sources || 0 }}/{{ progress.total_sources || 0 }} 个源 · 新增
        {{ progress.new_items || 0 }} 条，失败 {{ progress.failed_items || 0 }} 项
      </p>

      <div v-if="weekStatus" class="week-crawl-status">
        <p class="muted">
          本周抓取：{{ weekStatus.week_start }} 至 {{ weekStatus.today }} ·
          {{ weekStatus.missing_dates.length ? `待补 ${weekStatus.missing_dates.join("、")}` : "已补齐" }}
        </p>
        <div class="week-days">
          <el-tag
            v-for="day in weekStatus.days"
            :key="day.date"
            :type="day.crawled ? 'success' : 'warning'"
            effect="plain"
          >
            {{ day.date }} · {{ day.crawled ? "已抓取" : "未抓取" }} · {{ day.content_count }} 条
          </el-tag>
        </div>
      </div>
    </section>

    <section v-for="group in dashboard?.source_groups || []" :key="group.source.source_id" class="panel">
      <div class="panel-header">
        <div>
          <h3>{{ group.source.source_name }}</h3>
          <p class="muted">{{ group.source.source_category }} · {{ group.total_count }} 条当日内容</p>
        </div>
        <el-tag>{{ group.page }} / {{ group.total_pages }}</el-tag>
      </div>

      <el-table :data="group.content_items" empty-text="暂无内容" stripe>
        <el-table-column width="70" label="#">
          <template #default="{ $index }">{{ group.page_offset + $index + 1 }}</template>
        </el-table-column>
        <el-table-column label="标题" min-width="260">
          <template #default="{ row }">
            <RouterLink class="item-title" :to="`/content/${row.content_id}`" @click="saveDashboardReturnState">
              {{ row.title }}
            </RouterLink>
            <p class="muted">{{ row.summary || "暂无 summary" }}</p>
          </template>
        </el-table-column>
        <el-table-column prop="display_time" label="时间" width="170" />
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-tag :type="row.llm_status === 'success' ? 'success' : 'warning'">LLM {{ row.llm_status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="原文" width="90">
          <template #default="{ row }"><a :href="row.url" target="_blank">打开</a></template>
        </el-table-column>
      </el-table>

      <div v-if="group.total_pages > 1" class="source-pagination">
        <el-pagination
          background
          layout="prev, pager, next, jumper, total"
          :current-page="group.page"
          :page-size="group.per_page"
          :total="group.total_count"
          @current-change="(page) => changeSourcePage(group.source.source_id, page)"
        />
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";

import { api, notifyError } from "../api/client";

const loading = ref(false);
const crawlLoading = ref(false);
const weeklyCrawlLoading = ref(false);
const dashboard = ref<any>(null);
const progress = ref<any>({});
const sourcePages = ref<Record<number, number>>({});
const weekStatus = ref<WeekStatus | null>(null);
let timer: number | undefined;
const route = useRoute();
const router = useRouter();
const selectedDate = ref(validDateString(route.query.date) ? String(route.query.date) : "");
const currentDate = ref("");

const DASHBOARD_RETURN_STATE_KEY = "vc-news-agent-ai-dashboard-return-state";
const DASHBOARD_RESTORE_KEY = "vc-news-agent-ai-dashboard-restore";

type DashboardReturnState = {
  scrollY: number;
  sourcePages: Record<string, number>;
  selectedDate?: string;
};

type WeekDayStatus = {
  date: string;
  crawled: boolean;
  has_summary: boolean;
  content_count: number;
  run_status: string;
  run_started_at: string;
  run_finished_at: string;
  new_items: number;
  failed_items: number;
  message: string;
};

type WeekStatus = {
  week_start: string;
  today: string;
  days: WeekDayStatus[];
  missing_dates: string[];
};

function readDashboardReturnState(): DashboardReturnState | null {
  try {
    const raw = sessionStorage.getItem(DASHBOARD_RETURN_STATE_KEY);
    return raw ? (JSON.parse(raw) as DashboardReturnState) : null;
  } catch {
    return null;
  }
}

function shouldRestoreDashboard() {
  return sessionStorage.getItem(DASHBOARD_RESTORE_KEY) === "1";
}

function applySavedSourcePages(state: DashboardReturnState | null) {
  if (!state?.sourcePages) return;
  sourcePages.value = Object.fromEntries(
    Object.entries(state.sourcePages)
      .map(([sourceId, page]) => [Number(sourceId), Number(page)])
      .filter(([sourceId, page]) => Number.isFinite(sourceId) && Number.isFinite(page) && page > 0),
  );
}

function applySavedDate(state: DashboardReturnState | null) {
  if (!selectedDate.value && validDateString(state?.selectedDate)) {
    selectedDate.value = state?.selectedDate || "";
  }
}

async function restoreScroll(scrollY: number | undefined) {
  if (!Number.isFinite(scrollY)) return;
  await nextTick();
  window.requestAnimationFrame(() => {
    window.scrollTo({ top: scrollY, behavior: "auto" });
  });
}

function saveDashboardReturnState() {
  sessionStorage.setItem(
    DASHBOARD_RETURN_STATE_KEY,
    JSON.stringify({
      scrollY: window.scrollY,
      sourcePages: sourcePages.value,
      selectedDate: selectedDate.value,
    }),
  );
}

function dashboardPath() {
  const params = new URLSearchParams();
  if (selectedDate.value) {
    params.set("date", selectedDate.value);
  }
  for (const [sourceId, page] of Object.entries(sourcePages.value)) {
    if (page > 1) {
      params.set(`source_${sourceId}_page`, String(page));
    }
  }
  const query = params.toString();
  return query ? `/dashboard?${query}` : "/dashboard";
}

async function load() {
  loading.value = true;
  try {
    const [payload, weeklyPayload]: any[] = await Promise.all([
      api.get(dashboardPath()),
      api.get("/crawl/week-status"),
    ]);
    dashboard.value = payload;
    weekStatus.value = weeklyPayload;
    selectedDate.value = payload.selected_date || payload.today || selectedDate.value;
    currentDate.value = payload.current_date || weeklyPayload.today || selectedDate.value;
    for (const group of payload.source_groups || []) {
      sourcePages.value[group.source.source_id] = group.page || 1;
    }
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}


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

const isCurrentDate = computed(() => Boolean(currentDate.value && selectedDate.value >= currentDate.value));

function disableFutureDate(value: Date): boolean {
  if (!currentDate.value) return false;
  return formatDate(value) > currentDate.value;
}

async function updateSelectedDate(value: string) {
  if (!validDateString(value) || (currentDate.value && value > currentDate.value)) return;
  selectedDate.value = value;
  sourcePages.value = {};
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

async function changeSourcePage(sourceId: number, page: number) {
  sourcePages.value[sourceId] = page;
  await load();
}

async function pollProgress(reloadOnFinish = false) {
  try {
    progress.value = await api.get("/crawl/status");
    if (progress.value.running) {
      timer = window.setTimeout(() => pollProgress(reloadOnFinish), 1000);
    } else if (reloadOnFinish && ["success", "partial_success"].includes(progress.value.status)) {
      await load();
      ElMessage.success("抓取完成");
    }
  } catch (error) {
    notifyError(error);
  }
}

async function runCrawl() {
  crawlLoading.value = true;
  try {
    const result: any = await api.post("/crawl/run");
    progress.value = result.progress || {};
    await pollProgress(true);
  } catch (error) {
    notifyError(error);
  } finally {
    crawlLoading.value = false;
  }
}

async function backfillWeek() {
  weeklyCrawlLoading.value = true;
  try {
    const result: any = await api.post("/crawl/week-backfill");
    progress.value = result.progress || progress.value || {};
    if (result.ok) {
      ElMessage.success(result.message || "本周数据补抓已开始");
      await pollProgress(true);
    } else {
      ElMessage.warning(result.message || "本周数据补抓未启动");
      await load();
    }
  } catch (error) {
    notifyError(error);
  } finally {
    weeklyCrawlLoading.value = false;
  }
}

onMounted(async () => {
  const restoreState = readDashboardReturnState();
  const shouldRestore = shouldRestoreDashboard();
  if (shouldRestore) {
    applySavedSourcePages(restoreState);
    applySavedDate(restoreState);
  }
  await load();
  if (shouldRestore) {
    await restoreScroll(restoreState?.scrollY);
    sessionStorage.removeItem(DASHBOARD_RESTORE_KEY);
  }
  await pollProgress(false);
});

onUnmounted(() => {
  if (timer) window.clearTimeout(timer);
});
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

.week-crawl-status {
  margin-top: 16px;
}

.week-days {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
