-- connection codes for the obsidian plugin (#39). only a sha-256 hash of each code is stored.
CREATE TABLE api_tokens (
    id           bigserial PRIMARY KEY,
    guild_id     bigint NOT NULL REFERENCES guilds ON DELETE CASCADE,
    user_id      bigint NOT NULL,
    purpose      text   NOT NULL DEFAULT 'obsidian',
    token_hash   bytea  NOT NULL UNIQUE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    UNIQUE (guild_id, user_id, purpose)  -- one live code per person per server; reconnecting replaces it
);
