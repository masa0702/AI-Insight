# agent_survey_agent.py
# ============================================================
# 依頼 → Brief 生成 → アンケート初稿 → 自己改善ループ（Planner/Designer/Critic/Editor）
# ・.env から GOOGLE_API_KEY を読み込み（python-dotenv）
# ・google genai 新SDK: from google import genai / genai.Client()
# ・JSON Mode: response_mime_type=application/json / response_schema（大文字タイプ & nullable）
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
from google import genai  # 新SDK

# ---- .env からキー読込 & クライアント作成 ----
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise RuntimeError(".env に GOOGLE_API_KEY がありません。例：GOOGLE_API_KEY=xxxxxxxxxxxxxxxx")
client = genai.Client(api_key=API_KEY)

# ---- Structured Output 用 Schema（※SDK仕様に合わせた大文字タイプ & nullable）----
BRIEF_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "goal": {"type": "STRING"},
        "targets": {"type": "ARRAY", "items": {"type": "STRING"}},
        "region": {"type": "STRING"},
        "budget": {"type": "STRING", "nullable": True},
        "deadline": {"type": "STRING", "nullable": True},
        "constraints": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["goal", "targets", "region", "budget", "deadline", "constraints"],
    "propertyOrdering": ["goal", "targets", "region", "budget", "deadline", "constraints"],
}

QUESTIONNAIRE_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "sections": {
            "type": "ARRAY",
            "minItems": 1,
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "items": {
                        "type": "ARRAY",
                        "minItems": 1,
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "id": {"type": "STRING"},
                                "type": {
                                    "type": "STRING",
                                    "enum": [
                                        "single_choice",
                                        "multiple_choice",
                                        "rating_3",
                                        "rating_5",
                                        "rating_7",
                                        "text",
                                    ],
                                },
                                "text": {"type": "STRING"},
                                "options": {"type": "ARRAY", "items": {"type": "STRING"}, "nullable": True},
                            },
                            "required": ["id", "type", "text"],
                        },
                    },
                },
                "required": ["title", "items"],
                "propertyOrdering": ["title", "items"],
            },
        }
    },
    "required": ["sections"],
    "propertyOrdering": ["sections"],
}

# ---- ローカル検証用（jsonschema は “小文字タイプ” 期待なのでミラーを用意）----
BRIEF_SCHEMA_JSONSCHEMA = {
    "type": "object",
    "properties": {
        "goal": {"type": "string"},
        "targets": {"type": "array", "items": {"type": "string"}},
        "region": {"type": "string"},
        "budget": {"type": ["string", "null"]},
        "deadline": {"type": ["string", "null"]},
        "constraints": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["goal", "targets", "region", "budget", "deadline", "constraints"],
}
QUESTIONNAIRE_SCHEMA_JSONSCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "single_choice",
                                        "multiple_choice",
                                        "rating_3",
                                        "rating_5",
                                        "rating_7",
                                        "text",
                                    ],
                                },
                                "text": {"type": "string"},
                                "options": {"type": ["array", "null"], "items": {"type": "string"}},
                            },
                            "required": ["id", "type", "text"],
                        },
                    },
                },
                "required": ["title", "items"],
            },
        }
    },
    "required": ["sections"],
}
brief_validator = Draft202012Validator(BRIEF_SCHEMA_JSONSCHEMA)
q_validator = Draft202012Validator(QUESTIONNAIRE_SCHEMA_JSONSCHEMA)

# ---- ユーティリティ ----
def _extract_json(text: str) -> str:
    """```json ... ``` 等のフェンスを剥がし、純粋なJSON部分を抽出（内容は変更しない）"""
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

# ---- データ構造 ----
@dataclass
class Brief:
    goal: str
    targets: List[str]
    region: str
    budget: Optional[str]
    deadline: Optional[str]
    constraints: List[str]
    def to_json(self) -> str:
        return pretty_json(asdict(self))

