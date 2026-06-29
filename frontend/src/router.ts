import { createRouter, createWebHashHistory } from "vue-router";

import DashboardView from "./views/DashboardView.vue";
import FinancingView from "./views/FinancingView.vue";
import SourcesView from "./views/SourcesView.vue";
import ContentView from "./views/ContentView.vue";
import ContentDetailView from "./views/ContentDetailView.vue";
import SummariesView from "./views/SummariesView.vue";
import SummaryDetailView from "./views/SummaryDetailView.vue";
import TaxonomyView from "./views/TaxonomyView.vue";
import LlmView from "./views/LlmView.vue";
import SettingsView from "./views/SettingsView.vue";

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", component: DashboardView, meta: { title: "今日概览" } },
    { path: "/financing", component: FinancingView, meta: { title: "融资新闻" } },
    { path: "/sources", component: SourcesView, meta: { title: "信息源管理" } },
    { path: "/content", component: ContentView, meta: { title: "内容库" } },
    { path: "/content/:id", component: ContentDetailView, meta: { title: "内容详情" } },
    { path: "/summaries", component: SummariesView, meta: { title: "每日汇总" } },
    { path: "/summaries/:date", component: SummaryDetailView, meta: { title: "汇总详情" } },
    { path: "/taxonomy", component: TaxonomyView, meta: { title: "标签与实体" } },
    { path: "/llm", component: LlmView, meta: { title: "LLM / Prompt" } },
    { path: "/settings", component: SettingsView, meta: { title: "系统设置" } },
  ],
});
