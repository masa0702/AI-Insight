/* ===== 状態 ===== */
const screens = ["manual","survey","chat","history","finish"];
let current = "manual";
const total = 5; let count = 0;
const QA = [];
const $ = s => document.querySelector(s);

/* ===== 進捗 ===== */
const pc = $("#pc"), pt = $("#pt"), bar = $("#bar"); pt.textContent = total;
function setProgress(n){
  count = Math.max(0, Math.min(total, n));
  pc.textContent = count; bar.style.width = (count/total*100) + "%";
  $("#doneCard").style.display = count >= total ? "block" : "none";
  updateFab();
}

/* ===== 画面遷移 ===== */
function show(screen){
  current = screen;
  screens.forEach(sc=>{
    const el = document.getElementById(`screen-${sc}`);
    if(el) el.classList.toggle("active", sc===screen);
  });
  closeSheet();
  updateFab();
}

/* ===== トースト ===== */
function toast(msg){
  const t = $("#toast"); t.textContent = msg || "完了しました";
  t.classList.add("show"); setTimeout(()=> t.classList.remove("show"), 1500);
}

/* ===== メニュー ===== */
const sheet = $("#sheet"), overlay = $("#overlay"), menuOpen = $("#menuOpen");
const openSheet  = ()=>{ sheet.classList.add("open"); overlay.classList.add("show"); hideFab(true); };
const closeSheet = ()=>{ sheet.classList.remove("open"); overlay.classList.remove("show"); hideFab(false); };
menuOpen.addEventListener("click", openSheet);
overlay.addEventListener("click", closeSheet);
document.addEventListener("keydown", e=>{ if(e.key==="Escape") closeSheet(); });
let startY=null;
sheet.addEventListener("pointerdown", e=>{ startY=e.clientY; });
sheet.addEventListener("pointerup", e=>{ if(startY && e.clientY-startY>48) closeSheet(); startY=null; });
sheet.addEventListener("click", e=>{
  const a = e.target.closest("[data-screen]");
  if(a){ show(a.dataset.screen); }
});

/* ===== Survey: 提出・終了 ===== */
$("#submitBtn").addEventListener("click", ()=> $("#confirmModal").classList.add("open"));
$("#confirmModal [data-close]").addEventListener("click", ()=> $("#confirmModal").classList.remove("open"));
$("#confirmYes").addEventListener("click", ()=>{
  $("#confirmModal").classList.remove("open");
  toast("提出しました！"); setProgress(count + 1);
});
$("#endBtn").addEventListener("click", ()=> $("#exitModal").classList.add("open"));
$("#exitModal [data-close]").addEventListener("click", ()=> $("#exitModal").classList.remove("open"));
$("#exitYes").addEventListener("click", ()=>{
  $("#exitModal").classList.remove("open"); show("finish");
});

/* ===== Chat ===== */
const chatList = $("#chatList");
function addBubble(text, who="user"){
  const div = document.createElement("div");
  div.className = `bubble ${who}`; div.textContent = text;
  chatList.appendChild(div);
  chatList.parentElement.scrollTop = chatList.parentElement.scrollHeight;
}
$("#sendBtn").addEventListener("click", ()=>{
  const input = $("#qInput"); const q = input.value.trim(); if(!q) return;
  addBubble(q,"user"); input.value="";
  setTimeout(()=>{ const a="確認しました。次の手順へ進んでください。";
    addBubble(a,"bot"); QA.push({q,a}); renderHistory(); }, 220);
});

/* ===== History ===== */
function renderHistory(){
  const hist = $("#hist"); hist.innerHTML="";
  if(!QA.length){ hist.innerHTML = `<div class="subtle">まだ履歴がありません。</div>`; return; }
  QA.forEach((item,i)=>{
    const el=document.createElement("div");
    el.className="entry";
    el.innerHTML = `<div class="keta">質問 ${i+1}</div>
                    <div style="margin:.35rem 0 .5rem">${item.q}</div>
                    <div class="subtle">回答</div><div>${item.a}</div>`;
    hist.appendChild(el);
  });
}

/* ===== FAB：表示/位置調整（被り回避 + 画面に応じて表示制御） ===== */
const fab = $("#fabAsk");
function hideFab(h){ fab.classList.toggle("hidden", !!h); }
function updateFab(){
  const hide = (current==="chat" || current==="finish");
  fab.classList.toggle("hidden", hide);
  if(hide) return;
  const offset = (current==="survey" ? (count>=total ? 160 : 120) : 24);
  fab.style.bottom = `calc(${offset}px + env(safe-area-inset-bottom))`;
}
fab.addEventListener("click", ()=>{
  if(current!=="chat"){ show("chat"); }
  setTimeout(()=> document.getElementById("qInput")?.focus(), 60);
});

/* ===== 初期化 ===== */
show("manual"); setProgress(0); renderHistory();
document.getElementById("instBtn").addEventListener("click", ()=> toast("指示：画面Aを撮影して記録してください"));
const hash = location.hash.replace("#",""); if(screens.includes(hash)) show(hash);
window.addEventListener("hashchange", ()=>{ const h=location.hash.replace("#",""); if(screens.includes(h)) show(h); });
