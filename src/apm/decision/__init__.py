"""Decision Engine (spec §14, §39-40, §45-46, §57, §61). Milestone M4.

ClaudeProvider abstraction (Anthropic API + Mock impls), context builder (relevance
retrieval, never a full DB dump), structured decision contract (pydantic -> JSON schema
via tool-use), decision validation, and session/identity management. WAIT is a
first-class decision (spec §15).
"""
