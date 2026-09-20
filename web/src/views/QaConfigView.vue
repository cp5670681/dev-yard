<template>
  <div>
    <div ref="headRef" class="qa-head">
      <div style="min-width: 0">
        <h1 class="text-h5 text-sm-h4 mb-1">测试配置</h1>
        <p class="text-medium-emphasis text-body-2 mb-0">
          写入工作区根 <code>qa.yaml</code>，<code>req test</code> 跑测时读它。密码与 DB 连接串
          <b>明文</b>存这里（文件已 gitignore）。<b>默认环境</b>是跑测未指定 <code>--env</code> 时用的那个。
        </p>
      </div>
      <div class="d-flex align-center ga-2 flex-shrink-0 qa-actions">
        <v-chip
          v-if="dirty"
          size="small"
          color="warning"
          variant="tonal"
          :prepend-icon="mdiAlertCircleOutline"
        >
          未保存
        </v-chip>
        <v-btn variant="text" :prepend-icon="mdiRefresh" :disabled="saving" @click="load">刷新</v-btn>
        <v-btn
          variant="tonal"
          :prepend-icon="mdiLanCheck"
          :loading="checking"
          :disabled="saving"
          @click="checkEnv"
        >
          检查环境
        </v-btn>
        <v-btn color="primary" :prepend-icon="mdiContentSave" :loading="saving" @click="save">保存</v-btn>
      </div>
    </div>

    <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
      {{ error }}
    </v-alert>
    <v-alert v-if="catalogError" type="warning" class="mb-4">{{ catalogError }}</v-alert>

    <v-card v-if="state && !state.payload" variant="outlined" class="mb-4">
      <v-card-title>qa.yaml 无法解析</v-card-title>
      <v-card-text>
        <p class="text-error mb-3">{{ state.parse_error }}</p>
        <p class="text-medium-emphasis">
          表单不会覆盖它，免得抹掉你手写的内容。按下面原文改好（或删掉该文件）后刷新本页。
        </p>
        <pre class="qa-raw">{{ state.raw }}</pre>
      </v-card-text>
      <v-card-actions class="px-6 pb-4">
        <v-spacer />
        <v-btn variant="text" @click="load">刷新</v-btn>
      </v-card-actions>
    </v-card>

    <v-row v-if="form && currentEnv">
      <v-col cols="12" md="4" lg="3">
        <v-card variant="outlined" class="env-rail" :style="{ top: railTop }">
          <v-card-item>
            <template #prepend>
              <v-avatar color="primary" variant="tonal" size="36" rounded="sm">
                <v-icon :icon="mdiServerNetwork" size="20" />
              </v-avatar>
            </template>
            <template #title>环境</template>
            <template #subtitle>{{ envNames.length }} 个 · 默认 {{ form.active_env }}</template>
          </v-card-item>
          <v-divider />
          <v-list nav density="comfortable" class="py-2">
            <v-list-item
              v-for="name in envNames"
              :key="name"
              :active="name === editing"
              active-class="jira-nav-active"
              @click="selectEnv(name)"
            >
              <template #prepend>
                <v-icon
                  :icon="mdiServerNetwork"
                  :color="name === form.active_env ? 'primary' : undefined"
                />
              </template>
              <v-list-item-title class="d-flex align-center ga-2">
                <span class="text-truncate">{{ name }}</span>
                <v-chip
                  v-if="name === form.active_env"
                  size="x-small"
                  color="primary"
                  variant="tonal"
                >
                  默认
                </v-chip>
              </v-list-item-title>
              <v-list-item-subtitle class="text-truncate">
                {{ envSummary(name) }}
              </v-list-item-subtitle>
            </v-list-item>
          </v-list>
          <v-divider />
          <v-card-text class="pa-3">
            <v-text-field
              v-model="newEnvName"
              label="新环境名"
              variant="outlined"
              density="comfortable"
              hide-details
              placeholder="test"
              @keydown.enter="addEnv"
            />
            <v-btn
              block
              variant="tonal"
              class="mt-2"
              :prepend-icon="mdiPlus"
              :disabled="!newEnvName.trim()"
              @click="addEnv"
            >
              新增环境
            </v-btn>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="8" lg="9">
        <v-card variant="outlined">
          <v-toolbar density="comfortable" color="transparent" flat class="px-2">
            <v-toolbar-title class="text-body-1 d-flex align-center ga-2">
              <span class="font-weight-bold">{{ editing }}</span>
              <v-chip v-if="editing === form.active_env" size="x-small" color="primary" variant="flat">
                默认环境
              </v-chip>
            </v-toolbar-title>
            <v-btn
              v-if="editing !== form.active_env"
              :prepend-icon="mdiStarOutline"
              variant="text"
              size="small"
              @click="setActive(true)"
            >
              设为默认
            </v-btn>
            <v-btn :prepend-icon="mdiPencilOutline" variant="text" size="small" @click="openRename">
              重命名
            </v-btn>
            <v-btn
              :icon="mdiDeleteOutline"
              variant="text"
              size="small"
              color="error"
              :disabled="envNames.length <= 1"
              title="删除这个环境"
              @click="removeEnv"
            />
          </v-toolbar>
          <v-divider />
          <v-tabs v-model="tab" color="primary" align-tabs="start" show-arrows>
            <v-tab value="conn" :prepend-icon="mdiLinkVariant">连接</v-tab>
            <v-tab value="exec" :prepend-icon="mdiPipe">脚本执行</v-tab>
            <v-tab value="auth" :prepend-icon="mdiAccountKeyOutline">
              登录态
              <v-chip size="x-small" class="ml-2" variant="tonal">{{ namedAccounts }}</v-chip>
            </v-tab>
            <v-tab value="notes" :prepend-icon="mdiNoteTextOutline">备注</v-tab>
          </v-tabs>
          <v-divider />
          <v-window v-model="tab">
            <v-window-item value="conn">
              <v-card-text class="pa-4">
                <v-text-field
                  v-model="currentEnv.base_url"
                  label="前端地址 base_url"
                  variant="outlined"
                  density="comfortable"
                  placeholder="http://127.0.0.1:8080"
                  :prepend-inner-icon="mdiWeb"
                  persistent-hint
                  hint="跑测时浏览器从这里开；默认环境必填，其他环境可以留空。"
                  class="mb-4"
                />
                <v-text-field
                  v-model="currentEnv.script.runner"
                  label="造数脚本执行器 script.runner"
                  variant="outlined"
                  density="comfortable"
                  placeholder="bin/rails runner"
                  :prepend-inner-icon="mdiScriptTextOutline"
                  persistent-hint
                  hint="可留空；留空则造数只允许 .sql 脚本。"
                  class="mb-4"
                />
                <v-text-field
                  v-model="currentEnv.db.url"
                  label="数据库连接串 db.url"
                  variant="outlined"
                  density="comfortable"
                  placeholder="postgres://user:pass@host:5432/db"
                  :prepend-inner-icon="mdiDatabaseOutline"
                  persistent-hint
                  hint="可留空；留空则禁止用 usql 做 DB 断言。密码含特殊字符需 URL 编码。"
                />
              </v-card-text>
            </v-window-item>

            <v-window-item value="exec">
              <v-card-text class="pa-4" v-if="currentEnv.exec">
                <v-alert
                  v-if="currentEnv.exec.parse_error"
                  type="error"
                  variant="tonal"
                  class="mb-4"
                >
                  {{ currentEnv.exec.parse_error }}
                </v-alert>
                <v-select
                  v-model="currentEnv.exec.use"
                  :items="EXEC_USES"
                  label="exec.use 执行现场"
                  variant="outlined"
                  density="comfortable"
                  class="mb-4"
                  hint="local = freeze worktree；其它 = 远程。远程 base_url 配 local 会被拒绝。"
                  persistent-hint
                />
                <v-row dense>
                  <v-col cols="12" sm="6">
                    <v-text-field
                      v-model="currentEnv.exec.runner"
                      label="runner"
                      variant="outlined"
                      density="comfortable"
                      placeholder="bin/rails runner"
                      class="mb-2"
                    />
                  </v-col>
                  <v-col cols="12" sm="6">
                    <v-text-field
                      v-model.number="currentEnv.exec.timeout"
                      label="timeout（秒）"
                      variant="outlined"
                      density="comfortable"
                      class="mb-2"
                    />
                  </v-col>
                  <v-col cols="12" sm="6">
                    <v-select
                      v-model="currentEnv.exec.payload"
                      :items="['file', 'bundle']"
                      label="payload"
                      variant="outlined"
                      density="comfortable"
                    />
                  </v-col>
                  <v-col cols="12" sm="6">
                    <v-select
                      v-model="currentEnv.exec.db_exec"
                      :items="['host']"
                      label="db.exec"
                      variant="outlined"
                      density="comfortable"
                      hint="host = 本机 usql；.sql 一律在宿主跑（inherit 已废弃）"
                      persistent-hint
                    />
                  </v-col>
                </v-row>
                <v-checkbox v-model="currentEnv.exec.allow_cross_site" label="allow_cross_site（调试用，浏览器与脚本不在同一世界）" hide-details class="mb-2" />
                <template v-if="currentEnv.exec.use === 'ssh'">
                  <v-text-field v-model="currentEnv.exec.target" label="ssh target" variant="outlined" density="comfortable" class="mb-2" />
                  <v-text-field v-model.number="currentEnv.exec.port" label="ssh port" variant="outlined" density="comfortable" class="mb-2" />
                  <v-text-field v-model="currentEnv.exec.workdir" label="workdir" variant="outlined" density="comfortable" class="mb-2" />
                </template>
                <template v-else-if="currentEnv.exec.use === 'docker'">
                  <v-text-field v-model="currentEnv.exec.container" label="docker container" variant="outlined" density="comfortable" class="mb-2" />
                  <v-text-field v-model="currentEnv.exec.workdir" label="workdir" variant="outlined" density="comfortable" class="mb-2" />
                </template>
                <template v-else-if="currentEnv.exec.use === 'jms-k8s'">
                  <v-text-field v-model="currentEnv.exec.jms_host" label="jms.host 堡垒机域名" variant="outlined" density="comfortable" class="mb-2" />
                  <v-text-field v-model.number="currentEnv.exec.jms_port" label="jms.port" variant="outlined" density="comfortable" class="mb-2" />
                  <v-text-field v-model="currentEnv.exec.jms_user" label="jms.user 前两段（alice@root）" variant="outlined" density="comfortable" class="mb-2" />
                  <v-text-field v-model="currentEnv.exec.default_node" label="default_node" variant="outlined" density="comfortable" class="mb-2" />
                  <v-textarea v-model="currentEnv.exec.nodes_text" label="nodes（每行 名: IP）" variant="outlined" density="comfortable" rows="3" class="mb-2" />
                  <v-text-field v-model="currentEnv.exec.namespace" label="namespace" variant="outlined" density="comfortable" class="mb-2" />
                  <v-text-field v-model="currentEnv.exec.container" label="container" variant="outlined" density="comfortable" class="mb-2" />
                  <v-text-field v-model="currentEnv.exec.pod_selector" label="pod.selector（优先）" variant="outlined" density="comfortable" class="mb-2" />
                  <v-text-field v-model="currentEnv.exec.pod_pattern" label="pod.pattern（无 selector 时）" variant="outlined" density="comfortable" class="mb-2" />
                  <v-text-field v-model="currentEnv.exec.workdir" label="workdir（空=不加 cd）" variant="outlined" density="comfortable" class="mb-2" />
                </template>
                <template v-else-if="currentEnv.exec.use === 'raw'">
                  <v-checkbox v-model="currentEnv.exec.shell" label="shell: true（否则 run/ping 按 argv，一行一个）" hide-details class="mb-2" />
                  <v-textarea v-model="currentEnv.exec.run_text" label="run" variant="outlined" density="comfortable" rows="3" class="mb-2" hint="宿主把脚本喂进 stdin，不要写 < {script}" persistent-hint />
                  <v-textarea v-model="currentEnv.exec.ping_text" label="ping" variant="outlined" density="comfortable" rows="2" class="mb-2" />
                </template>
                <template v-else-if="currentEnv.exec.use === 'delegate'">
                  <v-text-field v-model="currentEnv.exec.skill" label="skill（与 command 二选一）" variant="outlined" density="comfortable" class="mb-2" />
                  <v-textarea v-model="currentEnv.exec.run_text" label="command argv（一行一个；配了 skill 则忽略）" variant="outlined" density="comfortable" rows="2" class="mb-2" />
                  <v-textarea v-model="currentEnv.exec.ping_text" label="ping argv" variant="outlined" density="comfortable" rows="2" class="mb-2" />
                </template>
                <v-alert v-if="checkResult" :type="checkResult.ok ? 'success' : 'error'" class="mt-4" variant="tonal">
                  {{ checkResult.ok ? "check-env 通过" : "check-env 失败" }}
                  · {{ checkResult.env }} / {{ checkResult.use }}
                  <ul v-if="checkResult.steps?.length" class="mt-2 text-body-2">
                    <li v-for="s in checkResult.steps" :key="s.step">
                      {{ s.status }} {{ s.step }}{{ s.detail ? `: ${s.detail}` : "" }}
                    </li>
                  </ul>
                </v-alert>
              </v-card-text>
            </v-window-item>

            <v-window-item value="auth">
              <v-card-text class="pa-4">
                <v-combobox
                  v-model="currentEnv.auth.default"
                  :items="accountNames"
                  label="默认账号 auth.default"
                  variant="outlined"
                  density="comfortable"
                  persistent-hint
                  hint="跑测默认用哪个账号；填下面的账号名，也可以手输。"
                  class="mb-4"
                />

                <div class="d-flex align-center justify-space-between mb-2">
                  <div class="text-subtitle-2">
                    账号
                    <span class="text-caption text-medium-emphasis">（{{ namedAccounts }} 个已命名）</span>
                  </div>
                  <v-btn :prepend-icon="mdiPlus" variant="tonal" size="small" @click="addAccount">
                    加账号
                  </v-btn>
                </div>
                <p class="text-caption text-medium-emphasis mb-3">
                  显示 <code>{{ MASK }}</code> 表示密码已存，保存会保留；填新值覆盖，留空清除。
                </p>

                <v-sheet
                  v-if="!currentAccounts.length"
                  border
                  rounded="lg"
                  class="pa-6 text-center text-body-2 text-medium-emphasis mb-3"
                >
                  这个环境还没有账号。点右上「加账号」登记一个，跑测时用
                  <code>auth.default</code> 指定默认登录哪个。
                </v-sheet>

                <v-sheet v-for="(acct, i) in currentAccounts" :key="i" border rounded="lg" class="pa-3 mb-3">
                  <div class="d-flex align-center ga-2 mb-2">
                    <v-icon :icon="mdiAccountOutline" size="18" color="primary" />
                    <span class="text-subtitle-2">{{ acct.name.trim() || `账号 ${i + 1}` }}</span>
                    <v-chip
                      v-if="acct.name.trim() && acct.name.trim() === currentEnv.auth.default"
                      size="x-small"
                      color="primary"
                      variant="tonal"
                    >
                      默认
                    </v-chip>
                    <v-spacer />
                    <v-btn
                      :icon="mdiDeleteOutline"
                      variant="text"
                      size="x-small"
                      color="error"
                      title="删除这个账号"
                      @click="removeAccount(i)"
                    />
                  </div>
                  <v-row dense>
                    <v-col cols="12" sm="6">
                      <v-text-field
                        v-model="acct.name"
                        label="账号名"
                        variant="outlined"
                        density="comfortable"
                        hide-details
                      />
                    </v-col>
                    <v-col cols="12" sm="6">
                      <v-text-field
                        v-model="acct.username"
                        label="username"
                        variant="outlined"
                        density="comfortable"
                        hide-details
                      />
                    </v-col>
                    <v-col cols="12" sm="6">
                      <v-text-field
                        v-model="acct.password"
                        label="password"
                        :type="isRevealed(i) ? 'text' : 'password'"
                        :append-inner-icon="isRevealed(i) ? mdiEyeOffOutline : mdiEyeOutline"
                        variant="outlined"
                        density="comfortable"
                        hide-details
                        @click:append-inner="toggleReveal(i)"
                      />
                    </v-col>
                    <v-col cols="12" sm="6">
                      <v-text-field
                        v-model="acct.state_file"
                        label="state_file（登录态文件，可留空）"
                        variant="outlined"
                        density="comfortable"
                        hide-details
                      />
                    </v-col>
                  </v-row>
                </v-sheet>
              </v-card-text>
            </v-window-item>

            <v-window-item value="notes">
              <v-card-text class="pa-4">
                <v-textarea
                  v-model="currentDraft.notes"
                  label="notes（一行一条）"
                  variant="outlined"
                  density="comfortable"
                  rows="5"
                  persistent-hint
                  hint="会注入 context.md，提醒跑测的人注意什么。"
                />
              </v-card-text>
            </v-window-item>
          </v-window>
        </v-card>
      </v-col>
    </v-row>

    <v-row v-if="form && currentEnv" class="mt-1">
      <v-col cols="12">
        <v-card variant="outlined">
          <v-card-item>
            <template #prepend>
              <v-avatar color="primary" variant="tonal" size="36" rounded="sm">
                <v-icon :icon="mdiFileDocumentOutline" size="20" />
              </v-avatar>
            </template>
            <template #title>用例设计模型</template>
            <template #subtitle>
              qa-design · 生成/重设计用例时用的 agent 模型；留空回退到工作区 pi 全局
            </template>
          </v-card-item>
          <v-card-text>
            <v-row dense>
              <v-col cols="12" sm="6">
                <v-select
                  v-model="form.design.provider"
                  :items="providerItems"
                  label="provider"
                  variant="outlined"
                  density="comfortable"
                  hide-details
                  clearable
                  @update:model-value="onDesignProviderChange($event)"
                />
              </v-col>
              <v-col cols="12" sm="6">
                <v-select
                  v-model="form.design.model"
                  :items="modelItems(form.design.provider)"
                  label="model"
                  variant="outlined"
                  density="comfortable"
                  hide-details
                  clearable
                />
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-row v-if="form && currentEnv" class="mt-1">
      <v-col cols="12" md="6">
        <v-card variant="outlined">
          <v-card-item>
            <template #prepend>
              <v-avatar color="secondary" variant="tonal" size="36" rounded="sm">
                <v-icon :icon="mdiMonitor" size="20" />
              </v-avatar>
            </template>
            <template #title>浏览器与并发</template>
            <template #subtitle>全环境共用</template>
          </v-card-item>
          <v-card-text>
            <v-combobox
              v-model="form.browser.channel"
              :items="CHANNELS"
              label="browser.channel"
              variant="outlined"
              density="comfortable"
              hide-details
              class="mb-2"
            />
            <v-switch
              v-model="form.browser.headed"
              label="headed（有头窗口）"
              color="primary"
              hide-details
              density="comfortable"
            />
            <v-switch
              v-model="form.serialize_accounts"
              label="同账号用例串行（登录会互踢时再开）"
              color="primary"
              hide-details
              density="comfortable"
              class="mt-2"
            />
            <v-alert
              v-if="totalConcurrency > 1"
              type="info"
              density="compact"
              variant="tonal"
              class="mt-2"
            >
              总并发 {{ totalConcurrency }} &gt; 1，宿主会强制无头，有头窗口会抢资源。
            </v-alert>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="6">
        <v-card variant="outlined">
          <v-card-item>
            <template #prepend>
              <v-avatar color="primary" variant="tonal" size="36" rounded="sm">
                <v-icon :icon="mdiRobotOutline" size="20" />
              </v-avatar>
            </template>
            <template #title>模型池</template>
            <template #subtitle>
              同时跑几条用例、用哪些模型 · 总并发 {{ totalConcurrency }}
            </template>
          </v-card-item>
          <v-card-text>
            <v-sheet
              v-if="!form.workers.length"
              border
              rounded="lg"
              class="pa-4 text-center text-body-2 text-medium-emphasis mb-3"
            >
              没配模型池：跑测回退到工作区 pi 全局模型，1 并发。
            </v-sheet>
            <v-sheet v-for="(w, i) in form.workers" :key="i" border rounded="lg" class="pa-3 mb-3">
              <div class="d-flex align-center ga-2 mb-2">
                <v-icon :icon="mdiRobotOutline" size="18" color="primary" />
                <span class="text-subtitle-2">{{ w.id || w.model || `模型 ${i + 1}` }}</span>
                <v-chip size="x-small" variant="tonal">{{ w.concurrency || 1 }} 并发</v-chip>
                <v-spacer />
                <v-btn
                  :icon="mdiDeleteOutline"
                  variant="text"
                  size="x-small"
                  color="error"
                  title="删掉这一行"
                  @click="form.workers.splice(i, 1)"
                />
              </div>
              <v-row dense>
                <v-col cols="12" sm="6">
                  <v-select
                    v-model="w.provider"
                    :items="providerItems"
                    label="provider"
                    variant="outlined"
                    density="comfortable"
                    hide-details
                    clearable
                    @update:model-value="onProviderChange(i, $event)"
                  />
                </v-col>
                <v-col cols="12" sm="6">
                  <v-select
                    v-model="w.model"
                    :items="modelItems(w.provider)"
                    label="model"
                    variant="outlined"
                    density="comfortable"
                    hide-details
                    clearable
                  />
                </v-col>
                <v-col cols="12" sm="4">
                  <v-text-field
                    v-model="w.id"
                    label="id（可留空）"
                    variant="outlined"
                    density="comfortable"
                    hide-details
                  />
                </v-col>
                <v-col cols="6" sm="4">
                  <v-text-field
                    v-model.number="w.concurrency"
                    label="并发"
                    type="number"
                    min="1"
                    max="8"
                    variant="outlined"
                    density="comfortable"
                    hide-details
                  />
                </v-col>
                <v-col cols="6" sm="4">
                  <v-text-field
                    v-model.number="w.priority"
                    label="优先级（小的先跑）"
                    type="number"
                    variant="outlined"
                    density="comfortable"
                    hide-details
                  />
                </v-col>
              </v-row>
            </v-sheet>
            <v-btn block variant="tonal" :prepend-icon="mdiPlus" @click="addWorker">加一个模型</v-btn>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <div v-if="form && currentEnv" class="d-flex align-center justify-end ga-2 mt-4">
      <span class="text-caption text-medium-emphasis">{{ saveHint }}</span>
    </div>

    <v-expansion-panels v-if="state" variant="accordion" class="mt-4">
      <v-expansion-panel>
        <v-expansion-panel-title>
          <v-icon :icon="mdiFileCodeOutline" class="mr-2" />
          原始 YAML
          <span class="text-caption text-medium-emphasis ml-2">磁盘上的当前内容</span>
        </v-expansion-panel-title>
        <v-expansion-panel-text>
          <p class="text-medium-emphasis text-body-2">
            密码、DB 连接串已脱敏为 <code>{{ MASK }}</code>。保存会重排这个文件，注释不会保留。
          </p>
          <pre class="qa-raw">{{ state.raw || "（还没有 qa.yaml，保存后创建）" }}</pre>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>

    <v-dialog v-model="renameDialog" max-width="420">
      <v-card>
        <v-card-title>重命名环境</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="nameDraft"
            label="环境名"
            variant="outlined"
            density="comfortable"
            autofocus
            hide-details
            @keydown.enter="confirmRename"
          />
          <p class="text-caption text-medium-emphasis mt-3 mb-0">
            改名后保存即生效。用旧环境名跑的脚本、命令要一起改。
          </p>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="renameDialog = false">取消</v-btn>
          <v-btn color="primary" @click="confirmRename">确定</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import {
  mdiAccountKeyOutline,
  mdiAccountOutline,
  mdiAlertCircleOutline,
  mdiContentSave,
  mdiDatabaseOutline,
  mdiDeleteOutline,
  mdiEyeOffOutline,
  mdiEyeOutline,
  mdiFileCodeOutline,
  mdiFileDocumentOutline,
  mdiLanCheck,
  mdiLinkVariant,
  mdiMonitor,
  mdiPipe,
  mdiNoteTextOutline,
  mdiPencilOutline,
  mdiPlus,
  mdiRefresh,
  mdiRobotOutline,
  mdiScriptTextOutline,
  mdiServerNetwork,
  mdiStarOutline,
  mdiWeb,
} from "@mdi/js";
import { checkQaEnv, getQaConfig, saveQaConfig } from "@/api/client";
import type { QaConfigPayload, QaConfigState, QaEnvCfg, QaExecCfg } from "@/api/types";
import { useSnack } from "@/composables/snack";

