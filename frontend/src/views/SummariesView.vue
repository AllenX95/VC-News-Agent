<template>
  <section class="page-grid" v-loading="loading">
    <section class="panel">
      <div class="panel-header">
        <h2>每日汇总</h2>
        <div class="toolbar">
          <el-date-picker
            v-model="selectedDate"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            :clearable="false"
          />
          <el-button @click="openSelectedSummary">查看所选日期</el-button>
          <el-button @click="load">刷新</el-button>
          <el-button type="primary" @click="generate">生成所选日期汇总</el-button>
        </div>
      </div>
      <el-table :data="summaries" stripe>
        <el-table-column prop="summary_date" label="日期" width="150">
          <template #default="{ row }">
            <RouterLink class="item-title" :to="`/summaries/${row.summary_date}`">{{ row.summary_date }}</RouterLink>
          </template>
        </el-table-column>
        <el-table-column prop="total_items" label="总条数" width="100" />
        <el-table-column prop="successful_items" label="成功" width="90" />
        <el-table-column prop="partial_items" label="Partial" width="90" />
        <el-table-column prop="failed_items" label="失败" width="90" />
        <el-table-column prop="llm_summary_status" label="LLM 汇总" width="130" />
        <el-table-column prop="generated_at" label="生成时间" />
      </el-table>
    </section>

    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>本周抓取</h2>
          <p v-if="weekStatus" class="muted">
            周期：{{ weekStatus.week_start }} 至 {{ weekStatus.today }}；缺失 {{ weekStatus.missing_dates.length }} 天
          </p>
        </div>
        <div class="toolbar">
          <el-button @click="load">刷新状态</el-button>
          <el-button
            type="primary"
            :disabled="!weekStatus || weekStatus.missing_dates.length === 0 || backfilling"
            :loading="backfilling"
            @click="backfillWeek"
          >
            抓取本周缺失日期
          </el-button>
        </div>
      </div>
      <div v-if="weekStatus" class="week-days">
        <el-tag
          v-for="day in weekStatus.days"
          :key="day.date"
          :type="day.crawled ? 'success' : 'warning'"
          effect="plain"
        >
          {{ day.date }} · {{ day.crawled ? "已抓取" : "未抓取" }} · {{ day.content_count }} 条
        </el-tag>
      </div>
      <p v-if="missingDatesText" class="muted">待补抓：{{ missingDatesText }}</p>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, notifyError } from "../api/client";

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

const router = useRouter();
const loading = ref(false);
const backfilling = ref(false);
const selectedDate = ref("");
const summaries = ref<any[]>([]);
const weekStatus = ref<WeekStatus | null>(null);

const missingDatesText = computed(() => (weekStatus.value?.missing_dates || []).join("、"));

function todayString() {
  const now = new Date();
  const year = now.getFullYear();
  const month = `${now.getMonth() + 1}`.padStart(2, "0");
  const day = `${now.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

async function load() {
  loading.value = true;
  try {
    const [summaryPayload, weekPayload]: any[] = await Promise.all([
      api.get("/summaries"),
      api.get("/crawl/week-status"),
    ]);
    summaries.value = summaryPayload.summaries;
    weekStatus.value = weekPayload;
    if (!selectedDate.value) {
      selectedDate.value = weekPayload.today || summaries.value[0]?.summary_date || todayString();
    }
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

async function generate() {
  loading.value = true;
  try {
    const targetDate = selectedDate.value || todayString();
    const payload: any = await api.post("/summaries/generate", { summary_date: targetDate });
    await router.push(`/summaries/${payload.summary.summary_date}`);
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

async function openSelectedSummary() {
  if (!selectedDate.value) {
    ElMessage.warning("请先选择日期");
    return;
  }
  await router.push(`/summaries/${selectedDate.value}`);
}

async function backfillWeek() {
  backfilling.value = true;
  try {
    const payload: any = await api.post("/crawl/week-backfill");
    if (payload.ok) {
      ElMessage.success(payload.message || "本周补抓已开始");
    } else {
      ElMessage.warning(payload.message || "本周补抓未启动");
    }
    await load();
  } catch (error) {
    notifyError(error);
  } finally {
    backfilling.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.week-days {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
