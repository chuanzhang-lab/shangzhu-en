// ── i18n：文案字典由后端 /i18n.js 下发（window.__I18N__），与 src/i18n 同源 ──
// t(key) 只读后端字典，前端不维护第二份文案；缺失键显式暴露，不静默返回空串。
function t(key) {
  let s = (window.__I18N__ || {})[key];
  if (s === undefined) return '[i18n:missing:' + key + ']';
  for (let i = 1; i < arguments.length; i++) {
    s = s.replace(new RegExp('\\{' + (i - 1) + '\\}', 'g'), arguments[i]);
  }
  return s;
}
function tOr(key, fallback) {
  const v = (window.__I18N__ || {})[key];
  return v === undefined ? fallback : v;
}

// ── DOM refs ──
const chat = document.getElementById('chat');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const status = document.getElementById('status');
let empty = document.getElementById('empty');
const taskList = document.getElementById('task-list');
const newTaskBtn = document.getElementById('new-task');
const paramsList = document.getElementById('params-list');
const catBar = document.getElementById('cat-bar');
const catBtns = catBar.querySelectorAll('.cat-btn');
const analyzeStatus = document.getElementById('analyze-status');
const analyzeControls = document.getElementById('analyze-controls');
const decidePrompt = document.getElementById('decide-prompt');
const decideControls = document.getElementById('decide-controls');
const paramsControls = document.getElementById('params-controls');
const pcFields = document.getElementById('pc-fields');
const pcRecalc = paramsControls.querySelector('.pc-recalc');
const compareControls = document.getElementById('compare-controls');
const ccField = document.getElementById('cc-field');
const ccValue = document.getElementById('cc-value');
const ccRun = compareControls.querySelector('.cc-run');
const exportPdf = document.getElementById('export-pdf');
const exportExcel = document.getElementById('export-excel');
const modelNameEl = document.getElementById('model-name');

// ── 分类系统状态 ──
const CATS = ['all', 'analyze', 'params', 'decide', 'compare'];
const CAT_LABELS = { all: 'ui.cat.all', analyze: 'ui.cat.analyze', params: 'ui.cat.params', decide: 'ui.cat.decide', compare: 'ui.cat.compare' };

// Module 1: 确定性 intent → 分类映射（权威来源是后端 intent，不再依赖 params diff 消歧）
const INTENT_TO_CAT = {
  quick_scan: 'analyze', breakeven: 'analyze', trend: 'analyze',
  cashflow: 'analyze', benchmark: 'analyze', market: 'analyze',
  suggest: 'params', apply: 'params',
  decide: 'decide',
  compare: 'compare',
  report_pdf: 'all', report_excel: 'all', chitchat: 'all',
};
const DEFAULT_CAT = 'all';

let currentCat = 'all';           // 当前激活分类
let lastParams = null;            // 上一轮 params 快照（仅用于 UI：参数面板/对比预填/导出校验）
let hasParams = false;            // 当前是否有项目参数
let opsAvailable = false;         // 是否有待确认方案 A/B
let userManualSwitch = false;     // 用户是否手动切过分类（防止自动抢焦点）
let drafts = {};                  // 分草稿 { all: '', analyze: '', params: '', decide: '', compare: '' }
CATS.forEach(c => drafts[c] = '');
let messageTags = [];             // [{ el, cat }] 每条消息的分类标签
let pendingParamChange = null;    // F3：本次改参意图 {field, value}，用于响应采纳校验
let recentlyUpdatedField = null;  // F3：最近一次被成功采纳的字段，用于面板高亮

// ── 任务状态 ──
let currentTaskId = null, busy = false;
let tasksCache = {};  // id → task（含 params，切任务时恢复参数状态）
let activeCtrl = null;  // 在飞的请求控制器：切任务时 abort，避免响应错配与输入框长锁
let _loadTasksInFlight = null;  // loadTasks 并发锁：防止多次调用导致重复渲染

// ── 健康检查 ──
function applyModelName(name) {
  const n = name || t('ui.connected');
  status.textContent = n;
  status.style.color = '#4ade80';
  if (modelNameEl) modelNameEl.textContent = '· ' + n;
}
fetch('/health').then(r => r.json()).then(d => {
  applyModelName(d.model);
}).catch(() => { status.textContent = t('ui.connect_failed'); status.style.color = '#f87171'; });

// ── 模型设置弹窗（点击右上角模型名打开）──
function testLlmConfig(body) {
  // 保存后自动连通性探测，body.api_key 可能为空（留空=不修改）
  fetch('/settings/llm/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
    body: JSON.stringify(body)
  })
  .then(r => r.json()).then(d => {
    if (d.ok) {
      setToast(t('ui.toast.saved_ok', d.latency_ms), '#059669');
    } else {
      const err = d.error || t('ui.unknown_error');
      setToast(t('ui.toast.saved_test_fail') + err, '#d97706');
    }
  })
  .catch(() => setToast(t('ui.toast.saved_req_fail'), '#d97706'));
}

function openModelSettings() {
  fetch('/settings/llm').then(r => r.json()).then(cfg => {
    // 兼容两种响应结构：config.settings 返回 {"config":{...}}（包裹），llm_advisor 返回平铺结构
    const view = (cfg && cfg.config) || cfg || {};
    let overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML =
      '<div class="modal-box">' +
        '<div class="modal-title">' + t('ui.modal_title') + '</div>' +
        '<label class="modal-label">' + t('ui.modal_model') + '</label>' +
        '<input class="modal-input" id="ms-model" placeholder="如 deepseek-v4-flash">' +
        '<label class="modal-label">' + t('ui.modal_url') + '</label>' +
        '<input class="modal-input" id="ms-url" placeholder="如 https://api.deepseek.com/v1">' +
        '<label class="modal-label">' + t('ui.modal_key') + '</label>' +
        '<input class="modal-input" id="ms-key" type="password" placeholder="sk-... 或 ak-... 格式，至少 8 位">' +
        '<div class="modal-actions">' +
          '<button class="modal-btn cancel" id="ms-cancel">' + t('ui.cancel') + '</button>' +
          '<button class="modal-btn save" id="ms-save">' + t('ui.save') + '</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    const m = overlay.querySelector('#ms-model');
    const u = overlay.querySelector('#ms-url');
    const k = overlay.querySelector('#ms-key');
    m.value = view.model || '';
    u.value = view.base_url || '';

    const close = () => overlay.remove();
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    overlay.querySelector('#ms-cancel').addEventListener('click', close);
    overlay.querySelector('#ms-save').addEventListener('click', () => {
      const body = { model: m.value.trim(), base_url: u.value.trim(), api_key: k.value.trim() };
      if (!body.model) { setToast(t('ui.toast.model_empty'), '#d97706'); return; }
      if (body.api_key && body.api_key.length < 8) { setToast(t('ui.toast.key_short'), '#d97706'); return; }
      fetch('/settings/llm', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }, body: JSON.stringify(body) })
        .then(r => r.json()).then(d => {
          if (d.ok !== undefined && d.ok === false) { setToast(t('ui.toast.save_fail'), '#d97706'); return; }
          if (d.error) { setToast('⚠️ ' + d.error, '#d97706'); return; }
          const saved = (d && d.config) || d || {};
          applyModelName(saved.model);
          setToast(t('ui.toast.saved') + saved.model, '#059669');
          close();
          // 保存后自动连通性探测
          testLlmConfig(body);
        })
        .catch(() => setToast(t('ui.toast.save_req_fail'), '#d97706'));
    });
    m.focus();
  }).catch(() => setToast(t('ui.toast.load_cfg_fail'), '#d97706'));
}
status.addEventListener('click', openModelSettings);
const settingsBtn = document.getElementById('settings-btn');
if (settingsBtn) settingsBtn.addEventListener('click', openModelSettings);

