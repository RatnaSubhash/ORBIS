import asyncio
import json
import sys
import os
sys.path.insert(0, os.path.abspath("."))
from core.agent.llm.ollama import OllamaProvider

async def main():
    llm = OllamaProvider()
    
    tools_catalog = [
        {"name": "echo", "description": "Echoes a message or responds directly to conversational questions or requests for clarification.", "parameters": {"type": "object", "properties": {"message": {"type": "string"}}}},
        {"name": "web_research", "description": "Searches the web for external information.", "parameters": {"type": "object", "properties": {"action": {"type": "string"}, "query": {"type": "string"}}}},
        {"name": "content_generator", "description": "Synthesizes structured, formal multi-section documents/reports.", "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}}},
    ]
    
    system_prompt = (
        "You are the hierarchical task planning engine of ORBIS, an autonomous desktop agent.\n"
        "Given a user prompt and available tools, create a structured execution plan.\n"
        "You MUST respond ONLY with a valid JSON object strictly conforming to the TaskPlan schema.\n"
        "Do NOT output markdown code blocks, conversational filler, or chain-of-thought.\n"
        "\n"
        "PLANNING & TOOL SELECTION RULES:\n"
        "1. For simple conversational questions, direct answers, greetings, or explanations (e.g., 'What is AI?', 'Explain computer networks', 'What can you do?'), use the 'echo' tool with the direct, concise answer in the 'message' parameter. Do NOT use 'content_generator' or 'document' unless the user explicitly requested creating a document, file, or report.\n"
        "2. For questions asking for current, latest, or real-time information, or current office holders / leadership roles (e.g., 'latest news', 'current version of X', 'Who is the Chief Minister/President/CEO of Y?'), use the 'web_research' tool with action 'research'. Do NOT invent current facts or office holders from memory.\n"
        "3. CRITICAL GROUNDING & AMBIGUITY RULES:\n"
        "   - If the user prompt is ambiguous, nonsensical, unclear, or asks about unknown or unverified entities/versions (e.g., 'Do you know Raja version?', 'What is the city where you are famous for?'), do NOT invent, assume, or hallucinate facts or version numbers.\n"
        "   - Instead, use the 'echo' tool with a clarifying question or clearly state that the request is unclear or that available information is insufficient to answer (e.g., 'I am not sure what Raja refers to. Could you clarify which software or project you mean?').\n"
        "\n"
        "TaskPlan JSON Schema:\n"
        "{\n"
        '  "request_id": "req-1",\n'
        '  "user_prompt": "<prompt>",\n'
        '  "task_type": "single_step",\n'
        '  "root_step_ids": ["step1"],\n'
        '  "steps": {\n'
        '    "step1": {\n'
        '      "step_id": "step1",\n'
        '      "tool_name": "<registered_tool_name>",\n'
        '      "parameters": {<key_value_pairs>},\n'
        '      "dependencies": []\n'
        '    }\n'
        '  }\n'
        "}"
    )

    test_prompts = [
        "Who is the Chief Minister of Tamil Nadu?",
        "Who is the Prime Minister of India?",
        "Who is the current CEO of Microsoft?",
        "Who is the President of India?",
    ]

    for p in test_prompts:
        user_prompt = f"Available Registered Tools:\n{json.dumps(tools_catalog, indent=2)}\n\nUser Prompt: {p}\n\nJSON Response:"
        resp = await llm.generate(prompt=user_prompt, system_prompt=system_prompt, format="json")
        print(f"=== PROMPT: {p} ===")
        print(resp)
        print()

if __name__ == "__main__":
    asyncio.run(main())
