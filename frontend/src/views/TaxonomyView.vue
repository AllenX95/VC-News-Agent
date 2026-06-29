<template>
  <section class="page-grid" v-loading="loading">
    <section class="panel">
      <div class="panel-header">
        <h2>标签</h2>
        <div class="toolbar">
          <el-input v-model="tagForm.tag_key" placeholder="tag_key" style="width: 180px" />
          <el-input v-model="tagForm.tag_value" placeholder="tag_value" style="width: 220px" />
          <el-button type="primary" @click="createTag">新增</el-button>
        </div>
      </div>
      <el-table :data="tags" stripe>
        <el-table-column prop="tag_key" label="Key" />
        <el-table-column prop="tag_value" label="Value" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? "启用" : "停用" }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button size="small" @click="toggleTag(row)">{{ row.enabled ? "停用" : "启用" }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>实体</h2>
        <el-button @click="load">刷新</el-button>
      </div>
      <el-table :data="entities" stripe>
        <el-table-column label="类型" width="170">
          <template #default="{ row }"><el-input v-model="row.entity_type" /></template>
        </el-table-column>
        <el-table-column label="标准名" min-width="220">
          <template #default="{ row }"><el-input v-model="row.canonical_name" /></template>
        </el-table-column>
        <el-table-column label="别名" min-width="220">
          <template #default="{ row }"><el-input v-model="row.aliases" /></template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }"><el-button size="small" type="primary" @click="saveEntity(row)">保存</el-button></template>
        </el-table-column>
      </el-table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { api, notifyError } from "../api/client";

const loading = ref(false);
const tags = ref<any[]>([]);
const entities = ref<any[]>([]);
const tagForm = reactive({ tag_key: "", tag_value: "" });

async function load() {
  loading.value = true;
  try {
    const payload: any = await api.get("/taxonomy");
    tags.value = payload.tags;
    entities.value = payload.entities;
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

async function createTag() {
  try {
    await api.post("/taxonomy/tags", tagForm);
    tagForm.tag_key = "";
    tagForm.tag_value = "";
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function toggleTag(row: any) {
  try {
    await api.post(`/taxonomy/tags/${row.tag_id}/toggle`);
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function saveEntity(row: any) {
  try {
    await api.patch(`/taxonomy/entities/${row.entity_id}`, row);
    ElMessage.success("已保存实体");
  } catch (error) {
    notifyError(error);
  }
}

onMounted(load);
</script>
