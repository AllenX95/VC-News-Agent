<template>
  <el-config-provider>
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-mark">AI</div>
          <div>
            <strong>AI 投资情报</strong>
            <span>Desktop Agent</span>
          </div>
        </div>

        <nav class="nav">
          <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-link">
            <component :is="item.icon" />
            <span>{{ item.label }}</span>
          </RouterLink>
        </nav>

        <div class="backend-panel">
          <div class="backend-row">
            <span :class="['status-dot', backend.ready ? 'ok' : 'warn']"></span>
            <span>{{ backend.ready ? "后端已连接" : "后端未就绪" }}</span>
          </div>
          <small>{{ backend.baseUrl }}</small>
          <el-button size="small" :loading="backend.loading" @click="backend.boot()">重连后端</el-button>
          <el-button class="shutdown-button" size="small" type="danger" plain :loading="shuttingDown" @click="shutdownAndExit">
            <el-icon><SwitchButton /></el-icon>
            <span>关闭服务并退出</span>
          </el-button>
        </div>
      </aside>

      <main class="main">
        <header class="topbar">
          <div>
            <h1>{{ route.meta.title || "AI 投资情报 Agent" }}</h1>
            <p v-if="backend.message">{{ backend.message }}</p>
          </div>
          <el-tag v-if="backend.owned" type="success">Tauri 托管后端</el-tag>
          <el-tag v-else type="info">外部后端</el-tag>
        </header>

        <el-alert
          v-if="backend.error"
          class="service-alert"
          type="error"
          :closable="false"
          show-icon
          :title="backend.error"
        />

        <RouterView v-if="backend.ready" />
        <section v-else class="empty-state">
          <el-empty description="正在等待 Python 后端服务">
            <el-button type="primary" :loading="backend.loading" @click="backend.boot()">启动或重连</el-button>
          </el-empty>
        </section>
      </main>
    </div>
  </el-config-provider>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";
import {
  Collection,
  Connection,
  DataAnalysis,
  Document,
  Files,
  HomeFilled,
  Money,
  Setting,
  SwitchButton,
  Tickets,
} from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { exitApplication } from "./api/client";
import { useBackendStore } from "./stores/backend";

const route = useRoute();
const backend = useBackendStore();
const shuttingDown = ref(false);

const navItems = [
  { path: "/", label: "今日概览", icon: HomeFilled },
  { path: "/financing", label: "融资新闻", icon: Money },
  { path: "/sources", label: "信息源", icon: Connection },
  { path: "/content", label: "内容库", icon: Files },
  { path: "/summaries", label: "每日汇总", icon: Document },
  { path: "/taxonomy", label: "标签实体", icon: Collection },
  { path: "/llm", label: "LLM / Prompt", icon: DataAnalysis },
  { path: "/settings", label: "系统设置", icon: Setting },
];


async function shutdownAndExit() {
  try {
    await ElMessageBox.confirm(
      "将关闭 Python 后端服务并退出桌面应用。",
      "关闭服务并退出",
      {
        type: "warning",
        confirmButtonText: "关闭并退出",
        cancelButtonText: "取消",
      },
    );
  } catch {
    return;
  }

  shuttingDown.value = true;
  try {
    await backend.stop();
    const closed = await exitApplication();
    if (!closed) {
      ElMessage.success("后端服务已请求关闭，请手动关闭浏览器标签页。");
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    ElMessage.error(message);
  } finally {
    shuttingDown.value = false;
  }
}

onMounted(() => {
  backend.boot();
});
</script>