// ── 任务管理 ──
async function loadTasks() {
  // 并发锁：如果已有 loadTasks 在执行，复用同一个 Promise 避免重复渲染
  if (_loadTasksInFlight) return _loadTasksInFlight;
  _loadTasksInFlight = _loadTasksInner().finally(() => { _loadTasksInFlight = null; });
  return _loadTasksInFlight;
}
async function _loadTasksInner() {
  try { const r = await fetch('/tasks'); if (!r.ok) throw new Error('HTTP ' + r.status); const tasks = await r.json(); taskList.innerHTML = '';
    tasks.forEach(t => {
      tasksCache[t.id] = t;  // 缓存含 params 的完整任务对象
      const div = document.createElement('div'); div.className = 'task-item' + (t.id === currentTaskId ? ' active' : ''); div.dataset.id = t.id;
      div.innerHTML = '<span class="tname"></span><span class="tmenu" title="' + t('ui.rename_title') + '">⋯</span><span class="tdel" title="删除任务">×</span>';
      div.querySelector('.tname').textContent = t.name || t('ui.unnamed_task');
      div.querySelector('.tname').addEventListener('click', () => switchTask(t.id));
      div.querySelector('.tmenu').addEventListener('click', e => { e.stopPropagation(); taskMenu(t, div); });
      div.querySelector('.tdel').addEventListener('click', e => { e.stopPropagation(); deleteTask(t, div); });
      taskList.appendChild(div); });
    // 刷新后自动恢复最近任务（否则分类按钮无参数状态、真实参数不加载）
    if (!currentTaskId && tasks.length > 0) {
      switchTask(tasks[0].id);  // /tasks 按 updated_at DESC，第一个是最近任务
    } else if (currentTaskId) {
      // F6：检查当前任务是否已被删除（不在最新列表中）
      const stillExists = tasks.some(t => t.id === currentTaskId);
      if (!stillExists && tasks.length > 0) {
        setToast(t('ui.toast.task_deleted'), '#d97706');
        switchTask(tasks[0].id);
      } else if (!stillExists) {
        // 当前任务被删且无其他任务 → 重置状态
        currentTaskId = null; hasParams = false; lastParams = null;
        clearChat();
      } else {
        // F2：currentTaskId 已存在 → 从刚同步的新缓存恢复当前任务参数态，防刷新后灰化
        const cur = tasksCache[currentTaskId];
        const tp = (cur && cur.params) || {};
        hasParams = Object.keys(tp).length > 0;
        lastParams = hasParams ? tp : null;
        updateCatDisabled(); updateBadges(); updateControls();
      }
    } else {
      updateCatDisabled(); updateBadges(); updateControls();
    }
  } catch (e) { console.error('[loadTasks] 失败:', e); setToast(t('ui.toast.tasks_load_fail'), '#d97706'); } }

// 空项目引导示例卡（新建任务/切到空任务时复用，保持与首屏一致）
const EMPTY_STATE_HTML = '<div class="empty" id="empty"><h2>' + t('ui.empty_title') + '</h2><p>' + t('ui.empty_desc') + '</p><div class="examples">' +
  '<button class="example" data-q="开一家咖啡店，月租金15000，员工3人，人均工资5000，每天50杯客流量，均价25元。详细分析">' + t('ui.example_coffee') + '</button>' +
  '<button class="example" data-q="做一个 SaaS 工具，目标客户中小企业，定价99元/月，预计首年1000用户。详细分析">' + t('ui.example_saas') + '</button>' +
  '<button class="example" data-q="快速：奶茶店总投资50万，月租2万">' + t('ui.example_quick') + '</button>' +
  '<button class="example" data-q="查一下 2024 年中国咖啡行业的毛利率和获客成本基准">' + t('ui.example_benchmark') + '</button>' +
  '</div></div>';

function clearChat() {
  chat.innerHTML = EMPTY_STATE_HTML;
  empty = document.getElementById('empty');  // 更新全局引用，防后续 addMessage 找不到新 empty
  clearParamsPanel();
  resetCatState();
}

