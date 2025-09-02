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
  const total = 5;
  let count = 0;
  const QA = [];
  let STEPS = [];

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
    count = Math.max(0, Math.min(total, n));
    const pc = $("#pc"), pt = $("#pt"), bar = $("#bar");
    if(pt) pt.textContent = String(total);
    if(pc) pc.textContent = String(count);
    if(bar) bar.style.width = (count/total*100) + "%";
    const done = $("#doneCard");
    if(done) done.style.display = count >= total ? "block" : "none";
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
    document.addEventListener("keydown", e=>{ if(e.key==="Escape") closeSheet(); });
    let startY=null;
    sheet?.addEventListener("pointerdown", e=>{ startY=e.clientY; });
    sheet?.addEventListener("pointerup", e=>{ if(startY && e.clientY-startY>48) closeSheet(); startY=null; });
    sheet?.addEventListener("click", e=>{
      const a = e.target.closest("[data-screen]");
      if(a){ show(a.dataset.screen); }
    });
  }

  /* =========================
     マニュアル（Markdown）
  ========================= */
  async function loadManual(){
    const el = $("#manualMD");
    if(!el) return;
    try{
      const res = await fetch("manual.md", { cache: "no-store" });
      const md  = await res.text();
      const html = (window.marked?.parse) ? window.marked.parse(md) : md.replace(/\n/g,"<br>");
      el.innerHTML = html;
    }catch(e){
      el.textContent = "マニュアルの読み込みに失敗しました。";
    }
  }

  /* =========================
     調査手順（進捗と連動）
  ========================= */
  async function loadSteps(){
    try{
      const res = await fetch("steps.json", { cache: "no-store" });
      if(res.ok){
        STEPS = await res.json();
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
      content: ($("#tb")?.value || "").trim(),
      ts: Date.now()
    };
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

    confirmYes && (confirmYes.onclick = async ()=>{
      try{
        await submitToServer();
        toast("提出しました！（サーバ保存済）");
        setProgress(count + 1);
      }catch(_){
        toast("提出に失敗しました");
      }finally{
        confirmModal?.classList.remove("open");
      }
    });

    const endBtn = $("#endBtn");
    const exitModal = $("#exitModal");
    const exitYes = $("#exitYes");

    endBtn?.addEventListener("click", ()=> exitModal?.classList.add("open"));
    exitModal?.querySelector("[data-close]")?.addEventListener("click", ()=> exitModal?.classList.remove("open"));
    exitYes?.addEventListener("click", ()=>{
      exitModal?.classList.remove("open");
      show("finish");
    });
  }

  /* =========================
     Chat（SSEストリーミング）
  ========================= */
  function addBubble(text, who="user"){
    const list = $("#chatList");
    if(!list) return;
    const div = document.createElement("div");
    div.className = `bubble ${who}`;
    div.textContent = text;
    list.appendChild(div);
    // スクロール追従
    list.parentElement && (list.parentElement.scrollTop = list.parentElement.scrollHeight);
  }

  let currentStreamCtrl = null;

  async function streamChat(userText){
    const list = $("#chatList");
    if(!list) return;

    // 既存ストリームがあれば中断（連打対策）
    if(currentStreamCtrl){ try{ currentStreamCtrl.abort(); }catch(_){} }
    currentStreamCtrl = new AbortController();

    // Bot吹き出し（逐次追記）
    const bot = document.createElement("div");
    bot.className = "bubble bot";
    bot.textContent = "…";
    list.appendChild(bot);

    const meta = { step: Math.min(count, Math.max(0, STEPS.length - 1)) + 1 };

    let res;
    try{
      res = await fetchApi("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type":"application/json" },
        body: JSON.stringify({ query: userText, meta }),
        signal: currentStreamCtrl.signal
      });
    }catch(_){
      bot.textContent = "接続に失敗しました。";
      return;
    }
    if(!res.ok || !res.body){ bot.textContent = "接続に失敗しました。"; return; }

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();

    // 再描画をrAFで間引いて高速化（文字数が多くても滑らか）
    let appendBuf = "";
    let rafPending = false;
    const flush = ()=>{
      if(!rafPending){
        rafPending = true;
        requestAnimationFrame(()=>{
          if(bot.textContent === "…") bot.textContent = "";
          if(appendBuf){
            bot.textContent += appendBuf;
            appendBuf = "";
            list.parentElement && (list.parentElement.scrollTop = list.parentElement.scrollHeight);
          }
          rafPending = false;
        });
      }
    };

    let buf = "";
    try{
      while(true){
        const {done, value} = await reader.read();
        if(done) break;
        buf += decoder.decode(value, {stream:true});
        const parts = buf.split("\n\n"); // SSE分割
        for(let i=0; i<parts.length-1; i++){
          const line = parts[i].trim();
          if(!line.startsWith("data:")) continue;
          const chunk = line.slice(5).trim();
          if(chunk === "[DONE]"){ break; }
          // サーバ側が "\n" をエスケープして送る前提
          appendBuf += chunk.replace(/\\n/g, "\n");
          flush();
        }
        buf = parts[parts.length-1];
      }
    }catch(_){
      // 中断等は無視して最新の状態で終了
    }finally{
      // 最終フラッシュ
      if(appendBuf){ bot.textContent = (bot.textContent==="…") ? appendBuf : (bot.textContent + appendBuf); }
      list.parentElement && (list.parentElement.scrollTop = list.parentElement.scrollHeight);
      currentStreamCtrl = null;
    }
  }

  function bindChat(){
    const sendBtn = $("#sendBtn");
    const input   = $("#qInput");

    // 送信ボタン
    sendBtn && (sendBtn.onclick = async ()=>{
      const q = input?.value?.trim() || "";
      if(!q) return;
      addBubble(q, "user");
      input.value = "";
      await streamChat(q);
      const lastBot = $("#chatList .bubble.bot:last-child");
      QA.push({ q, a: lastBot?.textContent || "" });
      renderHistory();
    });

    // Enter送信（Shift+Enterは改行）
    input && input.addEventListener("keydown", (e)=>{
      if(e.key === "Enter" && !e.shiftKey){
        e.preventDefault();
        sendBtn?.click();
      }
    });
  }

  function renderHistory(){
    const hist = $("#hist");
    if(!hist) return;
    hist.innerHTML = "";
    if(!QA.length){
      hist.innerHTML = `<div class="subtle">まだ履歴がありません。</div>`;
      return;
    }
    const frag = document.createDocumentFragment();
    QA.forEach((item,i)=>{
      const el = document.createElement("div");
      el.className = "entry";
      el.innerHTML = `
        <div class="keta">質問 ${i+1}</div>
        <div style="margin:.35rem 0 .5rem">${item.q}</div>
        <div class="subtle">回答</div>
        <div>${item.a}</div>
      `;
      frag.appendChild(el);
    });
    hist.appendChild(frag);
  }

  /* =========================
     FAB（被り回避・画面別表示）
  ========================= */
  const fab = $("#fabAsk");
  function hideFab(h){ fab?.classList.toggle("hidden", !!h); }
  function updateFab(){
    if(!fab) return;
    const hide = (current==="chat" || current==="finish");
    fab.classList.toggle("hidden", hide);
    if(hide) return;
    const offset = (current==="survey" ? (count>=total ? 160 : 120) : 24);
    fab.style.bottom = `calc(${offset}px + env(safe-area-inset-bottom))`;
  }
  function bindFab(){
    fab?.addEventListener("click", ()=>{
      if(current!=="chat"){ show("chat"); }
      setTimeout(()=> $("#qInput")?.focus(), 60);
    });
  }

  /* =========================
     その他（初期化・指示トースト）
  ========================= */
  function bindMisc(){
    $("#instBtn")?.addEventListener("click", ()=> toast("指示：画面Aを撮影して記録してください"));
    // ハッシュ遷移（任意）
    const hash = location.hash.replace("#","");
    if(screens.includes(hash)) show(hash);
    window.addEventListener("hashchange", ()=>{
      const h = location.hash.replace("#","");
      if(screens.includes(h)) show(h);
    });
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
    updateFab();
  });

})();
