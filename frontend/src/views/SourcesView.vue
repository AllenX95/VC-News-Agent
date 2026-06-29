<template>
  <section class="page-grid" v-loading="loading">
    <section class="panel">
      <div class="panel-header">
        <h2>{{ editingId ? "编辑信息源" : "新增信息源" }}</h2>
        <el-button v-if="editingId" @click="resetForm">取消编辑</el-button>
      </div>
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item label="名称"><el-input v-model="form.source_name" /></el-form-item>
          <el-form-item label="类别"><el-input v-model="form.source_category" /></el-form-item>
          <el-form-item class="full" label="URL"><el-input v-model="form.source_url" /></el-form-item>
          <el-form-item label="抓取方式">
            <el-select v-model="form.access_method">
              <el-option v-for="item in ['auto', 'api', 'http', 'browser']" :key="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="优先级">
            <el-select v-model="form.priority">
              <el-option v-for="item in ['P0', 'P1', 'P2']" :key="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="抓取风险">
            <el-select v-model="form.crawl_risk">
              <el-option v-for="item in ['low', 'medium', 'high']" :key="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="列表扫描上限"><el-input-number v-model="form.list_page_limit" :min="1" /></el-form-item>
          <el-form-item label="入库上限"><el-input-number v-model="form.item_limit_per_run" :min="1" /></el-form-item>
          <el-form-item label="超时秒数"><el-input-number v-model="form.timeout_seconds" :min="1" /></el-form-item>
          <el-form-item label="开关">
            <el-checkbox v-model="form.enabled">启用</el-checkbox>
            <el-checkbox v-model="form.requires_js">需要 JS 渲染</el-checkbox>
          </el-form-item>
        </div>
        <el-button type="primary" @click="saveSource">{{ editingId ? "保存修改" : "新增信息源" }}</el-button>
      </el-form>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>信息源列表</h2>
        <el-button @click="load">刷新</el-button>
      </div>
      <el-table :data="sources" stripe>
        <el-table-column prop="source_name" label="名称" min-width="180">
          <template #default="{ row }"><a :href="row.source_url" target="_blank">{{ row.source_name }}</a></template>
        </el-table-column>
        <el-table-column prop="source_category" label="类别" width="170" />
        <el-table-column prop="access_method" label="方式" width="90" />
        <el-table-column prop="priority" label="优先级" width="80" />
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? "启用" : "停用" }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="error_count" label="错误" width="80" />
        <el-table-column prop="last_success_at" label="最近成功" width="170" />
        <el-table-column label="操作" width="330" fixed="right">
          <template #default="{ row }">
            <div class="toolbar">
              <el-button size="small" @click="edit(row)">编辑</el-button>
              <el-button size="small" @click="toggle(row)">{{ row.enabled ? "停用" : "启用" }}</el-button>
              <el-button size="small" type="primary" @click="crawl(row)">测试抓取</el-button>
              <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, notifyError } from "../api/client";

const loading = ref(false);
const sources = ref<any[]>([]);
const editingId = ref<number | null>(null);
const form = reactive<any>({
  source_name: "",
  source_category: "ai_media",
  source_url: "",
  access_method: "auto",
  priority: "P0",
  crawl_risk: "low",
  list_page_limit: 50,
  item_limit_per_run: 20,
  timeout_seconds: 25,
  enabled: true,
  requires_js: false,
});

function resetForm() {
  editingId.value = null;
  Object.assign(form, {
    source_name: "",
    source_category: "ai_media",
    source_url: "",
    access_method: "auto",
    priority: "P0",
    crawl_risk: "low",
    list_page_limit: 50,
    item_limit_per_run: 20,
    timeout_seconds: 25,
    enabled: true,
    requires_js: false,
  });
}

async function load() {
  loading.value = true;
  try {
    const payload: any = await api.get("/sources");
    sources.value = payload.sources;
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

function edit(row: any) {
  editingId.value = row.source_id;
  Object.assign(form, row);
}

async function saveSource() {
  try {
    if (editingId.value) {
      await api.patch(`/sources/${editingId.value}`, form);
      ElMessage.success("已保存信息源");
    } else {
      await api.post("/sources", form);
      ElMessage.success("已新增信息源");
    }
    resetForm();
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function toggle(row: any) {
  try {
    await api.post(`/sources/${row.source_id}/toggle`);
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function crawl(row: any) {
  try {
    const result: any = await api.post(`/sources/${row.source_id}/crawl`);
    ElMessage.success(result.message || result.run?.message || "抓取完成");
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function remove(row: any) {
  try {
    await ElMessageBox.confirm("有历史内容的信息源会被停用而不是物理删除。确认继续？", "删除信息源");
    const result: any = await api.delete(`/sources/${row.source_id}`);
    ElMessage.success(result.action === "deleted" ? "已删除" : "已有历史内容，已停用");
    await load();
  } catch (error) {
    if (error !== "cancel") notifyError(error);
  }
}

onMounted(load);
</script>
