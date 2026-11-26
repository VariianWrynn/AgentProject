(() => {
  'use strict';

  const stages = {
    router: 0, chief_architect: 0, deep_scout: 1, lead_writer: 3,
    evidence_gate: 4, critic_master: 4, human_gate: 5, synthesizer: 6,
  };
  const nodeWork = {
    router: '正在识别研究需求',
    chief_architect: '正在拆解研究问题并生成大纲',
    deep_scout: '正在处理检索任务与返回的资料',
    lead_writer: '正在撰写或修订章节与引用',
    evidence_gate: '正在校验草稿引用与证据',
    critic_master: '正在评审草稿的论据与结论',
    human_gate: '正在准备人工审核',
    synthesizer: '正在汇总章节、图表与参考文献',
  };
  const toolWork = {
    web_search: '网页检索结果', rag_search: '知识库检索结果',
    text2sql: '数据库查询结果', data_analysis: '数据分析与制图结果',
  };
  const tools = {
    web_search: { stage: 1, name: '网页检索 · DeepSeek Search', icon: 'globe' },
    rag_search: { stage: 1, name: '知识库检索 · RAG', icon: 'layers' },
    text2sql: { stage: 2, name: 'Text2SQL · 能源数据库', icon: 'database' },
    data_analysis: { stage: 2, name: '数据查询与制图', icon: 'chart' },
  };
  const saved = new WeakMap();
  const revisionTimers = new WeakMap();
  const citation = /\[((?:E|D)\d+)\]/g;
  const capacityMessage = '当前两个并行名额均在使用，请稍后重试。';
  const scoutToolLogs = new Set(Object.values(tools).filter(tool => tool.stage === 1)
    .flatMap(tool => ['开始', '完成', '失败'].map(status => tool.name + '：' + status)));
  const localPage = /^https?:$/.test(location.protocol);
  const defaults = localPage ? location.origin + '/api' : 'http://localhost:8003';
  state.apiUrl = defaults;
  let personalKey = '';
  const keyTransportAllowed = location.protocol === 'https:' ||
    (location.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname));

  const original = { stageActivity, stageTasks, stageSpecific, outputSignature, nodeState, sendChat, selectProject,
    saveSettingsDialog, confirmReset, exportReport, previewSource, renderAll, stopRun, deleteProject, discardSource, restoreSource };
  const chat = document.getElementById('chatInput');
  const panel = document.getElementById('evidencePanel');
  const evidenceToggle = document.getElementById('relay-evidence-toggle');
  const projectHead = document.getElementById('projectHead');
  const map = document.getElementById('workflowMap');

  function alignWorkspace() {
    const header = projectHead.getBoundingClientRect();
    const diagram = map.getBoundingClientRect();
    if (!header.height || !diagram.height) return;
    const style = document.documentElement.style;
    const scale = Number(getComputedStyle(panel).zoom);
    const positions = { '--relay-evidence-top': diagram.top / scale, '--relay-evidence-height': diagram.height / scale,
      '--relay-toolbar-top': (header.bottom + 8) / scale };
    for (const [key, value] of Object.entries(positions)) style.setProperty(key, value + 'px');
  }

  const layoutObserver = new ResizeObserver(alignWorkspace);
  layoutObserver.observe(projectHead);
  layoutObserver.observe(map);
  window.addEventListener('resize', alignWorkspace);
  evidenceToggle.addEventListener('click', () => {
    const collapsed = panel.classList.toggle('relay-evidence-collapsed');
    evidenceToggle.setAttribute('aria-expanded', String(!collapsed));
    const label = collapsed ? '展开资料栏' : '收起资料栏';
    evidenceToggle.setAttribute('aria-label', label);
    evidenceToggle.title = label;
    if (collapsed) {
      state.dropdown = false;
      document.getElementById('libraryDropdown').classList.add('hide');
    }
  });

  function updateChatPrompt() {
    const project = proj();
    const review = state.mode === 'real' && !project.revisionQuestion && project.phase === 'awaiting' && context(project)?.review;
    const label = review ? '输入人工审核意见' : '输入新的研究问题';
    chat.setAttribute('aria-label', label);
    chat.placeholder = label + '（Enter 发送）';
  }

  stopRun = () => {
    const project = proj();
    const phase = project.phase;
    original.stopRun();
    if (project.relayRun) project.phase = phase;
  };

  renderAll = focus => {
    if (state.mode === 'real') {
      const run = context(proj());
      state.running = Boolean(run && !run.terminal && !['paused', 'resuming'].includes(run.info.control));
    }
    original.renderAll(focus);
    syncEvidenceCounts(proj());
    updateChatPrompt();
  };

  function context(project) {
    let run = saved.get(project);
    if (!run && project.relayRun) {
      run = { cursor: 0, snapshot: {}, pending: new Map(), tools: {}, review: null, report: project.report,
        outputVersions: {}, restoring: Boolean(project.relayRun.id),
        terminal: Boolean(project.relayRun.terminal), info: project.relayRun };
      saved.set(project, run);
    }
    return run;
  }

  function apiBase(value = state.apiUrl) {
    const url = new URL(value || defaults, location.href);
    if (!/^https?:$/.test(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error('请输入 HTTP 或 HTTPS 的 API 地址。');
    }
    const base = url.href.replace(/\/$/, '');
    return localPage && base === location.origin ? defaults : base;
  }

  async function json(url, options) {
    const response = await fetch(url, options);
    const type = response.headers.get('content-type') || '';
    const data = type.includes('json') ? await response.json() : null;
    if (!response.ok || !data) {
      const detail = data?.detail;
      const message = {
        'Research capacity reached; retry later': capacityMessage,
        'Research start rate limit reached; retry shortly': '启动请求过于频繁，请稍等片刻再试。',
      }[typeof detail === 'string' ? detail : ''];
      const error = new Error(message || (typeof detail === 'string' ? detail :
        detail?.message || (response.ok ? 'API 响应格式异常，请检查连接地址。' : 'HTTP ' + response.status)));
      error.status = response.status;
      throw error;
    }
    return data;
  }

  async function post(url, body, key = '') {
    const headers = { 'Content-Type': 'application/json' };
    if (key) {
      if (!keyTransportAllowed || new URL(url, location.href).origin !== location.origin) {
        throw new Error('请通过 HTTPS 或本机工作台使用 DeepSeek API Key。');
      }
      headers['X-DeepSeek-Api-Key'] = key;
    }
    return json(url, {
      method: 'POST', headers, body: JSON.stringify(body), redirect: key ? 'error' : 'follow',
    });
  }
  const selected = project => state.mode === 'real' && project === proj();
  const entry = (run, route) => run.info.base + '/research/runs/' + encodeURIComponent(run.info.id) + route;

  function syncEvidenceCounts(project) {
    const sources = new Set(project.sources.map(source => source.id));
    const report = project.report;
    const draft = project.revisionQuestion ? null : context(project)?.snapshot.draft_sections;
    const sections = report ? [report.summary, ...(report.sections || []).map(section => section.content)]
      : Object.values(draft || {});
    const labels = new Set(sections.flatMap(section =>
      [...String(section || '').matchAll(citation)].map(match => match[1])));
    const cited = new Set(project.sources.filter(source => source.relaySource && labels.has(source.label))
      .map(source => source.id));
    document.getElementById('evidenceTotal').textContent = sources.size;
    document.getElementById('evidenceCited').textContent = cited.size;
  }

  function refresh(project, before, ...cards) {
    if (!selected(project)) return;
    updateChatPrompt();
    const run = context(project);
    if (run.paint) {
      cards.forEach(card => run.paint.cards.add(card));
      return;
    }
    const batch = run.paint = { cards: new Set(cards) };
    requestAnimationFrame(() => {
      run.paint = null;
      if (!selected(project) || context(project) !== run) return;
      updateResearchEvent(project, before, project.stage);
      syncEvidenceCounts(project);
      if (state.view === 'workspace' && renderedProject === project.id) paintNodes(project, batch.cards);
      scheduleEdges();
    });
  }

  function step(project, index) {
    if (!Number.isInteger(index)) return;
    if (project.stage !== index) openNode(project, index);
    project.stage = index;
    if (project.autoFollow) {
      project.focus = project.expanded = index;
    }
    project.done = project.done.filter(stage => stage !== index);
  }

  function completeStep(project, index) {
    if (Number.isInteger(index) && !project.done.includes(index)) project.done.push(index);
    project.progress = Math.min(96, Math.round(project.done.length * 100 / STAGES.length));
  }

  function setEvidence(project, evidence) {
    const previous = new Map(project.sources.filter(item => item.relaySource).map(item => [item.id, item]));
    const items = Object.entries(evidence || {}).map(([label, item]) => {
      const identity = item.chunk_id ?? item.url ?? item.source ?? label;
      const id = 'relay:' + String(item.source_type || 'evidence') + ':' + String(identity) + ':' + (item.source || '');
      const old = previous.get(id);
      const kind = item.source_type === 'sql' ? '数据资料' : item.source_type === 'web' ? '网页资料' : '知识库资料';
      return { ...old, id, label, relaySource: true, title: item.title || item.source || label, publisher: kind,
        date: item.date || '本轮研究', kind, url: item.url || '', tags: [label],
        abstract: item.text || item.snippet || '', sourceType: item.source_type, chunkId: item.chunk_id, sql: item.sql, stage: item.source_type === 'sql' ? 2 : 1,
        status: old?.status || 'accepted' };
    });
    const ids = new Set(items.map(item => item.id));
    project.sources = [...items, ...project.sources.filter(item =>
      !item.relayDemo && !ids.has(item.id) && (!item.relaySource || item.status === 'discarded'))];
  }

  function applyState(project, run, delta) {
    Object.assign(run.snapshot, delta);
    if (Number.isInteger(delta.iteration)) project.iteration = delta.iteration;
    if (typeof delta.quality_score === 'number' && delta.phase !== 'planning') {
      project.reviewScore = delta.quality_score;
    }
    if (Object.hasOwn(delta, 'evidence_frozen')) setEvidence(project, delta.evidence_frozen);
  }

  function note(project, stage, message, symbol) {
    const start = (project.logs[stage] || []).length;
    appendLog(project, stage, message, symbol);
    for (const item of project.logs[stage].slice(start)) item.relayEvent = true;
    project.history.at(-1).relayEvent = true;
  }

  function reviseOutput(run, ...cards) {
    for (const card of cards) run.outputVersions[card] = (run.outputVersions[card] || 0) + 1;
  }

  function saveTerminal(project, run, phaseName) {
    run.terminal = run.info.terminal = true;
    run.review = null;
    delete run.info.selection;
    delete run.info.control;
    project.phase = project.revisionQuestion ? 'revising' : phaseName;
    run.source?.close();
    if (selected(project)) state.running = false;
    syncSidebarState(project);
  }

  function reportFromSnapshot(snapshot) {
    if (!snapshot.final_answer) return null;
    return { title: snapshot.question, summary: snapshot.draft_sections?.summary,
      sections: (snapshot.outline || []).map(section => ({
        title: section.title, content: snapshot.draft_sections?.[section.id],
      })).filter(section => section.content), charts_data: snapshot.charts_data,
      references: snapshot.references };
  }

  function applyEvent(project, run, event) {
    const before = selected(project) ? eventSnapshot(project) : null;
    const payload = event.payload || {};
    const stage = stages[event.node];
    const tool = tools[payload.tool];
    const changed = new Set([stage ?? project.stage]);
    switch (event.type) {
      case 'run_started':
        if (payload.state) {
          run.info.question ||= payload.state.question;
          applyState(project, run, payload.state);
        }
        break;
      case 'node_started':
        run.activeNode = event.node;
        if (project.phase !== 'awaiting') project.phase = 'running';
        step(project, stage);
        if (Number.isInteger(stage)) note(project, stage, '开始执行 ' + event.node, 'play');
        break;
      case 'node_completed':
        if (run.activeNode === event.node) run.activeNode = null;
        run.lastCompletedNode = event.node;
        if (event.node !== 'lead_writer' && event.node !== 'evidence_gate' && event.node !== 'router') {
          completeStep(project, stage);
        }
        if (Number.isInteger(stage)) note(project, stage, '已完成 ' + event.node);
        break;
      case 'state_updated':
        if (payload.delta) applyState(project, run, payload.delta);
        if (event.node === 'critic_master') run.criticQueries = payload.delta?.pending_queries || [];
        if (event.node === 'deep_scout') changed.add(2);
        if (Number.isInteger(stage)) reviseOutput(run, ...changed);
        if (event.node === 'human_gate') {
          const decision = payload.delta?.user_decision;
          if (decision) {
            run.lastReview = { issue_summary: run.review?.issue_summary, decision,
              feedback: payload.delta.review_feedback?.feedback };
            run.activeNode = null;
            run.lastCompletedNode = event.node;
            note(project, 5, '审核决定：' + decision);
          }
          run.review = null;
          delete run.info.selection;
          if (project.phase === 'awaiting') project.phase = 'running';
        }
        break;
      case 'tool_started':
      case 'tool_completed':
      case 'tool_failed':
        if (tool) {
          const activity = run.tools[payload.tool] ||= { pending: 0, ok: 0, failed: 0 };
          if (tool.stage === 2) run.dataToolFinished = event.type !== 'tool_started';
          if (event.type === 'tool_started') {
            activity.pending++;
            if (tool.stage === 2) step(project, 2);
          } else {
            activity.pending = Math.max(0, activity.pending - 1);
            if (event.type === 'tool_completed') {
              activity.ok++;
              if (tool.stage === 2) completeStep(project, 2);
            } else activity.failed++;
          }
          changed.add(tool.stage);
          if (tool.stage !== 1) note(project, tool.stage, tool.name + '：' +
            ({ tool_started: '开始', tool_completed: '完成', tool_failed: '失败' })[event.type]);
        }
        break;
      case 'branch_selected': {
        const target = stages[payload.target];
        if (Number.isInteger(stage) && Number.isInteger(target)) {
          if (stage === 5 && run.lastReview && [0, 1, 3].includes(target)) {
            run.lastReview.target = STAGES[target].en;
          }
          note(project, stage, '转到 ' + STAGES[target].en + ' · ' + (payload.reason || '流程分支'), 'play');
        }
        if (event.node === 'evidence_gate' && payload.target === 'critic_master') completeStep(project, 3);
        break;
      }
      case 'run_paused':
      case 'run_resumed':
        if (event.type === 'run_paused') run.info.control = 'paused';
        else delete run.info.control;
        if (before) {
          state.running = event.type === 'run_resumed';
          renderToolbar();
          syncNodeDetail();
        }
        syncSidebarState(project);
        reviseOutput(run, 5);
        changed.add(5);
        save();
        break;
      case 'awaiting_review':
        run.review = { id: payload.review_id, ...payload };
        if (run.info.selection?.id === run.review.id) Object.assign(run.review, run.info.selection);
        step(project, 5);
        project.phase = 'awaiting';
        reviseOutput(run, 5);
        note(project, 5, '等待人工审核');
        break;
      case 'run_completed':
        if (run.snapshot.final_answer && !run.terminal) {
          run.activeNode = null;
          step(project, 7);
          changed.add(7);
        }
        break;
      case 'node_failed':
        if (run.activeNode === event.node) run.activeNode = null;
        if (Number.isInteger(stage)) note(project, stage, '执行失败：' + (payload.error_type || event.node));
        break;
      case 'done':
        if (project.revisionQuestion) {
          saveTerminal(project, run, 'revising');
          break;
        }
        project.report = run.report || reportFromSnapshot(run.snapshot);
        if (project.report) {
          step(project, 7);
          completeStep(project, 7);
          project.progress = 100;
          reviseOutput(run, 3, 7);
          changed.add(3);
        }
        saveTerminal(project, run, project.report ? 'finished' : 'failed');
        break;
      case 'error':
        if (!project.revisionQuestion) note(project, project.stage, payload.content || '研究失败');
        saveTerminal(project, run, 'failed');
        break;
      default:
        break;
    }
    if (before) refresh(project, before, ...changed, project.stage);
    else {
      syncSidebarState(project);
      scheduleSave();
    }
  }

  function feed(project, run, events) {
    for (const event of events) {
      if (event.run_id !== run.info.id || event.session_id !== run.info.sessionId ||
          !Number.isInteger(event.seq) || event.seq <= run.cursor) continue;
      run.pending.set(event.seq, event);
    }
    while (run.pending.has(run.cursor + 1)) {
      if (run.restoring) {
        project.logs = Object.fromEntries(Object.entries(project.logs).map(([stage, logs]) => [
          stage, logs.filter(log => !log.relayEvent),
        ]));
        project.history = project.history.filter(event => !event.relayEvent);
        project.nodeIterationHistory = {};
        project.done = [];
        run.restoring = false;
      }
      const event = run.pending.get(++run.cursor);
      run.pending.delete(run.cursor);
      applyEvent(project, run, event);
    }
    return run.pending.size > 0;
  }

  function recover(project, run) {
    if (!run.info.id) return Promise.resolve();
    if (run.recovery) return run.recovery;
    run.recovery = json(entry(run, '/state?after_seq=' + run.cursor))
      .then(data => {
        if (saved.get(project) !== run || !state.projects.includes(project)) return;
        const restoring = run.restoring;
        feed(project, run, data.events || []);
        if (restoring && !run.terminal && typeof data.pause_requested === 'boolean') {
          if (data.pause_requested && run.info.control !== 'paused') run.info.control = 'pausing';
          else if (!data.pause_requested && run.info.control === 'pausing') delete run.info.control;
          if (selected(project)) renderAll();
          save();
        }
      })
      .catch(error => {
        if (error.status === 404 && !run.terminal && saved.get(project) === run) {
          failRun(project, run, '这次运行的记录已过期，可重新运行。');
        }
      }).finally(() => { run.recovery = null; });
    return run.recovery;
  }

  function attach(project, run) {
    if (run.terminal || !run.info.id || (run.source && run.source.readyState !== EventSource.CLOSED)) return;
    const source = new EventSource(entry(run, '/events?after_seq=' + run.cursor));
    run.source = source;
    source.onmessage = ({ data }) => {
      if (run.source !== source || run.terminal) return;
      let event;
      try { event = JSON.parse(data); } catch { return; }
      if (feed(project, run, [event])) void recover(project, run);
    };
    source.onerror = () => {
      if (run.source === source && !run.terminal) void recover(project, run);
    };
    void recover(project, run);
  }

  async function discover(project, run) {
    if (run.discovering) return;
    run.discovering = true;
    const until = Date.now() + 90000;
    try {
      while (!run.info.id && !run.terminal && saved.get(project) === run && Date.now() < until) {
        try {
          const data = await json(run.info.base + '/research/session/' + encodeURIComponent(run.info.sessionId) + '/run');
          run.info.id = data.run_id;
          if (selected(project)) renderToolbar();
          save();
          attach(project, run);
        } catch (error) {
          if (error.status !== 404) throw error;
          await new Promise(resolve => setTimeout(resolve, 300));
        }
      }
      if (!run.info.id && !run.terminal && !run.submitting && saved.get(project) === run) {
        failRun(project, run, '后端没有找到这次运行，可重新提交。');
      }
    } catch (error) {
      if (selected(project)) toast('读取运行失败：' + error.message);
    } finally {
      run.discovering = false;
    }
  }

  function watch(project, run) {
    if (run.info.id) {
      if (run.terminal && run.cursor === 0) void recover(project, run);
      else attach(project, run);
    } else void discover(project, run);
  }

  function failRun(project, run, message) {
    const before = eventSnapshot(project);
    note(project, project.stage, '研究失败：' + message, 'alert');
    saveTerminal(project, run, 'failed');
    if (selected(project)) toast(message);
    refresh(project, before, project.stage);
    save();
  }

  async function finishReport(project, run, question) {
    run.submitting = true;
    try {
      const report = await post(run.info.base + '/research/report', {
        question, session_id: run.info.sessionId, demo_mode: false,
        excluded_sources: Object.values(project.excludedSources || {}),
      }, personalKey);
      if (saved.get(project) !== run || !state.projects.includes(project)) return;
      run.report = report;
      run.info.id ||= report.run_id;
      await recover(project, run);
      if (project.revisionQuestion) return;
      const before = eventSnapshot(project);
      project.report = report;
      step(project, 7);
      completeStep(project, 7);
      project.progress = 100;
      reviseOutput(run, 3, 7);
      saveTerminal(project, run, 'finished');
      refresh(project, before, 3, 7);
    } catch (error) {
      if (saved.get(project) !== run || !state.projects.includes(project)) return;
      if (error.status === 429 && run.info.replacing && !run.info.id) {
        project.revisionQuestion ||= question;
        saveTerminal(project, run, 'revising');
        if (selected(project)) renderAll();
        queueRevision(project, 10000);
        return;
      }
      if (run.info.id) await recover(project, run);
      if (!run.terminal && (!run.info.id || (error.status >= 400 && error.status < 500))) {
        failRun(project, run, error.message);
      } else if (run.terminal && [401, 402, 403].includes(error.status) && selected(project)) {
        toast(error.message);
      }
    } finally {
      run.submitting = false;
      save();
    }
  }

  async function changeRunControl(project, run) {
    if (!run.info.id || ['pausing', 'resuming'].includes(run.info.control)) return;
    const previous = run.info.control;
    const action = previous === 'paused' ? 'resume' : 'pause';
    run.info.control = action === 'pause' ? 'pausing' : 'resuming';
    save();
    renderAll();
    try {
      await post(entry(run, '/control'), { session_id: run.info.sessionId, action });
    } catch (error) {
      if (!run.terminal) {
        if (previous) run.info.control = previous;
        else delete run.info.control;
        void recover(project, run);
        if (selected(project)) toast('未能' + (action === 'pause' ? '暂停' : '继续') + '研究：' + error.message);
      }
      save();
      if (selected(project)) renderAll();
    }
  }

  runReal = () => {
    const project = proj();
    if (!project.id || project.revisionQuestion) return;
    const existing = context(project);
    if (existing && !existing.terminal) {
      void changeRunControl(project, existing);
      return;
    }
    const question = existing?.info.question || [project.title, project.desc === '待补充研究问题。' ? '' : project.desc]
      .map(value => value?.trim()).filter(Boolean).join('\n\n');
    startResearch(project, question);
  };

  function startResearch(project, question, newProject = false, replacing = false) {
    const active = state.projects.filter(item => {
      const run = context(item);
      return run && !run.terminal;
    });
    if (active.length >= 2) {
      const toasts = document.querySelectorAll('#toastLayer .toast');
      if (![...toasts].some(item => item.textContent === capacityMessage)) toast(capacityMessage);
      return null;
    }
    let base;
    try { base = apiBase(); } catch (error) { toast(error.message); return null; }
    if (newProject) {
      const title = Array.from(question.split('\n')[0].trim()).slice(0, 60).join('');
      project = createProject(title, '新研究项目', question);
    }
    const sessionId = 'relay_' + ID() + Date.now().toString(36);
    project.relayRun = { base, sessionId, id: null, terminal: false, question, replacing };
    const run = contextAfterReset(project);
    project.sources = project.sources.filter(item => !item.relayDemo && (!item.relaySource || item.status === 'discarded'));
    Object.assign(project, { stage: 0, focus: 0, expanded: 0, expandedNodeIds: [0],
      manuallyCollapsedNodeIds: [], nodeIterationHistory: {}, done: [], logs: {}, history: [],
      report: null, reviewScore: null, iteration: 0, progress: 0, autoFollow: true, phase: 'running' });
    watch(project, run);
    renderAll(true);
    save();
    void finishReport(project, run, question);
    return project;
  }

  function exclusionFor(source) {
    const sql = source.sql || '';
    const isSql = sql || source.sourceType === 'sql' || source.id.startsWith('relay:sql:');
    if (isSql) return sql && source.abstract ? { sql, text: source.abstract } : null;
    const location = source.url || source.title;
    const chunk = source.chunkId;
    return location || chunk ? { location: location || '', chunk_id: chunk == null ? '' : String(chunk) } : null;
  }

  function queueRevision(project, delay = 600) {
    clearTimeout(revisionTimers.get(project));
    revisionTimers.set(project, setTimeout(async () => {
      if (!state.projects.includes(project) || !project.revisionQuestion || state.mode !== 'real') return;
      const run = context(project);
      if (run && !run.terminal && run.info.id && !run.supersedeRequested) {
        run.supersedeRequested = true;
        try {
          await post(entry(run, '/supersede'), { session_id: run.info.sessionId });
        } catch (error) {
          run.supersedeRequested = false;
          if (error.status === 404) void recover(project, run);
          else if (selected(project)) toast('暂时无法更新运行，正在重试。');
        }
      }
      if (!project.revisionQuestion || context(project) !== run) return;
      const active = state.projects.filter(item => {
        const current = context(item);
        return current && !current.terminal;
      });
      if ((run && (!run.terminal || run.submitting)) || active.length >= 2) {
        queueRevision(project, 1000);
        return;
      }
      const question = project.revisionQuestion;
      delete project.revisionQuestion;
      if (!startResearch(project, question, false, true)) {
        project.revisionQuestion = question;
        queueRevision(project, 1500);
      }
    }, delay));
  }

  function reviseForSource(project) {
    const run = context(project);
    if (!run) return;
    project.revisionQuestion ||= run.info.question || run.snapshot.question ||
      project.report?.title || project.title;
    project.report = null;
    project.phase = 'revising';
    save();
    renderAll();
    queueRevision(project);
  }

  discardSource = id => {
    const project = proj();
    const source = project.sources.find(item => item.id === id && item.status !== 'discarded');
    if (state.mode === 'real' && source && !source.relayDemo) {
      const rule = exclusionFor(source);
      if (!rule) return toast('这份资料缺少可用于排除的来源标识。');
      (project.excludedSources ||= {})[id] = rule;
    }
    original.discardSource(id);
    if (state.mode === 'real' && project.excludedSources?.[id]) reviseForSource(project);
  };

  restoreSource = id => {
    const project = proj();
    const excluded = Object.hasOwn(project.excludedSources || {}, id);
    if (excluded) delete project.excludedSources[id];
    original.restoreSource(id);
    if (state.mode === 'real' && excluded) reviseForSource(project);
  };

  function contextAfterReset(project) {
    saved.delete(project);
    const run = context(project);
    run.report = null;
    return run;
  }

  async function sendReview(project, payload) {
    const run = context(project);
    const review = run?.review;
    if (project.revisionQuestion || !review || run.terminal || run.sending) return;
    if (run.info.control) return toast('请继续研究后再提交审核意见。');
    run.sending = true;
    syncNodeDetail();
    if (renderedProject === project.id && state.view === 'workspace') paintNodes(project, new Set([5]));
    if (payload.feedback) project.chat.push({ role: 'user', text: payload.feedback, time: stamp() });
    try {
      const result = await post(run.info.base + '/research/decision', {
        session_id: run.info.sessionId, run_id: run.info.id, review_id: review.id, ...payload,
      }, payload.feedback ? personalKey : '');
      if (run.review !== review) return;
      if (result.status === 'selection_required') {
        run.info.selection = { id: review.id, options: result.options, message: result.message };
        Object.assign(review, run.info.selection);
        project.chat.push({ role: 'assistant', text: result.message, time: stamp() });
      } else if (result.status === 'ok') {
        review.submitted = true;
        delete run.info.selection;
        delete review.options;
        if (selected(project)) toast('审核决定已提交。');
      }
    } catch (error) {
      if (selected(project)) toast('审核提交失败：' + error.message);
      if (error.status === 409) void recover(project, run);
    } finally {
      run.sending = false;
      reviseOutput(run, 5);
      if (selected(project)) {
        syncNodeDetail();
        if (state.view === 'chat') renderOtherView();
        else if (renderedProject === project.id) paintNodes(project, new Set([5]));
      }
      save();
    }
  }

  sendChat = () => {
    if (state.mode !== 'real') return original.sendChat();
    const question = chat.value.trim();
    const project = proj();
    if (!question) return;
    const run = context(project);
    if (run && !run.terminal && project.phase === 'awaiting' && run.review && !project.revisionQuestion) {
      if (run.sending || run.review.submitted) return;
      if (run.info.control) return toast('请继续研究后再提交审核意见。');
      chat.value = '';
      void sendReview(project, { feedback: question });
      return;
    }
    const started = startResearch(project, question, !project.id || Boolean(run && !run.terminal));
    if (started) {
      started.chat.push({ role: 'user', text: question, time: stamp() });
      chat.value = '';
      save();
    }
  };

  function referenceText(value) {
    return escapeHTML(String(value || '')).replace(citation,
      (_, label) => '<button class="btn tiny" data-relay-citation="' + label + '">[' + label + ']</button>')
      .replace(/\n/g, '<br>');
  }

  function tableCells(line) {
    return line.trim().replace(/^\||(?<!\\)\|$/g, '').split(/(?<!\\)\|/)
      .map(cell => cell.trim().replace(/\\\|/g, '|'));
  }

  function documentMarkup(value) {
    const lines = String(value || '').split(/\r?\n/);
    const blocks = [];
    let paragraph = [];
    const flushParagraph = () => {
      if (paragraph.length) blocks.push('<p>' + referenceText(paragraph.join('\n')) + '</p>');
      paragraph = [];
    };
    for (let index = 0; index < lines.length; index++) {
      const line = lines[index];
      const headers = tableCells(line);
      const alignment = tableCells(lines[index + 1] || '');
      if (line.includes('|') && headers.length === alignment.length &&
          alignment.every(cell => /^:?-{3,}:?$/.test(cell))) {
        flushParagraph();
        const align = alignment.map(cell => cell.endsWith(':') ? (cell.startsWith(':') ? 'center' : 'right') : 'left');
        const row = (cells, tag) => '<tr>' + headers.map((_, column) =>
          '<' + tag + (tag === 'th' ? ' scope="col"' : '') + ' class="align-' + align[column] + '">' +
          referenceText(cells[column]) + '</' + tag + '>').join('') + '</tr>';
        const body = [];
        index += 2;
        while (index < lines.length && lines[index].trim() && lines[index].includes('|')) {
          const cells = tableCells(lines[index]);
          if (cells.length > headers.length) cells.splice(headers.length - 1, Infinity, cells.slice(headers.length - 1).join(' | '));
          body.push(row(cells, 'td'));
          index++;
        }
        index--;
        blocks.push('<div class="relay-table-scroll"><table><thead>' + row(headers, 'th') +
          '</thead><tbody>' + body.join('') + '</tbody></table></div>');
      } else if (line.trim()) {
        paragraph.push(line);
      } else {
        flushParagraph();
      }
    }
    flushParagraph();
    return blocks.join('');
  }

  function chartImage(chart) {
    if (typeof chart.image_b64 === 'string' && /^[A-Za-z0-9+/]+={0,2}$/.test(chart.image_b64)) {
      return 'data:image/png;base64,' + chart.image_b64;
    }
    return safeUrl(chart.image_url || chart.path || '');
  }

  function chartsMarkup(charts = []) {
    return charts.map(chart => {
      const image = chartImage(chart);
      const points = (chart.data || []).map(point =>
        '<p>' + escapeHTML(String(point.label ?? '')) + ' · ' + escapeHTML(String(point.value ?? '')) + '</p>').join('');
      return '<div class="article-draft"><h4>' + escapeHTML(String(chart.title || '数据图表')) + '</h4>' +
        (image ? '<img loading="lazy" style="max-width:100%;height:auto" src="' + escapeHTML(image) + '" alt="' +
          escapeHTML(String(chart.title || '数据图表')) + '">' : '') + points + '</div>';
    }).join('');
  }

  function reportMarkup(report) {
    const references = (report.references || []).map(reference => {
      const title = escapeHTML(String(reference.title || reference.source || '来源'));
      const label = escapeHTML(String(reference.label || ''));
      const url = safeUrl(reference.url || '');
      return '<p>' + (label ? '[' + label + '] ' : '') + (url ? '<a href="' +
        escapeHTML(url) + '" target="_blank" rel="noopener noreferrer">' + title + '</a>' : title) + '</p>';
    }).join('');
    return '<article class="report-paper"><div class="pill">真实后端返回 · Report</div><h2>' +
      escapeHTML(String(report.title || '研究报告')) + '</h2><h3>执行摘要</h3>' + documentMarkup(report.summary) + (report.sections || []).map(section => '<h3>' + escapeHTML(String(section.title || '章节')) +
        '</h3>' + documentMarkup(section.content)).join('') + chartsMarkup(report.charts_data) +
      '<h3>参考文献</h3>' + references + '<div class="stage-footer"><button class="btn primary" data-action="exportReport">' +
      icon('download', 15) + ' 导出 Markdown</button></div></article>';
  }

  nodeState = (project, index) => {
    if (state.mode !== 'real' || !project.relayRun) return original.nodeState(project, index);
    if (index === project.stage && project.relayRun.control === 'paused') return 'paused';
    if (index === 2 && (project.stage > 2 || project.phase === 'finished')) {
      const snapshot = context(project)?.snapshot;
      const hasData = snapshot?.data_points?.length || snapshot?.charts_data?.length ||
        project.report?.charts_data?.length || project.sources.some(source => source.relaySource && source.stage === 2);
      if (!hasData) return 'skipped';
    }
    if (project.phase === 'finished') return project.done.includes(index) ? 'complete' : 'future';
    return original.nodeState(project, index);
  };

  stageActivity = (project, index) => {
    if (state.mode !== 'real') return original.stageActivity(project, index);
    const run = context(project);
    if (project.stage !== index || !run || run.terminal) return null;
    const control = run.info.control;
    if (control === 'paused') return { text: '已暂停，等待继续研究' };
    if (control === 'pausing') return { text: '正在等待后端暂停当前步骤', busy: true };
    if (control === 'resuming') return { text: '正在等待后端恢复研究', busy: true };
    if (project.phase === 'awaiting' && run.review) {
      if (run.sending) return { text: '正在提交审核意见，等待后端处理', busy: true };
      if (run.review.submitted) return { text: '审核决定已提交，等待后端继续', busy: true };
      return { text: run.review.options ? '等待你选择审核处理方式' : '等待你提交人工审核意见' };
    }
    if (!run.info.id) return { text: '正在提交研究，等待后端启动', busy: true };
    const pending = Object.entries(run.tools).filter(([id, activity]) => tools[id].stage === index && activity.pending);
    if (pending.length) {
      const results = pending.map(([id, activity]) => toolWork[id] + (activity.pending > 1 ? '（' + activity.pending + ' 项）' : ''));
      return { text: '正在等待' + results.join('、'), busy: true };
    }
    if (run.activeNode && stages[run.activeNode] === index) return { text: nodeWork[run.activeNode], busy: true };
    if (index === 2 && run.dataToolFinished) return { text: '数据工具已结束，等待后端推进', busy: true };
    if (index === 7) return { text: '正在生成最终报告，等待后端返回', busy: true };
    const text = stages[run.lastCompletedNode] === index ? '当前步骤已完成，等待后端推进'
      : '等待后端开始' + STAGES[index].cn;
    return { text, busy: true };
  };

  outputSignature = (project, index, stageState) => {
    if (state.mode !== 'real') return original.outputSignature(project, index, stageState);
    const run = context(project);
    return [index, stageState, run?.info.sessionId, run?.info.control, Boolean(project.revisionQuestion),
      run?.outputVersions[index] || 0].join(':');
  };

  function writerSections(project, snapshot) {
    if (project.revisionQuestion) return [];
    const drafts = snapshot?.draft_sections || {};
    if (!Object.keys(drafts).length && project.report) {
      return [{ title: '执行摘要', content: project.report.summary }, ...(project.report.sections || [])];
    }
    const outline = snapshot?.outline || [];
    if (!outline.length && !drafts.summary) return [];
    return [{ title: '执行摘要', content: drafts.summary },
      ...outline.filter(section => section.id !== 'summary').map(section => ({
        title: section.title || section.id, content: drafts[section.id],
      }))];
  }

  function writerToolDetails(project, sections) {
    if (!sections.length) return ['研究大纲尚未返回。', '草稿尚未返回引用。'];
    const chapterList = '<ol>' + sections.map(section => '<li>' + escapeHTML(section.title) +
      (section.content ? '' : ' · 待生成') + '</li>').join('') + '</ol>';
    const byChapter = sections.map(section => ({
      title: section.title,
      labels: [...new Set([...String(section.content || '').matchAll(citation)].map(match => match[1]))],
    })).filter(section => section.labels.length);
    if (!byChapter.length) {
      return [chapterList, sections.some(section => section.content) ? '当前草稿没有标注引用。' : '草稿尚未返回引用。'];
    }
    const total = new Set(byChapter.flatMap(section => section.labels)).size;
    const available = new Set(project.sources.filter(source => source.relaySource && source.status === 'accepted')
      .map(source => source.label));
    const citations = '<p>共引用 ' + total + ' 份资料，分布在 ' + byChapter.length + ' 个章节</p><ul>' +
      byChapter.map(section => '<li><strong>' + escapeHTML(section.title) + '</strong> ' +
        section.labels.map(label => available.has(label) ? referenceText('[' + label + ']') : '[' + label + ']').join(' ') +
        '</li>').join('') + '</ul>';
    return [chapterList, citations];
  }

  function shortened(value, limit = 100) {
    const text = String(value ?? '').replace(/\s+/g, ' ').trim();
    return text.length > limit ? text.slice(0, limit) + '…' : text;
  }

  function summaryLine(label, value) {
    return value ? '<p><strong>' + escapeHTML(label) + '</strong> ' + escapeHTML(shortened(value)) + '</p>' : '';
  }

  function summaryList(label, values, limit = 3) {
    const items = [...new Set(values.filter(Boolean))];
    if (!items.length) return '';
    const remaining = items.slice(limit);
    const disclosure = remaining.length ? '<details class="relay-summary-more" data-summary-list="' +
      escapeHTML(label) + '"><summary><span class="relay-more-open">另有 ' + remaining.length +
      ' 项</span><span class="relay-more-close">收起其余 ' + remaining.length + ' 项</span></summary><ul>' +
      remaining.map(item => '<li>' + escapeHTML(String(item)) + '</li>').join('') + '</ul></details>' : '';
    return '<p><strong>' + escapeHTML(label) + '</strong> · ' + items.length + ' 项</p><ul>' +
      items.slice(0, limit).map(item => '<li>' + escapeHTML(shortened(item)) + '</li>').join('') + '</ul>' +
      disclosure;
  }

  function scoutSummary(project, run, id, sourceType) {
    const activity = run?.tools[id];
    const sources = project.sources.filter(source => source.relaySource && source.status === 'accepted' &&
      source.sourceType === sourceType);
    const queries = (run?.snapshot.raw_sources || []).filter(source => source.source_type === sourceType)
      .map(source => source.query);
    return (activity ? summaryLine('检索次数', '完成 ' + activity.ok + ' 次 · 失败 ' + activity.failed + ' 次') : '') +
      summaryList('有返回资料的检索词', queries, 2) +
      summaryList('已采纳资料', sources.map(source => source.title), 2);
  }

  function stageSummaries(project, index, run, status) {
    const snapshot = run?.snapshot || {};
    switch (index) {
      case 0:
        return [
          summaryList('研究子问题', snapshot.research_questions || []) +
            summaryList('待验证假设', snapshot.hypotheses || [], 2),
          summaryList('章节研究要点', (snapshot.outline || []).map(section =>
            [section.title || section.id, section.description || section.query].filter(Boolean).join(' · '))),
        ];
      case 1:
        return [scoutSummary(project, run, 'web_search', 'web'), scoutSummary(project, run, 'rag_search', 'rag')];
      case 2: {
        if (status === 'skipped') return ['已跳过 · 本轮未获得可用数据。', '已跳过 · 本轮未生成图表。'];
        const points = snapshot.data_points || [];
        return [
          summaryList('实际 SQL', points.map(point => point.sql), 1) +
            summaryList('指标与数值', points.filter(point => point.metric && point.value != null)
              .map(point => point.metric + ' · ' + point.value)),
          summaryList('生成图表', (snapshot.charts_data || project.report?.charts_data || []).map(chart => chart.title)),
        ];
      }
      case 3:
        return writerToolDetails(project, writerSections(project, snapshot));
      case 4: {
        const severity = { high: '高', medium: '中', low: '低' };
        const priority = { high: 0, medium: 1, low: 2 };
        const issues = (snapshot.critic_issues || []).slice().sort((a, b) =>
          (priority[a.severity] ?? 3) - (priority[b.severity] ?? 3));
        const score = project.reviewScore?.toFixed(2);
        const queries = run?.criticQueries || snapshot.pending_queries || [];
        return [
          summaryLine('评审评分', score) + summaryList('评审问题', issues.map(issue =>
            [severity[issue.severity], issue.section, issue.description].filter(Boolean).join(' · '))),
          summaryList('要求补查', queries) || (score ? '本轮没有补查要求。' : ''),
        ];
      }
      case 5: {
        const review = run?.review || run?.lastReview;
        const decisions = { approve: '通过', reject: '退回补研', chief_architect: '完全拒绝',
          deep_scout: '重新找资料', lead_writer: '重新写', timeout: '审核超时' };
        return [
          review ? summaryLine('研究问题', snapshot.question || run.info.question) +
            summaryList('待审问题', review.issue_summary?.split('\n') || [], 2) : '',
          summaryLine('提交意见', review?.feedback) + summaryLine('最终决定', decisions[review?.decision]) +
            summaryLine('退回目标', review?.target) || (run?.review ? '等待用户提交审核意见。' : ''),
        ];
      }
      case 6: {
        if (!snapshot.final_answer && !project.report) return [];
        const charts = project.report?.charts_data || snapshot.charts_data || [];
        const refs = project.report?.references || snapshot.references || [];
        return [
          summaryList('汇总章节', writerSections(project, snapshot).filter(section => section.content).map(section => section.title)) +
            (summaryList('纳入图表', charts.map(chart => chart.title), 2) || '本报告未纳入图表。'),
          summaryList('参考文献', refs.map(ref => [ref.label, ref.title || ref.source || ref.url].filter(Boolean).join(' · '))) ||
            '本报告没有参考文献。',
        ];
      }
      case 7: {
        const report = project.report;
        if (!report) return [];
        return [
          summaryLine('报告标题', report.title) + summaryLine('执行摘要', report.summary) +
            summaryList('章节目录', (report.sections || []).map(section => section.title)),
          '<button class="btn tiny" data-action="exportReport">' + icon('download', 15) + ' 导出 Markdown</button>',
        ];
      }
    }
  }

  stageTasks = (project, index) => {
    if (state.mode !== 'real') return original.stageTasks(project, index);
    const run = context(project);
    const status = nodeState(project, index);
    const configuredTools = Object.entries(tools).filter(([, tool]) => tool.stage === index);
    const rows = configuredTools.length ? configuredTools.map(([id, tool]) => [tool.icon, tool.name, id]) : STAGES[index].tools;
    const changing = project.revisionQuestion && [3, 4, 5, 6, 7].includes(index);
    const details = changing ? [] : stageSummaries(project, index, run, status);
    return rows.map(([symbol, name, id], i) => {
      const activity = run?.tools[id];
      const toolStatus = status === 'skipped' ? '已跳过'
        : activity?.pending ? '执行中'
        : activity?.ok ? '已完成'
        : activity?.failed ? '失败'
        : stateText(status);
      const key = index + '-' + i;
      return '<div class="stage-panel"><div class="tool-symbol">' + icon(symbol, 20) +
        '</div><div class="tool-main"><strong>' + escapeHTML(name) + '</strong></div><span class="tool-status">' + toolStatus +
        '</span><button class="tool-chevron" data-action="toolExpand" data-tool="' + key +
        '" title="展开工具详情">' + icon('chevron', 15) + '</button></div><div class="tool-expanded relay-tool-summary hide" id="tool-' +
        key + '">' + (details[i] || (changing ? '正在按资料变更更新。' : '暂无可展示的结果。')) + '</div>';
    }).join('');
  };

  stageSpecific = (project, index) => {
    if (state.mode !== 'real') return original.stageSpecific(project, index);
    const run = context(project);
    const snapshot = run?.snapshot || {};
    if (project.revisionQuestion && [3, 4, 5, 7].includes(index)) {
      return '<div class="future-plan">正在按资料变更重新生成报告。</div>';
    }
    if (index === 0 && snapshot.research_questions?.length) {
      return '<div class="mini-columns"><div class="mini-card"><strong>研究目标</strong><p>' +
        escapeHTML(project.desc) + '</p></div><div class="mini-card"><strong>拆解的子问题</strong><div class="chips">' +
        snapshot.research_questions.map(question => '<span class="chip">' + escapeHTML(String(question)) +
          '</span>').join('') + '</div></div></div>';
    }
    if (index === 1) return '';
    if (index === 2) {
      const sql = [...new Set((snapshot.data_points || []).map(point => point.sql).filter(Boolean))];
      return sql.map(query => '<div class="code-block">' + escapeHTML(query) + '</div>').join('') +
        chartsMarkup(snapshot.charts_data);
    }
    if (index === 3) {
      const content = writerSections(project, snapshot).filter(section => section.content).map(section =>
        '<section class="article-draft"><h4>' + escapeHTML(section.title) +
        '</h4>' + documentMarkup(section.content) + '</section>').join('');
      return content ? '<section class="relay-draft"><h3 id="relay-draft-title">草稿</h3>' +
        '<div class="relay-draft-scroll" role="region" tabindex="0" aria-labelledby="relay-draft-title">' +
        content + '</div></section>' : '';
    }
    if (index === 4) {
      const feedback = (snapshot.guard_feedback || []).map(item => item.detail).filter(Boolean).join('\n');
      const issue = snapshot.issue_summary || feedback;
      return (project.reviewScore == null ? '' : '<div class="review-score"><strong class="score-number">' +
        project.reviewScore.toFixed(2) + '</strong><span class="score-label">评审评分</span></div>') +
        (issue ? '<div class="warning">' + referenceText(issue) + '</div>' : '');
    }
    if (index === 5 && run?.review) {
      const review = run.review;
      const blocked = Boolean(review.submitted || run.sending || run.info.control);
      const disabled = blocked ? ' disabled' : '';
      return '<div class="mini-card" style="margin-top:12px"><strong>Human-in-the-loop · 人工审核</strong><p>' +
        referenceText(review.issue_summary || review.content) + '</p>' +
        (review.options ? '<p>' + escapeHTML(review.message || '') + '</p><div class="stage-footer">' +
          review.options.map(option => '<button class="btn ' + (option.decision === 'approve' ? 'primary' : '') +
            '" data-review-choice="' + escapeHTML(option.decision) + '"' + disabled + '>' +
          escapeHTML(option.label) + '</button>').join('') + '</div>' : reviewControls(blocked)) + '</div>';
    }
    if (index === 5) return '';
    if (index === 6 && project.done.includes(6)) return '<div class="mini-card"><strong>已汇总报告与参考资料</strong></div>';
    if (index === 7 && project.report) return reportMarkup(project.report);
    return '<div class="future-plan">后端执行后会在此显示结果。</div>';
  };

  previewSource = id => {
    original.previewSource(id);
    const source = proj().sources.find(item => item.id === id);
    if (state.mode !== 'real' || !source?.relaySource) return;
    const details = document.querySelector('#modalContent .demo-note:last-of-type');
    if (details) details.textContent = '引用 ' + source.label + (source.url ? ' · ' + source.url : '');
  };

  exportReport = () => {
    if (state.mode !== 'real') return original.exportReport();
    const project = proj(), run = context(project), report = project.report;
    if (!report) return toast('后端报告完成后可以导出。');
    const snapshot = run?.snapshot;
    let body = snapshot?.final_answer;
    if (!body) {
      const lines = ['# ' + report.title, '', '## 执行摘要', report.summary || ''];
      for (const section of report.sections || []) lines.push('', '## ' + section.title, section.content);
      for (const chart of report.charts_data || []) {
        lines.push('', '### ' + chart.title);
        const image = chartImage(chart);
        if (image) lines.push('![](' + image + ')');
        for (const point of chart.data || []) lines.push('- ' + point.label + ': ' + point.value);
      }
      lines.push('', '## 参考资料');
      for (const ref of report.references || []) lines.push('[' + (ref.label || '') + '] ' +
        (ref.title || ref.source || '') + (safeUrl(ref.url || '') ? ' · ' + ref.url : ''));
      body = lines.join('\n');
    }
    const url = URL.createObjectURL(new Blob([body], { type: 'text/markdown;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'RELAY_Research_Report.md';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 15000);
    toast('已导出后端报告');
  };

  document.addEventListener('click', event => {
    const disclosure = event.target.closest('[data-review-disclosure] > summary');
    if (disclosure) {
      if (disclosure.getAttribute('aria-disabled') === 'true') event.preventDefault();
      else if (!disclosure.parentElement.open) {
        requestAnimationFrame(() => disclosure.parentElement.querySelector('textarea')?.focus({ preventScroll: true }));
      }
      return;
    }
    if (event.target.closest('[data-relay-clear-key]')) {
      personalKey = '';
      accountDialog();
      return;
    }
    const choice = event.target.closest('[data-review-choice]');
    const citation = event.target.closest('[data-relay-citation]');
    if (choice) {
      event.preventDefault();
      if (state.mode === 'demo') decideHuman(choice.dataset.reviewChoice);
      else void sendReview(proj(), { decision: choice.dataset.reviewChoice });
    } else if (citation) {
      event.preventDefault();
      const source = proj().sources.find(item => item.relaySource && item.label === citation.dataset.relayCitation);
      if (source) previewSource(source.id);
      else toast('本轮证据中暂时找不到该引用。');
    }
  });

  document.addEventListener('submit', event => {
    if (event.target.matches('[data-relay-key]')) {
      event.preventDefault();
      const input = event.target.querySelector('input');
      const key = input.value.trim();
      if (!/^[A-Za-z0-9_-]{12,256}$/.test(key)) {
        input.setCustomValidity('请输入有效格式的 DeepSeek API Key。');
        input.reportValidity();
        return;
      }
      personalKey = key;
      input.value = '';
      closeModal();
      toast('已为当前页面设置 DeepSeek API Key');
      return;
    }
    if (!event.target.matches('[data-relay-review]')) return;
    event.preventDefault();
    const input = event.target.querySelector('textarea');
    const feedback = input.value.trim();
    if (!feedback) return;
    input.value = '';
    const project = proj();
    if (state.mode === 'demo') {
      if (project.stage !== 5 || project.phase !== 'awaiting') return;
      appendLog(project, 5, '审核意见：' + feedback);
      decideHuman('reject');
    } else void sendReview(project, { feedback });
  });

  document.addEventListener('keydown', event => {
    if (!event.target.matches('[data-relay-review] textarea') || event.key !== 'Enter' || event.shiftKey ||
        event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    event.target.form.requestSubmit();
  });

  document.getElementById('chatInput').addEventListener('keydown', event => {
    if (state.mode === 'real' && event.key === 'Enter' && (event.isComposing || event.keyCode === 229)) {
      event.stopImmediatePropagation();
    }
  }, true);

  accountDialog = () => {
    const description = keyTransportAllowed
      ? '用于研究和人工审核中的模型调用。只在当前页面生效，刷新后需要重新输入。'
      : '请通过 HTTPS 或本机工作台使用 DeepSeek API Key。';
    const form = keyTransportAllowed ? '<form data-relay-key><label class="form-row">DeepSeek API Key' +
      '<input type="password" autocomplete="off" spellcheck="false" maxlength="256" required ' +
      (personalKey ? 'placeholder="输入新密钥以替换"' : '') + '></label><div class="modal-actions">' +
      (personalKey ? '<button type="button" class="btn" data-relay-clear-key>移除密钥</button>' : '') +
      '<button type="submit" class="btn primary">保存</button></div></form>' : '';
    openModal(modalHeader('DeepSeek API Key', 'user') + '<p class="modal-desc">' + description + '</p>' + form);
    const input = modalContent.querySelector('[data-relay-key] input');
    input?.addEventListener('input', () => input.setCustomValidity(''));
  };

  selectProject = id => {
    original.selectProject(id);
    const run = context(proj());
    if (state.mode === 'real' && run) watch(proj(), run);
    if (state.mode === 'real' && proj().revisionQuestion) queueRevision(proj());
  };

  deleteProject = () => {
    const project = state.projects.find(item => item.id === state.deleteId);
    original.deleteProject();
    if (project && !state.projects.includes(project)) {
      context(project)?.source?.close();
      saved.delete(project);
    }
  };

  saveSettingsDialog = () => {
    const previousMode = state.mode;
    original.saveSettingsDialog();
    if (previousMode !== state.mode) stopRun();
    const run = context(proj());
    if (state.mode === 'real') {
      for (const project of state.projects) {
        const entry = context(project);
        if (entry && (!entry.terminal || entry === run)) watch(project, entry);
        if (project.revisionQuestion) queueRevision(project);
      }
    }
    renderAll();
  };

  confirmReset = () => {
    const run = context(proj());
    if (state.mode === 'real' && run && !run.terminal) {
      closeModal();
      return toast('当前研究仍在运行，请先完成本轮。');
    }
    original.confirmReset();
  };

  const project = proj();
  for (const item of state.projects) {
    if (item.logs[1]) item.logs[1] = item.logs[1].filter(log => !log.relayEvent || !scoutToolLogs.has(log.msg));
    for (const source of item.sources || []) {
      if (BASE_SOURCES.some(originalSource => originalSource.id === source.id) ||
          source.id === EXTRA_SOURCE.id || source.id === RETRIEVAL_SOURCE.id) source.relayDemo = true;
      if (!source.relayDemo && source.status === 'discarded' && source.reason === '用户手动排除') {
        const rule = exclusionFor(source);
        if (rule) (item.excludedSources ||= {})[source.id] = rule;
      }
    }
  }
  if (state.mode === 'real') {
    for (const item of state.projects) {
      const run = context(item);
      if (run && (!run.terminal || item === project)) watch(item, run);
      if (item.revisionQuestion) queueRevision(item);
    }
  }
  window.RELAY_DEMO.selectProject = selectProject;
  renderAll();
  alignWorkspace();
})();
