from google.adk.tools.agent_tool import AgentTool
from google.adk.agents import Agent
from google.adk.tools import google_search

from ..shard_lib.constants import MODEL_ID
from ..shard_lib.prompts import research_prompt


_research_agent = Agent(
    name = "research_agent",
    model = MODEL_ID,
    instruction=(research_prompt),
    tools = [google_search]
)

research_agent = AgentTool(agent = _research_agent)
