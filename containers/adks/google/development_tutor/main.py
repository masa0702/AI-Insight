from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types
from .shard_lib.constants import MODEL_ID, AGENT_NAME, USER_ID, SESSION_ID

from .agent import coordinator_agent
import asyncio

async def main():
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name = AGENT_NAME,
        user_id = USER_ID,
        session_id = SESSION_ID
    )

    runner = Runner(
        app_name = AGENT_NAME,
        agent = coordinator_agent,
        session_service = session_service,
    )

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

# if __name__ == "__main__":
#     asyncio.run(main())
