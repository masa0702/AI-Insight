# agent_manual_agent.py
# ============================================================
# 依頼 → Manual 生成 → Steps 生成 → 自己改善ループ（Planner/Designer/Critic/Editor）
# ・.env から GOOGLE_API_KEY / OPENAI_API_KEY（python-dotenv）
# ・Gemini と GPT(OpenAI) を切替（--provider / UI）
# ・JSON Mode（Gemini: response_schema, OpenAI: responses API の json_schema をまず試行、失敗時フォールバック）
# ・Markdownフェンスの安全パース（内容改変なし）
# ・ルールベース補完なし（生成/修正は LLM が実施）
# ・失敗時は例外で停止（モックなし）
# ・Gradio UI でログ/バージョン/プレビュー表示
# ============================================================

from __future__ import annotations
import os, re, json, time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

# ---- 依存 ----
from dotenv import load_dotenv
from jsonschema import Draft202012Validator
import gradio as gr

# ---- API クライアント候補（存在しない環境でもImportErrorにしない）
_GEMINI_AVAILABLE = True
_OPENAI_AVAILABLE = True
try:
    from google import genai as google_genai  # pip install google-genai
except Exception:
    _GEMINI_AVAILABLE = False
try:
    # 新SDK: pip install openai>=1.40
    from openai import OpenAI as OpenAIClient
except Exception:
    _OPENAI_AVAILABLE = False

# ---- .env からキー読込 ----
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ============================================================
# スキーマ定義（LLM用: 大文字タイプ & nullable）
# ============================================================
MANUAL_SCHEMA_LLM: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "description": {"type": "STRING"},
        "audience": {"type": "STRING", "nullable": True},
        "prerequisites": {"type": "ARRAY", "items": {"type": "STRING"}},
        "input_spec": {"type": "STRING"},
        "output_spec": {"type": "STRING"},
        "constraints": {"type": "ARRAY", "items": {"type": "STRING"}},
        "cautions": {"type": "ARRAY", "items": {"type": "STRING"}},
        "examples": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "content": {"type": "STRING", "nullable": True},
                },
                "required": ["name", "content"],
            },
            "nullable": True,
        },
    },
    "required": ["title", "description", "prerequisites", "input_spec", "output_spec", "constraints", "cautions"],
    "propertyOrdering": ["title","description","audience","prerequisites","input_spec","output_spec","constraints","cautions","examples"],
}

STEPS_SCHEMA_LLM: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "steps": {
            "type": "ARRAY",
            "minItems": 1,
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING"},
                    "title": {"type": "STRING"},
                    "instruction": {"type": "STRING"},
                    "notes": {"type": "STRING", "nullable": True},
                    "expected_output": {"type": "STRING", "nullable": True},
                    "checklist": {"type": "ARRAY", "items": {"type": "STRING"}, "nullable": True},
                },
                "required": ["id", "title", "instruction"],
            },
        }
    },
    "required": ["steps"],
    "propertyOrdering": ["steps"],
}

DOC_SCHEMA_LLM: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "manual": MANUAL_SCHEMA_LLM,
        "steps": STEPS_SCHEMA_LLM["properties"]["steps"],
    },
    "required": ["manual", "steps"],
    "propertyOrdering": ["manual", "steps"],
}

# ---- jsonschema 検証用（小文字タイプミラー）----
MANUAL_SCHEMA_JSON = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "audience": {"type": ["string", "null"]},
        "prerequisites": {"type": "array", "items": {"type": "string"}},
        "input_spec": {"type": "string"},
        "output_spec": {"type": "string"},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "cautions": {"type": "array", "items": {"type": "string"}},
        "examples": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "content": {"type": ["string", "null"]},
                },
                "required": ["name", "content"],
            },
        },
    },
    "required": ["title","description","prerequisites","input_spec","output_spec","constraints","cautions"],
}
STEPS_SCHEMA_JSON = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "instruction": {"type": "string"},
                    "notes": {"type": ["string", "null"]},
                    "expected_output": {"type": ["string", "null"]},
                    "checklist": {"type": ["array", "null"], "items": {"type": "string"}},
                },
                "required": ["id","title","instruction"],
            },
        }
    },
    "required": ["steps"],
}
DOC_SCHEMA_JSON = {
    "type": "object",
    "properties": {
        "manual": MANUAL_SCHEMA_JSON,
        "steps": STEPS_SCHEMA_JSON["properties"]["steps"],
    },
    "required": ["manual", "steps"],
}
manual_validator = Draft202012Validator(MANUAL_SCHEMA_JSON)
steps_validator = Draft202012Validator(STEPS_SCHEMA_JSON)
doc_validator = Draft202012Validator(DOC_SCHEMA_JSON)

