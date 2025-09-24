# tools_for_agents.py
import os
import datetime
import asyncio
import httpx
import inspect
import requests
import json
from typing import List
from agents import Agent, Runner, function_tool, set_default_openai_key

# APIキーは環境変数に入れること
set_default_openai_key(os.getenv("OPENAI_API_KEY"))
BRAVE_API_KEY  = os.getenv("BRAVE_API_KEY")

async def brave_search_api(query: str, count: int = 5, lang: str = "ja"):
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": query,"count":count}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers=headers, params=params)
        j = resp.json()
        results = j.get("web", {}).get("results", [])
        out = []
        for item in results:
            out.append(item)

        return out

@function_tool
async def web_scraper(url: str):
    print("[Tool call] web_scraper")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

@function_tool
async def search_engine(query: str):
    print("[Tool call] search_engine")
    content_headers = await brave_search_api(query)
    title_url = []
    for content in content_headers :
        title_url.append({"title": content.get("title"), "url": content.get("url")})

    return title_url

# --- 単純ツール（例: 時刻取得） ---
@function_tool
async def get_time() -> str:
    print("[Tool call] now time")
    now = datetime.datetime.now()
    return "現在時刻: " + now.isoformat()

web_search_agent = Agent(
    name = "web_search_agent",
    model = "gpt-4o",
    instructions=(
        "貴方は検索係のエージェントです。"
        "ツールのweb検索エンジンを持っています"
        "不足している情報を検索エンジンを用いて収集出来ます。"
        "キーワードを元に、不足している情報を検索しなさい"
        "但し、検索機能の利用は最小限に留めること"
        "キーワードを工夫しなさい"
        # "貴方には2つのtoolを利用出来ます。"
        # "1. search_engine : キーワードを元にtitleとurlを取得出来ます。この関数にはキーワード文字列として、引数に指定できます。"
        # "2. web_scraper : urlを元にweb pager 上のコンテキストを取得出来ます。この関数にはurlを文字列として、引数に指定できます。"
        # "初めにsearch_engineを利用し、関係が有りそうなtitleをからurlを抽出し、web_scraperを利用してコンテキストを取得しなさい。" 
        # "但し、web_scraperを使うのは1度に1回までの最小限に留めるように。"
    ),
    tools=[{"type": "web_search"}]
    #tools=[search_engine, web_scraper]
)

@function_tool
async def call_web_search_tool(query: str) -> str:
    res = await run_and_log(web_search_agent, query) 
    try:
        print(res.final_output)
        return res.final_output
    except Exception:
        return str(res)

# --- エージェント定義（まず素のエージェントを作る） ---
plan_agent = Agent(
    name="plan_Agent",
    model="gpt-4o",
    instructions=(
        "目的を達成するための手順を出力してください。"
        "必要があれば渡された tools を使って調べて構いません。"
        # "先頭に 'PLAN:' 行を必ず含めてください。"
    ),
    tools=[get_time, {"type": "web_search"}]  # plan は get_time を使える
)

# plan_agent を同期で呼び出すツール化ラッパー
@function_tool
async def call_plan_tool(plan_input: str) -> str:
    """plan_agent を Runner 経由で呼び出すツール（モデルがこれを呼べる）"""
    res = await run_and_log(plan_agent, plan_input)
    try:
        print(res.final_output)
        return res.final_output
    except Exception:
        return str(res)

# eval_agent を作る（eval は必要なら call_plan_tool を呼べる）
eval_agent = Agent(
    name="eval_Agent",
    model="gpt-4o",
    instructions=(
        "受け取った計画を評価してください。必要ならツール call_plan_tool を呼んで改訂版を得てよいです。\n"),
    tools=[call_plan_tool]  # eval が plan を再生成できるようにする
    )

# eval_agent を同期で呼ぶラッパー（必要なら外部から使える）
@function_tool
async def call_eval_tool(eval_input: str) -> str:
    res = await run_and_log(eval_agent, eval_input)
    try:
        print(res.final_output)
        return res.final_output
    except Exception:
        return str(res)

# output_agent を定義（最終要約用）
output_agent = Agent(
    name="output_agent",
    model="gpt-4o",
    instructions=(
        "最終的な成果物を読みやすく整理して出力してください。"
    )
)

@function_tool
async def call_output_tool(output_input: str) -> str:
    res = await run_and_log(output_agent, output_input)
    try:
        print(res.final_output)
        return res.final_output
    except Exception:
        return str(res)

# Interpretation エージェントは必要に応じて上のツールを使える
def Interpretation_Agent() -> Agent:
    return Agent(
        name="Interpretation_Agent",
        model="gpt-4o",
        instructions=(
            "与えられた命令の目的を抽出しなさい。抽出した目的を達成するためにツールやエージェントを利用しなさい。\n"
            "貴方には3つのツールを実行出来ます。"
            "1. call_plan_tool : 情報を収集するエージェント。目的達成の計画を考え、情報が不足していればツールを実行し検索を行う。\n"
            "2. call_eval_tool : 収集した情報を評価するエージェント。情報が不足していれば再度「call_plan_tool」を実行する。\n"
            "3. call_output_tool  :  収集した情報を整形するエージェント。収集及び評価した情報を元に、目的の内容に沿った形に整形する。\n"
            "4. web_search  :  web検索エンジンを備えたエージェント"
            "必ず出力前、call_eval_toolとcall_output_toolを呼び出して評価と整形を行うこと。"
            "但し、余りトークン上限も存在するため検索機能は極力利用を控えること。どうしても必要なときだけ使うように"
        ),
        tools=[call_plan_tool, call_eval_tool, call_output_tool, get_time, call_web_search_tool]
        #handoffs=[plan_agent, eval_agent, output_agent, web_search_agent]
    )


async def run_and_log(agent_factory_or_obj, prompt: str):
    # Agent オブジェクトを得る（ファクトリの戻り値が awaitable なら await）
    if callable(agent_factory_or_obj):
        maybe = agent_factory_or_obj()
        agent_obj = await maybe if inspect.isawaitable(maybe) else maybe
    else:
        maybe = agent_factory_or_obj
        agent_obj = await maybe if inspect.isawaitable(maybe) else maybe

    name = getattr(agent_obj, "name", "<unknown>")
    print(f"[Agent call] name={name} , prompt={prompt!r}")
    res = await Runner.run(agent_obj, prompt)
    print(f"[Agent finished] name={name}")
    return res

async def run_async_with_log(agent_factory_or_obj, prompt: str):
    # ファクトリが callable なら呼ぶ（戻り値が awaitable なら await）
    if callable(agent_factory_or_obj):
        maybe = agent_factory_or_obj()
        if inspect.isawaitable(maybe):
            agent_obj = await maybe
        else:
            agent_obj = maybe
    else:
        maybe = agent_factory_or_obj
        if inspect.isawaitable(maybe):
            agent_obj = await maybe
        else:
            agent_obj = maybe

    print("[Agent call] name=", getattr(agent_obj, "name", "<unknown>"))
    return await Runner.run(agent_obj, prompt)

async def main():
    interp_agent = Interpretation_Agent()
    prompt = "このタスクの目的を示してください：大阪に日本橋でレトロなPCを取り扱っている店を知りたい。"
    print("[call] Interpretation_Agent")
    interp_res = await run_async_with_log(interp_agent, prompt)
    print("== Interpretation output ==")
    try:
        print(interp_res.final_output)
    except Exception:
        print(str(interp_res))

# --- 使用例 ---
if __name__ == "__main__":
    asyncio.run(main())
