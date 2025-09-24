from google.adk.agents.llm_agent import Agent

from .shard_lib.constants import MODEL_ID
from .shard_lib.prompts import coordinator_prompt
from .tool_agents.research_agent import research_agent

root_agent = Agent(
    name = "coordinator_agent",
    model = MODEL_ID,
    instruction = (coordinator_prompt),
    tools = [research_agent]
)