# ============================================================
# ユーティリティ
# ============================================================
def _extract_json(text: str) -> str:
    """```json ... ``` 等のフェンスを剥がし、JSON 部分だけ抽出（内容は変更しない）"""
    if not text:
        return text
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        return text.strip()
    start = min(starts)
    end_brace = text.rfind("}")
    end_bracket = text.rfind("]")
    end = max(end_brace, end_bracket)
    if end >= start:
        return text[start : end + 1].strip()
    return text.strip()

def _loads_json_maybe_fenced(text: str) -> Any:
    return json.loads(_extract_json(text))

def validate_or_errors(validator: Draft202012Validator, data: Any) -> Tuple[bool, str]:
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    if not errors:
        return True, "OK"
    lines = []
    for e in errors:
        loc = "/".join(map(str, e.absolute_path)) or "(root)"
        lines.append(f"- at {loc}: {e.message}")
    return False, "\n".join(lines)

def retry(fn, retries=2, backoff=0.8):
    last = None
    for i in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(backoff * (2**i))
    raise last

def pretty_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)

# ============================================================
# LLM 抽象クライアント
# ============================================================
class LLMClient:
    def __init__(self, provider: str, model: str):
        self.provider = provider.lower()
        self.model = model
        self._gclient = None
        self._oclient = None
        if self.provider == "gemini":
            if not _GEMINI_AVAILABLE:
                raise RuntimeError("google-genai がインストールされていません。")
            if not GOOGLE_API_KEY:
                raise RuntimeError(".env に GOOGLE_API_KEY がありません。")
            self._gclient = google_genai.Client(api_key=GOOGLE_API_KEY)
        elif self.provider == "openai":
            if not _OPENAI_AVAILABLE:
                raise RuntimeError("openai>=1.40 がインストールされていません。")
            if not OPENAI_API_KEY:
                raise RuntimeError(".env に OPENAI_API_KEY がありません。")
            self._oclient = OpenAIClient(api_key=OPENAI_API_KEY)
        else:
            raise ValueError("provider は 'gemini' か 'openai' を指定してください。")

    # --- JSON生成（可能ならAPI側でスキーマ拘束） ---
    def generate_json(self, prompt: str, schema_llm: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
        if self.provider == "gemini":
            def _call():
                cfg = {"response_mime_type": "application/json"}
                if schema_llm:
                    cfg["response_schema"] = schema_llm
                r = self._gclient.models.generate_content(model=self.model, contents=prompt, config=cfg)
                return r
            resp = retry(_call)
            text = (getattr(resp, "text", "") or "").strip()
            try:
                return _loads_json_maybe_fenced(text)
            except Exception as e:
                raise RuntimeError(f"Gemini 出力のJSON化に失敗: {e}\n--- RAW ---\n{text}") from e

        # OpenAI
        # 1) responses API の json_schema を試す → 2) 失敗時は通常テキスト＋JSON抽出
        if self.provider == "openai":
            # 1) responses API
            try:
                def _call_resp():
                    # OpenAI Responses API
                    return self._oclient.responses.create(
                        model=self.model,
                        input=prompt,
                        response_format=(
                            {"type":"json_schema","json_schema":{"name":"DocSchema","schema": schema_llm or {"type":"object"}}}
                        ),
                    )
                r = retry(_call_resp)
                # 最初の出力テキスト抽出
                text = ""
                if r and r.output and len(r.output) > 0:
                    # unified Responses形式
                    parts = []
                    for item in r.output:
                        if item.type == "output_text":
                            parts.append(item.text or "")
                    text = "".join(parts).strip()
                if not text and hasattr(r, "output_text"):
                    text = (r.output_text or "").strip()
                if not text:
                    # fallback to first message content if present
                    text = json.dumps(r.to_dict(), ensure_ascii=False)
                return _loads_json_maybe_fenced(text)
            except Exception:
                # 2) chat/completions フォールバック（JSONのみ出力させるプロンプト）
                def _call_chat():
                    sys = "You are a helpful assistant. Output JSON only with no extra text."
                    return self._oclient.chat.completions.create(
                        model=self.model,
                        messages=[{"role":"system","content":sys},{"role":"user","content":prompt}],
                        temperature=0.2,
                    )
                r = retry(_call_chat)
                text = (r.choices[0].message.content or "").strip()
                try:
                    return _loads_json_maybe_fenced(text)
                except Exception as e:
                    raise RuntimeError(f"OpenAI 出力のJSON化に失敗: {e}\n--- RAW ---\n{text}") from e

        raise RuntimeError("サポート外の provider")

    # --- 通常テキスト生成 ---
    def generate_text(self, prompt: str) -> str:
        if self.provider == "gemini":
            def _call():
                return self._gclient.models.generate_content(model=self.model, contents=prompt)
            r = retry(_call)
            return (getattr(r, "text", "") or "").strip()

        if self.provider == "openai":
            # 可能なら Responses API → フォールバックは chat
            try:
                def _call_resp():
                    return self._oclient.responses.create(model=self.model, input=prompt)
                r = retry(_call_resp)
                text = ""
                if r and r.output and len(r.output) > 0:
                    parts = []
                    for item in r.output:
                        if item.type == "output_text":
                            parts.append(item.text or "")
                    text = "".join(parts).strip()
                if not text and hasattr(r, "output_text"):
                    text = (r.output_text or "").strip()
                return text
            except Exception:
                def _call_chat():
                    return self._oclient.chat.completions.create(
                        model=self.model,
                        messages=[{"role":"user","content":prompt}],
                        temperature=0.2,
                    )
                r = retry(_call_chat)
                return (r.choices[0].message.content or "").strip()

        raise RuntimeError("サポート外の provider")

# ============================================================
# データ構造
# ============================================================
@dataclass
class Manual:
    title: str
    description: str
    audience: Optional[str]
    prerequisites: List[str]
    input_spec: str
    output_spec: str
    constraints: List[str]
    cautions: List[str]
    examples: Optional[List[Dict[str, Optional[str]]]]
    def to_json(self) -> str:
        return pretty_json(asdict(self))

@dataclass
class LogEntry:
    role: str
    message: str

@dataclass
class AgentState:
    manual: Optional[Manual] = None
    steps: Optional[Dict[str, Any]] = None
    version: int = 0
    logs: List[LogEntry] = None
    def add_log(self, role: str, message: str):
        if self.logs is None:
            self.logs = []
        self.logs.append(LogEntry(role, message))

# ============================================================
# 役割別プロンプト
# ============================================================
PLANNER_PROMPT = (
    "あなたはタスクプランナーです。\n"
    "目的: 入力に対する『実務で使えるマニュアル＋手順書』を仕上げる。\n"
    "現在の状況とユーザー指示に基づき、次アクションを1語で決定。\n"
    "候補: DESIGN, CRITIC, EDIT, HALT\n"
    "- DESIGN: マニュアル/手順を新規/全面生成\n"
    "- CRITIC: 現行案の問題点を列挙\n"
    "- EDIT: 問題点と指示をすべて反映し修正版を出す\n"
    "- HALT: 更なる改善不要\n"
    '出力は必ず {\"action\":\"DESIGN\"} のようなJSONのみ。'
)

DESIGNER_MANUAL_PROMPT = (
    "あなたはマニュアルの専門家です。\n"
    "次の依頼文から、指定キーを必ず含むJSON（manual）だけを返してください。\n"
    "keys: title, description, audience(null可), prerequisites(list[str]), input_spec, output_spec, constraints(list[str]), cautions(list[str]), examples(list[{{name,content}}]|null)\n"
    "余計な文章は一切出力しない。\n"
    "依頼文:\n{request_text}"
)


DESIGNER_STEPS_PROMPT = (
    "あなたは手順設計の専門家です。\n"
    "以下のマニュアル（manual）とユーザー修正指示に基づき、実務で実行可能な手順をJSONのみで返してください。\n"
    "JSON構造は steps: [{{id,title,instruction,notes?,expected_output?,checklist?}}]。\n"
    "手順は3〜10ステップ程度、各ステップは行動可能な命令文で、重複・曖昧・誘導は避ける。\n"
    "manual:\n{manual_json}\n"
    "修正指示:\n{instruction}"
)

CRITIC_PROMPT = (
    "あなたはレビュー担当です。\n"
    "以下を評価し、問題点をJSONのみで返してください: {{\"issues\":[\"...\", ...]}}\n"
    "- JSON Schema 準拠か（必須キー・型・項目の妥当性）\n"
    "- ユーザー修正指示の反映\n"
    "- 重複/曖昧/誘導/過不足\n"
    "- 実務上の妥当性（実行可能性、負荷、順序）\n"
    "- セキュリティ/安全上の注意\n"
    "manual:\n{manual_json}\n"
    "steps:\n{steps_json}\n"
    "検証エラー(空可):\n{validation_errors}\n"
    "修正指示:\n{instruction}"
)


EDITOR_PROMPT = (
    "あなたは編集者です。\n"
    "次をすべて反映し、完全な JSON（manual と steps を含む）を返してください。\n"
    "- manual\n"
    "- ユーザー修正指示\n"
    "- Critic の指摘（issues）\n"
    "- 直前の steps（修正起点）\n"
    "JSON構造は {{manual: MANUAL_SCHEMA, steps: [..]}} の形。余計な文章は禁止。\n"
    "manual:\n{manual_json}\n"
    "修正指示:\n{instruction}\n"
    "Criticの指摘:\n{critic_json}\n"
    "直前のsteps:\n{steps_json}"
)


# ============================================================
# LLM 呼び出し
# ============================================================
def llm_json(llm: LLMClient, prompt: str, schema_llm: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
    return llm.generate_json(prompt, schema_llm)

def llm_text(llm: LLMClient, prompt: str) -> str:
    return llm.generate_text(prompt)

# ============================================================
# エージェント
# ============================================================
class ManualAgent:
    def __init__(self, llm: LLMClient):
        self.llm = llm
    def run(self, request_text: str) -> Manual:
        data = llm_json(self.llm, DESIGNER_MANUAL_PROMPT.format(request_text=request_text), MANUAL_SCHEMA_LLM)
        ok, err = validate_or_errors(manual_validator, data)
        if not ok:
            raise RuntimeError(f"manual がスキーマに適合しません:\n{err}\n--- RAW ---\n{pretty_json(data)}")
        return Manual(**data)

class StepsAgent:
    def __init__(self, llm: LLMClient):
        self.llm = llm
    def design(self, manual: Manual, instruction: str) -> Dict[str, Any]:
        p = DESIGNER_STEPS_PROMPT.format(manual_json=manual.to_json(), instruction=instruction or "")
        return llm_json(self.llm, p, STEPS_SCHEMA_LLM)
    def critic(self, manual: Manual, instruction: str, steps: Dict[str, Any], validation_errors: str) -> Dict[str, Any]:
        p = CRITIC_PROMPT.format(
            manual_json=manual.to_json(),
            steps_json=pretty_json(steps),
            validation_errors=validation_errors or "",
            instruction=instruction or "",
        )
        text = llm_text(self.llm, p)
        try:
            j = _loads_json_maybe_fenced(text)
            issues = j.get("issues", [])
            if not isinstance(issues, list):
                issues = [str(issues)]
            return {"issues": [s for s in issues if str(s).strip()]}
        except Exception:
            return {"issues": [text]}
    def edit(self, manual: Manual, instruction: str, critic_json: Dict[str, Any], steps: Dict[str, Any]) -> Dict[str, Any]:
        p = EDITOR_PROMPT.format(
            manual_json=manual.to_json(),
            instruction=instruction or "",
            critic_json=pretty_json(critic_json),
            steps_json=pretty_json(steps),
        )
        # Editor では最終完成形（manual+steps）を要求するので DOC_SCHEMA で拘束
        out = llm_json(self.llm, p, DOC_SCHEMA_LLM)
        ok, err = validate_or_errors(doc_validator, out)
        if not ok:
            raise RuntimeError(f"Editor 出力がスキーマに適合しません:\n{err}\n--- RAW ---\n{pretty_json(out)}")
        return out

class Orchestrator:
    def __init__(self, provider: str="gemini", model_name: str="gemini-2.5-flash"):
        self.llm = LLMClient(provider, model_name)
        self.manual_agent = ManualAgent(self.llm)
        self.steps_agent = StepsAgent(self.llm)
        self.state = AgentState(manual=None, steps=None, version=0, logs=[])
    def generate_manual(self, request_text: str) -> Manual:
        m = self.manual_agent.run(request_text)
        self.state.manual = m
        self.state.add_log("Designer(Manual)", "依頼文から Manual を抽出")
        return m
    def run_until_done(self, instruction: str = "", max_iters: int = 3) -> Dict[str, Any]:
        if not self.state.manual:
            raise RuntimeError("先に generate_manual() を呼んで Manual を作成してください。")
        action = "DESIGN"
        combined = None
        for it in range(1, max_iters + 1):
            self.state.add_log("Planner", f"iter={it}, action={action}")
            if action == "DESIGN" or self.state.steps is None:
                steps = self.steps_agent.design(self.state.manual, instruction)
                self.state.version += 1
                self.state.steps = steps
                self.state.add_log("Designer(Steps)", f"v{self.state.version} を生成")
            ok, err = validate_or_errors(steps_validator, self.state.steps)
            self.state.add_log("Validator", "OK" if ok else f"Schema errors:\n{err}")
            critic_j = self.steps_agent.critic(self.state.manual, instruction, self.state.steps, ("" if ok else err))
            issues = [s for s in critic_j.get("issues", []) if str(s).strip()]
            self.state.add_log("Critic", f"issues={len(issues)}" + ("" if not issues else "（要修正）"))
            if ok and not issues:
                # 仕上げ：manual + steps を束ねて最終検証
                combined = {"manual": json.loads(self.state.manual.to_json()), "steps": self.state.steps.get("steps", [])}
                ok2, err2 = validate_or_errors(doc_validator, combined)
                if ok2:
                    self.state.add_log("Planner", "HALT（十分に良好）")
                    break
            # 修正
            edited = self.steps_agent.edit(self.state.manual, instruction, critic_j, self.state.steps)
            self.state.version += 1
            self.state.steps = {"steps": edited["steps"]}
            self.state.manual = Manual(**edited["manual"])
            ctx = f"iter={it}, version={self.state.version}, schema=OK, last_issues={len(issues)}, user_instruction={instruction!r}"
            # Planner 判断
            p = PLANNER_PROMPT + "\n\n--- CONTEXT ---\n" + ctx
            action_resp = self.llm.generate_text(p)
            try:
                j = _loads_json_maybe_fenced(action_resp)
                action = str(j.get("action", "")).upper()
            except Exception:
                up = action_resp.upper()
                action = next((k for k in ("DESIGN","CRITIC","EDIT","HALT") if k in up), "CRITIC")
            if action not in ("DESIGN","CRITIC","EDIT"):
                action = "HALT"
            if action == "HALT":
                self.state.add_log("Planner", "HALT（計画判断）")
                break
        if combined is None:
            combined = {"manual": json.loads(self.state.manual.to_json()), "steps": self.state.steps.get("steps", [])}
        return {
            "version": self.state.version,
            "manual": combined["manual"],
            "steps": {"steps": combined["steps"]},
            "logs": [{"role": l.role, "message": l.message} for l in (self.state.logs or [])],
        }

# ============================================================
# 可読プレビュー
# ============================================================
def render_manual(m: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"# {m.get('title','')}")
    if m.get("description"): lines.append(m["description"])
    if m.get("audience"): lines.append(f"- 対象読者: {m['audience']}")
    if m.get("prerequisites"): lines.append("## 前提条件\n" + "\n".join(f"- {x}" for x in m["prerequisites"]))
    if m.get("input_spec"): lines.append("## 入力仕様\n" + m["input_spec"])
    if m.get("output_spec"): lines.append("## 出力仕様\n" + m["output_spec"])
    if m.get("constraints"): lines.append("## 制約\n" + "\n".join(f"- {x}" for x in m["constraints"]))
    if m.get("cautions"): lines.append("## 注意事項\n" + "\n".join(f"- {x}" for x in m["cautions"]))
    ex = m.get("examples")
    if ex:
        lines.append("## 例")
        for e in ex:
            name = e.get("name","example")
            content = e.get("content") or ""
            lines.append(f"### {name}\n{content}")
    return "\n\n".join(lines)

def render_steps(s: Dict[str, Any]) -> str:
    lines: List[str] = []
    for st in s.get("steps", []):
        lines.append(f"■ ({st.get('id','')}) {st.get('title','')}")
        lines.append(f"  指示: {st.get('instruction','')}")
        if st.get("expected_output"): lines.append(f"  期待される出力: {st['expected_output']}")
        if st.get("notes"): lines.append(f"  備考: {st['notes']}")
        chk = st.get("checklist") or []
        if chk:
            lines.append("  チェックリスト:")
            for c in chk:
                lines.append(f"    - {c}")
    return "\n".join(lines)

# ============================================================
# CLI / UI
# ============================================================
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--provider", type=str, default="gemini", choices=["gemini","openai"], help="LLMプロバイダ")
    p.add_argument("--model", type=str, default="gemini-2.5-flash", help="モデル名（Gemini or OpenAI）")
    p.add_argument("--request", type=str, default="『PythonスクリプトからCSVを読み込み、集計してJSONで出力する』ためのマニュアルと実行手順が欲しい。利用者は初学者。10分で実行可能に。")
    p.add_argument("--instruction", type=str, default="")
    p.add_argument("--iters", type=int, default=3)
    p.add_argument("--ui", action="store_true", help="Gradio UI を起動")
    p.add_argument("--share", action="store_true", help="外部公開URL")
    args = p.parse_args()

    orch = Orchestrator(args.provider, args.model)

    def do_generate(provider: str, model: str, req: str, instr: str, iters: int):
        # プロバイダ/モデル切替のためインスタンス作り直し
        local_orch = Orchestrator(provider, model)
        local_orch.state = AgentState(manual=None, steps=None, version=0, logs=[])
        local_orch.generate_manual(req.strip())
        out = local_orch.run_until_done(instruction=instr.strip(), max_iters=max(1, min(5, int(iters))))
        manual_json = pretty_json(out["manual"])
        steps_json = pretty_json(out["steps"])
        manual_preview = render_manual(out["manual"])
        steps_preview = render_steps(out["steps"])
        logs = "\n".join(f"[{l['role']}] {l['message']}" for l in out["logs"])
        ver = f"v{out['version']}"
        # まとめプレビュー
        full_preview = manual_preview + "\n\n----\n\n" + steps_preview
        return manual_json, steps_json, full_preview, logs, ver

    if args.ui:
        with gr.Blocks(title="マニュアル＆手順作成エージェント（自己改善）") as demo:
            gr.Markdown("## マニュアル＆手順作成エージェント（ルールベースなし / 自己改善ループ）")
            with gr.Row():
                with gr.Column(scale=1):
                    provider = gr.Dropdown(label="Provider", choices=["gemini","openai"], value=args.provider)
                    model = gr.Textbox(label="Model", value=args.model)
                    req = gr.Textbox(label="依頼文", lines=6, value=args.request)
                    instr = gr.Textbox(label="修正指示（任意）", lines=4, value=args.instruction)
                    iters = gr.Slider(1, 5, value=min(max(args.iters,1),5), step=1, label="自己改善ループ回数")
                    btn = gr.Button("生成")
                with gr.Column(scale=1):
                    manual_box = gr.Code(label="Manual(JSON)")
                    steps_box = gr.Code(label="Steps(JSON)")
                with gr.Column(scale=1):
                    preview_box = gr.Textbox(label="プレビュー", lines=26)
                    log_box = gr.Textbox(label="エージェントログ", lines=20)
                    ver = gr.Textbox(label="バージョン", interactive=False)
            btn.click(do_generate, inputs=[provider, model, req, instr, iters],
                      outputs=[manual_box, steps_box, preview_box, log_box, ver])
        demo.launch(share=args.share)
    else:
        orch.generate_manual(args.request)
        out = orch.run_until_done(instruction=args.instruction, max_iters=max(1, min(5, int(args.iters))))
        print("=== Manual ===")
        print(pretty_json(out["manual"]))
        print("\n=== Steps ===")
        print(pretty_json(out["steps"]))
        print("\n=== Logs ===")
        for l in out["logs"]:
            print(f"[{l['role']}] {l['message']}")
        print(f"\nVersion: v{out['version']}")
