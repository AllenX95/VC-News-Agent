<template>
  <section class="page-grid" v-loading="loading">
    <section id="llm-config-editor" class="panel">
      <div class="panel-header">
        <div>
          <h2>LLM 配置</h2>
          <p class="muted">{{ editingConfigId ? "正在编辑已有配置，API Key 留空则沿用原值" : `待处理内容 ${data?.llm_pending_count || 0} 条` }}</p>
        </div>
        <el-button @click="load">刷新</el-button>
      </div>
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item label="配置名称"><el-input v-model="configForm.config_name" /></el-form-item>
          <el-form-item label="Provider">
            <el-select v-model="configForm.provider_type">
              <el-option label="OpenAI" value="openai" />
              <el-option label="Anthropic" value="anthropic" />
            </el-select>
          </el-form-item>
          <el-form-item label="Base URL"><el-input v-model="configForm.base_url" /></el-form-item>
          <el-form-item label="Model Name"><el-input v-model="configForm.model_name" /></el-form-item>
          <el-form-item class="full" label="API Key"><el-input v-model="configForm.api_key" type="password" show-password :placeholder="editingConfigId ? '留空则沿用原 API Key' : ''" /></el-form-item>
          <el-form-item label="Timeout"><el-input-number v-model="configForm.timeout_seconds" :min="1" /></el-form-item>
          <el-form-item label="Max Retries"><el-input-number v-model="configForm.max_retries" :min="0" /></el-form-item>
          <el-form-item label="Context Window Tokens"><el-input-number v-model="configForm.context_window_tokens" :min="1" :step="1000" /></el-form-item>
          <el-form-item label="启用"><el-switch v-model="configForm.enabled" /></el-form-item>
        </div>
        <div class="toolbar">
          <el-button type="primary" @click="saveConfig">{{ editingConfigId ? "更新 LLM 配置" : "保存 LLM 配置" }}</el-button>
          <el-button v-if="editingConfigId" @click="cancelConfigEdit">取消编辑</el-button>
        </div>
      </el-form>

      <el-table :data="data?.configs || []" stripe style="margin-top: 18px">
        <el-table-column prop="config_name" label="名称" />
        <el-table-column prop="provider_type" label="Provider" width="120" />
        <el-table-column prop="model_name" label="Model" />
        <el-table-column prop="context_window_tokens" label="Context" width="130" />
        <el-table-column prop="api_key_masked" label="API Key" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? "启用" : "停用" }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <div class="toolbar">
              <el-button size="small" @click="editConfig(row)">编辑</el-button>
              <el-button size="small" @click="testConfig(row)">测试</el-button>
              <el-button size="small" type="danger" @click="removeConfig(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section id="prompt-editor" class="panel">
      <div class="panel-header">
        <div>
          <h2>Prompt</h2>
          <p class="muted">{{ editingPromptId ? "正在编辑已有 Prompt" : "新增 Prompt" }}</p>
        </div>
      </div>
      <el-form label-position="top">
        <el-form-item label="Prompt 名称"><el-input v-model="promptForm.prompt_name" /></el-form-item>
        <el-form-item label="任务名">
          <el-input v-model="promptForm.task_name" :disabled="Boolean(editingPromptId)" />
        </el-form-item>
        <el-form-item label="Prompt Text"><el-input v-model="promptForm.prompt_text" type="textarea" :rows="16" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="promptForm.enabled" /></el-form-item>
        <div class="toolbar">
          <el-button type="primary" @click="savePrompt">{{ editingPromptId ? "更新 Prompt" : "新增 Prompt" }}</el-button>
          <el-button v-if="editingPromptId" @click="cancelPromptEdit">取消编辑</el-button>
        </div>
      </el-form>

      <el-table :data="data?.prompts || []" stripe style="margin-top: 18px">
        <el-table-column prop="prompt_name" label="Prompt 名称" min-width="220" />
        <el-table-column prop="task_name" label="任务名" min-width="260" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? "启用" : "停用" }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }"><el-button size="small" @click="editPrompt(row)">编辑</el-button></template>
        </el-table-column>
      </el-table>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>任务绑定</h2>
        <div class="toolbar">
          <el-input-number v-model="reprocessLimit" :min="1" :max="100" />
          <el-button @click="reprocess">补处理待处理内容</el-button>
          <el-button type="primary" @click="saveTasks">保存全部绑定</el-button>
        </div>
      </div>
      <el-table :data="taskRows" stripe>
        <el-table-column label="位置" min-width="210">
          <template #default="{ row }">
            <div>{{ row.display_name }}</div>
            <el-tag v-if="row.task_name === 'classify_ai_financing_relevance'" size="small" type="warning">融资页使用</el-tag>
            <el-tag v-if="row.task_name === 'generate_current_week_financing_report'" size="small" type="success">本周总结使用</el-tag>
            <el-tag v-if="row.task_name === 'generate_previous_week_financing_report'" size="small" type="success">上周总结使用</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="用途" min-width="260" />
        <el-table-column label="使用模型" width="260">
          <template #default="{ row }">
            <el-select v-if="row.task" v-model="row.task.llm_config_id" clearable>
              <el-option
                v-for="config in data?.configs || []"
                :key="config.llm_config_id"
                :label="config.config_name + ' · ' + config.model_name"
                :value="config.llm_config_id"
                :disabled="!config.enabled"
              />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="Prompt" width="260">
          <template #default="{ row }">
            <el-select v-if="row.task" v-model="row.task.prompt_id" clearable>
              <el-option
                v-for="prompt in promptsForTask(row.task_name)"
                :key="prompt.prompt_id"
                :label="prompt.prompt_name"
                :value="prompt.prompt_id"
                :disabled="!prompt.enabled"
              />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="90">
          <template #default="{ row }"><el-switch v-if="row.task" v-model="row.task.enabled" /></template>
        </el-table-column>
      </el-table>
    </section>

    <section class="panel">
      <div class="panel-header"><h2>最近连接测试</h2></div>
      <el-table :data="data?.recent_connection_tests || []" stripe>
        <el-table-column prop="created_at" label="时间" />
        <el-table-column prop="llm_config_id" label="配置" width="90" />
        <el-table-column prop="model_name" label="模型" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column prop="latency_ms" label="耗时 ms" width="110" />
        <el-table-column prop="error_message" label="错误" />
      </el-table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, notifyError } from "../api/client";