async function newTask() {
  currentTaskId = null; let ok = false;
  try {
    const r = await fetch('/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }, body: JSON.stringify({ name: '新任务' }) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const t = await r.json(); currentTaskId = t.id; ok = true;
  } catch (e) { console.error('[newTask] 新建任务失败:', e); currentTaskId = null; }
  if (!ok) { setToast(t('ui.toast.new_task_fail'), '#d97706'); }
  clearChat(); activeTaskUi(); loadTasks(); input.focus();
}

async function switchTask(id) {
  // 切任务时作废在飞请求：abort + 改 currentTaskId 双保险，旧响应在 send 内被丢弃
  if (activeCtrl) { try { activeCtrl.abort(); } catch (e) { /* abort 可能抛出，忽略 */ } }
  currentTaskId = id; clearChat(); activeTaskUi();
  // 从「单一真相源」恢复本任务参数状态：有参数 → 改参/决策/对比可用，无 → 灰。
  // F2：先取缓存；若缓存缺失或为空但有消息历史，再从后端实时拉一次，避免陈旧快照
  //（此前 send() 产生参数后 tasksCache 不更新，切回时把真实参数态误判成「无参数」→ 上下文丢失）。
  await restoreTaskParams(id);
  try {
    const r = await fetch('/tasks/' + id + '/messages');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const msgs = await r.json();
    // 历史恢复不补挂「应用A/B」按钮：历史候选方案对应的 _pending_ops 已随会话失效，
    // 补挂只会产生「点击必失败」的死按钮（当前会话里没有生成过名为 A 的候选方案）。
    // 应用按钮仅在 send 实时返回 ops_available=true 时挂到当前轮消息上。
    msgs.forEach(m => { addMessage(m.role, m.content, false, 'all'); });
  } catch (e) { console.warn('[switchTask] 加载消息历史失败:', e.message); }
  input.focus();
}

// F2：从任务缓存（权威真相源）恢复 hasParams/lastParams，必要时防陈旧刷新
async function restoreTaskParams(id) {
  let t = tasksCache[id];
  let tp = (t && t.params) || {};
  if (!t || Object.keys(tp).length === 0) {
    try {
      const r = await fetch('/tasks');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const ts = await r.json();
      const fresh = ts.find(x => x.id === id);
      if (fresh) { tasksCache[id] = fresh; tp = fresh.params || {}; t = fresh; }
    } catch (e) { console.warn('[restoreTaskParams] 刷新任务缓存失败:', e.message); }
  }
  hasParams = Object.keys(tp).length > 0;
  lastParams = hasParams ? tp : null;
  updateCatDisabled(); updateBadges(); updateControls();
  // 恢复右侧参数面板：任务快照只存 params（无 param_sources/derived），
  // 传 null 让面板只渲染「输入参数」段，来源标签缺省不显示。
  updateParamsPanel(hasParams ? tp : null, null, null);
}

function taskMenu(t, div) {
  const tnameEl = div.querySelector('.tname');
  const originalName = t.name || t('ui.unnamed_task');
  // 创建行内输入框替换任务名显示
  const input = document.createElement('input');
  input.type = 'text';
  input.value = originalName;
  input.className = 'task-rename-input';
  input.style.cssText = 'font-size:13px;border:1px solid #2563eb;border-radius:4px;padding:2px 6px;width:100%;outline:none;box-sizing:border-box;';
  tnameEl.replaceWith(input);
  input.focus();
  input.select();

  // 防「键漂移」：Enter 与 blur 都会触发 finish，且 restore() 移除 input 会再次
  // 触发 blur → finish → 重复发改名请求（失败时无限重试）。finished 标记 +
  // cleanup() 移除监听器确保整轮改名只执行一次。
  let finished = false;
  const onKey = (e) => {
    // IME 组合输入防护：拼音选词时的 Enter（isComposing/229）不算提交，
    // 否则会拿未完成的拼音文本去改名，且 finished 锁会让后续正确输入被丢弃
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === 'Enter') { e.preventDefault(); finish(input.value); }
    if (e.key === 'Escape') { e.preventDefault(); finished = true; cleanup(); restore(); }
  };
  const onBlur = () => finish(input.value);
  const cleanup = () => {
    input.removeEventListener('keydown', onKey);
    input.removeEventListener('blur', onBlur);
  };
  const restore = () => {
    // 判断 input 是否仍在 DOM：在 → 换回 tnameEl（tnameEl 自 replaceWith 后已脱离 DOM，
    // 原实现判断 tnameEl.parentNode 恒为 false，导致 tnameEl 从不被放回、loadTasks 失败时任务名空白）
    if (input.parentNode) { input.replaceWith(tnameEl); }
  };
  const finish = (newName) => {
    if (finished) return;
    const clean = (newName || '').trim();
    if (!clean || clean === originalName) { finished = true; cleanup(); restore(); return; }
    finished = true; cleanup();   // 先上锁，避免 restore 触发的 blur 再次进入 finish
    fetch('/tasks/' + t.id + '/rename', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }, body: JSON.stringify({ name: clean }) })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(() => {
        if (tasksCache[t.id]) tasksCache[t.id].name = clean;  // 同步单一真相源
        restore();                                             // 先把 tnameEl 放回 DOM
        if (tnameEl.parentNode) tnameEl.textContent = clean;  // 立即更新显示，杜绝二次 loadTasks 竞态下的旧名闪烁
        loadTasks();
        setToast(t('ui.toast.renamed') + clean, '#059669');
      })
      .catch(() => { restore(); setToast(t('ui.toast.rename_fail'), '#d97706'); });
  };

  input.addEventListener('keydown', onKey);
  input.addEventListener('blur', onBlur);
}

// Module 4: 任务删除重写 — CSS class 状态管理，不替换事件监听器
async function deleteTask(t, div) {
  const tdel = div.querySelector('.tdel');
  if (tdel.classList.contains('confirming')) {
    // 二次点击 → 执行删除
    await executeDelete(t, div);
    return;
  }
  // 首次点击 → 进入确认状态
  tdel.classList.add('confirming');
  tdel.textContent = t('ui.confirm_delete');
  // 3 秒后自动取消确认状态
  tdel._cancelTimeout = setTimeout(() => resetDeleteButton(tdel), 3000);
}