const CHANNELS = ["chrome", "chromium", "msedge", "firefox", "webkit"];
const EXEC_USES = ["local", "ssh", "jms-k8s", "docker", "raw", "delegate"];
const MASK = "********";

interface AccountRow {
  name: string;
  username: string;
  password: string;
  state_file: string;
}

/** Per-env edit state kept out of `form.envs` until save, so switching envs
 *  never merges (and silently drops) rows the user is still editing. */
interface EnvDraft {
  accounts: AccountRow[];
  notes: string;
}

const snack = useSnack();
const state = ref<QaConfigState | null>(null);
const form = ref<QaConfigPayload | null>(null);
const drafts = ref<Record<string, EnvDraft>>({});
const editing = ref("");
const nameDraft = ref("");
const newEnvName = ref("");
const error = ref("");
const saving = ref(false);
const checking = ref(false);
const checkResult = ref<{
  ok: boolean;
  env: string;
  use: string;
  steps?: { step: string; status: string; detail: string }[];
} | null>(null);
const dirty = ref(false);
const tab = ref("conn");
const renameDialog = ref(false);
const revealed = ref<Set<string>>(new Set());
/** Suppress the dirty flag while `load` writes into the form. */
let loading = false;
const headRef = ref<HTMLElement | null>(null);
/** Sticky offset for the env rail: app bar (56) + the sticky header + a gap. */
const railTop = ref("140px");
let headRo: ResizeObserver | undefined;

