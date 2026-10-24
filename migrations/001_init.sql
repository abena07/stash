-- every table is scoped by guild_id so servers never see each other's data

CREATE TABLE guilds (
    guild_id              bigint PRIMARY KEY,
    read_later_channel_id bigint,
    drop_days             smallint[] NOT NULL DEFAULT '{3,7}',  -- iso weekdays: 3 = wed, 7 = sun
    drop_hour             smallint   NOT NULL DEFAULT 9 CHECK (drop_hour BETWEEN 0 AND 23),
    timezone              text       NOT NULL DEFAULT 'UTC',
    claude_key_enc        bytea,                                -- null = rule-based picks
    created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TYPE link_status AS ENUM ('unread', 'read', 'archived');

CREATE TABLE links (
    id                  bigserial PRIMARY KEY,
    guild_id            bigint NOT NULL REFERENCES guilds ON DELETE CASCADE,
    user_id             bigint NOT NULL,
    url                 text   NOT NULL,
    url_normalized      text   NOT NULL,
    source_message_id   bigint,
    thread_id           bigint,
    title               text,
    description         text,
    reading_minutes     smallint,
    tags                text[] NOT NULL DEFAULT '{}',
    status              link_status NOT NULL DEFAULT 'unread',
    loved               boolean NOT NULL DEFAULT false,
    times_sent          integer NOT NULL DEFAULT 0,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (guild_id, user_id, url_normalized)
);
CREATE INDEX links_unread_idx ON links (guild_id, user_id) WHERE status = 'unread';
CREATE INDEX links_thread_idx ON links (thread_id) WHERE thread_id IS NOT NULL;

CREATE TABLE notes (
    id                 bigserial PRIMARY KEY,
    link_id            bigint NOT NULL REFERENCES links ON DELETE CASCADE,
    author_id          bigint NOT NULL,
    discord_message_id bigint NOT NULL UNIQUE,
    content            text   NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- one row per (user, scheduled slot); the unique key stops double-sends across restarts
CREATE TABLE drops (
    id            bigserial PRIMARY KEY,
    guild_id      bigint NOT NULL REFERENCES guilds ON DELETE CASCADE,
    user_id       bigint NOT NULL,
    scheduled_for timestamptz NOT NULL,
    sent_at       timestamptz,
    UNIQUE (guild_id, user_id, scheduled_for)
);

CREATE TABLE drop_items (
    id                 bigserial PRIMARY KEY,
    drop_id            bigint NOT NULL REFERENCES drops ON DELETE CASCADE,
    link_id            bigint REFERENCES links ON DELETE CASCADE,  -- null for surprise picks
    external_url       text,
    is_surprise        boolean NOT NULL DEFAULT false,
    discord_message_id bigint UNIQUE,                             -- maps reactions back to links
    CHECK (link_id IS NOT NULL OR external_url IS NOT NULL)
);