async function executeDelete(t, div) {
  const tdel = div.querySelector('.tdel');
  if (!tdel) { console.error('[executeDelete] tdel not found'); return; }
  clearTimeout(tdel._cancelTimeout);
  try {
    const r = await fetch('/tasks/' + t.id, { method: 'DELETE', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
    if (!r.ok) {
      const errText = await r.text().catch(() => r.status);
      throw new Error('HTTP ' + r.status + ': ' + errText);
    }
    // 从缓存中移除
    delete tasksCache[t.id];
    // 统一调用 loadTasks 刷新列表（无论是否当前任务，保证 UI 与服务端一致）
    await loadTasks();
    setToast(t('ui.toast.deleted') + (t.name || t('ui.unnamed_task')), '#666');
  } catch (e) {
    console.error('[executeDelete] 删除失败:', e);
    resetDeleteButton(tdel);
    setToast(t('ui.toast.delete_fail') + (e.message || t('ui.toast.retry')), '#d97706');
  }
}

function resetDeleteButton(tdel) {
  if (!tdel || !tdel.parentNode) return;
  tdel.classList.remove('confirming');
  tdel.textContent = '×';
  tdel._cancelTimeout = null;
}

newTaskBtn.addEventListener('click', newTask);
function activeTaskUi() { Array.from(taskList.children).forEach(c => c.classList.toggle('active', c.dataset.id === currentTaskId)); }

// ── 输入框自适应 ──
input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px'; drafts[currentCat] = input.value; });
input.addEventListener('keydown', e => { if (e.isComposing || e.keyCode === 229) return; if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
sendBtn.addEventListener('click', send);

// ── 示例卡（事件委托：clearChat 重建空状态后新示例卡仍可点击）──
chat.addEventListener('click', e => {
  const btn = e.target.closest('.example');
  if (!btn) return;
  input.value = btn.dataset.q; input.style.height = 'auto'; input.style.height = input.scrollHeight + 'px'; drafts[currentCat] = input.value; send();
});

// ── 分类条点击 ──
catBtns.forEach(btn => { btn.addEventListener('click', () => { if (btn.disabled) return; switchCat(btn.dataset.cat, true); }); });

// ── 分析/决策动作按钮（触发语已验证命中后端意图路由）──
document.querySelectorAll('.panel-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    if (busy) return;
    input.value = btn.dataset.q; drafts[currentCat] = input.value; send();
  });
});

// ════════════════════════════════════════════════
// 分类系统核心逻辑
// ════════════════════════════════════════════════

// Module 3: 自动跳分类 — "建议，不覆盖"
function handleAutoSwitch(responseCat) {
  // 目标分类当前被禁用（无参数时 改参/决策/对比）则不跳，避免灰按钮被 auto 切成 active
  const targetBtn = [...catBtns].find(b => b.dataset.cat === responseCat);
  if (targetBtn && targetBtn.disabled) { userManualSwitch = false; return; }
  // responseCat='all' 时不跳转（避免抖到「全部」）
  if (responseCat !== 'all' && responseCat !== currentCat && !userManualSwitch) {
    switchCat(responseCat, false);
  }
  userManualSwitch = false; // 手动选择只压制一次自动跳转，此处统一重置
}

function switchCat(cat, manual) {
  // 保存当前草稿
  drafts[currentCat] = input.value;
  currentCat = cat;
  if (manual) userManualSwitch = true;
  // 更新分类条高亮
  catBtns.forEach(b => b.classList.toggle('active', b.dataset.cat === cat));
  // 恢复目标分类草稿
  input.value = drafts[cat] || '';
  input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  // 切换控件区
  updateControls();
  // 过滤消息
  filterMessages();
  if (manual) {
    focusLatestOfCat(cat);   // 手动切换：滚到该分类最近消息，给「视角切换」反馈
  } else {
    input.focus();           // 自动跳转：聚焦输入框继续对话
  }
}

function updateControls() {
  analyzeStatus.style.display = 'none';
  analyzeControls.style.display = 'none';
  decidePrompt.style.display = 'none';
  decideControls.style.display = 'none';
  paramsControls.style.display = 'none';
  compareControls.style.display = 'none';
  if (currentCat === 'analyze' && hasParams) {
    analyzeStatus.style.display = 'block';
    updateAnalyzeStatus();
    analyzeControls.style.display = 'flex';
  }
  if (currentCat === 'params' && hasParams) {
    paramsControls.style.display = 'block';
    renderParamFields();
  }
  if (currentCat === 'decide') {
    decidePrompt.style.display = 'block';
    decideControls.style.display = 'flex';
    if (!hasParams) decidePrompt.textContent = t('ui.decide_need_params');
    else decidePrompt.textContent = t('ui.decide_prompt');
  }
  if (currentCat === 'compare' && hasParams) {
    compareControls.style.display = 'block';
    prefillCompare();
  }
}

// F4 补充-B：compare 控件预填当前值 + vc 单位提示
// F6/F：草稿按字段保存，切字段来回不再互相清空
let _compareDraft = {};
function prefillCompare() {
  if (!ccField || !ccValue || !lastParams) return;
  const f = ccField.value;
  // 恢复该字段用户上次输入的值（若有），否则预填当前值
  if (_compareDraft[f] !== undefined && _compareDraft[f] !== '') {
    ccValue.value = _compareDraft[f];
  } else {
    const cur = lastParams[f];
    if (cur !== undefined && cur !== null) {
      ccValue.value = (f === 'variable_cost_ratio' && typeof cur === 'number')
        ? String(Math.round(cur * 100)) : String(cur);
    } else {
      ccValue.value = '';
    }
  }
  // 占位提示：vc 填百分比，其余填数值
  ccValue.placeholder = (f === 'variable_cost_ratio') ? t('ui.percent_hint') : t('ui.number');
}

// F6：对比框输入变化时按字段保存草稿；字段切换即时刷新预填
ccValue.addEventListener('input', () => {
  _compareDraft[ccField.value] = ccValue.value;
});
ccField.addEventListener('change', prefillCompare);

function updateAnalyzeStatus() {
  if (!lastParams) { analyzeStatus.textContent = ''; return; }
  // 真实状态摘要：展示已加载的关键参数（替代旧的误导性文案）
  const keys = ['monthly_rent', 'daily_traffic', 'price_per_unit'];
  const parts = [];
  keys.forEach(k => { if (lastParams[k] != null) parts.push(fieldLabel(k) + ' ' + formatVal(k, lastParams[k])); });
  analyzeStatus.textContent = parts.length ? t('ui.toast.current_params') + parts.join(' · ') : t('ui.toast.params_loaded');
}

function renderParamFields() {
  if (!lastParams) { pcFields.innerHTML = '<span class="empty-hint">' + t('ui.decide_need_params') + '</span>'; return; }
  const fields = ['total_investment', 'monthly_rent', 'daily_traffic', 'price_per_unit', 'employee_count', 'avg_salary', 'variable_cost_ratio'];
  let html = '';
  fields.forEach(f => {
    if (lastParams[f] !== undefined && lastParams[f] !== null) {
      let v = lastParams[f];
      if (f === 'variable_cost_ratio' && typeof v === 'number' && Number.isFinite(v)) v = (v * 100).toFixed(0) + '%';
      if (v === null || v === undefined || (typeof v === 'number' && !Number.isFinite(v))) v = '—';
      html += '<span class="pc-field"><label>' + fieldLabel(f) + '</label><input data-field="' + f + '" value="' + v + '"></span>';
    }
  });
  if (!html) html = '<span class="empty-hint">' + t('ui.no_params_to_change') + '</span>';
  pcFields.innerHTML = html;
}

pcRecalc.addEventListener('click', () => {
  const inputs = pcFields.querySelectorAll('input[data-field]');
  const parts = [];
  let lastChanged = null;
  inputs.forEach(inp => {
    const f = inp.dataset.field; const v = inp.value.trim(); if (!v) return;
    // F4：只发改动过的字段（与渲染初值不一致才提交），避免把未改字段无脑重发
    const orig = lastParams ? lastParams[f] : null;
    let origShown = null;
    if (orig !== null && orig !== undefined) {
      // 与渲染初值（renderParamFields）格式保持一致：vc 带 %，其余用 String()
      // （原实现 vc 用 "40" 比对 "40%" 恒不相等 → 未改动的 vc 也被当作改动提交）
      origShown = (f === 'variable_cost_ratio') ? String(Math.round(orig * 100)) + '%' : String(orig);
    }
    const changed = origShown === null || v !== origShown;
    if (changed) { parts.push(fieldLabel(f) + '改为' + v); lastChanged = { field: f, value: v }; }
  });
  if (parts.length === 0) { setToast(t('ui.toast.no_param_change')); return; }
  pendingParamChange = lastChanged;   // F3：记录本次改参意图，响应后校验采纳
  input.value = parts.join('，'); drafts[currentCat] = input.value; send();
});

// F4：改参输入框支持回车单字段提交（含 IME 组合防护，拼音选词回车不误提交）
pcFields.addEventListener('keydown', (e) => {
  if (e.isComposing || e.keyCode === 229) return;
  if (e.key === 'Enter') { const inputEl = e.target;
    if (inputEl && inputEl.dataset && inputEl.dataset.field) { e.preventDefault();
      const f = inputEl.dataset.field; const v = inputEl.value.trim(); if (!v) return;
      pendingParamChange = { field: f, value: v };
      input.value = fieldLabel(f) + '改为' + v; drafts[currentCat] = input.value; send();
    } }
});

ccRun.addEventListener('click', () => {
  const f = ccField.value; const v = ccValue.value.trim();
  if (!v) return;
  input.value = '如果' + fieldLabel(f) + '改成' + v; drafts[currentCat] = input.value; send();
});

// F6：对比框支持回车提交（含 IME 组合防护）
ccValue.addEventListener('keydown', (e) => {
  if (e.isComposing || e.keyCode === 229) return;
  if (e.key === 'Enter') { e.preventDefault(); ccRun.click(); }
});

// ── 空项目灰掉逻辑 ──
function updateCatDisabled() {
  catBtns.forEach(btn => {
    const cat = btn.dataset.cat;
    if (!hasParams && (cat === 'params' || cat === 'decide' || cat === 'compare')) {
      btn.disabled = true;
    } else {
      btn.disabled = false;
    }
  });
  exportPdf.disabled = !hasParams;
  exportExcel.disabled = !hasParams;
}

// ── 红点 ──
function updateBadges() {
  catBtns.forEach(btn => {
    // ops 红点挂「分析」tab——方案A/B 按钮贴在 quick_scan（analyze）消息上
    if (btn.dataset.cat === 'analyze' && opsAvailable) btn.classList.add('show-badge');
    else btn.classList.remove('show-badge');
  });
}

// Module 1.2: 确定性分类（不再引用 lastParams）
// 从动作按钮发送时直接用 sentIntent；否则查 INTENT_TO_CAT 映射表
function classifyResponse(data, sentIntent) {
  if (sentIntent) return sentIntent;
  const intent = data.intent;
  return INTENT_TO_CAT[intent] || DEFAULT_CAT;
}

// Module 2: tagMessage — 统一管理消息分类标签的创建/更新/移除
function tagMessage(el, cat) {
  if (!el) return;
  // 用 el.querySelector 在消息内查找标签，不依赖 firstChild 一定是 wrapper 元素
  // （send 曾用 innerHTML 覆盖整条消息导致 firstChild 变成文本节点，旧写法报错）
  let tagEl = el.querySelector('.msg-cat-tag');
  if (cat && cat !== 'all') {
    if (tagEl) {
      tagEl.className = 'msg-cat-tag ' + cat;
      tagEl.textContent = t(CAT_LABELS[cat]);
    } else {
      tagEl = document.createElement('span');
      tagEl.className = 'msg-cat-tag ' + cat;
      tagEl.textContent = t(CAT_LABELS[cat]);
      tagEl.addEventListener('click', () => switchCat(cat, true));
      // 优先插到 wrapper 内最前（保持 div>wrapper>(tag)+body 结构）；
      // 若 firstChild 不是元素（文本节点等），直接插到消息最前作为兜底。
      const first = el.firstChild;
      if (first && first.nodeType === 1) {
        first.insertBefore(tagEl, first.firstChild);
      } else {
        el.insertBefore(tagEl, el.firstChild);
      }
    }
  } else {
    if (tagEl) tagEl.remove();
  }
  // 同步更新 messageTags 数组里的 cat
  const tag = messageTags.find(t => t.el === el);
  if (tag) tag.cat = cat || 'all';
}

// ── 消息渲染 + 打标 ──
// 分类条作为「操作面板/意图透镜」：对话内容始终完整可见（不隐藏，避免切分类时上下文丢失），
// 点击分类的反馈由「底部控件区切换 + 滚动定位到最近一条该分类消息」提供（见 switchCat）。
function filterMessages() {
  messageTags.forEach(item => { item.el.style.display = ''; });
}

// 滚动定位：切到某分类时，滚到最近一条该分类消息并短暂高亮，给用户「视角已切换」的反馈。
function focusLatestOfCat(cat) {
  if (!cat || cat === 'all') return;
  let target = null;
  for (const item of messageTags) {
    if (!item.el.classList.contains('user') && item.cat === cat) target = item.el;
  }
  if (!target) return;
  target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  target.classList.add('cat-focus');
  setTimeout(() => target.classList.remove('cat-focus'), 1600);
}

// ── 重置分类状态（切任务时）──
function resetCatState() {
  currentCat = 'all'; lastParams = null; hasParams = false; opsAvailable = false; userManualSwitch = false;
  messageTags = [];
  CATS.forEach(c => drafts[c] = '');
  _compareDraft = {};   // F：切任务清对比草稿，防旧任务的草稿值污染新任务
  catBtns.forEach(b => { b.classList.toggle('active', b.dataset.cat === 'all'); b.classList.remove('show-badge'); });
  updateControls(); updateCatDisabled(); updateBadges();
  input.value = '';
}

// ── Markdown 渲染（不变）──
function escape(html) { return html.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

// F3/F5：轻量 toast 反馈（右上角浮动提示，3s 自动消失）
let _toastTimer = null;
function setToast(msg, color) {
  let el = document.getElementById('toast');
  if (!el) { el = document.createElement('div'); el.id = 'toast'; el.style.cssText = 'position:fixed;top:64px;right:20px;z-index:999;background:#1a1a1a;color:#fff;padding:10px 16px;border-radius:8px;font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,0.2);max-width:280px;'; document.body.appendChild(el); }
  el.textContent = msg; el.style.background = color || '#1a1a1a';
  clearTimeout(_toastTimer); _toastTimer = setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 3000);
}

function renderMarkdown(text) {
  let html = escape(text);
  html = html.replace(/\| (.+?) \|/g, (m, c) => '| ' + c.trim() + ' |');
  const lines = html.split('\n'); let inTable = false, tableHtml = '', headers = []; const out = [];
  for (let i = 0; i < lines.length; i++) { const line = lines[i].trim();
    if (line.startsWith('|') && line.endsWith('|')) { const cells = line.slice(1, -1).split('|').map(c => c.trim());
      if (!inTable) { const next = (lines[i+1] || '').trim(); if (next.match(/^\|[\s\-:|]+\|$/)) { inTable = true; headers = cells; i++; tableHtml = '<table><thead><tr>' + headers.map(h => '<th>' + h + '</th>').join('') + '</tr></thead><tbody>'; continue; } }
      else { tableHtml += '<tr>' + cells.map(c => '<td>' + c + '</td>').join('') + '</tr>'; continue; } }
    else if (inTable) { out.push(tableHtml + '</tbody></table>'); inTable = false; tableHtml = ''; }
    out.push(line); }
  if (inTable) out.push(tableHtml + '</tbody></table>');
  html = out.join('\n');
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\n/g, '<br>');
  return html; }

