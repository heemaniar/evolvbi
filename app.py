"""
app.py — EvolvBI entry point.

Run locally:
    python app.py

Or via ADK dev UI:
    adk web
"""

import asyncio

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from agents.sql_agent import sql_agent

SESSION_SERVICE = InMemorySessionService()
APP_NAME = "evolvbi"
USER_ID = "analyst"


async def ask(question: str) -> str:
    session = await SESSION_SERVICE.create_session(app_name=APP_NAME, user_id=USER_ID)
    runner = Runner(agent=sql_agent, app_name=APP_NAME, session_service=SESSION_SERVICE)

    message = Content(role="user", parts=[Part(text=question)])
    reply_parts = []

    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session.id,
        new_message=message,
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if part.text:
                    reply_parts.append(part.text)

    return "".join(reply_parts)


if __name__ == "__main__":
    questions = [
        "Which mall had the highest revenue last month?",
        "What are the top 5 categories by total sales across all malls?",
        "How many unique customers visited Kanyon in 2022?",
    ]
    for q in questions:
        print(f"\nQ: {q}")
        answer = asyncio.run(ask(q))
        print(f"A: {answer}")
