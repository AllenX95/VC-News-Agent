<template>
  <section class="page-grid" v-loading="loading">
    <section class="panel">
      <div class="panel-header">
        <h2>内容库</h2>
        <el-button @click="load">刷新</el-button>
      </div>
      <div class="toolbar">
        <el-input v-model="filters.q" clearable placeholder="搜索标题、summary、来源、标签、实体" style="max-width: 340px" />
        <el-select v-model="filters.source_id" clearable placeholder="来源" style="width: 220px">
          <el-option v-for="source in sources" :key="source.source_id" :label="source.source_name" :value="source.source_id" />
        </el-select>
        <el-select v-model="filters.status" clearable placeholder="状态" style="width: 160px">
          <el-option v-for="item in ['new', 'partial', 'processed', 'failed', 'archived']" :key="item" :value="item" />
        </el-select>
        <el-checkbox v-model="filters.favorite">收藏</el-checkbox>
        <el-button type="primary" @click="load">筛选</el-button>
      </div>
    </section>

    <section class="panel">
      <el-table :data="contents" stripe>
        <el-table-column label="标题" min-width="260">
          <template #default="{ row }">
            <RouterLink class="item-title" :to="`/content/${row.content_id}`">{{ row.title }}</RouterLink>
            <p class="muted">{{ row.summary || "暂无 summary" }}</p>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="180">
          <template #default="{ row }">
            {{ row.source_name }}
            <div class="muted">{{ row.source_category }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="display_time" label="时间" width="170" />
        <el-table-column label="标签" width="220">
          <template #default="{ row }">
            <el-tag v-for="tag in row.tags" :key="tag.tag_key + tag.tag_value" class="tag-gap" size="small">
              {{ tag.tag_key }}:{{ tag.tag_value }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <el-tag :type="row.llm_status === 'success' ? 'success' : 'warning'">{{ row.llm_status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button size="small" @click="favorite(row)">{{ row.is_favorite ? "取消收藏" : "收藏" }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { RouterLink } from "vue-router";
import { api, notifyError } from "../api/client";

const loading = ref(false);
const contents = ref<any[]>([]);
const sources = ref<any[]>([]);
const filters = reactive<any>({ q: "", source_id: null, status: "", favorite: false });

async function load() {
  loading.value = true;
  try {
    const params = new URLSearchParams();
    if (filters.q) params.set("q", filters.q);
    if (filters.source_id) params.set("source_id", String(filters.source_id));
    if (filters.status) params.set("status", filters.status);
    if (filters.favorite) params.set("favorite", "true");
    const payload: any = await api.get(`/content?${params.toString()}`);
    contents.value = payload.contents;
    sources.value = payload.sources;
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

async function favorite(row: any) {
  try {
    const payload: any = await api.post(`/content/${row.content_id}/favorite`);
    row.is_favorite = payload.is_favorite;
  } catch (error) {
    notifyError(error);
  }
}

onMounted(load);
</script>

<style scoped>
.tag-gap {
  margin: 0 4px 4px 0;
}
</style>
