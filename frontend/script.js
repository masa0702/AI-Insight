/* script.js (完全版・高速&堅牢)
   - API接続先は自動判定（同一オリジン → 127.0.0.1:8000 → localhost:8000）
     * index.html で window.__API_BASE__ を定義するとそれを最優先で使用
   - Markdown表示 / 進捗連動の指示 / 提出保存 / SSEストリーミングChat
   - メニュー操作・FAB表示制御（被り回避）・Enter送信対応
*/
(() => {
  "use strict";

  /* =========================
     APIベースURLの自動判定
  ========================= */
  let __apiBaseCache = null;
  async function resolveApiBase(){
    if(__apiBaseCache !== null) return __apiBaseCache;

    const explicit = window.__API_BASE__; // 明示指定（あれば最優先）
    const candidates = [
      explicit ?? "",                 // 明示 or 同一オリジン（相対パスで /api に到達可能な構成）
      (location && location.origin) ? location.origin : "",
      "http://127.0.0.1:8000",
      "http://localhost:8000",
    ];
    for(const base of candidates){
      try{
        const url = (base ? base : "") + "/healthz";
        const r = await fetch(url, { method:"GET", cache:"no-store", mode:"cors" });
        if(r.ok){ __apiBaseCache = base; return __apiBaseCache; }
      }catch(_){ /* 次の候補へ */ }
    }
    // 最後のフォールバック（起動順の問題があっても以降のfetchで失敗内容を返す）
    __apiBaseCache = "http://127.0.0.1:8000";
    return __apiBaseCache;
  }
  async function fetchApi(path, options){
    const base = await resolveApiBase();
    return fetch((base ? base : "") + path, options);
  }

  /* =========================
     状態・ユーティリティ
  ========================= */
  const screens = ["manual","survey","chat","history","finish"];
  let current = "manual";
  let count = 0;
  const QA = [];
  let STEPS = [];
  function totalSteps(){
    return (Array.isArray(STEPS) && STEPS.length > 0) ? STEPS.length : defaultSteps().length;
  }

  const $  = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  /* =========================
     画面遷移・進捗・トースト
  ========================= */
  function show(screen){
    current = screen;
    screens.forEach(sc=>{
      const el = document.getElementById(`screen-${sc}`);
      if(el) el.classList.toggle("active", sc===screen);
    });
    closeSheet();
    updateFab();
  }

  function setProgress(n){
    const t = Math.max(1, totalSteps());
    count = Math.max(0, Math.min(t, n));
    const pc = $("#pc"), pt = $("#pt"), bar = $("#bar");
    if(pt) pt.textContent = String(t);
    if(pc) pc.textContent = String(count);
    if(bar) bar.style.width = (count/t*100) + "%";
    const done = $("#doneCard");
    if(done) done.style.display = count >= t ? "block" : "none";
    updateStepBox();
    updateFab();
  }

  function toast(msg){
    const t = $("#toast");
    if(!t) return;
    t.textContent = msg || "完了しました";
    t.classList.add("show");
    setTimeout(()=> t.classList.remove("show"), 1500);
  }

  /* =========================
     メニュー（ボトムシート）
  ========================= */
  const sheet = $("#sheet");
  const overlay = $("#overlay");
  const menuOpen = $("#menuOpen");

  const openSheet  = ()=>{ sheet?.classList.add("open"); overlay?.classList.add("show"); hideFab(true); };
  const closeSheet = ()=>{ sheet?.classList.remove("open"); overlay?.classList.remove("show"); hideFab(false); };
  function bindMenu(){
    menuOpen?.addEventListener("click", openSheet);
    overlay?.addEventListener("click", closeSheet);
    $("#goto-manual")?.addEventListener("click", ()=>{ show("manual"); closeSheet(); });
    $("#goto-survey")?.addEventListener("click", ()=>{ show("survey"); closeSheet(); });
    $("#goto-chat")?.addEventListener("click", ()=>{ show("chat"); closeSheet(); });
    $("#goto-history")?.addEventListener("click", ()=>{ show("history"); closeSheet(); });
    $("#goto-finish")?.addEventListener("click", ()=>{ show("finish"); closeSheet(); });
  }

  /* =========================
     マニュアル読み込み
  ========================= */
  async function loadManual(){
    try{
      const res = await fetch("manual.md", { cache: "no-store" });
      if(!res.ok) throw new Error("manual fetch failed");
      const md = await res.text();
      $("#manual-content").textContent = md;
    }catch(_){
      $("#manual-content").textContent = "manual.md を読み込めませんでした。";
    }
  }

  /* =========================
     調査手順（進捗と連動）
  ========================= */
  function normalizeSteps(raw){
    const pick = (item) => {
      if (item == null) return "";
      if (typeof item === "string") return item;
      if (typeof item === "object") {
        return item.instruction || item.title || item.text || "";
      }
      return String(item);
    };
    if (Array.isArray(raw)) {
      return raw.map(pick).filter(s => typeof s === "string" && s.length > 0);
    }
    if (raw && Array.isArray(raw.steps)) {
      return raw.steps.map(pick).filter(s => typeof s === "string" && s.length > 0);
    }
    return defaultSteps();
  }

  async function loadSteps(){
    try{
      const res = await fetch("steps.json", { cache: "no-store" });
      if(res.ok){
        STEPS = normalizeSteps(await res.json());
      }else{
        STEPS = defaultSteps();
      }
    }catch(_){
      STEPS = defaultSteps();
    }
    updateStepBox();
  }
  function defaultSteps(){
    return [
      "装置Aの状態を写真と文で記録してください。",
      "ログBを取得し、エラーコードの有無を報告してください。",
      "設定Cを変更後の動作を観察して記録してください。",
      "Dが要件を満たすか確認し、根拠を記述してください。",
      "全体の所見を3点まとめてください。"
    ];
  }
  function updateStepBox(){
    const box = $("#stepBox");
    const num = $("#stepNum");
    const txt = $("#stepTxt");
    if(!box || !num || !txt) return;
    const idx = Math.min(count, Math.max(0, STEPS.length - 1));
    if(STEPS.length){
      box.style.display = "flex";
      num.textContent = String(idx + 1);
      txt.textContent = STEPS[idx];
    }else{
      box.style.display = "none";
    }
  }

  /* =========================
     提出（サーバ保存）
  ========================= */
  async function submitToServer(){
    const payload = {
      step: Math.min(count, Math.max(0, STEPS.length - 1)) + 1,
      content: ($("#qInput")?.value || "").trim()
    };
    if(!payload.content){
      toast("内容を入力してください");
      return { ok:false };
    }
    const res = await fetchApi("/api/submit", {
      method: "POST",
      headers: { "Content-Type":"application/json" },
      body: JSON.stringify(payload)
    });
    if(!res.ok) throw new Error("保存に失敗しました");
    return await res.json();
  }

  function bindSubmitFlow(){
    const submitBtn = $("#submitBtn");
    const confirmModal = $("#confirmModal");
    const confirmYes = $("#confirmYes");

    submitBtn?.addEventListener("click", ()=> confirmModal?.classList.add("open"));
    confirmModal?.querySelector("[data-close]")?.addEventListener("click", ()=> confirmModal?.classList.remove("open"));
    confirmYes?.addEventListener("click", async ()=>{
      confirmModal?.classList.remove("open");
      try{
        const r = await submitToServer();
        if(r?.ok){
          toast("保存しました");
          setProgress(count + 1);
          renderHistory();
          $("#qInput").value = "";
        }else{
          toast("保存に失敗しました");
        }
      }catch(e){
        console.error(e);
        toast("保存に失敗しました");
      }
    });
  }

  /* =========================
     Chat（SSEストリーム）
  ========================= */
  async function askLLMStream(q){
    const base = await resolveApiBase();
    const res = await fetch((base ? base : "") + "/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type":"application/json" },
      body: JSON.stringify({ q })
    });
    if(!res.ok) throw new Error("LLM接続に失敗しました");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    const out = $("#answer");
    out.textContent = "";

    while(true){
      const {value, done} = await reader.read();
      if(done) break;
      const chunk = decoder.decode(value, {stream:true});
      // サーバ側の仕様に合わせて必要なら調整（\\n → 改行など）
      out.textContent += chunk.replace(/\\n/g, "\n");
    }
  }

  function bindChat(){
    const input = $("#qInput");
    const send  = $("#qSend");

    const doSend = async ()=>{
      const q = (input?.value || "").trim();
      if(!q) return;
      QA.push({ q, t:Date.now() });
      renderHistory();
      input.value = "";
      try{
        await askLLMStream(q);
      }catch(e){
        console.error(e);
        $("#answer").textContent = "接続に失敗しました。";
      }
    };

    send?.addEventListener("click", doSend);
    input?.addEventListener("keydown", (ev)=>{
      if(ev.key === "Enter" && !ev.shiftKey){
        ev.preventDefault();
        doSend();
      }
    });
  }

  function renderHistory(){
    const list = $("#historyList");
    if(!list) return;
    list.innerHTML = "";
    for(const item of QA.slice().reverse()){
      const el = document.createElement("div");
      el.className = "hist";
      const q = document.createElement("div");
      q.className = "q";
      q.textContent = item.q; // XSS対策：textContentで挿入
      const t = document.createElement("div");
      t.className = "t";
      t.textContent = new Date(item.t).toLocaleString();
      el.appendChild(q);
      el.appendChild(t);
      list.appendChild(el);
    }
  }

  /* ======================
     FAB（表示/位置調整）
  ====================== */
  const fab = $("#fab");
  function hideFab(h){ fab?.classList.toggle("hidden", !!h); }
  function updateFab(){
    if(!fab) return;
    const hide = (current==="chat" || current==="finish");
    const t = totalSteps();
    fab.classList.toggle("hidden", hide);
    if(hide) return;
    const offset = (current==="survey" ? (count>=t ? 160 : 120) : 24);
    fab.style.bottom = `calc(${offset}px + env(safe-area-inset-bottom))`;
  }
  function bindFab(){
    fab?.addEventListener("click", ()=>{
      if(current!=="chat"){ show("chat"); }
      setTimeout(()=> $("#qInput")?.focus(), 60);
    });
  }

  /* ======================
     その他（小物）
  ====================== */
  function bindMisc(){
    $("#toSurvey")?.addEventListener("click", ()=> show("survey"));
    $("#toFinish")?.addEventListener("click", ()=> show("finish"));
  }

  /* =========================
     起動
  ========================= */
  window.addEventListener("DOMContentLoaded", async ()=>{
    bindMenu();
    bindSubmitFlow();
    bindChat();
    bindFab();
    bindMisc();

    show("manual");
    setProgress(0);
    renderHistory();

    // リソースを同時読み込み
    await Promise.all([loadManual(), loadSteps()]);
    setProgress(count);   // ← steps.json 反映後に総数を再適用
    updateFab();
  });

})();