const loading = ref(false);
const data = ref<any>(null);
const reprocessLimit = ref(20);
const editingConfigId = ref<number | null>(null);
const editingPromptId = ref<number | null>(null);
const configForm = reactive<any>({
  config_name: "",
  provider_type: "openai",
  base_url: "",
  api_key: "",
  model_name: "",
  timeout_seconds: 60,
  max_retries: 1,
  context_window_tokens: 1000000,
  enabled: true,
});
const promptForm = reactive<any>({
  prompt_name: "",
  task_name: "process_content_metadata",
  prompt_text: "",
  enabled: true,
});

const taskRows = computed(() => data.value?.task_rows || []);

function promptsForTask(taskName: string) {
  return (data.value?.prompts || []).filter((prompt: any) => prompt.task_name === taskName);
}

async function load() {
  loading.value = true;
  try {
    data.value = await api.get("/llm");
  } catch (error) {
    notifyError(error);
  } finally {
    loading.value = false;
  }
}

function resetConfigForm() {
  editingConfigId.value = null;
  Object.assign(configForm, {
    config_name: "",
    provider_type: "openai",
    base_url: "",
    api_key: "",
    model_name: "",
    timeout_seconds: 60,
    max_retries: 1,
    context_window_tokens: 1000000,
    enabled: true,
  });
}

function editConfig(config: any) {
  editingConfigId.value = config.llm_config_id;
  Object.assign(configForm, {
    config_name: config.config_name,
    provider_type: config.provider_type === "anthropic" ? "anthropic" : "openai",
    base_url: config.base_url || "",
    api_key: "",
    model_name: config.model_name || "",
    timeout_seconds: config.timeout_seconds || 60,
    max_retries: config.max_retries ?? 1,
    context_window_tokens: config.context_window_tokens || 1000000,
    enabled: Boolean(config.enabled),
  });
  document.getElementById("llm-config-editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function cancelConfigEdit() {
  resetConfigForm();
}

async function saveConfig() {
  try {
    if (editingConfigId.value) {
      await api.patch(`/llm/configs/${editingConfigId.value}`, configForm);
      ElMessage.success("已更新 LLM 配置");
    } else {
      await api.post("/llm/configs", configForm);
      ElMessage.success("已保存 LLM 配置");
    }
    resetConfigForm();
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function testConfig(row: any) {
  try {
    const result: any = await api.post(`/llm/configs/${row.llm_config_id}/test`);
    ElMessage[result.ok ? "success" : "error"](result.result?.message || "测试完成");
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function removeConfig(row: any) {
  try {
    await ElMessageBox.confirm("已有调用日志的配置会被停用并从任务绑定中移除；未被引用的配置会被删除。确认继续？", "删除 LLM 配置");
    const result: any = await api.delete(`/llm/configs/${row.llm_config_id}`);
    ElMessage.success(result.action === "deleted" ? "已删除 LLM 配置" : "已有历史日志，已停用并解绑");
    if (editingConfigId.value === row.llm_config_id) resetConfigForm();
    await load();
  } catch (error) {
    if (error !== "cancel") notifyError(error);
  }
}

function resetPromptForm() {
  editingPromptId.value = null;
  Object.assign(promptForm, {
    prompt_name: "",
    task_name: "process_content_metadata",
    prompt_text: "",
    enabled: true,
  });
}

function editPrompt(prompt: any) {
  editingPromptId.value = prompt.prompt_id;
  Object.assign(promptForm, {
    prompt_name: prompt.prompt_name,
    task_name: prompt.task_name,
    prompt_text: prompt.prompt_text,
    enabled: prompt.enabled,
  });
  document.getElementById("prompt-editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function cancelPromptEdit() {
  resetPromptForm();
}

async function savePrompt() {
  try {
    if (editingPromptId.value) {
      await api.patch(`/llm/prompts/${editingPromptId.value}`, promptForm);
      ElMessage.success("已更新 Prompt");
    } else {
      await api.post("/llm/prompts", promptForm);
      ElMessage.success("已新增 Prompt");
    }
    resetPromptForm();
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function saveTasks() {
  try {
    await api.patch("/llm/tasks/bulk", { tasks: taskRows.value.map((row: any) => row.task).filter(Boolean) });
    ElMessage.success("已保存任务绑定");
    await load();
  } catch (error) {
    notifyError(error);
  }
}

async function reprocess() {
  try {
    const result: any = await api.post("/llm/reprocess-not-configured", { limit: reprocessLimit.value });
    ElMessage.success(`补处理完成：成功 ${result.result.processed} 条，失败 ${result.result.failed} 条`);
    await load();
  } catch (error) {
    notifyError(error);
  }
}

onMounted(load);
</script>
