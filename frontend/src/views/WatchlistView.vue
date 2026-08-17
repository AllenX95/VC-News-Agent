<template>
  <section class="page-grid" v-loading="loading">
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>关注列表</h2>
          <p class="muted">只记录你主动加入的事件或内容，不自动发现、不自动改状态。</p>
        </div>
        <el-button @click="load">刷新</el-button>
      </div>
      <div class="toolbar">
        <el-select v-model="filters.status" clearable placeholder="状态" style="width: 150px">
          <el-option label="持续关注" value="watching" />
          <el-option label="需要跟进" value="follow_up" />
          <el-option label="已暂停" value="paused" />
          <el-option label="已完成" value="completed" />
        </el-select>
        <el-select v-model="filters.priority" clearable placeholder="优先级" style="width: 140px">
          <el-option label="高" value="high" />
          <el-option label="中" value="medium" />
          <el-option label="低" value="low" />
        </el-select>
        <el-select v-model="filters.target_type" clearable placeholder="对象类型" style="width: 160px">
          <el-option label="融资事件" value="financing_event" />
          <el-option label="单篇内容" value="content" />
        </el-select>
        <el-button type="primary" @click="load">筛选</el-button>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header"><h3>待跟进事项</h3><span class="muted">{{ total }} 条</span></div>
      <el-empty v-if="!items.length" description="还没有关注项" />
      <div v-for="item in items" :key="item.watch_id" class="watch-row" :class="{ due: item.is_due }">
        <div class="watch-main">
          <div class="watch-title-line">
            <RouterLink v-if="item.target_type === 'content'" class="item-title" :to="`/content/${item.target_id}`">{{ item.target_title_snapshot }}</RouterLink>
            <RouterLink v-else class="item-title" to="/financing-events">{{ item.target_title_snapshot }}</RouterLink>
            <el-tag v-if="item.is_due" type="danger">今日待跟进</el-tag>
            <el-tag :type="priorityType(item.priority)">{{ priorityLabel(item.priority) }}</el-tag>
            <el-tag>{{ statusLabel(item.status) }}</el-tag>
            <el-tag v-if="!item.target_available" type="info">原对象不可用</el-tag>
          </div>
          <p class="summary-text muted">{{ item.target_summary_snapshot || "暂无摘要" }}</p>
          <p v-if="item.reason" class="muted">关注原因：{{ item.reason }}</p>
        </div>
        <div class="watch-controls">
          <el-select v-model="item.priority" size="small" style="width: 90px" @change="save(item)">
            <el-option label="高" value="high" /><el-option label="中" value="medium" /><el-option label="低" value="low" />
          </el-select>
          <el-select v-model="item.status" size="small" style="width: 110px" @change="save(item)">
            <el-option label="持续关注" value="watching" /><el-option label="需要跟进" value="follow_up" /><el-option label="已暂停" value="paused" /><el-option label="已完成" value="completed" />
          </el-select>
          <el-date-picker v-model="item.next_review_date" size="small" type="date" value-format="YYYY-MM-DD" placeholder="回看日期" @change="save(item)" />
          <el-button size="small" @click="editNotes(item)">备注</el-button>
          <el-button size="small" type="danger" plain @click="remove(item)">删除</el-button>
        </div>
      </div>
      <el-pagination
        v-if="total > pageSize"
        class="watch-pagination"
        background
        layout="prev, pager, next"
        :page-size="pageSize"
        :current-page="currentPage"
        :total="total"
        @current-change="changePage"
      />
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { RouterLink } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";

import { api, notifyError } from "../api/client";
import type { WatchItem } from "../api/types";

const loading = ref(false);
const items = ref<WatchItem[]>([]);
const total = ref(0);
const currentPage = ref(1);
const pageSize = 30;
const filters = reactive({ status: "", priority: "", target_type: "" });

async function load() {
  loading.value = true;
  try {
    const params = new URLSearchParams({ limit: String(pageSize), offset: String((currentPage.value - 1) * pageSize) });
    for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
    const payload = await api.get<{ items: WatchItem[]; total: number }>(`/watch-items?${params.toString()}`);
    items.value = payload.items;
    total.value = payload.total;
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

async function save(item: WatchItem) {
  try {
    const payload = await api.patch<{ item: WatchItem }>(`/watch-items/${item.watch_id}`, {
      priority: item.priority,
      status: item.status,
      next_review_date: item.next_review_date,
      reason: item.reason,
      notes: item.notes,
    });
    Object.assign(item, payload.item);
    ElMessage.success("关注项已更新");
  } catch (error) {
    notifyError(error);
    await load();
  }
}

async function editNotes(item: WatchItem) {
  try {
    const result = await ElMessageBox.prompt("更新备注", "关注备注", { inputValue: item.notes || "", inputType: "textarea" });
    item.notes = result.value;
    await save(item);
  } catch (error) {
    if (error !== "cancel" && error !== "close") notifyError(error);
  }
}

async function remove(item: WatchItem) {
  try {
    await ElMessageBox.confirm("删除后关注项快照也会被移除，确认继续？", "删除关注项", { type: "warning" });
    await api.delete(`/watch-items/${item.watch_id}`);
    ElMessage.success("关注项已删除");
    await load();
  } catch (error) {
    if (error !== "cancel" && error !== "close") notifyError(error);
  }
}

function changePage(value: number) {
  currentPage.value = value;
  load();
}

function priorityType(priority: string) {
  return { high: "danger", medium: "warning", low: "info" }[priority] || "info";
}

function priorityLabel(priority: string) {
  return { high: "高优先级", medium: "中优先级", low: "低优先级" }[priority] || priority;
}

function statusLabel(status: string) {
  return { watching: "持续关注", follow_up: "需要跟进", paused: "已暂停", completed: "已完成" }[status] || status;
}

onMounted(load);
</script>

<style scoped>
.watch-row { align-items: flex-start; border-bottom: 1px solid #e5ebf2; display: flex; gap: 18px; justify-content: space-between; padding: 18px 0; }
.watch-row:last-child { border-bottom: 0; }
.watch-row.due { border-left: 3px solid #ef4444; padding-left: 12px; }
.watch-main { min-width: 0; }
.watch-title-line, .watch-controls { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; }
.watch-controls { flex-shrink: 0; justify-content: flex-end; max-width: 460px; }
.watch-pagination { justify-content: flex-end; margin-top: 16px; }
@media (max-width: 900px) { .watch-row { display: block; } .watch-controls { justify-content: flex-start; margin-top: 12px; max-width: none; } }
</style>
