<template>
  <section class="page-grid" v-loading="loading">
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>融资事件</h2>
          <p class="muted">将同一笔融资的多篇报道合并为一个可确认、可追溯的事件。</p>
        </div>
        <div class="toolbar">
          <el-button :loading="building" @click="build">聚合最近 30 天</el-button>
          <el-button @click="load">刷新</el-button>
        </div>
      </div>
      <div class="toolbar">
        <el-input v-model="filters.company" clearable placeholder="公司名称" style="width: 210px" @keyup.enter="load" />
        <el-select v-model="filters.status" clearable placeholder="审核状态" style="width: 150px">
          <el-option label="待确认" value="pending" />
          <el-option label="已确认" value="confirmed" />
          <el-option label="已排除" value="excluded" />
        </el-select>
        <el-date-picker v-model="filters.start_date" type="date" value-format="YYYY-MM-DD" placeholder="开始日期" />
        <el-date-picker v-model="filters.end_date" type="date" value-format="YYYY-MM-DD" placeholder="结束日期" />
        <el-input-number v-model="filters.min_confidence" :min="0" :max="1" :step="0.1" :precision="1" placeholder="最低置信度" />
        <el-button type="primary" @click="load">筛选</el-button>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <div class="toolbar">
          <el-button :disabled="selectedEvents.length < 2" @click="merge">合并选中事件</el-button>
          <span class="muted">{{ page.total }} 个事件</span>
        </div>
        <el-pagination
          v-if="page.total > page.limit"
          background
          layout="prev, pager, next"
          :page-size="page.limit"
          :current-page="currentPage"
          :total="page.total"
          @current-change="changePage"
        />
      </div>

      <el-table :data="page.items" stripe @selection-change="(rows: FinancingEvent[]) => (selectedEvents = rows)">
        <el-table-column type="selection" width="48" />
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="event-detail">
              <p class="summary-text">{{ row.event_summary || "暂无摘要" }}</p>
              <div class="event-sources">
                <div v-for="source in row.sources" :key="source.event_content_id" class="event-source-row">
                  <div>
                    <RouterLink class="item-title" :to="`/content/${source.content_id}`">{{ source.title }}</RouterLink>
                    <div class="muted">{{ source.source_name }} · {{ source.publish_time || source.crawl_time || "时间未知" }}</div>
                    <div class="reason-list">
                      <el-tag v-for="reason in source.match_reasons" :key="reason" size="small" effect="plain">{{ reason }}</el-tag>
                    </div>
                  </div>
                  <div class="toolbar">
                    <el-tag v-if="source.is_primary_source" type="success">主要来源</el-tag>
                    <el-button v-else size="small" @click="setPrimary(row, source.content_id)">设为主要来源</el-button>
                    <el-button size="small" type="danger" plain @click="detach(row, source.content_id)">移除</el-button>
                  </div>
                </div>
              </div>
              <el-button size="small" @click="split(row)">从选定来源拆分事件</el-button>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="事件" min-width="300">
          <template #default="{ row }">
            <div class="item-title">{{ row.event_title }}</div>
            <div class="muted">{{ row.company_name }} · {{ row.announced_date || "日期未知" }}</div>
          </template>
        </el-table-column>
        <el-table-column label="轮次 / 金额" width="180">
          <template #default="{ row }">{{ row.financing_round || "-" }} · {{ row.amount_original || "金额未知" }}</template>
        </el-table-column>
        <el-table-column label="置信度" width="110">
          <template #default="{ row }"><el-tag :type="scoreType(row.confidence)">{{ Math.round(row.confidence * 100) }}%</el-tag></template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }"><el-tag :type="statusType(row.review_status)">{{ statusLabel(row.review_status) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="来源" width="80"><template #default="{ row }">{{ row.sources.length }}</template></el-table-column>
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <div class="toolbar compact-toolbar">
              <el-button size="small" type="success" :disabled="row.review_status === 'confirmed'" @click="review(row, 'confirmed')">确认</el-button>
              <el-button size="small" type="warning" :disabled="row.review_status === 'excluded'" @click="review(row, 'excluded')">排除</el-button>
              <el-button size="small" @click="startEdit(row)">编辑</el-button>
              <el-button size="small" type="primary" plain @click="addWatch(row)">关注</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section v-if="editingId !== null" class="panel">
      <div class="panel-header"><h3>编辑融资事件</h3><el-button @click="editingId = null">取消</el-button></div>
      <el-form label-position="top" class="form-grid">
        <el-form-item label="事件标题"><el-input v-model="editForm.event_title" /></el-form-item>
        <el-form-item label="公司名称"><el-input v-model="editForm.company_name" /></el-form-item>
        <el-form-item label="宣布日期"><el-date-picker v-model="editForm.announced_date" type="date" value-format="YYYY-MM-DD" /></el-form-item>
        <el-form-item label="融资轮次"><el-input v-model="editForm.financing_round" /></el-form-item>
        <el-form-item label="原文金额"><el-input v-model="editForm.amount_original" /></el-form-item>
        <el-form-item label="投资方（逗号分隔）"><el-input v-model="editForm.investors" /></el-form-item>
        <el-form-item label="事件摘要" class="full"><el-input v-model="editForm.event_summary" type="textarea" :rows="4" /></el-form-item>
        <div class="full"><el-button type="primary" @click="saveEdit">保存并锁定人工结果</el-button></div>
      </el-form>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { RouterLink } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";

import { api, notifyError } from "../api/client";
import type { FinancingEvent, FinancingEventPage } from "../api/types";

