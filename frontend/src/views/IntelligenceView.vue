<template>
  <section class="page-grid" v-loading="loading">
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>情报收件箱</h2>
          <div class="muted">先处理高相关内容，再将重要事件加入后续跟踪。</div>
        </div>
        <div class="toolbar">
          <el-button @click="load">刷新</el-button>
          <el-button type="primary" :loading="taskLoading" @click="loadJobs">查看抓取任务</el-button>
        </div>
      </div>
      <div class="toolbar">
        <el-input v-model="filters.q" clearable placeholder="搜索标题、摘要或来源" style="max-width: 300px" @keyup.enter="load" />
        <el-date-picker v-model="filters.date" type="date" value-format="YYYY-MM-DD" placeholder="日期" clearable />
        <el-select v-model="filters.status" clearable placeholder="处理状态" style="width: 150px">
          <el-option label="待处理" value="unread" />
          <el-option label="已复核" value="reviewed" />
          <el-option label="待跟进" value="follow_up" />
          <el-option label="已忽略" value="ignored" />
        </el-select>
        <el-input-number v-model="filters.minScore" :min="0" :max="100" :step="10" controls-position="right" placeholder="最低分" />
        <el-button type="primary" @click="load">筛选</el-button>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h3>待处理内容</h3>
        <span class="muted">{{ page.total }} 条结果</span>
      </div>
      <el-empty v-if="!items.length" description="当前筛选条件下没有内容" />
      <div v-for="item in items" :key="item.content_id" class="intelligence-row">
        <div class="intelligence-main">
          <div class="intelligence-title-line">
            <RouterLink class="item-title" :to="`/content/${item.content_id}`">{{ item.title }}</RouterLink>
            <el-tag :type="scoreType(item.relevance_score)" effect="dark">{{ item.relevance_score }} 分</el-tag>
            <el-tag :type="statusType(item.review_status)">{{ statusLabel(item.review_status) }}</el-tag>
          </div>
          <p class="summary-text muted">{{ item.summary || "暂无摘要" }}</p>
          <div class="intelligence-meta muted">
            <span>{{ item.source_name }}</span>
            <span>{{ item.publish_time || item.crawl_time || "时间未知" }}</span>
            <span>置信度 {{ Math.round(item.relevance_confidence * 100) }}%</span>
          </div>
          <div class="reason-list">
            <el-tag v-for="reason in item.relevance_reasons" :key="reason" size="small" effect="plain">{{ reason }}</el-tag>
          </div>
          <div v-if="item.review_note" class="review-note">复核备注：{{ item.review_note }}</div>
        </div>
        <div class="intelligence-actions">
          <el-button size="small" type="success" @click="review(item, 'relevant')">相关</el-button>
          <el-button size="small" type="warning" @click="review(item, 'follow_up')">待跟进</el-button>
          <el-button size="small" type="info" @click="review(item, 'not_relevant')">忽略</el-button>
          <el-button size="small" @click="reprocess(item)">重算</el-button>
          <el-button size="small" link @click="openOriginal(item.url)">原文</el-button>
        </div>
      </div>
      <el-pagination
        v-if="page.total > page.limit"
        class="intelligence-pagination"
        background
        layout="prev, pager, next"
        :page-size="page.limit"
        :current-page="currentPage"
        :total="page.total"
        @current-change="changePage"
      />
    </section>

    <section v-if="jobs.length" class="panel">
      <div class="panel-header">
        <h3>最近抓取任务</h3>
        <el-button link @click="jobs = []">收起</el-button>
      </div>
      <el-table :data="jobs" stripe>
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column prop="status" label="状态" width="120" />
        <el-table-column label="来源进度" width="150">
          <template #default="{ row }">{{ row.succeeded_sources }} / {{ row.total_sources }}</template>
        </el-table-column>
        <el-table-column prop="message" label="说明" min-width="260" />
      </el-table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { RouterLink } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";

import { api, notifyError, openExternalUrl } from "../api/client";
import type { CrawlJob, IntelligenceItem, IntelligencePage, IntelligenceReviewStatus } from "../api/types";

