-- personal: everyone has their own queue and drops (the default).
-- shared: one reading pile and one drop for the whole server.
ALTER TABLE guilds ADD COLUMN mode text NOT NULL DEFAULT 'personal'
    CHECK (mode IN ('personal', 'shared'));

CREATE INDEX links_guild_url_idx ON links (guild_id, url_normalized);