const catalog = computed(() => state.value?.catalog ?? { providers: [], error: null });
const catalogError = computed(() => catalog.value.error || "");
const providerItems = computed(() => catalog.value.providers.map((p) => p.id));
const envNames = computed(() => (form.value ? Object.keys(form.value.envs) : []));
const currentEnv = computed(() => {
  if (!form.value) return null;
  return form.value.envs[editing.value] ?? null;
});
const currentDraft = computed<EnvDraft>(
  () => drafts.value[editing.value] ?? { accounts: [], notes: "" },
);
const currentAccounts = computed(() => currentDraft.value.accounts);
const accountNames = computed(() =>
  currentAccounts.value.map((a) => a.name.trim()).filter(Boolean),
);
const namedAccounts = computed(() => accountNames.value.length);
const totalConcurrency = computed(() =>
  (form.value?.workers ?? []).reduce((sum, w) => sum + (Number(w.concurrency) || 1), 0),
);
const saveHint = computed(() => {
  if (dirty.value) return "有改动未保存，点右上角「保存」写回 qa.yaml。";
  if (state.value && !state.value.exists) return "还没有 qa.yaml，点右上角「保存」创建。";
  return "已与 qa.yaml 一致。";
});

watch(
  [form, drafts],
  () => {
    if (!loading) dirty.value = true;
  },
  { deep: true },
);

