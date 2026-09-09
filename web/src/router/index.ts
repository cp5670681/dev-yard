import { createRouter, createWebHistory } from "vue-router";
import DashboardView from "@/views/DashboardView.vue";
import ReposView from "@/views/ReposView.vue";
import OpenView from "@/views/OpenView.vue";
import RequirementView from "@/views/RequirementView.vue";
import DocView from "@/views/DocView.vue";

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: DashboardView },
    { path: "/repos", name: "repos", component: ReposView },
    { path: "/open", name: "open", component: OpenView },
    { path: "/r/:jira", name: "requirement", component: RequirementView },
    { path: "/r/:jira/docs/:slug", name: "doc", component: DocView },
  ],
});