// 只更新消息的 body 内容，保留 div>wrapper>(tag)+body 结构，避免整条 innerHTML 覆盖破坏结构
function setMsgBody(el, html) {
  const b = el && el.querySelector('.msg-body');
  if (b) { b.innerHTML = html; } else if (el) { el.innerHTML = html; }  // 兜底
}

function addMessage(role, content, isHtml, cat) {
  if (empty && empty.parentNode) empty.remove();
  const div = document.createElement('div'); div.className = 'msg ' + role;
  const wrapper = document.createElement('div');
  // 用户消息（cat='all'）和历史恢复消息始终归类为 'all'，切换分类时仍可见
  const msgCat = cat || 'all';
  if (msgCat !== 'all' && role === 'assistant') {
    const tag = document.createElement('span');
    tag.className = 'msg-cat-tag ' + msgCat; tag.textContent = t(CAT_LABELS[msgCat]);
    tag.addEventListener('click', () => switchCat(msgCat, true));
    wrapper.appendChild(tag);
  }
  const body = document.createElement('div'); body.className = 'msg-body'; body.innerHTML = isHtml ? content : renderMarkdown(content);
  wrapper.appendChild(body);
  div.appendChild(wrapper);
  chat.appendChild(div); chat.scrollTop = chat.scrollHeight;
  // 记录分类标签
  messageTags.push({ el: div, cat: msgCat });
  // 对话始终完整可见（分类仅作面板透镜，不隐藏消息）
  div.style.display = '';
  return div;
}

