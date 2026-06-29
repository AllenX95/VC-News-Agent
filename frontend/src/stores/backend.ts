import { defineStore } from "pinia";
import { ensureBackend, getApiBaseUrl, stopBackend } from "../api/client";

export const useBackendStore = defineStore("backend", {
  state: () => ({
    ready: false,
    loading: false,
    owned: false,
    baseUrl: getApiBaseUrl(),
    message: "",
    error: "",
  }),
  actions: {
    async boot() {
      this.loading = true;
      this.error = "";
      try {
        const result = await ensureBackend();
        this.ready = !!result.ok;
        this.owned = !!result.owned;
        this.baseUrl = result.base_url || getApiBaseUrl();
        this.message = result.message || "";
        if (!result.ok) {
          this.error = result.message || "后端服务不可用";
        }
      } catch (error) {
        this.ready = false;
        this.error = error instanceof Error ? error.message : String(error);
      } finally {
        this.loading = false;
      }
    },
    async stop() {
      await stopBackend();
      this.ready = false;
    },
  },
});