function modelItems(providerId: string | null | undefined): string[] {
  const pid = providerId || "";
  if (!pid) {
    const all: string[] = [];
    for (const p of catalog.value.providers) {
      for (const m of p.models) {
        if (!all.includes(m)) all.push(m);
      }
    }
    return all;
  }
  return catalog.value.providers.find((p) => p.id === pid)?.models ?? [];
}

/** Keep a pair that is already saved in qa.yaml selectable even if pi no longer lists it. */
function ensureSaved(pid: string | null, mid: string | null) {
  if (!pid) return;
  const known = catalog.value.providers.find((p) => p.id === pid);
  if (!known) {
    catalog.value.providers.unshift({ id: pid, models: mid ? [mid] : [] });
    return;
  }
  if (mid && !known.models.includes(mid)) known.models = [mid, ...known.models];
}

/** Keep a provider/model pair consistent for any row (worker or design).
 *  Clearing the provider clears the model too, or the server rejects the pair. */
function syncProviderModel(row: { provider: string | null; model: string | null } | null | undefined, next: string | null) {
  if (!row) return;
  const pid = next || "";
  if (!pid) {
    row.model = "";
    return;
  }
  if (row.model && !modelItems(pid).includes(row.model)) row.model = "";
}

function onProviderChange(index: number, next: string | null) {
  syncProviderModel(form.value?.workers[index], next);
}