function parseOpsFromContent(content) {
  const ops = []; const re = /\*\*方案([AB])\*\*/g; let m;
  while ((m = re.exec(content)) !== null) { ops.push({ tag: m[1], label: '应用' + m[1] }); }
  return ops; }

// ── 参数面板（只读化）──
function clearParamsPanel() { paramsList.innerHTML = '<p style="color:#999;font-size:13px;padding:12px 0;">' + t('ui.params_placeholder') + '</p>'; }

// ── 顾问面板 ──
let _advisorVersion = null;
let _advisorRefreshTimer = null;
const advisorList = document.getElementById('advisor-list');

function renderAdvisorPanel(data) {
  if (!advisorList) return;
  if (!data || !data.judgment) {
    advisorList.innerHTML = '<div class="advisor-empty">' + t('ui.advisor_none') + '</div>';
    return;
  }
  let html = '<span class="advisor-tag">' + t('ui.advisor_tag') + '</span>';
  // 判断
  if (data.judgment) {
    html += '<div class="advisor-section">' + t('ui.advisor_current') + '</div>';
    html += '<div class="advisor-judgment">' + escape(data.judgment) + '</div>';
  }
  // 风险
  if (data.risks && data.risks.length > 0) {
    html += '<div class="advisor-section">' + t('ui.advisor_risk') + '</div>';
    data.risks.forEach(r => { html += '<div class="advisor-risk">' + escape(r.text || r) + '</div>'; });
  }
  // 建议动作
  if (data.actions && data.actions.length > 0) {
    html += '<div class="advisor-section">' + t('ui.advisor_actions') + '</div>';
    data.actions.forEach((a, i) => {
      html += '<div class="advisor-action"><div class="advisor-action-text">' + escape(a.preview || '') + '</div>';
      html += '<div class="advisor-action-btns"><button class="advisor-preview" data-idx="' + i + '">预览</button>';
      html += '<button class="advisor-apply act-btn apply" data-idx="' + i + '">' + t('ui.apply') + '</button></div></div>';
    });
  }
  // 依据
  if (data.judgment_citations && data.judgment_citations.length > 0) {
    html += '<div class="advisor-section">' + t('ui.advisor_basis') + '</div>';
    data.judgment_citations.forEach(c => {
      html += '<div class="advisor-citation">' + escape(c.field) + ' = ' + c.value + ' ' + (c.source || '') + '</div>';
    });
  }
  advisorList.innerHTML = html;
  // 绑定按钮事件
  advisorList.querySelectorAll('.advisor-preview').forEach(btn => {
    btn.addEventListener('click', () => previewAdvisorAction(parseInt(btn.dataset.idx)));
  });
  advisorList.querySelectorAll('.advisor-apply').forEach(btn => {
    btn.addEventListener('click', () => applyAdvisorAction(parseInt(btn.dataset.idx)));
  });
}

function previewAdvisorAction(idx) {
  if (!_advisorData || !_advisorData.actions || !_advisorData.actions[idx]) return;
  const op = _advisorData.actions[idx].op;
  if (!op) return;
  fetch('/advisor/preview?tid=' + currentTaskId + '&op=' + encodeURIComponent(JSON.stringify(op)))
    .then(r => r.json()).then(d => {
      if (d.ok) setToast(t('ui.toast.preview_ok') + (d.preview || ''), '#059669');
      else setToast('⚠️ ' + (d.reason || t('ui.preview_fail')), '#d97706');
    })
    .catch(() => setToast(t('ui.toast.preview_req_fail'), '#d97706'));
}

function applyAdvisorAction(idx) {
  if (!_advisorData || !_advisorData.actions || !_advisorData.actions[idx]) return;
  const op = _advisorData.actions[idx].op;
  if (!op) return;
  fetch('/advisor/apply?tid=' + currentTaskId, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
    body: JSON.stringify({ op: op }),
  }).then(r => r.json()).then(d => {
    if (d.ok && d.applied) {
      setToast(t('ui.toast.applied'), '#059669');
      refreshAdvisor();
    } else {
      setToast('⚠️ ' + (d.reason || t('ui.apply_fail')), '#d97706');
    }
  }).catch(() => setToast(t('ui.toast.apply_req_fail'), '#d97706'));
}

let _advisorData = null;
function refreshAdvisor() {
  if (!currentTaskId) return;
  // 防抖：5s 内不重复请求
  if (_advisorRefreshTimer) return;
  _advisorRefreshTimer = setTimeout(() => { _advisorRefreshTimer = null; }, 5000);
  fetch('/advisor?tid=' + currentTaskId)
    .then(r => r.json())
    .then(d => {
      _advisorData = d;
      _advisorVersion = d.params_version;
      renderAdvisorPanel(d);
    })
    .catch(() => {
      if (advisorList) advisorList.innerHTML = '<div class="advisor-empty">' + t('ui.advisor_unavailable') + '</div>';
    });
}

// Tab 切换
document.querySelectorAll('.params-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.params-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const target = tab.dataset.tab;
    if (target === 'advisor') {
      paramsList.style.display = 'none';
      advisorList.style.display = 'block';
      refreshAdvisor();
    } else {
      paramsList.style.display = 'block';
      advisorList.style.display = 'none';
    }
  });
});

