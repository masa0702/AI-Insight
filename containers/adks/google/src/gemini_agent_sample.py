import asyncio
from google.adk.tools.agent_tool import AgentTool
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools import google_search
from google.genai import types

APP_NAME = "multi_agent_demo"
USER_ID = "user_001"
SESSION_ID = "sess_001"
MODEL_ID = "gemini-2.0-flash"

# 下位エージェント（ツールは付けない）
# info_agent（ツール削除）
_info_agent = LlmAgent(
    name="info_agent",
    model=MODEL_ID,
    instruction=("貴方の役目はコンテキストを要約し、必要事項を抽出して返すこと。")
)

_plan_agent = LlmAgent(
    name="plan_agent",
    model=MODEL_ID,
    instruction=("貴方の役目は与えられたタスクの処理手順を考えること")
)

_research_agent = LlmAgent(
    name = "research_agent",
    model = MODEL_ID,
    instruction=("貴方の役目は与えられたタスクで不足している情報を検索することです"
                 "貴方にはgoogle_searchを使う権限を与えられています。"
                 "google_searchを利用して、必用に応じて検索をかけて下さい。"),
    tools = [google_search]
)


info_agent = AgentTool(agent=_info_agent)
plan_agent = AgentTool(agent=_plan_agent)
research_agent = AgentTool(agent=_research_agent)

coordinator = LlmAgent(
    name="coordinator_agent",
    model=MODEL_ID,
    instruction=(
            "与えられた命令の目的を抽出しなさい。抽出した目的を達成するためにツールやエージェントを利用しなさい。"
            "貴方には2つのエージェントを実行出来ます"
            "1. info_agent : 当たらえたテキストから必用事項を抽出して要約することに長けたエージェント"
            "2. plan_agent : 仕事をこなすためのプランを考えるのに長けたエージェント"
            "3. research_agent : 不足している情報を検索して補うことに長けたエージェント"
            "必ず、テキストから要求を吸い出し(info_agentを利用)、そこから計画を立てて(plan_agentを利用)、必用に応じて検索をかけてタスクをこなすこと。"
            "検索エージェントを利用した場合は、何を検索して何を得たのかを完結に説明すること"
            "なお、出力は日本語で行うこと。"
            ),
    tools=[info_agent, plan_agent, research_agent]
    #sub_agents=[info_agent, plan_agent]
)

async def main():
    # セッション準備
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=SESSION_ID,
    )

    runner = Runner(
        app_name=APP_NAME,
        agent=coordinator,
        session_service=session_service,
    )

    # ユーザー入力
    content = types.Content(
        role="user",
        parts=[types.Part(text="大阪吹田市の今日の天気を知りたい。検索を利用してほしい。また、情報源を教えてほしい")]
    )

    # 実行（イベントを順次受け取り、最終応答を取得）
    try:
        events = runner.run_async(
            user_id=USER_ID,
            session_id=session.id,
            new_message=content,
        )

        # ループ前で初期化
        used_tools = set()

        # イベント受信
        async for event in events:

            if event.content and event.content.parts:
                part = event.content.parts[0]
                if getattr(part, "function_call", None):
                    print("function_call:", part.function_call.name, part.function_call.arguments)
            
            # ツール呼び出しがあれば記録
            if getattr(event, "tool_name", None):
                tool_name = event.tool_name
                if tool_name is not None:
                    used_tools.add(tool_name)

            # 最終応答の処理
            if event.is_final_response():
                if event.content and event.content.parts:
                    part = event.content.parts[0]
                    if hasattr(part, "text") and part.text is not None:
                        print(part.text)

                        # 使われたツール名の表示（重複排除・整列）
                        tools_list = list(used_tools)
                        tools_list_sorted = sorted(tools_list)

                        if len(tools_list_sorted) > 0:
                            print("使用ツール: " + ", ".join(tools_list_sorted))
                        else:
                            print("使用ツール: なし")
                    else:
                        print("最終応答を取得できませんでした。")
    except Exception as e:
        # 簡易エラーハンドリング
        print(f"エラー: {e}")

if __name__ == "__main__":
    asyncio.run(main())