function onDesignProviderChange(next: string | null) {
  syncProviderModel(form.value?.design, next);
}

/** A blank field means "use the default"; 0 is a real priority (runs first). */
function intOr(value: unknown, fallback: number): number {
  if (value === "" || value == null) return fallback;
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function blankExec(): QaExecCfg {
  return {
    use: "local",
    payload: "file",
    parallel: false,
    allow_cross_site: false,
    timeout: 300,
    runner: "",
    workdir: "",
    sql_runner: "",
    target: "",
    port: 22,
    container: "",
    jms_host: "",
    jms_port: 22222,
    jms_user: "",
    default_node: "",
    nodes_text: "",
    namespace: "",
    pod_selector: "",
    pod_pattern: "",
    shell: false,
    run_text: "",
    ping_text: "",
    skill: "",
    db_exec: "host",
    parse_error: "",
  };
}

function blankEnv(): QaEnvCfg {
  return {
    base_url: "",
    auth: { default: "default", accounts: {} },
    db: { url: "", exec: "host" },
    script: { runner: "" },
    notes: [],
    exec: blankExec(),
  };
}

function blankDraft(): EnvDraft {
  return { accounts: [{ name: "", username: "", password: "", state_file: "" }], notes: "" };
}

function initDraft(name: string) {
  const env = form.value?.envs[name];
  const accounts: AccountRow[] = [];
  if (env) {
    for (const [acctName, a] of Object.entries(env.auth.accounts ?? {})) {
      accounts.push({
        name: acctName,
        username: a.username,
        password: a.password,
        state_file: a.state_file,
      });
    }
  }
  drafts.value[name] = { accounts, notes: (env?.notes ?? []).join("\n") };
}

function safeHost(url: string) {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

function accountCount(name: string) {
  const draft = drafts.value[name];
  if (draft) return draft.accounts.filter((a) => a.name.trim()).length;
  return Object.keys(form.value?.envs[name]?.auth.accounts ?? {}).length;
}

function envSummary(name: string) {
  const url = (form.value?.envs[name]?.base_url ?? "").trim();
  const address = url ? safeHost(url) : "未填 base_url";
  return `${address} · ${accountCount(name)} 个账号`;
}

function isRevealed(i: number) {
  return revealed.value.has(`${editing.value}:${i}`);
}

function toggleReveal(i: number) {
  const key = `${editing.value}:${i}`;
  const next = new Set(revealed.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  revealed.value = next;
}

function selectEnv(next: string | null) {
  const name = next || "";
  if (!name || name === editing.value) return;
  editing.value = name;
  nameDraft.value = name;
  if (!drafts.value[name]) initDraft(name);
}

function setActive(on: boolean | null) {
  if (!form.value) return;
  if (on) {
    form.value.active_env = editing.value;
  } else if (form.value.active_env === editing.value) {
    const other = envNames.value.find((n) => n !== editing.value);
    if (other) form.value.active_env = other;
  }
}

function addEnv() {
  if (!form.value) return;
  const name = newEnvName.value.trim();
  if (!name) {
    error.value = "请填环境名。";
    return;
  }
  if (form.value.envs[name]) {
    error.value = `环境「${name}」已存在。`;
    return;
  }
  error.value = "";
  form.value.envs[name] = blankEnv();
  drafts.value[name] = blankDraft();
  newEnvName.value = "";
  editing.value = name;
  nameDraft.value = name;
}

function removeEnv() {
  if (!form.value) return;
  if (envNames.value.length <= 1) {
    error.value = "至少保留一个环境。";
    return;
  }
  const name = editing.value;
  delete form.value.envs[name];
  delete drafts.value[name];
  if (form.value.active_env === name) {
    form.value.active_env = envNames.value[0];
  }
  editing.value = envNames.value[0];
  nameDraft.value = editing.value;
  if (!drafts.value[editing.value]) initDraft(editing.value);
}

function openRename() {
  error.value = "";
  nameDraft.value = editing.value;
  renameDialog.value = true;
}

function confirmRename() {
  renameCurrent();
  if (!error.value) renameDialog.value = false;
}

function renameCurrent() {
  if (!form.value) return;
  const from = editing.value;
  const to = nameDraft.value.trim();
  if (!to) {
    error.value = "环境名不能为空。";
    nameDraft.value = from;
    return;
  }
  if (to === from) {
    error.value = "";
    return;
  }
  if (form.value.envs[to]) {
    error.value = `环境「${to}」已存在。`;
    nameDraft.value = from;
    return;
  }
  error.value = "";
  const rebuilt: Record<string, QaEnvCfg> = {};
  for (const [key, value] of Object.entries(form.value.envs)) {
    rebuilt[key === from ? to : key] = value;
  }
  form.value.envs = rebuilt;
  const draft = drafts.value[from];
  if (draft) {
    delete drafts.value[from];
    drafts.value[to] = draft;
  }
  if (form.value.active_env === from) form.value.active_env = to;
  // Remember the original name so the server can recover masked secrets.
  if (!form.value.renamed) form.value.renamed = {};
  const origin = form.value.renamed[from] ?? from;
  delete form.value.renamed[from];
  if (origin !== to) form.value.renamed[to] = origin;
  editing.value = to;
  nameDraft.value = to;
}

function addWorker() {
  form.value?.workers.push({ id: "", provider: null, model: null, concurrency: 1, priority: 100 });
}

function addAccount() {
  currentAccounts.value.push({ name: "", username: "", password: "", state_file: "" });
}

function removeAccount(index: number) {
  const row = currentAccounts.value[index];
  if (!row) return;
  currentAccounts.value.splice(index, 1);
  if (currentEnv.value && row.name.trim() === currentEnv.value.auth.default) {
    currentEnv.value.auth.default = accountNames.value[0] ?? "default";
  }
}

async function load() {
  error.value = "";
  loading = true;
  try {
    const res = await getQaConfig();
    state.value = res;
    if (!res.payload) {
      form.value = null;
      return;
    }
    form.value = res.payload;
    form.value.renamed = {};
    drafts.value = {};
    const names = Object.keys(res.payload.envs);
    for (const name of names) {
      const env = res.payload.envs[name];
      if (!env.exec) env.exec = blankExec();
      initDraft(name);
    }
    editing.value = names.includes(res.payload.active_env)
      ? res.payload.active_env
      : names[0] ?? "";
    nameDraft.value = editing.value;
    tab.value = "conn";
    if (!form.value.design) form.value.design = { provider: null, model: null };
    ensureSaved(form.value.design.provider, form.value.design.model);
    for (const w of res.payload.workers) ensureSaved(w.provider, w.model);
    if (!res.payload.workers.length) addWorker();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    await nextTick();
    loading = false;
    dirty.value = false;
  }
}

function buildAccounts(rows: AccountRow[]): Record<string, AccountRow> {
  const built: Record<string, AccountRow> = {};
  for (const row of rows) {
    const key = row.name.trim();
    if (key) built[key] = { ...row, name: key };
  }
  return built;
}

/** Rows the form would silently drop or that the server would reject by index. */
function accountProblems(): string[] {
  const problems: string[] = [];
  for (const [envName, draft] of Object.entries(drafts.value)) {
    const seen = new Set<string>();
    draft.accounts.forEach((row, i) => {
      const name = row.name.trim();
      const filled = [row.username, row.password, row.state_file].some((v) => v.trim());
      if (!name) {
        if (filled) problems.push(`环境 ${envName} 第 ${i + 1} 个账号没填名字，保存会把它丢掉。`);
        return;
      }
      if (seen.has(name)) problems.push(`环境 ${envName} 账号名「${name}」重复。`);
      seen.add(name);
    });
  }
  return problems;
}

async function checkEnv() {
  if (!form.value) return;
  if (dirty.value) {
    await save();
    if (error.value) return;
  }
  checking.value = true;
  error.value = "";
  checkResult.value = null;
  try {
    const res = await checkQaEnv({ env: editing.value });
    checkResult.value = res;
    snack.notify(res.ok ? "check-env 通过" : "check-env 未通过", res.ok ? "success" : "error");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    checking.value = false;
  }
}

async function save() {
  if (!form.value || !currentEnv.value) return;
  const problems = accountProblems();
  if (problems.length) {
    error.value = problems.join(" ");
    return;
  }
  if (!form.value.active_env || !form.value.envs[form.value.active_env]) {
    error.value = "默认环境必须是一个已配置的环境。";
    return;
  }
  saving.value = true;
  error.value = "";
  try {
    const envs: Record<string, QaEnvCfg> = {};
    for (const [name, env] of Object.entries(form.value.envs)) {
      const draft = drafts.value[name] ?? { accounts: [], notes: "" };
      envs[name] = {
        base_url: env.base_url,
        auth: { default: env.auth.default, accounts: buildAccounts(draft.accounts) },
        db: { ...env.db, exec: env.exec?.db_exec || env.db.exec || "host" },
        script: { ...env.script },
        exec: { ...(env.exec || blankExec()) },
        notes: draft.notes
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
      };
    }
    const saved = await saveQaConfig({
      active_env: form.value.active_env,
      browser: { ...form.value.browser },
      design: {
        provider: form.value.design?.provider ?? "",
        model: form.value.design?.model ?? "",
      },
      workers: form.value.workers.map((w) => ({
        ...w,
        concurrency: intOr(w.concurrency, 1),
        priority: intOr(w.priority, 100),
      })),
      envs,
      serialize_accounts: Boolean(form.value.serialize_accounts),
      renamed: form.value.renamed ?? {},
    });
    state.value = saved;
    form.value.renamed = {};
    dirty.value = false;
    snack.notify("已写入 qa.yaml", "success");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    saving.value = false;
  }
}

function measureHead() {
  const height = headRef.value?.offsetHeight ?? 0;
  railTop.value = `${56 + height + 8}px`;
}

onMounted(() => {
  load();
  measureHead();
  if (typeof ResizeObserver !== "undefined" && headRef.value) {
    headRo = new ResizeObserver(measureHead);
    headRo.observe(headRef.value);
  }
});
onUnmounted(() => headRo?.disconnect());
</script>

<style scoped>
.qa-head {
  position: sticky;
  top: 56px;
  z-index: 4;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  margin-bottom: 8px;
  background: rgb(var(--v-theme-background));
  border-bottom: 1px solid rgb(var(--v-theme-surface-variant));
}

@media (min-width: 960px) {
  .env-rail {
    position: sticky;
  }
}

@media (max-width: 599px) {
  .qa-head {
    flex-direction: column;
    gap: 8px;
  }
  .qa-head .qa-actions {
    width: 100%;
    justify-content: flex-end;
  }
}

.qa-raw {
  background: rgba(var(--v-theme-surface-variant), 0.5);
  border-radius: 4px;
  padding: 12px;
  margin: 0;
  overflow-x: auto;
  font-size: 0.8rem;
  line-height: 1.5;
}
</style>