const loading = ref(false);
const building = ref(false);
const currentPage = ref(1);
const selectedEvents = ref<FinancingEvent[]>([]);
const editingId = ref<number | null>(null);
const page = ref<FinancingEventPage>({ items: [], total: 0, limit: 30, offset: 0 });
const filters = reactive({ company: "", status: "", start_date: "", end_date: "", min_confidence: null as number | null });
const editForm = reactive({ event_title: "", company_name: "", announced_date: "", financing_round: "", amount_original: "", investors: "", event_summary: "" });

function queryString() {
  const params = new URLSearchParams({ limit: String(page.value.limit), offset: String((currentPage.value - 1) * page.value.limit) });
  for (const [key, value] of Object.entries(filters)) {
    if (value !== "" && value !== null) params.set(key, String(value));
  }
  return params.toString();
}

async function load() {
  loading.value = true;
  try {
    page.value = await api.get<FinancingEventPage>(`/financing-events?${queryString()}`);
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

async function build() {
  building.value = true;
  try {
    const result = await api.post<{ created: number; attached: number; conflicts: number }>("/financing-events/build", { limit: 200 });
    ElMessage.success(`聚合完成：新建 ${result.created} 个，加入 ${result.attached} 条来源`);
    await load();
  } catch (error) {
    notifyError(error);
  } finally {
    building.value = false;
  }
}

async function review(row: FinancingEvent, status: "confirmed" | "excluded") {
  try {
    const result = await api.patch<{ event: FinancingEvent }>(`/financing-events/${row.event_id}`, { review_status: status, locked_by_user: true });
    Object.assign(row, result.event);
    ElMessage.success(status === "confirmed" ? "事件已确认并锁定" : "事件已排除并锁定");
  } catch (error) {
    notifyError(error);
  }
}

function startEdit(row: FinancingEvent) {
  editingId.value = row.event_id;
  Object.assign(editForm, {
    event_title: row.event_title,
    company_name: row.company_name,
    announced_date: row.announced_date || "",
    financing_round: row.financing_round || "",
    amount_original: row.amount_original || "",
    investors: row.investors.join(", "),
    event_summary: row.event_summary || "",
  });
}

async function saveEdit() {
  if (editingId.value === null) return;
  try {
    const result = await api.patch<{ event: FinancingEvent }>(`/financing-events/${editingId.value}`, {
      ...editForm,
      investors: editForm.investors.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
    });
    const row = page.value.items.find((item) => item.event_id === editingId.value);
    if (row) Object.assign(row, result.event);
    editingId.value = null;
    ElMessage.success("事件已保存并锁定");
  } catch (error) {
    notifyError(error);
  }
}

async function addWatch(row: FinancingEvent) {
  try {
    await api.post("/watch-items", { target_type: "financing_event", target_id: row.event_id, priority: "high", status: "watching", reason: "来自融资事件工作台" });
    ElMessage.success("已加入关注列表");
  } catch (error) {
    notifyError(error);
  }
}

async function setPrimary(row: FinancingEvent, contentId: number) {
  try {
    const result = await api.post<{ event: FinancingEvent }>("/financing-events/reorganize", { operation: "set_primary_source", target_event_id: row.event_id, content_id: contentId });
    Object.assign(row, result.event);
  } catch (error) {
    notifyError(error);
  }
}

async function detach(row: FinancingEvent, contentId: number) {
  try {
    await ElMessageBox.confirm("移除来源后原始内容仍会保留，确认继续？", "移除来源", { type: "warning" });
    const result = await api.post<{ event: FinancingEvent }>("/financing-events/reorganize", { operation: "detach_content", target_event_id: row.event_id, content_id: contentId });
    Object.assign(row, result.event);
  } catch (error) {
    if (error !== "cancel" && error !== "close") notifyError(error);
  }
}

async function split(row: FinancingEvent) {
  try {
    const result = await ElMessageBox.prompt("输入要拆出的 content_id，多个 ID 用逗号分隔", "拆分事件", { inputPlaceholder: row.sources.map((source) => source.content_id).join(", ") });
    const contentIds = result.value.split(/[,，]/).map((item) => Number(item.trim())).filter((item) => Number.isFinite(item));
    if (!contentIds.length) return;
    await api.post("/financing-events/reorganize", { operation: "split", source_event_id: row.event_id, content_ids: contentIds });
    ElMessage.success("已拆分为新事件");
    await load();
  } catch (error) {
    if (error !== "cancel" && error !== "close") notifyError(error);
  }
}

async function merge() {
  try {
    await api.post("/financing-events/reorganize", { operation: "merge", event_ids: selectedEvents.value.map((event) => event.event_id), target_event_id: selectedEvents.value[0].event_id });
    ElMessage.success("事件已合并，来源关系已保留");
    selectedEvents.value = [];
    await load();
  } catch (error) {
    notifyError(error);
  }
}

function changePage(value: number) {
  currentPage.value = value;
  load();
}

function scoreType(value: number) {
  if (value >= 0.8) return "success";
  if (value >= 0.6) return "warning";
  return "info";
}

function statusType(status: string) {
  return { pending: "warning", confirmed: "success", excluded: "info" }[status] || "info";
}

function statusLabel(status: string) {
  return { pending: "待确认", confirmed: "已确认", excluded: "已排除" }[status] || status;
}

onMounted(load);
</script>

<style scoped>
.event-detail { display: grid; gap: 14px; padding: 8px 28px; }
.event-sources { display: grid; gap: 10px; }
.event-source-row { align-items: flex-start; border-bottom: 1px solid #e5ebf2; display: flex; gap: 14px; justify-content: space-between; padding: 10px 0; }
.reason-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.compact-toolbar { gap: 4px; }
@media (max-width: 900px) { .event-source-row { display: block; } .event-source-row .toolbar { margin-top: 8px; } }
</style>