const loading = ref(false);
const taskLoading = ref(false);
const reprocessingAll = ref(false);
const items = ref<IntelligenceItem[]>([]);
const jobs = ref<CrawlJob[]>([]);
const currentPage = ref(1);
const filters = reactive({ q: "", date: "", status: "", minScore: null as number | null });
const page = ref<IntelligencePage>({
  items: [],
  total: 0,
  limit: 30,
  offset: 0,
  filters: { query: "", status: "", minimum_score: null, date: null },
});

function queryString() {
  const params = new URLSearchParams({ limit: String(page.value.limit), offset: String((currentPage.value - 1) * page.value.limit) });
  if (filters.q) params.set("q", filters.q);
  if (filters.date) params.set("date", filters.date);
  if (filters.status) params.set("status", filters.status);
  if (filters.minScore !== null) params.set("min_score", String(filters.minScore));
  return params.toString();
}

async function load() {
  loading.value = true;
  try {
    const payload = await api.get<IntelligencePage>(`/intelligence?${queryString()}`);
    page.value = payload;
    items.value = payload.items;
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

async function review(item: IntelligenceItem, decision: string) {
  let note: string | undefined;
  if (decision === "not_relevant" || decision === "follow_up") {
    try {
      note = await ElMessageBox.prompt("可选：记录这次判断的原因", "复核备注", { inputPlaceholder: "例如：公司主体不明确" }).then((result) => result.value);
    } catch {
      return;
    }
  }
  try {
    const payload = await api.post<{ ok: boolean; item: IntelligenceItem }>(`/intelligence/${item.content_id}/review`, { decision, note });
    Object.assign(item, payload.item);
    ElMessage.success("复核结果已保存");
    if (filters.status && item.review_status !== filters.status) {
      await load();
    }
  } catch (error) {
    notifyError(error);
  }
}

async function reprocess(item: IntelligenceItem) {
  reprocessingAll.value = true;
  try {
    const payload = await api.post<{ ok: boolean; item: IntelligenceItem }>(`/intelligence/${item.content_id}/reprocess`);
    Object.assign(item, payload.item);
    ElMessage.success("评分已重新计算");
  } catch (error) {
    notifyError(error);
  } finally {
    reprocessingAll.value = false;
  }
}

async function loadJobs() {
  taskLoading.value = true;
  try {
    const payload = await api.get<{ jobs: CrawlJob[] }>("/crawl/jobs");
    jobs.value = payload.jobs;
  } catch (error) {
    notifyError(error);
  } finally {
    taskLoading.value = false;
  }
}

function changePage(value: number) {
  currentPage.value = value;
  load();
}

function openOriginal(url: string) {
  openExternalUrl(url).catch(notifyError);
}

function scoreType(score: number) {
  if (score >= 80) return "danger";
  if (score >= 60) return "warning";
  return "info";
}

function statusType(status: IntelligenceReviewStatus) {
  return { unread: "warning", reviewed: "success", follow_up: "danger", ignored: "info", archived: "info" }[status] || "info";
}

function statusLabel(status: IntelligenceReviewStatus) {
  return { unread: "待处理", reviewed: "已复核", follow_up: "待跟进", ignored: "已忽略", archived: "已归档" }[status] || status;
}

onMounted(load);
</script>

<style scoped>
.intelligence-row {
  align-items: flex-start;
  border-bottom: 1px solid #e5ebf2;
  display: flex;
  gap: 18px;
  justify-content: space-between;
  padding: 18px 0;
}

.intelligence-row:last-child {
  border-bottom: 0;
}

.intelligence-main {
  min-width: 0;
}

.intelligence-title-line,
.intelligence-meta,
.reason-list,
.intelligence-actions {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.intelligence-title-line .item-title {
  margin-right: 4px;
}

.intelligence-meta {
  font-size: 13px;
  margin-top: 8px;
}

.reason-list {
  margin-top: 10px;
}

.review-note {
  color: #8a5b00;
  font-size: 13px;
  margin-top: 10px;
}

.intelligence-actions {
  flex-shrink: 0;
  justify-content: flex-end;
  max-width: 260px;
}

.intelligence-pagination {
  justify-content: flex-end;
  margin-top: 16px;
}

@media (max-width: 900px) {
  .intelligence-row {
    display: block;
  }

  .intelligence-actions {
    justify-content: flex-start;
    margin-top: 14px;
    max-width: none;
  }
}
</style>
