-- bring-your-own key for any supported ai provider, not only claude (#11)
ALTER TABLE guilds RENAME COLUMN claude_key_enc TO ai_key_enc;
ALTER TABLE guilds
    ADD COLUMN ai_provider text,  -- e.g. 'claude', 'openai', 'gemini', 'deepseek', 'openrouter'
    ADD COLUMN ai_model    text;