# ---- 役割別プロンプト（波括弧はエスケープ済み）----
PLANNER_PROMPT = (
    "あなたはタスクプランナーです。\n"
    "目的: 高品質なアンケート仕様を作る。\n"
    "現在の状況とユーザー指示に基づき、次に行うべきアクションを1語で決めてください。\n"
    "候補: DESIGN, CRITIC, EDIT, HALT\n"
    "- DESIGN: アンケートを新規/全面生成する\n"
    "- CRITIC: 直前のアンケートを検査し問題点を列挙する\n"
    "- EDIT: CRITICの指摘とユーザーの修正指示を全て反映して修正版を出す\n"
    "- HALT: これ以上の改善不要\n"
    '出力は必ず次のJSONだけ: {{"action":"DESIGN"}} のように。'
)
DESIGNER_BRIEF_PROMPT = (
    "あなたはBrief抽出器です。\n"
    "次の依頼文から、指定したキーを必ず含むJSONのみを返してください。\n"
    "keys: goal(str), targets(list[str]), region(str), budget(str|null), deadline(str|null), constraints(list[str])\n"
    "targets には層（年齢・性別など）が判れば [\"20代\",\"男性\"] のように入れてください。\n"
    "不明は null または []。余計な文章は一切出力しない。\n"
    "依頼文:\n{request_text}"
)
DESIGNER_Q_PROMPT = (
    "あなたは調査アンケートの設計者です。\n"
    "以下のBriefと（あれば）ユーザーの修正指示に基づき、回答しやすく重複のないアンケート仕様をJSONのみで返してください。\n"
    "JSON構造は QUESTIONNAIRE_SCHEMA に準拠。各 item は id/type/text を必須。\n"
    "single_choice/multiple_choice には options を含める。全体で3〜6問に収める。\n"
    "Brief:\n{brief_json}\n"
    "修正指示:\n{instruction}"
)
CRITIC_PROMPT = (
    "あなたはアンケートの検査官です。\n"
    "以下の情報を見て、問題点を箇条書きで出してください。\n"
    "- JSON Schema準拠か（必須キー、型、optionsの有無など）\n"
    "- ユーザー修正指示がすべて反映されているか\n"
    "- 重複/曖昧/誘導的な文面がないか\n"
    "- 実務上の妥当性（回答できるか、過剰負荷でないか）\n"
    '出力はJSONのみ: {{"issues":[ "...", ... ]}}。空なら {{"issues": []}} を返す。\n'
    "Brief:\n{brief_json}\n"
    "修正指示:\n{instruction}\n"
    "現行アンケート:\n{questionnaire_json}\n"
    "検証エラー(なければ空文字):\n{validation_errors}"
)
EDITOR_PROMPT = (
    "あなたはアンケートの編集者です。\n"
    "次の要素を全て反映して、完全なJSON(QUESTIONNAIRE_SCHEMA準拠)を返してください。\n"
    "- Brief\n"
    "- ユーザー修正指示\n"
    "- Criticの指摘（issues）\n"
    "- 直前のアンケート（修正の起点）\n"
    "出力はJSONのみ、余計な文章は禁止。\n"
    "Brief:\n{brief_json}\n"
    "修正指示:\n{instruction}\n"
    "Criticの指摘:\n{critic_json}\n"
    "直前アンケート:\n{questionnaire_json}"
)

# ---- LLM 呼び出し（JSONモード優先）----
def llm_json(model: str, prompt: str, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    def _call():
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                **({"response_schema": schema} if schema else {}),
            },
        )
    resp = retry(_call)
    try:
        return _loads_json_maybe_fenced(resp.text or "")
    except Exception as e:
        raise RuntimeError(f"LLM出力のJSON化に失敗しました: {e}\n--- RAW ---\n{resp.text}") from e

def llm_text(model: str, prompt: str) -> str:
    def _call():
        return client.models.generate_content(model=model, contents=prompt)
    resp = retry(_call)
    return (resp.text or "").strip()

# ---- エージェント実装 ----
@dataclass
class LogEntry:
    role: str
    message: str

@dataclass
class AgentState:
    brief: Optional[Brief] = None
    questionnaire: Optional[Dict[str, Any]] = None
    version: int = 0
    logs: List[LogEntry] = None
    def add_log(self, role: str, message: str):
        if self.logs is None:
            self.logs = []
        self.logs.append(LogEntry(role, message))