function updateParamsPanel(params, paramSources, derived) {
  if (!params || Object.keys(params).length === 0) { clearParamsPanel(); return; }
  const inputFields = ['total_investment','monthly_rent','daily_traffic','price_per_unit','employee_count','avg_salary','variable_cost_ratio','labor_burden','utilities','packaging','commission','other_fixed'];
  let html = '';
  html += '<div class="param-section">' + t('ui.input_params_section') + '</div>';
  inputFields.forEach(f => {
    if (params[f] !== undefined && params[f] !== null) {
      const src = (paramSources || {})[f] || '';
      // 来源标签：无来源信息（历史快照恢复）时不渲染，避免误标「推算」
      let srcHtml = '';
      if (src) {
        const srcClass = src.startsWith('[用户]') ? 'psrc-user' : src.startsWith('[缺失]') ? 'psrc-missing' : 'psrc-derived';
        const srcLabel = src.startsWith('[用户]') ? t('ui.src_user') : src.startsWith('[缺失]') ? t('ui.src_missing') : t('ui.src_derived');
        srcHtml = '<span class="psrc ' + srcClass + '">' + srcLabel + '</span>';
      }
      const hl = (f === recentlyUpdatedField) ? ' just-updated' : '';   // F3 变更高亮
      html += '<div class="param-row' + hl + '"><span class="pname">' + fieldLabel(f) + '</span><span style="display:flex;align-items:center;gap:6px;">' + srcHtml + '<span class="pval" style="cursor:default;">' + formatVal(f, params[f]) + '</span></span></div>';
    }
  });
  if (derived && derived.length > 0) {
    const okDerived = derived.filter(d => d.status === 'ok');
    const missDerived = derived.filter(d => d.status === 'missing');
    if (okDerived.length > 0) { html += '<div class="param-section">' + t('ui.derived_section') + '</div>';
      okDerived.forEach(d => { html += '<div class="param-row"><span class="pname">' + d.label + '</span><span style="display:flex;align-items:center;gap:6px;"><span class="psrc psrc-derived">推算</span><span class="pval" style="color:#666;cursor:default;">' + formatNum(d.value) + ' ' + (d.unit||'') + '</span></span></div>'; }); }
    if (missDerived.length > 0) { html += '<div class="param-section">' + t('ui.missing_section') + '</div>';
      missDerived.forEach(d => { html += '<div class="param-row"><span class="pname">' + d.label + '</span><span class="psrc psrc-missing">缺 ' + (d.missing||'') + '</span></div>'; }); }
  }
  paramsList.innerHTML = html;
}

function fieldLabel(f) {
  return tOr('ui.field.' + f, f); }
function formatVal(f, v) { if (f === 'variable_cost_ratio' && typeof v === 'number' && Number.isFinite(v)) return (v * 100).toFixed(0) + '%'; return formatNum(v); }
function formatNum(v) { if (v === null || v === undefined) return '—'; if (typeof v === 'number' && Number.isFinite(v)) return v.toLocaleString(); if (typeof v === 'number' && !Number.isFinite(v)) return '—'; if (String(v).trim() === '') return '—'; return String(v); }

// ── 导出按钮（F5 补充-D：参数完整性前置校验）──
function exportGuard() {
  if (exportPdf.disabled || exportExcel.disabled) return false;
  const p = lastParams || {};
  const hasRevenue = (p.monthly_revenue != null) || (p.daily_traffic != null && p.price_per_unit != null);
  const hasVc = p.variable_cost_ratio != null || p.variable_cost_rate != null;
  // 固定成本：组件任一 或 显式月固支
  const comps = ['monthly_rent','employee_count','avg_salary','utilities','packaging','commission','other_fixed','monthly_expense'];
  const hasFixed = comps.some(k => p[k] != null);
  if (!hasRevenue || !hasVc || !hasFixed) {
    const missing = [];
    if (!hasRevenue) missing.push(t('ui.missing.revenue'));
    if (!hasVc) missing.push(t('ui.missing.vc'));
    if (!hasFixed) missing.push(t('ui.missing.fixed'));
    setToast(t('ui.toast.export_incomplete', missing.join('、')), '#d97706');
    return true; // blocked
  }
  return false;
}
exportPdf.addEventListener('click', () => { if (exportGuard()) return; input.value = t('ui.gen_pdf'); drafts[currentCat] = input.value; send(); });
exportExcel.addEventListener('click', () => { if (exportGuard()) return; input.value = t('ui.gen_excel'); drafts[currentCat] = input.value; send(); });

// Module 5: 辅助函数 — validateParamAdoption（F3 改参校验）
function validateParamAdoption(params, pending) {
  if (!pending) return null;
  const pc = pending;
  const target = (params || {})[pc.field];
  let accepted = false;
  if (target !== undefined && target !== null) {
    if (pc.field === 'variable_cost_ratio' && typeof target === 'number') {
      const wantRate = parseFloat(String(pc.value).replace('%', ''));
      accepted = Math.abs(target * 100 - wantRate) < 1e-6;
    } else {
      accepted = String(target) === String(pc.value).replace('%', '') || String(target) === String(pc.value);
    }
  }
  const label = fieldLabel(pc.field);
  if (accepted) {
    recentlyUpdatedField = pc.field;
    setToast(t('ui.toast.updated', label, pc.value), '#059669');
  } else {
    recentlyUpdatedField = null;
    setToast(t('ui.toast.not_adopted', label, formatVal(pc.field, target), pc.value), '#d97706');
  }
  return accepted;
}

// Module 5: 辅助函数 — addActionButtons（应用 A/B 按钮）
function addActionButtons(el, content) {
  const ops = parseOpsFromContent(content || '');
  if (ops.length === 0) return;
  const actionsDiv = document.createElement('div');
  actionsDiv.className = 'msg-actions';
  ops.forEach(op => {
    const btn = document.createElement('button');
    btn.className = 'act-btn apply';
    btn.textContent = op.label;
    btn.addEventListener('click', () => { input.value = '应用' + op.tag; drafts[currentCat] = input.value; send(); });
    actionsDiv.appendChild(btn);
  });
  el.appendChild(actionsDiv);
}

