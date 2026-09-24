import { createRouter, createWebHistory } from "vue-router";
import DashboardView from "@/views/DashboardView.vue";
import ReposView from "@/views/ReposView.vue";
import SettingsView from "@/views/SettingsView.vue";
import OpenView from "@/views/OpenView.vue";
import ImportView from "@/views/ImportView.vue";
import RequirementView from "@/views/RequirementView.vue";
import DocView from "@/views/DocView.vue";
import QaView from "@/views/QaView.vue";
import QaConfigView from "@/views/QaConfigView.vue";

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: DashboardView },
    { path: "/repos", name: "repos", component: ReposView },
    { path: "/settings", name: "settings", component: SettingsView },
    { path: "/qa-config", name: "qa-config", component: QaConfigView },
    { path: "/open", name: "open", component: OpenView },
    { path: "/import", name: "import", component: ImportView },
    { path: "/r/:jira", name: "requirement", component: RequirementView },
    { path: "/r/:jira/qa", name: "qa", component: QaView },
    { path: "/r/:jira/docs/:slug", name: "doc", component: DocView },
  ],
});