class BriefAgent:
    def __init__(self, model_name: str = "gemini-2.5-flash"):
        self.model = model_name
    def run(self, request_text: str) -> Brief:
        prompt = DESIGNER_BRIEF_PROMPT.format(request_text=request_text)
        data = llm_json(self.model, prompt, BRIEF_SCHEMA)
        ok, err = validate_or_errors(brief_validator, data)
        if not ok:
            raise RuntimeError(f"Brief がスキーマに適合しません:\n{err}\n--- RAW ---\n{pretty_json(data)}")
        return Brief(**data)

class QuestionnaireAgent:
    def __init__(self, model_name: str = "gemini-2.5-flash"):
        self.model_json = model_name
        self.model_text = model_name
    def design(self, brief: Brief, instruction: str) -> Dict[str, Any]:
        p = DESIGNER_Q_PROMPT.format(brief_json=brief.to_json(), instruction=instruction or "")
        return llm_json(self.model_json, p, QUESTIONNAIRE_SCHEMA)
    def critic(self, brief: Brief, instruction: str, questionnaire: Dict[str, Any], validation_errors: str) -> Dict[str, Any]:
        p = CRITIC_PROMPT.format(
            brief_json=brief.to_json(),
            instruction=instruction or "",
            questionnaire_json=pretty_json(questionnaire),
            validation_errors=validation_errors or "",
        )
        text = llm_text(self.model_text, p)
        try:
            j = _loads_json_maybe_fenced(text)
            issues = j.get("issues", [])
            if not isinstance(issues, list):
                issues = [str(issues)]
            return {"issues": issues}
        except Exception:
            return {"issues": [text]}
    def edit(self, brief: Brief, instruction: str, critic_json: Dict[str, Any], questionnaire: Dict[str, Any]) -> Dict[str, Any]:
        p = EDITOR_PROMPT.format(
            brief_json=brief.to_json(),
            instruction=instruction or "",
            critic_json=pretty_json(critic_json),
            questionnaire_json=pretty_json(questionnaire),
        )
        return llm_json(self.model_json, p, QUESTIONNAIRE_SCHEMA)
    def plan(self, context_summary: str) -> str:
        text = llm_text(self.model_text, PLANNER_PROMPT + "\n\n--- CONTEXT ---\n" + context_summary)
        try:
            j = _loads_json_maybe_fenced(text)
            return str(j.get("action", "")).upper()
        except Exception:
            up = text.upper()
            for k in ("DESIGN", "CRITIC", "EDIT", "HALT"):
                if k in up:
                    return k
            return "CRITIC"

class SurveyOrchestrator:
    def __init__(self, model_name: str = "gemini-2.5-flash"):
        self.brief_agent = BriefAgent(model_name)
        self.qa = QuestionnaireAgent(model_name)
        self.state = AgentState(brief=None, questionnaire=None, version=0, logs=[])
    def generate_brief(self, request_text: str) -> Brief:
        brief = self.brief_agent.run(request_text)
        self.state.brief = brief
        self.state.add_log("Designer(Brief)", "依頼文からBriefを抽出")
        return brief
    def run_until_questionnaire(self, instruction: str = "", max_iters: int = 3) -> Dict[str, Any]:
        if not self.state.brief:
            raise RuntimeError("先に generate_brief() を呼んで Brief を作成してください。")
        action = "DESIGN"
        for it in range(1, max_iters + 1):
            self.state.add_log("Planner", f"iter={it}, action={action}")
            if action == "DESIGN" or self.state.questionnaire is None:
                q = self.qa.design(self.state.brief, instruction)
                self.state.version += 1
                self.state.questionnaire = q
                self.state.add_log("Designer", f"v{self.state.version} を生成")
            ok, err = validate_or_errors(q_validator, self.state.questionnaire)
            self.state.add_log("Validator", "OK" if ok else f"Schema errors:\n{err}")
            critic_j = self.qa.critic(self.state.brief, instruction, self.state.questionnaire, ("" if ok else err))
            issues = [s for s in critic_j.get("issues", []) if str(s).strip()]
            self.state.add_log("Critic", f"issues={len(issues)}" + ("" if not issues else "（要修正）"))
            if ok and not issues:
                self.state.add_log("Planner", "HALT（十分に良好）")
                break
            q2 = self.qa.edit(self.state.brief, instruction, critic_j, self.state.questionnaire)
            ok2, err2 = validate_or_errors(q_validator, q2)
            self.state.add_log("Editor", f"修正版を提案（schema={'OK' if ok2 else 'NG'}）")
            self.state.version += 1
            self.state.questionnaire = q2
            ctx = f"iter={it}, version={self.state.version}, schema={'OK' if ok2 else 'NG'}, last_issues={len(issues)}, user_instruction={instruction!r}"
            action = self.qa.plan(ctx)
            if action not in ("DESIGN", "CRITIC", "EDIT"):
                action = "HALT"
            if action == "HALT":
                self.state.add_log("Planner", "HALT（計画判断）")
                break
        return {
            "version": self.state.version,
            "brief": json.loads(self.state.brief.to_json()),
            "questionnaire": self.state.questionnaire,
            "logs": [{"role": l.role, "message": l.message} for l in (self.state.logs or [])],
        }