function addAdviceButton(el, adviceMeta, tid) {
  if (!el || !adviceMeta) return;
  const wrap = document.createElement('div');
  wrap.className = 'ai-advice-wrap';
  const btn = document.createElement('button');
  btn.className = 'act-btn advice-btn';
  btn.textContent = t('ui.gen_advice');
  const statusEl = document.createElement('div');
  statusEl.className = 'ai-advice-status';
  wrap.appendChild(btn);
  wrap.appendChild(statusEl);
  el.appendChild(wrap);

  const setStatus = (html, cls) => {
    statusEl.className = 'ai-advice-status' + (cls ? ' ' + cls : '');
    statusEl.innerHTML = html || '';
  };

  const runAdvice = () => {
    btn.disabled = true;
    btn.textContent = t('ui.interpreting');
    setStatus(t('ui.interpreting_hint'), 'loading');
    const ctrl = new AbortController();
    const timeoutId = setTimeout(() => ctrl.abort(), 45000);
    fetch('/analysis/advice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({
        task_id: tid,
        thread_id: tid,
        analysis_id: adviceMeta.analysis_id,
        params_version: adviceMeta.params_version,
      }),
      signal: ctrl.signal,
    }).then(r => r.json().then(d => ({ ok: r.ok, status: r.status, d }))).then(({ d }) => {
      const st = (d && d.status) || 'error';
      if (st === 'ok' || (st === 'empty' && (d.text || d.ops_block))) {
        let html = '';
        if (d.text) html += '<div class="ai-advice-text">' + renderMarkdown('💡 **AI Interpretation**\n\n' + d.text) + '</div>';
        if (d.ops_block) html += '<div class="ai-advice-ops">' + renderMarkdown(d.ops_block) + '</div>';
        if (!html) html = '<em>' + t('ui.no_advice') + '</em>';
        setStatus(html, 'ok');
        btn.textContent = t('ui.generated');
        if (d.ops_block) addActionButtons(el, d.ops_block);
        if (d.ops && d.ops.length) opsAvailable = true;
        updateBadges();
        return;
      }
      if (st === 'stale') {
        setStatus(escape(d.reason || t('ui.stale_advice')), 'stale');
        btn.textContent = t('ui.expired');
        btn.disabled = true;
        return;
      }
      setStatus(escape(d.reason || t('ui.advice_fail_retry')), st === 'timeout' ? 'timeout' : 'error');
      btn.disabled = false;
      btn.textContent = t('ui.retry');
    }).catch(e => {
      const isTimeout = e && e.name === 'AbortError';
      setStatus(isTimeout ? t('ui.advice_timeout') : t('ui.net_error_retry'), isTimeout ? 'timeout' : 'error');
      btn.disabled = false;
      btn.textContent = t('ui.retry');
    }).finally(() => { clearTimeout(timeoutId); });
  };
  btn.addEventListener('click', runAdvice);
}

// ════════════════════════════════════════════════
// send() — 核心发送逻辑（重构，使用新辅助函数）
// ════════════════════════════════════════════════
async function send() {
  const text = input.value.trim(); if (!text || busy) return;
  busy = true; sendBtn.disabled = true; input.disabled = true; input.value = ''; input.style.height = 'auto';
  drafts[currentCat] = '';  // 清当前分类草稿
  addMessage('user', text, false, 'all');  // 用户消息始终归类为「全部」，切换分类时仍可见
  const placeholder = addMessage('assistant', '<span class="typing">' + t('ui.thinking') + '</span>', true, currentCat);
  placeholder.classList.add('streaming');
  // F8：60s 超时兜底——LLM 挂起时不再永久锁死输入框
  const ctrl = new AbortController();
  activeCtrl = ctrl;                     // 登记在飞请求，供 switchTask abort
  const timeoutId = setTimeout(() => ctrl.abort(), 60000);
  const sentTaskId = currentTaskId;      // 竞态保护：记录发送时所在任务
  try {
    const body = { messages: [{ role: 'user', content: text }] };
    if (currentTaskId) body.task_id = currentTaskId;
    const resp = await fetch('/chat', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }, body: JSON.stringify(body), signal: ctrl.signal });
    if (!resp.ok) {
      const err = await resp.text();
      if (sentTaskId !== currentTaskId) { placeholder.remove(); return; }  // 已切任务，丢弃
      placeholder.classList.remove('streaming');
      setMsgBody(placeholder, '<em style="color:#dc2626">' + t('ui.req_fail_prefix') + escape(err) + '</em>');
      input.value = text; drafts[currentCat] = text;   // F7：失败保草稿
      return;
    }
    const data = await resp.json();
    if (sentTaskId !== currentTaskId) { placeholder.remove(); return; }  // 已切任务，丢弃
    setMsgBody(placeholder, renderMarkdown(data.content || t('ui.no_response')));
    placeholder.classList.remove('streaming');

    // ── F3 改参采纳校验 ──
    if (pendingParamChange) {
      const pc = pendingParamChange;
      pendingParamChange = null;
      validateParamAdoption(data.params, pc);
    }

    // ── 打标（使用 tagMessage 辅助函数）──
    const msgCat = classifyResponse(data, null);
    tagMessage(placeholder, msgCat);
    // 对话始终完整可见（分类仅作面板透镜，不隐藏消息）

    // ── 应用 A/B 按钮（使用 addActionButtons 辅助函数）──
    if (data.ops_available) {
      addActionButtons(placeholder, data.content);
    }

    // ── 按需 AI 解读（structured 先出规则结果，点击才触发 LLM）──
    if (data.ai_advice && data.ai_advice.available) {
      addAdviceButton(placeholder, data.ai_advice, data.thread_id || currentTaskId);
    }

    // ── 参数面板更新 ──
    if (data.params) updateParamsPanel(data.params, data.param_sources, data.derived);

    // ── 状态更新 ──
    if (data.params && Object.keys(data.params).length > 0) {
      hasParams = true;
      lastParams = data.params;
      // F2：写回「单一真相源」——当前任务的真实参数立即同步进缓存
      if (currentTaskId) {
        tasksCache[currentTaskId] = tasksCache[currentTaskId] || {};
        tasksCache[currentTaskId].params = data.params;
      }
    }
    opsAvailable = !!data.ops_available;
    updateCatDisabled();
    updateBadges();
    updateControls();

    // ── 自动跳分类（使用 handleAutoSwitch 辅助函数）──
    if (hasParams && data.intent) {
      handleAutoSwitch(msgCat);
    }

    if (data.thread_id && data.thread_id !== currentTaskId) { currentTaskId = data.thread_id; loadTasks(); }
  } catch (e) {
    pendingParamChange = null;  // 网络错误时清除，防止下次发送误校验
    if (sentTaskId !== currentTaskId) { placeholder.remove(); return; }  // 已切任务，丢弃
    placeholder.classList.remove('streaming');
    const isTimeout = (e && e.name === 'AbortError');
    setMsgBody(placeholder, '<em style="color:#dc2626">' + (isTimeout ? t('ui.req_timeout') : 'Network error: ' + escape(e.message)) + '</em>');
    input.value = text; drafts[currentCat] = text;   // F7：失败保草稿
  }
  finally { clearTimeout(timeoutId); if (activeCtrl === ctrl) activeCtrl = null; busy = false; sendBtn.disabled = false; input.disabled = false; input.focus(); }
}

loadTasks();
