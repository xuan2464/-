/* 绝影 Lite3 · 相机实时画面 —— 前端逻辑 */
(function () {
  'use strict';

  var MODE_LABEL = { color: '彩色', depth: '深度', dual: '双画面' };
  var $ = function (id) { return document.getElementById(id); };

  var el = {
    stage: $('stage'), img: $('stream'), overlay: $('overlay'), overlayText: $('overlayText'),
    spinner: $('spinner'), retryBtn: $('retryBtn'), statusPill: $('statusPill'),
    statusText: $('statusText'), clock: $('clock'), hudSource: $('hudSource'),
    hudRes: $('hudRes'), hudFps: $('hudFps'), hudMode: $('hudMode'), hudTime: $('hudTime'),
    mSource: $('mSource'), mRes: $('mRes'), mFps: $('mFps'), mStreamFps: $('mStreamFps'),
    mBitrate: $('mBitrate'), mAge: $('mAge'), mFrame: $('mFrame'), chart: $('chart'),
    modeGroup: $('modeGroup'), sizeSelect: $('sizeSelect'), fpsSelect: $('fpsSelect'),
    quality: $('quality'), qualityVal: $('qualityVal'), depthFar: $('depthFar'),
    depthFarVal: $('depthFarVal'), legend: $('depthLegend'), legendNear: $('legendNear'),
    legendMid: $('legendMid'), legendFar: $('legendFar'), snapBtn: $('snapBtn'),
    fsBtn: $('fsBtn'), log: $('log'), footerState: $('footerState'),
    viewerCount: $('viewerCount'), depthFarRow: $('depthFarRow'),
    addrText: $('addrText'), copyBtn: $('copyBtn'),
    panelTabs: $('panelTabs'), motionHint: $('motionHint'), motionTip: $('motionTip'),
    armBtn: $('armBtn'), stopBtn: $('stopBtn'), padMode: $('padMode'),
    mTarget: $('mTarget'), mSent: $('mSent'), mLastCmd: $('mLastCmd'), mAxes: $('mAxes'),
    speed: $('speed'), speedVal: $('speedVal'), padStop: $('padStop')
  };

  var state = {
    cid: 'v' + Math.random().toString(36).slice(2, 10),
    mode: 'color',
    live: false,
    connecting: false,
    lastFrameId: 0,
    lastProgress: 0,
    retries: 0,
    token: 0,
    config: { width: 640, height: 480, fps: 30, quality: 80, depth_near: 0.2, depth_far: 2.5 },
    status: null,
    fpsHist: [],
    logSig: '',
    sizes: [],
    retryTimer: null,
    countdownTimer: null,
    retryAt: 0,
    watchdog: null,
    tab: 'monitor'
  };

  /* ---------------------------------------------------------------- utils */
  function pad(n, w) { n = String(n); while (n.length < w) n = '0' + n; return n; }
  function clockText(d) {
    return pad(d.getHours(), 2) + ':' + pad(d.getMinutes(), 2) + ':' + pad(d.getSeconds(), 2);
  }
  function timecode(d) {
    return clockText(d) + '.' + pad(d.getMilliseconds(), 3);
  }
  function fmt(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    return Number(v).toFixed(digits === undefined ? 1 : digits);
  }
  function fmtRate(kbps) {
    if (!kbps || kbps <= 0) return '—';
    return kbps >= 1000 ? (kbps / 1000).toFixed(2) + ' Mbps' : Math.round(kbps) + ' kbps';
  }

  /* ------------------------------------------------------------- overlay  */
  function setPill(kind, text) {
    el.statusPill.classList.remove('live', 'lost');
    if (kind === 'live') el.statusPill.classList.add('live');
    if (kind === 'lost') el.statusPill.classList.add('lost');
    el.statusText.textContent = text;
  }

  function showOverlay(text, opts) {
    opts = opts || {};
    el.overlay.classList.remove('hidden');
    el.overlayText.textContent = text;
    el.spinner.classList.toggle('static', !!opts.lost);
    el.retryBtn.hidden = !opts.retry;
  }

  function hideOverlay() { el.overlay.classList.add('hidden'); }

  /* -------------------------------------------------------------- stream  */
  function streamUrl() {
    return '/stream?mode=' + state.mode + '&cid=' + state.cid + '&t=' + Date.now();
  }

  function connect() {
    if (state.connecting) return;
    state.connecting = true;
    state.live = false;
    state.lastFrameId = 0;
    state.lastProgress = Date.now();
    var token = ++state.token;

    el.stage.classList.remove('ready');
    showOverlay(state.retries ? '第 ' + (state.retries + 1) + ' 次尝试连接视频流…' : '正在建立视频流…');

    el.img.onload = function () {
      if (token !== state.token) return;
      markLive();
    };
    el.img.onerror = function () {
      if (token !== state.token) return;
      fail('画面连接中断');
    };
    el.img.src = streamUrl();

    clearTimeout(state.watchdog);
    state.watchdog = setTimeout(function () {
      if (token === state.token && !state.live) fail('视频流无响应');
    }, 7000);
  }

  function markLive() {
    state.live = true;
    state.connecting = false;
    state.retries = 0;
    state.lastProgress = Date.now();
    clearTimeout(state.watchdog);
    el.stage.classList.add('ready');
    el.stage.classList.remove('idle');
    hideOverlay();
    setPill('live', '直播中');
    stopCountdown();
  }

  function fail(reason) {
    if (state.failGuard === reason + ':' + state.lastProgress) return;
    state.failGuard = reason + ':' + state.lastProgress;
    state.live = false;
    state.connecting = false;
    el.stage.classList.remove('ready');
    el.stage.classList.add('idle');
    setPill('lost', '已断开');
    scheduleRetry(reason);
  }

  function stopCountdown() {
    clearInterval(state.countdownTimer);
    state.countdownTimer = null;
  }

  function scheduleRetry(reason) {
    if (state.retryTimer) return;
    state.retries++;
    var delay = Math.min(1200 * Math.pow(1.5, Math.min(state.retries, 5)), 6000);
    state.retryAt = Date.now() + delay;
    showOverlay(reason + '，' + Math.ceil(delay / 1000) + ' 秒后重连…', { lost: true, retry: true });

    stopCountdown();
    state.countdownTimer = setInterval(function () {
      var left = Math.max(0, Math.ceil((state.retryAt - Date.now()) / 1000));
      el.overlayText.textContent = reason + '，' + left + ' 秒后重连…';
    }, 300);

    state.retryTimer = setTimeout(function () {
      state.retryTimer = null;
      stopCountdown();
      connect();
    }, delay);
  }

  function reconnectNow() {
    clearTimeout(state.retryTimer);
    state.retryTimer = null;
    stopCountdown();
    state.retries = 0;
    state.connecting = false;
    connect();
  }

  /* --------------------------------------------------------------- mode   */
  function setMode(mode) {
    if (mode === state.mode) return;
    if (mode !== 'color' && state.status && state.status.source && !state.status.source.has_depth) return;
    state.mode = mode;
    Array.prototype.forEach.call(el.modeGroup.querySelectorAll('button'), function (b) {
      b.classList.toggle('active', b.dataset.mode === mode);
    });
    el.hudMode.textContent = MODE_LABEL[mode];
    updateLegend();
    updateAspect();
    state.connecting = false;
    connect();
  }

  function hasDepth() {
    return !!(state.status && state.status.source && state.status.source.has_depth);
  }

  function updateLegend() {
    var show = state.mode !== 'color' && hasDepth();
    el.legend.classList.toggle('show', show);
    var near = state.config.depth_near, far = state.config.depth_far;
    el.legendNear.textContent = near.toFixed(1);
    el.legendFar.textContent = far.toFixed(1);
    el.legendMid.textContent = ((near + far) / 2).toFixed(1);
  }

  function updateAspect() {
    var c = state.config;
    var dual = state.mode === 'dual' && hasDepth();
    var w = dual ? c.width * 2 + 2 : c.width;
    el.stage.style.setProperty('--ar', w + ' / ' + c.height);
    el.stage.classList.toggle('is-dual', dual);
    fitStage(w / c.height);
  }

  /* 把画面框精确贴合容器的可用空间：等比放大到刚好放下，不留黑边 */
  var STACK_QUERY = '(max-width: 900px)';
  function fitStage(ar) {
    var wrap = el.stage.parentElement;
    if (!wrap) return;
    if (window.matchMedia && window.matchMedia(STACK_QUERY).matches) {
      el.stage.style.width = '';
      el.stage.style.height = '';
      return;
    }
    var aw = wrap.clientWidth, ah = wrap.clientHeight;
    if (!aw || !ah) return;
    var W = Math.min(aw, ah * ar);
    var H = W / ar;
    if (H > ah) { H = ah; W = H * ar; }
    el.stage.style.width = Math.round(W) + 'px';
    el.stage.style.height = Math.round(H) + 'px';
  }

  function refitStage() {
    var c = state.config;
    var dual = state.mode === 'dual' && hasDepth();
    fitStage((dual ? c.width * 2 + 2 : c.width) / c.height);
  }

  /* -------------------------------------------------------------- config  */
  function applyConfig(patch) {
    fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch)
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (!j.ok) { pushLocalLog('error', j.error || '参数设置失败'); return; }
      state.config = j.config;
      updateAspect();
      updateLegend();
      if (j.changed) {
        state.connecting = false;
        connect();
      }
    }).catch(function (e) { pushLocalLog('error', '参数设置失败：' + e.message); });
  }

  function pushLocalLog(level, msg) {
    var li = document.createElement('li');
    li.className = level;
    var t = document.createElement('time');
    t.textContent = clockText(new Date());
    var s = document.createElement('span');
    s.textContent = msg;
    li.appendChild(t); li.appendChild(s);
    el.log.insertBefore(li, el.log.firstChild);
  }

  /* --------------------------------------------------------------- stats  */
  function renderStatus(s) {
    state.status = s;
    var src = s.source || {}, fr = s.frame || {}, cl = s.client;

    el.mSource.textContent = src.name || '—';
    el.hudSource.textContent = src.name || '—';
    el.mRes.textContent = src.width + '×' + src.height;
    el.hudRes.textContent = src.width + '×' + src.height;
    el.mFps.textContent = fmt(fr.capture_fps, 1) + ' fps';
    el.hudFps.textContent = fmt(fr.capture_fps, 0) + ' FPS';
    el.mStreamFps.textContent = cl ? fmt(cl.fps, 1) + ' fps' : '—';
    el.mBitrate.textContent = cl ? fmtRate(cl.kbps) : '—';
    el.mAge.textContent = fmt(fr.age_ms, 0) + ' ms';
    el.mFrame.textContent = '# ' + (fr.id || 0);
    el.viewerCount.textContent = (s.viewers || 1) + ' 路观看';
    el.footerState.textContent = 'v' + (s.version || '1.0.0') + '  ·  在线 ' +
      Math.floor((s.uptime_s || 0) / 60) + ' 分钟  ·  ' + (s.stream && s.stream.encoder || '-');

    // 深度不可用时禁用相关模式
    var depthOk = !!(src.has_depth);
    Array.prototype.forEach.call(el.modeGroup.querySelectorAll('button'), function (b) {
      if (b.dataset.mode !== 'color') {
        b.disabled = !depthOk;
        b.title = depthOk ? '' : '当前信号源没有深度数据';
      }
    });
    el.depthFarRow.style.opacity = depthOk ? '1' : '0.45';

    // 帧号推进 => 直播中
    if (fr.id && fr.id !== state.lastFrameId) {
      state.lastFrameId = fr.id;
      state.lastProgress = Date.now();
      if (!state.live) markLive();
    } else if (state.live && Date.now() - state.lastProgress > 4500) {
      fail('画面已停止更新');
    }

    // 帧率曲线
    state.fpsHist.push(fr.capture_fps || 0);
    if (state.fpsHist.length > 48) state.fpsHist.shift();
    drawChart();

    renderLog(s.log || []);
    updateLegend();
    updateAspect();
  }

  function renderLog(logs) {
    var tail = logs.slice(-9).reverse();
    var sig = tail.map(function (x) { return x.t + x.level; }).join('|');
    if (sig === state.logSig) return;
    state.logSig = sig;
    el.log.innerHTML = '';
    tail.forEach(function (item) {
      var li = document.createElement('li');
      li.className = item.level || 'info';
      var t = document.createElement('time');
      t.textContent = clockText(new Date(item.t * 1000));
      var s = document.createElement('span');
      s.textContent = item.msg;
      li.appendChild(t); li.appendChild(s);
      el.log.appendChild(li);
    });
  }

  /* --------------------------------------------------------------- chart  */
  function drawChart() {
    var cv = el.chart;
    var dpr = window.devicePixelRatio || 1;
    var w = cv.clientWidth || 280, h = 54;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
    }
    var ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    var data = state.fpsHist;
    var target = (state.config && state.config.fps) || 30;
    var peak = data.reduce(function (m, v) { return v > m ? v : m; }, 0);
    var maxY = Math.max(target * 1.15, peak * 1.1, 5);
    var pad = 4;

    // 参考线
    ctx.strokeStyle = 'rgba(255,255,255,0.07)';
    ctx.lineWidth = 1;
    for (var g = 0; g <= 2; g++) {
      var y = pad + (h - pad * 2) * (g / 2);
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
    var ty = pad + (h - pad * 2) * (1 - target / maxY);
    ctx.setLineDash([3, 4]);
    ctx.strokeStyle = 'rgba(63,224,176,0.28)';
    ctx.beginPath(); ctx.moveTo(0, ty); ctx.lineTo(w, ty); ctx.stroke();
    ctx.setLineDash([]);

    if (data.length < 2) return;

    var x = function (i) { return (i / (data.length - 1)) * w; };
    var y = function (v) { return pad + (h - pad * 2) * (1 - Math.min(v / maxY, 1)); };

    // 面积
    var grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, 'rgba(63,224,176,0.28)');
    grad.addColorStop(1, 'rgba(63,224,176,0.0)');
    ctx.beginPath();
    ctx.moveTo(0, h - pad);
    data.forEach(function (v, i) { ctx.lineTo(x(i), y(v)); });
    ctx.lineTo(w, h - pad);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // 折线
    ctx.beginPath();
    data.forEach(function (v, i) {
      if (i === 0) ctx.moveTo(x(i), y(v)); else ctx.lineTo(x(i), y(v));
    });
    ctx.strokeStyle = '#3fe0b0';
    ctx.lineWidth = 1.6;
    ctx.lineJoin = 'round';
    ctx.stroke();

    // 末端点
    var last = data[data.length - 1];
    ctx.beginPath();
    ctx.arc(w - 1, y(last), 2.4, 0, Math.PI * 2);
    ctx.fillStyle = '#d8fff2';
    ctx.fill();
  }

  /* ------------------------------------------------------------ snapshot  */
  function snapshot() {
    var d = new Date();
    var name = 'lite3-' + state.mode + '-' + d.getFullYear() + pad(d.getMonth() + 1, 2) +
      pad(d.getDate(), 2) + '-' + pad(d.getHours(), 2) + pad(d.getMinutes(), 2) +
      pad(d.getSeconds(), 2) + '.jpg';
    var a = document.createElement('a');
    a.href = '/snapshot.jpg?mode=' + state.mode + '&t=' + Date.now();
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    pushLocalLog('ok', '已抓拍 ' + name);
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else if (el.stage.requestFullscreen) {
      el.stage.requestFullscreen().catch(function () {});
    }
  }

  /* ----------------------------------------------------------- poll loop  */
  function poll() {
    fetch('/api/status?cid=' + state.cid, { cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (s) {
        renderStatus(s);
        if (!state.live && !state.connecting && !state.retryTimer) {
          setPill('lost', '等待画面');
        }
      })
      .catch(function () {
        if (!state.live) {
          setPill('lost', '服务端离线');
          el.footerState.textContent = '无法连接服务端';
        }
      });
  }

  /* ------------------------------------------------------------ address   */
  function copyAddr() {
    var text = location.origin + '/';
    var done = function () {
      el.copyBtn.textContent = '已复制';
      el.copyBtn.classList.add('done');
      setTimeout(function () {
        el.copyBtn.textContent = '复制';
        el.copyBtn.classList.remove('done');
      }, 1400);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, fallback);
    } else {
      fallback();
    }
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); done(); } catch (e) { pushLocalLog('warn', '复制失败，请手动选中地址'); }
      ta.remove();
    }
  }

  /* -------------------------------------------------------------- motion  */
  var CMD_LABEL = {
    sit_stand: '站立/坐下', stop: '急停', zero: '回零位',
    posture_mode: '姿态模式', motion_mode: '运动模式',
    nav_mode: '导航模式', manual_mode: '手动模式',
    gait_slow: '慢速步态', gait_mid: '中速步态', gait_fast: '高速步态',
    gait_crawl: '爬行步态', gait_rug_normal: 'RUG 普通', gait_rug_grip: 'RUG 抓地',
    gait_rug_high: 'RUG 高抬腿', hello: '打招呼', twist: '扭腰', moonwalk: '太空步'
  };
  var AXIS_KEYS = ['lat', 'fwd', 'yaw', 'hgt'];

  var motion = {
    enabled: false, armed: false, mode: 'move', speed: 0.6,
    held: {}, timer: null
  };

  var PAD_LABELS = {
    move: { up: '前进', down: '后退', left: '左移', right: '右移',
            yawL: '↺ 左转', yawR: '↻ 右转', hgtUp: '⬆ 抬高', hgtDown: '⬇ 降低' },
    pose: { up: '低头', down: '抬头', left: '左翻滚', right: '右翻滚',
            yawL: '↺ 左偏航', yawR: '↻ 右偏航', hgtUp: '⬆ 抬高', hgtDown: '⬇ 降低' }
  };

  var tokenMeta = document.querySelector('meta[name="lite3-api-token"]');
  var API_TOKEN = (tokenMeta && tokenMeta.content && tokenMeta.content.indexOf('__') !== 0)
    ? tokenMeta.content : '';

  function postJSON(url, body) {
    var headers = { 'Content-Type': 'application/json' };
    if (API_TOKEN) headers['X-API-Token'] = API_TOKEN;
    return fetch(url, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }

  function axesPayload() {
    var amp = Math.round(32767 * motion.speed);
    var out = { mode: motion.mode };
    AXIS_KEYS.forEach(function (axis) {
      out[axis] = Math.round((motion.held[axis] || 0) * amp);
    });
    return out;
  }

  function sendAxes() {
    if (!motion.armed) return;
    postJSON('/api/motion/axis', axesPayload()).catch(function () {});
  }

  function holdAxis(axis, sign) {
    if (!motion.armed) {
      pushLocalLog('warn', '先点「启用控制」才能下发摇杆指令');
      return;
    }
    motion.held[axis] = sign;
    if (!motion.timer) motion.timer = setInterval(sendAxes, 50);  // 20 Hz
    sendAxes();
  }

  function releaseAxis(axis) {
    delete motion.held[axis];
    if (!Object.keys(motion.held).length && motion.timer) {
      clearInterval(motion.timer);
      motion.timer = null;
    }
    sendAxes();
  }

  function releaseAll() {
    motion.held = {};
    if (motion.timer) { clearInterval(motion.timer); motion.timer = null; }
    Array.prototype.forEach.call(document.querySelectorAll('.held'), function (n) {
      n.classList.remove('held');
    });
    sendAxes();
  }

  function motionCmd(cmd) {
    if (!motion.enabled) { pushLocalLog('warn', '运动控制未启用'); return; }
    postJSON('/api/motion/cmd', { cmd: cmd }).then(function (j) {
      if (!j.ok) { pushLocalLog('error', '指令失败：' + (j.error || cmd)); return; }
      pushLocalLog(cmd === 'stop' ? 'warn' : 'ok', '已下发 ' + (CMD_LABEL[cmd] || cmd));
      if (cmd === 'stop') releaseAll();
      if (j.motion) applyMotion(j.motion);
    }).catch(function (e) { pushLocalLog('error', '指令发送失败：' + e.message); });
  }

  function setArmed(on) {
    if (!motion.enabled) { pushLocalLog('warn', '运动控制未启用（服务端 --no-motion）'); return; }
    postJSON('/api/motion/arm', { on: on }).then(function (j) {
      if (!j.ok) { pushLocalLog('error', '启用失败：' + (j.error || '')); return; }
      applyMotion(j.motion);
      pushLocalLog(on ? 'ok' : 'info', on ? '运动控制已启用' : '运动控制已解除');
      if (!on) releaseAll();
    }).catch(function (e) { pushLocalLog('error', '启用失败：' + e.message); });
  }

  function applyMotion(st) {
    if (!st) return;
    motion.enabled = !!st.enabled;
    motion.armed = !!st.armed;
    if (st.mode) motion.mode = st.mode;

    el.armBtn.textContent = motion.armed ? '解除控制' : '启用控制';
    el.armBtn.disabled = !motion.enabled;
    el.stopBtn.disabled = !motion.enabled;
    el.armBtn.classList.toggle('primary', !motion.armed);
    el.motionHint.textContent = !motion.enabled ? '未启用'
      : (motion.armed ? '已启用' : '待启用');
    el.motionHint.style.color = motion.armed ? '#3fe0b0' : '';
    el.padMode.textContent = motion.mode === 'pose' ? '姿态模式' : '移动模式';

    var labels = PAD_LABELS[motion.mode] || PAD_LABELS.move;
    Array.prototype.forEach.call(document.querySelectorAll('[data-label]'), function (n) {
      var t = labels[n.dataset.label];
      if (t) n.textContent = t;
    });

    el.mTarget.textContent = st.target || '—';
    el.mSent.textContent = (st.sent || 0) + ' 帧';
    el.mLastCmd.textContent = CMD_LABEL[st.last_command] || st.last_command || '—';
    var axes = st.axes || {};
    el.mAxes.textContent = AXIS_KEYS.map(function (k) {
      return k + ' ' + (axes[k] || 0);
    }).join('   ');
  }

  function pollMotion() {
    fetch('/api/motion', { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (j) { applyMotion(j.motion); })
      .catch(function () {});
  }

  function switchTab(tab) {
    state.tab = tab;
    Array.prototype.forEach.call(el.panelTabs.querySelectorAll('button'), function (b) {
      b.classList.toggle('active', b.dataset.tab === tab);
    });
    Array.prototype.forEach.call(document.querySelectorAll('.tabpage'), function (p) {
      p.hidden = p.dataset.page !== tab;
    });
    if (tab !== 'control') releaseAll();
    if (tab === 'control') pollMotion();
  }

  /* ---------------------------------------------------------------- boot  */
  function boot() {
    var want = new URLSearchParams(location.search).get('mode');
    if (want && MODE_LABEL[want]) { state.mode = want; }
    el.hudMode.textContent = MODE_LABEL[state.mode];
    Array.prototype.forEach.call(el.modeGroup.querySelectorAll('button'), function (b) {
      b.classList.toggle('active', b.dataset.mode === state.mode);
    });
    el.addrText.textContent = location.origin + '/';
    el.addrText.title = location.origin + '/';

    fetch('/api/config').then(function (r) { return r.json(); }).then(function (j) {
      state.config = j.config || state.config;
      state.sizes = j.sizes || [];
      el.sizeSelect.innerHTML = '';
      state.sizes.forEach(function (s) {
        var o = document.createElement('option');
        o.value = s[0] + 'x' + s[1];
        o.textContent = s[0] + ' × ' + s[1];
        if (s[0] === state.config.width && s[1] === state.config.height) o.selected = true;
        el.sizeSelect.appendChild(o);
      });
      el.fpsSelect.innerHTML = '';
      (j.fps || [15, 30, 60]).forEach(function (f) {
        var o = document.createElement('option');
        o.value = f;
        o.textContent = f + ' fps';
        if (f === state.config.fps) o.selected = true;
        el.fpsSelect.appendChild(o);
      });
      el.quality.value = state.config.quality;
      el.qualityVal.textContent = state.config.quality;
      el.depthFar.value = state.config.depth_far;
      el.depthFarVal.textContent = state.config.depth_far.toFixed(1) + 'm';
      updateAspect();
      updateLegend();
    }).catch(function () {});

    el.modeGroup.addEventListener('click', function (e) {
      var btn = e.target.closest('button');
      if (btn && !btn.disabled) setMode(btn.dataset.mode);
    });

    el.sizeSelect.addEventListener('change', function () {
      var p = this.value.split('x');
      applyConfig({ width: parseInt(p[0], 10), height: parseInt(p[1], 10) });
    });
    el.fpsSelect.addEventListener('change', function () {
      applyConfig({ fps: parseInt(this.value, 10) });
    });
    el.quality.addEventListener('input', function () {
      el.qualityVal.textContent = this.value;
    });
    el.quality.addEventListener('change', function () {
      applyConfig({ quality: parseInt(this.value, 10) });
    });
    el.depthFar.addEventListener('input', function () {
      el.depthFarVal.textContent = parseFloat(this.value).toFixed(1) + 'm';
      state.config.depth_far = parseFloat(this.value);
      updateLegend();
    });
    el.depthFar.addEventListener('change', function () {
      applyConfig({ depth_far: parseFloat(this.value) });
    });

    el.snapBtn.addEventListener('click', snapshot);
    el.fsBtn.addEventListener('click', toggleFullscreen);
    el.retryBtn.addEventListener('click', reconnectNow);
    el.copyBtn.addEventListener('click', copyAddr);

    /* ---- 控制页 ---- */
    el.panelTabs.addEventListener('click', function (e) {
      var btn = e.target.closest('button');
      if (btn && btn.dataset.tab) switchTab(btn.dataset.tab);
    });
    el.armBtn.addEventListener('click', function () { setArmed(!motion.armed); });
    el.stopBtn.addEventListener('click', function () { motionCmd('stop'); });
    el.padStop.addEventListener('click', releaseAll);
    Array.prototype.forEach.call(document.querySelectorAll('[data-cmd]'), function (btn) {
      btn.addEventListener('click', function () { motionCmd(btn.dataset.cmd); });
    });
    el.speed.addEventListener('input', function () {
      motion.speed = parseInt(this.value, 10) / 100;
      el.speedVal.textContent = this.value + '%';
      if (motion.timer) sendAxes();
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-axis]'), function (btn) {
      var axis = btn.dataset.axis;
      var sign = parseInt(btn.dataset.sign, 10);
      var release = function () {
        btn.classList.remove('held');
        releaseAxis(axis);
      };
      btn.addEventListener('pointerdown', function (e) {
        e.preventDefault();
        btn.classList.add('held');
        if (btn.setPointerCapture && e.pointerId !== undefined) {
          try { btn.setPointerCapture(e.pointerId); } catch (err) { /* 旧浏览器忽略 */ }
        }
        holdAxis(axis, sign);
      });
      btn.addEventListener('pointerup', release);
      btn.addEventListener('pointercancel', release);
      btn.addEventListener('pointerleave', function () {
        if (btn.classList.contains('held')) release();
      });
      btn.addEventListener('contextmenu', function (e) { e.preventDefault(); });
    });
    window.addEventListener('blur', function () { if (state.tab === 'control') releaseAll(); });

    var KEY_AXIS = { w: ['fwd', 1], s: ['fwd', -1], a: ['lat', -1], d: ['lat', 1],
                     q: ['yaw', -1], e: ['yaw', 1] };

    document.addEventListener('keydown', function (e) {
      var tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
      var k = e.key.toLowerCase();
      if (state.tab === 'control') {
        if (KEY_AXIS[k]) {
          e.preventDefault();
          if (!e.repeat) holdAxis(KEY_AXIS[k][0], KEY_AXIS[k][1]);
          return;
        }
        if (k === ' ') { e.preventDefault(); motionCmd('stop'); return; }
        if (k === 'escape') { releaseAll(); return; }
      }
      if (k === '1') setMode('color');
      else if (k === '2') setMode('depth');
      else if (k === '3') setMode('dual');
      else if (k === 's') { e.preventDefault(); snapshot(); }
      else if (k === 'f') { e.preventDefault(); toggleFullscreen(); }
      else if (k === 'r') reconnectNow();
    });

    document.addEventListener('keyup', function (e) {
      if (state.tab !== 'control') return;
      var pair = KEY_AXIS[(e.key || '').toLowerCase()];
      if (pair) releaseAxis(pair[0]);
    });

    document.addEventListener('fullscreenchange', function () {
      var on = !!document.fullscreenElement;
      el.fsBtn.lastChild.textContent = on ? ' 退出全屏' : ' 全屏';
    });

    setInterval(function () {
      var d = new Date();
      el.clock.textContent = clockText(d);
      el.hudTime.textContent = timecode(d);
    }, 120);

    window.addEventListener('resize', function () { refitStage(); drawChart(); });
    if (window.ResizeObserver) { new ResizeObserver(refitStage).observe(el.stage.parentElement); }

    connect();
    poll();
    pollMotion();
    setInterval(poll, 1000);
    setInterval(pollMotion, 1000);

    if (new URLSearchParams(location.search).get('tab') === 'control') switchTab('control');
  }

  boot();
})();