# ---- 可読プレビュー ----
def render_questionnaire(q: Dict[str, Any]) -> str:
    lines: List[str] = []
    for sec in q.get("sections", []):
        lines.append(f"■ {sec.get('title','')}")
        for it in sec.get("items", []):
            t = it.get("type", "")
            if t in ("single_choice", "multiple_choice"):
                opts = " / ".join(map(str, it.get("options", [])))
                lines.append(f"  - ({it.get('id')}) [{t}] {it.get('text')} 〔{opts}〕")
            else:
                lines.append(f"  - ({it.get('id')}) [{t}] {it.get('text')}")
    return "\n".join(lines)

# ---- CLI / UI ----
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--model", type=str, default="gemini-2.5-flash")
    p.add_argument("--request", type=str, default="生成AIの評判を日本で調査したい。対象は20代男性で、9月末までに完了。")
    p.add_argument("--instruction", type=str, default="")
    p.add_argument("--ui", action="store_true", help="Gradio UI を起動")
    p.add_argument("--share", action="store_true", help="外部公開URL")
    args = p.parse_args()

    orch = SurveyOrchestrator(args.model)

    def do_generate(req: str, instr: str, iters: int):
        orch.state = AgentState(brief=None, questionnaire=None, version=0, logs=[])
        orch.generate_brief(req.strip())
        out = orch.run_until_questionnaire(instruction=instr.strip(), max_iters=max(1, min(5, iters)))
        brief_json = pretty_json(out["brief"])
        q_json = pretty_json(out["questionnaire"])
        preview = render_questionnaire(out["questionnaire"])
        logs = "\n".join(f"[{l['role']}] {l['message']}" for l in out["logs"])
        return brief_json, q_json, preview, logs, f"v{out['version']}"

    if args.ui:
        with gr.Blocks(title="アンケート作成エージェント（Gemini / 自己改善）") as demo:
            gr.Markdown("## アンケート作成エージェント（Gemini / ルールベースなし / 自己改善ループ）")
            with gr.Row():
                with gr.Column(scale=1):
                    req = gr.Textbox(label="依頼文", lines=6, value=args.request)
                    instr = gr.Textbox(label="修正指示（任意）", lines=4, value=args.instruction)
                    iters = gr.Slider(1, 5, value=3, step=1, label="自己改善ループ回数")
                    btn = gr.Button("生成")
                with gr.Column(scale=1):
                    brief_box = gr.Code(label="Brief(JSON)")
                    q_box = gr.Code(label="Questionnaire(JSON)")
                with gr.Column(scale=1):
                    preview_box = gr.Textbox(label="プレビュー", lines=20)
                    log_box = gr.Textbox(label="エージェントログ", lines=20)
                    ver = gr.Textbox(label="バージョン", interactive=False)
            btn.click(do_generate, inputs=[req, instr, iters], outputs=[brief_box, q_box, preview_box, log_box, ver])
        demo.launch(share=args.share)
    else:
        orch.generate_brief(args.request)
        out = orch.run_until_questionnaire(instruction=args.instruction, max_iters=3)
        print("=== Brief ===")
        print(pretty_json(out["brief"]))
        print("\n=== Questionnaire ===")
        print(pretty_json(out["questionnaire"]))
        print("\n=== Logs ===")
        for l in out["logs"]:
            print(f"[{l['role']}] {l['message']}")
        print(f"\nVersion: v{out['version']}")
