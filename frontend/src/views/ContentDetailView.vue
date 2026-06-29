<template>
  <section class="page-grid" v-loading="loading">
    <section v-if="content" class="panel">
      <div class="panel-header">
        <div>
          <h2>{{ content.title }}</h2>
          <p class="muted">{{ content.source_name }} / {{ content.source_category }} / {{ content.display_time }}</p>
        </div>
        <div class="toolbar">
          <el-button @click="returnToDashboard">返回今日概览</el-button>
          <el-button @click="openOriginal">打开原文</el-button>
          <el-button @click="toggleFavorite">{{ content.is_favorite ? "取消收藏" : "收藏" }}</el-button>
          <el-button type="primary" @click="archive">手动归档</el-button>
          <el-button @click="recrawl">重抓来源</el-button>
        </div>
      </div>
      <div class="toolbar">
        <el-tag>{{ content.extraction_status }}</el-tag>
        <el-tag :type="content.llm_status === 'success' ? 'success' : 'warning'">LLM {{ content.llm_status }}</el-tag>
        <el-tag v-if="content.full_content_saved" type="success">已归档</el-tag>
        <el-tag v-if="content.is_favorite" type="success">已收藏</el-tag>
      </div>
    </section>

    <section v-if="content" class="panel">
      <div class="panel-header"><h3>编辑内容</h3></div>
      <el-form label-position="top">
        <el-form-item label="标题"><el-input v-model="editForm.title" /></el-form-item>
        <el-form-item label="Summary"><el-input v-model="editForm.summary" type="textarea" :rows="4" /></el-form-item>
        <el-form-item label="处理状态">
          <el-select v-model="editForm.extraction_status">
            <el-option v-for="item in ['new', 'partial', 'processed', 'failed', 'archived']" :key="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-button type="primary" @click="save">保存编辑</el-button>
      </el-form>
    </section>

    <section v-if="content" class="panel">
      <div class="panel-header"><h3>标签与实体</h3></div>
      <div class="toolbar">
        <el-tag v-for="tag in content.tags" :key="tag.tag_key + tag.tag_value">{{ tag.tag_key }}:{{ tag.tag_value }}</el-tag>
        <el-tag v-for="entity in content.entities" :key="entity.content_entity_id" type="success">
          {{ entity.entity_type }}:{{ entity.display_name || entity.canonical_name }}
        </el-tag>
      </div>
      <div class="form-grid" style="margin-top: 16px">
        <el-form label-position="top">
          <el-form-item label="标签 Key"><el-input v-model="tagForm.tag_key" placeholder="sector" /></el-form-item>
          <el-form-item label="标签 Value"><el-input v-model="tagForm.tag_value" placeholder="AI Agent" /></el-form-item>
          <el-button @click="addTag">添加标签</el-button>
        </el-form>
        <el-form label-position="top">
          <el-form-item label="实体类型">
            <el-select v-model="entityForm.entity_type">
              <el-option v-for="item in ['company', 'product', 'investor', 'person', 'org']" :key="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="实体名称"><el-input v-model="entityForm.entity_name" placeholder="OpenAI" /></el-form-item>
          <el-button @click="addEntity">添加实体</el-button>
        </el-form>
      </div>
    </section>

    <section v-if="content?.cache" class="panel">
      <div class="panel-header">
        <h3>缓存正文</h3>
        <el-tag>到期 {{ content.cache.expire_at }}</el-tag>
      </div>
      <pre class="summary-text">{{ content.cache.clean_text }}</pre>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, notifyError, openExternalUrl } from "../api/client";

const route = useRoute();
const router = useRouter();
const loading = ref(false);
const content = ref<any>(null);
const editForm = reactive<any>({ title: "", summary: "", extraction_status: "processed" });
const tagForm = reactive({ tag_key: "", tag_value: "" });
const entityForm = reactive({ entity_type: "company", entity_name: "" });
const DASHBOARD_RESTORE_KEY = "vc-news-agent-ai-dashboard-restore";

async function load() {
  loading.value = true;
  try {
    const payload: any = await api.get(`/content/${route.params.id}`);
    content.value = payload.content;
    Object.assign(editForm, {
      title: content.value.title,
      summary: content.value.summary || "",
      extraction_status: content.value.extraction_status,
    });
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

async function save() {
  try {
    await api.patch(`/content/${route.params.id}`, editForm);
    ElMessage.success("已保存");
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function openOriginal() {
  try {
    await openExternalUrl(content.value?.url || "");
  } catch (error) {
    notifyError(error);
  }
}

async function returnToDashboard() {
  sessionStorage.setItem(DASHBOARD_RESTORE_KEY, "1");
  await router.push("/");
}

async function toggleFavorite() {
  try {
    await api.post(`/content/${route.params.id}/favorite`);
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function archive() {
  try {
    const payload: any = await api.post(`/content/${route.params.id}/archive`);
    ElMessage.success(`已归档：${payload.archive_object_path}`);
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function recrawl() {
  try {
    const payload: any = await api.post(`/content/${route.params.id}/recrawl`);
    ElMessage.success(payload.message || "已重抓来源");
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function addTag() {
  try {
    await api.post(`/content/${route.params.id}/tags`, tagForm);
    tagForm.tag_key = "";
    tagForm.tag_value = "";
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function addEntity() {
  try {
    await api.post(`/content/${route.params.id}/entities`, entityForm);
    entityForm.entity_name = "";
    await load();
  } catch (error) {
    notifyError(error);
  }
}

onMounted(load);
</script>
